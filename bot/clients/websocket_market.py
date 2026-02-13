"""Market WebSocket client for real-time orderbook data."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import structlog
import websockets

from bot.config import BotConfig
from bot.constants import WS_MARKET_URL, WS_PING_INTERVAL
from bot.types import OrderBook, OrderBookLevel

logger = structlog.get_logger()


class MarketWebSocket:
    """Persistent WebSocket for real-time L2 orderbook updates."""

    def __init__(self, config: BotConfig) -> None:
        self._url = WS_MARKET_URL
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._subscribed: set[str] = set()
        self._orderbooks: dict[str, OrderBook] = {}
        self._running = False
        self._reconnect_delay = 1.0

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        try:
            self._ws = await websockets.connect(self._url)
            self._running = True
            self._reconnect_delay = 1.0
            logger.info("Market WebSocket connected", url=self._url)
        except Exception as e:
            logger.error("Market WebSocket connection failed", error=str(e))
            raise

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("Market WebSocket disconnected")

    async def subscribe(self, asset_ids: list[str]) -> None:
        """Subscribe to orderbook updates for given token IDs."""
        if not self._ws:
            return
        new_ids = [aid for aid in asset_ids if aid not in self._subscribed]
        if not new_ids:
            return
        msg = json.dumps({"assets_ids": new_ids, "type": "market"})
        await self._ws.send(msg)
        self._subscribed.update(new_ids)
        logger.info("Subscribed to markets", count=len(new_ids))

    async def unsubscribe(self, asset_ids: list[str]) -> None:
        """Unsubscribe from token IDs."""
        if not self._ws:
            return
        msg = json.dumps({"assets_ids": asset_ids, "type": "market", "action": "unsubscribe"})
        await self._ws.send(msg)
        self._subscribed -= set(asset_ids)

    def get_orderbook(self, token_id: str) -> OrderBook | None:
        """Get cached orderbook snapshot for a token."""
        return self._orderbooks.get(token_id)

    async def listen(self) -> AsyncIterator[dict]:
        """Yield orderbook update messages. Auto-reconnects on failure."""
        while self._running:
            try:
                if not self._ws:
                    await self.connect()
                    # Re-subscribe after reconnect
                    if self._subscribed:
                        await self.subscribe(list(self._subscribed))

                ping_task = asyncio.create_task(self._heartbeat())
                try:
                    async for raw in self._ws:  # type: ignore[union-attr]
                        if not self._running:
                            break
                        try:
                            data = json.loads(raw)
                            self._update_cache(data)
                            yield data
                        except json.JSONDecodeError:
                            continue
                finally:
                    ping_task.cancel()

            except websockets.ConnectionClosed:
                logger.warning("Market WS disconnected, reconnecting...")
            except Exception as e:
                logger.error("Market WS error", error=str(e))

            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
                self._ws = None

    async def _heartbeat(self) -> None:
        """Send PING every 10 seconds to keep connection alive."""
        while self._running and self._ws:
            try:
                await self._ws.ping()
                await asyncio.sleep(WS_PING_INTERVAL)
            except Exception:
                break

    def _update_cache(self, data: dict) -> None:
        """Update in-memory orderbook cache from a message."""
        asset_id = data.get("asset_id") or data.get("market")
        if not asset_id:
            return
        bids = [
            OrderBookLevel(price=float(b["price"]), size=float(b["size"]))
            for b in data.get("bids", [])
            if "price" in b and "size" in b
        ]
        asks = [
            OrderBookLevel(price=float(a["price"]), size=float(a["size"]))
            for a in data.get("asks", [])
            if "price" in a and "size" in a
        ]
        if bids or asks:
            existing = self._orderbooks.get(asset_id)
            if existing:
                if bids:
                    existing.bids = bids
                if asks:
                    existing.asks = asks
            else:
                self._orderbooks[asset_id] = OrderBook(
                    token_id=asset_id, bids=bids, asks=asks
                )
