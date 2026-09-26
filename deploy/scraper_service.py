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
# sheet header exactly (24 columns).
FIELDS = [
    "job_id", "company", "title", "location", "experience_status",
    "role_match", "skill_match", "tweak_level", "location_match",
    "overall_score", "source", "job_url", "posted_date", "discovered_date",
    "jd", "recommended", "why_this_job", "review_required", "last_updated",
    "decision", "decision_reason", "decision_updated",
    "company_url", "careers_url",
]

# Human-readable header, written once by scripts/reset_jobs_sheet.py.
HEADERS = [
    "Job_ID", "Company", "Title", "Location", "Experience_Status",
    "Role_Match", "Skill_Match", "Tweak_Level", "Location_Match",
    "Overall_Score", "Source", "Job_URL", "Posted_Date", "Discovered_Date",
    "JD", "Recommended", "Why_This_Job", "Review_Required", "Last_Updated",
    "Decision", "Decision_Reason", "Decision_Updated",
    "Company_URL", "Careers_URL",
]

# Verified corporate career portals for the companies we actually see. Seeded
# by hand on purpose: resolving a real ATS board per job costs a network round
# trip on every run, and a wrong guess is worse than an honest blank.
# ponytail: static map, no live probing. Add a company when it shows up often.
# Verified from the VM on 2026-09-26. 200 = confirmed. 403 = Akamai/WAF blocks
# bots but the page is live for a human. 500 = Workday rejects non-browser
# requests on the board root; the tenant is real.
CAREERS_URLS = {
    "accenture": "https://www.accenture.com/us-en/careers",
    "infosys": "https://www.infosys.com/careers.html",
    "wipro": "https://careers.wipro.com/",
    "ust": "https://www.ust.com/careers",
    "yash": "https://careers.yash.com/",
    "docusign": "https://careers.docusign.com/",
    "citi": "https://careers.citi.com/",
    "state street": "https://careers.statestreet.com/",
    "capgemini": "https://www.capgemini.com/careers/",
    "qualcomm": "https://qualcomm.wd5.myworkdayjobs.com/External",
    "jpmorgan": "https://www.jpmorganchase.com/careers",
    "nvidia": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    "mphasis": "https://careers.mphasis.com/",
    "ey": "https://www.ey.com/en_gl/careers",
    "vois": "https://www.vois.com/careers",
}


def careers_url(company, company_url=""):
    """Careers portal for a company, else its profile page, else blank."""
    name = (company or "").strip().lower()
    if name in CAREERS_URLS:
        return CAREERS_URLS[name]
    for key, url in CAREERS_URLS.items():
        if name.startswith(key) or key in name:
            return url
    return text(company_url)

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
    # Naukri answers 406 recaptcha unless requests come from a residential IP.
    # Only that one site gets the proxy; proxying LinkedIn/Indeed gets us
    # rate-limited for nothing.
    proxy = os.environ.get("JBFIND_NAUKRI_PROXY", "").strip()
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    if "naukri" in site_list and not proxy:
        site_list = [s for s in site_list if s != "naukri"]
    jobs = scrape_jobs(
        site_name=site_list,
        search_term=q,
        google_search_term=f"{q} jobs near {location}",
        location=location,
        results_wanted=count,
        hours_old=hours_old,
        country_indeed="India",
        proxies=proxies,
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
    # Enrich here so the careers map lives in exactly one place; the workflow
    # only has to pass through the company profile URL it already scraped.
    job.setdefault("company_url", "")
    job["careers_url"] = job.get("careers_url") or careers_url(
        job.get("company", ""), job.get("company_url", ""))
    row = [_cell(job.get(field)) for field in FIELDS]
    existing = _row_index().get(str(job["job_id"]))
    row_no = 0
    if existing:
        rng = f"{SHEET_TAB}!A{existing}:{_col(len(FIELDS) - 1)}{existing}"
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


def _col(index):
    """0-based column index to its A1 letter."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


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
