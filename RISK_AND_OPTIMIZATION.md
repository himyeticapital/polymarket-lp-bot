# Risk Parameters and LP Rewards Optimization Guide

## Quick Reference: All Risk Parameters

| Parameter | Config Key | Default | Current Live | Description |
|-----------|-----------|---------|-------------|-------------|
| Starting Balance | `PM_STARTING_BALANCE_USD` | $500 | $500 | Baseline for drawdown calculation |
| Max Drawdown | `PM_MAX_DRAWDOWN_USD` | $250 | $250 | Hard stop -- bot halts ALL trading |
| Drawdown Threshold | (computed) | $250 | $250 | `starting_balance - max_drawdown` |
| Max Trade Size | `PM_MAX_TRADE_SIZE_USD` | $25 | $50 | Max USD per individual trade |
| Max Per-Market | `PM_MAX_PER_MARKET_USD` | $25 | $50 | Max USD exposure per market |
| Max Portfolio Exposure | `PM_MAX_PORTFOLIO_EXPOSURE_USD` | $400 | $200 | Total position value cap |
| Max Open Positions | `PM_MAX_OPEN_POSITIONS` | 15 | 15 | Maximum concurrent positions |
| Daily Volume Cap | `PM_DAILY_VOLUME_CAP_USD` | $25,000 | $25,000 | Max daily trading volume |
| LP Order Size | `PM_LP_ORDER_SIZE_USD` | $25 | $25 | Default order size per market |
| LP Max Markets | `PM_LP_MAX_MARKETS` | 10 | 6 | Number of markets to quote |
| LP Refresh Interval | `PM_LP_REFRESH_INTERVAL_SEC` | 60 | 300 | Seconds between quote refresh cycles |
| LP Min Daily Reward | `PM_LP_MIN_DAILY_REWARD` | $10 | $10 | Minimum reward pool to participate |
| LP Max Days to Resolve | `PM_LP_MAX_DAYS_TO_RESOLVE` | 180 | 180 | Max days until market resolution |
| Timing Jitter | `PM_TIMING_JITTER_PCT` | 0.15 | 0.15 | +/-15% randomization on timing |
| Size Jitter | `PM_SIZE_JITTER_PCT` | 0.10 | 0.10 | +/-10% randomization on order size |
| Stop-Loss Threshold | (hardcoded) | 50% | 50% | Sell position if price drops 50% from fill |
| Fill Cooldown | (hardcoded) | 1800s | 1800s | 30-minute cooldown after a fill |
| Expiry Filter | (hardcoded) | 3 days | 3 days | Skip markets resolving within 3 days |
| Midpoint Filter | (hardcoded) | 0.10-0.90 | 0.10-0.90 | Skip tokens outside this range |

---

# Part 1: Risk Parameters

## 1.1 Risk Management Overview

The bot operates with a **capital preservation first, rewards second** philosophy. With approximately $412 USDC in capital and no ability to deploy more, every dollar lost is permanent. The risk system implements multi-layer defense:

```
Layer 1: Drawdown Kill Switch     -- Absolute capital floor (non-negotiable)
Layer 2: Position Limits          -- Per-trade, per-market, portfolio-wide caps
Layer 3: Stop-Loss Exit           -- Auto-sell positions dropping 50%+
Layer 4: Fill Cooldown            -- 30-min pause after fills to avoid cycling
Layer 5: Market Filters           -- Expiry, midpoint, and reward filters
Layer 6: Anti-Detection           -- Timing/size jitter to avoid bot detection
Layer 7: Order Placement          -- Behind best bid to minimize fill risk
```

Each layer operates independently. A signal must pass through all layers before execution. No single layer failure should cause catastrophic loss.

## 1.2 Drawdown Kill Switch

The most critical protection in the system. When triggered, **all trading halts immediately** with no automatic recovery.

### How It Works

The drawdown check runs as the very first gate in `RiskManager.check_signal()` (at `bot/risk/manager.py:58-60`). It compares the current balance against a computed threshold:

```python
drawdown_threshold = starting_balance_usd - max_drawdown_usd
# Default: $500 - $250 = $250
```

If `balance <= threshold`, the `_halted` flag is set to `True` and a `DRAWDOWN_HALT` event is published to the event bus. Once halted, the flag is never automatically cleared -- every subsequent `check_signal()` call returns an immediate REJECT.

### Current Thresholds

| Metric | Value |
|--------|-------|
| Starting Balance | $500.00 |
| Max Drawdown | $250.00 |
| Halt Threshold | $250.00 |
| Warning Level (80% consumed) | $300.00 (balance = $300 means $200 drawn down) |

### Warning System

At 80% drawdown consumed (i.e., when `drawdown_used >= max_drawdown * 0.80`), a `DRAWDOWN_WARNING` event fires. This happens at `bot/risk/manager.py:132-147`. The warning emits a log at `WARNING` level and publishes a `DRAWDOWN_WARNING` event that can trigger Telegram alerts.

Example: With $500 starting and $250 max drawdown, the warning fires when balance drops to $300 ($200 of $250 consumed = 80%).

### Config Parameters

- `PM_STARTING_BALANCE_USD` (default: `500.0`) -- The reference balance for drawdown calculation. Should match actual initial deposit.
- `PM_MAX_DRAWDOWN_USD` (default: `250.0`) -- Maximum acceptable loss before trading halts.
- Computed: `config.drawdown_threshold` property returns `starting_balance_usd - max_drawdown_usd`.

### Recovery Procedure

1. Stop the bot process (`kill <PID>`)
2. Investigate what caused the drawdown (check logs, positions, fills)
3. Add capital if needed (deposit USDC to proxy wallet)
4. Update `PM_STARTING_BALANCE_USD` in `.env` to reflect new baseline
5. Restart the bot -- the `_halted` flag resets on fresh initialization

**There is no hot recovery.** The bot must be fully restarted after a drawdown halt.

