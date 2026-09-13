#!/usr/bin/env bash
set -euo pipefail

# run.sh — trigger an n8n workflow by name and block until it finishes.
# Usage: ./run.sh <workflow_name>
# Requires: N8N_API_KEY in environment, n8n at http://localhost:5678

N8N_URL="${N8N_URL:-http://localhost:5678}"
WORKFLOW_NAME="${1:-test-workflow}"

echo "[run] Looking up workflow ID for: $WORKFLOW_NAME"
WORKFLOW_ID=$(curl -sS -H "X-N8N-API-KEY: $N8N_API_KEY" "$N8N_URL/rest/workflows" | \
  python3 -c "import sys,json; wfs=json.load(sys.stdin).get('data',[]); [print(w['id']) for w in wfs if w['name']=='$WORKFLOW_NAME']")

if [ -z "$WORKFLOW_ID" ]; then
  echo "[run] ERROR: Workflow '$WORKFLOW_NAME' not found."
  exit 1
fi

echo "[run] Triggering $WORKFLOW_NAME (id=$WORKFLOW_ID)..."
EXEC_ID=$(curl -sS -X POST -H "X-N8N-API-KEY: $N8N_API_KEY" \
  "$N8N_URL/rest/workflows/$WORKFLOW_ID/execute" | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('executionId',''))")

if [ -z "$EXEC_ID" ]; then
  echo "[run] ERROR: Failed to trigger execution."
  exit 1
fi

echo "[run] Execution $EXEC_ID started. Waiting for completion..."

for i in $(seq 1 120); do
  sleep 2
  STATUS=$(curl -sS -H "X-N8N-API-KEY: $N8N_API_KEY" \
    "$N8N_URL/rest/executions/$EXEC_ID" | \
    python3 -c "import sys,json; d=json.load(sys.stdin).get('data',{}); print(d.get('status','unknown'))")
  if [ "$STATUS" = "success" ]; then
    echo "[run] Completed successfully."
    break
  elif [ "$STATUS" = "error" ]; then
    echo "[run] Completed with ERROR."
    break
  fi
done

echo "[run] Result: $STATUS"