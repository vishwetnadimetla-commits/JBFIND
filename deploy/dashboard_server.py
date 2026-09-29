from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
import json, mimetypes, os, urllib.parse, urllib.request
from google.oauth2 import service_account
from googleapiclient.discovery import build

SA_PATH = "/secrets/google-service-account.json"
SHEET_ID = "1voOTzO8auKbgrP4J4dPACnwf9Tg2MsgUSCU9w2-mPFY"
SHEET = "JOBS"
PORT = 8080
ALLOWED_EMAILS = {x.strip().lower() for x in os.getenv("JBFINDS_ALLOWED_EMAILS", "vishwet.nadimetla@gmail.com").split(",") if x.strip()}

# Tailored resumes land in Drive as 01_TAILORED_RESUMES/<date>_<company>/.
# DRIVE_PARENT is the "Vishwet" folder that holds MASTER RESUME.
DRIVE_PARENT = "1S9aMzkWeAKDqkprkpzgMavC-rTmUwPMJ"
TAILORED_ROOT = "01_TAILORED_RESUMES"
FOLDER_MIME = "application/vnd.google-apps.folder"

# The header row is owned by the scraper / reset script. The dashboard reads it
# and never writes it, so adding a column upstream cannot corrupt the sheet here.
DECISIONS = {"APPLY", "REJECT", "NOT_SUITABLE", "CLEAR"}
REASON_TAGS = {
    "NOT_SUITABLE": [
        "missing_core_skill", "seniority_too_high", "seniority_too_low",
        "title_mismatch", "not_mobile_domain", "outside_target_role",
    ],
    "REJECT": [
        "compensation_below_target", "location_not_remote", "relocation_required",
        "company_red_flags", "role_already_filled", "too_competitive",
    ],
}

creds = service_account.Credentials.from_service_account_file(
    SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
sheets = build("sheets", "v4", credentials=creds)
drive = build("drive", "v3", credentials=service_account.Credentials.from_service_account_file(
    SA_PATH, scopes=["https://www.googleapis.com/auth/drive"]))


def tailored_resumes(limit=30):
    """Generated resumes from 01_TAILORED_RESUMES, newest folder first.

    Each entry is a Drive folder holding the DOCX, the tailored markdown and the
    change log written by tools/resume_tailor.py.
    """
    def child(parent, name, folder=False):
        q = f"'{parent}' in parents and name = '{name}' and trashed = false"
        if folder:
            q += f" and mimeType = '{FOLDER_MIME}'"
        found = drive.files().list(q=q, fields="files(id)").execute().get("files", [])
        return found[0]["id"] if found else None

    root = child(DRIVE_PARENT, TAILORED_ROOT, folder=True)
    if not root:
        return {"resumes": []}

    folders = drive.files().list(
        q=f"'{root}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false",
        fields="files(id,name)", orderBy="name desc", pageSize=limit).execute().get("files", [])
    out = []
    for f in folders:
        files = drive.files().list(
            q=f"'{f['id']}' in parents and trashed = false",
            fields="files(id,name,mimeType,webViewLink)").execute().get("files", [])
        entry = {"folder": f["id"], "version": f["name"],
                 "url": f"https://drive.google.com/drive/folders/{f['id']}", "files": {}}
        for item in files:
            entry["files"][item["name"]] = item.get("webViewLink", "")
            if item["name"] == "changes.json":
                try:
                    changes = json.loads(drive.files().get_media(
                        fileId=item["id"]).execute().decode())
                    entry.update({k: changes.get(k) for k in
                                  ("company", "title", "job_id", "tweak_level",
                                   "created_date", "review_status", "factuality_check",
                                   "template_fidelity", "summary_rewritten")})
                except Exception:
                    pass
        out.append(entry)
    return {"resumes": out}


def col_letter(index):
    """0-based column index -> spreadsheet column letter."""
    index, out = index + 1, ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def fetch_jobs():
    res = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET}!A:X"
    ).execute()
    values = res.get("values", [])
    if not values:
        return {"headers": [], "jobs": []}
    headers = values[0]
    width = len(headers)
    jobs = [
        {h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)}
        for row in values[1:]
    ]
    return {"headers": headers, "jobs": jobs}


def update_decision(job_id, decision, reason=""):
    if decision not in DECISIONS:
        raise ValueError("invalid decision")
    reason = (reason or "").strip()
    if decision in REASON_TAGS and not reason:
        raise ValueError(f"{decision} requires a reason")
    if len(reason) > 300:
        raise ValueError("reason too long")

    data = fetch_jobs()
    headers, jobs = data["headers"], data["jobs"]
    cols = {name: headers.index(name) for name in
            ("Job_ID", "Decision", "Decision_Reason", "Decision_Updated")
            if name in headers}
    if len(cols) != 4:
        raise ValueError("JOBS sheet is missing decision columns")

    row_index = next(
        (i for i, job in enumerate(jobs, start=2) if job.get("Job_ID") == job_id),
        None,
    )
    if row_index is None:
        raise ValueError("job not found")

    first, last = col_letter(cols["Decision"]), col_letter(cols["Decision_Updated"])
    values = [[
        "" if decision == "CLEAR" else decision,
        "" if decision == "CLEAR" else reason,
        "" if decision == "CLEAR" else datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ]]
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET}!{first}{row_index}:{last}{row_index}",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
    return {"ok": True}