## 1.3 Position Limits

Position limits are checked sequentially in `RiskManager.check_signal()` (`bot/risk/manager.py:52-98`). The first failure short-circuits with a REJECT. Some checks can **downsize** the signal instead of rejecting outright.

### Check Order (Sequential)

```
1. Drawdown Kill Switch    → REJECT (halt all trading)
2. Trade Size Cap          → DOWNSIZE to max_trade_size_usd
3. Daily Volume Cap        → DOWNSIZE or REJECT
4. Open Positions Limit    → REJECT
5. Per-Market Exposure     → DOWNSIZE or REJECT (BUY only)
6. Portfolio Exposure      → DOWNSIZE or REJECT
```

### Detailed Limit Table

| Check | Parameter | Default | Behavior on Breach |
|-------|-----------|---------|-------------------|
| Trade Size | `PM_MAX_TRADE_SIZE_USD` | $25 | Signal size capped to `max / price` |
| Daily Volume | `PM_DAILY_VOLUME_CAP_USD` | $25,000 | Downsized to remaining capacity; REJECT if zero remaining |
| Open Positions | `PM_MAX_OPEN_POSITIONS` | 15 | Hard REJECT (no downsizing) |
| Per-Market | `PM_MAX_PER_MARKET_USD` | $25 | Downsized to remaining capacity; REJECT if zero remaining; only applies to BUY orders |
| Portfolio | `PM_MAX_PORTFOLIO_EXPOSURE_USD` | $400 | Downsized to remaining capacity; REJECT if zero remaining |

### Exposure Calculation

Exposure is calculated in `InventoryManager` (`bot/risk/inventory.py:134-144`):

- **Total exposure**: `sum(position.size * position.avg_entry_price)` across all positions
- **Per-market exposure**: Same formula, filtered by `condition_id`
- **Open position count**: `len(self.positions)`

Note: SELL orders bypass the per-market exposure check (step 5) because selling reduces exposure.

## 1.4 Stop-Loss System

The stop-loss monitors all filled LP positions and auto-sells when a position drops 50% or more from its fill price.

### How It Monitors

The `LiquidityStrategy._check_and_exit_positions()` method (`bot/strategies/liquidity.py:328-379`) runs at the start of every scan cycle. For each tracked position:

1. Fetch current sell price via `clob_client.get_price(token_id, "SELL")`
2. Calculate `loss_pct = (fill_price - current_price) / fill_price`
3. If `loss_pct >= 0.50` (50% loss), trigger exit

### Exit Execution

The sell process in `_sell_position()` (`bot/strategies/liquidity.py:381-428`):

1. **Approve conditional token**: Call `update_balance_allowance(AssetType.CONDITIONAL)` -- required before any sell
2. **Check actual balance**: Query `get_balance_allowance()` -- uses 6-decimal precision (`balance / 1_000_000 = actual shares`)
3. **Sell at aggressive price**: Place a GTC limit sell at `price * 0.5` to ensure immediate fill through price improvement from the order book
4. **Skip negligible positions**: If `sell_shares < 1`, remove from tracking silently

### Legacy Position Seeding

On the first scan cycle, `_seed_legacy_positions()` (`bot/strategies/liquidity.py:238-271`) loads all existing positions from the inventory into the exit monitoring system. This ensures positions acquired in previous sessions are still protected by stop-loss logic.

### Configuration

- `_exit_loss_pct = 0.50` -- hardcoded in `LiquidityStrategy.__init__()` at `bot/strategies/liquidity.py:65`
- Not currently configurable via `.env` -- requires code change to modify

## 1.5 Fill Cooldown

After a fill is detected, the bot imposes a 30-minute cooldown before re-quoting in that market.

### Why It Exists

Fills often indicate adverse selection -- an informed trader is sweeping the book because the market is about to move against the LP. Re-quoting immediately would place another order at risk of the same adverse move. The cooldown gives time for:

- The market to stabilize after the event
- Price discovery to complete
- The stop-loss system to evaluate the filled position

### How It's Tracked

In `_check_fills_and_update()` (`bot/strategies/liquidity.py:310-311`):

```python
self._fill_cooldowns[cid] = _time.monotonic()
```

Uses `time.monotonic()` (not wall clock) for reliable duration measurement even across system clock changes.

In `_passes_filters()` (`bot/strategies/liquidity.py:490-498`), the cooldown is checked:

```python
cooldown_ts = self._fill_cooldowns.get(m.condition_id)
if cooldown_ts is not None:
    elapsed = _time.monotonic() - cooldown_ts
    if elapsed < self._fill_cooldown_sec:
        return False  # Skip this market
    else:
        del self._fill_cooldowns[m.condition_id]  # Cooldown expired
```

### Configuration

- `_fill_cooldown_sec = 1800` -- hardcoded at `bot/strategies/liquidity.py:70`
- Duration: 30 minutes (1800 seconds)
- Not configurable via `.env`

## 1.6 Expiry Filter

Markets resolving within 3 days are automatically skipped.

### Why Near-Expiry is Dangerous

As a market approaches resolution, informed traders ("sharks") have increasingly confident predictions. They sweep the order book aggressively, filling LP orders that would otherwise rest safely. The LP gets filled on the wrong side just before resolution, locking in a near-total loss. This is classic adverse selection -- the fill itself is evidence the LP is on the wrong side.

### Implementation

In `_passes_filters()` (`bot/strategies/liquidity.py:479-488`):

```python
end_dt = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
days_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400
if days_left < 3:
    return False  # Skip this market
```

Note: The `end_date` from Gamma API is timezone-naive. The code adds UTC timezone info via `replace("Z", "+00:00")`.

### Configuration

- Hardcoded 3-day minimum in `_passes_filters()`
- `PM_LP_MAX_DAYS_TO_RESOLVE` (default: 180) sets the upper bound -- skip markets more than 180 days out

## 1.7 Anti-Detection

