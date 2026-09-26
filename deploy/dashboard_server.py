from http.server import BaseHTTPRequestHandler, HTTPServer
import json, mimetypes, os, urllib.parse, urllib.request
import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build

SA_PATH = "/secrets/google-service-account.json"
SHEET_ID = "1voOTzO8auKbgrP4J4dPACnwf9Tg2MsgUSCU9w2-mPFY"
PORT = 8080
ALLOWED_EMAILS = {x.strip().lower() for x in os.getenv("JBFINDS_ALLOWED_EMAILS", "vishwet.nadimetla@gmail.com").split(",") if x.strip()}
SHEET_RANGE = "JOBS!A1:V"
HEADERS = [
    "Job_ID", "Company", "Job_Title", "Location", "Experience_Status",
    "Role_Match", "Skill_Match", "Tweak_Level", "Location_Match",
    "Overall_Score", "Job_Source", "Job_URL", "Posted_Date", "Discovered_Date",
    "Full_JD", "Recommended", "Why_This_Job", "Review_Required", "Last_Updated",
    "Decision", "Decision_Reason", "Decision_Updated",
]

creds = service_account.Credentials.from_service_account_file(
    SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
sheets = build("sheets", "v4", credentials=creds)


def fetch_jobs():
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=SHEET_RANGE
        ).execute()
        values = res.get("values", [])
        headers = values[0] if values else []
        rows = values[1:] if values else []
        missing = [h for h in HEADERS if h not in headers]
        if missing:
            headers = headers + missing
            sheets.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range="JOBS!A1:V",
                valueInputOption="RAW", body={"values": [HEADERS]},
            ).execute()
        rows = [row + [""] * (len(HEADERS) - len(row)) for row in rows]
        return headers, rows
    except Exception as e:
        return [], [{"error": str(e)}]


def update_decision(job_id, decision, reason=""):
    headers, rows = fetch_jobs()
    if not headers or "Job_ID" not in headers:
        raise ValueError("JOBS sheet must contain a Job_ID header")
    if decision not in {"APPLY", "NOT_SUITABLE", "REVIEW", "CLEAR"}:
        raise ValueError("invalid decision")
    try:
        row_index = next(i for i, row in enumerate(rows, start=2) if row and row[0] == job_id)
    except StopIteration:
        raise ValueError("job not found")

    values = [[decision if decision != "CLEAR" else "", reason, "" if decision == "CLEAR" else __import__("datetime").datetime.utcnow().isoformat() + "Z"]]
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"JOBS!T{row_index}:V{row_index}",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def authenticated(handler):
    token = handler.headers.get("Authorization", "")
    if not token.startswith("Bearer "):
        return False


def operations():
    result = {"n8n": {"status": "unknown"}, "schedule": "hourly at minute 00", "telegram": {"configured": False, "messages": []}}
    try:
        with urllib.request.urlopen("http://n8n:5678/healthz", timeout=3) as response:
            result["n8n"] = {"status": "healthy", "http": response.status}
    except Exception as exc:
        result["n8n"] = {"status": "offline", "error": str(exc)}
    try:
        values = {}
        with open("/run/secrets/telegram.env") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.strip().split("=", 1)
                    values[key] = value
        token = values.get("JB_FINDER_REPORTER_TOKEN", "")
        result["telegram"]["configured"] = bool(token)
        if token:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=5) as response:
                updates = json.loads(response.read()).get("result", [])[-5:]
            result["telegram"]["messages"] = [{"id": u.get("update_id"), "text": u.get("message", {}).get("text", ""), "date": u.get("message", {}).get("date")} for u in updates]
    except Exception as exc:
        result["telegram"]["error"] = str(exc)
    return result
    try:
        query = urllib.parse.urlencode({"id_token": token[7:]})
        with urllib.request.urlopen("https://oauth2.googleapis.com/tokeninfo?" + query, timeout=5) as response:
            email = json.loads(response.read()).get("email", "").lower()
        return email in ALLOWED_EMAILS
    except Exception:
        return False


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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open("/dash/app/index.html", "rb") as f:
                html = f.read()
            self._send(200, html, "text/html")
            return

        if self.path == "/config.js":
            body = ("window.__JBFINDS_GOOGLE_CLIENT_ID__ = " + json.dumps(os.getenv("JBFINDS_GOOGLE_CLIENT_ID", "")) + ";\n"
                    "window.__JBFINDS_ALLOWED_EMAILS__ = " + json.dumps(",".join(sorted(ALLOWED_EMAILS))) + ";\n").encode()
            self._send(200, body, "application/javascript")
            return

        if self.path.startswith("/assets/"):
            serve_asset(self, self.path)
            return

        if self.path == "/api/jobs":
            if not authenticated(self):
                self._send(401, b'{"error":"authentication required"}')
                return
            headers, rows = fetch_jobs()
            self._send(200, json.dumps({
                "headers": headers,
                "rows": [dict(zip(HEADERS, row)) for row in rows],
            }).encode())
            return

        if self.path == "/api/operations":
            if not authenticated(self):
                self._send(401, b'{"error":"authentication required"}')
                return
            self._send(200, json.dumps(operations()).encode())
            return

        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if not authenticated(self):
            self._send(401, b'{"error":"authentication required"}')
            return
        if not self.path.startswith("/api/jobs/") or not self.path.endswith("/decision"):
            self._send(404, b'{"error":"not found"}')
            return
        try:
            job_id = self.path[len("/api/jobs/"):-len("/decision")]
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            update_decision(job_id, payload.get("decision", ""), payload.get("reason", ""))
            self._send(200, b'{"ok":true}')
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"JBFind dashboard: http://localhost:{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
