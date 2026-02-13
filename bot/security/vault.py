"""Multi-backend secret vault: macOS Keychain > GPG-encrypted .env > plaintext .env.

Usage:
    vault = SecretVault(service="polymarket-bot")
    private_key = vault.get("PM_PRIVATE_KEY")
"""

from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger()

KEYCHAIN_SERVICE = "polymarket-bot"

# Keys that should never appear in plaintext logs or files
SENSITIVE_KEYS = frozenset({
    "PM_PRIVATE_KEY",
    "PM_SYNTH_API_KEY",
    "PM_TELEGRAM_BOT_TOKEN",
})


class SecretVault:
    """Loads secrets with fallback chain: Keychain → GPG .env → plaintext .env."""

    def __init__(
        self,
        service: str = KEYCHAIN_SERVICE,
        env_path: str = ".env",
        gpg_env_path: str = ".env.gpg",
    ) -> None:
        self._service = service
        self._env_path = Path(env_path)
        self._gpg_env_path = Path(gpg_env_path)
        self._cache: dict[str, str] = {}
        self._source: str = "unknown"

    def get(self, key: str) -> str | None:
        """Retrieve a secret by key using the fallback chain."""
        if key in self._cache:
            return self._cache[key]

        # 1. Try macOS Keychain
        value = self._get_from_keychain(key)
        if value:
            self._source = "keychain"
            self._cache[key] = value
            logger.debug("Secret loaded from Keychain", key=key)
            return value

        # 2. Try GPG-encrypted .env
        value = self._get_from_gpg_env(key)
        if value:
            self._source = "gpg"
            self._cache[key] = value
            logger.debug("Secret loaded from GPG .env", key=key)
            return value

        # 3. Fall back to env var / plaintext .env
        value = os.environ.get(key)
        if value:
            self._source = "env"
            self._cache[key] = value
            if key in SENSITIVE_KEYS:
                logger.warning(
                    "Secret loaded from plaintext env — consider using Keychain or GPG",
                    key=key,
                )
            return value

        return None

    @property
    def source(self) -> str:
        """Which backend last served a secret."""
        return self._source

    def _get_from_keychain(self, key: str) -> str | None:
        """Read from macOS Keychain via `security` CLI."""
        try:
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-s", self._service,
                    "-a", key,
                    "-w",  # output password only
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def _get_from_gpg_env(self, key: str) -> str | None:
        """Decrypt .env.gpg and extract a specific key."""
        if not self._gpg_env_path.exists():
            return None
        if not shutil.which("gpg"):
            return None

        try:
            result = subprocess.run(
                ["gpg", "--quiet", "--batch", "--decrypt", str(self._gpg_env_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None

            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k == key:
                        return v
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def clear_cache(self) -> None:
        """Clear cached secrets from memory."""
        self._cache.clear()


def store_in_keychain(key: str, value: str, service: str = KEYCHAIN_SERVICE) -> bool:
    """Store a secret in macOS Keychain."""
    try:
        # Delete existing entry first (ignore errors if it doesn't exist)
        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", key],
            capture_output=True,
            timeout=5,
        )
        # Add new entry
        result = subprocess.run(
            [
                "security", "add-generic-password",
                "-s", service,
                "-a", key,
                "-w", value,
                "-U",  # update if exists
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_env_permissions(env_path: str = ".env") -> dict[str, bool]:
    """Check if .env file has safe permissions (600 or stricter)."""
    path = Path(env_path)
    result: dict[str, bool] = {"exists": False, "permissions_ok": False, "readable_by_others": True}

    if not path.exists():
        return result

    result["exists"] = True
    mode = path.stat().st_mode
    # Check that group and others have no access (octal: 0o077 mask)
    others_can_read = bool(mode & 0o077)
    result["readable_by_others"] = others_can_read
    result["permissions_ok"] = not others_can_read

    return result
