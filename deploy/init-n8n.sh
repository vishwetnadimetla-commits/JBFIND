#!/usr/bin/env bash
set -euo pipefail

# init-n8n.sh — run AFTER docker-compose up on the VM.
# Imports workflows, sets up credentials, activates schedules.

N8N_URL="${N8N_URL:-http://localhost:5678}"
N8N_API_KEY="${N8N_API_KEY:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[init] Waiting for n8n to be ready..."
for i in $(seq 1 30); do
  if curl -sS -o /dev/null -w '%{http_code}' "$N8N_URL/rest/workflows" 2>/dev/null | grep -q 200; then
    echo "[init] n8n ready."
    break
  fi
  sleep 2
done

echo "[init] Importing test workflow..."
curl -sS -X POST "$N8N_URL/rest/workflows" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -d @"$SCRIPT_DIR/jbfind-test-workflow.json" || echo "[init] Test workflow import done (may be duplicate)"

echo "[init] Importing daily workflow..."
curl -sS -X POST "$N8N_URL/rest/workflows" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -d @"$SCRIPT_DIR/jbfind-daily-workflow.json" || echo "[init] Daily workflow import done (may be duplicate)"

echo "[init] Done. Open $N8N_URL to configure credentials."
echo "[init] You need to create these credential types:"
echo "  - Header Auth: EXPlabs API key"
echo "  - OAuth2: Google Sheets/Drive"
echo "  - Telegram Bot: both bot tokens"
echo "  - HTTP Request: Apify token"