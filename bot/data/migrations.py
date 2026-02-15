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


MIGRATION_LP_FLIP_SQL = """
-- Add lp_flip_volume column to daily_volume (idempotent via IF NOT EXISTS workaround)
ALTER TABLE daily_volume ADD COLUMN lp_flip_volume REAL NOT NULL DEFAULT 0;

-- Flip cycle tracking for LP Flip (Strategy 2)
CREATE TABLE IF NOT EXISTS flip_cycles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id    TEXT NOT NULL,
    market_question TEXT DEFAULT '',
    entry_side      TEXT NOT NULL,
    entry_token_id  TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    entry_shares    REAL NOT NULL,
    entry_order_id  TEXT,
    entry_filled_at TEXT,
    exit_side       TEXT,
    exit_token_id   TEXT,
    exit_price      REAL,
    exit_shares     REAL,
    exit_order_id   TEXT,
    exit_filled_at  TEXT,
    profit          REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_flip_cycles_condition ON flip_cycles(condition_id);
CREATE INDEX IF NOT EXISTS idx_flip_cycles_status ON flip_cycles(status);
"""


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Create all tables if they don't exist, then run incremental migrations."""
    await db.executescript(SCHEMA_SQL)

    # Incremental migration: add lp_flip_volume column + flip_cycles table
    # ALTER TABLE ADD COLUMN is not idempotent, so check first
    cursor = await db.execute("PRAGMA table_info(daily_volume)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "lp_flip_volume" not in columns:
        await db.execute(
            "ALTER TABLE daily_volume ADD COLUMN lp_flip_volume REAL NOT NULL DEFAULT 0"
        )

    # flip_cycles table (CREATE IF NOT EXISTS is idempotent)
    await db.executescript("""
CREATE TABLE IF NOT EXISTS flip_cycles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id    TEXT NOT NULL,
    market_question TEXT DEFAULT '',
    entry_side      TEXT NOT NULL,
    entry_token_id  TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    entry_shares    REAL NOT NULL,
    entry_order_id  TEXT,
    entry_filled_at TEXT,
    exit_side       TEXT,
    exit_token_id   TEXT,
    exit_price      REAL,
    exit_shares     REAL,
    exit_order_id   TEXT,
    exit_filled_at  TEXT,
    profit          REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_flip_cycles_condition ON flip_cycles(condition_id);
CREATE INDEX IF NOT EXISTS idx_flip_cycles_status ON flip_cycles(status);
""")

    await db.commit()
