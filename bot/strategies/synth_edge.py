"""Synth Edge strategy — exploit divergence between Synth and Polymarket."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from bot.constants import EventType, OrderType, Side, Strategy
from bot.data.models import insert_synth_signal
from bot.risk.anti_detection import jitter_delay
from bot.strategies.base import BaseStrategy
from bot.types import Signal, SynthForecast
from bot.utils.math import clamp, kelly_criterion

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.data.database import Database
    from bot.execution.order_manager import OrderManager
    from bot.risk.manager import RiskManager
    from bot.types import EventBus

logger = structlog.get_logger(__name__)


class SynthEdgeStrategy(BaseStrategy):
    """Compare Synth API probability forecasts with Polymarket prices.

    When the absolute edge exceeds ``synth_edge_threshold``:
    * Positive edge (synth > poly) -> BUY the UP token.
    * Negative edge (synth < poly) -> BUY the DOWN token.

    Position size is computed via fractional Kelly criterion and capped
    at ``max_trade_size_usd``.
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
            config.synth_poll_interval_sec, config.timing_jitter_pct
        )

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    async def scan(self) -> list[Signal]:
        signals: list[Signal] = []

        for asset in self.config.synth_assets_list:
            try:
                forecast = await self._fetch_forecast(asset)
            except Exception:
                logger.exception("synth.fetch_failed", asset=asset)
                continue

            if forecast is None:
                continue

            edge = forecast.synth_prob_up - forecast.poly_prob_up
            abs_edge = abs(edge)

            if abs_edge < self.config.synth_edge_threshold:
                await self._log_signal(forecast, "skip", 0.0)
                continue

            # Determine direction
            if edge > 0:
                token_id = forecast.up_token_id
                price = forecast.poly_prob_up
                reason = f"synth UP edge={edge:+.4f}"
            else:
                token_id = forecast.down_token_id
                price = 1.0 - forecast.poly_prob_up
                reason = f"synth DOWN edge={abs_edge:+.4f}"

            if not token_id or price <= 0 or price >= 1:
                await self._log_signal(forecast, "invalid", 0.0)
                continue

            # Kelly sizing
            kelly_frac = kelly_criterion(abs_edge, price, self.config.synth_kelly_fraction)
            size_usd = clamp(
                kelly_frac * self.config.starting_balance_usd,
                0.0,
                self.config.max_trade_size_usd,
            )

            if size_usd <= 0:
                await self._log_signal(forecast, "kelly_zero", 0.0)
                continue

            signal = Signal(
                strategy=Strategy.SYNTH_EDGE,
                token_id=token_id,
                condition_id=f"synth_{asset.lower()}",
                side=Side.BUY,
                price=price,
                size=size_usd / price,
                order_type=OrderType.GTC,
                reason=reason,
                edge=abs_edge,
                confidence=forecast.synth_prob_up,
                market_question=f"{asset} price movement",
            )
            signals.append(signal)

            self._publish_event(
                EventType.EDGE_DETECTED,
                {
                    "strategy": Strategy.SYNTH_EDGE,
                    "asset": asset,
                    "edge": edge,
                    "synth_prob": forecast.synth_prob_up,
                    "poly_prob": forecast.poly_prob_up,
                    "kelly_size": size_usd,
                },
            )

            await self._log_signal(forecast, "trade", size_usd)

            logger.info(
                "synth.signal",
                asset=asset,
                edge=round(edge, 4),
                kelly_usd=round(size_usd, 2),
            )

        return signals

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_forecast(self, asset: str) -> SynthForecast | None:
        """Retrieve Synth API forecast and Polymarket price for *asset*."""
        try:
            return await self.clob_client.get_hourly_up_down(asset)  # type: ignore[attr-defined]
        except Exception:
            return None

    async def _log_signal(self, forecast: SynthForecast, action: str, kelly_size: float) -> None:
        try:
            await insert_synth_signal(self.db, forecast, action, kelly_size)
        except Exception:
            logger.warning("synth.log_failed", asset=forecast.asset)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def on_shutdown(self) -> None:
        logger.info("synth.shutdown")