The bot randomizes both timing intervals and order sizes to avoid pattern detection.

### Timing Jitter

- **Parameter**: `PM_TIMING_JITTER_PCT` (default: `0.15` = +/-15%)
- **Implementation**: `jitter_delay(base_seconds, pct)` in `bot/risk/anti_detection.py:23-35`
- **Formula**: `base_seconds * (1.0 + random.uniform(-pct, pct))`
- **Effect**: A 300-second refresh interval becomes 255-345 seconds randomly each cycle
- **Applied at**: Strategy initialization (`bot/strategies/liquidity.py:51-53`)

### Size Jitter

- **Parameter**: `PM_SIZE_JITTER_PCT` (default: `0.10` = +/-10%)
- **Implementation**: `jitter_size(size, pct)` in `bot/risk/anti_detection.py:8-20`
- **Formula**: `size * (1.0 + random.uniform(-pct, pct))`
- **Effect**: A 50-share order becomes 45-55 shares randomly
- **Applied at**: Order execution pipeline (`bot/execution/order_manager.py:76-77`), after risk check but before execution

### Why This Matters

Polymarket monitors for bot activity. Predictable intervals and exact round-number sizes are telltale bot signatures. The jitter makes the bot's activity appear more organic. Both jitter functions clamp output to `>= 0` to prevent negative values.

## 1.8 Risk Check Pipeline

Complete flow from signal generation to execution, as implemented in `OrderManager.execute_signal()` (`bot/execution/order_manager.py:58-94`):

```
Signal Generated by Strategy
         |
         v
[1] RiskManager.check_signal()
    |
    +-- [1a] Drawdown Kill Switch
    |        balance <= threshold? --> REJECT "DRAWDOWN HALT"
    |
    +-- [1b] Trade Size Cap
    |        trade_usd > max_trade_size? --> DOWNSIZE (cap size)
    |
    +-- [1c] Daily Volume Cap
    |        today_vol + trade > daily_cap? --> DOWNSIZE or REJECT
    |
    +-- [1d] Open Positions Limit
    |        count >= max_open_positions? --> REJECT
    |
    +-- [1e] Per-Market Exposure (BUY only)
    |        market_exp + trade > max_per_market? --> DOWNSIZE or REJECT
    |
    +-- [1f] Portfolio Exposure
    |        total_exp + trade > max_portfolio? --> DOWNSIZE or REJECT
    |
    +-- PASS --> RiskVerdict(allowed=True, adjusted_signal=...)
         |
         v
[2] Anti-Detection Size Jitter
    |   jitter_size(signal.size, size_jitter_pct)
    |
    v
[3] Execute Order
    |   Dry-run: simulated fill
    |   Live: CLOB create_and_post_limit_order()
    |
    v
[4] Update Inventory
    |   inventory.update_on_fill(result)
    |   Adjusts balance, positions, exposure
    |
    v
[5] Log Trade to Database
    |   insert_trade() + update_daily_volume()
    |
    v
[6] Publish Event to Bus
    |   EventType.TRADE_EXECUTED
    |   Dashboard, Telegram, and other consumers receive it
    |
    v
[DONE] Return OrderResult
```

At any REJECT step, the pipeline short-circuits:
- Returns `OrderResult(success=False, error=reason)`
- No execution, no inventory update, no DB logging
- The rejection is logged at INFO level

---

# Part 1B: Loss History and Lessons Learned

Every protection in this bot was added in response to a real loss event. This section documents what happened, why it happened, and what was built to prevent recurrence.

## Loss Timeline

```
Phase 1: Initial Bot (no protections)
    |
    +-- Two-sided LP losses (~$17)
    |   Fix: Switch to one-sided LP only (commit 1918dae)
    |
    +-- Elon market fills (adverse selection near expiry)
    |   Fix: 3-day expiry filter (commit 32e2771)
    |
    +-- US Fed market fills (same pattern)
    |   Fix: Same expiry filter + fill cooldown
    |
    +-- Fill cycling (repeated fills in same market)
    |   Fix: 30-minute fill cooldown (commit 32e2771)
    |
    +-- Positions dropping without exit
    |   Fix: 50% stop-loss auto-sell (commit 32e2771)
    |
    +-- Extreme midpoint markets earning zero rewards
    |   Fix: Skip mid < 0.10 or > 0.90 (commit 1918dae)
    |
Phase 2: Current Bot (all protections active)
```

## Incident 1: Two-Sided LP Losses (~$17 lost)

### What Happened

The bot placed orders on BOTH sides (YES and NO) of the same market. In illiquid markets, both sides got filled -- meaning someone sold YES into our YES bid AND someone sold NO into our NO bid. We ended up holding both YES and NO shares with no clean exit.

### Why It Was Bad

- Holding both sides is directional exposure with slippage locked in
- In illiquid markets, selling the losing side meant eating significant spread
- The fills were not accidental -- in thin books, resting orders on both sides are easy targets
- Net loss: ~$17 across multiple markets

### Root Cause

Two-sided LP assumes you can exit positions cleanly. In markets with low liquidity ($5k-$20k book depth), the spread is wide and selling at a reasonable price is not possible. The bot was providing liquidity it could not afford to have taken.

### Fix Implemented

**One-sided LP only** (commit `1918dae` - "Rewrite LP strategy: one-sided reward hunting with strict filters"):

- Place orders on ONE side per market (YES or NO, not both)
- On fill, switch to the opposite side for the next quote
- Scoring penalty accepted: single-sided earns score/3, but capital is safer

## Incident 2: Elon Markets (Adverse Selection Near Expiry)

### What Happened

Markets like "Will Elon [do X] by [date within 2-3 days]" had active reward pools. The bot placed LP orders in these markets. As the resolution date approached, informed traders -- people who could strongly predict the outcome -- swept the order book. Our resting limit orders got filled by these informed traders because they knew the outcome was going against our side.

### Why It Was Bad

