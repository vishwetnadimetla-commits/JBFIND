#!/usr/bin/env bash
set -euo pipefail

# inject-credentials.sh — import n8n credentials + workflows via REST API
# Run after docker-compose up. Source the .env first.

N8N_URL="${N8N_URL:-http://localhost:5678}"
N8N_API_KEY="${N8N_API_KEY:-}"
SECRETS_DIR="$HOME/.jbfind/secrets"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$SECRETS_DIR/telegram.env" ] || [ ! -f "$SECRETS_DIR/google-service-account.json" ]; then
  echo "[inject] ERROR: secrets missing in $SECRETS_DIR"
  exit 1
fi

source "$SECRETS_DIR/telegram.env"
GOOGLE_SA=$(cat "$SECRETS_DIR/google-service-account.json" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))")

echo "[inject] Waiting for n8n..."
for i in $(seq 1 30); do
  if curl -sS -o /dev/null -w '%{http_code}' "$N8N_URL/rest/workflows" 2>/dev/null | grep -q 200; then
    echo "[inject] n8n ready."
    break
  fi
  sleep 2
done

echo "[inject] Creating credential: Experiential Labs API..."
curl -sS -X POST "$N8N_URL/rest/credentials" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -d '{
    "name": "Experiential Labs",
    "type": "httpHeaderAuth",
    "data": {
      "name": "Authorization",
      "value": "Bearer '"$EXPLABS_API_KEY"'"
    }
  }'

echo ""
echo "[inject] Creating credential: Telegram Reporter..."
curl -sS -X POST "$N8N_URL/rest/credentials" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -d '{
    "name": "JBFind Reporter Bot",
    "type": "telegramApi",
    "data": {
      "accessToken": "'"$JB_FINDER_REPORTER_TOKEN"'"
    }
  }'

echo ""
echo "[inject] Creating credential: Telegram Tracker..."
curl -sS -X POST "$N8N_URL/rest/credentials" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -d '{
    "name": "JBFind Tracker Bot",
    "type": "telegramApi",
    "data": {
      "accessToken": "'"$JB_FINDER_TRACKER_TOKEN"'"
    }
  }'

echo ""
echo "[inject] Creating credential: Google Service Account..."
curl -sS -X POST "$N8N_URL/rest/credentials" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -d '{
    "name": "Google Service Account",
    "type": "googleServiceAccount",
    "data": {
      "serviceAccountKey": '$GOOGLE_SA'
    }
  }'

echo ""
echo "[inject] Importing test workflow..."
curl -sS -X POST "$N8N_URL/rest/workflows" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -d @"$SCRIPT_DIR/jbfind-test-workflow.json"

echo ""
echo "[inject] Importing daily workflow..."
curl -sS -X POST "$N8N_URL/rest/workflows" \
  -H "Content-Type: application/json" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -d @"$SCRIPT_DIR/jbfind-daily-workflow.json"

echo ""
echo "[inject] Done. Open $N8N_URL to activate workflows."