#!/bin/bash
# ──────────────────────────────────────────────────
# Deploy car-metrics to Pi Zero via rsync over SSH
# Usage: ./deploy.sh [pi-hostname-or-ip]
# ──────────────────────────────────────────────────
set -e

PI_HOST="${1:-192.168.1.10}"
PI_USER="dietpi"
PI_PATH="/home/dietpi/car-metrics"

echo "🚗 Deploying car-metrics to ${PI_USER}@${PI_HOST}:${PI_PATH}"

# Sync files (excludes data, pycache, git)
rsync -avz --progress \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude 'car-metrics-data' \
    --exclude '.DS_Store' \
    "$(dirname "$0")/" \
    "${PI_USER}@${PI_HOST}:${PI_PATH}/"

echo ""
echo "✅ Deployed! Next steps:"
echo "   ssh ${PI_USER}@${PI_HOST}"
echo ""
echo "   # First time? Run setup:"
echo "   cd ${PI_PATH} && chmod +x install.sh && ./install.sh"
echo ""
echo "   # Restart data collection after code update:"
echo "   sudo systemctl restart car-metrics"
echo ""
echo "   # Web dashboard (manual start/stop — NOT auto-started):"
echo "   sudo systemctl start car-metrics-web   # start"
echo "   sudo systemctl stop car-metrics-web    # stop when done"
echo ""
echo "   # View logs:"
echo "   journalctl -u car-metrics -f"
echo "   journalctl -u car-metrics-web -f"
