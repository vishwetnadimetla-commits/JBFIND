#!/usr/bin/env python3
"""AutoApply: turn an APPLY decision into a ready-to-send application package.

For each job it produces, in Google Drive under 03_APPLICATIONS/<date>_<company>/:
  the tailored CV (reuses tools/resume_tailor, master template preserved)
  a factual cover-letter draft written by the local LLM
  and it appends one row to the APPLICATIONS sheet.

It never submits anything. A human opens the link and sends it.

    python3 tools/autoapply.py --dry-run          # list the queue
    python3 tools/autoapply.py --job-file job.json # one job (testable)
    python3 tools/autoapply.py                    # process the APPLY queue
"""
import argparse
import datetime as _dt

# Every date the user sees (Drive folder names, APPLICATION rows) is IST.
# The VM container runs UTC, which is a day behind and silently forks folders.
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def today():
    return _dt.datetime.now(IST).date()

import io
import json
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from resume_tailor import (DOCX_MIME, FOLDER_MIME, OLLAMA_MODEL, OLLAMA_URL,  # noqa: E402
                           VISHWET_PARENT, docx_text, download, drive, ensure_folder,
                           fetch_master, put_file, slug, tailor_job, upload)

REPO = pathlib.Path(__file__).resolve().parent.parent
SA_FILE = REPO / "keys" / "google-service-account.json"
SHEET_ID = "1voOTzO8auKbgrP4J4dPACnwf9Tg2MsgUSCU9w2-mPFY"
APPLICATIONS_SHEET = "APPLICATIONS"
APPLY_FOLDER = "03_APPLICATIONS"
FOLLOW_UP_DAYS = 7

APPLY_HEADERS = ["Job_ID", "Company", "Title", "Application_Date", "Application_Status",
                 "Current_Stage", "Next_Action", "Follow_Up_Date", "Application_URL",
                 "Resume_Version", "Notes", "Last_Status_Update"]


def sheets():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        str(SA_FILE), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds)


def ready_jobs():
    """APPLY rows that are still eligible: not rejected, not already packaged."""
    svc = sheets()
    vals = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="JOBS!A:X").execute().get("values", [])
    if not vals:
        return []
    head = vals[0]
    done = set()
    try:
        for row in svc.spreadsheets().values().get(
                spreadsheetId=SHEET_ID, range=f"{APPLICATIONS_SHEET}!A:C").execute().get("values", [])[1:]:
            row = (row + ["", "", ""])[:3]
            if row[0]:
                done.add(row[0])
            if row[1] and row[2]:
                done.add(f"{row[1].strip().lower()}|{row[2].strip().lower()}")
    except Exception:
        pass
    out = []
    for row in vals[1:]:
        job = {h: (row[i] if i < len(row) else "") for i, h in enumerate(head)}
        if job.get("Decision") != "APPLY" or job.get("Experience_Status") == "REJECT":
            continue
        key = f"{(job.get('Company') or '').strip().lower()}|{(job.get('Title') or '').strip().lower()}"
        if job.get("Job_ID") in done or key in done:
            continue
        out.append(job)
    return out


def cover_letter(master, job, url=OLLAMA_URL, model=OLLAMA_MODEL):
    # job arrives with normalized lowercase keys from the sheet/dict; the prompt
    # reads TitleCase, so an empty string here silently fed the LLM no job at all.
    job = {"Company": job.get("company", ""), "Title": job.get("title", ""),
           "Location": job.get("location", ""), "JD": job.get("jd", "")}
    ask = (
        "JOB:\n" + json.dumps(job, ensure_ascii=False) +
        "\n\nMASTER RESUME:\n" + master +
        "\n\nWrite a short application email. First line 'Subject: ...'. Then 120-160 words "
        "of body. Reference only experience that appears in the master resume, name 2-3 skills "
        "the job asks for, no placeholders, no invented projects or numbers, no salary or "
        "availability claims. Plain text only."
    )
    body = json.dumps({
        "model": model, "stream": False,
        # see resume_tailor._call_llm: grammar-constrained JSON is minutes-slow here
        "options": {"num_predict": 400, "temperature": 0.3},
        "keep_alive": "30m",
        "messages": [
            {"role": "system", "content":
             "You write factual, concise job application emails for a mobile developer "
             "(Flutter, Android Kotlin/Java, iOS Swift, 3+ years). Never invent anything."},
            {"role": "user", "content": ask}],
    }).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    letter = json.load(urllib.request.urlopen(req, timeout=600))["message"]["content"].strip()
    if not letter.lower().startswith("subject:"):
        letter = f"Subject: Application - {job.get('Title', 'Role')} at {job.get('Company', '')}\n\n{letter}"
    return letter


