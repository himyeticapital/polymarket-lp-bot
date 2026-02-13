"""Gamma API client for market discovery."""

from __future__ import annotations

import aiohttp
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
        self._session = aiohttp.ClientSession()
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
    async def get_markets(
        self, active: bool = True, limit: int = 100, offset: int = 0
    ) -> list[Market]:
        """Fetch active markets with incentive parameters."""
        params = {"limit": limit, "offset": offset, "active": str(active).lower()}
        async with self.session.get(f"{self._base_url}/markets", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        markets = []
        for m in data if isinstance(data, list) else []:
            tokens = []
            for t in m.get("tokens", []):
                tokens.append(TokenInfo(
                    token_id=t.get("token_id", ""),
                    outcome=t.get("outcome", ""),
                    price=float(t.get("price", 0)),
                ))
            markets.append(Market(
                condition_id=m.get("condition_id", ""),
                question=m.get("question", ""),
                tokens=tokens,
                active=m.get("active", True),
                min_incentive_size=float(m.get("min_incentive_size", 0)),
                max_incentive_spread=float(m.get("max_incentive_spread", 0)),
                category=m.get("category", ""),
                end_date=m.get("end_date_iso"),
            ))
        return markets

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

        tokens = [
            TokenInfo(
                token_id=t.get("token_id", ""),
                outcome=t.get("outcome", ""),
                price=float(t.get("price", 0)),
            )
            for t in m.get("tokens", [])
        ]
        return Market(
            condition_id=m.get("condition_id", ""),
            question=m.get("question", ""),
            tokens=tokens,
            active=m.get("active", True),
            min_incentive_size=float(m.get("min_incentive_size", 0)),
            max_incentive_spread=float(m.get("max_incentive_spread", 0)),
            category=m.get("category", ""),
            end_date=m.get("end_date_iso"),
        )
