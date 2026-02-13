"""Bayes posterior panel showing probability calculations."""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from bot.dashboard.state import DashboardState


class BayesianPanel(Widget):
    """Shows Bayesian posterior probability calculation in real-time."""

    DEFAULT_CSS = """
    BayesianPanel {
        width: 1fr;
        height: 100%;
        border: solid #333;
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static(id="bayes-content")

    def update_bayes(self, state: DashboardState) -> None:
        """Redraw Bayesian posterior panel."""
        edge_color = "green" if state.bayes_edge > 0 else "red"
        edge_sign = "+" if state.bayes_edge > 0 else ""

        content = (
            "[bold]BAYES POSTERIOR[/]\n\n"
            "  [dim]P(H|E) =[/]\n"
            "   [dim]P(E|H)·P(H)[/]\n"
            "   [dim]/ P(E)[/]\n\n"
            f"  P(H) prior:     [white]{state.prior:.3f}[/]\n"
            f"  P(E|H) likelih: [white]{state.likelihood:.3f}[/]\n"
            f"  P(E) evidence:  [white]{state.evidence:.3f}[/]\n\n"
            f"  [bold white]POSTERIOR: {state.posterior:.3f}[/]\n\n"
            f"  [{edge_color} bold]EDGE {edge_sign}{state.bayes_edge:.3f}[/]\n"
            f"  [{edge_color} bold]FAIR {state.bayes_fair:.2f}[/]\n\n"
            "  [dim]AUTO BAYES · REAL-TIME[/]"
        )

        self.query_one("#bayes-content", Static).update(content)
