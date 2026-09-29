#!/usr/bin/env bash
# Watchdog for the JBFind pipeline. Pages you ONCE when the pipeline has been
# dead for a while, and pages again only if it is still dead much later.
#
# The Telegram alert only fires when a run *errors*. A pipeline that stops
# running entirely -- disabled workflow, crashed container, dead cron -- is
# silent, and that is the failure that actually loses jobs. This closes that
# gap.
#
# crontab (every 15 min):
#   */15 * * * * /home/ubuntu/jbfind/deploy/healthcheck.sh >> /var/log/jbfind-health.log 2>&1
set -uo pipefail

STATE_DIR="${JBFIND_STATE_DIR:-/var/lib/jbfind}"
STATE_FILE="$STATE_DIR/healthcheck.state"
mkdir -p "$STATE_DIR" 2>/dev/null || STATE_FILE="/tmp/jbfind-healthcheck.state"

# A run must have finished more recently than this, or the pipeline is dead.
MAX_AGE_MIN="${JBFIND_MAX_AGE_MIN:-75}"
PAGES="${JBFIND_TELEGRAM_CHAT_ID:-8511150256}"
TG_TOKEN_FILE="${JBFIND_TELEGRAM_TOKEN_FILE:-/home/ubuntu/.jbfind/secrets/telegram.env}"

# Cooldown: do not re-page for the same problem within this window.
COOLDOWN_MIN="${JBFIND_ALERT_COOLDOWN_MIN:-240}"

now=$(date +%s)

# Newest daily execution start time, straight from the n8n database.
# SQLite lives in the n8n volume; postgres is the default but sqlite is used
# in some installs, so try the API-free DB path first and fall back.
newest_epoch() {
  local db
  for db in /home/node/.n8n/database.sqlite /opt/n8n/database.sqlite; do
    [ -f "$db" ] || continue
    local t
    t=$(docker run --rm -v "$(dirname "$db"):/d:ro" keinos/sqlite3 \
          "SELECT MAX(startedAt) FROM execution_entity WHERE workflowId='jbfind-daily-workflow';" \
          2>/dev/null | tr -d '[:space:]')
    case "$t" in
      ''|*[!0-9]*) continue ;;
      *) date -d "@$((t/1000))" +%s 2>/dev/null && return 0 ;;
    esac
  done
  # Postgres fallback.
  docker exec jbfind-postgres sh -lc \
    "psql -U n8n -d n8n -tAc \"SELECT EXTRACT(EPOCH FROM MAX(\\\"startedAt\\\")) FROM execution_entity WHERE \\\"workflowId\\\"='jbfind-daily-workflow';\"" \
    2>/dev/null | tr -d '[:space:]'
}

page() {
  local msg="$1"
  [ -f "$TG_TOKEN_FILE" ] || return 0
  # shellcheck disable=SC1090
  . "$TG_TOKEN_FILE" 2>/dev/null
  local tok="${TELEGRAM_BOT_TOKEN:-${TELEGRAM_TOKEN:-}}"
  [ -n "$tok" ] || return 0
  curl -fsS --max-time 20 -X POST "https://api.telegram.org/bot${tok}/sendMessage" \
    --data-urlencode "chat_id=${PAGES}" \
    --data-urlencode "text=${msg}" >/dev/null 2>&1
}

last=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
started=$(newest_epoch)

if [ -z "$started" ] || [ "$started" -eq 0 ] 2>/dev/null; then
  body="JBFind watchdog: cannot read the last daily run (is n8n up?)"
  if [ $(( (now-last) / 60 )) -ge "$COOLDOWN_MIN" ]; then
    page "⚠️ ${body}"
    echo "$now" > "$STATE_FILE"
  fi
  echo "$(date -Is) $body"
  exit 0
fi

age_min=$(( (now - started) / 60 ))
if [ "$age_min" -le "$MAX_AGE_MIN" ]; then
  # healthy: clear the alert state so the next outage pages immediately
  echo 0 > "$STATE_FILE"
  echo "$(date -Is) ok: last run ${age_min}m ago"
  exit 0
fi

if [ $(( (now-last) / 60 )) -ge "$COOLDOWN_MIN" ]; then
  page "⚠️ JBFind pipeline STALE
No daily run for ${age_min} minutes (limit ${MAX_AGE_MIN}).
Last run started $(date -r "$started" -Is 2>/dev/null || echo unknown).
Expected: every 30 min. Check: docker compose -f deploy/docker-compose.yml ps"
  echo "$now" > "$STATE_FILE"
  echo "$(date -Is) paged: stale ${age_min}m"
else
  echo "$(date -Is) stale ${age_min}m (alert in cooldown)"
fi
