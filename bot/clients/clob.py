"""Async wrapper around the synchronous py-clob-client SDK."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from bot.config import BotConfig
from bot.constants import Side
from bot.types import OrderBook, OrderBookLevel
from bot.utils.retry import async_retry

logger = structlog.get_logger()


class AsyncClobClient:
    """Async interface to Polymarket CLOB API via py-clob-client."""

    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._client: Any = None

    async def connect(self) -> None:
        """Initialize the synchronous CLOB client and derive API credentials."""
        from py_clob_client.client import ClobClient

        self._client = await asyncio.to_thread(
            ClobClient,
            self._config.clob_host,
            key=self._config.private_key.get_secret_value(),
            chain_id=self._config.chain_id,
            signature_type=0,
        )
        creds = await asyncio.to_thread(self._client.create_or_derive_api_creds)
        self._client.set_api_creds(creds)
        logger.info("CLOB client connected", host=self._config.clob_host)

    async def close(self) -> None:
        """No persistent connection to close."""
        self._client = None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("CLOB client not connected. Call connect() first.")
        return self._client

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch order book for a token."""
        raw = await asyncio.to_thread(self.client.get_order_book, token_id)
        bids = [
            OrderBookLevel(price=float(b.get("price", 0)), size=float(b.get("size", 0)))
            for b in (raw.get("bids") or [])
        ]
        asks = [
            OrderBookLevel(price=float(a.get("price", 0)), size=float(a.get("size", 0)))
            for a in (raw.get("asks") or [])
        ]
        return OrderBook(token_id=token_id, bids=bids, asks=asks)

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price for a token."""
        raw = await asyncio.to_thread(self.client.get_midpoint, token_id)
        return float(raw.get("mid", 0) if isinstance(raw, dict) else raw)

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_price(self, token_id: str, side: str = "BUY") -> float:
        """Get quoted price for a token."""
        raw = await asyncio.to_thread(self.client.get_price, token_id, side)
        return float(raw.get("price", 0) if isinstance(raw, dict) else raw)

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_markets(self) -> list[dict]:
        """Fetch paginated list of markets."""
        raw = await asyncio.to_thread(self.client.get_markets)
        return raw if isinstance(raw, list) else raw.get("data", [])

    @async_retry(max_attempts=2, base_delay=1.0)
    async def create_and_post_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
    ) -> dict:
        """Create and post a limit order."""
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.clob_types import OrderType as ClobOrderType

        args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side.upper(),
        )
        otype = ClobOrderType.FOK if order_type == "FOK" else ClobOrderType.GTC
        signed = await asyncio.to_thread(self.client.create_order, args)
        result = await asyncio.to_thread(self.client.post_order, signed, otype)
        logger.info(
            "Order posted",
            token_id=token_id, side=side, price=price, size=size,
            order_type=order_type, result=result,
        )
        return result if isinstance(result, dict) else {"id": str(result)}

    @async_retry(max_attempts=2, base_delay=1.0)
    async def create_and_post_market_order(
        self, token_id: str, amount: float, side: str
    ) -> dict:
        """Create and post a market order (Fill or Kill)."""
        from py_clob_client.clob_types import MarketOrderArgs
        from py_clob_client.clob_types import OrderType as ClobOrderType

        args = MarketOrderArgs(token_id=token_id, amount=amount)
        signed = await asyncio.to_thread(self.client.create_market_order, args)
        result = await asyncio.to_thread(
            self.client.post_order, signed, ClobOrderType.FOK
        )
        return result if isinstance(result, dict) else {"id": str(result)}

    @async_retry(max_attempts=2, base_delay=0.5)
    async def cancel_order(self, order_id: str) -> dict:
        """Cancel a specific order."""
        result = await asyncio.to_thread(self.client.cancel, order_id)
        return result if isinstance(result, dict) else {}

    async def cancel_all(self) -> dict:
        """Cancel all open orders."""
        result = await asyncio.to_thread(self.client.cancel_all)
        logger.info("All orders cancelled", result=result)
        return result if isinstance(result, dict) else {}

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        from py_clob_client.clob_types import OpenOrderParams

        raw = await asyncio.to_thread(self.client.get_orders, OpenOrderParams())
        return raw if isinstance(raw, list) else []

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_trades(self) -> list[dict]:
        """Get user's trade history."""
        raw = await asyncio.to_thread(self.client.get_trades)
        return raw if isinstance(raw, list) else []
