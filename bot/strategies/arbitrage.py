"""Arbitrage strategy — buy YES + NO when combined cost < $1."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from bot.constants import EventType, OrderType, Side, Strategy
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


class ArbitrageStrategy(BaseStrategy):
    """Scan binary markets for YES+NO mispricing.

    If the best ask for YES plus the best ask for NO sums to less
    than $1.00 minus the minimum profit threshold, buy both sides
    with FOK orders to lock in a risk-free profit.
    """

    def __init__(
        self,
        config: BotConfig,
        clob_client: object,
        gamma_client: object,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        db: Database,
        event_bus: EventBus,
    ) -> None:
        super().__init__(config, clob_client, order_manager, risk_manager, db, event_bus)
        self.gamma_client = gamma_client
        self.scan_interval_sec = jitter_delay(
            config.arb_scan_interval_sec, config.timing_jitter_pct
        )

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    async def scan(self) -> list[Signal]:
        """Scan active markets for arbitrage opportunities."""
        signals: list[Signal] = []
        min_profit = self.config.arb_min_profit_cents / 100.0
        max_trade = self.config.max_trade_size_usd

        try:
            markets = await self.gamma_client.get_markets()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("arb.fetch_markets_failed")
            return signals

        # Build market summary for dashboard display
        dashboard_markets = []
        for m in markets:
            if not m.active or len(m.tokens) != 2:
                continue
            yes_t = next((t for t in m.tokens if t.outcome == "Yes"), None)
            if yes_t:
                dashboard_markets.append({
                    "name": m.question[:40],
                    "price": yes_t.price,
                    "edge": 0.0,
                    "fair": yes_t.price,
                })

        self._publish_event(
            EventType.MARKET_SCANNED,
            {
                "strategy": Strategy.ARBITRAGE,
                "count": len(markets),
                "total_scanned": len(markets),
                "avg_edge": 0.0,
                "markets": dashboard_markets[:8],
            },
        )

        for market in markets:
            if not market.active or len(market.tokens) != 2:
                continue

            try:
                yes_token = next(t for t in market.tokens if t.outcome == "Yes")
                no_token = next(t for t in market.tokens if t.outcome == "No")

                yes_book = await self.clob_client.get_order_book(yes_token.token_id)  # type: ignore[attr-defined]
                no_book = await self.clob_client.get_order_book(no_token.token_id)  # type: ignore[attr-defined]
            except Exception:
                continue

            yes_ask = yes_book.best_ask
            no_ask = no_book.best_ask
            if yes_ask is None or no_ask is None:
                continue

            cost = yes_ask + no_ask
            profit = 1.0 - cost

            if profit < min_profit:
                continue

            # Determine sizes — scale so total outlay <= max_trade
            trade_amount = min(max_trade, max_trade)
            yes_size = trade_amount * (1.0 - no_ask)
            no_size = trade_amount * (1.0 - yes_ask)

            logger.info(
                "arb.opportunity",
                market=market.question[:60],
                yes_ask=yes_ask,
                no_ask=no_ask,
                profit=round(profit, 4),
            )

            self._publish_event(
                EventType.EDGE_DETECTED,
                {
                    "strategy": Strategy.ARBITRAGE,
                    "market": market.question,
                    "edge": profit,
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                },
            )

            signals.extend([
                Signal(
                    strategy=Strategy.ARBITRAGE,
                    token_id=yes_token.token_id,
                    condition_id=market.condition_id,
                    side=Side.BUY,
                    price=yes_ask,
                    size=yes_size,
                    order_type=OrderType.FOK,
                    reason=f"arb profit={profit:.4f}",
                    edge=profit,
                    market_question=market.question,
                ),
                Signal(
                    strategy=Strategy.ARBITRAGE,
                    token_id=no_token.token_id,
                    condition_id=market.condition_id,
                    side=Side.BUY,
                    price=no_ask,
                    size=no_size,
                    order_type=OrderType.FOK,
                    reason=f"arb profit={profit:.4f}",
                    edge=profit,
                    market_question=market.question,
                ),
            ])

        if signals:
            logger.info("arb.signals_generated", count=len(signals))

        return signals

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def on_shutdown(self) -> None:
        logger.info("arb.shutdown")
