#!/usr/bin/env bash
# open-in.sh — open a URL in Chrome, Firefox, or Playwright headless
# Usage: ./open-in.sh chrome|firefox|headless [url]
# Default URL: http://localhost:8080

BROWSER="${1:-chrome}"
URL="${2:-http://localhost:8080}"

case "$BROWSER" in
  chrome|google-chrome)
    echo "[open] Opening in Chrome..."
    open -a "/Applications/Google Chrome.app" "$URL"
    ;;
  firefox|ff)
    echo "[open] Opening in Firefox..."
    open -a "/Applications/Firefox.app" "$URL"
    ;;
  headless)
    echo "[open] Opening in Playwright headless..."
    npx -y @playwright/mcp@latest --headless &
    echo "  (Playwright MCP headless started — use browser tools to navigate)"
    ;;
  *)
    echo "Usage: $0 {chrome|firefox|headless} [url]"
    exit 1
    ;;
esac

echo "[open] Done: $URL"