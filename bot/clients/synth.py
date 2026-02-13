"""Synth API client for probability forecasts and edge detection."""

from __future__ import annotations

import aiohttp
import structlog

from bot.config import BotConfig
from bot.types import SynthForecast
from bot.utils.retry import async_retry

logger = structlog.get_logger()


class SynthClient:
    """Async client for Synth probability forecast API."""

    def __init__(self, config: BotConfig) -> None:
        self._base_url = config.synth_host
        self._api_key = config.synth_api_key.get_secret_value()
        self._session: aiohttp.ClientSession | None = None

    async def connect(self) -> None:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        self._session = aiohttp.ClientSession(headers=headers)
        logger.info("Synth client connected", url=self._base_url)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("Synth client not connected.")
        return self._session

    @async_retry(max_attempts=3, base_delay=2.0)
    async def get_hourly_up_down(self, asset: str) -> SynthForecast:
        """Get hourly up/down probability forecast for a crypto asset."""
        url = f"{self._base_url}/insights/polymarket/up-down/hourly"
        params = {"asset": asset.upper()}
        async with self.session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        synth_prob = float(data.get("synth_probability_up", 0.5))
        poly_prob = float(data.get("polymarket_probability_up", 0.5))
        return SynthForecast(
            asset=asset.upper(),
            synth_prob_up=synth_prob,
            poly_prob_up=poly_prob,
            edge=synth_prob - poly_prob,
            up_token_id=data.get("up_token_id", ""),
            down_token_id=data.get("down_token_id", ""),
        )

    @async_retry(max_attempts=3, base_delay=2.0)
    async def get_daily_up_down(self, asset: str) -> SynthForecast:
        """Get daily up/down probability forecast."""
        url = f"{self._base_url}/insights/polymarket/up-down/daily"
        params = {"asset": asset.upper()}
        async with self.session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        synth_prob = float(data.get("synth_probability_up", 0.5))
        poly_prob = float(data.get("polymarket_probability_up", 0.5))
        return SynthForecast(
            asset=asset.upper(),
            synth_prob_up=synth_prob,
            poly_prob_up=poly_prob,
            edge=synth_prob - poly_prob,
            up_token_id=data.get("up_token_id", ""),
            down_token_id=data.get("down_token_id", ""),
        )

    @async_retry(max_attempts=3, base_delay=2.0)
    async def get_volatility(self, asset: str) -> dict:
        """Get volatility forecast for an asset."""
        url = f"{self._base_url}/insights/volatility"
        params = {"asset": asset.upper()}
        async with self.session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()
