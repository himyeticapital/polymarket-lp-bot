"""Order manager — orchestrates signal execution pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from bot.constants import MAX_BATCH_ORDERS, EventType
from bot.data.models import insert_trade, update_daily_volume
from bot.execution.dry_run import DryRunExecutor
from bot.risk.anti_detection import jitter_size
from bot.types import BotEvent, EventBus, OrderResult, Signal

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.data.database import Database
    from bot.risk.inventory import InventoryManager
    from bot.risk.manager import RiskManager

logger = structlog.get_logger(__name__)


class OrderManager:
    """Central execution pipeline.

    For every signal the pipeline is:
    1. Risk check  (RiskManager)
    2. Anti-detection jitter on size
    3. Execute  (dry-run or live CLOB)
    4. Update inventory
    5. Log trade to DB / update daily volume
    6. Publish event to bus
    """

    def __init__(
        self,
        config: BotConfig,
        clob_client: object,
        risk_manager: RiskManager,
        inventory: InventoryManager,
        db: Database,
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.clob_client = clob_client
        self.risk_manager = risk_manager
        self.inventory = inventory
        self.db = db
        self.event_bus = event_bus
        self.dry_run = config.dry_run
        self._dry_executor = DryRunExecutor()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_signal(self, signal: Signal) -> OrderResult:
        """Run one signal through the full pipeline."""
        # 1. Risk check
        verdict = await self.risk_manager.check_signal(signal)
        if not verdict.allowed:
            logger.info("order.rejected", reason=verdict.reason, token=signal.token_id[:12])
            return OrderResult(
                signal=signal,
                success=False,
                error=verdict.reason,
                is_dry_run=self.dry_run,
            )

        # Use adjusted signal if risk manager modified size
        active_signal = verdict.adjusted_signal or signal

        # 2. Anti-detection jitter
        from dataclasses import replace
        jittered_size = jitter_size(active_signal.size, self.config.size_jitter_pct)
        active_signal = replace(active_signal, size=jittered_size)

        # 3. Execute
        if self.dry_run:
            result = await self._dry_executor.execute(active_signal)
        else:
            result = await self._execute_live(active_signal)

        # 4. Update inventory
        self.inventory.update_on_fill(result)

        # 5. Log to DB
        await self._log_trade(result)

        # 6. Publish event
        self._publish_trade_event(result)

        return result

    async def execute_batch(self, signals: list[Signal]) -> list[OrderResult]:
        """Execute multiple signals, respecting the batch limit."""
        results: list[OrderResult] = []
        for batch_start in range(0, len(signals), MAX_BATCH_ORDERS):
            batch = signals[batch_start : batch_start + MAX_BATCH_ORDERS]
            for sig in batch:
                result = await self.execute_signal(sig)
                results.append(result)
        return results

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order."""
        if self.dry_run:
            return await self._dry_executor.cancel(order_id)
        try:
            await self.clob_client.cancel_order(order_id)  # type: ignore[attr-defined]
            return True
        except Exception:
            logger.exception("order.cancel_failed", order_id=order_id)
            return False

    async def cancel_all_orders(self) -> int:
        """Cancel all open orders.  Returns count of cancelled orders."""
        if self.dry_run:
            logger.info("order.cancel_all_dry_run")
            return 0
        try:
            result = await self.clob_client.cancel_all()  # type: ignore[attr-defined]
            count = result if isinstance(result, int) else 0
            logger.info("order.cancel_all", cancelled=count)
            return count
        except Exception:
            logger.exception("order.cancel_all_failed")
            return 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _execute_live(self, signal: Signal) -> OrderResult:
        """Place a real order via the CLOB client."""
        try:
            resp = await self.clob_client.create_and_post_limit_order(  # type: ignore[attr-defined]
                token_id=signal.token_id,
                price=signal.price,
                size=signal.size,
                side=signal.side.value,
                order_type=signal.order_type.value,
            )
            # GTC orders may be "live" (resting) not "filled"
            status = resp.get("status", "").lower()
            is_resting = status == "live"
            fill_price = float(resp.get("fillPrice", 0)) or (0.0 if is_resting else signal.price)
            fill_size = float(resp.get("fillSize", 0)) or (0.0 if is_resting else signal.size)
            return OrderResult(
                signal=signal,
                success=True,
                order_id=resp.get("orderID") or resp.get("id"),
                fill_price=fill_price,
                fill_size=fill_size,
                fee_paid=float(resp.get("fee", 0.0)),
                is_dry_run=False,
            )
        except Exception as exc:
            logger.exception("order.live_failed", token=signal.token_id[:12])
            return OrderResult(
                signal=signal,
                success=False,
                error=str(exc),
                is_dry_run=False,
            )

    async def _log_trade(self, result: OrderResult) -> None:
        """Persist trade and update daily volume."""
        try:
            await insert_trade(self.db, result)
            if result.success:
                fill_size = result.fill_size or result.signal.size
                fill_price = result.fill_price or result.signal.price
                volume = fill_size * fill_price
                await update_daily_volume(self.db, result.signal.strategy, volume)
        except Exception:
            logger.exception("order.log_failed")

    def _publish_trade_event(self, result: OrderResult) -> None:
        fill_price = result.fill_price or result.signal.price
        fill_size = result.fill_size or result.signal.size
        event = BotEvent(
            type=EventType.TRADE_EXECUTED,
            data={
                "strategy": result.signal.strategy,
                "token_id": result.signal.token_id,
                "side": result.signal.side,
                "price": fill_price,
                "size": fill_size,
                "success": result.success,
                "order_id": result.order_id,
                "is_dry_run": result.is_dry_run,
                "error": result.error,
                "market": result.signal.market_question or "???",
                "pnl": 0.0,  # Real P&L tracked on resolution, not at order time
                "balance": self.inventory.balance,
                "positions_value": self.inventory.get_total_exposure(),
            },
        )
        try:
            self.event_bus.put_nowait(event)
        except Exception:
            pass
