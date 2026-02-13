"""Main Textual TUI dashboard application."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches

from bot.dashboard.state import DashboardState, process_events
from bot.dashboard.widgets.activity_log import ActivityLog
from bot.dashboard.widgets.balance_chart import BalanceChart
from bot.dashboard.widgets.bayesian import BayesianPanel
from bot.dashboard.widgets.footer_stats import FooterStats
from bot.dashboard.widgets.markets_panel import MarketsPanel
from bot.dashboard.widgets.stats_bar import StatsBar
from bot.types import EventBus

DASHBOARD_CSS = """
Screen {
    background: #0a0a0a;
    color: #e0e0e0;
}

Horizontal {
    height: auto;
}

#top-bar {
    height: 5;
}

#chart-row {
    height: 3;
}

#middle-row {
    height: 1fr;
    min-height: 16;
}

#bottom-bar {
    height: 3;
}

StatBox {
    border: solid #1a1a1a;
    background: #111;
}

BalanceChart {
    border: solid #1a1a1a;
    background: #111;
}

MarketsPanel {
    border: solid #1a1a1a;
    background: #111;
}

BayesianPanel {
    border: solid #1a1a1a;
    background: #111;
}

ActivityLog {
    border: solid #1a1a1a;
    background: #111;
}

FooterStats {
    border: solid #1a1a1a;
    background: #111;
}
"""


class DashboardApp(App):
    """Polymarket Bot live trading dashboard."""

    CSS = DASHBOARD_CSS
    TITLE = "Polymarket Bot"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("c", "clear_log", "Clear Log"),
    ]

    def __init__(
        self,
        event_bus: EventBus | None = None,
        state: DashboardState | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.event_bus = event_bus or asyncio.Queue()
        self.state = state or DashboardState()
        self._event_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield StatsBar(id="top-bar")
        yield BalanceChart(id="chart-row")
        with Horizontal(id="middle-row"):
            yield MarketsPanel()
            yield BayesianPanel()
            yield ActivityLog()
        yield FooterStats(id="bottom-bar")

    def on_mount(self) -> None:
        """Start background event processing and periodic refresh."""
        self._event_task = asyncio.create_task(self._event_loop())
        self.set_interval(1.0, self._refresh_widgets)

    async def _event_loop(self) -> None:
        """Process events from the bus and refresh dashboard."""
        await process_events(self.state, self.event_bus)

    def _refresh_widgets(self) -> None:
        """Update all widgets from current state."""
        try:
            self.query_one(StatsBar).update_stats(self.state)
            self.query_one(BalanceChart).update_chart(self.state)
            self.query_one(MarketsPanel).update_markets(self.state)
            self.query_one(BayesianPanel).update_bayes(self.state)
            self.query_one(ActivityLog).update_log(self.state)
            self.query_one(FooterStats).update_footer(self.state)
        except NoMatches:
            pass

    def action_clear_log(self) -> None:
        """Clear the activity log."""
        self.state.activity_log.clear()

    async def action_quit(self) -> None:
        """Quit the application."""
        if self._event_task:
            self._event_task.cancel()
        self.exit()


# Allow standalone testing: python -m bot.dashboard.app
if __name__ == "__main__":
    import random
    from bot.constants import EventType
    from bot.types import BotEvent
    from bot.utils.time import utc_now

    async def mock_events(bus: EventBus) -> None:
        """Generate mock events for testing the dashboard."""
        markets = [
            {"name": "BTC > $67,500", "price": 0.54, "edge": -0.040, "fair": 0.58},
            {"name": "ETH > $1,966", "price": 0.48, "edge": 0.010, "fair": 0.47},
            {"name": "SOL > $81.00", "price": 0.66, "edge": 0.300, "fair": 0.30},
            {"name": "XRP > $1.39", "price": 0.37, "edge": -0.180, "fair": 0.55},
        ]
        await bus.put(BotEvent(
            type=EventType.MARKET_SCANNED,
            data={"count": 94, "total_scanned": 94, "avg_edge": 0.15, "markets": markets},
        ))
        while True:
            await asyncio.sleep(random.uniform(2, 5))
            event_type = random.choice([
                EventType.TRADE_EXECUTED,
                EventType.EDGE_DETECTED,
                EventType.MARKET_SCANNED,
            ])
            if event_type == EventType.TRADE_EXECUTED:
                pnl = random.uniform(-12, 32)
                await bus.put(BotEvent(type=event_type, data={
                    "pnl": pnl, "size": random.uniform(5, 25),
                    "price": random.uniform(0.3, 0.7),
                    "market": random.choice(["BTC 5min", "ETH 5min", "SOL 5min"]),
                    "side": "BUY",
                }))
            elif event_type == EventType.EDGE_DETECTED:
                m = random.choice(markets)
                await bus.put(BotEvent(type=event_type, data={
                    "market": m["name"], "price": m["price"],
                    "fair": m["fair"], "edge": m["edge"],
                    "prior": 0.346, "likelihood": 0.890, "evidence": 0.449,
                }))
            else:
                await bus.put(BotEvent(type=event_type, data={
                    "count": random.randint(500, 1000),
                    "total_scanned": 94, "avg_edge": 0.15, "markets": markets,
                }))

    bus: EventBus = asyncio.Queue()
    state = DashboardState(initial_balance=500.0, balance=500.0)
    app = DashboardApp(event_bus=bus, state=state)

    async def run():
        mock_task = asyncio.create_task(mock_events(bus))
        try:
            await app.run_async()
        finally:
            mock_task.cancel()

    asyncio.run(run())
