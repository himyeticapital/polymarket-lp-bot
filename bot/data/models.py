"""Database CRUD helpers for trade logging, volume tracking, and state."""

from __future__ import annotations

from bot.constants import Strategy
from bot.data.database import Database
from bot.types import OrderResult, SynthForecast
from bot.utils.time import utc_iso, utc_today_str


async def insert_trade(db: Database, result: OrderResult) -> int:
    """Insert a trade record and return its ID."""
    cursor = await db.execute(
        """INSERT INTO trades
           (strategy, condition_id, token_id, market_question, side, price, size,
            order_type, order_id, status, fill_price, fill_size, fee_paid,
            edge, reason, is_dry_run)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            result.signal.strategy.value,
            result.signal.condition_id,
            result.signal.token_id,
            result.signal.market_question,
            result.signal.side.value,
            result.signal.price,
            result.signal.size,
            result.signal.order_type.value,
            result.order_id,
            "filled" if result.success else "rejected",
            result.fill_price,
            result.fill_size,
            result.fee_paid,
            result.signal.edge,
            result.signal.reason,
            1 if result.is_dry_run else 0,
        ),
    )
    return cursor.lastrowid or 0


async def update_daily_volume(
    db: Database, strategy: Strategy, volume: float, pnl: float = 0.0
) -> None:
    """Upsert today's volume tracking."""
    today = utc_today_str()
    col = {
        Strategy.ARBITRAGE: "arb_volume",
        Strategy.LIQUIDITY: "lp_volume",
        Strategy.COPY_TRADING: "copy_volume",
        Strategy.SYNTH_EDGE: "synth_volume",
    }[strategy]

    await db.execute(
        f"""INSERT INTO daily_volume (date, total_volume, {col}, trade_count, pnl)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_volume = total_volume + ?,
                {col} = {col} + ?,
                trade_count = trade_count + 1,
                pnl = pnl + ?,
                updated_at = ?""",
        (today, volume, volume, pnl, volume, volume, pnl, utc_iso()),
    )


async def get_today_volume(db: Database) -> dict | None:
    """Get today's volume summary."""
    return await db.fetch_one(
        "SELECT * FROM daily_volume WHERE date = ?", (utc_today_str(),)
    )


async def get_trade_stats(db: Database) -> dict:
    """Get overall trade statistics."""
    row = await db.fetch_one(
        """SELECT
               COUNT(*) as total_trades,
               SUM(CASE WHEN status = 'filled' AND fill_price > price AND side = 'BUY' THEN 1
                        WHEN status = 'filled' AND fill_price < price AND side = 'SELL' THEN 1
                        ELSE 0 END) as wins,
               SUM(CASE WHEN status = 'filled' THEN (fill_size * fill_price) - (size * price) ELSE 0 END) as total_pnl,
               AVG(size * price) as avg_bet,
               MAX(CASE WHEN status = 'filled' THEN (fill_size * fill_price) - (size * price) END) as best_trade,
               MIN(CASE WHEN status = 'filled' THEN (fill_size * fill_price) - (size * price) END) as worst_trade
           FROM trades WHERE status = 'filled'"""
    )
    return row or {
        "total_trades": 0, "wins": 0, "total_pnl": 0.0,
        "avg_bet": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
    }


async def get_trade_returns(db: Database, limit: int = 100) -> list[float]:
    """Get recent trade returns for Sharpe ratio calculation."""
    rows = await db.fetch_all(
        """SELECT (fill_size * fill_price) - (size * price) as pnl
           FROM trades WHERE status = 'filled'
           ORDER BY timestamp DESC LIMIT ?""",
        (limit,),
    )
    return [r["pnl"] for r in rows]


async def get_recent_trades(db: Database, limit: int = 50) -> list[dict]:
    """Get recent trades for activity log."""
    return await db.fetch_all(
        "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
    )


async def insert_synth_signal(db: Database, forecast: SynthForecast, action: str, kelly_size: float) -> None:
    """Log a Synth signal evaluation."""
    await db.execute(
        """INSERT INTO synth_signals (asset, synth_prob_up, poly_prob_up, edge, action_taken, kelly_size)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (forecast.asset, forecast.synth_prob_up, forecast.poly_prob_up,
         forecast.edge, action, kelly_size),
    )


async def get_state(db: Database, key: str) -> str | None:
    """Get a bot state value."""
    row = await db.fetch_one("SELECT value FROM bot_state WHERE key = ?", (key,))
    return row["value"] if row else None


async def set_state(db: Database, key: str, value: str) -> None:
    """Set a bot state value."""
    await db.execute(
        """INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?""",
        (key, value, utc_iso(), value, utc_iso()),
    )
