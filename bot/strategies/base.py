"""Abstract base class for all trading strategies."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import structlog

from bot.constants import EventType
from bot.types import BotEvent, EventBus, Signal

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.data.database import Database
    from bot.execution.order_manager import OrderManager
    from bot.risk.manager import RiskManager

logger = structlog.get_logger(__name__)


class BaseStrategy(ABC):
    """Base class every strategy inherits from.

    Subclasses implement ``scan()`` to produce signals and
    ``on_shutdown()`` for cleanup.  The default ``run()`` loop
    repeatedly scans, sends signals through the order manager,
    then sleeps for a strategy-specific interval.
    """

    # Subclasses set this to control loop cadence.
    scan_interval_sec: float = 30.0

    def __init__(
        self,
        config: BotConfig,
        clob_client: object,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        db: Database,
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.clob_client = clob_client
        self.order_manager = order_manager
        self.risk_manager = risk_manager
        self.db = db
        self.event_bus = event_bus
        self._running = False

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def scan(self) -> list[Signal]:
        """Run one scan cycle and return trade signals."""

    @abstractmethod
    async def on_shutdown(self) -> None:
        """Release resources (e.g. cancel LP orders)."""

    # ------------------------------------------------------------------
    # Default run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop: scan -> execute -> sleep."""
        self._running = True
        name = self.__class__.__name__
        logger.info("strategy.start", strategy=name)

        while self._running:
            try:
                signals = await self.scan()
                if signals:
                    await self.order_manager.execute_batch(signals)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("strategy.scan_error", strategy=name)
                self._publish_event(
                    EventType.STRATEGY_ERROR,
                    {"strategy": name, "error": "scan cycle failed"},
                )
            await asyncio.sleep(self.scan_interval_sec)

        logger.info("strategy.stopped", strategy=name)

    def stop(self) -> None:
        """Signal the run loop to exit after the current cycle."""
        self._running = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _publish_event(self, event_type: EventType, data: dict) -> None:
        """Publish an event to the bus (non-blocking)."""
        try:
            self.event_bus.put_nowait(BotEvent(type=event_type, data=data))
        except asyncio.QueueFull:
            logger.warning("event_bus.full", event_type=event_type)
