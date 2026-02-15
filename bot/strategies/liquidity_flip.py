"""LP Flip strategy (Strategy 2 — Didi Flip): single-market flip state machine.

Cycle:
  IDLE          → pick best reward market, place BUY behind best bid
  RESTING_ENTRY → poll for fill every 30s; on fill → place BUY on opposite side
  RESTING_EXIT  → poll for exit fill; on fill → log profit, go IDLE
                  stop-loss: if >25% loss, cancel exit order, market sell
"""

from __future__ import annotations

import asyncio as _asyncio
import time as _time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

from bot.constants import EventType, OrderType, Side, Strategy
from bot.data.models import update_daily_volume
from bot.risk.anti_detection import jitter_delay
from bot.strategies.base import BaseStrategy
from bot.types import Market, Signal, TokenInfo
from bot.utils.math import reward_score, round_to_tick
from bot.utils.time import utc_iso

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.dashboard.state import DashboardState
    from bot.data.database import Database
    from bot.execution.order_manager import OrderManager
    from bot.risk.manager import RiskManager
    from bot.types import EventBus

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# State machine types
# ---------------------------------------------------------------------------

class FlipPhase(StrEnum):
    IDLE = "idle"
    RESTING_ENTRY = "resting_entry"
    RESTING_EXIT = "resting_exit"


