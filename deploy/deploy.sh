#!/usr/bin/env bash
# Deploy bot to VPS from your local machine.
# Usage: bash deploy/deploy.sh <VPS_IP> [SSH_KEY_PATH]
#
# Example: bash deploy/deploy.sh YOUR_VPS_IP
#          bash deploy/deploy.sh YOUR_VPS_IP ~/.ssh/oracle_key

set -euo pipefail

VPS_IP="${1:?Usage: deploy.sh <VPS_IP> [SSH_KEY_PATH]}"
SSH_KEY="${2:-}"
APP_DIR="/opt/polymarket-bot"

# Build SSH command
SSH_CMD="ssh"
SCP_CMD="scp"
if [ -n "$SSH_KEY" ]; then
    SSH_CMD="ssh -i $SSH_KEY"
    SCP_CMD="scp -i $SSH_KEY"
fi

REMOTE="ubuntu@$VPS_IP"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Deploying Polymarket Bot to $VPS_IP ==="

# 1. Sync code (exclude secrets, venv, db, etc.)
echo "[1/4] Syncing code..."
rsync -avz --delete \
    ${SSH_KEY:+-e "ssh -i $SSH_KEY"} \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='*.db' \
    --exclude='*.db-journal' \
    --exclude='.venv/' \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='.git/' \
    --exclude='.config/' \
    --exclude='node_modules/' \
    --exclude='.DS_Store' \
    --exclude='nohup.out' \
    "$PROJECT_DIR/" "$REMOTE:$APP_DIR/"

# 2. Copy .env if it doesn't exist on server yet
echo "[2/4] Checking .env..."
if ! $SSH_CMD "$REMOTE" "test -f $APP_DIR/.env" 2>/dev/null; then
    echo "  Copying .env to server (first deploy)..."
    $SCP_CMD "$PROJECT_DIR/.env" "$REMOTE:$APP_DIR/.env"
    $SSH_CMD "$REMOTE" "chmod 600 $APP_DIR/.env"
else
    echo "  .env already exists on server (skipping — edit manually if needed)"
fi

# 3. Fix ownership
echo "[3/4] Fixing permissions..."
$SSH_CMD "$REMOTE" "sudo chown -R botuser:botuser $APP_DIR && sudo chmod 600 $APP_DIR/.env"

# 4. Install deps + restart
echo "[4/4] Installing dependencies and restarting..."
$SSH_CMD "$REMOTE" "sudo bash $APP_DIR/deploy/install-deps.sh && sudo systemctl restart polymarket-bot"

echo ""
echo "=== Deploy complete ==="
echo "View logs: $SSH_CMD $REMOTE 'sudo journalctl -u polymarket-bot -f'"
