"""Shared reactive state for the TUI dashboard."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from bot.constants import EventType, Strategy
from bot.types import BotEvent, EventBus


@dataclass
class StrategyStats:
    """Per-strategy statistics."""
    name: str = ""
    trades: int = 0
    pnl: float = 0.0
    volume: float = 0.0           # Actual fill volume only
    order_notional: float = 0.0   # All orders placed (fills + resting)
    signals: int = 0
    last_scan: str = ""
    status: str = "idle"  # "idle", "scanning", "active", "error"


# Map Strategy enum values (and raw strings) to strategy_stats dict keys.
_STRATEGY_KEY_MAP: dict[str, str] = {
    Strategy.ARBITRAGE: "arbitrage",
    Strategy.LIQUIDITY: "liquidity",
    Strategy.COPY_TRADING: "copy_trading",   # Strategy.COPY_TRADING == "copy"
    Strategy.SYNTH_EDGE: "synth_edge",
    # Also allow raw dict-key strings coming in directly
    "arbitrage": "arbitrage",
    "liquidity": "liquidity",
    "copy_trading": "copy_trading",
    "copy": "copy_trading",
    "synth_edge": "synth_edge",
}


def _resolve_strategy_key(raw: str) -> str | None:
    """Return the canonical strategy_stats dict key, or *None* if unknown."""
    return _STRATEGY_KEY_MAP.get(str(raw).lower().strip())


@dataclass
class DashboardState:
    """Central state that all dashboard widgets read from."""

    # Stats bar
    balance: float = 500.0       # USDC cash only
    positions_value: float = 0.0  # Current value of all positions
    initial_balance: float = 500.0
    total_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    daily_volume: float = 0.0
    api_costs: float = 0.0

    # Polymarket profile stats (from API)
    total_volume: float = 0.0       # All-time volume from leaderboard
    lp_rewards: float = 0.0         # Total LP rewards earned
    # LP market details (set by LiquidityStrategy each scan)
    lp_markets: list[dict] = field(default_factory=list)
    markets_traded: int = 0         # Unique markets traded

    # Balance history (sparkline data points)
    balance_history: list[float] = field(default_factory=lambda: [500.0])

    # Markets panel
    markets: list[dict] = field(default_factory=list)
    markets_scanned: int = 0
    avg_edge: float = 0.0

    # Activity log (most recent first, max 200)
    activity_log: list[str] = field(default_factory=list)
    total_trades: int = 0

    # Footer stats
    avg_bet: float = 0.0
    _orders_notional: float = 0.0  # Total notional of all orders for avg_bet
    best_trade: float = 0.0
    worst_trade: float = 0.0
    sharpe: float = 0.0
    runway_pct: float = 100.0

    # Per-strategy stats
    strategy_stats: dict[str, StrategyStats] = field(default_factory=lambda: {
        "arbitrage": StrategyStats(name="Arbitrage"),
        "liquidity": StrategyStats(name="LP Rewards"),
        "copy_trading": StrategyStats(name="Copy Trading"),
        "synth_edge": StrategyStats(name="Synth Edge"),
    })

    # Status
    is_halted: bool = False
    is_dry_run: bool = False

    # LP controls (toggled via dashboard)
    lp_auto_close: bool = False  # If True, sell filled positions immediately

    def add_log(self, message: str) -> None:
        """Add a message to the activity log (capped at 200)."""
        self.activity_log.insert(0, message)
        if len(self.activity_log) > 200:
            self.activity_log = self.activity_log[:200]


def apply_event(state: DashboardState, event: BotEvent) -> None:
    """Update dashboard state from a bot event."""
    d = event.data
    ts = event.timestamp.strftime("%H:%M:%S")

    if event.type == EventType.TRADE_EXECUTED:
        is_resting = d.get("is_resting", False)
        size = d.get("size", 0) * d.get("price", 0)
        state.total_trades += 1
        state._orders_notional += size  # All orders for avg_bet

        # Only count actual fills as volume (not resting GTC orders)
        if not is_resting:
            state.daily_volume += size

        # Use real inventory balance if provided
        if "balance" in d:
            state.balance = d["balance"]
        if "positions_value" in d:
            state.positions_value = d["positions_value"]
            portfolio = state.balance + state.positions_value
            state.total_pnl = portfolio - state.initial_balance
        state.balance_history.append(state.balance + state.positions_value)

        if d.get("success", False):
            state.wins += 1
        else:
            state.losses += 1

        symbol = d.get("market", "???")
        label = "RESTING" if is_resting else "ORDER"
        state.add_log(f"{ts} | {label} ${size:.2f} → {symbol}")

        # Per-strategy tracking
        skey = _resolve_strategy_key(d.get("strategy", ""))
        if skey and skey in state.strategy_stats:
            ss = state.strategy_stats[skey]
            ss.trades += 1
            ss.pnl = state.total_pnl  # Use overall P&L from inventory balance
            ss.order_notional += size  # All orders (fills + resting)
            if not is_resting:
                ss.volume += size
            ss.status = "active"

    elif event.type == EventType.EDGE_DETECTED:
        market = d.get("market", "")
        price = d.get("price", 0)
        fair = d.get("fair", 0)
        edge = d.get("edge", 0)
        state.add_log(f'{ts} | Edge: "{market}" @ {price:.2f} (fair {fair:.2f})')

        # Per-strategy tracking
        skey = _resolve_strategy_key(d.get("strategy", ""))
        if skey and skey in state.strategy_stats:
            state.strategy_stats[skey].signals += 1

    elif event.type == EventType.MARKET_SCANNED:
        count = d.get("count", d.get("markets_checked", d.get("markets_quoted", 0)))
        state.markets_scanned = d.get("total_scanned", state.markets_scanned)
        state.avg_edge = d.get("avg_edge", state.avg_edge)
        state.markets = d.get("markets", state.markets)
        # Append a portfolio snapshot so the chart always grows
        state.balance_history.append(state.balance + state.positions_value)
        if len(state.balance_history) > 300:
            state.balance_history = state.balance_history[-300:]
        state.add_log(f"{ts} | {count} contracts checked, waiting")

        # Per-strategy tracking
        skey = _resolve_strategy_key(d.get("strategy", ""))
        if skey and skey in state.strategy_stats:
            ss = state.strategy_stats[skey]
            ss.signals = d.get("signals", ss.signals)
            ss.last_scan = ts
            ss.status = "scanning"

    elif event.type == EventType.ORDER_RESOLVED:
        pnl = d.get("pnl", 0)
        sign = "+" if pnl >= 0 else ""
        state.add_log(f"{ts} | RESOLVED {sign}${pnl:.2f}")

    elif event.type == EventType.DRAWDOWN_HALT:
        state.is_halted = True
        state.add_log(f"{ts} | ⚠ DRAWDOWN HALT — trading stopped")

    elif event.type == EventType.STRATEGY_ERROR:
        error = d.get("error", "unknown")
        strategy = d.get("strategy", "")
        state.add_log(f"{ts} | ERROR [{strategy}]: {error}")

        # Per-strategy tracking
        skey = _resolve_strategy_key(strategy)
        if skey and skey in state.strategy_stats:
            state.strategy_stats[skey].status = "error"

    # Update footer stats
    if state.total_trades > 0:
        state.avg_bet = state._orders_notional / state.total_trades


async def process_events(state: DashboardState, event_bus: EventBus) -> None:
    """Background loop: read events from bus and update state."""
    while True:
        try:
            event = await asyncio.wait_for(event_bus.get(), timeout=1.0)
            apply_event(state, event)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
