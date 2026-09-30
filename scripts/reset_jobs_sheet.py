#!/usr/bin/env python3
"""Back up the JOBS tab, clear the data rows, and write the current header.

Destructive on purpose: it is the one-shot used to drop the two-week backlog
so every row going forward is genuinely new. Backs up first, always, and
refuses to clear anything unless a backup was written and verified.

The service account can read/write cell *values* but Google blocks it from
changing spreadsheet structure, so it cannot create the backup tab. Flow:
  1. dump every JOBS value to JSON on disk and verify the row count
  2. if JOBS_BAK_<date> already exists, mirror the values into that tab too
  3. clear JOBS!A2:X and write the current 24-column header

Run inside the scraper container (holds the service-account key):
    python3 reset_jobs_sheet.py [--yes]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "deploy"))
sys.path.insert(0, "/app")

import scraper_service as svc  # noqa: E402  (needs the env above)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SHEET_ID = os.environ.get("JBFIND_SHEET_ID", "")
TAB = os.environ.get("JBFIND_SHEET_TAB", "JOBS")
STAMP = time.strftime("%Y-%m-%d", time.gmtime())
BACKUP_TAB = f"JOBS_BAK_{STAMP}"
BACKUP_JSON = f"/opt/jbfind/backup/{TAB}_{STAMP}.json"
LAST_COL = svc._col(len(svc.HEADERS) - 1)

_creds = None


def token():
    global _creds
    if _creds is None:
        from google.oauth2 import service_account

        _creds = service_account.Credentials.from_service_account_file(
            os.environ.get("JBFIND_GOOGLE_SA", "/secrets/google-service-account.json"),
            scopes=["https://www.googleapis.com/auth/spreadsheets"])
    if not _creds.valid:
        from google.auth.transport.requests import Request

        _creds.refresh(Request())
    return _creds.token


def api(method, path, payload=None):
    url = f"{SHEETS_API}/{SHEET_ID}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token(),
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        if "<" in detail[:20]:
            detail = "google returned an html error page (structure change blocked?)"
        raise SystemExit(f"{method} {path} -> {exc.code}: {detail[:300]}")


def q(tab, rng):
    return urllib.parse.quote(f"{tab}!{rng}", safe="")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="skip the confirm prompt")
    args = ap.parse_args()

    if not SHEET_ID:
        raise SystemExit("JBFIND_SHEET_ID not set")

    tabs = [s["properties"]["title"] for s in
            api("GET", "?fields=sheets.properties").get("sheets", [])]
    print("tabs:", tabs)

    values = api("GET", f"/values/{q(TAB, 'A1:X')}").get("values", [])
    header, data_rows = (values[:1] or [[]]), max(len(values) - 1, 0)
    print(f"{TAB}: 1 header + {data_rows} data rows, {len(header[0])} cols")

    # 1. disk backup, verified by read-back
    os.makedirs(os.path.dirname(BACKUP_JSON), exist_ok=True)
    with open(BACKUP_JSON, "w") as fh:
        json.dump({"sheet_id": SHEET_ID, "tab": TAB, "dumped_at": STAMP,
                   "rows": values}, fh)
    with open(BACKUP_JSON) as fh:
        saved = json.load(fh)["rows"]
    if len(saved) != len(values):
        raise SystemExit(f"backup verify failed: {len(saved)} != {len(values)}")
    print(f"backup -> {BACKUP_JSON} ({len(values)} rows, verified)")

    # 2. mirror into the backup tab when it exists
    if BACKUP_TAB in tabs:
        for i in range(0, len(values), 400):
            chunk = values[i:i + 400]
            api("PUT", f"/values/{q(BACKUP_TAB, f'A{i + 1}')}"
                       f"?valueInputOption=RAW", {"values": chunk})
        print(f"mirrored {len(values)} rows into tab {BACKUP_TAB}")
    else:
        print(f"NOTE: tab {BACKUP_TAB} not found. The service account cannot "
              f"create tabs. JSON backup above is complete; add the tab in the "
              f"Sheets UI and re-run to mirror it.")

    if not args.yes:
        ans = input(f"\nClear {TAB}!A2:{LAST_COL} and write the new header? [y/N] ")
        if ans.strip().lower() != "y":
            raise SystemExit("aborted, nothing cleared")

    # 3. clear + header
    if data_rows:
        api("POST", "/values:batchClear", {"ranges": [f"{TAB}!A2:{LAST_COL}"]})
        print(f"cleared {TAB}!A2:{LAST_COL} ({data_rows} rows)")
    api("PUT", f"/values/{q(TAB, f'A1:{LAST_COL}')}?valueInputOption=RAW",
        {"values": [svc.HEADERS]})
    print(f"wrote {len(svc.HEADERS)} headers: {svc.HEADERS[-4:]}")


if __name__ == "__main__":
    main()
