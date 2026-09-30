#!/usr/bin/env python3
"""evaluate.py — the autoresearch metric.

Compares what the pipeline actually scored against the human labels in
test_jobs/ and prints ONE number: mean distance from the labels, 0 = perfect,
100 = worst, 1000 = the run did not produce usable output.

    python3 evaluate.py [execution_id] [--verbose]

With no execution_id it scores the most recent execution of the test workflow.
The n8n plumbing (login, the 2.x flatted execution-data format) lives here too
so experiment.py can import it instead of duplicating it.
"""

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

N8N_URL = os.environ.get("N8N_URL", "https://n8n.jbfind.duckdns.org")
TEST_WF = os.environ.get("JBFIND_TEST_WF_ID", "jbfind-test-workflow")
ROOT = os.path.dirname(os.path.abspath(__file__))

# how much each kind of error is worth, in points of the 0-100 score
W_GATE, W_FIELDS, W_RECOMMENDED, W_RANK, PARSE_PENALTY = 30.0, 40.0, 20.0, 10.0, 15.0


def load_dotenv():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))


class N8n:
    def __init__(self):
        load_dotenv()
        self._op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._min_left = 30

    def call(self, path, payload=None, method=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            N8N_URL + path, data=body,
            method=method or ("POST" if body else "GET"),
            headers={"Content-Type": "application/json"})
        try:
            with self._op.open(req, timeout=180) as r:
                text = r.read().decode()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"n8n {e.code} on {path}: {e.read().decode()[:200]}")
        return json.loads(text) if text.strip() else {}

    def login(self):
        import time as _t
        email = os.environ.get("N8N_LOGIN_EMAIL", "")
        password = os.environ.get("N8N_LOGIN_PASSWORD", "")
        if not email or not password:
            raise SystemExit("set N8N_LOGIN_EMAIL / N8N_LOGIN_PASSWORD in .env")
        for attempt in range(6):
            try:
                self.call("/rest/login",
                          {"emailOrLdapLoginId": email, "password": password})
                self._min_left = 30
                return
            except SystemExit as ex:
                if "429" not in str(ex):
                    raise
                self._min_left += 30
                print(f"login rate-limited, waiting {self._min_left}s "
                      f"(attempt {attempt + 1}/6)", flush=True)
                _t.sleep(self._min_left)
        raise SystemExit("n8n login still rate-limited after 6 attempts")

    def execution(self, exec_id):
        ex = self.call(f"/rest/executions/{exec_id}?includeData=true")["data"]
        return ex, unpack(ex.get("data"))

    def latest_id(self, workflow_id=TEST_WF, after=0):
        ids = []
        for page in (1, 2):
            res = self.call(f"/rest/executions?limit=100&page={page}")
            rows = (res.get("data") or {}).get("results", [])
            for r in rows:
                if r.get("workflowId") == workflow_id and int(r["id"]) > after:
                    ids.append(int(r["id"]))
            if not rows:
                break
        return max(ids) if ids else None

    def workflow(self, wf_id=TEST_WF):
        return self.call(f"/rest/workflows/{wf_id}")["data"]

    def patch_workflow(self, wf_id, wf):
        live = self.workflow(wf_id)
        body = {"id": wf_id, "name": wf.get("name", live["name"]),
                "nodes": wf["nodes"], "connections": wf["connections"],
                "settings": wf.get("settings", {}), "versionId": live.get("versionId")}
        self.call(f"/rest/workflows/{wf_id}", body, "PATCH")
        return live.get("versionId")

    def set_active(self, on, wf_id=TEST_WF):
        vid = self.workflow(wf_id).get("versionId")
        self.call(f"/rest/workflows/{wf_id}/{'activate' if on else 'deactivate'}",
                  {"versionId": vid}, "POST")
        return self.workflow(wf_id).get("active")


