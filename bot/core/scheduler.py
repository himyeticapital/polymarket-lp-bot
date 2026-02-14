"""Scheduler for periodic tasks: health checks, daily summaries, stats refresh."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from bot.config import BotConfig
from bot.data.database import Database
from bot.data.models import get_today_volume, get_trade_returns, get_trade_stats
from bot.dashboard.state import DashboardState
from bot.notifications.telegram import TelegramNotifier
from bot.notifications.formatter import format_daily_summary
from bot.utils.math import sharpe_ratio, runway_pct

if TYPE_CHECKING:
    from bot.clients.data_api import DataApiClient

logger = structlog.get_logger()


class Scheduler:
    """Runs periodic background tasks."""

    def __init__(
        self,
        config: BotConfig,
        db: Database,
        state: DashboardState,
        notifier: TelegramNotifier | None = None,
        data_api: DataApiClient | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._state = state
        self._notifier = notifier
        self._data_api = data_api

    async def run_stats_refresh(self) -> None:
        """Periodically refresh footer stats from DB (every 30s)."""
        while True:
            try:
                stats = await get_trade_stats(self._db)
                returns = await get_trade_returns(self._db)
                volume = await get_today_volume(self._db)

                # Only update stats that events don't track.
                # daily_volume and avg_bet are driven by the event bus, not DB.
                self._state.best_trade = stats.get("best_trade", 0) or 0
                self._state.worst_trade = stats.get("worst_trade", 0) or 0
                self._state.sharpe = sharpe_ratio(returns)

                avg_daily_loss = abs(self._state.worst_trade) if self._state.worst_trade < 0 else 0
                self._state.runway_pct = runway_pct(self._state.balance, avg_daily_loss)

            except Exception as e:
                logger.error("Stats refresh failed", error=str(e))

            await asyncio.sleep(30)

    async def run_profile_refresh(self) -> None:
        """Fetch total volume and LP rewards from Polymarket API (every 60s)."""
        if not self._data_api:
            return

        address = self._config.proxy_address or self._config.wallet_address
        if not address:
            return

        while True:
            try:
                profile = await self._data_api.get_profile_stats(address)
                if profile:
                    self._state.total_volume = float(profile.get("vol", 0) or 0)

                rewards = await self._data_api.get_rewards_earned(address)
                self._state.lp_rewards = rewards

                markets = await self._data_api.get_markets_traded(address)
                self._state.markets_traded = markets

                logger.debug(
                    "profile.refreshed",
                    total_volume=round(self._state.total_volume, 2),
                    lp_rewards=round(self._state.lp_rewards, 4),
                    markets_traded=self._state.markets_traded,
                )
            except Exception as e:
                logger.warning("Profile refresh failed", error=str(e))

            await asyncio.sleep(60)

    async def run_daily_summary(self) -> None:
        """Send daily summary via Telegram at midnight UTC."""
        while True:
            await asyncio.sleep(3600)  # Check every hour
            try:
                if self._notifier and self._config.telegram_enabled:
                    stats = await get_trade_stats(self._db)
                    msg = format_daily_summary(stats, self._state.balance)
                    await self._notifier.send_message(msg)
            except Exception as e:
                logger.error("Daily summary failed", error=str(e))

    async def run_health_check(self) -> None:
        """Log health status periodically."""
        while True:
            logger.info(
                "Health check",
                balance=self._state.balance,
                trades=self._state.total_trades,
                pnl=self._state.total_pnl,
                halted=self._state.is_halted,
            )
            await asyncio.sleep(300)  # Every 5 min
