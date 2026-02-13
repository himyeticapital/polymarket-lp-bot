"""Bot configuration using Pydantic Settings — loads from .env file.

Secret loading priority: macOS Keychain > GPG-encrypted .env > plaintext .env.
"""

from __future__ import annotations

import structlog
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings

from bot.constants import (
    CLOB_HOST,
    DATA_HOST,
    DEFAULT_DB_PATH,
    DEFAULT_LOG_LEVEL,
    GAMMA_HOST,
    SYNTH_HOST,
)
from bot.security.vault import SecretVault, check_env_permissions

logger = structlog.get_logger()


def _load_vault_secrets() -> dict[str, str]:
    """Pre-load sensitive keys from Keychain/GPG before Pydantic reads .env."""
    vault = SecretVault()
    overrides: dict[str, str] = {}
    for key in ("PM_PRIVATE_KEY", "PM_SYNTH_API_KEY", "PM_TELEGRAM_BOT_TOKEN"):
        value = vault.get(key)
        if value:
            overrides[key] = value
    return overrides


# Pre-load secrets so they're in env before Pydantic reads
_vault_overrides = _load_vault_secrets()
for _k, _v in _vault_overrides.items():
    import os
    os.environ.setdefault(_k, _v)


class BotConfig(BaseSettings):
    """Central configuration for the Polymarket bot.

    All values can be set via environment variables with PM_ prefix.
    E.g., PM_PRIVATE_KEY, PM_DRY_RUN, etc.

    Secrets are loaded via fallback chain:
    1. macOS Keychain (most secure)
    2. GPG-encrypted .env.gpg
    3. Plaintext .env (warns on startup)
    """

    # === Wallet ===
    private_key: SecretStr = Field(description="Ethereum private key (with 0x prefix)")
    wallet_address: str = Field(default="", description="Public wallet address")
    chain_id: int = 137

    # === API URLs ===
    clob_host: str = CLOB_HOST
    gamma_host: str = GAMMA_HOST
    data_host: str = DATA_HOST
    synth_host: str = SYNTH_HOST
    synth_api_key: SecretStr = Field(default=SecretStr(""), description="Synth API key")

    # === Mode ===
    dry_run: bool = True  # Safe default — always start in dry-run

    # === Strategy Toggles ===
    enable_arbitrage: bool = True
    enable_liquidity: bool = True
    enable_copy_trading: bool = True
    enable_synth_edge: bool = True
    enable_dashboard: bool = True
    enable_web_dashboard: bool = True
    web_dashboard_port: int = 8080

    # === Capital & Risk Limits ===
    starting_balance_usd: float = 500.0
    max_drawdown_usd: float = 250.0  # Hard stop — bot halts all trading
    max_trade_size_usd: float = 25.0  # 5% of capital per trade
    daily_volume_cap_usd: float = 25000.0
    max_open_positions: int = 15
    max_per_market_usd: float = 100.0  # 20% of capital per market
    max_portfolio_exposure_usd: float = 400.0  # 80% of capital

    # === Arbitrage ===
    arb_min_profit_cents: float = 0.5  # Min profit per YES+NO pair (cents)
    arb_scan_interval_sec: float = 15.0

    # === Liquidity Provision ===
    lp_target_spread_pct: float = 0.02  # 2% spread from midpoint
    lp_order_size_usd: float = 25.0
    lp_refresh_interval_sec: float = 60.0
    lp_max_markets: int = 10

    # === Copy Trading ===
    copy_traders: str = ""  # Comma-separated addresses
    copy_scale_factor: float = 0.1  # 10% of copied trader's size
    copy_poll_interval_sec: float = 30.0
    copy_min_trade_usd: float = 10.0
    copy_max_delay_sec: float = 5.0

    # === Synth Edge ===
    synth_edge_threshold: float = 0.05  # 5% edge required
    synth_assets: str = "BTC,ETH"  # Comma-separated asset symbols
    synth_poll_interval_sec: float = 300.0  # 5 minutes
    synth_kelly_fraction: float = 0.25  # Quarter-Kelly for safety

    # === Anti-Detection ===
    timing_jitter_pct: float = 0.15  # +/- 15% timing randomization
    size_jitter_pct: float = 0.10  # +/- 10% size randomization

    # === Telegram ===
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # === Database ===
    db_path: str = DEFAULT_DB_PATH

    # === Logging ===
    log_level: str = DEFAULT_LOG_LEVEL

    model_config = {
        "env_file": ".env",
        "env_prefix": "PM_",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
    }

    @property
    def copy_traders_list(self) -> list[str]:
        """Parsed list of trader addresses to copy."""
        return [x.strip() for x in self.copy_traders.split(",") if x.strip()]

    @property
    def synth_assets_list(self) -> list[str]:
        """Parsed list of Synth assets to track."""
        return [x.strip() for x in self.synth_assets.split(",") if x.strip()]

    @model_validator(mode="after")
    def _warn_env_permissions(self) -> "BotConfig":
        """Warn if .env file has insecure permissions."""
        perms = check_env_permissions()
        if perms["exists"] and perms["readable_by_others"]:
            logger.warning(
                ".env file is readable by other users — run: python scripts/lock_permissions.py",
            )
        return self

    @property
    def drawdown_threshold(self) -> float:
        """Balance level at which the bot halts trading."""
        return self.starting_balance_usd - self.max_drawdown_usd

    @property
    def telegram_enabled(self) -> bool:
        return self.telegram_bot_token is not None and self.telegram_chat_id is not None
