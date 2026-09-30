# Autoresearch program for JBFind

Port of karpathy/autoresearch: one agent, one file it edits, one number, ~100
experiments per session. The upstream repo needs an NVIDIA GPU; this box is an
Apple M1 and the target here is a job-scoring pipeline, not nanochat. The parts
that carry over are the parts that matter — fixed budget per experiment, a single
comparable metric, one edited target, mechanical keep/revert.

## The three files that matter

| file | role | who edits it |
|---|---|---|
| `test_jobs/*.json` | the data + human labels (prepare.py) | human, only when labels are wrong |
| `agent/prompts/jd_analysis.md`, `agent/weights.json` | the system under test (train.py) | **the agent, one target per experiment** |
| `evaluate.py` | the metric (val_bpb) | never during a research session |

`experiment.py` is the harness: it injects `agent/` + `test_jobs/` into
`deploy/jbfind-test-workflow.json`, deploys it to n8n, runs it, prints one
number and appends to `experiments.jsonl`. It never invents a change.

## Setup (already done, re-check only if something fails)

1. n8n at `$N8N_URL` (default https://n8n.jbfind.duckdns.org), `N8N_LOGIN_EMAIL`
   and `N8N_LOGIN_PASSWORD` in `.env`.
2. `deploy/jbfind-test-workflow.json` is the harness. It is `active: false` in
   git; `experiment.py` activates it, takes the first run that starts, and
   deactivates it immediately so a 10 minute experiment does not fork 10 runs.
3. `deploy/jbfind-daily-workflow.json` is production. **Never touch it here.**

## The loop

### Step 1 — pick ONE target
Ordered by expected impact, from the 2026-09-28 baseline run (score 38.4):

1. `agent/prompts/jd_analysis.md` — the experience rule. Baseline says
   "3+ years" in the header and "4 years candidate" in the rules; the model
   resolves the contradiction by REJECTing all 7 in-scope jobs.
2. `agent/prompts/jd_analysis.md` — the location list. Chennai and Kolkata score
   0 because only four cities are named. Production already names six.
3. `agent/weights.json` — now applied deterministically in the harness's
   `Parse + Score` node instead of trusting the LLM's `overall_score`, so
   weight changes actually move the ranking. Upstream still asks the LLM for
   `overall_score` and ignores the weights entirely.
4. `agent/prompts/jd_analysis.md` — role scope: backend-only, manual QA, and
   network roles must be REJECTed with role_match 0 and skill_match 0.
5. `agent/prompts/jd_analysis.md` — output discipline: fewer, tighter numbers.

### Step 2 — edit exactly one thing
One experiment, one change. Batching makes the result unattributable.

### Step 3 — measure
```bash
python3 experiment.py --label "exp: exp rule 4yr -> 3yr"
```
Takes ~10 min: 12 jobs at ~30 s each on the ARM VM's CPU-only ollama, plus up to
60 s waiting for the schedule tick. It prints one JSON line and appends to
`experiments.jsonl` (gitignored).

### Step 4 — read the number
`score` is the mean distance from the labels, 0 is perfect, 100 is worst:

- `30 x gate_errors/n` — experience_status disagreeing with the label
- `40 x field_mae` — mean |actual - expected| over role/skill/location, /100
- `20 x recommended_errors/n` — the report/no-report decision
- `10 x rank_inversions/pairs` — does the weighted score order jobs as the
  labels imply
- `+15 x parse_errors/n`
- `1000` = the run broke, a job went missing, or n8n errored. Revert.

`details` says which of the four it lost. A partial run is always 1000 — a run
that scores 4 of 12 jobs must never look like a result.

**Noise floor: measure it before believing a delta.** `--repeat 3` on an
unchanged prompt. `tmp_resume/noise.log` holds the first reading. Only trust an
improvement larger than the spread.

### Step 5 — keep or revert
Improved → `git commit`. Worse or within noise → `git checkout` the file, note
why in `experiments.jsonl` via the next run's label.

### Step 6 — repeat
Target ~100 experiments. At the measured cost that is ~17 h for 100, so budget
it: overnight gets you ~50-60. `JBFIND_LLM_MODEL=3b python3 experiment.py ...`
roughly halves it and changes what the numbers mean, so confirm any winner on
the 7b model before promoting it.

## Production reality check (measured 2026-09-29 against real executions)
The test set and production disagree in a way that invalidates a naive promote.
Do not read the harness results as production results without this section.

- **The `experience` field is empty on 0/28 real normalized jobs.** The schema
  has it; no scraper fills it. Any gate reading `job.experience` returns
  MISSING_DATA on real data no matter how good it is on `test_jobs/`.
- **Only 2/28 real JDs contain a parseable experience range.** 92% of real jobs
  cannot be gated from text with a regex. The deterministic gate is correct but
  nearly inapplicable.
- **The `senior|lead` fallback is a hazard.** Real titles include "Lead Mobile
  Application Developer" — the candidate's own title. A title-based REJECT
  fallback would discard the best-matching job in the set.
- **The LLM prompt has the gate logic inverted.** Real `experience_reason` values
  read "0-3 years ... does not meet the minimum 3+ years required" — it rejects
  jobs whose *minimum is low* as if a low bar were a high one. Job ads state the
  minimum the employer will accept, so 0-3 accepts a 3-year candidate.
- **4 of the last 100 real jobs were discarded for that inverted reason.**
  Recoverable, and the single largest correctness win available.

Measured production yield over 4 successful daily runs: 100 jobs analysed,
**2 recommended**. The pipeline is currently near-useless end to end, and the
cause is the gate, not the ranking.

So: promote nothing from the harness to production until the gate reads real
data. Fix the inversion in the production prompt first, and extend the harness
test set with jobs that have *no* stated experience, because that is 92% of the
real distribution and nothing in the current 12 covers it.

## Real-distribution results (test_jobs/real_prod.json, 22 scraped jobs)

Run with `JBFIND_TESTSET=real_prod.json`. Three defects were found and fixed
against real postings rather than hand-built ones:

1. **Gate read the wrong field.** `job.experience` is empty on 0/28 real jobs,
   so the gate must parse the JD text. Regex on the low bound of the stated
   range: min 0-3 PASS, 4 REVIEW, 5+ REJECT.
2. **Title seniority must not reject.** The `senior|lead` fallback would have
   rejected "Lead Mobile Application Developer" -- the candidate's own title.
   Removed; no stated experience is `MISSING_DATA`, not a rejection.
3. **`recommended` had no rule.** The LLM guessed, which is why a job scoring
   85 came back `recommended: false`. Now computed from role>=50, skill>=60,
   location>=30 -- location matters because a France or Ireland posting is not
   actionable for a candidate based in Pune.

Two more findings that are not prompt problems:

- **The ollama box is single-tenant.** exp6 died with "connection was aborted"
  at item 4 because the production daily workflow was running at the same time.
  Never run a harness experiment while the daily workflow is in flight.
- **The prefilter leaks non-tech jobs.** `SWIFT` in a Fireblocks banking ad
  matches the mobile regex; arbeitnow returns a psychologist, a sales engineer
  and a content creator. Measured precision on 28 real jobs: 17/20 relevant
  rows kept correct, 3 leaks. The prefilter is recall-oriented by design, but
  the leaks are whole non-tech categories, not borderline titles.

Scoring note: `MISSING_DATA` is not a gate error. A label of PASS on a posting
with no stated experience means "no reason to reject", not "the model was
wrong". Counting it as an error inflated exp6b from 12.6 to 37.2.

Remaining honest error: `rank_inversions 6/21`. Ranking is still the weak
part, and 22 hand-labelled jobs is too few to tune it without overfitting.

## Promoted to production (deployed to jbfind-daily-workflow, active)

Three changes, each measured before deployment:

1. **Deterministic gate in `Parse Scores + Rank`.** Parses the stated range out
   of the JD text, decides on the low number, and overwrites the LLM's
   `experience_status` entirely. The LLM is no longer asked to do arithmetic.
2. **`recommended` computed, not guessed.** `gate !== REJECT && role >= 50 &&
   skill >= 60 && location >= 30`.
3. **Prefilter: word boundaries + non-tech negatives + strong-host budget.**
   `SWIFT` in a banking ad was matching the iOS term. Round-robin spent 3 of 8
   LLM slots on EU boards with a 0.31% mobile yield; restricting the budget to
   hosts that have actually produced a mobile-titled job took a batch from
   5/8 to 8/8 mobile roles, and leaks fell 4 -> 1.

Verified the deployed Parse node by running it locally under node against the
real inputs of execution 197: it returns PASS / MISSING_DATA where the live run
had returned REJECT. Execution 197 itself had started *before* the patch landed,
which is why the live run still showed the old behaviour -- a lesson worth
keeping: check whether an execution started after the patch, not just whether
the patch is deployed.

### The LLM zeroes role and skill when it rejects on experience

Even with the gate overridden, the model returned `role_match: 0,
skill_match: 0` for genuine mobile jobs, because the old prompt told it to set
those to 0 when rejecting. That poisoned the new `recommended` rule, which
needs `skill_match >= 60`. Fixed by telling the model to score role and skill
independently of the experience requirement.

### Operational constraint

ollama on this host is single-tenant. The daily workflow and the test harness
cannot run at the same time -- one of them dies with "connection was aborted".
The daily workflow also errors on roughly one run in four for the same reason.
Do not start a harness experiment while a daily run is in flight.

## 2026-09-29 pipeline FAILED alerts (the "connection was aborted" spam)

Eight identical bot alerts. Root cause is **not** the service being down —
209 and 211-217 all succeeded. It is a missing retry path around a slow call.

Two nodes were hitting the same failure mode:

- `Sheets Write Jobs` -> `http://scraper:8001/sheets/upsert`, hard-coded
  **30s timeout, zero retries**. The scraper does a Google Sheets column-A
  read plus a write per job, and n8n's HTTP node runs once per item, so a
  32-job batch meant 32 sequential Google API calls. One slow call tripped
  the 30s abort -> "connection was aborted" -> error trigger -> bot.
- `LLM Analyze Batch` -> ollama, also no retry. This one is ollama contention
  when the harness and the daily run overlap. 4 of the 5 recent failures.

**Deployed:** both nodes now retry (Sheets 4 tries / 10s apart / 120s
timeout; LLM 3 tries / 30s apart), and an alert-dedupe node sits between
`Error Trigger` and `Send Error Alert` so the same message within 30 minutes
is swallowed instead of posted eight times.

### Root cause, confirmed by measurement

The alert says "connection was aborted" at `Sheets Write Jobs`, but the
service was never down: a probe workflow calling `http://scraper:8001/health`
from inside the docker network returns `{"status":"ok"}`, and 217 completed
the same node fine.

Execution 218 is the useful one. Its per-node timings:

```
LLM Analyze Batch   start=+408s  duration=378s
Sheets Write Jobs   attempt 1  duration=4.9s   -> 8 rows written OK
                    attempt 2  duration=30s    -> connection aborted
                    gap between attempts: 535s
```

The node failed in **4.9 seconds**, not after the 120s timeout. A timeout
takes 120s. This was an instant connection reset, and n8n's own retry could
not outlast the container being briefly unavailable.

The actual defect is in the scraper, not in n8n. `_api()` did a single
`urlopen` with a 30s timeout and **no retry**, and it only caught
`HTTPError`. A dropped connection raises `URLError`, which was unhandled,
so one flaky Google Sheets call became a 500. Because n8n calls
`/sheets/upsert` once per job, one bad call out of 32 destroyed the run --
and the 8 writes that had already succeeded are lost with it, since the
whole execution aborts.

**Fixed in `deploy/scraper_service.py`:** `_api()` now retries
429/500/502/503/504 and any `URLError`/timeout/reset 4 times with backoff,
and raises a clean 503 instead of a raw 500 when Google is genuinely
unreachable. Verified offline with a stubbed `urlopen`:

| scenario | attempts | outcome |
|---|---|---|
| 2 socket resets | 3 | succeeded |
| service down | 4 | 503 `google unreachable` |
| google 500 | 4 | surfaced after retry |
| google 403 | 1 | surfaced immediately, no wasted retry |

**Deployed to n8n meanwhile:** Sheets node timeout 30s -> 120s, 6 tries
15s apart; LLM node 3 tries 30s apart; alert dedupe between `Error Trigger`
and `Send Error Alert` (same message within 30 min is swallowed).

### Not yet deployed: the scraper fix

`deploy/scraper.Dockerfile` does `COPY deploy/scraper_service.py`, so the
running container still has the old `_api()`. **The n8n-side retry is a
band-aid; the durable fix needs one rebuild on the VM:**

```sh
docker compose -f deploy/docker-compose.yml up -d --build scraper
```

That also activates `/sheets/upsert_batch`, after which the n8n Sheets node
can be pointed at it (one call for the whole batch instead of 32) with the
`Expand Sheet Results` code node to re-split the envelope.



- **Ranking.** `rank_inversions 6/21` is the largest remaining error.
- **More labels.** Only 22 real jobs are labelled. The blocker is the source
  mix, not effort: 546 postings from arbeitnow/jobicy/himalayas contain 3
  mobile titles, while naukri returns 7/7. Better labels come from a source
  that returns mobile roles, not from labelling more junk.
- **Source mix is the real ceiling.** naukri is doing all the work. The right
  next change is to drop arbeitnow/jobicy/himalayas and spend the scrape budget
  on naukri/indeed/linkedin, or to replace them with Indian job boards.


## Rules
- Never edit `evaluate.py`, `test_jobs/`, or `deploy/jbfind-daily-workflow.json`
  mid-session. A metric edited between runs makes the ledger a lie.
- Never live-scrape. The harness only ever sees `test_jobs/`.
- `test_jobs/` labels are the ground truth. Fix a wrong label before running, not
  after; `experiment.py` refuses to run when a label contradicts the rule in
  `evaluate.py:check_labels`.
- 12 labelled jobs will overfit well before 100 experiments. When the score
  stops moving, add jobs rather than adding experiments.

## Step 7 — promote a winner
The harness prompt lives in `agent/prompts/jd_analysis.md`; production carries
its own inline copy inside the `LLM Analyze Batch` node of the daily workflow.
When research settles, copy the winning text into that node and inject
`agent/weights.json` into `Parse Scores + Rank` so production matches what was
measured. That edit is deliberate and manual — the daily workflow has uncommitted
local changes, so do not let a harness run touch it.

## 2026-09-29 — Global budget fix VERIFIED live (exec 230)

Problem found while reading live `Normalize + Dedupe`: node is `runOnceForAllItems`
but n8n re-triggers it once per input branch of the multi-input Merge. Per-invocation
`seenUrls`/`seenKeys`/`LLM_BUDGET` meant every source branch got its own budget of 8
(32 jobs reached the LLM) and cross-source duplicates slipped past dedupe.

Fix (deployed to live workflow, versionId 0c6810ee-8b4c-48c1-9429-a5f7907bc0d8):
- Shared per-execution state via `$getWorkflowStaticData('global')`, keyed by
  `$execution.id`, reset at the top of each run (a stale run cannot poison the next).
- Dedupe sets (`urls`, `keys`) persisted across branch invocations.
- `remaining = LLM_BUDGET - store.emitted` cap so the FIRST branch to deliver
  relevant jobs takes the whole budget (desired: naukri/linkedin go first).
- Tested offline under node with 5-source simulation: 8/8 budget respected,
  cross-source duplicate caught, budget resets next execution.

Also removed the three dead boards from the n8n node graph (Arbeitnow API, Jobicy
API, Himalayas API) — they were separate n8n HTTP nodes. The sources that kept
appearing in 224-229 were a CACHED ACTIVE SNAPSHOT: cron kept executing the old
workflow definition until the workflow was properly reactivated via
`POST /rest/workflows/{id}/activate {"versionId": ...}` (PATCH active=true alone
did NOT reload the running cron snapshot).

Measurement (exec 230, first run on new definition):
- JobSpy scraped 39 rows: linkedin 19 + naukri 20 (only requested sites served)
- Normalize out: 8 (was 25-32) | LLM Analyze Batch: 8 | Parse: 8 | Sheets write: 8
- 23/39 scraped titles genuinely mobile (naukri now delivers actual
  "Mobile Application Developer" roles)
- Sanity: 4 later runs (224, 225, 226, 227) all executed old def — do NOT trust
  run data from executions that started before the activate call; check branch
  sources (arbeitnow/jobicy/himalayas => stale) before measuring.
