#!/usr/bin/env python3
"""Clear the probe rows written while verifying the service-account path.

Run inside the scraper container, which already has google-auth and the key:
    docker exec jbfind-scraper python /tmp/cleanup_probe_rows.py
"""
import json, os, urllib.error, urllib.parse, urllib.request

from google.auth.transport.requests import Request
from google.oauth2 import service_account

SHEET = os.environ.get("JBFIND_SHEET_ID", "")
TAB = os.environ.get("JBFIND_SHEET_TAB", "JOBS")
API = "https://sheets.googleapis.com/v4/spreadsheets"
MARKERS = ("n8nsa", "n8ntest", "TESTCO", "SATEST", "RAWPROBE", "ALIGN")

creds = service_account.Credentials.from_service_account_file(
    "/secrets/google-service-account.json",
    scopes=["https://www.googleapis.com/auth/spreadsheets"])
creds.refresh(Request())
TOKEN = creds.token


def api(method, url, payload=None):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        method=method, headers={"Authorization": "Bearer " + TOKEN,
                                "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        print("FAILED", method, e.code, e.read().decode()[:300])
        raise SystemExit(1)


values = api("GET", f"{API}/{SHEET}/values/{TAB}!A1:Z2000").get("values", [])
targets = [i + 1 for i, r in enumerate(values)
           if any(m in c for c in r for m in MARKERS)]
print("probe rows found:", targets)

blocks = []
for row in sorted(targets):
    if blocks and row == blocks[-1][1] + 1:
        blocks[-1][1] = row
    else:
        blocks.append([row, row])

for start, end in reversed(blocks):
    rng = f"{TAB}!A{start}:W{end}"
    print("cleared", rng, "->", api("POST", f"{API}/{SHEET}/values/{rng}:clear").get("clearedRange"))

after = api("GET", f"{API}/{SHEET}/values/{TAB}!A1:Z2000").get("values", [])
print("rows before:", len(values), "| rows after:", len(after))
