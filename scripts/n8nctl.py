#!/usr/bin/env python3
"""Small n8n REST CLI: deploy / activate / deactivate / status / last-exec.

    python3 scripts/n8nctl.py status
    python3 scripts/n8nctl.py deploy deploy/jbfind-daily-workflow.json
    python3 scripts/n8nctl.py activate
    python3 scripts/n8nctl.py deactivate
"""
import json
import os
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
        deploy(sys.argv[2])
        if len(sys.argv) > 3 and sys.argv[3] == "activate":
            active, r = set_active(True)
            print("active:", active)
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