- **Adverse selection**: The only reason someone fills your resting order near expiry is because they have information that the outcome will go against you
- Near resolution, the price should be close to 0 or 1 -- the "informed" side buys aggressively at any available price
- The fills were not random market making -- they were directional attacks on stale resting liquidity
- The LP essentially provided free exit liquidity to informed traders

### Root Cause

Expiring markets attract "sharks" -- traders with increasingly confident predictions as the resolution approaches. Resting LP orders become free money for them. The bot had no filter for market expiry date.

### Fix Implemented

**3-day expiry filter** (commit `32e2771` - "Add exit logic, fill cooldown, expiry filter"):

```python
# bot/strategies/liquidity.py:479-488
days_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400
if days_left < 3:
    return False  # Skip this market
```

Markets resolving within 3 days are now excluded entirely. This eliminates the highest-risk adverse selection window.

## Incident 3: US Fed Markets (Same Pattern)

### What Happened

Same adverse selection pattern as Elon markets but with Federal Reserve rate decision markets. Markets like "Will the Fed cut rates at [upcoming meeting]" had high reward pools but were days from resolution. Informed traders (institutional players, analysts with strong Fed models) filled our orders just before the announcement.

### Why It Was Bad

- Fed decision markets have some of the highest informed trading near resolution
- Institutional traders actively sweep LP orders in the hours before announcements
- Our resting orders were filled at prices that were clearly wrong post-resolution

### Root Cause

Same as Elon markets: no expiry filter, and the bot was unaware that "high reward pool + near expiry" is a dangerous combination, not an attractive one. High reward pools near expiry often exist BECAUSE the risk is high -- informed LPs have already pulled out.

### Fix Implemented

The same 3-day expiry filter covers this case. Additionally:

- **Fill cooldown** (30 minutes): Prevents re-quoting the same market immediately after a fill, which would have caused repeated losses in the same session
- **Stop-loss exit** (50%): Limits the damage from any single fill that goes wrong

## Incident 4: Fill Cycling

### What Happened

After a fill, the bot would immediately re-quote the same market in the next scan cycle (every 60 seconds at the time). In fast-moving or adversely-selected markets, this meant:

1. Order filled (unwanted)
2. Bot places new order on opposite side (next cycle)
3. New order filled again (market still moving)
4. Bot places another order...

This cycle accumulated multiple unwanted positions in the same market.

### Why It Was Bad

- Each fill locked up capital in a position
- Multiple fills in the same market multiplied the loss
- The bot was effectively fighting the market instead of stepping back

### Root Cause

No cooldown between fill detection and re-quoting. The bot treated a fill as a normal event and immediately tried to re-enter, without considering that the fill itself was a signal that the market was moving against LP interests.

### Fix Implemented

**30-minute fill cooldown** (commit `32e2771`):

```python
# bot/strategies/liquidity.py:69-70
self._fill_cooldowns: dict[str, float] = {}
self._fill_cooldown_sec = 1800  # 30 minutes
```

After any fill is detected, the market's `condition_id` is added to a cooldown map with a `time.monotonic()` timestamp. The market is excluded from quoting until 30 minutes have elapsed. This breaks the fill cycling pattern.

## Incident 5: Extreme Midpoint Markets (Zero Reward Earned)

### What Happened

The bot placed one-sided LP orders in markets with midpoints near 0.05 or 0.95. These orders rested on the book for hours/days but earned zero LP rewards.

### Why It Was Bad

- Capital was deployed doing nothing -- no rewards earned
- The bot believed it was earning rewards but was getting zero
- Opportunity cost: those order slots could have been used on eligible markets

### Root Cause

Polymarket's reward rules require **two-sided** orders when the midpoint is below 0.10 or above 0.90. Single-sided orders in that zone score exactly zero regardless of placement or size. The bot was not aware of this rule.

### Fix Implemented

**Midpoint filter** (commit `1918dae`):

```python
# bot/strategies/liquidity.py:542-551
if mid is None or mid < 0.10 or mid > 0.90:
    return None  # Skip -- two-sided required
```

Markets with extreme midpoints are now skipped entirely since the bot only does one-sided LP.

## Summary: Protection-to-Incident Mapping

| Protection | Incident | Loss | Commit |
|------------|----------|------|--------|
| One-sided LP only | Two-sided fills in illiquid markets | ~$17 | `1918dae` |
| 3-day expiry filter | Elon + US Fed market adverse selection | Variable | `32e2771` |
| 30-min fill cooldown | Fill cycling (repeated fills same market) | Compounding | `32e2771` |
| 50% stop-loss exit | Positions dropping without any exit | Uncapped → 50% max | `32e2771` |
| Midpoint filter (0.10-0.90) | Zero rewards earned in extreme-mid markets | Opportunity cost | `1918dae` |
| Behind best bid placement | Reduce fill probability across all markets | Preventive | `1918dae` |
| Smart refresh (keep if < $0.02 move) | Maximize time-on-book for rewards | Preventive | `1918dae` |
| Legacy position seeding | Old positions not monitored for stop-loss | Preventive | `32e2771` |

---

# Part 2: LP Rewards Optimization

## 2.1 Reward Formula Deep Dive

### The Q-Score Formula

Polymarket calculates each LP's reward score using a quadratic distance function:

```
S(v, s) = ((v - s) / v)^2 * b
```

Where:
- `v` = `max_incentive_spread` -- the maximum distance from midpoint that still qualifies for rewards (e.g., 0.04 = 4 cents)
- `s` = your order's distance from the midpoint (e.g., 0.02 = 2 cents away)
- `b` = your order size in shares

This is implemented in `reward_score()` at `bot/utils/math.py:33-50`.

### Why Quadratic Matters

The `((v - s) / v)^2` term creates a **quadratic** (not linear) relationship between placement and reward. Moving closer to the midpoint provides disproportionately more reward, while moving further away destroys score rapidly.

