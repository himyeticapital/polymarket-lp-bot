"""Database schema creation and migrations."""

from __future__ import annotations

import aiosqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    strategy        TEXT NOT NULL,
    condition_id    TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    market_question TEXT DEFAULT '',
    side            TEXT NOT NULL,
    price           REAL NOT NULL,
    size            REAL NOT NULL,
    order_type      TEXT NOT NULL DEFAULT 'GTC',
    order_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    fill_price      REAL,
    fill_size       REAL,
    fee_paid        REAL DEFAULT 0,
    edge            REAL,
    reason          TEXT,
    is_dry_run      INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_condition ON trades(condition_id);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);

CREATE TABLE IF NOT EXISTS daily_volume (
    date            TEXT PRIMARY KEY,
    total_volume    REAL NOT NULL DEFAULT 0,
    arb_volume      REAL NOT NULL DEFAULT 0,
    lp_volume       REAL NOT NULL DEFAULT 0,
    copy_volume     REAL NOT NULL DEFAULT 0,
    synth_volume    REAL NOT NULL DEFAULT 0,
    trade_count     INTEGER NOT NULL DEFAULT 0,
    pnl             REAL NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id    TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    size            REAL NOT NULL,
    avg_entry_price REAL NOT NULL,
    current_price   REAL DEFAULT 0,
    unrealized_pnl  REAL DEFAULT 0,
    strategy        TEXT NOT NULL,
    opened_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at       TEXT,
    realized_pnl    REAL,
    UNIQUE(condition_id, token_id, strategy)
);

CREATE TABLE IF NOT EXISTS lp_rewards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    condition_id    TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    q_score         REAL,
    estimated_reward REAL,
    actual_reward   REAL,
    spread_from_mid REAL,
    order_size      REAL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS copy_targets (
    address         TEXT PRIMARY KEY,
    alias           TEXT,
    last_snapshot   TEXT,
    last_checked_at TEXT,
    total_copied    REAL DEFAULT 0,
    win_rate        REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS synth_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    asset           TEXT NOT NULL,
    synth_prob_up   REAL NOT NULL,
    poly_prob_up    REAL NOT NULL,
    edge            REAL NOT NULL,
    action_taken    TEXT,
    kelly_size      REAL,
    outcome         TEXT DEFAULT 'pending',
    pnl             REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_synth_asset ON synth_signals(asset);

CREATE TABLE IF NOT EXISTS bot_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Create all tables if they don't exist."""
    await db.executescript(SCHEMA_SQL)
    await db.commit()