def authenticated(handler):
    token = handler.headers.get("Authorization", "")
    if not token.startswith("Bearer "):
        return False
    if not ALLOWED_EMAILS:
        return False
    try:
        query = urllib.parse.urlencode({"id_token": token[7:]})
        with urllib.request.urlopen(
            "https://oauth2.googleapis.com/tokeninfo?" + query, timeout=5
        ) as response:
            email = json.loads(response.read()).get("email", "").lower()
        return bool(email) and email in ALLOWED_EMAILS
    except Exception:
        return False


def telegram_configured():
    try:
        with open("/run/secrets/telegram.env") as f:
            for line in f:
                if line.startswith("JB_FINDER_REPORTER_TOKEN="):
                    return bool(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return False


def operations():
    # No getUpdates here: that conflicts with the n8n Telegram trigger polling
    # the same bot and makes both sides fail. Bot status is a token-presence check.
    result = {
        "n8n": {"status": "unknown"},
        "schedule": "every 30 minutes",
        "telegram": {"configured": telegram_configured()},
    }
    try:
        with urllib.request.urlopen("http://n8n:5678/healthz", timeout=3) as response:
            result["n8n"] = {"status": "healthy", "http": response.status}
    except Exception as exc:
        result["n8n"] = {"status": "offline", "error": str(exc)}
    return result


def settings_proxy(method, payload=None):
    """The scraper owns settings storage (/data/settings.json) because n8n
    reads the same endpoint for its report filter. This layer only
    authenticates and forwards, so read/write logic exists once."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "http://scraper:8001/settings", data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise ValueError(exc.read().decode()[:300] or "invalid settings")


def serve_asset(handler, relative):
    root = "/dash/app"
    path = os.path.realpath(os.path.join(root, relative.lstrip("/")))
    if not path.startswith(root + "/") or not os.path.isfile(path):
        handler._send(404, b"not found", "text/plain")
        return
    with open(path, "rb") as f:
        handler._send(200, f.read(), mimetypes.guess_type(path)[0] or "application/octet-stream")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open("/dash/app/index.html", "rb") as f:
                self._send(200, f.read(), "text/html")
            return

        if self.path == "/config.js":
            body = (
                "window.__JBFINDS_GOOGLE_CLIENT_ID__ = "
                + json.dumps(os.getenv("JBFINDS_GOOGLE_CLIENT_ID", ""))
                + ";\nwindow.__JBFINDS_ALLOWED_EMAILS__ = "
                + json.dumps(",".join(sorted(ALLOWED_EMAILS))) + ";\n"
            ).encode()
            self._send(200, body, "application/javascript")
            return

        if self.path.startswith("/assets/"):
            serve_asset(self, self.path)
            return

        if self.path == "/api/settings":
            if not authenticated(self):
                self._send(401, b'{"error":"authentication required"}')
                return
            try:
                self._send(200, json.dumps(settings_proxy("GET")).encode())
            except Exception as exc:
                self._send(502, json.dumps({"error": str(exc)}).encode())
            return

        if self.path == "/api/resume":
            if not authenticated(self):
                self._send(401, b'{"error":"authentication required"}')
                return
            try:
                self._send(200, json.dumps(tailored_resumes()).encode())
            except Exception as exc:
                self._send(502, json.dumps({"error": str(exc)}).encode())
            return

        if self.path in ("/api/jobs", "/api/operations"):
            if not authenticated(self):
                self._send(401, b'{"error":"authentication required"}')
                return
            if self.path == "/api/operations":
                self._send(200, json.dumps(operations()).encode())
                return
            try:
                payload = fetch_jobs()
                payload["reasonTags"] = REASON_TAGS
                self._send(200, json.dumps(payload).encode())
            except Exception as exc:
                self._send(502, json.dumps({"error": str(exc)}).encode())
            return

        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if not authenticated(self):
            self._send(401, b'{"error":"authentication required"}')
            return
        if self.path == "/api/settings":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._send(200, json.dumps(
                    settings_proxy("PUT", payload)).encode())
            except ValueError as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode())
            except Exception as exc:
                self._send(502, json.dumps({"error": str(exc)}).encode())
            return
        if not self.path.startswith("/api/jobs/") or not self.path.endswith("/decision"):
            self._send(404, b'{"error":"not found"}')
            return
        try:
            job_id = urllib.parse.unquote(self.path[len("/api/jobs/"):-len("/decision")])
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, json.dumps(update_decision(
                job_id, payload.get("decision", ""), payload.get("reason", "")
            )).encode())
        except ValueError as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode())
        except Exception as exc:
            self._send(502, json.dumps({"error": str(exc)}).encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"JBFind dashboard: http://localhost:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
