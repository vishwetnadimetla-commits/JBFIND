# Autoresearch program for JBFind

## Objective
Optimize the JBFind job-hunting automation pipeline. The scoring weights, LLM analysis prompts, Apify search queries, and resume tailoring instructions live in `agent/`. Each experiment edits ONE target, triggers a test run via `run.sh`, evaluates via `evaluate.py`, and keeps or reverts.

## Setup (one-time, human-assisted)
1. Oracle VM provisioned with Docker + n8n running (see `deploy/bootstrap.sh`).
2. n8n instance accessible at `$N8N_URL` with `$N8N_API_KEY` set.
3. Test workflow `jb-find-test` created in n8n (runs against `test_jobs/`).
4. Credentials configured in n8n: Apify, Google Sheets, Google Drive, Telegram.

## Experiment loop (repeat 100+ times per session)

### Step 1 — Read current state
- `agent/weights.json` — current scoring weights
- `agent/prompts/jd_analysis.md` — current analysis prompt
- `agent/prompts/resume_tailor.md` — current tailoring prompt
- Last run log from `run.sh` output

### Step 2 — Make ONE modification
Choose one of the following targets (rotate each experiment):
- **Weights**: modify one scoring weight in `agent/weights.json` (e.g. skill_weight 0.4 → 0.35)
- **Analysis prompt**: refine `agent/prompts/jd_analysis.md` (clarify rule, add edge case)
- **Tailoring prompt**: refine `agent/prompts/resume_tailor.md`
- **Apify query**: update search keywords/location filters (via n8n workflow API)

### Step 3 — Run the test
```bash
bash run.sh "jb-find-test"
```

### Step 4 — Evaluate
```bash
python3 evaluate.py
```
A single number: lower is better.
- < 50: good
- 50–200: needs work
- > 200: the change broke something — revert

### Step 5 — Keep or discard
- If score improved → commit the change (git commit)
- If score degraded → revert the file (git checkout)
- Log experiment to `experiments.jsonl` with format:
  ```json
  {"timestamp": "...", "target": "weights.skill_weight", "change": "0.4->0.35", "score": 45}
  ```

### Step 6 — Repeat
Loop back to Step 1. Target ~100 experiments per overnight session.

## Wallet of targets (ordered by expected impact)
1. Skill match weight — most sensitive, tune first.
2. JD analysis prompt — clarify experience gate edge cases.
3. Role match weight — second-order.
4. Location match weight — low impact, tune last.
5. Apify search query — find better job listings.
6. Resume tailor prompt — factual constraints enforcement.

## Rules
- Always run against the static `test_jobs/` set, never against live job listings.
- Only edit ONE thing per experiment — never batch changes.
- If evaluate.py fails to connect to n8n, abort and check the VM is running.
- Never modify `test_jobs/`, `evaluate.py`, `run.sh`, `deploy/`, or `.gitignore`.