from fastapi import FastAPI, Query, HTTPException
from jobspy import scrape_jobs
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import json
import math
import os
import time
import urllib.error
import urllib.request

app = FastAPI(title="JBFind Scraper")

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SHEET_ID = os.environ.get("JBFIND_SHEET_ID", "")
SHEET_TAB = os.environ.get("JBFIND_SHEET_TAB", "JOBS")
SA_PATH = os.environ.get("JBFIND_GOOGLE_SA", "/secrets/google-service-account.json")

# Sheet column order, owned here so the workflow does not repeat the contract.
# Keys are the snake_case job fields the workflow sends; order matches the
# sheet header exactly (22 columns).
FIELDS = [
    "job_id", "company", "title", "location", "experience_status",
    "role_match", "skill_match", "tweak_level", "location_match",
    "overall_score", "source", "job_url", "posted_date", "discovered_date",
    "jd", "recommended", "why_this_job", "review_required", "last_updated",
    "decision", "decision_reason", "decision_updated",
]

_creds = None
_index = {"at": 0.0, "rows": {}}


def text(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value)


def number(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0
    return value


def clean(value):
    if isinstance(value, float) and math.isnan(value):
        return 0
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value

@app.get("/scrape")
def scrape(
    q: str = Query(..., min_length=1),
    location: str = Query("Bangalore, India"),
    sites: str = Query("indeed,linkedin,naukri,google"),
    count: int = Query(50, le=200),
    hours_old: int = Query(168, ge=1, le=720),
):
    site_list = [s.strip() for s in sites.split(",")]
    jobs = scrape_jobs(
        site_name=site_list,
        search_term=q,
        google_search_term=f"{q} jobs near {location}",
        location=location,
        results_wanted=count,
        hours_old=hours_old,
        country_indeed="India",
        verbose=0,
    )
    result = []
    for _, job in jobs.iterrows():
        result.append({
            "source": job.get("site", ""),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "company_url": job.get("company_url", ""),
            "location": f"{job.get('city', '')}, {job.get('state', '')}".strip(", "),
            "country": job.get("country", ""),
            "is_remote": bool(job.get("is_remote")),
            "description": text(job.get("description"))[:3000],
            "url": job.get("job_url", ""),
            "date_posted": text(job.get("date_posted")),
            "job_type": text(job.get("job_type")),
            "salary_min": number(job.get("min_amount")),
            "salary_max": number(job.get("max_amount")),
            "salary_currency": text(job.get("currency")),
            "experience": text(job.get("experience")),
        })
    return clean(result)


@app.post("/sheets/upsert")
def sheets_upsert(job: dict):
    """Upsert one job row by Job_ID. Google auth lives here, not in n8n:
    n8n 2.x only accepts an OAuth token written by its own consent flow, which
    needs an HTTPS redirect URI this host does not have."""
    if not SHEET_ID:
        raise HTTPException(status_code=500, detail="JBFIND_SHEET_ID not set")
    if not job.get("job_id"):
        raise HTTPException(status_code=400, detail="job_id is required")

    job.setdefault("last_updated", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    row = [_cell(job.get(field)) for field in FIELDS]
    existing = _row_index().get(str(job["job_id"]))
    row_no = 0
    if existing:
        rng = f"{SHEET_TAB}!A{existing}:{chr(ord('A') + len(FIELDS) - 1)}{existing}"
        _api("PUT", f"{SHEETS_API}/{SHEET_ID}/values/{rng}"
                    f"?valueInputOption=RAW", {"values": [row]})
        row_no = existing
        action = "updated"
    else:
        out = _api("POST", f"{SHEETS_API}/{SHEET_ID}/values/{SHEET_TAB}:append"
                           f"?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
                   {"values": [row]})
        updated = out.get("updates", {}).get("updatedRange", "")
        if ":" in updated:
            tail = updated.split(":")[-1]
            row_no = int("".join(c for c in tail if c.isdigit()) or 0)
        action = "appended"
    if row_no:
        _index["rows"][str(job["job_id"])] = row_no
    # Echo the job back so downstream nodes (aggregate, report) keep the ranked
    # fields instead of only the write result.
    return {**job, "sheet_action": action, "sheet_row": row_no}


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def _credentials():
    global _creds
    if _creds is None:
        _creds = service_account.Credentials.from_service_account_file(
            SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return _creds


def _token():
    creds = _credentials()
    if not creds.valid:
        creds.refresh(Request())
    return creds.token


def _api(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + _token(),
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=exc.code,
                            detail=exc.read().decode()[:500])


def _row_index():
    """Job_ID -> sheet row number, cached briefly so a 12-job run reads
    column A once instead of twelve times."""
    if time.time() - _index["at"] < 120:
        return _index["rows"]
    values = _api("GET", f"{SHEETS_API}/{SHEET_ID}/values/{SHEET_TAB}!A:A") \
        .get("values", [])
    rows = {}
    for offset, row in enumerate(values[1:], start=2):
        if row and row[0]:
            rows[str(row[0])] = offset
    _index["rows"] = rows
    _index["at"] = time.time()
    return rows


@app.get("/health")
def health():
    return {"status": "ok"}
