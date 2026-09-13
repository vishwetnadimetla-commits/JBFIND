from http.server import BaseHTTPRequestHandler, HTTPServer
import json, os
import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build

SA_PATH = os.path.expanduser("~/.jbfind/secrets/google-service-account.json")
SHEET_ID = "1voOTzO8auKbgrP4J4dPACnwf9Tg2MsgUSCU9w2-mPFY"
PORT = 8080

creds = service_account.Credentials.from_service_account_file(
    SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
)
sheets = build("sheets", "v4", credentials=creds)


def fetch_jobs():
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range="JOBS!A1:T"
        ).execute()
        values = res.get("values", [])
        headers = values[0] if values else []
        rows = values[1:] if values else []
        return headers, rows
    except Exception as e:
        return [], [{"error": str(e)}]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(os.path.dirname(__file__), "dashboard.html"), "rb") as f:
                html = f.read()
            self._send(200, html, "text/html")
            return

        if self.path == "/api/jobs":
            headers, rows = fetch_jobs()
            self._send(200, json.dumps({"headers": headers, "rows": rows}).encode())
            return

        self._send(404, b'{"error":"not found"}')

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"JBFind dashboard: http://localhost:{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
