"""Liquidity provision strategy — one-sided LP reward hunting."""

from __future__ import annotations

import asyncio as _asyncio
from datetime import datetime, timezone
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
    """One-sided LP: place limit orders on ONE side per market, switch on fill.

    Based on @DidiTrading approach:
      - Sort markets by reward/competition sweet spot
      - Place ONE limit order per market on the active side
      - Never place closest to midpoint — stay behind best bid
      - When filled, switch to the other side and repeat
      - Max $10 per position to cap forced liquidation loss
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
            config.lp_refresh_interval_sec, config.timing_jitter_pct
        )
        # Per-market state: which side to place orders on
        self._market_sides: dict[str, str] = {}  # condition_id -> "yes" | "no"
        # Track active orders from last cycle for fill detection
        self._active_orders: list[dict] = []  # [{order_id, condition_id, token_id, side}]
        # Signal info for order tracking after execution
        self._pending_signal_info: dict[str, dict] = {}  # token_id -> {condition_id, side}

    # ------------------------------------------------------------------
    # Run loop override
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Override to track order IDs for fill detection."""
        self._running = True
        logger.info("strategy.start", strategy="LiquidityStrategy")

        while self._running:
            try:
                signals = await self.scan()
                if signals:
                    results = await self.order_manager.execute_batch(signals)
                    for result in results:
                        if result.success and result.order_id:
                            info = self._pending_signal_info.get(result.signal.token_id, {})
                            self.track_order(
                                order_id=result.order_id,
                                condition_id=info.get("condition_id", result.signal.condition_id),
                                token_id=result.signal.token_id,
                                side=info.get("side", "yes"),
                            )
            except _asyncio.CancelledError:
                break
            except Exception:
                logger.exception("strategy.scan_error", strategy="LiquidityStrategy")
                self._publish_event(
                    EventType.STRATEGY_ERROR,
                    {"strategy": "LiquidityStrategy", "error": "scan cycle failed"},
                )
            await _asyncio.sleep(self.scan_interval_sec)

        logger.info("strategy.stopped", strategy="LiquidityStrategy")

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    async def scan(self) -> list[Signal]:
        """One-sided LP scan: detect fills, cancel stale, quote one side."""
        # 1. Detect fills from previous cycle and switch sides
        await self._detect_fills_and_switch()

        # 2. Cancel remaining unfilled orders
        await self._cancel_stale_orders()

        # 3. Fetch and rank markets
        try:
            markets = await self.gamma_client.get_markets()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("lp.fetch_markets_failed")
            return []

        ranked = self._rank_markets(markets)
        signals: list[Signal] = []

        # 4. Place ONE order per market on active side
        for market in ranked[: self.config.lp_max_markets]:
            signal = await self._quote_one_side(market)
            if signal is not None:
                signals.append(signal)

        # Store signal info for order tracking after execution
        self._pending_signal_info.clear()
        for sig in signals:
            side = self._market_sides.get(sig.condition_id, "yes")
            self._pending_signal_info[sig.token_id] = {
                "condition_id": sig.condition_id,
                "side": side,
            }

        # Dashboard event
        dashboard_markets = []
        for m in ranked[: self.config.lp_max_markets]:
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
                "strategy": Strategy.LIQUIDITY,
                "count": len(markets),
                "total_scanned": len(markets),
                "avg_edge": 0.0,
                "markets": dashboard_markets[:8],
                "markets_quoted": min(len(ranked), self.config.lp_max_markets),
                "signals": len(signals),
            },
        )

        return signals

    # ------------------------------------------------------------------
    # Fill detection
    # ------------------------------------------------------------------

    async def _detect_fills_and_switch(self) -> None:
        """Check which orders from last cycle filled. Switch sides for those."""
        if not self._active_orders:
            return

        try:
            open_orders = await self.clob_client.get_open_orders()  # type: ignore[attr-defined]
        except Exception:
            logger.warning("lp.fill_check_failed")
            return

        open_ids = set()
        for o in open_orders:
            oid = o.get("id") or o.get("order_id") or o.get("orderID")
            if oid:
                open_ids.add(oid)

        for info in self._active_orders:
            if info["order_id"] not in open_ids:
                # Order no longer open = filled! Switch sides
                cid = info["condition_id"]
                old_side = self._market_sides.get(cid, "yes")
                new_side = "no" if old_side == "yes" else "yes"
                self._market_sides[cid] = new_side
                logger.info(
                    "lp.fill_detected",
                    market=cid[:12],
                    old_side=old_side,
                    new_side=new_side,
                )

        self._active_orders.clear()

    # ------------------------------------------------------------------
    # Market ranking
    # ------------------------------------------------------------------

    def _rank_markets(self, markets: list[Market]) -> list[Market]:
        """Filter and rank by balanced reward/competition/liquidity."""
        eligible: list[Market] = []
        for m in markets:
            if not self._passes_filters(m):
                continue
            eligible.append(m)

        logger.info(
            "lp.markets_filtered",
            total=len(markets),
            eligible=len(eligible),
        )

        # Score: reward * competition_sweetspot * liquidity_factor
        # competition_sweetspot peaks at 0.5 (moderate), penalizes 0 (dead) and 1 (overcrowded)
        def score(m: Market) -> float:
            comp = m.competitive_raw
            comp_score = max(0.1, 1.0 - abs(comp - 0.5) * 2)
            liq_factor = min(m.liquidity / 10_000, 3.0)
            return m.daily_reward_usd * comp_score * max(liq_factor, 0.1)

        eligible.sort(key=score, reverse=True)
        return eligible

    def _passes_filters(self, m: Market) -> bool:
        """Apply strict liquidity + reward filters."""
        if not m.active or m.max_incentive_spread <= 0:
            return False
        if m.volume_24h < self.config.lp_min_volume_24h:
            return False
        if m.liquidity < self.config.lp_min_liquidity:
            return False
        if m.spread > self.config.lp_max_spread:
            return False
        if m.best_bid < self.config.lp_min_best_bid:
            return False
        if m.daily_reward_usd < self.config.lp_min_daily_reward:
            return False
        # Skip markets resolving > N days out (minimize opp cost)
        if m.end_date:
            try:
                end = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                days_left = (end - datetime.now(timezone.utc)).days
                if days_left < 0 or days_left > self.config.lp_max_days_to_resolve:
                    return False
            except (ValueError, TypeError):
                pass
        return True

    # ------------------------------------------------------------------
    # Quote one side
    # ------------------------------------------------------------------

    async def _quote_one_side(self, market: Market) -> Signal | None:
        """Place ONE BUY order on the active side for this market."""
        if len(market.tokens) < 2:
            return None

        yes_token = next((t for t in market.tokens if t.outcome == "Yes"), None)
        no_token = next((t for t in market.tokens if t.outcome == "No"), None)
        if yes_token is None or no_token is None:
            return None

        # Determine which side to place on
        side = self._market_sides.get(market.condition_id, "yes")
        token = yes_token if side == "yes" else no_token

        try:
            book = await self.clob_client.get_order_book(token.token_id)  # type: ignore[attr-defined]
        except Exception:
            return None

        mid = book.midpoint
        if mid is None or mid <= 0.05 or mid >= 0.95:
            return None

        # Verify bid-side depth
        if book.best_bid is None or book.best_bid < self.config.lp_min_best_bid:
            return None

        # Place BEHIND best bid (not at top of book to avoid instant fills)
        if len(book.bids) >= 2:
            price = book.bids[1].price  # 2nd-best bid
        else:
            price = round_to_tick(book.best_bid - 0.01)  # 1 tick behind

        if price <= 0.01 or price >= 0.99:
            return None

        # Verify price is within max_incentive_spread of midpoint (reward eligibility)
        spread_from_mid = abs(mid - price)
        if spread_from_mid > market.max_incentive_spread:
            # Fall back to edge of reward zone
            price = round_to_tick(mid - market.max_incentive_spread + 0.01)
            if price <= 0.01:
                return None

        size_usd = self.config.lp_order_size_usd
        size_shares = size_usd / price

        # Enforce min_incentive_size
        if size_shares < market.min_incentive_size:
            size_shares = market.min_incentive_size

        score = reward_score(market.max_incentive_spread, spread_from_mid, size_usd)

        logger.info(
            "lp.quote",
            market=market.question[:40],
            side=side,
            price=price,
            mid=round(mid, 3),
            spread_from_mid=round(spread_from_mid, 4),
            reward=round(market.daily_reward_usd, 1),
            score=round(score, 2),
        )

        return Signal(
            strategy=Strategy.LIQUIDITY,
            token_id=token.token_id,
            condition_id=market.condition_id,
            side=Side.BUY,
            price=price,
            size=size_shares,
            order_type=OrderType.GTC,
            reason=f"lp {side}-bid behind_best reward=${market.daily_reward_usd:.0f}/d",
            edge=score,
            market_question=market.question,
        )

    # ------------------------------------------------------------------
    # Order tracking
    # ------------------------------------------------------------------

    async def _cancel_stale_orders(self) -> None:
        """Cancel ALL open orders to prevent stacking across cycles."""
        # Cancel all open orders via bulk API (most reliable)
        count = await self.order_manager.cancel_all_orders()
        if count or self._active_orders:
            logger.info("lp.cancelled_all", api_count=count, tracked=len(self._active_orders))
        self._active_orders.clear()

    def track_order(self, order_id: str, condition_id: str = "", token_id: str = "", side: str = "") -> None:
        """Record an order for fill detection and cleanup."""
        self._active_orders.append({
            "order_id": order_id,
            "condition_id": condition_id,
            "token_id": token_id,
            "side": side,
        })

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def on_shutdown(self) -> None:
        """Cancel all outstanding LP orders."""
        logger.info("lp.shutdown", pending_orders=len(self._active_orders))
        await self._cancel_stale_orders()
