#!/usr/bin/env python3
"""Tailor the master resume to one job and save the artifacts to Google Drive.

The tailored CV is a *copy of the master DOCX with text edited in place*, so the
template, fonts, margins, section headings and paragraph count stay identical.
Only three kinds of edit happen, all factual:
  1. reorder skills inside the existing SKILLS paragraphs
  2. optionally rewrite the PROFILE paragraph (re-wrapped to the same line count)
  3. nothing else - dates, employers, titles, achievements are never touched

Auth uses the service account for reads and the user OAuth token for writes
(service accounts have no Drive storage quota).

    python3 tools/resume_tailor.py --job-file job.json          # LLM + Drive
    python3 tools/resume_tailor.py --job-file jobs.json --offline --no-upload
    python3 tools/resume_tailor.py --selftest
"""
import argparse
import copy
import datetime as _dt

# Every date the user sees (Drive folder names, APPLICATION rows) is IST.
# The VM container runs UTC, which is a day behind and silently forks folders.
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def today():
    return _dt.datetime.now(IST).date()

import io
import json
import os
import pathlib
import re
import sys
import textwrap
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
KEYS = REPO / "keys"
SA_FILE = KEYS / "google-service-account.json"
CACHE = REPO / ".cache"
OUT = REPO / "out" / "tailored"
PROMPT_FILE = REPO / "agent" / "prompts" / "resume_tailor.md"

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
VISHWET_PARENT = "1S9aMzkWeAKDqkprkpzgMavC-rTmUwPMJ"  # "Vishwet" folder
MASTER_FOLDER_NAME = "MASTER RESUME"
MASTER_DOCX_NAME = "Vishwet_Nadimetla_Mobile_Developer.docx"
TAILORED_ROOT_NAME = "01_TAILORED_RESUMES"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GDOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"
EMDASH = "\u2014"


# --------------------------------------------------------------------------- util
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "job").lower()).strip("-") or "job"


def prompt_text():
    return PROMPT_FILE.read_text() if PROMPT_FILE.exists() else \
        "Tailor the resume factually. Never invent anything. Record every change."


def jd_of(job):
    return " ".join(str(job.get(k, "")) for k in ("title", "jd", "skills")).lower()


# --------------------------------------------------------------------------- drive
def drive(write=False):
    """Drive client. Prefers the user OAuth token for writes (service accounts
    have no storage quota); falls back to the service account for reads."""
    tok = KEYS / "google-drive-token.json"
    if write or tok.exists():
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            creds = Credentials.from_authorized_user_file(str(tok), DRIVE_SCOPES)
            if not creds.valid:
                creds.refresh(Request())
            return build("drive", "v3", credentials=creds)
        except Exception as e:  # noqa: BLE001
            if write:
                raise SystemExit(
                    "Drive upload needs the user OAuth token (service accounts have no "
                    "storage quota). Re-run: python3 tools/drive_oauth.py\n  (" + str(e) + ")")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(str(SA_FILE), scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds)


def find_child(svc, parent, name, folder=None):
    q = f"'{parent}' in parents and name = '{name}' and trashed = false"
    if folder:
        q += f" and mimeType = '{FOLDER_MIME}'"
    files = svc.files().list(q=q, fields="files(id,name)", pageSize=10).execute().get("files", [])
    return files[0]["id"] if files else None


def ensure_folder(svc, parent, name):
    fid = find_child(svc, parent, name, folder=True)
    if fid:
        return fid
    return svc.files().create(body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                              fields="id").execute()["id"]


def download(svc, file_id):
    return svc.files().get_media(fileId=file_id).execute()


def fetch_master(svc=None):
    """Return (master_docx_bytes, profile_dict). Cached locally when Drive is offline."""
    CACHE.mkdir(exist_ok=True)
    doc_f, prof_f = CACHE / "master.docx", CACHE / "candidate_profile.json"
    if doc_f.exists() and prof_f.exists():
        try:
            svc = svc or drive()
            mid = find_child(svc, VISHWET_PARENT, MASTER_FOLDER_NAME, folder=True)
            data = download(svc, find_child(svc, mid, MASTER_DOCX_NAME))
            doc_f.write_bytes(data)
            return data, _fetch_profile(svc, mid, prof_f)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[master] Drive unavailable ({e}); using cache\n")
            return doc_f.read_bytes(), json.loads(prof_f.read_text())
    svc = svc or drive()
    mid = find_child(svc, VISHWET_PARENT, MASTER_FOLDER_NAME, folder=True)
    if not mid:
        raise RuntimeError(f"Drive folder '{MASTER_FOLDER_NAME}' not found")
    data = download(svc, find_child(svc, mid, MASTER_DOCX_NAME))
    doc_f.write_bytes(data)
    return data, _fetch_profile(svc, mid, prof_f)


