# Browser Bridge

`browser-bridge.mjs` attaches to an existing Chrome or Firefox window through
the browser's remote debugging endpoint. It does not create a Playwright
profile, copy cookies, or open a replacement browser session.

## Start an attachable browser

```bash
./attach-browser.sh chrome https://example.com
./attach-browser.sh firefox https://example.com
```

The Chrome endpoint is `9222`; Firefox uses `9223`. If an existing browser
process reuses the old process, close that browser and run the command again.

## Inspect and automate

```bash
node browser-bridge.mjs list chrome
node browser-bridge.mjs active chrome
node browser-bridge.mjs eval chrome 'document.title'
node browser-bridge.mjs navigate chrome https://example.com
node browser-bridge.mjs click chrome 'button[type=submit]'
node browser-bridge.mjs type chrome '#q' 'network engineer'
```

Every action uses macOS frontmost-tab information and then matches that exact
URL or title against the debugging targets. If the browser was not launched
with debugging enabled, the bridge fails instead of accidentally controlling a
different tab.

Use `BROWSER_DEBUG_PORT` when a different port is required.

## Reuse the current browser session

To open a URL in a new tab of an already-running browser, without restarting
it:

```bash
./open-in.sh active https://example.com
```

`active` prefers Firefox when it is running, then falls back to Chrome. This
preserves the existing browser profile, cookies, login state, and window.