def unpack(data):
    """n8n 2.x hands back execution data as a JSON string wrapping a flatted
    index-map array, or as that array directly. Normalise both to the dict."""
    store = data
    if isinstance(store, str):
        try:
            store = json.loads(store)
        except ValueError:
            return {}
    if isinstance(store, list):
        if store and isinstance(store[0], dict) and "resultData" in store[0]:
            def rf(x, depth=0):
                if depth > 60:
                    return x
                if isinstance(x, str):
                    s = x.strip()
                    if s.isdigit() and int(s) < len(store):
                        return rf(store[int(s)], depth + 1)
                    try:
                        return rf(json.loads(s), depth + 1)
                    except ValueError:
                        return x
                if isinstance(x, list):
                    return [rf(v, depth + 1) for v in x]
                if isinstance(x, dict):
                    return {k: rf(v, depth + 1) for k, v in x.items()}
                return x
            return rf(store[0])
    return store if isinstance(store, dict) else {}


def scored_items(execution):
    """All items a node produced. The LLM node emits one run per input item
    (batchSize 1), so the items are spread across runs, not stacked in one."""
    run_data = (execution.get("resultData") or {}).get("runData") or {}
    for name in ("Parse + Score", "Output"):
        runs = run_data.get(name)
        if not runs:
            continue
        items = []
        for run in runs:
            for branch in ((run.get("data") or {}).get("main") or []):
                if isinstance(branch, list):
                    items.extend(i.get("json", {}) for i in branch)
        if items:
            return items
    return []


def norm_gate(value):
    """The model spells the REVIEW state several ways (REVIEW,
    REVIEW_EXPERIENCE_REQUIRED, EXPERIENCE_REVIEW_REQUIRED). They mean the
    same thing, so a vocabulary difference is not a reasoning error."""
    v = str(value or "").strip().upper()
    if "REVIEW" in v:
        return "REVIEW"
    if v in ("PASS", "REJECT", "MISSING_DATA"):
        return v
    return v or "MISSING_DATA"


def expected_order(items):
    """The order the labels imply: rejected last, then best skill/role/location.
    Mirrors ranking.sort_by in agent/weights.json."""
    def key(exp):
        gate_ok = norm_gate(exp.get("experience_status")) != "REJECT"
        return (0 if gate_ok else 1,
                -(exp.get("skill_match") or 0),
                -(exp.get("role_match") or 0),
                -(exp.get("location_match") or 0))
    return sorted(range(len(items)), key=lambda i: key(items[i].get("expected") or {}))


JOB_FIELDS = ("company", "title", "location", "experience", "jd")
LABEL_FIELDS = ("experience_status", "role_match", "skill_match", "location_match", "recommended")


def check_labels(where, expected):
    """Guard the ground truth itself. A mislabelled job silently poisons every
    experiment, so refuse to score rather than report a meaningless number."""
    for field in LABEL_FIELDS:
        if field not in expected:
            raise SystemExit(f"{where}: label is missing `{field}`")
    if expected["experience_status"] not in ("PASS", "REJECT", "REVIEW", "MISSING_DATA"):
        raise SystemExit(f"{where}: bad experience_status {expected['experience_status']!r}")
    # ponytail: the labelling rule, in one place so a re-label is mechanical.
    # recommended must follow it exactly or the metric rewards a broken gate.
    # Location counts: 92% of real postings carry no experience info, so
    # location is the only hard filter left, and a job in France is not
    # actionable for a candidate based in Pune.
    should = (expected["experience_status"] != "REJECT"
              and expected["role_match"] >= 50 and expected["skill_match"] >= 60
              and expected["location_match"] >= 30)
    if should != bool(expected["recommended"]):
        raise SystemExit(f"{where}: recommended={expected['recommended']} contradicts "
                         f"gate={expected['experience_status']} role={expected['role_match']} "
                         f"skill={expected['skill_match']} (rule says {should})")


