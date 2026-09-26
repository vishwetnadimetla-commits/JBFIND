#!/usr/bin/env python3
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
CLIENT = ROOT / "keys/google-oauth-client.json"
TOKEN = ROOT / "keys/google-drive-token.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
flow.redirect_uri = "http://localhost:8766/"
url, _ = flow.authorization_url(access_type="offline", prompt="consent", login_hint="vishwet.nadimetla@gmail.com")
print(url, flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.callback = "http://localhost:8766" + self.path
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Authentication complete. You may close this window.")
    def log_message(self, *_):
        pass

server = HTTPServer(("localhost", 8766), Handler)
server.handle_request()
flow.fetch_token(authorization_response=server.callback)
TOKEN.write_text(flow.credentials.to_json())
TOKEN.chmod(0o600)
print({"saved": str(TOKEN), "scopes": SCOPES})
