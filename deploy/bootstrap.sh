#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)

# bootstrap.sh — one-shot provision for Oracle ARM VM
# Run once after SSH-ing into the fresh Ubuntu 24.04 LTS VM.
# Usage: chmod +x bootstrap.sh && sudo ./bootstrap.sh

echo "[bootstrap] Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

echo "[bootstrap] Installing Docker + Compose plugin..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "[bootstrap] Enabling Docker on boot..."
systemctl enable docker
systemctl start docker

echo "[bootstrap] Creating n8n directory..."
mkdir -p /opt/jbfind

echo "[bootstrap] Copying deployment files..."
rm -rf /opt/jbfind/deploy
cp -R "$PROJECT_DIR/deploy" /opt/jbfind/deploy
cp "$PROJECT_DIR/dashboard.html" /opt/jbfind/dashboard.html

echo "[bootstrap] Creating .env (override before first start)..."
if [ ! -f /opt/jbfind/.env ]; then
  cat > /opt/jbfind/.env <<'ENVEOF'
N8N_DB_PASSWORD=$(openssl rand -base64 24)
HEALTHCHECKS_UUID=replace-me-with-your-healthchecks-uuid
ENVEOF
fi

echo "[bootstrap] Starting stack..."
cd /opt/jbfind/deploy && docker compose --env-file /opt/jbfind/.env up -d

echo "[bootstrap] Done. n8n should be available at http://$(hostname -I | awk '{print $1}'):5678"
echo "[bootstrap] Run: docker compose logs -f to watch startup."
