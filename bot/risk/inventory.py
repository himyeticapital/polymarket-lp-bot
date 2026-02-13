"""Inventory manager — tracks balance, positions, and exposure."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from bot.types import OrderResult, Position

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.data.database import Database

logger = structlog.get_logger(__name__)


class InventoryManager:
    """In-memory tracker for balance and open positions.

    Periodically synced with the CLOB API via ``refresh_from_api``
    and updated locally on each fill via ``update_on_fill``.
    """

    def __init__(self, config: BotConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self.balance: float = config.starting_balance_usd
        self.positions: dict[str, Position] = {}

    # ------------------------------------------------------------------
    # API sync
    # ------------------------------------------------------------------

    async def refresh_from_api(self, clob_client: object) -> None:
        """Pull latest balance and positions from the CLOB API."""
        try:
            balance = await clob_client.get_balance()  # type: ignore[attr-defined]
            self.balance = float(balance)
        except Exception:
            logger.warning("inventory.balance_refresh_failed")

        try:
            raw_positions = await clob_client.get_positions()  # type: ignore[attr-defined]
            self.positions.clear()
            for p in raw_positions:
                self.positions[p.token_id] = p
        except Exception:
            logger.warning("inventory.positions_refresh_failed")

        logger.debug(
            "inventory.refreshed",
            balance=round(self.balance, 2),
            open_positions=len(self.positions),
        )

    # ------------------------------------------------------------------
    # Local updates on fills
    # ------------------------------------------------------------------

    def update_on_fill(self, result: OrderResult) -> None:
        """Update balance and positions after a fill."""
        if not result.success:
            return

        fill_price = result.fill_price or result.signal.price
        fill_size = result.fill_size or result.signal.size
        cost = fill_price * fill_size + result.fee_paid

        sig = result.signal
        token_id = sig.token_id

        if sig.side.value == "BUY":
            self.balance -= cost
            existing = self.positions.get(token_id)
            if existing:
                total_size = existing.size + fill_size
                avg_price = (
                    (existing.avg_entry_price * existing.size + fill_price * fill_size)
                    / total_size
                )
                existing.size = total_size
                existing.avg_entry_price = avg_price
            else:
                self.positions[token_id] = Position(
                    condition_id=sig.condition_id,
                    token_id=token_id,
                    outcome="",
                    size=fill_size,
                    avg_entry_price=fill_price,
                    strategy=sig.strategy,
                    current_price=fill_price,
                )
        else:
            # SELL — add proceeds, reduce position
            self.balance += fill_price * fill_size - result.fee_paid
            existing = self.positions.get(token_id)
            if existing:
                existing.size -= fill_size
                if existing.size <= 0:
                    del self.positions[token_id]

        logger.debug(
            "inventory.updated",
            balance=round(self.balance, 2),
            positions=len(self.positions),
        )

    # ------------------------------------------------------------------
    # Exposure queries
    # ------------------------------------------------------------------

    def get_total_exposure(self) -> float:
        """Sum of (size * avg_entry_price) across all positions."""
        return sum(p.size * p.avg_entry_price for p in self.positions.values())

    def get_market_exposure(self, condition_id: str) -> float:
        """Total exposure for a specific market (condition_id)."""
        return sum(
            p.size * p.avg_entry_price
            for p in self.positions.values()
            if p.condition_id == condition_id
        )

    def get_open_position_count(self) -> int:
        return len(self.positions)

    def get_unrealized_pnl(self) -> float:
        """Sum of unrealized P&L across all positions."""
        return sum(
            (p.current_price - p.avg_entry_price) * p.size
            for p in self.positions.values()
        )
