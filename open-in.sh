#!/usr/bin/env bash
# open-in.sh — open URL in a browser, optionally with an attachable endpoint
# Usage: ./open-in.sh [active|default|chrome|firefox|headless|attach-chrome|attach-firefox] [url]

BROWSER="${1:-default}"
URL="${2:-http://localhost:8080}"

case "$BROWSER" in
  active)
    FIREFOX_RUNNING=$(osascript -e 'tell application "System Events" to exists process "Firefox"' 2>/dev/null || true)
    CHROME_RUNNING=$(osascript -e 'tell application "System Events" to exists process "Google Chrome"' 2>/dev/null || true)
    if [ "$FIREFOX_RUNNING" = "true" ]; then
      open -a "Firefox" "$URL"
    elif [ "$CHROME_RUNNING" = "true" ]; then
      open -a "Google Chrome" "$URL"
    else
      echo "[open] Neither Firefox nor Google Chrome is running." >&2
      exit 1
    fi
    ;;
  default)
    echo "[open] Opening in default browser..."
    open "$URL"
    ;;
  chrome|google-chrome)
    echo "[open] Opening in Chrome..."
    open -a "/Applications/Google Chrome.app" "$URL"
    ;;
  firefox|ff)
    echo "[open] Opening in Firefox..."
    open -a "/Applications/Firefox.app" "$URL"
    ;;
  headless)
    echo "[open] Starting Playwright headless..."
    npx -y @playwright/mcp@latest --headless &
    ;;
  attach-chrome|attach-firefox)
    "$(dirname "$0")/attach-browser.sh" "${BROWSER#attach-}" "$URL"
    ;;
  *)
    echo "Usage: $0 {active|default|chrome|firefox|headless|attach-chrome|attach-firefox} [url]"
    exit 1
    ;;
esac

echo "[open] Done: $URL"