@dataclass
class FlipCycleState:
    """Tracks one flip cycle from entry to exit."""

    condition_id: str = ""
    market_question: str = ""

    # Entry
    entry_side: str = ""            # "yes" | "no"
    entry_token_id: str = ""
    entry_price: float = 0.0
    entry_shares: float = 0.0
    entry_order_id: str = ""
    entry_placed_at: float = 0.0    # monotonic timestamp

    # Exit
    exit_side: str = ""             # opposite of entry
    exit_token_id: str = ""
    exit_price: float = 0.0
    exit_shares: float = 0.0
    exit_order_id: str = ""

    # DB row id (set after INSERT)
    db_id: int = 0


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class LiquidityFlipStrategy(BaseStrategy):
    """Single-market flip: buy one side, wait for fill, buy the other, repeat.

    Overrides ``run()`` with a custom state-machine loop instead of the
    default scan/execute pattern.
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
        dashboard_state: DashboardState | None = None,
    ) -> None:
        super().__init__(config, clob_client, order_manager, risk_manager, db, event_bus)
        self.gamma_client = gamma_client
        self._dashboard_state = dashboard_state

        # State machine
        self._phase = FlipPhase.IDLE
        self._cycle: FlipCycleState | None = None

        # Lifetime stats
        self._total_profit: float = 0.0
        self._total_flips: int = 0
        self._recent_flips: list[dict] = []  # last 20 completed cycles

    # ------------------------------------------------------------------
    # Abstract interface stubs (not used — run() is overridden)
    # ------------------------------------------------------------------

    async def scan(self) -> list[Signal]:  # pragma: no cover
        return []

    async def on_shutdown(self) -> None:
        """Cancel any resting order on shutdown."""
        if self._cycle:
            oid = self._cycle.entry_order_id or self._cycle.exit_order_id
            if oid:
                try:
                    await self.order_manager.cancel_order(oid)
                    logger.info("lp_flip.shutdown_cancelled", order_id=oid[:16])
                except Exception:
                    pass
        logger.info("lp_flip.shutdown", phase=self._phase)

    # ------------------------------------------------------------------
    # Main run loop — state machine
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        logger.info("strategy.start", strategy="LiquidityFlipStrategy")

        while self._running:
            try:
                if self._phase == FlipPhase.IDLE:
                    await self._do_idle()
                elif self._phase == FlipPhase.RESTING_ENTRY:
                    await self._do_resting_entry()
                elif self._phase == FlipPhase.RESTING_EXIT:
                    await self._do_resting_exit()
            except _asyncio.CancelledError:
                break
            except Exception:
                logger.exception("lp_flip.loop_error", phase=self._phase)
                self._publish_event(
                    EventType.STRATEGY_ERROR,
                    {"strategy": "LiquidityFlipStrategy", "error": f"{self._phase} failed"},
                )
                # On error, reset to IDLE after a cooldown
                self._phase = FlipPhase.IDLE
                self._cycle = None
                await _asyncio.sleep(60)
                continue

            self._push_dashboard()

        logger.info("strategy.stopped", strategy="LiquidityFlipStrategy")

    # ------------------------------------------------------------------
    # IDLE — pick market, place entry order
    # ------------------------------------------------------------------

    async def _do_idle(self) -> None:
        """Select best reward market and place an entry BUY behind best bid."""
        scan_interval = jitter_delay(
            self.config.lp_flip_scan_interval_sec, self.config.timing_jitter_pct
        )
        await _asyncio.sleep(scan_interval)

        # Fetch reward markets (same API as Strategy 1)
        try:
            reward_markets = await self.clob_client.get_reward_markets()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("lp_flip.fetch_reward_markets_failed")
            return

        markets = self._convert_reward_markets(reward_markets)
        ranked = self._rank_markets(markets)

        # Publish scan event so dashboard updates
        self._publish_event(EventType.MARKET_SCANNED, {
            "strategy": Strategy.LP_FLIP,
            "count": len(ranked),
            "total_scanned": len(markets),
            "signals": len(ranked),
        })

        if not ranked:
            logger.info("lp_flip.no_eligible_markets")
            return

        # Try each ranked market until we successfully place an entry
        for market in ranked:
            entry = await self._try_entry(market)
            if entry is not None:
                self._cycle = entry
                self._phase = FlipPhase.RESTING_ENTRY

                # Insert into DB
                try:
                    cursor = await self.db.execute(
                        """INSERT INTO flip_cycles
                           (condition_id, market_question, entry_side, entry_token_id,
                            entry_price, entry_shares, entry_order_id, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'open')""",
                        (
                            entry.condition_id,
                            entry.market_question,
                            entry.entry_side,
                            entry.entry_token_id,
                            entry.entry_price,
                            entry.entry_shares,
                            entry.entry_order_id,
                        ),
                    )
                    self._cycle.db_id = cursor.lastrowid or 0
                except Exception:
                    logger.exception("lp_flip.db_insert_failed")

                logger.info(
                    "lp_flip.entry_placed",
                    market=market.question[:40],
                    side=entry.entry_side,
                    price=entry.entry_price,
                    shares=round(entry.entry_shares, 1),
                    order_id=entry.entry_order_id[:16] if entry.entry_order_id else "",
                )

                # Publish order event for dashboard tracking
                self._publish_event(EventType.TRADE_EXECUTED, {
                    "strategy": Strategy.LP_FLIP,
                    "market": market.question[:40],
                    "side": "BUY",
                    "price": entry.entry_price,
                    "size": entry.entry_shares,
                    "is_resting": True,
                    "success": True,
                })
                return

        logger.info("lp_flip.no_viable_entry", tried=len(ranked))

    # ------------------------------------------------------------------
    # RESTING_ENTRY — poll for entry fill
    # ------------------------------------------------------------------

    async def _do_resting_entry(self) -> None:
        """Poll open orders; on fill -> place exit order on opposite side."""
        poll_interval = jitter_delay(
            self.config.lp_flip_poll_interval_sec, self.config.timing_jitter_pct
        )
        await _asyncio.sleep(poll_interval)

        cycle = self._cycle
        if cycle is None:
            self._phase = FlipPhase.IDLE
            return

        # Check stale entry (max resting time)
        elapsed = _time.monotonic() - cycle.entry_placed_at
        if elapsed > self.config.lp_flip_max_resting_sec:
            logger.info(
                "lp_flip.entry_stale",
                market=cycle.market_question[:40],
                elapsed_sec=int(elapsed),
            )
            try:
                await self.order_manager.cancel_order(cycle.entry_order_id)
            except Exception:
                pass
            await self._update_cycle_status("cancelled")
            self._phase = FlipPhase.IDLE
            self._cycle = None
            return

        # Check if order is still open
        filled = await self._is_order_filled(cycle.entry_order_id)
        if not filled:
            return

        # Entry filled! Place exit order on opposite side
        logger.info(
            "lp_flip.entry_filled",
            market=cycle.market_question[:40],
            side=cycle.entry_side,
            price=cycle.entry_price,
            shares=round(cycle.entry_shares, 1),
        )

        # Update DB
        try:
            await self.db.execute(
                """UPDATE flip_cycles SET entry_filled_at = ?, updated_at = ?
                   WHERE id = ?""",
                (utc_iso(), utc_iso(), cycle.db_id),
            )
        except Exception:
            pass

        # Track volume
        try:
            volume = cycle.entry_price * cycle.entry_shares
            await update_daily_volume(self.db, Strategy.LP_FLIP, volume)
        except Exception:
            pass

        # Publish fill event
        self._publish_event(EventType.TRADE_EXECUTED, {
            "strategy": Strategy.LP_FLIP,
            "market": cycle.market_question[:40],
            "side": "BUY",
            "price": cycle.entry_price,
            "size": cycle.entry_shares,
            "is_resting": False,
            "success": True,
        })

        # Place exit order on opposite side
        exit_placed = await self._place_exit_order(cycle)
        if exit_placed:
            self._phase = FlipPhase.RESTING_EXIT
        else:
            # Failed to place exit — emergency sell and go IDLE
            logger.warning("lp_flip.exit_place_failed, emergency selling")
            await self._emergency_exit(cycle)
            await self._update_cycle_status("error")
            self._phase = FlipPhase.IDLE
            self._cycle = None

    # ------------------------------------------------------------------
    # RESTING_EXIT — poll for exit fill + stop-loss
    # ------------------------------------------------------------------

    async def _do_resting_exit(self) -> None:
        """Poll for exit fill; enforce stop-loss."""
        poll_interval = jitter_delay(
            self.config.lp_flip_poll_interval_sec, self.config.timing_jitter_pct
        )
        await _asyncio.sleep(poll_interval)

        cycle = self._cycle
        if cycle is None:
            self._phase = FlipPhase.IDLE
            return

        # Check stop-loss: get current price of entry token
        try:
            current_price = await self.clob_client.get_price(  # type: ignore[attr-defined]
                cycle.entry_token_id, "SELL"
            )
        except Exception:
            current_price = 0.0

        if current_price > 0 and cycle.entry_price > 0:
            loss_pct = (cycle.entry_price - current_price) / cycle.entry_price
            if loss_pct >= self.config.lp_flip_stop_loss_pct:
                logger.warning(
                    "lp_flip.stop_loss_triggered",
                    market=cycle.market_question[:40],
                    entry_price=cycle.entry_price,
                    current_price=round(current_price, 4),
                    loss_pct=round(loss_pct, 3),
                )
                # Cancel exit order
                if cycle.exit_order_id:
                    try:
                        await self.order_manager.cancel_order(cycle.exit_order_id)
                    except Exception:
                        pass
                # Emergency sell
                await self._emergency_exit(cycle)
                profit = (current_price - cycle.entry_price) * cycle.entry_shares
                await self._complete_cycle(cycle, profit, "stop_loss")
                return

        # Check if exit order filled
        if not cycle.exit_order_id:
            self._phase = FlipPhase.IDLE
            self._cycle = None
            return

        filled = await self._is_order_filled(cycle.exit_order_id)
        if not filled:
            return

        # Exit filled! Calculate profit and complete cycle
        logger.info(
            "lp_flip.exit_filled",
            market=cycle.market_question[:40],
            exit_side=cycle.exit_side,
            exit_price=cycle.exit_price,
            exit_shares=round(cycle.exit_shares, 1),
        )

        # Profit = what we get back minus what we paid
        # Entry: bought entry_shares at entry_price → cost = entry_price * entry_shares
        # Exit: bought exit_shares of opposite token at exit_price → cost = exit_price * exit_shares
        # If both sides fill, we hold both YES and NO tokens which redeem at $1 total
        # Profit = $1 * min(entry_shares, exit_shares) - entry_cost - exit_cost
        entry_cost = cycle.entry_price * cycle.entry_shares
        exit_cost = cycle.exit_price * cycle.exit_shares
        redeemable = min(cycle.entry_shares, cycle.exit_shares)
        profit = redeemable - entry_cost - exit_cost

        # Track volume
        try:
            volume = cycle.exit_price * cycle.exit_shares
            await update_daily_volume(self.db, Strategy.LP_FLIP, volume, pnl=profit)
        except Exception:
            pass

        await self._complete_cycle(cycle, profit, "completed")

    # ------------------------------------------------------------------
    # Helpers — market selection
    # ------------------------------------------------------------------

    def _convert_reward_markets(self, reward_data: list[dict]) -> list[Market]:
        """Convert raw CLOB reward market dicts into Market objects."""
        markets: list[Market] = []
        for rd in reward_data:
            tokens_raw = rd.get("tokens", [])
            tokens = []
            for t in tokens_raw:
                tokens.append(TokenInfo(
                    token_id=str(t.get("token_id", "")),
                    outcome=t.get("outcome", ""),
                    price=float(t.get("price", 0)),
                ))
            markets.append(Market(
                condition_id=rd["condition_id"],
                question=rd.get("question", ""),
                tokens=tokens,
                active=rd.get("active", True),
                min_incentive_size=float(rd.get("rewards_min_size", 0)),
                max_incentive_spread=float(rd.get("rewards_max_spread", 0)),
                daily_reward_usd=float(rd.get("daily_reward", 0)),
                end_date=rd.get("end_date_iso"),
            ))
        return markets

    def _rank_markets(self, markets: list[Market]) -> list[Market]:
        """Filter and rank: highest reward first."""
        eligible: list[Market] = []
        for m in markets:
            if not self._passes_filters(m):
                continue
            eligible.append(m)

        eligible.sort(key=lambda m: m.daily_reward_usd, reverse=True)

        logger.info(
            "lp_flip.markets_ranked",
            total=len(markets),
            eligible=len(eligible),
            top_reward=round(eligible[0].daily_reward_usd, 1) if eligible else 0,
        )
        return eligible

    def _passes_filters(self, m: Market) -> bool:
        """Apply same filters as Strategy 1: reward, spread, expiry, token count."""
        if not m.active or m.max_incentive_spread <= 0:
            return False
        if len(m.tokens) < 2:
            return False
        if m.daily_reward_usd < self.config.lp_min_daily_reward:
            return False
        # Skip markets expiring within 3 days
        if m.end_date:
            try:
                from datetime import datetime, timezone
                end_dt = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                days_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400
                if days_left < 3:
                    return False
            except (ValueError, TypeError):
                pass
        return True

    # ------------------------------------------------------------------
    # Helpers — entry order
    # ------------------------------------------------------------------

    async def _try_entry(self, market: Market) -> FlipCycleState | None:
        """Try to place entry BUY on the best side of a market."""
        yes_token = next((t for t in market.tokens if t.outcome == "Yes"), None)
        no_token = next((t for t in market.tokens if t.outcome == "No"), None)
        if yes_token is None or no_token is None:
            return None

        # Try yes side first, then no
        for side, token in [("yes", yes_token), ("no", no_token)]:
            result = await self._try_entry_side(market, side, token)
            if result is not None:
                return result
        return None

    async def _try_entry_side(
        self, market: Market, side: str, token: TokenInfo,
    ) -> FlipCycleState | None:
        """Attempt entry on a specific side. Returns cycle state or None."""
        try:
            book = await self.clob_client.get_order_book(token.token_id)  # type: ignore[attr-defined]
        except Exception:
            return None

        mid = book.midpoint
        if mid is None or mid < 0.10 or mid > 0.90:
            return None

        if book.best_bid is None or book.best_bid < self.config.lp_min_best_bid:
            return None

        # Place behind best bid (2nd bid level or best_bid - 0.01)
        if len(book.bids) >= 2:
            price = book.bids[1].price
        else:
            price = round_to_tick(book.best_bid - 0.01)

        if price <= 0.01 or price >= 0.99:
            return None

        # Verify within max_incentive_spread
        spread_from_mid = abs(mid - price)
        if spread_from_mid > market.max_incentive_spread:
            price = round_to_tick(mid - market.max_incentive_spread + 0.01)
            if price <= 0.01:
                return None
            spread_from_mid = abs(mid - price)

        # Size
        size_usd = self.config.lp_flip_order_size_usd
        size_shares = size_usd / price

        # Q-score reward estimate
        total_q = sum(
            reward_score(market.max_incentive_spread, abs(mid - lvl.price), lvl.size)
            for lvl in book.bids
            if abs(mid - lvl.price) <= market.max_incentive_spread
        )
        our_q = reward_score(market.max_incentive_spread, spread_from_mid, size_shares)
        pool_share = our_q / (total_q + our_q) if (total_q + our_q) > 0 else 0.0
        est_daily = market.daily_reward_usd * pool_share

        if est_daily < self.config.lp_min_estimated_reward:
            return None

        # Place the order
        try:
            result = await self.clob_client.create_and_post_limit_order(  # type: ignore[attr-defined]
                token_id=token.token_id,
                price=price,
                size=size_shares,
                side="BUY",
                order_type="GTC",
            )
        except Exception:
            logger.exception("lp_flip.entry_order_failed", market=market.question[:40])
            return None

        order_id = result.get("orderID") or result.get("id") or ""
        if not order_id:
            return None

        return FlipCycleState(
            condition_id=market.condition_id,
            market_question=market.question,
            entry_side=side,
            entry_token_id=token.token_id,
            entry_price=price,
            entry_shares=size_shares,
            entry_order_id=order_id,
            entry_placed_at=_time.monotonic(),
        )

    # ------------------------------------------------------------------
    # Helpers — exit order
    # ------------------------------------------------------------------

    async def _place_exit_order(self, cycle: FlipCycleState) -> bool:
        """Place BUY on opposite side's token_id, behind that side's best bid."""
        # Find opposite token
        try:
            reward_markets = await self.clob_client.get_reward_markets()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("lp_flip.exit_fetch_failed")
            return False

        # Find our market and the opposite token
        opposite_token_id = ""
        for rd in reward_markets:
            if rd.get("condition_id") != cycle.condition_id:
                continue
            tokens = rd.get("tokens", [])
            opposite_outcome = "No" if cycle.entry_side == "yes" else "Yes"
            for t in tokens:
                if t.get("outcome") == opposite_outcome:
                    opposite_token_id = str(t.get("token_id", ""))
                    break
            break

        if not opposite_token_id:
            # Fallback: try to get market data directly
            logger.warning("lp_flip.opposite_token_not_found", condition_id=cycle.condition_id[:16])
            return False

        # Get order book for opposite side
        try:
            book = await self.clob_client.get_order_book(opposite_token_id)  # type: ignore[attr-defined]
        except Exception:
            return False

        if book.best_bid is None:
            return False

        # Place behind best bid
        if len(book.bids) >= 2:
            price = book.bids[1].price
        else:
            price = round_to_tick(book.best_bid - 0.01)

        if price <= 0.01 or price >= 0.99:
            return False

        exit_side = "no" if cycle.entry_side == "yes" else "yes"

        try:
            result = await self.clob_client.create_and_post_limit_order(  # type: ignore[attr-defined]
                token_id=opposite_token_id,
                price=price,
                size=cycle.entry_shares,  # same shares as entry
                side="BUY",
                order_type="GTC",
            )
        except Exception:
            logger.exception("lp_flip.exit_order_failed")
            return False

        order_id = result.get("orderID") or result.get("id") or ""
        if not order_id:
            return False

        cycle.exit_side = exit_side
        cycle.exit_token_id = opposite_token_id
        cycle.exit_price = price
        cycle.exit_shares = cycle.entry_shares
        cycle.exit_order_id = order_id

        # Update DB
        try:
            await self.db.execute(
                """UPDATE flip_cycles
                   SET exit_side = ?, exit_token_id = ?, exit_price = ?,
                       exit_shares = ?, exit_order_id = ?, updated_at = ?
                   WHERE id = ?""",
                (exit_side, opposite_token_id, price,
                 cycle.entry_shares, order_id, utc_iso(), cycle.db_id),
            )
        except Exception:
            pass

        logger.info(
            "lp_flip.exit_placed",
            market=cycle.market_question[:40],
            exit_side=exit_side,
            price=price,
            shares=round(cycle.entry_shares, 1),
            order_id=order_id[:16],
        )

        # Publish exit order event
        self._publish_event(EventType.TRADE_EXECUTED, {
            "strategy": Strategy.LP_FLIP,
            "market": cycle.market_question[:40],
            "side": "BUY",
            "price": price,
            "size": cycle.entry_shares,
            "is_resting": True,
            "success": True,
        })
        return True

    # ------------------------------------------------------------------
    # Helpers — fill detection
    # ------------------------------------------------------------------

    async def _is_order_filled(self, order_id: str) -> bool:
        """Check if an order_id is no longer in open orders (i.e., filled)."""
        if not order_id:
            return False
        try:
            open_orders = await self.clob_client.get_open_orders()  # type: ignore[attr-defined]
        except Exception:
            logger.warning("lp_flip.fill_check_failed")
            return False

        open_ids: set[str] = set()
        for o in open_orders:
            oid = o.get("id") or o.get("order_id") or o.get("orderID")
            if oid:
                open_ids.add(oid)

        return order_id not in open_ids

    # ------------------------------------------------------------------
    # Helpers — emergency exit (same pattern as Strategy 1)
    # ------------------------------------------------------------------

    async def _emergency_exit(self, cycle: FlipCycleState) -> bool:
        """Approve conditional token and sell at aggressive price."""
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        token_id = cycle.entry_token_id
        shares = cycle.entry_shares

        try:
            # 1. Approve conditional token
            sig_type = 2 if self.config.proxy_address else 0
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
                signature_type=sig_type,
            )
            await _asyncio.to_thread(
                self.clob_client.client.update_balance_allowance, params  # type: ignore[attr-defined]
            )

            # 2. Check actual token balance
            bal_result = await _asyncio.to_thread(
                self.clob_client.client.get_balance_allowance, params  # type: ignore[attr-defined]
            )
            actual_balance = int(bal_result.get("balance", 0)) / 1_000_000
            sell_shares = min(shares, actual_balance)

            if sell_shares < 1:
                logger.info("lp_flip.exit_skip_tiny", token=token_id[:16], balance=actual_balance)
                return True

            # 3. Sell at aggressive price for immediate fill
            try:
                current = await self.clob_client.get_price(token_id, "SELL")  # type: ignore[attr-defined]
            except Exception:
                current = cycle.entry_price

            sell_price = max(0.01, round_to_tick(current * 0.5))
            result = await self.clob_client.create_and_post_limit_order(  # type: ignore[attr-defined]
                token_id=token_id,
                price=sell_price,
                size=sell_shares,
                side="SELL",
                order_type="GTC",
            )
            logger.info(
                "lp_flip.emergency_sold",
                token=token_id[:16],
                shares=round(sell_shares, 1),
                price=sell_price,
                result=result,
            )
            return True

        except Exception:
            logger.exception("lp_flip.emergency_sell_failed", token=token_id[:16])
            return False

    # ------------------------------------------------------------------
    # Helpers — cycle completion
    # ------------------------------------------------------------------

    async def _complete_cycle(
        self, cycle: FlipCycleState, profit: float, status: str
    ) -> None:
        """Finalize a flip cycle: update DB, stats, reset to IDLE."""
        self._total_profit += profit
        self._total_flips += 1
        self._recent_flips.append({
            "market": cycle.market_question[:40],
            "entry_side": cycle.entry_side,
            "entry_price": cycle.entry_price,
            "exit_price": cycle.exit_price,
            "profit": round(profit, 4),
            "status": status,
        })
        if len(self._recent_flips) > 20:
            self._recent_flips = self._recent_flips[-20:]

        # Update DB
        try:
            await self.db.execute(
                """UPDATE flip_cycles
                   SET exit_filled_at = ?, profit = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (utc_iso(), profit, status, utc_iso(), cycle.db_id),
            )
        except Exception:
            logger.exception("lp_flip.db_update_failed")

        logger.info(
            "lp_flip.cycle_complete",
            market=cycle.market_question[:40],
            profit=round(profit, 4),
            status=status,
            total_profit=round(self._total_profit, 4),
            total_flips=self._total_flips,
        )

        self._phase = FlipPhase.IDLE
        self._cycle = None

    async def _update_cycle_status(self, status: str) -> None:
        """Update just the status of the current cycle in DB."""
        if self._cycle and self._cycle.db_id:
            try:
                await self.db.execute(
                    "UPDATE flip_cycles SET status = ?, updated_at = ? WHERE id = ?",
                    (status, utc_iso(), self._cycle.db_id),
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Dashboard state push
    # ------------------------------------------------------------------

    def _push_dashboard(self) -> None:
        """Update dashboard state with current flip status."""
        if self._dashboard_state is None:
            return

        cycle = self._cycle
        self._dashboard_state.lp_flip_phase = self._phase.value  # type: ignore[attr-defined]
        self._dashboard_state.lp_flip_market = (  # type: ignore[attr-defined]
            cycle.market_question[:40] if cycle else ""
        )
        self._dashboard_state.lp_flip_entry_side = (  # type: ignore[attr-defined]
            cycle.entry_side if cycle else ""
        )
        self._dashboard_state.lp_flip_entry_price = (  # type: ignore[attr-defined]
            cycle.entry_price if cycle else 0.0
        )
        self._dashboard_state.lp_flip_exit_price = (  # type: ignore[attr-defined]
            cycle.exit_price if cycle else 0.0
        )
        self._dashboard_state.lp_flip_total_profit = self._total_profit  # type: ignore[attr-defined]
        self._dashboard_state.lp_flip_total_flips = self._total_flips  # type: ignore[attr-defined]
        self._dashboard_state.lp_flip_recent_flips = self._recent_flips  # type: ignore[attr-defined]