### Score Comparison Table

For a market with `max_incentive_spread = 0.04` (4 cents) and `b = 100` shares:

| Distance from Mid (`s`) | `(v-s)/v` | Score Multiplier | Raw Score | Fill Risk |
|--------------------------|-----------|-------------------|-----------|-----------|
| 0.00 (at midpoint) | 1.000 | 1.00x | 100.0 | Very High |
| 0.005 (0.5 cent) | 0.875 | 0.77x | 76.6 | High |
| 0.01 (1 cent) | 0.750 | 0.56x | 56.3 | Moderate-High |
| 0.015 (1.5 cents) | 0.625 | 0.39x | 39.1 | Moderate |
| 0.02 (2 cents) | 0.500 | 0.25x | 25.0 | Moderate-Low |
| 0.03 (3 cents) | 0.250 | 0.06x | 6.3 | Low |
| 0.035 (3.5 cents) | 0.125 | 0.02x | 1.6 | Very Low |
| 0.04 (at edge) | 0.000 | 0.00x | 0.0 | None |

Key insight: An order 2 cents from mid earns only 25% of the score compared to an order at the midpoint, despite being only halfway to the edge.

### Worked Example

Market: "Will Bitcoin exceed $100k by March?"
- `max_incentive_spread` = 0.05 (5 cents)
- `min_incentive_size` = 50 shares
- `daily_reward` = $200/day
- Your order: 80 shares at 3 cents from midpoint

```
S = ((0.05 - 0.03) / 0.05)^2 * 80
S = (0.40)^2 * 80
S = 0.16 * 80
S = 12.8
```

If total pool score is 50,000 and your order rests for the full 24 hours:

```
your_payout = (12.8 / 50,000) * $200 = $0.051/day
```

## 2.2 Two-Sided vs One-Sided

### Polymarket Rules

| Midpoint Range | Requirement | Single-Sided Score |
|---------------|-------------|-------------------|
| `mid < 0.10` | **TWO-SIDED REQUIRED** | Zero (no rewards) |
| `mid > 0.90` | **TWO-SIDED REQUIRED** | Zero (no rewards) |
| `0.10 <= mid <= 0.90` | Single-sided OK | Score / 3 (c=3.0 penalty) |

### Current Strategy

The bot uses **one-sided only** and skips markets with extreme midpoints:

```python
# bot/strategies/liquidity.py:542-551
if mid is None or mid < 0.10 or mid > 0.90:
    return None  # Skip -- two-sided required
```

### Trade-Off Analysis

**One-Sided (current)**:
- Pros: Simpler risk management, only exposed on one side, lower fill risk
- Cons: Score divided by 3 in the 0.10-0.90 range, cannot access extreme-mid markets at all
- History: The bot lost $17 from two-sided LP in illiquid markets where both sides filled and positions could not be exited

**Two-Sided (not implemented)**:
- Pros: Full score (no 1/3 penalty), access to extreme-mid markets (often less competitive)
- Cons: Both sides can fill simultaneously creating directional exposure, doubles capital at risk, requires more sophisticated position management

## 2.3 Market Selection Criteria

Markets go through a multi-stage filter pipeline in `LiquidityStrategy._rank_markets()` and `_passes_filters()` (`bot/strategies/liquidity.py:434-499`).

### Filter Chain (Sequential)

```
Raw CLOB Reward Markets (~3000)
         |
    [1] active == True
    [2] max_incentive_spread > 0
    [3] len(tokens) >= 2
         |
    [4] daily_reward >= lp_min_daily_reward ($10)
         |
    [5] Not expiring within 3 days
         |
    [6] Not on fill cooldown (30 min)
         |
    [7] Midpoint in [0.10, 0.90] range
         |
    [8] best_bid >= lp_min_best_bid ($0.02)
         |
    Ranked by daily_reward (descending)
         |
    Top lp_max_markets (6) selected
```

### Deep Search

The bot does not stop at the first `lp_max_markets` eligible markets from the ranked list. Instead, it iterates through all ranked markets, skipping those that fail side-specific checks (two-sided required, too expensive for min shares, no viable price), and continues until `lp_max_markets` active slots are filled (`bot/strategies/liquidity.py:145-157`).

This means if markets #1, #3, and #7 fail side checks, the bot picks markets #2, #4, #5, #6, #8, #9 instead of giving up after 6 attempts.

### Reward Distribution Logging

Each scan cycle logs the reward distribution across all markets for diagnostics (`bot/strategies/liquidity.py:443-464`):

```
reward_dist: {"0": 1200, "1-9": 800, "10-49": 500, "50-99": 200, "100-499": 150, "500+": 50}
```

## 2.4 Order Placement Strategy

### Behind Best Bid

Orders are placed **behind** the best bid to minimize fill risk. The pricing logic in `_try_quote_side()` (`bot/strategies/liquidity.py:576-579`):

```python
if len(book.bids) >= 2:
    price = book.bids[1].price      # 2nd-best bid
else:
    price = round_to_tick(book.best_bid - 0.01)  # best_bid minus 1 cent
```

**Why NOT at best bid**: Being at the best bid means you are the first to get filled when someone sells. In LP reward farming, fills are generally unwanted -- they create positions that may lose value. Staying behind the best bid reduces fill probability while still being within the reward-eligible spread.

### Smart Refresh

Orders are not blindly cancelled and replaced every cycle. The smart refresh system (`bot/strategies/liquidity.py:558-573`) checks if the existing order is still valid:

```python
if existing and existing["side"] == side:
    old_mid = existing.get("mid", 0)
    if abs(mid - old_mid) < 0.02:
        return None  # Keep existing order (price stable)
    # Price moved > 2 cents -- cancel and replace
```

Benefits:
- More time on book = more reward accrual (rewards are time-weighted)
- Fewer API calls = lower detection risk
- Less downtime during cancel-replace cycles

### Reward Eligibility Verification

