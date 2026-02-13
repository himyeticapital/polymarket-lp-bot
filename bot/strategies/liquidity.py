"""Liquidity provision strategy — earn LP rewards via spread quoting."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from bot.constants import EventType, OrderType, Side, Strategy
from bot.risk.anti_detection import jitter_delay
from bot.strategies.base import BaseStrategy
from bot.types import Market, Signal
from bot.utils.math import reward_score, round_to_tick

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.data.database import Database
    from bot.execution.order_manager import OrderManager
    from bot.risk.manager import RiskManager
    from bot.types import EventBus

logger = structlog.get_logger(__name__)


class LiquidityStrategy(BaseStrategy):
    """Place two-sided GTC limit orders to capture LP incentive rewards.

    Market selection prioritises high ``daily_reward_usd`` relative to
    competition.  Orders are placed at a target spread that maximises
    the quadratic reward score while staying within max_incentive_spread.
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
            config.lp_refresh_interval_sec, config.timing_jitter_pct
        )
        self._active_order_ids: list[str] = []

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    async def scan(self) -> list[Signal]:
        """Select top markets and generate bid/ask signals."""
        # Cancel stale orders before placing new ones.
        await self._cancel_stale_orders()

        try:
            markets = await self.clob_client.get_markets()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("lp.fetch_markets_failed")
            return []

        ranked = self._rank_markets(markets)
        signals: list[Signal] = []

        for market in ranked[: self.config.lp_max_markets]:
            new_signals = await self._quote_market(market)
            signals.extend(new_signals)

        self._publish_event(
            EventType.MARKET_SCANNED,
            {
                "strategy": Strategy.LIQUIDITY,
                "markets_quoted": min(len(ranked), self.config.lp_max_markets),
                "orders_placed": len(signals),
            },
        )

        return signals

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rank_markets(self, markets: list[Market]) -> list[Market]:
        """Rank markets by reward potential (reward / competition)."""
        eligible = [
            m for m in markets
            if m.active and m.max_incentive_spread > 0 and m.daily_reward_usd > 0
        ]
        competition_weights = {"mild": 1.0, "moderate": 0.5, "fierce": 0.25}
        eligible.sort(
            key=lambda m: m.daily_reward_usd * competition_weights.get(m.competition_level, 0.5),
            reverse=True,
        )
        return eligible

    async def _quote_market(self, market: Market) -> list[Signal]:
        """Generate bid + ask signals for a single market."""
        signals: list[Signal] = []
        if len(market.tokens) < 2:
            return signals

        yes_token = next((t for t in market.tokens if t.outcome == "Yes"), None)
        if yes_token is None:
            return signals

        try:
            book = await self.clob_client.get_order_book(yes_token.token_id)  # type: ignore[attr-defined]
        except Exception:
            return signals

        mid = book.midpoint
        if mid is None:
            return signals

        # Target spread: 30-50% of max_incentive_spread for high reward score.
        target_spread = market.max_incentive_spread * 0.40
        size = self.config.lp_order_size_usd

        bid_price = round_to_tick(mid - target_spread / 2)
        ask_price = round_to_tick(mid + target_spread / 2)

        # Midpoint in extreme zone [0, 0.10) or (0.90, 1.0]: must quote both sides.
        # Midpoint in safe zone [0.10, 0.90]: both sides still preferred for full score.
        if bid_price > 0:
            signals.append(
                Signal(
                    strategy=Strategy.LIQUIDITY,
                    token_id=yes_token.token_id,
                    condition_id=market.condition_id,
                    side=Side.BUY,
                    price=bid_price,
                    size=size,
                    order_type=OrderType.GTC,
                    reason=f"lp bid spread={target_spread:.4f}",
                    edge=reward_score(market.max_incentive_spread, target_spread / 2, size),
                    market_question=market.question,
                )
            )

        if ask_price < 1.0:
            signals.append(
                Signal(
                    strategy=Strategy.LIQUIDITY,
                    token_id=yes_token.token_id,
                    condition_id=market.condition_id,
                    side=Side.SELL,
                    price=ask_price,
                    size=size,
                    order_type=OrderType.GTC,
                    reason=f"lp ask spread={target_spread:.4f}",
                    edge=reward_score(market.max_incentive_spread, target_spread / 2, size),
                    market_question=market.question,
                )
            )

        return signals

    async def _cancel_stale_orders(self) -> None:
        """Cancel previously placed LP orders."""
        for oid in self._active_order_ids:
            try:
                await self.order_manager.cancel_order(oid)
            except Exception:
                logger.warning("lp.cancel_failed", order_id=oid)
        self._active_order_ids.clear()

    def track_order(self, order_id: str) -> None:
        """Record an order ID for later cleanup."""
        self._active_order_ids.append(order_id)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def on_shutdown(self) -> None:
        """Cancel all outstanding LP orders."""
        logger.info("lp.shutdown", pending_orders=len(self._active_order_ids))
        await self._cancel_stale_orders()
