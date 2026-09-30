#!/usr/bin/env node

// Attach to an already-running Chrome/Firefox debugging target.
// Browsers must be started with a remote debugging port; this does not create
// a second browser profile or silently fall back to a new session.

import { execFileSync } from "node:child_process";
import process from "node:process";

const DEFAULT_PORTS = { chrome: 9222, firefox: 9223 };

function usage() {
  console.error(`Usage:
  browser-bridge.mjs list [chrome|firefox]
  browser-bridge.mjs active [chrome|firefox]
  browser-bridge.mjs eval [chrome|firefox] <javascript>
  browser-bridge.mjs navigate [chrome|firefox] <url>
  browser-bridge.mjs click [chrome|firefox] <css-selector>
  browser-bridge.mjs type [chrome|firefox] <css-selector> <text>

Environment:
  BROWSER_DEBUG_PORT overrides the default port.`);
  process.exit(2);
}

function browserName(value) {
  const name = (value || "chrome").toLowerCase();
  if (!(name in DEFAULT_PORTS)) throw new Error(`Unsupported browser: ${name}`);
  return name;
}

function frontmostTab(browser) {
  const app = browser === "chrome" ? "Google Chrome" : "Firefox";
  const script = browser === "chrome"
    ? `tell application "${app}" to return (URL of active tab of front window) & linefeed & (title of active tab of front window)`
    : `tell application "${app}" to return (URL of active tab of front window) & linefeed & (name of active tab of front window)`;
  try {
    const [url, title = ""] = execFileSync("osascript", ["-e", script], { encoding: "utf8" }).trim().split("\n");
    return { url, title };
  } catch {
    return null;
  }
}

async function json(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json();
}

async function targets(browser) {
  const port = process.env.BROWSER_DEBUG_PORT || DEFAULT_PORTS[browser];
  let items;
  try {
    items = await json(`http://127.0.0.1:${port}/json/list`);
  } catch {
    throw new Error(`${browser} debugging endpoint is unavailable on port ${port}. Start it with --remote-debugging-port=${port}.`);
  }
  return items.filter((item) => item.type === "page" && item.webSocketDebuggerUrl);
}

function chooseTarget(items, browser, active) {
  if (!items.length) throw new Error(`No debuggable ${browser} pages found.`);
  if (!active) return items[0];
  const front = frontmostTab(browser);
  if (!front) throw new Error(`Could not read the frontmost ${browser} tab. Pass a browser target explicitly or run on macOS with the browser open.`);
  const target = items.find((item) => item.url === front.url) || items.find((item) => item.title === front.title);
  if (!target) throw new Error(`The frontmost ${browser} tab is not exposed by its debugging endpoint: ${front.url}`);
  return target;
}

async function cdp(target, method, params = {}) {
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const id = Math.floor(Math.random() * 1_000_000_000);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { socket.close(); reject(new Error("CDP request timed out")); }, 15_000);
    socket.addEventListener("open", () => socket.send(JSON.stringify({ id, method, params })));
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== id) return;
      clearTimeout(timer);
      socket.close();
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
    });
    socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error("Could not connect to browser debugging socket")); });
  });
}

async function evaluate(target, expression) {
  const result = await cdp(target, "Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || "JavaScript evaluation failed");
  return result.result?.value;
}

async function main() {
  const [command, browserArg, ...args] = process.argv.slice(2);
  if (!command) usage();
  const browser = browserName(browserArg);
  const items = await targets(browser);
  if (command === "list") {
    console.log(JSON.stringify(items.map(({ id, title, url }) => ({ id, title, url })), null, 2));
    return;
  }
  const target = chooseTarget(items, browser, true);
  if (command === "active") {
    console.log(JSON.stringify({ id: target.id, title: target.title, url: target.url }, null, 2));
  } else if (command === "eval") {
    if (!args[0]) usage();
    console.log(JSON.stringify(await evaluate(target, args.join(" "))));
  } else if (command === "navigate") {
    if (!args[0]) usage();
    await cdp(target, "Page.navigate", { url: args[0] });
    console.log(JSON.stringify({ ok: true, url: args[0] }));
  } else if (command === "click") {
    if (!args[0]) usage();
    await evaluate(target, `document.querySelector(${JSON.stringify(args[0])})?.click() ?? (() => { throw new Error("Element not found") })()`);
    console.log(JSON.stringify({ ok: true }));
  } else if (command === "type") {
    if (args.length < 2) usage();
    const expression = `(() => { const el = document.querySelector(${JSON.stringify(args[0])}); if (!el) throw new Error("Element not found"); el.focus(); el.value = ${JSON.stringify(args.slice(1).join(" "))}; el.dispatchEvent(new Event("input", { bubbles: true })); })()`;
    await evaluate(target, expression);
    console.log(JSON.stringify({ ok: true }));
  } else usage();
}

main().catch((error) => { console.error(`[browser-bridge] ${error.message}`); process.exit(1); });
