#!/usr/bin/env python3
"""Store bot secrets in macOS Keychain for secure secret management.

Usage:
    python scripts/setup_keychain.py

This script reads your .env file and stores sensitive keys
(PM_PRIVATE_KEY, PM_SYNTH_API_KEY, PM_TELEGRAM_BOT_TOKEN) into
macOS Keychain under the service "polymarket-bot".

After running, you can delete the sensitive values from .env
and the bot will load them from Keychain automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.security.vault import SENSITIVE_KEYS, store_in_keychain


def load_env_values(env_path: str = ".env") -> dict[str, str]:
    """Parse .env file and return key-value pairs."""
    values: dict[str, str] = {}
    path = Path(env_path)
    if not path.exists():
        return values

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            values[key] = value
    return values


def main() -> None:
    print("=== Polymarket Bot — Keychain Setup ===\n")

    env_values = load_env_values()
    if not env_values:
        print("No .env file found. Create one from .env.example first.")
        sys.exit(1)

    stored = 0
    for key in SENSITIVE_KEYS:
        value = env_values.get(key, "")
        if not value or value.startswith("<") or value == "0x...":
            print(f"  SKIP  {key} — not set or placeholder")
            continue

        if store_in_keychain(key, value):
            print(f"  OK    {key} — stored in Keychain")
            stored += 1
        else:
            print(f"  FAIL  {key} — could not store (are you on macOS?)")

    if stored > 0:
        print(f"\n{stored} secret(s) stored in macOS Keychain.")
        print("\nYou can now remove these values from .env:")
        print("  The bot will load them from Keychain automatically.")
        print("  Keep non-sensitive config values in .env as usual.")
    else:
        print("\nNo secrets were stored. Check your .env file.")


if __name__ == "__main__":
    main()
