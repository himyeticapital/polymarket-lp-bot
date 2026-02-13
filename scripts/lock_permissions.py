#!/usr/bin/env python3
"""Lock down file permissions on sensitive files.

Usage:
    python scripts/lock_permissions.py

Sets:
  - .env, .env.gpg, *.pem, *.key → 600 (owner read/write only)
  - bot_data.db → 600 (contains trade history)
  - scripts/ → 700 (owner execute only)
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


# Files that should be owner-only (rw-------)
SENSITIVE_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "bot_data.db",
    "credentials.json",
]

# Directories that should be owner-only (rwx------)
SENSITIVE_DIRS = [
    ".git",
]


def lock_file(path: Path, mode: int = 0o600) -> None:
    """Set file permissions to owner-only."""
    try:
        os.chmod(path, mode)
        perms = stat.filemode(mode)
        print(f"  {perms}  {path}")
    except OSError as e:
        print(f"  FAIL     {path} — {e}")


def main() -> None:
    print("=== File Permission Lockdown ===\n")

    root = Path(".")
    locked = 0

    # Lock sensitive files
    for pattern in SENSITIVE_PATTERNS:
        for path in root.glob(pattern):
            if path.is_file():
                lock_file(path, 0o600)
                locked += 1

    # Lock sensitive directories
    for dirname in SENSITIVE_DIRS:
        dirpath = root / dirname
        if dirpath.is_dir():
            lock_file(dirpath, 0o700)
            locked += 1

    # Lock scripts to owner-executable only
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for script in scripts_dir.glob("*.py"):
            lock_file(script, 0o700)
            locked += 1

    # Check for world-readable .env
    env_path = root / ".env"
    if env_path.exists():
        mode = env_path.stat().st_mode
        if mode & 0o077:
            print(f"\n  WARNING: .env was world-readable! Now locked down.")

    print(f"\n{locked} file(s) locked down.")


if __name__ == "__main__":
    main()
