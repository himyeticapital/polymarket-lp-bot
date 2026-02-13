"""Data API client for leaderboard, positions, and trader activity (copy trading)."""

from __future__ import annotations

import aiohttp
import structlog

from bot.config import BotConfig
from bot.utils.retry import async_retry

logger = structlog.get_logger()


class DataApiClient:
    """Async client for Polymarket Data API."""

    def __init__(self, config: BotConfig) -> None:
        self._base_url = config.data_host
        self._session: aiohttp.ClientSession | None = None

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info("Data API client connected", url=self._base_url)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("Data API client not connected.")
        return self._session

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_leaderboard(self, window: str = "all") -> list[dict]:
        """Fetch trader leaderboard rankings."""
        params = {"window": window}
        async with self.session.get(
            f"{self._base_url}/leaderboard", params=params
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data if isinstance(data, list) else data.get("leaders", [])

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_positions(self, address: str) -> list[dict]:
        """Fetch current positions for a wallet address."""
        params = {"user": address}
        async with self.session.get(
            f"{self._base_url}/positions", params=params
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data if isinstance(data, list) else []

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_activity(self, address: str, limit: int = 50) -> list[dict]:
        """Fetch recent activity for a wallet address."""
        params = {"user": address, "limit": limit}
        async with self.session.get(
            f"{self._base_url}/activity", params=params
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data if isinstance(data, list) else []

    @async_retry(max_attempts=3, base_delay=1.0)
    async def get_trades(self, address: str, limit: int = 100) -> list[dict]:
        """Fetch trade history for a wallet address."""
        params = {"user": address, "limit": limit}
        async with self.session.get(
            f"{self._base_url}/trades", params=params
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data if isinstance(data, list) else []
