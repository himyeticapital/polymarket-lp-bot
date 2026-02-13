"""User WebSocket client for authenticated order fill notifications."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import structlog
import websockets

from bot.config import BotConfig
from bot.constants import WS_USER_URL, WS_PING_INTERVAL

logger = structlog.get_logger()


class UserWebSocket:
    """Authenticated WebSocket for order status and fill updates."""

    def __init__(self, config: BotConfig) -> None:
        self._url = WS_USER_URL
        self._config = config
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._reconnect_delay = 1.0

    async def connect(self) -> None:
        """Establish authenticated WebSocket connection."""
        self._ws = await websockets.connect(self._url)
        self._running = True
        self._reconnect_delay = 1.0
        logger.info("User WebSocket connected")

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def listen(self) -> AsyncIterator[dict]:
        """Yield order/fill update messages. Auto-reconnects."""
        while self._running:
            try:
                if not self._ws:
                    await self.connect()

                ping_task = asyncio.create_task(self._heartbeat())
                try:
                    async for raw in self._ws:  # type: ignore[union-attr]
                        if not self._running:
                            break
                        try:
                            yield json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                finally:
                    ping_task.cancel()

            except websockets.ConnectionClosed:
                logger.warning("User WS disconnected, reconnecting...")
            except Exception as e:
                logger.error("User WS error", error=str(e))

            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
                self._ws = None

    async def _heartbeat(self) -> None:
        """Send PING to keep connection alive."""
        while self._running and self._ws:
            try:
                await self._ws.ping()
                await asyncio.sleep(WS_PING_INTERVAL)
            except Exception:
                break