def log_application(job, row):
    """Write to the first free row, and never twice for the same job.

    --job-file bypasses the queue, so this is the only place that can stop a
    repeated run from appending duplicate rows for one job.
    """
    svc = sheets()
    try:
        svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{APPLICATIONS_SHEET}!A1").execute()
    except Exception:
        svc.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={
            "requests": [{"addSheet": {"properties": {"title": APPLICATIONS_SHEET}}}]}).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{APPLICATIONS_SHEET}!A1:L",
        valueInputOption="RAW", body={"values": [APPLY_HEADERS]}).execute()
    rows = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{APPLICATIONS_SHEET}!A:C").execute().get("values", [])
    key = f"{(row[1] or '').strip().lower()}|{(row[2] or '').strip().lower()}"
    free = None
    for i, r in enumerate(rows[1:], start=2):
        r = (r + ["", ""])[:3]
        if not (r[1] or "").strip():
            free = free or i                       # first gap, but keep scanning
        elif f"{r[1].strip().lower()}|{(r[2] or '').strip().lower()}" == key:
            return today()                          # already logged; never duplicate
    free = free if free is not None else len(rows) + 1
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{APPLICATIONS_SHEET}!A{free}:L{free}",
        valueInputOption="RAW", body={"values": [row]}).execute()
    return today()


def package(job, offline=False, dry_run=False, ollama_url=OLLAMA_URL):
    """Tailored CV + cover letter into 03_APPLICATIONS/<date>_<company>/."""
    company = job.get("company") or job.get("Company", "")
    title = job.get("title") or job.get("Title", "")
    normalized = {"company": company, "title": title, "jd": job.get("jd") or job.get("JD", ""),
                  "location": job.get("Location", ""),
                  "experience": job.get("Experience_Required", ""),
                  "job_id": job.get("job_id") or job.get("Job_ID", ""),
                  "job_url": job.get("job_url") or job.get("Job_URL", "")}
    changes, _ = tailor_job(normalized, offline=offline, upload_to_drive=True,
                            ollama_url=ollama_url)
    master_bytes, _ = fetch_master()
    master = docx_text(master_bytes)
    letter = ("Cover letter generation skipped (offline mode)." if offline
              else cover_letter(master, normalized, ollama_url))

    when = today().isoformat()
    svc = drive(write=True)
    folder = ensure_folder(svc, ensure_folder(svc, VISHWET_PARENT, APPLY_FOLDER),
                           f"{when}_{slug(company)}")
    letter_link = put_file(svc, folder, "cover_letter.txt", "text/plain", letter.encode())
    put_file(svc, folder, "changes.json", "application/json",
             json.dumps(changes, indent=2).encode())
    # reuse the already-uploaded tailored CV instead of storing a second copy
    tail = ensure_folder(svc, VISHWET_PARENT, "01_TAILORED_RESUMES")
    resume_id = ""
    for f in svc.files().list(q=f"'{tail}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false",
                              fields="files(id,name)").execute().get("files", []):
        if f["name"] == changes["resume_version"]:
            for cv in svc.files().list(q=f"'{f['id']}' in parents and trashed = false",
                                       fields="files(id,name,webViewLink)").execute().get("files", []):
                if cv["name"].endswith(".docx"):
                    resume_id = cv.get("webViewLink", "")
                    break

    links = {"folder": f"https://drive.google.com/drive/folders/{folder}",
             "cover_letter": letter_link, "resume": resume_id or ""}
    if dry_run:
        return {"job": title, "company": company, "dry_run": True, "links": links}
    now = _dt.datetime.now(IST).isoformat(timespec="seconds")
    row = [normalized["job_id"], company, title, when, "PREPARED", "resume_ready",
           "Review draft and submit", str(today() + _dt.timedelta(days=FOLLOW_UP_DAYS)),
           normalized["job_url"], changes["resume_version"],
           f"cover letter: {links['cover_letter']}", now]
    log_application(normalized, row)
    return {"job": title, "company": company, "status": "PREPARED", "links": links,
            "apply_url": normalized["job_url"]}


def main():
    ap = argparse.ArgumentParser(description="AutoApply: build application packages")
    ap.add_argument("--job-file", help="single job JSON (same shape as resume_tailor)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="list the queue, write nothing")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--ollama-url", default=OLLAMA_URL)
    a = ap.parse_args()

    if a.job_file:
        jobs = json.loads(pathlib.Path(a.job_file).read_text())
        jobs = jobs if isinstance(jobs, list) else [jobs]
    else:
        jobs = ready_jobs()
    if a.dry_run:
        print(json.dumps([{k: j.get(k) for k in ("Job_ID", "Company", "Title", "Job_URL",
                                                 "Experience_Status", "Skill_Match")}
                          for j in jobs], indent=2))
        return
    out = []
    for job in jobs[:a.limit]:
        try:
            out.append(package(job, offline=a.offline, ollama_url=a.ollama_url))
        except SystemExit:
            raise
        except Exception as exc:
            out.append({"job": job.get("Title", ""), "company": job.get("Company", ""),
                        "error": str(exc)[:200]})
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
