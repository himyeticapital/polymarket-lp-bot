"""Entry point: python -m bot"""

from __future__ import annotations

import asyncio
import logging
import sys

import structlog


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    from bot.security.scrubber import SecretScrubber

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            SecretScrubber(),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )


async def main() -> None:
    from bot.config import BotConfig
    from bot.core.engine import Engine

    config = BotConfig()  # type: ignore[call-arg]
    setup_logging(config.log_level)

    log = structlog.get_logger()
    log.info(
        "Starting Polymarket Bot",
        dry_run=config.dry_run,
        balance=config.starting_balance_usd,
        max_drawdown=config.max_drawdown_usd,
    )

    engine = Engine(config)
    try:
        await engine.start()
        await engine.run()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
    finally:
        await engine.shutdown()
        log.info("Bot shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
