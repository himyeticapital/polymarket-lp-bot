"""Risk manager — pre-trade validation with drawdown kill switch."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import structlog

from bot.constants import EventType
from bot.data.models import get_today_volume
from bot.types import BotEvent, EventBus, RiskVerdict, Signal

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.data.database import Database
    from bot.risk.inventory import InventoryManager

logger = structlog.get_logger(__name__)


class RiskManager:
    """Centralised pre-trade risk gate.

    Every signal passes through ``check_signal`` before execution.
    Checks run in order; the first failure short-circuits with a
    REJECT verdict.  The $250 drawdown kill switch is **always**
    evaluated first and halts all trading immediately.
    """

    def __init__(
        self,
        config: BotConfig,
        inventory: InventoryManager,
        db: Database,
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.inventory = inventory
        self.db = db
        self.event_bus = event_bus
        self._halted = False

    @property
    def is_halted(self) -> bool:
        return self._halted

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_signal(self, signal: Signal) -> RiskVerdict:
        """Run all risk checks on *signal*.

        Returns a RiskVerdict that is either ALLOWED (possibly with an
        adjusted signal) or REJECTED with a reason string.
        """
        # 1. Drawdown kill switch — most critical check
        if self._check_drawdown():
            return RiskVerdict(allowed=False, reason="DRAWDOWN HALT — trading suspended")

        # 2. Trade size cap
        trade_usd = signal.size * signal.price
        if trade_usd > self.config.max_trade_size_usd:
            capped_size = self.config.max_trade_size_usd / signal.price
            signal = replace(signal, size=capped_size)
            trade_usd = capped_size * signal.price
            logger.info("risk.size_capped", new_size=round(capped_size, 4))

        # 3. Daily volume cap
        verdict = await self._check_daily_volume(signal, trade_usd)
        if verdict is not None:
            return verdict

        # 4. Open positions limit
        if self.inventory.get_open_position_count() >= self.config.max_open_positions:
            return RiskVerdict(allowed=False, reason="max open positions reached")

        # 5. Per-market exposure (only limit BUY orders; SELL reduces exposure)
        if signal.side.value == "BUY":
            market_exp = self.inventory.get_market_exposure(signal.condition_id)
            if market_exp + trade_usd > self.config.max_per_market_usd:
                remaining = self.config.max_per_market_usd - market_exp
                if remaining <= 0:
                    return RiskVerdict(allowed=False, reason="per-market exposure limit reached")
                signal = replace(signal, size=remaining / signal.price)
                trade_usd = remaining
                logger.info("risk.market_cap_adjusted", remaining=round(remaining, 2))

        # 6. Portfolio-wide exposure
        total_exp = self.inventory.get_total_exposure()
        if total_exp + trade_usd > self.config.max_portfolio_exposure_usd:
            remaining = self.config.max_portfolio_exposure_usd - total_exp
            if remaining <= 0:
                return RiskVerdict(allowed=False, reason="portfolio exposure limit reached")
            signal = replace(signal, size=remaining / signal.price)
            logger.info("risk.portfolio_cap_adjusted", remaining=round(remaining, 2))

        return RiskVerdict(allowed=True, adjusted_signal=signal)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_drawdown(self) -> bool:
        """Return True if portfolio value has breached the drawdown threshold.

        Uses total portfolio value (cash + positions) instead of cash-only,
        since LP positions tie up cash in tokens that retain value.
        """
        if self._halted:
            return True

        portfolio = self.inventory.portfolio_value
        threshold = self.config.drawdown_threshold

        if portfolio <= threshold:
            self._halted = True
            logger.critical(
                "risk.DRAWDOWN_HALT",
                portfolio=round(portfolio, 2),
                cash=round(self.inventory.balance, 2),
                threshold=round(threshold, 2),
            )
            try:
                self.event_bus.put_nowait(
                    BotEvent(
                        type=EventType.DRAWDOWN_HALT,
                        data={"balance": portfolio, "threshold": threshold},
                    )
                )
            except Exception:
                pass
            return True

        # Warning at 80% of max drawdown consumed
        drawdown_used = self.config.starting_balance_usd - portfolio
        if drawdown_used >= self.config.max_drawdown_usd * 0.80:
            logger.warning(
                "risk.drawdown_warning",
                portfolio=round(portfolio, 2),
                cash=round(self.inventory.balance, 2),
                drawdown_used=round(drawdown_used, 2),
            )
            try:
                self.event_bus.put_nowait(
                    BotEvent(
                        type=EventType.DRAWDOWN_WARNING,
                        data={"balance": portfolio, "drawdown_used": drawdown_used},
                    )
                )
            except Exception:
                pass

        return False

    async def _check_daily_volume(
        self, signal: Signal, trade_usd: float
    ) -> RiskVerdict | None:
        """Check daily volume cap.  Returns a REJECT verdict or None."""
        row = await get_today_volume(self.db)
        today_vol = row["total_volume"] if row else 0.0

        if today_vol + trade_usd > self.config.daily_volume_cap_usd:
            remaining = self.config.daily_volume_cap_usd - today_vol
            if remaining <= 0:
                return RiskVerdict(allowed=False, reason="daily volume cap reached")
            # Downsize to fit
            signal = replace(signal, size=remaining / signal.price)
            logger.info("risk.daily_vol_adjusted", remaining=round(remaining, 2))

        return None