def _fetch_profile(svc, mid, prof_f):
    data = json.loads(download(svc, find_child(svc, mid, "candidate_profile.json")).decode())
    prof_f.write_text(json.dumps(data, indent=2))
    return data


def docx_text(data):
    import docx
    return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)


# --------------------------------------------------------------------------- edits
def _reorder_items(items, jd):
    """Stable sort: skills named in the JD float to the front. Order of the rest kept."""
    items = [s.strip() for s in items if s and s.strip()]
    return sorted(items, key=lambda s: 0 if s.lower() in jd else 1)


def reorder_skills_text(text, jd):
    """Reorder comma-separated skills inside the SKILLS block. Returns (text, emphasized)."""
    lines = text.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().upper() == "SKILLS")
        end = next(i for i in range(start + 1, len(lines))
                   if lines[i].strip() and lines[i].strip().upper() == lines[i].strip())
    except StopIteration:
        return text, []
    emphasized = []
    for i in range(start + 1, end):
        if EMDASH not in lines[i]:
            continue
        cat, rest = lines[i].split(EMDASH, 1)
        ordered = _reorder_items(rest.split(","), jd)
        emphasized += [s for s in ordered if s.lower() in jd]
        lines[i] = cat + EMDASH + " " + ", ".join(ordered)
    return "\n".join(lines), emphasized


def _set_runs(paragraph, text):
    """Replace paragraph text but keep the first run's formatting."""
    runs = paragraph.runs
    if not runs:
        if text:
            paragraph.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r._element.getparent().remove(r._element)


def _fit_lines(text, n, base_width):
    """Wrap text into exactly n lines, so the block keeps the master's line count."""
    for width in range(base_width, 30, -2):
        lines = textwrap.wrap(text, width) or [""]
        if len(lines) == n:
            return lines
    return textwrap.wrap(text, base_width) or [""]


def set_summary_text(data, summary, jd):
    """Rewrite the PROFILE block, re-wrapped to the master's line count.

    Returns (docx_bytes, applied). applied is False when the rewrite would not
    fit the existing layout - the master profile is then left untouched.
    """
    import docx
    from docx.text.paragraph import Paragraph
    doc = docx.Document(io.BytesIO(data))
    paras = doc.paragraphs
    try:
        head = next(i for i, p in enumerate(paras) if p.text.strip().upper() == "PROFILE")
    except StopIteration:
        return data, False
    body = [i for i in range(head + 1, len(paras))
            if paras[i].text.strip() and paras[i].text.strip().upper() != paras[i].text.strip()]
    if not body:
        return data, False
    width = max(len(paras[i].text) for i in body)
    lines = _fit_lines(summary, len(body), width)
    if len(lines) != len(body):
        return data, False
    for slot, i in enumerate(body):
        _set_runs(paras[i], lines[slot])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), True


def reorder_skills_docx(data, jd):
    """Reorder skills in place. Returns (docx_bytes, emphasized, reordered_paras)."""
    import docx
    doc = docx.Document(io.BytesIO(data))
    paras = doc.paragraphs
    try:
        start = next(i for i, p in enumerate(paras) if p.text.strip().upper() == "SKILLS")
    except StopIteration:
        return data, [], 0
    end = len(paras)
    for i in range(start + 1, len(paras)):
        if paras[i].text.strip() and paras[i].text.strip().upper() == paras[i].text.strip():
            end = i
            break
    emphasized, touched = [], 0
    for i in range(start + 1, end):
        if EMDASH not in paras[i].text:
            continue
        cat, rest = paras[i].text.split(EMDASH, 1)
        ordered = _reorder_items(rest.split(","), jd)
        if ordered != [s.strip() for s in rest.split(",")]:
            touched += 1
        emphasized += [s for s in ordered if s.lower() in jd]
        _set_runs(paras[i], cat + EMDASH + " " + ", ".join(ordered))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), emphasized, touched


