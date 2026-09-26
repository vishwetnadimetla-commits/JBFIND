from fastapi import FastAPI, Query, HTTPException
from jobspy import scrape_jobs
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import json
import math
import os
import threading
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


# Tunables the dashboard edits at runtime. A JSON file, not a sheet tab: the
# service account may read/write cell values but Google blocks it from adding
# tabs (see reset_jobs_sheet.py), so a SETTINGS tab would need a manual setup
# step and this would still be the simpler thing.
SETTINGS_PATH = "/data/settings.json"
SETTINGS_DEFAULTS = {"min_skill_match": 60}
SETTINGS_LIMITS = {"min_skill_match": (0, 100)}


def settings_read():
    try:
        with open(SETTINGS_PATH) as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        stored = {}
    return {**SETTINGS_DEFAULTS,
            **{k: v for k, v in stored.items() if k in SETTINGS_DEFAULTS}}


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

def scrape_naukri(search_term, count=20):
    """Naukri over plain HTTP answers 406 recaptcha, and Playwright's headless
    *shell* gets 403 from Akamai - but full Chromium headless loads the search
    page and its own /jobapi/v3/search XHR hands back JSON. Verified from this
    host on 2026-09-26 (200, 20 cards); the search is public, so no login
    cookie is needed. Any failure means this source alone comes back empty."""
    from playwright.sync_api import sync_playwright

    seo = "-".join(search_term.lower().split()) + "-jobs"
    details = []

    def collect(response):
        if "/jobapi/v3/search" in response.url:
            try:
                details.extend(response.json().get("jobDetails") or [])
            except Exception:
                pass

    response = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, channel="chromium",
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            # The browser-identifying bits the probe needed too: default
            # HeadlessChrome UA makes Naukri serve its bot shell instead of the
            # search page.
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0.0.0 Safari/537.36",
                locale="en-US", viewport={"width": 1366, "height": 900})
            page.on("response", collect)
            response = page.goto(f"https://www.naukri.com/{seo}",
                                 wait_until="domcontentloaded", timeout=45000)
            # the search widget mounts first, then fires the XHR we collect
            page.wait_for_selector("div.srp-jobtuple-wrapper", timeout=25000)
            browser.close()
    except Exception as exc:
        print(f"naukri scrape failed: {exc} "
              f"(nav={getattr(response, 'status', '?')})", flush=True)
        return []

    out = []
    for job in details[:count]:
        ph = job.get("placeholders") or []
        loc = next((x.get("label", "") for x in ph if x.get("type") == "location"), "")
        title = job.get("title", "")
        body = job.get("jobDescription") or job.get("tagsAndSkills") or ""
        out.append({
            "source": "naukri",
            "title": title,
            "company": job.get("companyName", ""),
            "company_url": "",
            "location": loc,
            "country": "India",
            "is_remote": "work from home" in f"{title} {loc}".lower(),
            "description": text(body)[:3000],
            "url": f"https://www.naukri.com{job.get('jdURL') or '/job/' + str(job.get('jobId', ''))}",
            "date_posted": text(job.get("createdDate") or job.get("footerPlaceholderLabel")),
            "job_type": "",
            "salary_min": 0,
            "salary_max": 0,
            "salary_currency": "",
            "experience": text(job.get("experienceText")),
        })
    return out


@app.get("/scrape")
def scrape(
    q: str = Query(..., min_length=1),
    location: str = Query("Bangalore, India"),
    sites: str = Query("indeed,linkedin,naukri,google"),
    count: int = Query(50, le=200),
    hours_old: int = Query(168, ge=1, le=720),
):
    site_list = [s.strip() for s in sites.split(",")]
    # Naukri runs through the browser-backed scraper above; JobSpy's plain-HTTP
    # one for it is 406 recaptcha from this host, so it never reaches scrape_jobs.
    naukri = scrape_naukri(q, count=min(count, 20)) if "naukri" in site_list else []
    site_list = [s for s in site_list if s != "naukri"]
    jobs = scrape_jobs(
        site_name=site_list or ["indeed"],
        search_term=q,
        google_search_term=f"{q} jobs near {location}",
        location=location,
        results_wanted=count,
        hours_old=hours_old,
        country_indeed="India",
        verbose=0,
    ) if site_list else None
    result = []
    for _, job in (jobs.iterrows() if jobs is not None and len(jobs) else []):
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
    result.extend(naukri)
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