Before placing, the code verifies the order falls within `max_incentive_spread` (`bot/strategies/liquidity.py:584-590`):

```python
spread_from_mid = abs(mid - price)
if spread_from_mid > market.max_incentive_spread:
    # Adjust price to be just inside the eligible zone
    price = round_to_tick(mid - market.max_incentive_spread + 0.01)
```

### Side Selection

1. Use the current side preference for the market (default: "yes")
2. If the current side fails (too expensive, no viable price), try the opposite side
3. If the opposite side works, update the side preference for future cycles

After a fill, the side automatically switches to the opposite in `_check_fills_and_update()` (`bot/strategies/liquidity.py:298-300`).

### Min Share Enforcement

If the calculated order size (from `lp_order_size_usd / price`) is less than the market's `min_incentive_size`, the order is scaled up to meet the minimum (`bot/strategies/liquidity.py:596-609`):

```python
if size_shares < market.min_incentive_size:
    needed_usd = market.min_incentive_size * price
    if needed_usd <= self.config.max_per_market_usd:
        size_shares = market.min_incentive_size  # Scale up
    else:
        return None  # Too expensive, skip market
```

## 2.5 Estimated Returns Calculation

### Current Setup

| Factor | Value |
|--------|-------|
| Markets targeted | 6 |
| Order size | $25 per market |
| Capital deployed (resting) | ~$150 |
| Placement | Behind best bid (2nd-best bid, typically 2-3 cents from mid) |
| Strategy | One-sided only |
| Score multiplier (behind best bid) | ~0.25-0.56x depending on spread |
| Single-sided penalty | Score / 3 (c=3.0) |

### Revenue Model

For a single market with $100/day reward pool:

```
Order: 100 shares at 2 cents from mid
max_spread: 0.04

Raw score: ((0.04 - 0.02) / 0.04)^2 * 100 = 25.0
Single-sided penalty: 25.0 / 3 = 8.33
Time weight: ~95% (5% downtime during refreshes)
Effective score: 8.33 * 0.95 = 7.92

If total market score = 100,000:
Daily reward = (7.92 / 100,000) * $100 = $0.008/day
```

Across 6 markets with varying reward pools ($10-$500/day), expected total:

```
Estimated daily reward: $0.30 - $0.60/day
On ~$150 deployed capital: ~120% APY annualized
```

Note: Actual returns vary significantly based on competition levels and market conditions.

## 2.6 Optimization Levers

### Lever 1: Distance from Midpoint

**Current**: 2nd-best bid (~2-3 cents from mid)
**Impact**: Quadratic -- moving 1 cent closer roughly doubles the score

| Change | Score Impact | Fill Risk Impact |
|--------|-------------|-----------------|
| Move to best bid | ~2-4x increase | Significant increase |
| Move to 1 cent from mid | ~4-8x increase | High increase |
| Move to midpoint | ~16x increase | Very high -- frequent fills |

**Recommendation**: Move to best bid in deep, liquid markets (>$50k liquidity) where fill risk is lower. Keep behind best bid in thin books.

### Lever 2: Order Size

**Current**: $25 per order
**Impact**: Linear -- doubling size doubles the score

Score is `((v-s)/v)^2 * b` -- the `b` (shares) term is linear.

| Size | Score Multiplier | Capital at Risk |
|------|-----------------|-----------------|
| $10 | 1.0x | Low |
| $25 | 2.5x | Moderate |
| $50 | 5.0x | Higher |

**Constraint**: `max_per_market_usd` caps the maximum. For markets near 0.50 midpoint, a filled order's max loss is 50% of order value. For markets near 0.90, max loss is ~90%.

### Lever 3: Number of Markets

**Current**: 6 markets
**Impact**: Linear -- more markets = more reward pools

More markets also provides:
- Better diversification of fill risk
- Wider on-chain activity footprint
- More sources of LP rewards

**Trade-off**: Each additional market requires more capital (resting orders) and more API calls per cycle.

### Lever 4: Minimum Daily Reward Threshold

**Current**: $10/day minimum
**Impact**: Concentrates capital on richest pools

| Threshold | Markets Available | Avg Pool Size | Capital Concentration |
|-----------|------------------|---------------|----------------------|
| $1/day | ~2000+ | Low | Very spread out |
| $10/day | ~200-500 | Moderate | Moderate |
| $50/day | ~50-100 | High | Concentrated |
| $100/day | ~20-40 | Very high | Highly concentrated |

**Trade-off**: Higher threshold = fewer but richer pools, but also more competition from other LPs.

### Lever 5: Refresh Interval

**Current**: 300 seconds (5 minutes)
**Impact**: Affects time-on-book and quote freshness

| Interval | Time on Book | Quote Freshness | API Load |
|----------|-------------|-----------------|----------|
| 60s | ~90% | Very fresh | High |
| 180s | ~95% | Fresh | Moderate |
| 300s | ~97% | Moderate | Low |
| 600s | ~98% | Stale | Very low |

Rewards accrue per-second that orders are resting. Shorter intervals mean more cancel-replace downtime but fresher prices. With the smart refresh system (keep orders if mid moved < $0.02), actual cancel-replace frequency is lower than the nominal interval.

### Lever 6: Two-Sided Quoting

**Current**: Not implemented
**Impact**: Eliminates the 1/3 penalty; unlocks extreme-mid markets

Enabling two-sided quoting would:
- Triple the score per market (remove c=3.0 penalty)
- Access markets with mid < 0.10 or > 0.90 (currently skipped)
- Double capital at risk per market
- Require more sophisticated position management

## 2.7 Parameter Sensitivity Analysis

