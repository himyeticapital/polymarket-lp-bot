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

        sig_type = 2 if self._config.proxy_address else 0
        funder = self._config.proxy_address or None
        self._client = await asyncio.to_thread(
            ClobClient,
            self._config.clob_host,
            key=self._config.private_key.get_secret_value(),
            chain_id=self._config.chain_id,
            signature_type=sig_type,
            funder=funder,
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
        bids = sorted(
            [OrderBookLevel(price=float(b.price), size=float(b.size))
             for b in (raw.bids or [])],
            key=lambda x: x.price, reverse=True,  # best (highest) bid first
        )
        asks = sorted(
            [OrderBookLevel(price=float(a.price), size=float(a.size))
             for a in (raw.asks or [])],
            key=lambda x: x.price,  # best (lowest) ask first
        )
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

    async def get_reward_markets(self) -> list[dict]:
        """Fetch reward-eligible markets from CLOB /rewards/markets/current.

        Returns list of dicts with keys:
          condition_id, question, tokens, daily_reward, rewards_max_spread,
          rewards_min_size, active
        """
        import aiohttp
        import ssl
        import certifi

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        all_items: list[dict] = []
        cursor = ""
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl_ctx)
        ) as session:
            for page in range(30):  # safety pagination limit
                params: dict[str, str] = {"limit": "100"}
                if cursor:
                    params["next_cursor"] = cursor
                async with session.get(
                    f"{self._config.clob_host}/rewards/markets/current",
                    params=params,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(
                            "clob.rewards_page_error",
                            status=resp.status,
                            page=page,
                            body=body[:200],
                        )
                        break
                    data = await resp.json()
                items = data.get("data", [])
                cursor = data.get("next_cursor", "")
                all_items.extend(items)
                if not items or not cursor:
                    break

            # Filter to markets with min reward threshold, then enrich
            reward_items = []
            for item in all_items:
                configs = item.get("rewards_config", [])
                daily = sum(float(c.get("rate_per_day", 0)) for c in configs)
                if daily >= self._config.lp_min_daily_reward:
                    reward_items.append((daily, item))

            reward_items.sort(key=lambda x: x[0], reverse=True)
            logger.info(
                "clob.reward_items",
                total_fetched=len(all_items),
                above_threshold=len(reward_items),
                threshold=self._config.lp_min_daily_reward,
            )

            # Enrich top candidates with market metadata (question, tokens)
            # Use 5x max_markets since many high-reward markets have extreme
            # midpoints (< 0.10) that won't qualify for single-sided LP
            max_enrich = max(self._config.lp_max_markets * 5, 15)
            results: list[dict] = []
            for daily, item in reward_items[:max_enrich]:
                cid = item["condition_id"]
                try:
                    async with session.get(
                        f"{self._config.clob_host}/markets/{cid}"
                    ) as resp2:
                        if resp2.status != 200:
                            continue
                        mdata = await resp2.json()
                except Exception:
                    continue

                if not mdata.get("active", False) or mdata.get("closed", True):
                    continue

                tokens = mdata.get("tokens", [])
                results.append({
                    "condition_id": cid,
                    "question": mdata.get("question", ""),
                    "tokens": tokens,
                    "daily_reward": daily,
                    "rewards_max_spread": float(item.get("rewards_max_spread", 0)) / 100.0,
                    "rewards_min_size": float(item.get("rewards_min_size", 0)),
                    "active": True,
                    "min_tick_size": float(mdata.get("minimum_tick_size", 0.01)),
                    "end_date_iso": mdata.get("end_date_iso") or mdata.get("endDateIso"),
                })

        logger.info("clob.reward_markets_fetched", total=len(all_items), enriched=len(results))
        return results

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_balance(self) -> float:
        """Get USDC balance from CLOB API."""
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        sig_type = 2 if self._config.proxy_address else 0
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL, signature_type=sig_type
        )
        result = await asyncio.to_thread(self.client.get_balance_allowance, params)
        # Balance is in USDC units (6 decimals)
        raw_balance = int(result.get("balance", 0))
        return raw_balance / 1_000_000
