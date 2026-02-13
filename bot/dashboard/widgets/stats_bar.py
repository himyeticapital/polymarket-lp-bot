"""Top stats bar: Balance, P&L, Win Rate, Daily Vol, API Costs."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from bot.dashboard.state import DashboardState


class StatBox(Static):
    """A single stat box in the top bar."""

    DEFAULT_CSS = """
    StatBox {
        width: 1fr;
        height: 5;
        border: solid #333;
        padding: 0 1;
        content-align: left middle;
    }
    """


class StatsBar(Widget):
    """Top bar showing key metrics."""

    DEFAULT_CSS = """
    StatsBar {
        height: 5;
        layout: horizontal;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield StatBox(id="stat-balance")
            yield StatBox(id="stat-pnl")
            yield StatBox(id="stat-winrate")
            yield StatBox(id="stat-volume")
            yield StatBox(id="stat-costs")

    def update_stats(self, state: DashboardState) -> None:
        """Refresh all stat boxes from current state."""
        pnl_color = "green" if state.total_pnl >= 0 else "red"
        pnl_sign = "+" if state.total_pnl >= 0 else ""
        pnl_pct = (
            (state.total_pnl / state.initial_balance * 100)
            if state.initial_balance > 0
            else 0
        )

        total_games = state.wins + state.losses
        wr = (state.wins / total_games * 100) if total_games > 0 else 0

        self.query_one("#stat-balance", StatBox).update(
            f"[bold]BALANCE[/]\n[bold white]${state.balance:,.2f}[/]\n"
            f"[dim]init ${state.initial_balance:,.2f}[/]"
        )
        self.query_one("#stat-pnl", StatBox).update(
            f"[bold]TOTAL P&L[/]\n"
            f"[bold {pnl_color}]{pnl_sign}${state.total_pnl:,.2f}[/]\n"
            f"[dim]{pnl_sign}{pnl_pct:.1f}%[/]"
        )
        self.query_one("#stat-winrate", StatBox).update(
            f"[bold]WIN RATE[/]\n[bold white]{wr:.1f}%[/]\n"
            f"[dim]{state.wins}W / {state.losses}L[/]"
        )
        self.query_one("#stat-volume", StatBox).update(
            f"[bold]DAILY VOL[/]\n[bold white]${state.daily_volume:,.2f}[/]\n"
            f"[dim]24h[/]"
        )
        self.query_one("#stat-costs", StatBox).update(
            f"[bold]API COSTS[/]\n[bold white]${state.api_costs:.2f}[/]\n"
            f"[dim]self-funded[/]"
        )
