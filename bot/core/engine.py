"""Main engine orchestrator — wires all components together."""

from __future__ import annotations

import asyncio

import structlog

from bot.clients.clob import AsyncClobClient
from bot.clients.data_api import DataApiClient
from bot.clients.gamma import GammaClient
from bot.clients.synth import SynthClient
from bot.clients.websocket_market import MarketWebSocket
from bot.config import BotConfig
from bot.core.scheduler import Scheduler
from bot.core.shutdown import ShutdownHandler
from bot.dashboard.state import DashboardState
from bot.dashboard.web import WebDashboard
from bot.data.database import Database
from bot.execution.order_manager import OrderManager
from bot.notifications.telegram import TelegramNotifier
from bot.risk.inventory import InventoryManager
from bot.risk.manager import RiskManager
from bot.strategies.arbitrage import ArbitrageStrategy
from bot.strategies.copy_trading import CopyTradingStrategy
from bot.strategies.liquidity import LiquidityStrategy
from bot.strategies.liquidity_flip import LiquidityFlipStrategy
from bot.strategies.synth_edge import SynthEdgeStrategy
from bot.types import EventBus

logger = structlog.get_logger()


class Engine:
    """Central orchestrator that initializes and runs all bot components."""

    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._event_bus: EventBus = asyncio.Queue()
        self._state = DashboardState(
            balance=config.starting_balance_usd,
            initial_balance=config.original_deposit_usd,
        )
        self._tasks: list[asyncio.Task] = []
        self._shutdown = ShutdownHandler()

        # Components (initialized in start())
        self._db: Database | None = None
        self._clob: AsyncClobClient | None = None
        self._gamma: GammaClient | None = None
        self._synth: SynthClient | None = None
        self._data_api: DataApiClient | None = None
        self._ws_market: MarketWebSocket | None = None
        self._notifier: TelegramNotifier | None = None
        self._inventory: InventoryManager | None = None
        self._risk: RiskManager | None = None
        self._order_mgr: OrderManager | None = None

    async def start(self) -> None:
        """Initialize all components and connect to services."""
        logger.info("Engine starting...")

        # Database
        self._db = Database(self._config.db_path)
        await self._db.connect()

        # API Clients
        self._clob = AsyncClobClient(self._config)
        self._gamma = GammaClient(self._config)
        self._synth = SynthClient(self._config)
        self._data_api = DataApiClient(self._config)
        self._ws_market = MarketWebSocket(self._config)

        await self._clob.connect()
        await self._gamma.connect()
        if self._config.enable_synth_edge:
            await self._synth.connect()
        await self._data_api.connect()  # Always connect — needed for inventory sync

        # Notifications
        if self._config.telegram_enabled:
            self._notifier = TelegramNotifier(self._config)
            await self._notifier.connect()

        # Risk & Execution
        self._inventory = InventoryManager(self._config, self._db)
        self._risk = RiskManager(
            self._config, self._inventory, self._db, self._event_bus
        )
        self._order_mgr = OrderManager(
            self._config, self._clob, self._risk, self._inventory,
            self._db, self._event_bus,
        )

        # Sync inventory with real API data (balance + existing positions)
        await self._inventory.refresh_from_api(self._clob, self._data_api)
        logger.info(
            "Inventory synced from API",
            balance=round(self._inventory.balance, 2),
            positions=len(self._inventory.positions),
        )
        # Update dashboard state with real balance
        self._state.balance = self._inventory.balance
        self._state.positions_value = self._inventory.get_total_exposure()
        self._state.is_dry_run = self._config.dry_run
        self._state.lp_enabled = self._config.enable_liquidity
        self._state.lp_flip_enabled = self._config.enable_lp_flip
        # Compute portfolio P&L and seed balance history
        portfolio = self._state.balance + self._state.positions_value
        self._state.total_pnl = portfolio - self._state.initial_balance
        self._state.balance_history = [portfolio]

        # Shutdown handler
        self._shutdown.register()
        self._shutdown.add_callback(self.shutdown)

        logger.info(
            "Engine started",
            dry_run=self._config.dry_run,
            strategies={
                "arb": self._config.enable_arbitrage,
                "lp": self._config.enable_liquidity,
                "lp_flip": self._config.enable_lp_flip,
                "copy": self._config.enable_copy_trading,
                "synth": self._config.enable_synth_edge,
            },
        )

    async def run(self) -> None:
        """Run all enabled strategies concurrently."""
        # Strategy tasks
        if self._config.enable_arbitrage:
            strat = ArbitrageStrategy(
                self._config, self._clob, self._gamma,
                self._order_mgr, self._risk, self._db, self._event_bus,
            )
            self._tasks.append(asyncio.create_task(strat.run(), name="arb"))

        if self._config.enable_liquidity:
            strat = LiquidityStrategy(
                self._config, self._clob, self._gamma,
                self._order_mgr, self._risk, self._db, self._event_bus,
                dashboard_state=self._state,
            )
            self._tasks.append(asyncio.create_task(strat.run(), name="lp"))

        if self._config.enable_lp_flip:
            strat = LiquidityFlipStrategy(
                self._config, self._clob, self._gamma,
                self._order_mgr, self._risk, self._db, self._event_bus,
                dashboard_state=self._state,
            )
            self._tasks.append(asyncio.create_task(strat.run(), name="lp_flip"))

        if self._config.enable_copy_trading:
            strat = CopyTradingStrategy(
                self._config, self._data_api, self._order_mgr,
                self._risk, self._db, self._event_bus,
            )
            self._tasks.append(asyncio.create_task(strat.run(), name="copy"))

        if self._config.enable_synth_edge:
            strat = SynthEdgeStrategy(
                self._config, self._synth, self._order_mgr,
                self._risk, self._db, self._event_bus,
            )
            self._tasks.append(asyncio.create_task(strat.run(), name="synth"))

        # Background tasks
        scheduler = Scheduler(
            self._config, self._db, self._state, self._notifier,
            data_api=self._data_api,
        )
        self._tasks.append(asyncio.create_task(scheduler.run_stats_refresh(), name="stats"))
        self._tasks.append(asyncio.create_task(scheduler.run_profile_refresh(), name="profile"))
        self._tasks.append(asyncio.create_task(scheduler.run_health_check(), name="health"))

        # Dashboard (if enabled and not headless)
        if self._config.enable_dashboard:
            from bot.dashboard.app import DashboardApp

            app = DashboardApp(event_bus=self._event_bus, state=self._state)
            self._tasks.append(asyncio.create_task(app.run_async(), name="dashboard"))

        # Web dashboard (browser-based)
        if self._config.enable_web_dashboard:
            from bot.dashboard.state import process_events

            web_dash = WebDashboard(
                state=self._state,
                event_bus=self._event_bus,
                port=self._config.web_dashboard_port,
            )
            self._tasks.append(asyncio.create_task(web_dash.run_forever(), name="web-dashboard"))
            # Process events from bus → dashboard state (only needed when TUI is off)
            if not self._config.enable_dashboard:
                self._tasks.append(asyncio.create_task(
                    process_events(self._state, self._event_bus), name="event-processor"
                ))

        # Wait for shutdown signal or task failure
        shutdown_task = asyncio.create_task(self._shutdown.wait(), name="shutdown-wait")
        self._tasks.append(shutdown_task)

        done, pending = await asyncio.wait(
            self._tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            if task.exception() and task.get_name() != "shutdown-wait":
                logger.error(
                    "Task failed",
                    task=task.get_name(),
                    error=str(task.exception()),
                )

    async def shutdown(self) -> None:
        """Graceful shutdown: cancel orders, close connections."""
        logger.info("Engine shutting down...")

        # Cancel all running tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Cancel all open orders
        if self._order_mgr and not self._config.dry_run:
            try:
                count = await self._order_mgr.cancel_all_orders()
                logger.info("Cancelled open orders", count=count)
            except Exception as e:
                logger.error("Failed to cancel orders", error=str(e))

        # Send shutdown notification
        if self._notifier:
            try:
                await self._notifier.send_message(
                    f"Bot shutdown. Balance: ${self._state.balance:.2f}, "
                    f"P&L: ${self._state.total_pnl:+.2f}"
                )
            except Exception:
                pass

        # Close connections
        if self._ws_market:
            await self._ws_market.disconnect()
        if self._gamma:
            await self._gamma.close()
        if self._synth:
            await self._synth.close()
        if self._data_api:
            await self._data_api.close()
        if self._clob:
            await self._clob.close()
        if self._notifier:
            await self._notifier.close()
        if self._db:
            await self._db.close()

        logger.info("Engine shutdown complete")
