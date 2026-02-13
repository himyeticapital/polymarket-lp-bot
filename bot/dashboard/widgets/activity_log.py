"""Scrolling activity log showing trades, scans, and resolutions."""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from bot.dashboard.state import DashboardState


class ActivityLog(Widget):
    """Real-time scrolling activity log."""

    DEFAULT_CSS = """
    ActivityLog {
        width: 2fr;
        height: 100%;
        border: solid #333;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def compose(self):
        yield Static(id="log-content")

    def update_log(self, state: DashboardState) -> None:
        """Redraw the activity log with color-coded entries."""
        header = (
            f"[bold]ACTIVITY LOG[/]"
            f"{'':>20}[bold]{state.total_trades} TRADES[/]\n\n"
        )

        lines = []
        # Show most recent entries (reversed so newest at bottom for readability)
        entries = state.activity_log[:30]  # Show last 30
        for entry in reversed(entries):
            colored = self._colorize(entry)
            lines.append(colored)

        content = header + "\n".join(lines) if lines else header + "[dim]No activity yet...[/]"
        self.query_one("#log-content", Static).update(content)

    @staticmethod
    def _colorize(entry: str) -> str:
        """Apply color based on entry type."""
        if "ORDER" in entry:
            return f"[cyan]{entry}[/]"
        if "Edge:" in entry:
            return f"[yellow]{entry}[/]"
        if "RESOLVED" in entry:
            if "+" in entry.split("RESOLVED")[1]:
                return f"[green]{entry}[/]"
            return f"[red]{entry}[/]"
        if "HALT" in entry or "ERROR" in entry:
            return f"[bold red]{entry}[/]"
        if "checked" in entry:
            return f"[dim]{entry}[/]"
        return entry
