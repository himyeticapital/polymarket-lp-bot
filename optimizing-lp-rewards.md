# Optimizing LP Rewards on Polymarket

## How LP Rewards Work

Polymarket pays daily rewards to liquidity providers who maintain qualifying resting limit orders on the order book. You **do not need fills** to earn — just resting orders that meet the criteria.

### Reward Scoring Formula

```
S(v, s) = ((v - s) / v)^2 * b
```

- `v` = max_incentive_spread (e.g., 0.04 = 4 cents from midpoint)
- `s` = your order's distance from midpoint
- `b` = your order size in shares

**Key insight**: The score is **quadratic** — an order at midpoint scores 1.0x, an order at half the max spread scores 0.25x, and an order at the edge scores ~0x. Closer to midpoint = exponentially more reward.

### Eligibility Requirements

1. **Order must be within `max_incentive_spread`** of the midpoint
2. **Order size must meet `min_incentive_size`** (varies per market, typically 20-100 shares)
3. **Order must be resting** (GTC limit order, not FOK/IOC)
4. **Market must have an active reward pool** (daily_reward > $0)

### Daily Payout

Your share of the daily reward pool is proportional to your time-weighted score relative to all other LPs in that market:

```
your_payout = (your_score * your_time) / (total_score * total_time) * daily_pool
```

## Current Strategy: One-Sided LP

### What We Do

- Place ONE BUY limit order per market on the YES or NO side
- Price behind best bid (2nd-best bid or best_bid - 0.01)
- Cancel all and re-quote every ~2 minutes
- Target 7-8 markets with reward pools
- $10 per order, max $10 per market

### Why One-Sided

Two-sided LP (orders on both YES and NO) scores higher but creates positions if both sides fill. We lost $17 from two-sided LP in illiquid markets where we couldn't exit. One-sided is safer for capital preservation.

**Trade-off**: Single-sided orders score lower than two-sided, but we accept this for safety.

## Optimization Levers

### 1. Distance from Midpoint (Biggest Impact)

| Placement | Score Multiplier | Fill Risk |
|-----------|-----------------|-----------|
| At midpoint | 1.00x | Very high |
| 1 cent from mid | ~0.94x | High |
| 2 cents from mid (best bid) | ~0.75x | Moderate |
| 3 cents from mid (behind best) | ~0.44x | Low |
| At max spread edge | ~0.01x | None |

**Current**: We place at 2nd-best bid (~2-3 cents from mid). This gives ~0.4-0.75x score.

**To increase rewards**: Move closer to midpoint. But this increases fill risk.

**Recommended**: Place at best bid (not behind it) for markets with tight spreads and deep books. Only go behind best bid for thin books.

### 2. Order Size

Reward scales linearly with size (`b` in the formula). Doubling size = doubling reward.

**Current**: $10 per order.

**Constraint**: Max $10 per market to cap forced liquidation loss.

**Optimization**: In markets where the midpoint is near 0.50, a filled order loses less on resolution (max loss = $5 at 0.50 vs $9.50 at 0.95). Could increase size for balanced markets.

### 3. Market Selection

**Higher reward pools** = more reward per dollar of liquidity provided.

**Lower competition** = larger share of the pool. A $10 order in a $1k liquidity market gets a much bigger share than in a $500k market.

**Current scoring**: `daily_reward * comp_sweetspot * liq_factor`
- `comp_sweetspot = max(0.1, 1.0 - abs(competitive - 0.5) * 2)` — peaks at moderate competition
- `liq_factor = min(liquidity / 10000, 3.0)` — prefers liquid markets

**Optimization**: Shift toward lower-liquidity markets with decent rewards. Our $10 order has more relative weight there. But must still have enough liquidity to exit if filled.

### 4. Time on Book

Rewards accumulate per second that your order is resting. Longer = more reward.

**Current**: Cancel-replace every ~2 minutes. This means ~0 seconds of downtime per cycle (cancel + place takes ~5-10 seconds).

**Optimization**: Reduce cancel frequency to 3-5 minutes. Less API calls, more time on book. But slower reaction to market moves.

### 5. Two-Sided vs One-Sided

Two-sided LP scores significantly higher (orders on both sides of the book contribute independently).

**Risk**: If both sides fill, you hold both YES and NO shares = directional exposure.

**Mitigation for two-sided** (not currently implemented):
- Only go two-sided in highly liquid markets (liquidity > $50k)
- Keep orders far from midpoint on both sides
- Set tight size limits ($5 per side instead of $10)

### 6. Number of Markets

More markets = wider activity footprint (good for airdrop) + diversified reward sources.

**Current**: 8 markets.

**Recommendation**: Could increase to 10-12 if enough eligible markets pass filters. Capital is not the constraint (orders are resting, not filled), activity breadth is.

## Estimated Daily Rewards

With current setup (7 markets, $10/order, behind best bid, one-sided):

| Factor | Value |
|--------|-------|
| Avg daily pool per market | ~$10-20 |
| Our share (relative to $500k+ liquidity) | ~0.001-0.01% |
| Score multiplier (behind best bid) | ~0.4-0.5x |
| Single-sided penalty | ~0.5x vs two-sided |
| **Estimated daily reward** | **$0.01-0.10** |

The rewards are minimal with our setup. The real value is **airdrop activity** — continuous order placement across multiple markets signals active participation.

## Quick Wins (Low Risk)

1. **Move to best bid** (not behind) in deep markets — ~2x reward score
2. **Increase to 10-12 markets** — wider activity footprint
3. **Reduce refresh to 3 min** — more time on book
4. **Target lower-liquidity markets** — bigger relative share

## Higher Risk Optimizations

1. **Go two-sided** in top 3 most liquid markets — ~2x reward per market
2. **Place at midpoint** in markets with > $100k liquidity — max score but high fill risk
3. **Increase order size to $20** for markets priced near 0.50 — limited downside

## Monitoring

- Check `polymarket.com/rewards` daily for accumulated rewards
- Check `polymarket.com/@username` Activity tab for volume/trade count
- Bot logs: `grep "lp.quote" bot.log` shows placement details per cycle
- Open orders: `polymarket.com/rewards` → "Open Orders" tab