DECISIONS = {"APPLY", "REJECT", "NOT_SUITABLE", "CLEAR"}
REASON_REQUIRED = {"REJECT", "NOT_SUITABLE"}


def _decide(job_id, decision, reason=""):
    """Patch only the three decision columns. /sheets/upsert rewrites the
    whole row, so a caller that only wants to decide would blank the job."""
    if decision not in DECISIONS:
        raise HTTPException(status_code=400, detail="invalid decision")
    reason = (reason or "").strip()
    if decision in REASON_REQUIRED and not reason:
        raise HTTPException(status_code=400, detail=f"{decision} requires a reason")
    row = _row_index().get(str(job_id))
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    clear = decision == "CLEAR"
    stamp = "" if clear else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rng = f"{SHEET_TAB}!{_col(19)}{row}:{_col(21)}{row}"
    _api("PUT", f"{SHEETS_API}/{SHEET_ID}/values/{rng}?valueInputOption=RAW",
         {"values": [["" if clear else decision,
                      "" if clear else reason, stamp]]})
    return {"ok": True, "job_id": job_id, "decision": decision}


@app.post("/decide")
def decide(payload: dict):
    return _decide(payload.get("job_id", ""), payload.get("decision", ""),
                   payload.get("reason", ""))


# Telegram inline-button presses arrive as callback_query updates. n8n's
# Telegram Trigger only takes those over an HTTPS webhook, which this host
# does not have until the DuckDNS/Caddy workstream, so the scraper polls
# getUpdates itself. Nothing else on the bot polls (the dashboard stopped
# long ago), so there is no competing consumer.
TG_OFFSET_PATH = "/data/tg_offset"


def tg_token():
    try:
        with open("/run/secrets/telegram.env") as fh:
            for line in fh:
                if line.startswith("JB_FINDER_REPORTER_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _tg(method, **params):
    token = tg_token()
    if not token:
        raise RuntimeError("bot token not mounted")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        body = json.loads(response.read())
    if not body.get("ok"):
        raise RuntimeError(f"telegram {method}: {body.get('description')}")
    return body.get("result", [])


def _answer(callback_id, text):
    try:
        _tg("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except Exception as exc:
        print(f"answerCallbackQuery failed: {exc}", flush=True)


def _handle_callback(cq):
    parts = str(cq.get("data") or "").split(":", 2)
    callback_id = cq.get("id", "")
    if len(parts) != 3 or parts[0] != "d":
        _answer(callback_id, "Unknown button.")
        return
    _, decision, job_id = parts
    user = (cq.get("from") or {}).get("first_name", "you")
    reason = f"decided from Telegram by {user}" if decision in REASON_REQUIRED else ""
    try:
        _decide(job_id, decision, reason)
        _answer(callback_id, f"{decision} — {job_id}")
    except HTTPException as exc:
        _answer(callback_id, str(exc.detail))
    except Exception as exc:
        print(f"callback failed: {exc}", flush=True)
        _answer(callback_id, "Could not save the decision.")


def _poll_loop():
    try:
        offset = int(open(TG_OFFSET_PATH).read().strip())
    except (OSError, ValueError):
        offset = 0
    print("telegram poller started", flush=True)
    while True:
        try:
            updates = _tg("getUpdates", offset=offset, timeout=30,
                          allowed_updates=["callback_query"])
        except Exception as exc:
            print(f"getUpdates failed: {exc}", flush=True)
            time.sleep(15)
            continue
        for update in updates:
            offset = update.get("update_id", offset) + 1
            if update.get("callback_query"):
                _handle_callback(update["callback_query"])
        if updates:
            try:
                with open(TG_OFFSET_PATH, "w") as fh:
                    fh.write(str(offset))
            except OSError:
                pass


@app.on_event("startup")
def start_telegram_poller():
    if tg_token():
        threading.Thread(target=_poll_loop, daemon=True).start()
    else:
        print("telegram poller off: no bot token mounted", flush=True)


@app.get("/settings")
def settings_get():
    return settings_read()


@app.put("/settings")
def settings_put(payload: dict):
    data = settings_read()
    for key, value in payload.items():
        if key not in SETTINGS_LIMITS:
            continue
        lo, hi = SETTINGS_LIMITS[key]
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{key} must be an integer")
        if not lo <= value <= hi:
            raise HTTPException(status_code=400, detail=f"{key} must be {lo}..{hi}")
        data[key] = value
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, SETTINGS_PATH)   # readers never see a half-written file
    return data


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
