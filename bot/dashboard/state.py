"""Shared reactive state for the TUI dashboard."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from bot.constants import EventType
from bot.types import BotEvent, EventBus
from bot.utils.math import bayesian_posterior


@dataclass
class DashboardState:
    """Central state that all dashboard widgets read from."""

    # Stats bar
    balance: float = 500.0
    initial_balance: float = 500.0
    total_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    daily_volume: float = 0.0
    api_costs: float = 0.0

    # Balance history (sparkline data points)
    balance_history: list[float] = field(default_factory=lambda: [500.0])

    # Markets panel
    markets: list[dict] = field(default_factory=list)
    markets_scanned: int = 0
    avg_edge: float = 0.0

    # Bayes panel
    prior: float = 0.5
    likelihood: float = 0.5
    evidence: float = 0.5
    posterior: float = 0.5
    bayes_edge: float = 0.0
    bayes_fair: float = 0.5

    # Activity log (most recent first, max 200)
    activity_log: list[str] = field(default_factory=list)
    total_trades: int = 0

    # Footer stats
    avg_bet: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    sharpe: float = 0.0
    runway_pct: float = 100.0

    # Status
    is_halted: bool = False

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
        pnl = d.get("pnl", 0)
        size = d.get("size", 0) * d.get("price", 0)
        state.total_trades += 1
        state.daily_volume += size
        state.total_pnl += pnl
        state.balance += pnl
        state.balance_history.append(state.balance)

        if pnl >= 0:
            state.wins += 1
        else:
            state.losses += 1

        symbol = d.get("market", "???")
        side = d.get("side", "BUY")
        state.add_log(f"{ts} | ORDER ${size:.2f} → {symbol}")

    elif event.type == EventType.EDGE_DETECTED:
        market = d.get("market", "")
        price = d.get("price", 0)
        fair = d.get("fair", 0)
        edge = d.get("edge", 0)
        state.add_log(f'{ts} | Edge: "{market}" @ {price:.2f} (fair {fair:.2f})')

        # Update Bayes posterior
        state.prior = d.get("prior", state.prior)
        state.likelihood = d.get("likelihood", state.likelihood)
        state.evidence = d.get("evidence", state.evidence)
        if state.evidence > 0:
            state.posterior = bayesian_posterior(
                state.prior, state.likelihood, state.evidence
            )
        state.bayes_edge = d.get("edge", state.bayes_edge)
        state.bayes_fair = d.get("fair", state.bayes_fair)

    elif event.type == EventType.MARKET_SCANNED:
        count = d.get("count", 0)
        state.markets_scanned = d.get("total_scanned", state.markets_scanned)
        state.avg_edge = d.get("avg_edge", state.avg_edge)
        state.markets = d.get("markets", state.markets)
        state.add_log(f"{ts} | {count} contracts checked, waiting")

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

    # Update footer stats
    total = state.wins + state.losses
    if total > 0:
        state.avg_bet = state.daily_volume / max(state.total_trades, 1)


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