# --------------------------------------------------------------------------- fidelity
def fidelity_problems(master_bytes, new_bytes):
    """Anything that would make the tailored CV look different from the master."""
    import docx
    a = docx.Document(io.BytesIO(master_bytes))
    b = docx.Document(io.BytesIO(new_bytes))
    problems = []
    if len(a.sections) != len(b.sections):
        problems.append(f"section count {len(a.sections)} -> {len(b.sections)}")
    for i, (sa, sb) in enumerate(zip(a.sections, b.sections)):
        for attr in ("left_margin", "right_margin", "top_margin", "bottom_margin",
                     "page_width", "page_height", "orientation"):
            if getattr(sa, attr) != getattr(sb, attr):
                problems.append(f"section {i} {attr} changed")
    sa = [p.style.name for p in a.paragraphs]
    sb = [p.style.name for p in b.paragraphs]
    if sa != sb:
        problems.append(f"paragraph styles differ ({len(sa)} vs {len(sb)} paragraphs)")

    def head_fonts(d):
        out = []
        for p in d.paragraphs[:12]:
            for r in p.runs[:2]:
                out.append((r.font.name, r.font.size.pt if r.font.size else None, r.bold))
        return out
    if head_fonts(a) != head_fonts(b):
        problems.append("header run fonts differ")
    return problems


