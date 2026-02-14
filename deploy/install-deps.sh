#!/usr/bin/env bash
# Install Python dependencies in a venv. Run on the VPS after copying code.
# Usage: sudo bash /opt/polymarket-bot/deploy/install-deps.sh

set -euo pipefail

APP_DIR="/opt/polymarket-bot"
APP_USER="botuser"

echo "=== Installing Python dependencies ==="

# Create venv
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/.venv"

# Install dependencies
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

echo "=== Dependencies installed ==="
echo "Start the bot: sudo systemctl start polymarket-bot"
echo "View logs:     sudo journalctl -u polymarket-bot -f"
