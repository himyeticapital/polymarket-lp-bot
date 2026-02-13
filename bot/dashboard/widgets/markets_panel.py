"""Live crypto markets panel showing prices, edges, and fair values."""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from bot.dashboard.state import DashboardState


class MarketsPanel(Widget):
    """Shows monitored crypto markets with edge calculations."""

    DEFAULT_CSS = """
    MarketsPanel {
        width: 1fr;
        height: 100%;
        border: solid #333;
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static(id="markets-content")

    def update_markets(self, state: DashboardState) -> None:
        """Redraw the markets panel."""
        lines = ["[bold]5MIN CRYPTO MARKETS[/]   [dim]UPDATE 0.3S[/]\n"]

        for m in state.markets[:6]:  # Show top 6
            name = m.get("name", "???")
            price = m.get("price", 0)
            edge = m.get("edge", 0)
            fair = m.get("fair", 0)

            edge_color = "green" if edge > 0 else "red"
            lines.append(
                f"[bold white]{name}[/]   [bold]{price:.2f}[/]\n"
                f" [{edge_color}]edge {edge:+.3f}[/]   [dim]fair {fair:.2f}[/]\n"
            )

        # Footer
        lines.append(
            f"\n[dim]MARKETS SCANNED {state.markets_scanned} · "
            f"AVG EDGE {state.avg_edge:.2f}[/]"
        )

        self.query_one("#markets-content", Static).update("\n".join(lines))
