#!/usr/bin/env bash
# open-in.sh — open URL in system default browser (new tab), Chrome, Firefox, or headless
# Usage: ./open-in.sh [chrome|firefox|headless] [url]

BROWSER="${1:-default}"
URL="${2:-http://localhost:8080}"

case "$BROWSER" in
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
  *)
    echo "Usage: $0 {default|chrome|firefox|headless} [url]"
    exit 1
    ;;
esac

echo "[open] Done: $URL"