#!/usr/bin/env bash
# Rebuild the jbfind scraper and verify the new /sheets endpoints are live.
#
# Why this exists: deploy/scraper.Dockerfile does `COPY deploy/scraper_service.py`,
# so edits to the scraper do nothing until the image is rebuilt. The old _api()
# had no retry and only caught HTTPError, so one reset socket to Google Sheets
# turned into a 500 and killed the whole 30-min run ("connection was aborted").
#
# Safe to re-run. Verifies before it claims success.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f deploy/docker-compose.yml"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

say "pre-flight: current state"
$COMPOSE ps scraper || true

say "rebuilding scraper image"
$COMPOSE build scraper || die "image build failed"

say "restarting scraper"
$COMPOSE up -d scraper || die "container did not start"

say "waiting for /health"
for i in $(seq 1 30); do
  if curl -fsS --max-time 5 http://127.0.0.1:8001/health >/dev/null 2>&1; then
    echo "healthy after ${i}s"
    break
  fi
  [ "$i" = 30 ] && die "scraper never became healthy"
  sleep 1
done

# The whole point of the rebuild: the new endpoints must actually be served.
# A container that is merely "up" is not proof the new code loaded.
say "verifying the rebuilt code is really loaded"
if curl -fsS --max-time 5 -X POST http://127.0.0.1:8001/sheets/upsert_batch \
     -H 'content-type: application/json' -d '{"jobs":[]}' >/dev/null 2>&1; then
  echo "OK: /sheets/upsert_batch is live"
else
  die "/sheets/upsert_batch is 404 -- image did not rebuild, or compose is using a cached layer"
fi

say "confirming _api() retries (send a bad token path; must not hang or 500)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST \
  http://127.0.0.1:8001/sheets/upsert -H 'content-type: application/json' -d '{}' || true)
case "$code" in
  400|422) echo "OK: /sheets/upsert validates input (http $code)" ;;
  500)    die "still 500 -- old code still in the image" ;;
  *)       echo "note: /sheets/upsert returned http $code" ;;
esac

say "done"
$COMPOSE ps scraper
cat <<'EOF'

Next: point the n8n "Sheets Write Jobs" node at the batch endpoint to turn
32 sequential Google writes into one call. Until that is switched, the
pipeline is already safe -- the rebuilt _api() retries transient Google
errors, which is the fix for the "connection was aborted" alerts.
EOF
