"""Copy trading strategy — mirror positions of tracked wallets."""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from bot.constants import EventType, OrderType, Side, Strategy
from bot.data.models import get_state, set_state
from bot.risk.anti_detection import jitter_delay
from bot.strategies.base import BaseStrategy
from bot.types import Signal

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.data.database import Database
    from bot.execution.order_manager import OrderManager
    from bot.risk.manager import RiskManager
    from bot.types import EventBus

logger = structlog.get_logger(__name__)

_STATE_PREFIX = "copy_snapshot_"


@dataclass
class _PositionSnapshot:
    """Lightweight representation of a tracked wallet's position."""
    token_id: str
    condition_id: str
    outcome: str
    size: float
    price: float
    market_question: str = ""


class CopyTradingStrategy(BaseStrategy):
    """Poll tracked wallets and mirror their position changes.

    For each address in ``config.copy_traders``:
    * Fetch current positions from the Polymarket Data API.
    * Diff against the last stored snapshot.
    * Generate BUY signals for new / increased positions.
    * Generate SELL signals for closed / decreased positions.
    * Add random delay for anti-detection.
    """

    def __init__(
        self,
        config: BotConfig,
        clob_client: object,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        db: Database,
        event_bus: EventBus,
    ) -> None:
        super().__init__(config, clob_client, order_manager, risk_manager, db, event_bus)
        self.scan_interval_sec = jitter_delay(
            config.copy_poll_interval_sec, config.timing_jitter_pct
        )

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    async def scan(self) -> list[Signal]:
        signals: list[Signal] = []

        for address in self.config.copy_traders:
            try:
                addr_signals = await self._check_trader(address)
                signals.extend(addr_signals)
            except Exception:
                logger.exception("copy.check_failed", address=address)

        return signals

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _check_trader(self, address: str) -> list[Signal]:
        """Compare current positions against stored snapshot."""
        current = await self._fetch_positions(address)
        previous = await self._load_snapshot(address)
        signals: list[Signal] = []

        current_map = {p.token_id: p for p in current}
        previous_map = {p.token_id: p for p in previous}

        # Detect new or increased positions -> BUY
        for token_id, pos in current_map.items():
            prev = previous_map.get(token_id)
            if prev is None or pos.size > prev.size:
                delta = pos.size if prev is None else pos.size - prev.size
                scaled = delta * self.config.copy_scale_factor
                if scaled * pos.price < self.config.copy_min_trade_usd:
                    continue

                signals.append(
                    Signal(
                        strategy=Strategy.COPY_TRADING,
                        token_id=pos.token_id,
                        condition_id=pos.condition_id,
                        side=Side.BUY,
                        price=pos.price,
                        size=scaled,
                        order_type=OrderType.GTC,
                        reason=f"copy {address[:8]}.. +{delta:.1f}",
                        market_question=pos.market_question,
                    )
                )

        # Detect closed or decreased positions -> SELL
        for token_id, prev in previous_map.items():
            cur = current_map.get(token_id)
            if cur is None or cur.size < prev.size:
                delta = prev.size if cur is None else prev.size - cur.size
                scaled = delta * self.config.copy_scale_factor

                signals.append(
                    Signal(
                        strategy=Strategy.COPY_TRADING,
                        token_id=prev.token_id,
                        condition_id=prev.condition_id,
                        side=Side.SELL,
                        price=prev.price,
                        size=scaled,
                        order_type=OrderType.GTC,
                        reason=f"copy {address[:8]}.. -{delta:.1f}",
                        market_question=prev.market_question,
                    )
                )

        if signals:
            # Anti-detection: random delay before execution
            delay = random.uniform(0, self.config.copy_max_delay_sec)
            await asyncio.sleep(delay)

            self._publish_event(
                EventType.EDGE_DETECTED,
                {"strategy": Strategy.COPY_TRADING, "address": address, "signals": len(signals)},
            )

        await self._save_snapshot(address, current)
        return signals

    async def _fetch_positions(self, address: str) -> list[_PositionSnapshot]:
        """Fetch positions from the Data API for *address*."""
        try:
            raw = await self.clob_client.get_positions(address)  # type: ignore[attr-defined]
        except Exception:
            logger.warning("copy.fetch_failed", address=address)
            return []

        snapshots: list[_PositionSnapshot] = []
        for p in raw:
            snapshots.append(
                _PositionSnapshot(
                    token_id=p.token_id,
                    condition_id=p.condition_id,
                    outcome=p.outcome,
                    size=p.size,
                    price=p.current_price,
                    market_question=getattr(p, "market_question", ""),
                )
            )
        return snapshots

    # ------------------------------------------------------------------
    # Snapshot persistence (via bot_state table)
    # ------------------------------------------------------------------

    async def _load_snapshot(self, address: str) -> list[_PositionSnapshot]:
        raw = await get_state(self.db, f"{_STATE_PREFIX}{address}")
        if raw is None:
            return []
        try:
            return [_PositionSnapshot(**item) for item in json.loads(raw)]
        except (json.JSONDecodeError, TypeError):
            return []

    async def _save_snapshot(self, address: str, positions: list[_PositionSnapshot]) -> None:
        data = json.dumps([
            {
                "token_id": p.token_id,
                "condition_id": p.condition_id,
                "outcome": p.outcome,
                "size": p.size,
                "price": p.price,
                "market_question": p.market_question,
            }
            for p in positions
        ])
        await set_state(self.db, f"{_STATE_PREFIX}{address}", data)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def on_shutdown(self) -> None:
        logger.info("copy.shutdown")
