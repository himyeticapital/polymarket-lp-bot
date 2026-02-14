#!/usr/bin/env bash
# Setup script for Ubuntu VPS (Oracle Cloud Free Tier / any Ubuntu 22.04+)
# Run as root or with sudo: sudo bash setup-server.sh

set -euo pipefail

APP_USER="botuser"
APP_DIR="/opt/polymarket-bot"

echo "=== Polymarket Bot — Server Setup ==="

# 1. System packages
echo "[1/5] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3.11 python3.11-venv python3.11-dev git curl 2>/dev/null || {
    # If python3.11 not in default repos, add deadsnakes
    apt-get install -y -qq software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y -qq python3.11 python3.11-venv python3.11-dev
}

# 2. Create app user (no login shell)
echo "[2/5] Creating app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -s /usr/sbin/nologin "$APP_USER"
fi

# 3. Create app directory
echo "[3/5] Setting up app directory..."
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

# 4. Install systemd service
echo "[4/5] Installing systemd service..."
cat > /etc/systemd/system/polymarket-bot.service << 'UNIT'
[Unit]
Description=Polymarket LP Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
Group=botuser
WorkingDirectory=/opt/polymarket-bot
ExecStart=/opt/polymarket-bot/.venv/bin/python -m bot.main
Restart=always
RestartSec=30
StartLimitIntervalSec=300
StartLimitBurst=5

# Environment
EnvironmentFile=/opt/polymarket-bot/.env

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/polymarket-bot
PrivateTmp=true

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=polymarket-bot

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable polymarket-bot

# 5. Firewall (allow SSH only)
echo "[5/5] Configuring firewall..."
if command -v ufw &>/dev/null; then
    ufw allow 22/tcp
    ufw --force enable
fi

echo ""
echo "=== Server setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy your bot code:  scp -r ./* root@YOUR_IP:/opt/polymarket-bot/"
echo "  2. Copy your .env:      scp .env root@YOUR_IP:/opt/polymarket-bot/.env"
echo "  3. SSH in and run:      sudo bash /opt/polymarket-bot/deploy/install-deps.sh"
echo "  4. Start the bot:       sudo systemctl start polymarket-bot"
echo "  5. Check logs:          sudo journalctl -u polymarket-bot -f"