# --------------------------------------------------------------------------- LLM
def _loads_json(raw):
    """Parse the LLM's JSON, tolerating a num_predict cut-off mid-string/object."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"```$", "", raw.strip())
    start = raw.find("{")
    if start > 0:
        raw = raw[start:]
    for attempt in _repairs(raw):
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            continue
    raise ValueError(f"no JSON object in LLM output: {raw[:200]!r}")


def _repairs(s):
    """Yield progressively repaired forms of a cut-off JSON object."""
    t = s
    for _ in range(4):
        yield t
        yield _close_truncated(t)
        n = _peel(t)
        if n == t:
            return
        t = n


def _peel(s):
    """Drop a dangling trailing fragment: `"key":`, a bare `"key"`, or `"unclosed."""
    t = re.sub(r',?\s*"[^"]*"\s*:\s*$', "", s.rstrip())   # "key": with no value
    t = re.sub(r',?\s*"[^"]*"\s*$', "", t.rstrip())       # bare "key"
    return re.sub(r',?\s*"[^"]*$', "", t.rstrip())        # unterminated "key or value


def _close_truncated(s):
    """Repair a cut-off JSON object.

    A string cut mid-value is DROPPED, not closed off -- half a profile summary
    written into a resume is worse than no rewrite at all.
    """
    stack, in_str, esc, str_start = [], False, False, 0
    for i, ch in enumerate(s):
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            if in_str:
                in_str = False
            else:
                in_str, str_start = True, i
        elif not in_str:
            if ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
    if in_str:                       # unterminated string: drop it and its key
        out = s[:str_start]
    else:
        out = s
    out = out.rstrip().rstrip(",")
    return out + "".join(reversed(stack))


def _call_llm(master, job, url=OLLAMA_URL, model=OLLAMA_MODEL):
    ask = (
        "JOB:\n" + json.dumps(job, ensure_ascii=False) + "\n\n"
        "MASTER RESUME:\n" + master + "\n\n"
        "Return ONE JSON object with keys:\n"
        '  "profile_summary": the rewritten PROFILE paragraph as one plain string '
        "(same facts, same claims, only reordered/retargeted at this job). Use the "
        "EXISTING text verbatim if you cannot improve it.\n"
        '  "summary_adjustment": one line describing what you changed\n'
        '  "skills_emphasized": list of skills you put first\n'
        '  "keywords_added": list of JD keywords already supported by the resume\n'
        '  "experience_adjustments": list (bullets may only be reordered, never rewritten)\n'
        '  "changes_not_made": list\n'
        '  "tweak_level": int 0-100\n'
        "Do NOT return a full resume; the tool edits the master DOCX itself.\n"
        "Be terse: at most 5 short items per list, no prose before or after the JSON."
    )
    body = json.dumps({
        "model": model, "stream": False,
        # no format:"json" -- grammar-constrained decoding on this CPU-only box
        # costs minutes per call. num_predict bounds the ramble instead, and
        # _loads_json salvages the output if the cap still cuts it off.
        # keep_alive holds the 5.1 GB weights in RAM: ollama evicts after 5 min,
        # and a cold call measured 156s vs 28s warm.
        "options": {"num_predict": 400, "temperature": 0.2},
        "keep_alive": "30m",
        "messages": [{"role": "system", "content": prompt_text()},
                     {"role": "user", "content": ask}],
    }).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req, timeout=600))
    return _loads_json(resp["message"]["content"])


def offline_decision(master, job):
    """No-LLM decision: only the deterministic skill reorder, no profile rewrite."""
    jd = jd_of(job)
    _, emphasized = reorder_skills_text(master, jd)
    return {"profile_summary": None, "summary_adjustment": "unchanged (offline)",
            "skills_emphasized": emphasized, "keywords_added": [],
            "experience_adjustments": [], "tweak_level": 0,
            "changes_not_made": ["dates", "employers", "titles", "achievements", "content"]}


# --------------------------------------------------------------------------- guard
def factuality_check(master, md):
    """Fail loud on anything tailoring must never change."""
    problems = []
    m = set(re.findall(r"\b\d{2}/\d{4}\b", master))
    t = set(re.findall(r"\b\d{2}/\d{4}\b", md))
    if not t <= m:
        problems.append(f"new dates introduced: {sorted(t - m)}")
    for token in ("@gmail.com", "918956389393"):
        if token in master and token not in md:
            problems.append(f"contact token removed: {token}")
    for token in ("Appristine", "Enthral.ai", "06/2023", "08/2024"):
        if token in master and token not in md:
            problems.append(f"fact dropped: {token}")
    return problems


# --------------------------------------------------------------------------- drive out
def put_file(svc, folder, name, mime, data, gdoc=False):
    """Upload once. A re-run updates nothing and never creates a duplicate."""
    from googleapiclient.http import MediaIoBaseUpload
    existing = find_child(svc, folder, name)
    if existing:
        return svc.files().get(fileId=existing, fields="webViewLink").execute().get("webViewLink", "")
    body = {"name": name, "parents": [folder]}
    if gdoc:
        body["mimeType"] = GDOC_MIME
    f = svc.files().create(body=body,
                           media_body=MediaIoBaseUpload(io.BytesIO(data), mimetype=mime),
                           fields="id,webViewLink").execute()
    return f.get("webViewLink", "")


def upload(company, when, md, changes, docx_bytes):
    svc = drive(write=True)
    root = ensure_folder(svc, VISHWET_PARENT, TAILORED_ROOT_NAME)
    folder = ensure_folder(svc, root, f"{when}_{slug(company)}")
    base = f"Vishwet_Nadimetla_{slug(company)}"
    links = {"folder": f"https://drive.google.com/drive/folders/{folder}"}
    links[f"{base}.docx"] = put_file(svc, folder, f"{base}.docx", DOCX_MIME, docx_bytes)
    links["tailored.md"] = put_file(svc, folder, "tailored.md", "text/markdown", md.encode())
    links["changes.json"] = put_file(svc, folder, "changes.json", "application/json",
                                     json.dumps(changes, indent=2).encode())
    links["google_doc"] = put_file(svc, folder, base, "text/markdown", md.encode(), gdoc=True)
    return links


# --------------------------------------------------------------------------- main
def tailor_job(job, offline=False, upload_to_drive=True, ollama_url=OLLAMA_URL):
    master_bytes, _profile = fetch_master()
    master_text = docx_text(master_bytes)
    decision = offline_decision(master_text, job) if offline else _call_llm(master_text, job, ollama_url)

    jd = jd_of(job)
    new_bytes, emphasized, touched = reorder_skills_docx(master_bytes, jd)
    applied = False
    if decision.get("profile_summary"):
        candidate, applied = set_summary_text(new_bytes, decision["profile_summary"], jd)
        if applied:
            new_bytes = candidate

    fidelity = fidelity_problems(master_bytes, new_bytes)
    if fidelity:
        raise SystemExit("TEMPLATE FIDELITY FAILED: " + "; ".join(fidelity))

    new_text = docx_text(new_bytes)
    problems = factuality_check(master_text, new_text)
    if problems:
        raise SystemExit("FACTUALITY CHECK FAILED: " + "; ".join(problems))

    when = today().isoformat()
    changes = {
        "job_id": job.get("job_id") or job.get("Job_ID", ""),
        "company": job.get("company", ""), "title": job.get("title", ""),
        "resume_version": f"{when}_{slug(job.get('company'))}",
        "skills_emphasized": emphasized or decision.get("skills_emphasized", []),
        "skills_paragraphs_reordered": touched,
        "summary_adjustment": decision.get("summary_adjustment", ""),
        "summary_rewritten": applied,
        "experience_adjustments": decision.get("experience_adjustments", []),
        "keywords_added": decision.get("keywords_added", []),
        "changes_not_made": decision.get("changes_not_made", []),
        "tweak_level": decision.get("tweak_level", 0),
        "template": "master copy, text edited in place",
        "template_fidelity": "PASS", "factuality_check": "PASS",
        "drive_file": "", "created_date": when, "review_status": "PENDING",
    }

    d = OUT / changes["resume_version"]
    d.mkdir(parents=True, exist_ok=True)
    name = f"Vishwet_Nadimetla_{slug(job.get('company'))}.docx"
    (d / name).write_bytes(new_bytes)
    (d / "tailored.md").write_text(new_text)
    if upload_to_drive:
        links = upload(job.get("company"), when, new_text, changes, new_bytes)
        changes["drive_file"] = links.get("google_doc", "")
        changes["drive_links"] = links
    (d / "changes.json").write_text(json.dumps(changes, indent=2))
    return changes, d


def _job_from_args(a):
    if a.job_file:
        data = json.loads(pathlib.Path(a.job_file).read_text())
        return data if isinstance(data, list) else [data]
    jd = pathlib.Path(a.jd_file).read_text() if a.jd_file else (a.jd or "")
    return [{"company": a.company or "Unknown", "title": a.title or "Role",
             "jd": jd, "job_id": a.job_id or ""}]


def selftest():
    master, _ = fetch_master()
    text = docx_text(master)
    jd = "flutter dart kotlin jetpack compose mvvm"
    job = {"company": "Test Co", "title": "Flutter Developer", "jd": jd}

    new_bytes, emphasized, touched = reorder_skills_docx(master, jd)
    assert touched > 0, "no skills paragraph reordered"
    assert "Flutter" in emphasized, emphasized
    assert not fidelity_problems(master, new_bytes), fidelity_problems(master, new_bytes)
    assert not factuality_check(text, docx_text(new_bytes))

    # a fabricated profile rewrite must be rejected when it invents a date
    assert factuality_check(text, text.replace("07/2020", "07/2019"))
    # summary rewrite keeps the line count
    long_summary = "Rewritten summary. " * 12
    got, applied = set_summary_text(master, long_summary, jd)
    if applied:
        assert not fidelity_problems(master, got), fidelity_problems(master, got)

    # LLM JSON must survive a num_predict cut-off at any point
    whole = ('{"profile_summary": "Flutter dev.", "summary_adjustment": "reordered",'
             ' "skills_emphasized": ["Flutter", "Kotlin"], "keywords_added": ["mvvm"],'
             ' "experience_adjustments": ["bullet order only"], "changes_not_made": [],'
             ' "tweak_level": 40}')
    assert _loads_json(whole)["tweak_level"] == 40
    for cut in range(2, len(whole)):
        salvaged = _loads_json(whole[:cut])
        assert isinstance(salvaged, dict), f"cut at {cut} -> {salvaged!r}"
        # at a key boundary nothing that was already emitted may be lost;
        # a cut inside a string is allowed to drop just that key.
        if whole[cut - 1] in ",}" and cut > whole.index('", '):
            assert "profile_summary" in salvaged, f"cut at {cut} lost the summary"
    assert _loads_json('```json\n' + whole + '\n```')["tweak_level"] == 40
    assert _loads_json("Sure! Here you go:\n" + whole)["tweak_level"] == 40

    print(f"SELFTEST PASS: reorder({touched} paras), fidelity, factuality, "
          f"summary={applied}, json-salvage={len(whole)} cut points")


def main():
    ap = argparse.ArgumentParser(description="Tailor resume to a job -> Google Drive")
    ap.add_argument("--job-file"); ap.add_argument("--jd-file"); ap.add_argument("--jd")
    ap.add_argument("--company"); ap.add_argument("--title"); ap.add_argument("--job-id")
    ap.add_argument("--offline", action="store_true", help="no LLM; skills reorder only")
    ap.add_argument("--no-upload", action="store_true", help="write locally only")
    ap.add_argument("--ollama-url", default=OLLAMA_URL)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    results = []
    for job in _job_from_args(a):
        try:
            changes, d = tailor_job(job, offline=a.offline,
                                    upload_to_drive=not a.no_upload, ollama_url=a.ollama_url)
            results.append({"job": job.get("title", ""), "company": job.get("company", ""),
                            "local": str(d), "tweak_level": changes["tweak_level"],
                            "template_fidelity": changes["template_fidelity"],
                            "drive": changes.get("drive_links", {}).get("folder", "-")})
        except SystemExit:
            raise
        except Exception as exc:
            results.append({"job": job.get("title", ""), "company": job.get("company", ""),
                            "error": str(exc)[:200]})
    print(json.dumps(results if len(results) > 1 else results[0], indent=2))


if __name__ == "__main__":
    main()
