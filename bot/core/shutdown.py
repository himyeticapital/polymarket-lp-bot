"""Graceful shutdown handler — cancel orders, close connections."""

from __future__ import annotations

import asyncio
import signal
from typing import Callable

import structlog

logger = structlog.get_logger()


class ShutdownHandler:
    """Registers SIGINT/SIGTERM handlers and coordinates graceful shutdown."""

    def __init__(self) -> None:
        self._shutdown_event = asyncio.Event()
        self._callbacks: list[Callable] = []

    def register(self) -> None:
        """Register signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_signal, sig)
        logger.info("Shutdown handler registered")

    def _handle_signal(self, sig: signal.Signals) -> None:
        logger.info("Shutdown signal received", signal=sig.name)
        self._shutdown_event.set()

    def add_callback(self, callback: Callable) -> None:
        """Add an async cleanup callback to run on shutdown."""
        self._callbacks.append(callback)

    async def wait(self) -> None:
        """Wait until a shutdown signal is received."""
        await self._shutdown_event.wait()

    async def execute(self) -> None:
        """Run all registered cleanup callbacks."""
        logger.info("Executing shutdown callbacks", count=len(self._callbacks))
        for cb in self._callbacks:
            try:
                result = cb()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("Shutdown callback failed", error=str(e))

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_event.is_set()
