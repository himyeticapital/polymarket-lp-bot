"""Constants for the Polymarket bot."""

from enum import StrEnum

# API Base URLs
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
DATA_HOST = "https://data-api.polymarket.com"
SYNTH_HOST = "https://api.synthdata.co"

# WebSocket URLs
WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

# Polygon Mainnet
CHAIN_ID = 137

# Contract Addresses (Polygon)
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER_ADDRESS = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# Defaults
DEFAULT_DB_PATH = "bot_data.db"
DEFAULT_LOG_LEVEL = "INFO"
MAX_BATCH_ORDERS = 15  # Polymarket batch limit
WS_PING_INTERVAL = 10  # seconds
WS_MAX_INSTRUMENTS = 500  # per connection


class Strategy(StrEnum):
    ARBITRAGE = "arbitrage"
    LIQUIDITY = "liquidity"
    COPY_TRADING = "copy"
    SYNTH_EDGE = "synth_edge"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderType(StrEnum):
    GTC = "GTC"  # Good Till Cancel
    FOK = "FOK"  # Fill or Kill


class EventType(StrEnum):
    """Events published by the engine for dashboard/notifications."""
    TRADE_EXECUTED = "trade_executed"
    EDGE_DETECTED = "edge_detected"
    MARKET_SCANNED = "market_scanned"
    ORDER_RESOLVED = "order_resolved"
    DRAWDOWN_WARNING = "drawdown_warning"
    DRAWDOWN_HALT = "drawdown_halt"
    STRATEGY_ERROR = "strategy_error"
