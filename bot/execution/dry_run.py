"""Dry-run executor — simulates order execution without API calls."""

from __future__ import annotations

import structlog

from bot.types import OrderResult, Signal

logger = structlog.get_logger(__name__)


class DryRunExecutor:
    """Simulates order execution for paper trading.

    Returns a successful ``OrderResult`` with a fake order ID.
    No real orders are placed on-chain.
    """

    _counter: int = 0

    async def execute(self, signal: Signal) -> OrderResult:
        """Return a simulated fill at the signal's requested price."""
        DryRunExecutor._counter += 1
        order_id = f"DRY-{DryRunExecutor._counter:06d}"

        logger.info(
            "dry_run.execute",
            order_id=order_id,
            side=signal.side,
            price=signal.price,
            size=round(signal.size, 4),
            token=signal.token_id[:12],
        )

        return OrderResult(
            signal=signal,
            success=True,
            order_id=order_id,
            fill_price=signal.price,
            fill_size=signal.size,
            fee_paid=0.0,
            is_dry_run=True,
        )

    async def cancel(self, order_id: str) -> bool:
        """Dry-run cancel always succeeds."""
        logger.info("dry_run.cancel", order_id=order_id)
        return True
