"""Runtime log scrubber — redacts secrets from structlog output.

Usage:
    import structlog
    from bot.security.scrubber import SecretScrubber

    structlog.configure(processors=[..., SecretScrubber(), ...])
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that match secret-like values
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Ethereum private keys (0x + 64 hex chars)
    ("private_key", re.compile(r"0x[0-9a-fA-F]{64}")),
    # API keys (common formats)
    ("api_key", re.compile(r"sk_[a-zA-Z0-9]{20,}")),
    ("api_key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    # Telegram bot tokens
    ("bot_token", re.compile(r"\d{8,}:[A-Za-z0-9_-]{30,}")),
    # Generic long hex strings (likely keys/hashes — 40+ chars)
    ("hex_secret", re.compile(r"(?<![a-fA-F0-9])[0-9a-fA-F]{40,}(?![a-fA-F0-9])")),
]

# Keys in log event dicts that should always be redacted
SENSITIVE_FIELD_NAMES = frozenset({
    "private_key", "secret", "password", "token", "api_key",
    "secret_key", "auth", "authorization", "credential",
    "synth_api_key", "telegram_bot_token",
})

REDACTED = "***REDACTED***"


class SecretScrubber:
    """structlog processor that redacts secrets from log events."""

    def __call__(self, logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        return _scrub_dict(event_dict)


def _scrub_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively scrub sensitive values from a dict."""
    scrubbed = {}
    for key, value in d.items():
        lower_key = key.lower()
        if any(s in lower_key for s in SENSITIVE_FIELD_NAMES):
            scrubbed[key] = REDACTED
        elif isinstance(value, str):
            scrubbed[key] = _scrub_string(value)
        elif isinstance(value, dict):
            scrubbed[key] = _scrub_dict(value)
        elif isinstance(value, (list, tuple)):
            scrubbed[key] = type(value)(_scrub_value(v) for v in value)
        else:
            scrubbed[key] = value
    return scrubbed


def _scrub_value(value: Any) -> Any:
    """Scrub a single value."""
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return _scrub_dict(value)
    return value


def _scrub_string(s: str) -> str:
    """Replace any secret patterns found in a string."""
    result = s
    for _name, pattern in SECRET_PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result
