#!/usr/bin/env bash
set -euo pipefail

# Launch a browser with an attachable debugging endpoint. Existing processes
# may need to be closed first because Chrome/Firefox reuse an old process.
BROWSER="${1:-chrome}"
URL="${2:-about:blank}"
PORT="${BROWSER_DEBUG_PORT:-$([[ "$BROWSER" == "firefox" ]] && echo 9223 || echo 9222)}"

case "$BROWSER" in
  chrome|google-chrome)
    open -na "/Applications/Google Chrome.app" --args "--remote-debugging-port=$PORT" "$URL"
    ;;
  firefox|ff)
    open -na "/Applications/Firefox.app" --args "--remote-debugging-port=$PORT" "$URL"
    ;;
  *)
    echo "Usage: $0 {chrome|firefox} [url]" >&2
    exit 2
    ;;
esac

echo "Attached-browser endpoint: http://127.0.0.1:$PORT"
