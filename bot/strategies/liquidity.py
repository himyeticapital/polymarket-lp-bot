"""Liquidity provision strategy — one-sided LP reward hunting."""

from __future__ import annotations

import asyncio as _asyncio
import time as _time
from typing import TYPE_CHECKING

import structlog

from bot.constants import EventType, OrderType, Side, Strategy
from bot.risk.anti_detection import jitter_delay
from bot.strategies.base import BaseStrategy
from bot.types import Market, Signal, TokenInfo
from bot.utils.math import round_to_tick

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.dashboard.state import DashboardState
    from bot.data.database import Database
    from bot.execution.order_manager import OrderManager
    from bot.risk.manager import RiskManager
    from bot.types import EventBus

logger = structlog.get_logger(__name__)


class LiquidityStrategy(BaseStrategy):
    """One-sided LP: place limit orders on ONE side per market, switch on fill.

    Based on @DidiTrading approach:
      - Sort markets by reward first, prefer low competition
      - Place ONE limit order per market on the active side
      - Never place closest to midpoint — stay behind best bid
      - Smart refresh: keep stable orders, only replace moved ones
      - When filled, switch to the other side and repeat
      - Uses CLOB /rewards/markets/current for real reward data
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
        self.scan_interval_sec = jitter_delay(
            config.lp_refresh_interval_sec, config.timing_jitter_pct
        )
        # Per-market state: which side to place orders on
        self._market_sides: dict[str, str] = {}  # condition_id -> "yes" | "no"
        # Track live orders for smart refresh: condition_id -> {order_id, price, token_id, side, mid, shares}
        self._live_orders: dict[str, dict] = {}
        # Signal info for order tracking after execution
        self._pending_signal_info: dict[str, dict] = {}  # token_id -> {condition_id, side}
        # Midpoints at time of quoting, for accurate smart refresh tracking
        self._pending_mids: dict[str, float] = {}  # condition_id -> midpoint
        # Filled positions awaiting exit check: condition_id -> {token_id, side, fill_price, shares}
        self._filled_positions: dict[str, dict] = {}
        # Stop-loss threshold: exit if position drops more than 5% from fill price
        self._exit_loss_pct = 0.05
        # Take-profit threshold: exit if position gains more than 50%
        self._take_profit_pct = 0.50
        # Flag to seed legacy positions on first scan
        self._seeded_legacy = False
        # Fill cooldown: condition_id -> timestamp of last fill (skip for 30 min)
        self._fill_cooldowns: dict[str, float] = {}
        self._fill_cooldown_sec = 1800  # 30 minutes
        # Market metadata for dashboard: condition_id -> {question, min_shares, daily_reward, max_spread}
        self._market_metadata: dict[str, dict] = {}
        # Cached current prices for filled positions (updated by monitor loop)
        self._filled_prices: dict[str, float] = {}  # condition_id -> current price
        # Mid-price history for volatility detection: condition_id -> [(timestamp, mid)]
        self._mid_history: dict[str, list[tuple[float, float]]] = {}
        # Trade cycle tracking for dashboard history
        self._trade_history: list[dict] = []  # completed cycles (most recent first, max 50)
        self._pending_cycles: dict[str, dict] = {}  # condition_id -> entry info awaiting exit

    # ------------------------------------------------------------------
    # Run loop override
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Override to track order IDs for fill detection."""
        self._running = True
        logger.info("strategy.start", strategy="LiquidityStrategy")

        # Launch fast position monitor (30s) alongside main scan loop
        monitor_task = _asyncio.ensure_future(self._position_monitor_loop())

        while self._running:
            try:
                signals = await self.scan()
                if signals:
                    results = await self.order_manager.execute_batch(signals)
                    for result in results:
                        if result.success and result.order_id:
                            info = self._pending_signal_info.get(result.signal.token_id, {})
                            cid = info.get("condition_id", result.signal.condition_id)
                            side = info.get("side", "yes")
                            pending_mid = self._pending_mids.get(cid, result.signal.price)
                            self._live_orders[cid] = {
                                "order_id": result.order_id,
                                "price": result.signal.price,
                                "token_id": result.signal.token_id,
                                "side": side,
                                "mid": pending_mid,
                                "shares": result.signal.size,
                            }
            except _asyncio.CancelledError:
                break
            except Exception:
                logger.exception("strategy.scan_error", strategy="LiquidityStrategy")
                self._publish_event(
                    EventType.STRATEGY_ERROR,
                    {"strategy": "LiquidityStrategy", "error": "scan cycle failed"},
                )
            await _asyncio.sleep(self.scan_interval_sec)

        monitor_task.cancel()
        try:
            await monitor_task
        except _asyncio.CancelledError:
            pass
        logger.info("strategy.stopped", strategy="LiquidityStrategy")

    async def _position_monitor_loop(self) -> None:
        """Fast loop (30s): detect fills and enforce stop-loss between scan cycles."""
        while self._running:
            try:
                await _asyncio.sleep(30)
                await self._check_fills_and_update()
                await self._check_and_exit_positions()
            except _asyncio.CancelledError:
                break
            except Exception:
                logger.exception("lp.monitor_error")

    # ------------------------------------------------------------------
    # Scan — smart refresh using CLOB rewards API
    # ------------------------------------------------------------------

    async def scan(self) -> list[Signal]:
        """Smart refresh: detect fills, keep stable orders, only replace moved ones."""
        # 0. On first scan, seed legacy positions from inventory for exit monitoring
        if not self._seeded_legacy:
            self._seed_legacy_positions()

        # 1. Check which live orders are still open vs filled
        await self._check_fills_and_update()

        # Halt check: if auto-close failed, don't place new orders — only monitor exits
        if self._dashboard_state and self._dashboard_state.is_halted:
            await self._check_and_exit_positions()
            if not self._filled_positions:
                # All stuck positions cleared — resume trading
                self._dashboard_state.is_halted = False
                self._dashboard_state.add_log("Stuck positions cleared — resuming trading")
                logger.info("lp.halt_cleared_resuming")
            else:
                logger.warning(
                    "lp.halted_waiting_for_exit",
                    stuck_positions=len(self._filled_positions),
                )
            return []

        # 1b. Check filled positions for stop-loss exits
        await self._check_and_exit_positions()

        # 2. Fetch reward markets from CLOB API (real reward data)
        try:
            reward_markets = await self.clob_client.get_reward_markets()  # type: ignore[attr-defined]
        except Exception:
            logger.exception("lp.fetch_reward_markets_failed")
            return []

        # 3. Convert to Market objects and rank
        markets = self._convert_reward_markets(reward_markets)
        ranked = self._rank_markets(markets)
        signals: list[Signal] = []
        target_cids: set[str] = set()
        self._pending_mids.clear()  # clear before quote loop, populated by _try_quote_side

        # 4. Iterate ranked markets until we fill lp_max_markets active slots.
        #    Skip markets that fail (two-sided required, too expensive) and
        #    keep searching for viable ones further down the ranked list.
        active_count = 0
        for market in ranked:
            if active_count >= self.config.lp_max_markets:
                break
            signal = await self._quote_or_keep(market)
            if signal is not None:
                target_cids.add(market.condition_id)
                signals.append(signal)
                active_count += 1
                self._market_metadata[market.condition_id] = {
                    "question": market.question,
                    "min_shares": market.min_incentive_size,
                    "daily_reward": market.daily_reward_usd,
                    "max_spread": market.max_incentive_spread,
                }
            elif market.condition_id in self._live_orders:
                # Existing order was kept (price stable) — still an active slot
                target_cids.add(market.condition_id)
                active_count += 1
                self._market_metadata[market.condition_id] = {
                    "question": market.question,
                    "min_shares": market.min_incentive_size,
                    "daily_reward": market.daily_reward_usd,
                    "max_spread": market.max_incentive_spread,
                }

        # 5. Cancel orders in markets we're no longer targeting
        for cid in list(self._live_orders.keys()):
            if cid not in target_cids:
                info = self._live_orders[cid]
                try:
                    await self.order_manager.cancel_order(info["order_id"])
                    logger.info("lp.cancelled_non_target", market=cid[:12])
                except Exception:
                    pass
                del self._live_orders[cid]
                self._mid_history.pop(cid, None)

        # Store signal info for order tracking (mids already populated by _try_quote_side)
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
                    "edge": m.daily_reward_usd,
                    "fair": yes_t.price,
                })

        self._publish_event(
            EventType.MARKET_SCANNED,
            {
                "strategy": Strategy.LIQUIDITY,
                "count": len(reward_markets),
                "total_scanned": len(reward_markets),
                "avg_edge": 0.0,
                "markets": dashboard_markets[:8],
                "markets_quoted": min(len(ranked), self.config.lp_max_markets),
                "signals": len(signals),
            },
        )

        # Push LP market data to dashboard (resting + filled positions)
        if self._dashboard_state is not None:
            lp_market_data = []
            # Resting orders
            for cid, info in self._live_orders.items():
                meta = self._market_metadata.get(cid, {})
                question = meta.get("question", "")
                lp_market_data.append({
                    "market": question or cid[:16],
                    "condition_id": cid,
                    "side": info.get("side", ""),
                    "price": info.get("price", 0),
                    "shares": info.get("shares", 0),
                    "min_shares": meta.get("min_shares", 0),
                    "pool": meta.get("daily_reward", 0),
                    "spread": abs(info.get("mid", 0) - info.get("price", 0)),
                    "max_spread": meta.get("max_spread", 0),
                    "eligible": (
                        info.get("shares", 0) >= meta.get("min_shares", 0)
                        and abs(info.get("mid", 0) - info.get("price", 0)) <= meta.get("max_spread", 0)
                    ) if meta else False,
                    "filled": False,
                })
            # Filled positions (stop-loss monitored) with risk metrics
            for cid, pos in self._filled_positions.items():
                meta = self._market_metadata.get(cid, {})
                question = meta.get("question", "")
                fill_price = pos.get("fill_price", 0)
                shares = pos.get("shares", 0)
                current_price = self._filled_prices.get(cid, fill_price)
                cost = fill_price * shares
                current_value = current_price * shares
                unrealized_pnl = current_value - cost
                stop_loss_price = fill_price * (1 - self._exit_loss_pct)
                max_loss = cost * self._exit_loss_pct  # Max loss if stop-loss fires
                lp_market_data.append({
                    "market": question or cid[:16],
                    "condition_id": cid,
                    "side": pos.get("side", ""),
                    "price": fill_price,
                    "shares": shares,
                    "min_shares": meta.get("min_shares", 0),
                    "pool": meta.get("daily_reward", 0),
                    "spread": 0,
                    "max_spread": meta.get("max_spread", 0),
                    "eligible": False,
                    "filled": True,
                    # Risk metrics for filled positions
                    "current_price": round(current_price, 4),
                    "cost": round(cost, 2),
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "max_loss": round(max_loss, 2),
                    "stop_loss_price": round(stop_loss_price, 4),
                    "stop_loss_pct": self._exit_loss_pct,
                })
            self._dashboard_state.lp_markets = lp_market_data

        return signals

    # ------------------------------------------------------------------
    # Convert CLOB reward data to Market objects
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

    # ------------------------------------------------------------------
    # Legacy position seeding
    # ------------------------------------------------------------------

    def _seed_legacy_positions(self) -> None:
        """On first scan, load all existing positions from inventory into exit monitoring."""
        self._seeded_legacy = True
        try:
            positions = self.risk_manager.inventory.positions
        except AttributeError:
            logger.warning("lp.seed_legacy_no_inventory")
            return

        count = 0
        for token_id, pos in positions.items():
            cid = pos.condition_id
            if not cid or pos.size <= 0:
                continue
            # Don't overwrite positions already tracked from LP fills
            if cid in self._filled_positions:
                continue
            side = (pos.outcome or "yes").lower()
            self._filled_positions[cid] = {
                "token_id": token_id,
                "side": side,
                "fill_price": pos.avg_entry_price,
                "shares": pos.size,
            }
            # Also create pending cycle so exit is recorded in trade history
            meta = self._market_metadata.get(cid, {})
            self._pending_cycles[cid] = {
                "market": meta.get("question", cid[:16]),
                "side": side,
                "entry_price": pos.avg_entry_price,
                "shares": pos.size,
                "entry_time": "legacy",
            }
            count += 1

        if count:
            logger.info(
                "lp.seeded_legacy_positions",
                count=count,
                positions={cid[:12]: {
                    "shares": p["shares"],
                    "fill_price": p["fill_price"],
                } for cid, p in self._filled_positions.items()},
            )

    # ------------------------------------------------------------------
    # Fill detection
    # ------------------------------------------------------------------

    async def _check_fills_and_update(self) -> None:
        """Check live orders: detect fills (switch sides), confirm still open."""
        if not self._live_orders:
            return

        try:
            open_orders = await self.clob_client.get_open_orders()  # type: ignore[attr-defined]
        except Exception:
            logger.warning("lp.fill_check_failed")
            return

        open_ids: set[str] = set()
        for o in open_orders:
            oid = o.get("id") or o.get("order_id") or o.get("orderID")
            if oid:
                open_ids.add(oid)

        filled_cids: list[str] = []
        for cid, info in self._live_orders.items():
            if info["order_id"] not in open_ids:
                # Filled! Record position for exit monitoring, then switch sides
                old_side = self._market_sides.get(cid, "yes")
                new_side = "no" if old_side == "yes" else "yes"
                self._market_sides[cid] = new_side
                filled_cids.append(cid)

                # Record entry time for trade history
                entry_time = _time.strftime("%H:%M:%S")
                fill_shares = info.get("shares", 0)
                fill_price = info["price"]
                meta = self._market_metadata.get(cid, {})
                market_name = meta.get("question", cid[:16])

                # Auto-close mode: sell immediately instead of holding
                auto_close = self._dashboard_state and self._dashboard_state.lp_auto_close
                if auto_close:
                    sold, exit_price = await self._sell_position(
                        info["token_id"], fill_shares, fill_price
                    )
                    logger.info(
                        "lp.auto_close",
                        market=cid[:12],
                        side=old_side,
                        price=fill_price,
                        shares=fill_shares,
                        sold=sold,
                    )
                    if sold:
                        # Record completed trade cycle
                        cost = fill_price * fill_shares
                        revenue = exit_price * fill_shares if exit_price > 0 else 0.0
                        spread_cost = abs(fill_price - exit_price) * fill_shares if exit_price > 0 else 0.0
                        pnl = revenue - cost
                        self._record_trade_cycle(
                            market=market_name,
                            condition_id=cid,
                            side=old_side,
                            entry_price=fill_price,
                            exit_price=exit_price,
                            shares=fill_shares,
                            pnl=pnl,
                            spread_cost=spread_cost,
                            exit_reason="auto_close",
                            entry_time=entry_time,
                        )
                    if not sold:
                        # Auto-close failed — track for stop-loss AND halt new orders
                        logger.error(
                            "lp.auto_close_FAILED_HALTING",
                            market=cid[:12],
                            shares=fill_shares,
                            price=fill_price,
                        )
                        self._filled_positions[cid] = {
                            "token_id": info["token_id"],
                            "side": old_side,
                            "fill_price": fill_price,
                            "shares": fill_shares,
                        }
                        # Track as pending cycle for later exit
                        self._pending_cycles[cid] = {
                            "market": market_name,
                            "side": old_side,
                            "entry_price": fill_price,
                            "shares": fill_shares,
                            "entry_time": entry_time,
                        }
                        # Cancel all live orders — don't take new risk while stuck
                        try:
                            await self.clob_client.cancel_all()  # type: ignore[attr-defined]
                            self._live_orders.clear()
                            logger.warning("lp.auto_close_fail_cancelled_all_orders")
                        except Exception:
                            logger.exception("lp.cancel_all_after_fail")
                        # Mark halted on dashboard so operator sees it
                        if self._dashboard_state:
                            self._dashboard_state.is_halted = True
                            self._dashboard_state.add_log(
                                f"AUTO-CLOSE SELL FAILED for {cid[:12]} "
                                f"({info.get('shares', 0):.0f} shares @ "
                                f"{info['price']}). Trading halted — "
                                f"position tracked for stop-loss retry."
                            )
                else:
                    # Track the filled position for stop-loss monitoring
                    self._filled_positions[cid] = {
                        "token_id": info["token_id"],
                        "side": old_side,
                        "fill_price": fill_price,
                        "shares": fill_shares,
                    }
                    # Track as pending cycle for later exit
                    self._pending_cycles[cid] = {
                        "market": market_name,
                        "side": old_side,
                        "entry_price": fill_price,
                        "shares": fill_shares,
                        "entry_time": entry_time,
                    }
                # Cooldown: don't re-quote this market for 30 min
                self._fill_cooldowns[cid] = _time.monotonic()
                logger.info(
                    "lp.fill_detected",
                    market=cid[:12],
                    old_side=old_side,
                    new_side=new_side,
                    fill_price=info["price"],
                    shares=info.get("shares", 0),
                )

        for cid in filled_cids:
            del self._live_orders[cid]

    # ------------------------------------------------------------------
    # Position exit — stop-loss for filled positions
    # ------------------------------------------------------------------

    async def _check_and_exit_positions(self) -> None:
        """Check filled positions against current price; sell if loss exceeds threshold."""
        if not self._filled_positions:
            return

        exited: list[str] = []
        for cid, pos in self._filled_positions.items():
            token_id = pos["token_id"]
            fill_price = pos["fill_price"]
            shares = pos["shares"]

            if shares <= 0 or fill_price <= 0:
                exited.append(cid)
                continue

            try:
                current_price = await self.clob_client.get_price(token_id, "SELL")  # type: ignore[attr-defined]
            except Exception:
                continue

            if current_price <= 0:
                continue

            # Cache current price for dashboard display
            self._filled_prices[cid] = current_price

            change_pct = (current_price - fill_price) / fill_price  # positive = profit
            loss_pct = -change_pct  # positive = loss (for backward compat in logs)

            logger.info(
                "lp.exit_check",
                market=cid[:12],
                side=pos["side"],
                fill_price=round(fill_price, 3),
                current=round(current_price, 3),
                change_pct=round(change_pct, 3),
                shares=round(shares, 1),
                sl_threshold=self._exit_loss_pct,
                tp_threshold=self._take_profit_pct,
            )

            # Determine exit reason
            exit_reason = None
            if loss_pct >= self._exit_loss_pct:
                exit_reason = "stop_loss"
            elif change_pct >= self._take_profit_pct:
                exit_reason = "take_profit"

            if exit_reason:
                logger.warning(
                    "lp.exit_triggered",
                    market=cid[:12],
                    reason=exit_reason,
                    change_pct=round(change_pct, 3),
                    fill_price=round(fill_price, 3),
                    current=round(current_price, 3),
                    shares=round(shares, 1),
                )
                sold, exit_price = await self._sell_position(token_id, shares, current_price)
                if sold:
                    exited.append(cid)
                    # Complete pending trade cycle
                    pending = self._pending_cycles.pop(cid, None)
                    if pending:
                        cost = pending["entry_price"] * pending["shares"]
                        revenue = exit_price * pending["shares"] if exit_price > 0 else 0.0
                        pnl = revenue - cost
                        self._record_trade_cycle(
                            market=pending["market"],
                            condition_id=cid,
                            side=pending["side"],
                            entry_price=pending["entry_price"],
                            exit_price=exit_price,
                            shares=pending["shares"],
                            pnl=pnl,
                            spread_cost=abs(pending["entry_price"] - exit_price) * pending["shares"],
                            exit_reason=exit_reason,
                            entry_time=pending.get("entry_time", ""),
                        )

        for cid in exited:
            del self._filled_positions[cid]

    def _record_trade_cycle(
        self,
        market: str,
        condition_id: str,
        side: str,
        entry_price: float,
        exit_price: float,
        shares: float,
        pnl: float,
        spread_cost: float,
        exit_reason: str,
        entry_time: str,
    ) -> None:
        """Record a completed trade cycle for dashboard history."""
        exit_time = _time.strftime("%H:%M:%S")
        cycle = {
            "market": market,
            "condition_id": condition_id[:12],
            "side": side,
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "shares": round(shares, 1),
            "cost": round(entry_price * shares, 2),
            "revenue": round(exit_price * shares, 2) if exit_price > 0 else 0.0,
            "pnl": round(pnl, 2),
            "spread_cost": round(spread_cost, 2),
            "exit_reason": exit_reason,
            "entry_time": entry_time,
            "exit_time": exit_time,
        }
        self._trade_history.insert(0, cycle)
        if len(self._trade_history) > 50:
            self._trade_history = self._trade_history[:50]

        # Push to dashboard state
        if self._dashboard_state is not None:
            self._dashboard_state.lp_trade_history = self._trade_history

        logger.info(
            "lp.trade_cycle_complete",
            market=market[:30],
            side=side,
            entry=entry_price,
            exit=exit_price,
            shares=round(shares, 1),
            pnl=round(pnl, 2),
            reason=exit_reason,
        )

    async def _sell_position(self, token_id: str, shares: float, price: float) -> tuple[bool, float]:
        """Sell position using FOK at best bid for immediate fill.

        Strategy: check order book → FOK at best bid → step down if needed.
        FOK (Fill-or-Kill) guarantees immediate fill or nothing — no resting
        orders that the bot loses track of.

        Returns (success, exit_price) — exit_price is the actual fill price.
        """
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        try:
            # 1. Approve conditional token for selling
            sig_type = 2 if self.config.proxy_address else 0
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
                signature_type=sig_type,
            )
            await _asyncio.to_thread(
                self.clob_client.client.update_balance_allowance, params  # type: ignore[attr-defined]
            )

            # 2. Check actual token balance (6-decimal precision)
            bal_result = await _asyncio.to_thread(
                self.clob_client.client.get_balance_allowance, params  # type: ignore[attr-defined]
            )
            actual_balance = int(bal_result.get("balance", 0)) / 1_000_000
            sell_shares = min(shares, actual_balance)

            if sell_shares < 1:
                logger.info("lp.exit_skip_tiny", token=token_id[:16], balance=actual_balance)
                return True, 0.0  # remove from tracking, position is negligible

            # 3. Check order book to find best bid (actual market price)
            best_bid = None
            try:
                book = await self.clob_client.get_order_book(token_id)  # type: ignore[attr-defined]
                best_bid = book.best_bid
            except Exception:
                logger.warning("lp.exit_book_fetch_failed", token=token_id[:16])

            # Determine starting price: best bid if available, else fall back to passed price
            if best_bid and best_bid > 0:
                start_price = max(0.01, round_to_tick(best_bid))
                logger.info(
                    "lp.exit_at_best_bid",
                    token=token_id[:16],
                    best_bid=best_bid,
                    entry_price=round(price, 3),
                    spread=round(abs(price - best_bid), 3),
                )
            else:
                start_price = max(0.01, round_to_tick(price))

            # 4. FOK sell at best bid, then step down 1¢ at a time
            #    FOK = Fill-or-Kill: immediate fill at price or better, or order is cancelled
            #    No resting orders — we always know if we actually exited
            last_err = None
            attempt = 0
            sell_price = start_price
            while sell_price >= 0.01:
                attempt += 1
                try:
                    result = await self.clob_client.create_and_post_limit_order(  # type: ignore[attr-defined]
                        token_id=token_id,
                        price=sell_price,
                        size=sell_shares,
                        side="SELL",
                        order_type="FOK",
                    )
                    logger.info(
                        "lp.exit_sold_fok",
                        token=token_id[:16],
                        shares=round(sell_shares, 1),
                        price=sell_price,
                        attempt=attempt,
                        result=result,
                    )
                    return True, sell_price
                except Exception as e:
                    last_err = e
                    logger.warning(
                        "lp.exit_fok_retry",
                        token=token_id[:16],
                        price=sell_price,
                        attempt=attempt,
                        error=str(e)[:120],
                    )
                    await _asyncio.sleep(0.3)
                sell_price = round_to_tick(sell_price - 0.01)

            logger.error(
                "lp.exit_sell_all_failed",
                token=token_id[:16],
                shares=round(sell_shares, 1),
                attempts=attempt,
                last_error=str(last_err)[:200],
            )
            return False, 0.0

        except Exception:
            logger.exception("lp.exit_sell_failed", token=token_id[:16])
            return False, 0.0

    # ------------------------------------------------------------------
    # Market ranking
    # ------------------------------------------------------------------

    def _rank_markets(self, markets: list[Market]) -> list[Market]:
        """Filter and rank: highest reward first (Didi's sort)."""
        eligible: list[Market] = []
        for m in markets:
            if not self._passes_filters(m):
                continue
            eligible.append(m)

        # Log reward distribution for diagnostics
        reward_counts = {"0": 0, "1-9": 0, "10-49": 0, "50-99": 0, "100-499": 0, "500+": 0}
        for m in markets:
            r = m.daily_reward_usd
            if r <= 0:
                reward_counts["0"] += 1
            elif r < 10:
                reward_counts["1-9"] += 1
            elif r < 50:
                reward_counts["10-49"] += 1
            elif r < 100:
                reward_counts["50-99"] += 1
            elif r < 500:
                reward_counts["100-499"] += 1
            else:
                reward_counts["500+"] += 1

        logger.info(
            "lp.markets_filtered",
            total=len(markets),
            eligible=len(eligible),
            reward_dist=reward_counts,
        )

        # Didi: sort rewards high to low
        eligible.sort(key=lambda m: m.daily_reward_usd, reverse=True)
        return eligible

    def _passes_filters(self, m: Market) -> bool:
        """Apply reward + spread + expiry + cooldown filters."""
        if not m.active or m.max_incentive_spread <= 0:
            return False
        if len(m.tokens) < 2:
            return False
        if m.daily_reward_usd < self.config.lp_min_daily_reward:
            return False
        # Skip markets expiring within 3 days — high adverse selection risk
        if m.end_date:
            try:
                from datetime import datetime, timezone
                end_dt = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                days_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400
                if days_left < 3:
                    logger.debug("lp.skip_expiring", market=m.question[:40], days_left=round(days_left, 1))
                    return False
            except (ValueError, TypeError):
                pass
        # Skip markets on fill cooldown (30 min after fill)
        cooldown_ts = self._fill_cooldowns.get(m.condition_id)
        if cooldown_ts is not None:
            elapsed = _time.monotonic() - cooldown_ts
            if elapsed < self._fill_cooldown_sec:
                remaining = int(self._fill_cooldown_sec - elapsed)
                logger.info("lp.skip_cooldown", market=m.question[:40], remaining_sec=remaining)
                return False
            else:
                del self._fill_cooldowns[m.condition_id]
        return True

    # ------------------------------------------------------------------
    # Quote or keep — smart refresh core
    # ------------------------------------------------------------------

    async def _quote_or_keep(self, market: Market) -> Signal | None:
        """Place new order OR keep existing if price hasn't moved much."""
        if len(market.tokens) < 2:
            return None

        yes_token = next((t for t in market.tokens if t.outcome == "Yes"), None)
        no_token = next((t for t in market.tokens if t.outcome == "No"), None)
        if yes_token is None or no_token is None:
            return None

        side = self._market_sides.get(market.condition_id, "yes")

        # Try current side first, fall back to other side if too expensive
        result = await self._try_quote_side(market, side, yes_token, no_token)
        if result is not None:
            return result

        # If we already have an order tracked for this market (on the current side),
        # the None means "order was kept — price stable". Do NOT try the fallback
        # side, or we'd stack a second order on the opposite side every cycle.
        if market.condition_id in self._live_orders:
            return None

        # Current side genuinely failed (mid out of range, too expensive, etc.)
        # — try the other side as fallback
        alt_side = "no" if side == "yes" else "yes"
        result = await self._try_quote_side(market, alt_side, yes_token, no_token)
        if result is not None:
            # Update side preference since we're using the fallback
            self._market_sides[market.condition_id] = alt_side
        return result

    async def _try_quote_side(
        self, market: Market, side: str, yes_token: TokenInfo, no_token: TokenInfo
    ) -> Signal | None:
        """Try to quote on a specific side. Returns None if not viable."""
        token = yes_token if side == "yes" else no_token

        try:
            book = await self.clob_client.get_order_book(token.token_id)  # type: ignore[attr-defined]
        except Exception:
            return None

        mid = book.midpoint
        # Track mid-price history for volatility / manipulation detection
        _now = _time.monotonic()
        _hist = self._mid_history.setdefault(market.condition_id, [])
        _hist.append((_now, mid if mid is not None else 0.0))
        if len(_hist) > 10:
            self._mid_history[market.condition_id] = _hist[-10:]
            _hist = self._mid_history[market.condition_id]
        if mid is not None and len(_hist) >= 3:
            _recent = [h[1] for h in _hist]
            _mid_range = max(_recent) - min(_recent)
            if _mid_range > 0.05:
                logger.warning(
                    "lp.volatile_mid",
                    market=market.question[:40],
                    mid_range=round(_mid_range, 3),
                    recent_mids=[round(m, 3) for m in _recent[-5:]],
                )
        if mid is None or mid < 0.10 or mid > 0.90:
            # Polymarket requires two-sided orders when mid < 0.10 or > 0.90.
            # Single-sided earns ZERO rewards in that range.
            if mid is not None and (mid < 0.10 or mid > 0.90):
                logger.info(
                    "lp.skip_two_sided_required",
                    market=market.question[:40],
                    side=side,
                    mid=round(mid, 3),
                )
            return None

        if book.best_bid is None or book.best_bid < self.config.lp_min_best_bid:
            return None

        # Anti-manipulation smart refresh: only replace if order is outside
        # max_incentive_spread from current mid. Resists manipulation where
        # bots push mid slightly to trigger unnecessary cancel+replaces.
        existing = self._live_orders.get(market.condition_id)
        if existing and existing["side"] == side:
            existing_price = existing.get("price", 0)
            existing_spread = abs(mid - existing_price)

            if existing_spread <= market.max_incentive_spread:
                # Still within reward range — keep order
                logger.debug(
                    "lp.keeping_order",
                    market=market.question[:30],
                    mid=round(mid, 3),
                    order_price=round(existing_price, 3),
                    spread=round(existing_spread, 4),
                    max_spread=round(market.max_incentive_spread, 4),
                )
                return None

            # Outside max_incentive_spread — must replace to stay eligible
            try:
                await self.order_manager.cancel_order(existing["order_id"])
                logger.info(
                    "lp.replacing_order",
                    market=market.question[:30],
                    order_price=round(existing_price, 3),
                    new_mid=round(mid, 3),
                    spread=round(existing_spread, 4),
                    max_spread=round(market.max_incentive_spread, 4),
                )
            except Exception:
                pass
            del self._live_orders[market.condition_id]

        # Place BEHIND best bid
        if len(book.bids) >= 2:
            price = book.bids[1].price
        else:
            price = round_to_tick(book.best_bid - 0.01)

        if price <= 0.01 or price >= 0.99:
            return None

        # Verify within max_incentive_spread (reward eligibility)
        spread_from_mid = abs(mid - price)
        if spread_from_mid > market.max_incentive_spread:
            price = round_to_tick(mid - market.max_incentive_spread + 0.01)
            if price <= 0.01:
                return None
            spread_from_mid = abs(mid - price)

        # Size calculation with min share enforcement.
        # Account for size jitter (±10%) applied by order_manager — ensure
        # min_incentive_size is met even after worst-case jitter reduction.
        jitter_buffer = 1.0 / (1.0 - self.config.size_jitter_pct) if self.config.size_jitter_pct > 0 else 1.0
        min_with_buffer = market.min_incentive_size * jitter_buffer

        size_usd = self.config.lp_order_size_usd
        size_shares = size_usd / price

        if size_shares < min_with_buffer:
            needed_usd = min_with_buffer * price
            if needed_usd <= self.config.max_per_market_usd:
                size_shares = min_with_buffer
                size_usd = needed_usd
            else:
                logger.info(
                    "lp.skip_min_too_expensive",
                    market=market.question[:40],
                    side=side,
                    needed=round(needed_usd, 2),
                    max=self.config.max_per_market_usd,
                )
                return None

        shares_ok = size_shares >= market.min_incentive_size
        spread_ok = spread_from_mid <= market.max_incentive_spread

        # Pool share estimation: skip if our estimated daily reward < threshold.
        # Uses Q-score formula to weight book orders by proximity to mid.
        from bot.utils.math import reward_score as _reward_score
        total_q = sum(
            _reward_score(market.max_incentive_spread, abs(mid - lvl.price), lvl.size)
            for lvl in book.bids
            if abs(mid - lvl.price) <= market.max_incentive_spread
        )
        our_q = _reward_score(market.max_incentive_spread, spread_from_mid, size_shares)
        pool_share = our_q / (total_q + our_q) if (total_q + our_q) > 0 else 0.0
        est_daily = market.daily_reward_usd * pool_share

        if est_daily < self.config.lp_min_estimated_reward:
            logger.info(
                "lp.skip_low_reward_share",
                market=market.question[:40],
                side=side,
                est_daily=round(est_daily, 3),
                pool_share_pct=round(pool_share * 100, 2),
                our_q=round(our_q, 1),
                total_q=round(total_q, 1),
                pool=round(market.daily_reward_usd, 1),
            )
            return None

        # Store midpoint for accurate smart refresh tracking
        self._pending_mids[market.condition_id] = mid

        logger.info(
            "lp.quote",
            market=market.question[:40],
            side=side,
            price=price,
            shares=round(size_shares, 1),
            min_shares=market.min_incentive_size,
            shares_ok=shares_ok,
            spread_from_mid=round(spread_from_mid, 4),
            max_spread=round(market.max_incentive_spread, 4),
            spread_ok=spread_ok,
            reward=round(market.daily_reward_usd, 1),
        )

        return Signal(
            strategy=Strategy.LIQUIDITY,
            token_id=token.token_id,
            condition_id=market.condition_id,
            side=Side.BUY,
            price=price,
            size=size_shares,
            order_type=OrderType.GTC,
            reason=f"lp {side}-bid reward=${market.daily_reward_usd:.0f}/d shares_ok={shares_ok} spread_ok={spread_ok}",
            edge=market.daily_reward_usd,
            market_question=market.question,
        )

    # ------------------------------------------------------------------
    # Order tracking
    # ------------------------------------------------------------------

    def track_order(self, order_id: str, condition_id: str = "", token_id: str = "", side: str = "") -> None:
        """Record an order for fill detection and cleanup.

        Only updates _live_orders if the entry doesn't already exist
        (the run() method sets it with proper mid tracking).
        """
        if condition_id and order_id and condition_id not in self._live_orders:
            self._live_orders[condition_id] = {
                "order_id": order_id,
                "price": 0.0,
                "token_id": token_id,
                "side": side,
                "mid": 0.0,
                "shares": 0.0,
            }

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def on_shutdown(self) -> None:
        """Cancel all outstanding LP orders."""
        logger.info("lp.shutdown", live_orders=len(self._live_orders), tracked_positions=len(self._filled_positions))
        for cid, info in self._live_orders.items():
            try:
                await self.order_manager.cancel_order(info["order_id"])
            except Exception:
                pass
        self._live_orders.clear()
        if self._filled_positions:
            logger.warning(
                "lp.shutdown_open_positions",
                positions={cid[:12]: {
                    "side": p["side"],
                    "fill_price": p["fill_price"],
                    "shares": p["shares"],
                } for cid, p in self._filled_positions.items()},
            )
