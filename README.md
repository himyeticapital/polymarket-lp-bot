# Polymarket Trading & LP Bot

Automated trading bot for [Polymarket](https://polymarket.com) focused on earning LP (Liquidity Provider) rewards. Places one-sided limit orders in high-reward prediction markets, manages positions with stop-loss exits, and cycles through markets to maximize daily reward payouts.

Built with pure Python, async throughout, using the official `py-clob-client` SDK.

---

## Features

- **4 Trading Strategies** -- Liquidity provision (primary), arbitrage, copy trading, and synth edge
- **LP Reward Hunting** -- Ranks markets by daily reward pool size, targets the highest-paying opportunities
- **One-Sided LP** -- Places limit orders on one side per market, switches sides on fill (based on @DidiTrading approach)
- **Smart Refresh** -- Keeps stable orders alive, only replaces when midpoint drifts more than $0.02
- **Fill Detection & Side Switching** -- Detects filled orders via open order polling, automatically switches to the opposite side
- **Stop-Loss Exits** -- Auto-sells positions that drop 50% from fill price
- **Fill Cooldown** -- 30-minute cooldown after fills to prevent fill cycling
- **Risk Management** -- $250 drawdown kill switch, per-trade/market/portfolio exposure limits
- **Anti-Detection** -- Randomized timing (+/-15%) and size (+/-10%) jitter on all orders
- **Web Dashboard** -- Real-time browser dashboard with WebSocket updates (port 8080)
- **Telegram Alerts** -- Trade notifications, drawdown warnings, and daily summaries
- **Secret Vault** -- Multi-backend secret storage (macOS Keychain, GPG-encrypted .env, plaintext .env)
- **VPS Deployment** -- Systemd service with auto-restart, deploy scripts for Ubuntu/Oracle Cloud
- **Structured Logging** -- `structlog` with automatic secret scrubbing

---

## Architecture Overview

```
                         +-------------------+
                         |      Engine       |
                         | (Orchestrator)    |
                         +--------+----------+
                                  |
              +-------------------+-------------------+
              |                   |                   |
     +--------v-------+  +-------v--------+  +-------v--------+
     |   Strategies    |  |   Scheduler    |  |   Dashboard    |
     | LP / Arb / Copy |  | Stats, Health  |  | Web + Events   |
     | / Synth Edge    |  | Profile Sync   |  | (port 8080)    |
     +--------+--------+  +----------------+  +----------------+
              |
              | Signal
              v
     +--------+--------+
     |  Risk Manager   |   <-- Drawdown kill switch
     |  (Pre-trade)    |       Per-trade / market / portfolio caps
     +--------+--------+       Daily volume cap
              |
              | RiskVerdict (ALLOW / REJECT)
              v
     +--------+--------+
     |  Order Manager  |   <-- Anti-detection jitter
     |  (Execution)    |       Dry-run / live toggle
     +--------+--------+       Trade logging to SQLite
              |
              v
     +--------+--------+
     |   CLOB Client   |   <-- py-clob-client SDK
     |   (Async wrap)  |       L2 HMAC auth, signature_type=2
     +--------+--------+
              |
              v
       Polymarket CLOB
```

**Data flow**: Strategies generate `Signal` objects. Each signal passes through the `RiskManager` gate (drawdown check, size caps, exposure limits). Approved signals are executed by the `OrderManager`, which applies anti-detection jitter, posts orders via the `AsyncClobClient`, updates inventory, logs to SQLite, and publishes events to the dashboard.

---

## Project Structure

```
bot/
├── __main__.py                # Entry point: python -m bot
├── config.py                  # Pydantic Settings (.env with PM_ prefix)
├── constants.py               # API URLs, contract addresses, enums
├── types.py                   # Signal, OrderResult, OrderBook, Market, BotEvent
├── core/
│   ├── engine.py              # Central orchestrator — wires all components
│   ├── scheduler.py           # Background tasks: stats refresh, health checks
│   └── shutdown.py            # Graceful shutdown handler (SIGINT/SIGTERM)
├── clients/
│   ├── clob.py                # Async CLOB client (py-clob-client wrapper)
│   ├── gamma.py               # Gamma API client (market metadata)
│   ├── data_api.py            # Data API client (balance, positions)
│   ├── synth.py               # Synth API client (probability forecasts)
│   └── websocket_market.py    # WebSocket client for market data
├── strategies/
│   ├── base.py                # Base strategy class
│   ├── liquidity.py           # LP reward hunting (primary strategy)
│   ├── arbitrage.py           # YES+NO mispricing arbitrage
│   ├── copy_trading.py        # Mirror wallet copy trading
│   └── synth_edge.py          # Synth probability edge trading
├── execution/
│   ├── order_manager.py       # Signal → risk check → execute → log pipeline
│   └── dry_run.py             # Simulated execution for testing
├── risk/
│   ├── manager.py             # Pre-trade risk gate with kill switch
│   ├── inventory.py           # Position tracking and exposure calculation
│   └── anti_detection.py      # Timing and size jitter
├── security/
│   ├── vault.py               # Secret vault (Keychain / GPG / .env)
│   └── scrubber.py            # Log scrubber — auto-redacts secrets
├── data/
│   ├── database.py            # Async SQLite wrapper (aiosqlite)
│   ├── migrations.py          # Schema migrations
│   └── models.py              # CRUD operations (trades, daily volume)
├── dashboard/
│   ├── web.py                 # aiohttp web server + WebSocket push
│   ├── state.py               # Dashboard state + event processor
│   ├── app.py                 # Textual TUI dashboard (optional)
│   └── templates/
│       └── index.html         # Browser dashboard HTML/JS
├── notifications/
│   ├── telegram.py            # Telegram bot alerts
│   └── formatter.py           # Message formatting (trades, summaries)
└── utils/
    ├── math.py                # Round to tick, Kelly criterion
    ├── retry.py               # @async_retry decorator for API calls
    └── time.py                # Time utilities

scripts/
├── sell_position.py           # Sell a position (approve + limit sell)
├── liquidate_all.py           # Emergency: cancel all orders + sell all positions
├── estimate_liquidation.py    # Estimate cost of closing all positions
├── test_new_lp.py             # Test LP filters against live market data
├── test_lp_filters.py         # Test LP filter logic
├── check_rewards.py           # Check reward markets via Gamma client
├── check_rewards_paginated.py # Raw paginated reward check from CLOB API
├── check_gamma_fields.py      # Inspect Gamma API response fields
├── setup_keychain.py          # Store secrets in macOS Keychain
└── lock_permissions.py        # Lock .env file permissions to 600

deploy/
├── setup-server.sh            # Ubuntu VPS initial setup (user, systemd, firewall)
├── install-deps.sh            # Create venv and install Python dependencies
└── deploy.sh                  # rsync code + restart service on VPS
```

---

## Prerequisites

- **Python 3.11+**
- **Polymarket account** with a funded proxy wallet on Polygon
- **Private key** for the wallet (EOA that controls the proxy)
- **USDC on Polygon** in the proxy wallet
- (Optional) Telegram bot token + chat ID for alerts
- (Optional) Synth API key for synth edge strategy

---

## Installation

```bash
# Clone the repository
git clone <repo-url> polymarket-bot
cd polymarket-bot

# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Install dev dependencies (optional)
pip install -e ".[dev]"

# Create .env from template
cp .env.example .env   # or create manually (see Configuration below)
chmod 600 .env          # restrict permissions

# Edit .env with your keys
# PM_PRIVATE_KEY=0x...
# PM_PROXY_ADDRESS=0x...
```

---

## Configuration

All configuration is via environment variables with the `PM_` prefix, loaded from a `.env` file by Pydantic Settings.

### Wallet & Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_PRIVATE_KEY` | (required) | Ethereum private key with `0x` prefix |
| `PM_WALLET_ADDRESS` | `""` | Public EOA wallet address |
| `PM_PROXY_ADDRESS` | `""` | Polymarket proxy wallet address |
| `PM_CHAIN_ID` | `137` | Polygon chain ID |
| `PM_CLOB_HOST` | `https://clob.polymarket.com` | CLOB API base URL |
| `PM_GAMMA_HOST` | `https://gamma-api.polymarket.com` | Gamma API base URL |
| `PM_DATA_HOST` | `https://data-api.polymarket.com` | Data API base URL |
| `PM_SYNTH_HOST` | `https://api.synthdata.co` | Synth API base URL |
| `PM_SYNTH_API_KEY` | `""` | Synth API key |

### Mode & Strategy Toggles

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_DRY_RUN` | `true` | Simulate trades without placing real orders |
| `PM_ENABLE_ARBITRAGE` | `true` | Enable arbitrage strategy |
| `PM_ENABLE_LIQUIDITY` | `true` | Enable LP reward strategy |
| `PM_ENABLE_COPY_TRADING` | `true` | Enable copy trading strategy |
| `PM_ENABLE_SYNTH_EDGE` | `true` | Enable synth edge strategy |
| `PM_ENABLE_DASHBOARD` | `true` | Enable TUI dashboard |
| `PM_ENABLE_WEB_DASHBOARD` | `true` | Enable browser dashboard |
| `PM_WEB_DASHBOARD_PORT` | `8080` | Web dashboard port |

### Capital & Risk Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_STARTING_BALANCE_USD` | `500.0` | Starting balance for drawdown tracking |
| `PM_ORIGINAL_DEPOSIT_USD` | `498.0` | Original deposit for all-time P&L |
| `PM_MAX_DRAWDOWN_USD` | `250.0` | Hard stop -- bot halts ALL trading |
| `PM_MAX_TRADE_SIZE_USD` | `25.0` | Maximum single trade size |
| `PM_DAILY_VOLUME_CAP_USD` | `25000.0` | Maximum daily trading volume |
| `PM_MAX_OPEN_POSITIONS` | `15` | Maximum concurrent open positions |
| `PM_MAX_PER_MARKET_USD` | `25.0` | Maximum exposure per market |
| `PM_MAX_PORTFOLIO_EXPOSURE_USD` | `400.0` | Maximum total portfolio exposure |

### Liquidity Strategy

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_LP_TARGET_SPREAD_PCT` | `0.02` | Target spread from midpoint (2%) |
| `PM_LP_ORDER_SIZE_USD` | `25.0` | Default order size in USD |
| `PM_LP_REFRESH_INTERVAL_SEC` | `60.0` | Order refresh interval (seconds) |
| `PM_LP_MAX_MARKETS` | `10` | Maximum concurrent LP markets |
| `PM_LP_MIN_VOLUME_24H` | `5000.0` | Minimum 24h volume to enter |
| `PM_LP_MIN_LIQUIDITY` | `1000.0` | Minimum order book liquidity |
| `PM_LP_MAX_SPREAD` | `0.15` | Maximum bid-ask spread (skip wider) |
| `PM_LP_MIN_BEST_BID` | `0.02` | Minimum best bid price |
| `PM_LP_MIN_DAILY_REWARD` | `10.0` | Minimum daily reward pool (USD) |
| `PM_LP_MAX_DAYS_TO_RESOLVE` | `180` | Skip markets more than N days out |

### Arbitrage Strategy

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_ARB_MIN_PROFIT_CENTS` | `0.5` | Minimum profit per YES+NO pair |
| `PM_ARB_SCAN_INTERVAL_SEC` | `15.0` | Scan interval (seconds) |

### Copy Trading Strategy

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_COPY_TRADERS` | `""` | Comma-separated wallet addresses to copy |
| `PM_COPY_SCALE_FACTOR` | `0.1` | Scale factor (10% of copied size) |
| `PM_COPY_POLL_INTERVAL_SEC` | `30.0` | Polling interval (seconds) |
| `PM_COPY_MIN_TRADE_USD` | `10.0` | Minimum trade size to copy |
| `PM_COPY_MAX_DELAY_SEC` | `5.0` | Maximum delay before copying |

### Synth Edge Strategy

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_SYNTH_EDGE_THRESHOLD` | `0.05` | Required probability edge (5%) |
| `PM_SYNTH_ASSETS` | `"BTC,ETH"` | Comma-separated crypto assets |
| `PM_SYNTH_POLL_INTERVAL_SEC` | `300.0` | Poll interval (seconds) |
| `PM_SYNTH_KELLY_FRACTION` | `0.25` | Kelly fraction for position sizing |

### Anti-Detection

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_TIMING_JITTER_PCT` | `0.15` | Timing randomization (+/-15%) |
| `PM_SIZE_JITTER_PCT` | `0.10` | Size randomization (+/-10%) |

### Telegram Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_TELEGRAM_BOT_TOKEN` | `null` | Telegram bot token (from @BotFather) |
| `PM_TELEGRAM_CHAT_ID` | `null` | Telegram chat/group ID |

### Database & Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `PM_DB_PATH` | `bot_data.db` | SQLite database file path |
| `PM_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |

---

## Usage

### Run Locally

```bash
# Activate virtualenv
source .venv/bin/activate

# Dry-run mode (default -- no real orders)
PYTHONPATH=. python -m bot

# Live trading (set PM_DRY_RUN=false in .env first)
PYTHONPATH=. python -m bot
```

### Run in Background

```bash
cd "/path/to/polymarket-bot"
PYTHONPATH=. nohup .venv/bin/python3 -m bot > /tmp/bot_output.log 2>&1 &

# Check if running
ps aux | grep 'python.*bot' | grep -v grep

# View logs
tail -f /tmp/bot_output.log

# Stop the bot
kill <PID>
```

### Lint & Format

```bash
ruff check .       # Lint
ruff format .      # Format
mypy bot/          # Type check
pytest             # Run tests
```

---

## Strategies

### Liquidity Provision (Primary)

The LP strategy earns rewards by providing liquidity on Polymarket's order books. It follows the @DidiTrading approach with added risk protections.

**How it works:**

1. **Reward-first ranking** -- Fetches all reward-eligible markets from the CLOB `/rewards/markets/current` endpoint and sorts by daily reward pool size (highest first)
2. **One side only** -- Places a single BUY limit order on one side (YES or NO) per market
3. **Behind best bid** -- Prices at the 2nd-best bid level (never closest to midpoint) to minimize fill risk
4. **Smart refresh** -- Keeps existing orders if midpoint has moved less than $0.02; only cancels and replaces when the price has drifted
5. **Min share enforcement** -- Scales up order size to meet the market's `rewards_min_size` requirement
6. **Fill detection** -- Polls open orders each cycle; if an order is gone, it was filled -- switch to the opposite side
7. **Fill cooldown** -- 30-minute cooldown after a fill before re-quoting the same market
8. **Stop-loss exit** -- Monitors filled positions; auto-sells if current price drops 50% below fill price
9. **Expiry filter** -- Skips markets resolving within 3 days (high adverse selection risk)
10. **Midpoint filter** -- Skips tokens with mid < 0.10 or > 0.90 (Polymarket requires two-sided orders in that range; single-sided earns zero rewards)

### Arbitrage

Scans for YES + NO token mispricing. If `YES_price + NO_price > 1.00` (or `< 1.00` with sufficient margin), the bot can buy/sell both sides to lock in a risk-free profit. Requires a minimum profit threshold per pair.

### Copy Trading

Monitors specified wallet addresses for new trades and mirrors them at a configurable scale factor. Polls the Data API for trade activity and replicates positions with size and delay constraints.

### Synth Edge

Compares Synth API probability forecasts for crypto assets (BTC, ETH) against Polymarket prices. When the edge exceeds the threshold, positions are sized using fractional Kelly criterion.

---

## Risk Management

The risk manager acts as a pre-trade gate. Every signal must pass through it before execution.

| Check | Behavior |
|-------|----------|
| **Drawdown Kill Switch** | If balance drops below `starting_balance - max_drawdown`, ALL trading halts immediately. Non-negotiable. |
| **Trade Size Cap** | Individual trades capped at `max_trade_size_usd`. Oversized signals are automatically downsized. |
| **Daily Volume Cap** | Total daily volume capped at `daily_volume_cap_usd`. |
| **Open Positions Limit** | Maximum `max_open_positions` concurrent positions. |
| **Per-Market Exposure** | Each market capped at `max_per_market_usd`. BUY signals downsized to fit. |
| **Portfolio Exposure** | Total portfolio exposure capped at `max_portfolio_exposure_usd`. |
| **Anti-Detection** | Timing jitter (+/-15%) and size jitter (+/-10%) applied to every order. |
| **Stop-Loss** | Filled LP positions auto-sold if they drop 50% from fill price. |
| **Fill Cooldown** | 30-minute cooldown after fills prevents fill cycling. |
| **Expiry Filter** | Markets resolving within 3 days are excluded from LP. |

---

## Dashboard

The web dashboard runs on `http://localhost:8080` (configurable via `PM_WEB_DASHBOARD_PORT`).

**Features:**
- Real-time updates via WebSocket (1-second push interval)
- Portfolio balance, P&L, and P&L percentage
- Win/loss rate and trade statistics
- Per-strategy breakdown (trades, P&L, volume, signals)
- Market scan results and active orders
- Activity log
- Dry-run / halted state indicators

**Endpoints:**
- `GET /` -- Dashboard HTML page
- `GET /api/state` -- JSON snapshot of current bot state
- `GET /ws` -- WebSocket endpoint for real-time state updates

---

## Scripts Reference

| Script | Usage | Description |
|--------|-------|-------------|
| `sell_position.py` | `PYTHONPATH=. python scripts/sell_position.py <token_id> <shares> <price>` | Approve conditional token and sell a position via limit order |
| `liquidate_all.py` | `PYTHONPATH=. python scripts/liquidate_all.py` | Emergency: cancel all open orders and sell all positions |
| `estimate_liquidation.py` | `PYTHONPATH=. python scripts/estimate_liquidation.py` | Estimate the cost of closing all current positions |
| `test_new_lp.py` | `PYTHONPATH=. python scripts/test_new_lp.py` | Test LP filters against live market data |
| `test_lp_filters.py` | `PYTHONPATH=. python scripts/test_lp_filters.py` | Test LP filter logic standalone |
| `check_rewards.py` | `PYTHONPATH=. python scripts/check_rewards.py` | Check reward-eligible markets via Gamma client |
| `check_rewards_paginated.py` | `PYTHONPATH=. python scripts/check_rewards_paginated.py` | Raw paginated reward check from CLOB API |
| `check_gamma_fields.py` | `PYTHONPATH=. python scripts/check_gamma_fields.py` | Inspect Gamma API response fields |
| `setup_keychain.py` | `PYTHONPATH=. python scripts/setup_keychain.py` | Store secrets in macOS Keychain |
| `lock_permissions.py` | `PYTHONPATH=. python scripts/lock_permissions.py` | Lock `.env` file permissions to 600 |

---

## Deployment

### VPS Deployment (Ubuntu / Oracle Cloud Free Tier)

**1. Initial server setup:**

```bash
# SSH into your VPS
ssh ubuntu@YOUR_VPS_IP

# Run the setup script (installs Python 3.11, creates botuser, configures systemd)
sudo bash deploy/setup-server.sh
```

This creates:
- A `botuser` system user (no login shell)
- App directory at `/opt/polymarket-bot`
- A `polymarket-bot.service` systemd unit with auto-restart
- UFW firewall (SSH only)

**2. Deploy from local machine:**

```bash
# From your local project directory
bash deploy/deploy.sh YOUR_VPS_IP [~/.ssh/your_key]
```

This script:
- Syncs code via `rsync` (excludes `.env`, `.venv`, `.git`, databases)
- Copies `.env` on first deploy only
- Fixes ownership to `botuser`
- Installs dependencies and restarts the service

**3. Manage the service:**

```bash
# Start / stop / restart
sudo systemctl start polymarket-bot
sudo systemctl stop polymarket-bot
sudo systemctl restart polymarket-bot

# View logs
sudo journalctl -u polymarket-bot -f

# Check status
sudo systemctl status polymarket-bot
```

The systemd service is configured with:
- `Restart=always` with 30-second delay
- Rate limiting: max 5 restarts per 5 minutes
- Security hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`

---

## Security

### Secret Storage

The bot uses a multi-backend secret vault with fallback chain:

1. **macOS Keychain** (most secure) -- secrets stored via `security` CLI
2. **GPG-encrypted `.env.gpg`** -- decrypted at runtime via `gpg`
3. **Plaintext `.env`** -- warns on startup if sensitive keys are loaded this way

```bash
# Store secrets in macOS Keychain (recommended for local development)
PYTHONPATH=. python scripts/setup_keychain.py

# Lock .env file permissions
chmod 600 .env
# or use the script:
PYTHONPATH=. python scripts/lock_permissions.py
```

### Log Scrubbing

The `SecretScrubber` structlog processor automatically redacts sensitive values (private keys, API tokens) from all log output.

### Other Measures

- All API calls use HTTPS only
- `.env` permissions are checked on startup (warns if readable by others)
- Secrets are stored as `SecretStr` in Pydantic config (not printed in repr/logs)
- Sensitive keys: `PM_PRIVATE_KEY`, `PM_SYNTH_API_KEY`, `PM_TELEGRAM_BOT_TOKEN`

---

## Known Gotchas

| Issue | Detail |
|-------|--------|
| **CLOB SDK returns bids ascending** | The `py-clob-client` returns bids worst-first. The bot sorts descending in `clob.py` so `bids[0]` is always the best bid. |
| **Gamma `endDateIso` is timezone-naive** | Must add `.replace(tzinfo=utc)` before comparing with `datetime.now(timezone.utc)`. |
| **`cancel_all` reports 0 cancelled** | The CLOB API response shows 0 even when orders were successfully cancelled. |
| **GTC orders return status "live"** | Resting GTC orders have `fill_size=0`. They are open orders, not filled positions. |
| **Conditional token balance uses 6 decimals** | Raw balance from API must be divided by `1_000_000` to get actual shares. |
| **Selling requires approval** | Must call `update_balance_allowance(AssetType.CONDITIONAL)` before selling any position. |
| **Expiring markets are fill traps** | Informed traders sweep the book near resolution. The 3-day expiry filter mitigates this. |
| **nohup dies on sleep/restart** | Local `nohup` has no auto-recovery. Use systemd on VPS for production. |
| **Port 8080 conflicts on restart** | Kill the old process before restarting: `lsof -i :8080` then `kill <PID>`. |
| **Pagination cursor errors** | CLOB rewards API may return 400 on later pages (e.g., page 6). Harmless -- the bot stops paginating. |

---

## API Reference

| API | Base URL | Auth | Purpose |
|-----|----------|------|---------|
| **CLOB** | `https://clob.polymarket.com` | L2 HMAC (`signature_type=2`, `funder=proxy`) | Order placement, order book, cancellations, trades |
| **CLOB Rewards** | `https://clob.polymarket.com/rewards/markets/current` | Public | Reward-eligible markets with daily reward amounts |
| **Gamma** | `https://gamma-api.polymarket.com` | Public | Market metadata, categories, end dates (paginates at 100/page) |
| **Data API** | `https://data-api.polymarket.com` | L2 HMAC | Balance sync, position queries |
| **Synth** | `https://api.synthdata.co` | API Key | Probability forecasts for crypto assets |
| **WebSocket (Market)** | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | None | Real-time market data streams |
| **WebSocket (User)** | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | None | Real-time user order/trade updates |

---

## Disclaimer

This software is provided for educational and research purposes. Trading on prediction markets involves substantial risk of loss. There is no guarantee of profit. The authors are not responsible for any financial losses incurred through the use of this bot. Use at your own risk.

- Never trade with funds you cannot afford to lose
- Always test in dry-run mode before enabling live trading
- Monitor the bot actively -- automated trading requires supervision
- Understand the risk parameters and set conservative limits
