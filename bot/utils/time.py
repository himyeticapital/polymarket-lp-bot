"""UTC time helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


def utc_today_str() -> str:
    """Get today's date as 'YYYY-MM-DD' in UTC."""
    return utc_now().strftime("%Y-%m-%d")


def utc_iso(dt: datetime | None = None) -> str:
    """Format datetime as ISO 8601 UTC string."""
    if dt is None:
        dt = utc_now()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def timestamp_short() -> str:
    """Short timestamp for activity log: HH:MM:SS."""
    return utc_now().strftime("%H:%M:%S")