def load_test_set(root=ROOT):
    """JBFIND_TESTSET=name.json loads one file; default loads every json file
    in test_jobs/. A second distribution (real scraped jobs) is worth measuring
    against separately -- mixing it with hand-built jobs hides which one
    regressed."""
    only = os.environ.get("JBFIND_TESTSET")
    names = [only] if only else sorted(
        n for n in os.listdir(os.path.join(root, "test_jobs")) if n.endswith(".json")
    )
    jobs = []
    for name in names:
        rows = json.load(open(os.path.join(root, "test_jobs", name)))
        for i, row in enumerate(rows if isinstance(rows, list) else [rows]):
            expected = row.get("expected")
            if not expected:
                raise SystemExit(f"test_jobs/{name}[{i}]: an entry has no `expected` labels")
            check_labels(f"test_jobs/{name}[{i}] {row.get('company')}", expected)
            jobs.append({"job": {k: row[k] for k in JOB_FIELDS if k in row},
                         "expected": expected})
    if not jobs:
        raise SystemExit("test_jobs/ is empty -- nothing to measure")
    return jobs


def score_items(items, expected_jobs=None):
    """One number: mean distance from the labels, 0-100. 1000 = unusable run."""
    if not items:
        return 1000.0, {"reason": "no scored items came back"}
    if expected_jobs is not None and len(items) != expected_jobs:
        return 1000.0, {"reason": f"partial run: {len(items)}/{expected_jobs} jobs scored"}

    n = len(items)
    field_err = gate_err = rec_err = parse_err = 0
    missing = 0
    for it in items:
        exp = it.get("expected")
        if not exp:
            missing += 1
            continue
        if it.get("parse_error"):
            parse_err += 1
        # MISSING_DATA means the posting stated no experience. Demanding a
        # PASS there punishes honesty: 92% of real postings say nothing, and
        # the label PASS just means "no reason to reject". Only score the gate
        # when a range was actually posted, on either side.
        got, want = norm_gate(it.get("experience_status")), norm_gate(exp.get("experience_status"))
        if got != "MISSING_DATA" and want != "MISSING_DATA" and got != want:
            gate_err += 1
        for key in ("role_match", "skill_match", "location_match"):
            field_err += abs((it.get(key) or 0) - (exp.get(key) or 0)) / 100.0
        if bool(it.get("recommended")) != bool(exp.get("recommended")):
            rec_err += 1

    if missing:
        return 1000.0, {"reason": f"{missing}/{n} items had no expected labels"}

    got = sorted(range(n), key=lambda i: -items[i].get("overall_score", 0))
    want = expected_order(items)
    got_pos = {item: rank for rank, item in enumerate(got)}
    pairs = [(a, b) for a, b in zip(want, want[1:])]
    rank_err = sum(1 for a, b in pairs if got_pos[a] > got_pos[b])

    details = {
        "jobs": n,
        "gate_errors": gate_err,
        "field_mae": round(field_err / (3 * n), 4),
        "recommended_errors": rec_err,
        "rank_inversions": f"{rank_err}/{len(pairs)}",
        "parse_errors": parse_err,
    }
    score = (W_GATE * gate_err / n + W_FIELDS * field_err / (3 * n)
             + W_RECOMMENDED * rec_err / n + W_RANK * rank_err / max(1, len(pairs))
             + PARSE_PENALTY * parse_err / n)
    return round(score, 3), details


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verbose = "--verbose" in sys.argv
    client = N8n()
    client.login()
    exec_id = int(args[0]) if args else client.latest_id()
    if not exec_id:
        print(1000.0)
        return
    ex, execution = client.execution(exec_id)
    if ex.get("status") != "success":
        print(1000.0)
        if verbose:
            print(f"execution {exec_id} status={ex.get('status')}")
        return
    items = scored_items(execution)
    score, details = score_items(items, len(load_test_set()))
    if verbose:
        print(f"execution {exec_id}: {json.dumps(details)}")
        for it in sorted(items, key=lambda i: -(i.get("skill_match") or 0)):
            exp = it.get("expected") or {}
            print(f"  {it.get('company',''):<14} {it.get('title','')[:34]:<34} "
                  f"gate {it.get('experience_status','?'):<8} want {exp.get('experience_status','?'):<8} "
                  f"skill {it.get('skill_match')}/{exp.get('skill_match')} "
                  f"role {it.get('role_match')}/{exp.get('role_match')} "
                  f"loc {it.get('location_match')}/{exp.get('location_match')} "
                  f"rec {it.get('recommended')}/{exp.get('recommended')}")
    print(score)


if __name__ == "__main__":
    main()