| Parameter | Current | Conservative | Aggressive | Impact on Returns |
|-----------|---------|-------------|------------|-------------------|
| `lp_order_size_usd` | $25 | $15 | $50 | Linear: 2x size = 2x score |
| `lp_max_markets` | 6 | 4 | 10 | ~Linear: more pools, more diverse |
| `lp_min_daily_reward` | $10 | $50 | $1 | Indirect: higher = richer pools but fewer options |
| `lp_refresh_interval_sec` | 300 | 600 | 60 | Marginal: 97% vs 90% time-on-book |
| Placement distance | 2nd bid | 3rd bid | Best bid | Quadratic: best bid ~2-4x score vs 2nd bid |
| `_exit_loss_pct` | 0.50 | 0.30 | 0.70 | Risk: tighter = more exits but less loss per position |
| `_fill_cooldown_sec` | 1800 | 3600 | 900 | Risk: longer = safer but loses reward time |

### Conservative Profile (Capital Protection)

```
PM_LP_ORDER_SIZE_USD=15
PM_LP_MAX_MARKETS=4
PM_LP_MIN_DAILY_REWARD=50
PM_LP_REFRESH_INTERVAL_SEC=600
_exit_loss_pct=0.30
```

Expected: ~$0.10-0.20/day, very low fill risk, capital well-protected.

### Aggressive Profile (Maximum Rewards)

```
PM_LP_ORDER_SIZE_USD=50
PM_LP_MAX_MARKETS=10
PM_LP_MIN_DAILY_REWARD=1
PM_LP_REFRESH_INTERVAL_SEC=60
# Place at best bid instead of behind
```

Expected: ~$1.00-2.00/day, higher fill risk, more capital exposed.

**Warning**: The aggressive profile significantly increases fill risk and potential losses. Only use with careful monitoring.

## 2.8 Capital Efficiency

### Optimal Order Sizing

The goal is to meet `min_incentive_size` without over-deploying capital:

```
Ideal size = max(min_incentive_size, lp_order_size_usd / price)
Capped at: min(ideal_size * price, max_per_market_usd)
```

Over-deploying (placing much more than `min_incentive_size`) gives linear returns but linear risk. Under-deploying earns zero rewards.

### Market Concentration vs Diversification

| Strategy | Reward Rate | Risk | Capital Efficiency |
|----------|------------|------|-------------------|
| 3 markets, $50 each | Higher per-market | Concentrated fill risk | Moderate |
| 6 markets, $25 each | Moderate per-market | Diversified fill risk | Good |
| 12 markets, $12.50 each | Lower per-market | Very diversified | May miss min_size requirements |

The sweet spot depends on `min_incentive_size` requirements. If most markets require 50+ shares and prices are ~$0.50, you need $25+ per market just to qualify. With ~$150 deployable capital, 6 markets at $25 is near-optimal.

### When to Reallocate Capital

Consider reallocating when:

1. **Reward pool drops significantly** -- A market's daily reward drops below threshold; free up capital for better pools
2. **Competition surges** -- Many new LPs enter a market, diluting your share; move to less competitive pools
3. **Position accumulated** -- After a fill, capital is locked in a position; reduce order size in other markets to compensate
4. **Market approaching resolution** -- Within 3-day window (auto-filtered), but also consider the 7-14 day zone where informed trading increases

---

# Part 3: Performance Monitoring

## 3.1 Key Metrics to Track

### Primary Metrics

| Metric | Target | How to Measure | Why It Matters |
|--------|--------|----------------|----------------|
| Daily LP Rewards | $0.30-0.60 | Check `polymarket.com/rewards` | Primary revenue source |
| Fill Rate | < 1 fill/day | Count fill detections in logs | Lower = better for pure LP |
| Stop-Loss Triggers | < 1/week | Count `lp.exit_triggered` events | Indicates adverse selection |
| Capital Utilization | 30-40% | Resting capital / total balance | Too low = inefficient; too high = risky |
| Effective APY | > 100% | (daily_rewards * 365) / deployed_capital | Overall efficiency measure |

### Secondary Metrics

| Metric | Description | Source |
|--------|-------------|--------|
| Smart Refresh Rate | % of cycles where orders are kept (not replaced) | Count `lp.keeping_order` vs total |
| Markets Skipped | Count of markets failing each filter | `lp.markets_filtered` log |
| Cooldown Active | Number of markets currently on fill cooldown | Count `lp.skip_cooldown` events |
| Reward Eligibility | % of orders meeting both `shares_ok` and `spread_ok` | `lp.quote` log fields |

## 3.2 Dashboard Metrics

The web dashboard (`http://localhost:8080`) displays real-time state via WebSocket updates every second. State is managed in `DashboardState` (`bot/dashboard/state.py:46-94`).

### Top-Level Stats

| Display | Source Field | Description |
|---------|-------------|-------------|
| Balance | `balance + positions_value` | Total portfolio value (cash + positions) |
| Cash | `balance` | USDC available for new orders |
| Positions Value | `positions_value` | Current value of all open positions |
| P&L | `total_pnl` | Portfolio value minus initial balance |
| P&L % | `pnl_pct` | Percentage return from initial balance |
| Win Rate | `wins / (wins + losses)` | Percentage of successful orders |
| Daily Volume | `daily_volume` | Total fill volume today (excludes resting orders) |

### Per-Strategy Stats

Each strategy (Liquidity, Arbitrage, Copy Trading, Synth Edge) has independent tracking:

| Field | Description |
|-------|-------------|
| `trades` | Total orders placed |
| `pnl` | Strategy P&L (derived from overall inventory) |
| `volume` | Actual fill volume (not resting order notional) |
| `order_notional` | Total value of all orders (fills + resting) |
| `signals` | Number of signals generated |
| `last_scan` | Timestamp of last market scan |
| `status` | Current state: `idle`, `scanning`, `active`, `error` |

### Footer Stats

| Display | Source | Description |
|---------|--------|-------------|
| Avg Bet | `_orders_notional / total_trades` | Average order size |
| Best Trade | `best_trade` | Highest single-trade P&L |
| Worst Trade | `worst_trade` | Lowest single-trade P&L |
| Sharpe Ratio | `sharpe` | Risk-adjusted return metric |
| Runway % | `runway_pct` | Days until bankrupt at current loss rate (normalized to 30-day horizon) |

