"""Bottom footer stats bar: Avg Bet, Best/Worst, Sharpe, Runway."""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from bot.dashboard.state import DashboardState


class FooterStats(Widget):
    """Bottom stats bar showing aggregate performance metrics."""

    DEFAULT_CSS = """
    FooterStats {
        height: 3;
        border: solid #333;
        padding: 0 1;
        content-align: center middle;
    }
    """

    def compose(self):
        yield Static(id="footer-content")

    def update_footer(self, state: DashboardState) -> None:
        """Redraw footer stats."""
        best_color = "green" if state.best_trade >= 0 else "red"
        worst_color = "red"

        # Runway color
        if state.runway_pct > 60:
            run_color = "green"
        elif state.runway_pct > 30:
            run_color = "yellow"
        else:
            run_color = "red"

        best_sign = "+" if state.best_trade >= 0 else ""
        worst_sign = "+" if state.worst_trade >= 0 else ""

        halted = "  [bold red]⚠ HALTED[/]" if state.is_halted else ""

        content = (
            f"  AVG BET [bold]${state.avg_bet:.2f}[/]"
            f"   BEST [{best_color}]{best_sign}${state.best_trade:.2f}[/]"
            f"   WORST [{worst_color}]{worst_sign}${state.worst_trade:.2f}[/]"
            f"   SHARPE [bold]{state.sharpe:.2f}[/]"
            f"   [{run_color} bold]RUNWAY {state.runway_pct:.0f}%[/]"
            f"{halted}"
        )

        self.query_one("#footer-content", Static).update(content)
