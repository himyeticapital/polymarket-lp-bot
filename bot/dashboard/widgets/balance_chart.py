"""Balance history sparkline chart."""

from __future__ import annotations

import math

from textual.widget import Widget
from textual.widgets import Static

from bot.dashboard.state import DashboardState

BLOCKS = " ▁▂▃▄▅▆▇"


def sparkline(values: list[float], width: int = 60) -> str:
    """Generate a unicode sparkline from values."""
    if not values:
        return ""
    # Sample down to width if needed
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    if not sampled:
        return ""

    lo = min(sampled)
    hi = max(sampled)
    rng = hi - lo if hi != lo else 1.0

    chars = []
    for v in sampled:
        idx = int((v - lo) / rng * (len(BLOCKS) - 1))
        idx = max(0, min(idx, len(BLOCKS) - 1))
        chars.append(BLOCKS[idx])
    return "".join(chars)


class BalanceChart(Widget):
    """Balance history bar chart with log scale."""

    DEFAULT_CSS = """
    BalanceChart {
        height: 3;
        border: solid #333;
        padding: 0 1;
    }
    """

    def compose(self):
        yield Static(id="chart-content")

    def update_chart(self, state: DashboardState) -> None:
        """Redraw the sparkline from balance history."""
        history = state.balance_history
        if len(history) < 2:
            line = "[dim]Waiting for data...[/]"
        else:
            # Use log scale for display
            log_vals = [math.log(max(v, 0.01)) for v in history]
            spark = sparkline(log_vals, width=60)

            # Color: green if up overall, red if down
            color = "green" if history[-1] >= history[0] else "red"
            lo = min(history)
            hi = max(history)
            line = (
                f"[{color}]{spark}[/]  "
                f"[bold]BALANCE HISTORY (LOG SCALE)[/]   "
                f"[dim]${hi:,.0f} → ${history[-1]:,.0f}[/]"
            )

        self.query_one("#chart-content", Static).update(line)
