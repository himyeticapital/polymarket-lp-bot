"""Gamma API client for market discovery."""

from __future__ import annotations

import json
import ssl

import aiohttp
import certifi
import structlog

from bot.config import BotConfig
from bot.types import Market, TokenInfo
from bot.utils.retry import async_retry

logger = structlog.get_logger()


class GammaClient:
    """Async client for the Polymarket Gamma API (market metadata)."""

    def __init__(self, config: BotConfig) -> None:
        self._base_url = config.gamma_host
        self._session: aiohttp.ClientSession | None = None

    async def connect(self) -> None:
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx))
        logger.info("Gamma client connected", url=self._base_url)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("Gamma client not connected.")
        return self._session

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_markets(self, active: bool = True, max_results: int = 500) -> list[Market]:
        """Fetch active markets with pagination (up to max_results)."""
        all_markets: list[Market] = []
        for offset in range(0, max_results, 100):
            batch = await self._fetch_market_page(active, limit=100, offset=offset)
            all_markets.extend(batch)
            if len(batch) < 100:
                break
        return all_markets

    async def _fetch_market_page(
        self, active: bool, limit: int = 100, offset: int = 0
    ) -> list[Market]:
        """Fetch a single page of active, non-closed markets with incentive parameters."""
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": "false",
        }
        async with self.session.get(f"{self._base_url}/markets", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        markets = []
        for m in data if isinstance(data, list) else []:
            tokens = self._parse_tokens(m)
            # Extract daily reward from clobRewards if available
            daily_reward = 0.0
            for cr in m.get("clobRewards", []) or []:
                daily_reward += float(cr.get("rewardsDailyRate", 0))
            # competitive is 0-1 float; map to category
            comp = float(m.get("competitive", 0.5))
            if comp < 0.4:
                comp_level = "mild"
            elif comp < 0.75:
                comp_level = "moderate"
            else:
                comp_level = "fierce"
            markets.append(Market(
                condition_id=m.get("conditionId", ""),
                question=m.get("question", ""),
                tokens=tokens,
                active=m.get("active", True),
                min_incentive_size=float(m.get("rewardsMinSize", 0)),
                max_incentive_spread=float(m.get("rewardsMaxSpread", 0)) / 100.0,
                category=m.get("category", ""),
                end_date=m.get("endDateIso"),
                daily_reward_usd=daily_reward,
                competition_level=comp_level,
                competitive_raw=float(m.get("competitive", 0.5)),
                volume_24h=float(m.get("volume24hr", 0)),
                liquidity=float(m.get("liquidity", 0)),
                spread=float(m.get("spread", 0)),
                best_bid=float(m.get("bestBid", 0)),
                best_ask=float(m.get("bestAsk", 0)),
            ))
        return markets

    @staticmethod
    def _parse_tokens(m: dict) -> list[TokenInfo]:
        """Parse clobTokenIds + outcomes + outcomePrices into TokenInfo list."""
        try:
            raw_ids = m.get("clobTokenIds", "[]")
            token_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
        except (json.JSONDecodeError, TypeError):
            return []
        try:
            raw_outcomes = m.get("outcomes", "[]")
            outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
        except (json.JSONDecodeError, TypeError):
            outcomes = []
        try:
            raw_prices = m.get("outcomePrices", "[]")
            prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        except (json.JSONDecodeError, TypeError):
            prices = []

        tokens = []
        for i, tid in enumerate(token_ids):
            outcome = outcomes[i] if i < len(outcomes) else ""
            price = float(prices[i]) if i < len(prices) else 0.0
            tokens.append(TokenInfo(token_id=str(tid), outcome=outcome, price=price))
        return tokens

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_events(self, active: bool = True) -> list[dict]:
        """Fetch events with nested markets."""
        params = {"active": str(active).lower(), "closed": "false"}
        async with self.session.get(f"{self._base_url}/events", params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    @async_retry(max_attempts=3, base_delay=1.0)
    async def search(self, query: str) -> list[dict]:
        """Search markets by keyword."""
        params = {"query": query}
        async with self.session.get(f"{self._base_url}/search", params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_market_by_id(self, condition_id: str) -> Market | None:
        """Fetch a single market by condition ID."""
        async with self.session.get(
            f"{self._base_url}/markets/{condition_id}"
        ) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            m = await resp.json()

        tokens = self._parse_tokens(m)
        return Market(
            condition_id=m.get("conditionId", ""),
            question=m.get("question", ""),
            tokens=tokens,
            active=m.get("active", True),
            min_incentive_size=float(m.get("rewardsMinSize", 0)),
            max_incentive_spread=float(m.get("rewardsMaxSpread", 0)) / 100.0,
            category=m.get("category", ""),
            end_date=m.get("endDateIso"),
        )