### Halted State

When the drawdown kill switch triggers, `is_halted = True` is set and a `DRAWDOWN HALT` message appears in the activity log. The dashboard reflects this state so it is immediately visible.

## 3.3 Tuning Workflow

A systematic approach to optimizing bot performance:

### Step 1: Check Rewards Earned vs Capital Deployed

```bash
# Check accumulated rewards
PYTHONPATH=. .venv/bin/python3 scripts/check_rewards.py

# Compare to capital deployed (from dashboard or logs)
# Target: > $0.30/day on $150 deployed
```

If rewards are lower than expected:
- Are orders meeting `min_incentive_size`? (Check `shares_ok` in `lp.quote` logs)
- Are orders within `max_incentive_spread`? (Check `spread_ok` in `lp.quote` logs)
- How many markets are actually being quoted? (Check `markets_quoted` in scan events)

### Step 2: Analyze Fill Frequency

```bash
# Count fill detections in logs
grep "lp.fill_detected" /tmp/bot_output.log | wc -l

# Check fill details
grep "lp.fill_detected" /tmp/bot_output.log
```

If fills are too frequent (>1/day):
- Move orders further from midpoint
- Increase expiry filter (skip markets resolving within 7 days instead of 3)
- Focus on deeper, more liquid markets
- Increase cooldown duration

If fills are zero over multiple days:
- Orders may be too far from midpoint to get noticed
- This is not necessarily bad for pure LP reward farming

### Step 3: Review Stop-Loss Triggers

```bash
grep "lp.exit_triggered" /tmp/bot_output.log
```

Each stop-loss exit represents a realized loss. If frequent:
- Fills are happening in adversely selected markets
- Consider tighter stop-loss (e.g., 30% instead of 50%)
- Consider longer cooldown after fills
- Review which markets are causing fills -- may need to add to a blacklist

### Step 4: Adjust Parameters Based on Data

Based on the analysis above, adjust parameters in `.env`:

**If earning too little reward**:
- Increase `PM_LP_ORDER_SIZE_USD` (more shares = more score)
- Increase `PM_LP_MAX_MARKETS` (more pools)
- Decrease `PM_LP_MIN_DAILY_REWARD` (access more markets)
- Consider moving closer to midpoint (code change)

**If losing too much capital**:
- Decrease `PM_LP_ORDER_SIZE_USD`
- Increase `PM_MAX_DRAWDOWN_USD` warning threshold
- Tighten stop-loss (code change: `_exit_loss_pct`)
- Increase fill cooldown (code change: `_fill_cooldown_sec`)
- Add per-market blacklist for consistently adverse markets

**If bot detection is a concern**:
- Increase `PM_TIMING_JITTER_PCT` (e.g., 0.25 for +/-25%)
- Increase `PM_SIZE_JITTER_PCT` (e.g., 0.15 for +/-15%)
- Increase `PM_LP_REFRESH_INTERVAL_SEC` (less frequent activity)

### Step 5: Monitor After Changes

After adjusting parameters:
1. Run in `PM_DRY_RUN=true` mode first to verify behavior
2. Switch to live and monitor for at least 24 hours
3. Check rewards accumulation on `polymarket.com/rewards`
4. Review fill frequency and stop-loss triggers
5. Iterate as needed

---

## Appendix A: Configuration File Reference

All parameters are set in `.env` with the `PM_` prefix and loaded via Pydantic Settings in `bot/config.py`.

### Risk Parameters

```env
PM_STARTING_BALANCE_USD=500
PM_MAX_DRAWDOWN_USD=250
PM_MAX_TRADE_SIZE_USD=50
PM_MAX_PER_MARKET_USD=50
PM_MAX_PORTFOLIO_EXPOSURE_USD=200
PM_MAX_OPEN_POSITIONS=15
PM_DAILY_VOLUME_CAP_USD=25000
```

### LP Strategy Parameters

```env
PM_LP_ORDER_SIZE_USD=25
PM_LP_MAX_MARKETS=6
PM_LP_REFRESH_INTERVAL_SEC=300
PM_LP_MIN_DAILY_REWARD=10
PM_LP_MAX_DAYS_TO_RESOLVE=180
PM_LP_MIN_BEST_BID=0.02
PM_LP_MIN_VOLUME_24H=5000
PM_LP_MIN_LIQUIDITY=1000
PM_LP_MAX_SPREAD=0.15
```

### Anti-Detection Parameters

```env
PM_TIMING_JITTER_PCT=0.15
PM_SIZE_JITTER_PCT=0.10
```

### Mode

```env
PM_DRY_RUN=false
PM_ENABLE_LIQUIDITY=true
PM_ENABLE_ARBITRAGE=false
PM_ENABLE_COPY_TRADING=false
PM_ENABLE_SYNTH_EDGE=false
```

## Appendix B: Hardcoded Values

These values require code changes to modify (not configurable via `.env`):

| Value | Location | Current | Description |
|-------|----------|---------|-------------|
| Stop-loss threshold | `liquidity.py:65` | 0.50 (50%) | Sell if position drops 50% |
| Fill cooldown | `liquidity.py:70` | 1800s (30 min) | Pause after fill |
| Expiry filter | `liquidity.py:483` | 3 days | Skip near-expiry markets |
| Midpoint filter | `liquidity.py:542` | 0.10-0.90 | Skip extreme midpoints |
| Smart refresh threshold | `liquidity.py:562` | 0.02 ($0.02) | Keep order if mid moved less |
| Sell price discount | `liquidity.py:409` | 0.50 (50%) | Sell at half price for immediate fill |
| Min sell shares | `liquidity.py:404` | 1 share | Skip positions < 1 share |
| Drawdown warning | `manager.py:133` | 80% | Warn at 80% of max drawdown consumed |
