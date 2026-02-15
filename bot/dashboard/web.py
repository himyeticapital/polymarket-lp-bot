"""Web dashboard — serves live dashboard at http://localhost:8080."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

import structlog

if TYPE_CHECKING:
    from bot.dashboard.state import DashboardState
    from bot.types import EventBus

logger = structlog.get_logger(__name__)

_HTML_PATH = Path(__file__).parent / "templates" / "index.html"


class WebDashboard:
    """aiohttp web server that serves the dashboard HTML and pushes
    real-time updates to connected browsers via WebSocket."""

    def __init__(
        self,
        state: DashboardState,
        event_bus: EventBus,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self._state = state
        self._event_bus = event_bus
        self._host = host
        self._port = port
        self._app = web.Application()
        self._clients: set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None

        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/ws", self._handle_ws)
        self._app.router.add_get("/api/state", self._handle_api_state)
        self._app.router.add_post("/api/lp/auto-close", self._handle_toggle_auto_close)
        self._app.router.add_post("/api/lp-flip/toggle", self._handle_toggle_lp_flip)
        self._app.router.add_post("/api/strategy/switch", self._handle_strategy_switch)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("Web dashboard started", url=f"http://localhost:{self._port}")

    async def stop(self) -> None:
        for ws in list(self._clients):
            await ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def run_forever(self) -> None:
        """Start server and push state updates every second."""
        await self.start()
        try:
            while True:
                await self._broadcast_state()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        html = _HTML_PATH.read_text()
        return web.Response(text=html, content_type="text/html")

    async def _handle_api_state(self, request: web.Request) -> web.Response:
        return web.json_response(self._serialize_state())

    async def _handle_toggle_auto_close(self, request: web.Request) -> web.Response:
        self._state.lp_auto_close = not self._state.lp_auto_close
        logger.info("lp.auto_close_toggled", enabled=self._state.lp_auto_close)
        return web.json_response({"lp_auto_close": self._state.lp_auto_close})

    async def _handle_toggle_lp_flip(self, request: web.Request) -> web.Response:
        self._state.lp_flip_enabled = not self._state.lp_flip_enabled
        logger.info("lp_flip.toggled", enabled=self._state.lp_flip_enabled)
        return web.json_response({"lp_flip_enabled": self._state.lp_flip_enabled})

    async def _handle_strategy_switch(self, request: web.Request) -> web.Response:
        data = await request.json()
        choice = data.get("strategy", "didi_flip")
        if choice == "didi_flip":
            self._state.lp_flip_enabled = True
            self._state.lp_enabled = False
        elif choice == "multi_market":
            self._state.lp_flip_enabled = False
            self._state.lp_enabled = True
        else:
            self._state.lp_flip_enabled = False
            self._state.lp_enabled = False
        logger.info("strategy.switched", choice=choice,
                     lp_flip=self._state.lp_flip_enabled,
                     lp=self._state.lp_enabled)
        return web.json_response({
            "lp_flip_enabled": self._state.lp_flip_enabled,
            "lp_enabled": self._state.lp_enabled,
        })

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._clients.add(ws)
        logger.debug("ws.client_connected", total=len(self._clients))

        # Send initial state
        try:
            await ws.send_json(self._serialize_state())
        except Exception:
            pass

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            self._clients.discard(ws)
            logger.debug("ws.client_disconnected", total=len(self._clients))

        return ws

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def _broadcast_state(self) -> None:
        if not self._clients:
            return
        data = self._serialize_state()
        dead: list[web.WebSocketResponse] = []
        for ws in self._clients:
            try:
                await ws.send_json(data)
            except (ConnectionResetError, Exception):
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    def _serialize_state(self) -> dict:
        s = self._state
        total = s.wins + s.losses
        win_rate = (s.wins / total * 100) if total > 0 else 0.0
        portfolio = s.balance + s.positions_value
        pnl_pct = ((portfolio - s.initial_balance) / s.initial_balance * 100) if s.initial_balance else 0.0

        return {
            "balance": round(portfolio, 2),
            "cash": round(s.balance, 2),
            "positions_value": round(s.positions_value, 2),
            "initial_balance": round(s.initial_balance, 2),
            "total_pnl": round(s.total_pnl, 2),
            "pnl_pct": round(pnl_pct, 1),
            "wins": s.wins,
            "losses": s.losses,
            "win_rate": round(win_rate, 1),
            "daily_volume": round(s.daily_volume, 2),
            "api_costs": round(s.api_costs, 2),
            "total_volume": round(s.total_volume, 2),
            "lp_rewards": round(s.lp_rewards, 4),
            "lp_markets": s.lp_markets,
            "markets_traded": s.markets_traded,
            "balance_history": s.balance_history[-60:],
            "markets": s.markets[:8],
            "markets_scanned": s.markets_scanned,
            "avg_edge": round(s.avg_edge, 3),
            "activity_log": s.activity_log[:50],
            "total_trades": s.total_trades,
            "avg_bet": round(s.avg_bet, 2),
            "best_trade": round(s.best_trade, 2),
            "worst_trade": round(s.worst_trade, 2),
            "sharpe": round(s.sharpe, 2),
            "runway_pct": round(s.runway_pct, 1),
            "is_halted": s.is_halted,
            "is_dry_run": s.is_dry_run,
            "lp_auto_close": s.lp_auto_close,
            "lp_enabled": s.lp_enabled,
            "lp_flip_enabled": s.lp_flip_enabled,
            "lp_flip_phase": s.lp_flip_phase,
            "lp_flip_market": s.lp_flip_market,
            "lp_flip_entry_side": s.lp_flip_entry_side,
            "lp_flip_entry_price": s.lp_flip_entry_price,
            "lp_flip_exit_price": s.lp_flip_exit_price,
            "lp_flip_total_profit": round(s.lp_flip_total_profit, 4),
            "lp_flip_total_flips": s.lp_flip_total_flips,
            "lp_flip_recent_flips": s.lp_flip_recent_flips[:10],
            "strategies": {
                key: {
                    "name": ss.name,
                    "trades": ss.trades,
                    "pnl": round(ss.pnl, 2),
                    "volume": round(ss.volume, 2),
                    "order_notional": round(ss.order_notional, 2),
                    "signals": ss.signals,
                    "last_scan": ss.last_scan,
                    "status": ss.status,
                }
                for key, ss in s.strategy_stats.items()
            },
        }
