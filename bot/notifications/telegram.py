"""Telegram notifier — sends alerts via python-telegram-bot."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from bot.notifications.formatter import (
    format_daily_summary,
    format_drawdown_alert,
    format_trade_alert,
)
from bot.types import OrderResult

if TYPE_CHECKING:
    from bot.config import BotConfig

logger = structlog.get_logger(__name__)


class TelegramNotifier:
    """Sends trade alerts and summaries to a Telegram chat.

    Only active when ``config.telegram_enabled`` is True (both
    ``telegram_bot_token`` and ``telegram_chat_id`` are set).
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._bot: object | None = None

    @property
    def enabled(self) -> bool:
        return self.config.telegram_enabled

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Initialise the Telegram bot instance."""
        if not self.enabled:
            logger.info("telegram.disabled")
            return

        try:
            from telegram import Bot  # type: ignore[import-untyped]

            token = self.config.telegram_bot_token
            assert token is not None
            self._bot = Bot(token=token.get_secret_value())
            logger.info("telegram.connected")
        except ImportError:
            logger.warning("telegram.missing_dependency — pip install python-telegram-bot")
        except Exception:
            logger.exception("telegram.connect_failed")

    async def close(self) -> None:
        """Shut down the bot session."""
        if self._bot is not None:
            try:
                await self._bot.shutdown()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._bot = None
            logger.info("telegram.closed")

    # ------------------------------------------------------------------
    # Notification methods
    # ------------------------------------------------------------------

    async def send_trade_alert(self, result: OrderResult) -> None:
        text = format_trade_alert(result)
        await self.send_message(text)

    async def send_drawdown_alert(self, balance: float, threshold: float) -> None:
        text = format_drawdown_alert(balance, threshold)
        await self.send_message(text)

    async def send_daily_summary(self, stats: dict, balance: float) -> None:
        text = format_daily_summary(stats, balance)
        await self.send_message(text)

    async def send_message(self, text: str) -> None:
        """Send a plain-text message to the configured chat."""
        if not self.enabled or self._bot is None:
            return

        chat_id = self.config.telegram_chat_id
        if chat_id is None:
            return

        try:
            await self._bot.send_message(chat_id=chat_id, text=text)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("telegram.send_failed")
