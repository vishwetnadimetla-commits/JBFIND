#!/usr/bin/env python3
"""Small n8n REST CLI: deploy / activate / deactivate / status / verify.

    python3 scripts/n8nctl.py status
    python3 scripts/n8nctl.py deploy deploy/jbfind-daily-workflow.json
    python3 scripts/n8nctl.py activate
    python3 scripts/n8nctl.py deactivate
    python3 scripts/n8nctl.py verify deploy/jbfind-daily-workflow.json
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar

BASE = os.environ.get("N8N_URL", "http://155.248.244.186:5678")
WF = os.environ.get("JBFIND_WF_ID", "jbfind-daily-workflow")
EMAIL = os.environ.get("N8N_LOGIN_EMAIL", "")
PASSWORD = os.environ.get("N8N_LOGIN_PASSWORD", "")


def _load_dotenv():
    """Read .env ourselves. Sourcing it in bash mangles values containing $
    or ! because the shell expands them."""
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("'\""))


_load_dotenv()
EMAIL = os.environ.get("N8N_LOGIN_EMAIL", EMAIL)
PASSWORD = os.environ.get("N8N_LOGIN_PASSWORD", PASSWORD)

_jar = http.cookiejar.CookieJar()
_op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))


def call(path, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method or ("POST" if data else "GET"),
        headers={"Content-Type": "application/json"})
    try:
        with _op.open(req, timeout=60) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        return {"__status": e.code, "__body": e.read().decode()[:400]}
    return json.loads(body) if body.strip() else {}


def login():
    r = call("/rest/login", {"emailOrLdapLoginId": EMAIL, "password": PASSWORD})
    if "__status" in r:
        raise SystemExit(f"login failed: {r}")
    return r


def get_wf():
    return call(f"/rest/workflows/{WF}")["data"]


def set_active(on):
    vid = get_wf().get("versionId")
    r = call(f"/rest/workflows/{WF}/{'activate' if on else 'deactivate'}",
             {"versionId": vid}, "POST")
    return get_wf()["active"], r


# n8n fills in its own defaults (onError, retryOnFail, positions...), so diffing
# every parameter is all false positives. Compare only what we actually set --
# these are the values whose loss silently broke runs.
CRITICAL = {
    "LLM Analyze Batch": ["batchSize", "batchInterval", "options.timeout", "url"],
    "JobSpy Scrape": ["url"],
    "RemoteOK API": ["url"],
    "Remotive API": ["url"],
    "Arbeitnow API": ["url"],
    "Jobicy API": ["url"],
    "Himalayas API": ["url"],
    "Sheets Write Jobs": ["url"],
    "Send Error Alert": ["text"],
}


def _get(obj, path):
    """Dotted-path lookup, None when absent."""
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def _edges(conns):
    """Collect (source, target) pairs from either connection shape.

    The repo file nests as {node: {main: [[{"node": x}]]}} while the REST API
    normalises to {node: {main: [[["x", {...}]]]}}, so walk it generically.
    """
    out = set()

    def walk(src, node):
        if isinstance(node, dict):
            if isinstance(node.get("node"), str):
                out.add((src, node["node"]))
                return
            for v in node.values():
                walk(src, v)
        elif isinstance(node, (list, tuple)):
            if node and isinstance(node[0], str):
                out.add((src, node[0]))
                return
            for v in node:
                walk(src, v)

    for src, outs in (conns or {}).items():
        walk(src, outs)
    return sorted(out)


def verify(path):
    """Fail loudly when the live workflow drifted from the repo file.

    The n8n editor canvas overwrites REST deploys, and the only symptom was a
    node quietly losing batchSize hours later, so the cron kept failing while
    every deploy reported success. Run this any time you suspect that.
    """
    want = json.load(open(path))
    live = get_wf()
    wmap = {n["name"]: n.get("parameters", {}) for n in want["nodes"]}
    lmap = {n["name"]: n.get("parameters", {}) for n in live["nodes"]}
    bad = []
    for name in set(wmap) ^ set(lmap):
        side = "missing from live" if name in wmap else "only in live"
        bad.append(f"node {name!r} {side}")
    for name, keys in CRITICAL.items():
        for k in keys:
            a, b = _get(wmap.get(name, {}), k), _get(lmap.get(name, {}), k)
            if a != b:
                bad.append(f"{name}.{k}: want={a!r} live={b!r}")
    for node in ("Normalize + Dedupe",):
        pat = r"const LLM_BUDGET = (\d+)"
        w, l = re.search(pat, wmap.get(node, {}).get("jsCode", "")), \
               re.search(pat, lmap.get(node, {}).get("jsCode", ""))
        if (w and l and w.group(1) != l.group(1)) or bool(w) != bool(l):
            bad.append(f"{node}.LLM_BUDGET: want={w and w.group(1)} live={l and l.group(1)}")
    if _edges(want.get("connections")) != _edges(live.get("connections")):
        bad.append("connection graph differs")
    for b in bad:
        print("  DRIFT:", b)
    if bad:
        raise SystemExit(f"verify FAILED ({len(bad)} drift) - redeploy: "
                         f"n8nctl.py deploy {path} activate")
    print(f"verify OK: {len(lmap)} nodes, {len(_edges(live.get('connections')))} edges, "
          f"live matches {path}")


def deploy(path):
    wf = json.load(open(path))
    cur = get_wf()
    body = {
        "id": WF,
        "name": wf.get("name", cur["name"]),
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": wf.get("settings", {}),
        "versionId": cur.get("versionId"),
    }
    r = call(f"/rest/workflows/{WF}", body, "PATCH")
    if "__status" in r:
        raise SystemExit(f"patch failed: {r}")
    print(f"patched {WF} nodes={len(wf['nodes'])}")
    return r


def main():
    if not EMAIL or not PASSWORD:
        raise SystemExit("set N8N_LOGIN_EMAIL / N8N_LOGIN_PASSWORD")
    login()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "deploy":
        path = sys.argv[2]
        deploy(path)
        if len(sys.argv) > 3 and sys.argv[3] == "activate":
            active, r = set_active(True)
            print("active:", active)
        # never claim success without proving the live copy took it
        verify(path)
    elif cmd == "verify":
        verify(sys.argv[2] if len(sys.argv) > 2
               else "deploy/jbfind-daily-workflow.json")
    elif cmd == "activate":
        print("active:", set_active(True)[0])
    elif cmd == "deactivate":
        print("active:", set_active(False)[0])
    elif cmd == "status":
        w = get_wf()
        cron = [n for n in w["nodes"] if n["type"] == "n8n-nodes-base.cron"]
        print(f"{w['name']} active={w['active']} nodes={len(w['nodes'])} "
              f"version={w.get('versionId')}")
        for c in cron:
            print("  cron:", json.dumps(c["parameters"].get("triggerTimes")))
        ex = call(f"/rest/executions?workflowId={WF}&limit=3")["data"]["results"]
        for e in ex:
            print(f"  exec {e['id']} {e.get('status')} {e.get('mode')} {e['startedAt']}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
