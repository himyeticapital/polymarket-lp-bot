"""Message formatting helpers for Telegram notifications."""

from __future__ import annotations

from bot.types import OrderResult, Signal
from bot.utils.time import timestamp_short


def format_trade_alert(result: OrderResult) -> str:
    """Format a trade execution result for Telegram."""
    sig = result.signal
    status = "FILLED" if result.success else "REJECTED"
    mode = " [DRY]" if result.is_dry_run else ""
    side = sig.side.value
    price = result.fill_price or sig.price
    size = result.fill_size or sig.size
    cost = round(price * size, 2)

    lines = [
        f"{side} {status}{mode}",
        f"Strategy: {sig.strategy.value}",
        f"Market: {sig.market_question[:60]}" if sig.market_question else None,
        f"Price: {price:.4f}  Size: {size:.2f}  Cost: ${cost}",
    ]

    if result.order_id:
        lines.append(f"Order: {result.order_id}")
    if result.error:
        lines.append(f"Error: {result.error}")
    if sig.edge is not None:
        lines.append(f"Edge: {sig.edge:.4f}")

    lines.append(f"Time: {timestamp_short()}")
    return "\n".join(line for line in lines if line is not None)


def format_daily_summary(stats: dict, balance: float) -> str:
    """Format end-of-day summary."""
    total = stats.get("total_trades", 0)
    wins = stats.get("wins", 0)
    pnl = stats.get("total_pnl", 0.0)
    avg = stats.get("avg_bet", 0.0)
    best = stats.get("best_trade", 0.0)
    worst = stats.get("worst_trade", 0.0)
    win_rate = (wins / total * 100) if total > 0 else 0.0

    return (
        "--- Daily Summary ---\n"
        f"Trades: {total}  |  Win rate: {win_rate:.1f}%\n"
        f"P&L: ${pnl:+.2f}\n"
        f"Avg bet: ${avg:.2f}\n"
        f"Best: ${best:+.2f}  Worst: ${worst:+.2f}\n"
        f"Balance: ${balance:.2f}"
    )


def format_drawdown_alert(balance: float, threshold: float) -> str:
    """Format a critical drawdown alert."""
    return (
        "DRAWDOWN ALERT\n"
        f"Balance: ${balance:.2f}\n"
        f"Threshold: ${threshold:.2f}\n"
        "All trading has been HALTED."
    )


def format_edge_detected(signal: Signal) -> str:
    """Format an edge detection notification."""
    lines = [
        f"Edge Detected [{signal.strategy.value}]",
        f"Market: {signal.market_question[:60]}" if signal.market_question else None,
        f"Side: {signal.side.value}  Price: {signal.price:.4f}",
    ]
    if signal.edge is not None:
        lines.append(f"Edge: {signal.edge:.4f}")
    if signal.confidence is not None:
        lines.append(f"Confidence: {signal.confidence:.2%}")
    lines.append(f"Time: {timestamp_short()}")
    return "\n".join(line for line in lines if line is not None)
