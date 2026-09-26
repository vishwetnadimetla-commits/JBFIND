# JBFind Phase 1.5 — Dedupe, cadence, actions, sources, ATS

## Findings that shape this plan

**Already 24x7, no work needed.** The cron has fired unattended every hour: exec 28
(00:00) then 32–39 (01:00→08:00), all `success`, Telegram `message_id` 7→18. VM uptime
5d15h, every container `restart: always`/`unless-stopped`, `RestartCount=0`.

The "only when my laptop is open" symptom is Telegram **Web** (open in the browser now).
Telegram Web shows an in-page banner only while that tab is focused — no desktop push
when closed. Fix is behavioural: use the Telegram mobile app.

**Duplicate notifications confirmed.** Every run sends unconditionally. Exec 37 updated
all 7 jobs, had 0 recommended, and still sent msg 16. Cause: no newness gate, plus
`hours_old=168` re-scraping a 7-day window hourly.

**Source reality check.** Only `linkedin` (262 rows) and `indeed` (45) ever produced data.
- `naukri` → `406 recaptcha required` on every run. Datacenter IP flagged. Needs residential proxy.
- `google` → returns 0 silently, no error. JobSpy's Google Jobs path is non-functional here.
- JobSpy also ships `ziprecruiter`, `glassdoor`, `bayt`, `bdjobs` — untested from this IP.
- `scrape_jobs()` has a native `proxies` param, so no monkeypatching is needed.

**Career page is half-solved and discarded.** `/scrape` already returns `company_url`;
no column exists for it, so it is dropped on the floor.

**Action UI already exists but is unreachable.** React dashboard has
APPLY/REVIEW/NOT_SUITABLE/CLEAR + reason box writing to `Decision`/`Decision_Reason`/
`Decision_Updated` (T/U/V). Blocked by two things:
- `dashboard_server.py:67-70` `authenticated()` is truncated — ends after the `if`, so it
  returns `None` for *any* Bearer token. The verification block was orphaned into
  `operations()` at lines 96-102, after `return result` (line 95). Line 97 also
  references `token`, which in that scope is the *bot* token, not an ID token.
- Port 8080 is blocked by the Oracle NSG. No OCI CLI or credentials on the VM.
`Decision` is empty on all 307 rows — the tracker has never been used.

**Sheet facts.** 307 data rows, 22 cols, `Posted_Date` 2026-09-14→2026-09-25, so "the last
2 weeks" **is** the whole sheet. `Posted_Date` blank on 18/307 (all remote-API jobs).
Tabs `APPLICATIONS`, `RESUME_CHANGES`, `SOURCES` exist but are completely empty.
219 distinct companies; top: Accenture 12, Infosys 11, Wipro 7, UST 6, YASH 6,
DocuSign 5, Citi 5, State Street 4, Capgemini 4, Qualcomm 3, JPMorganChase 3, NVIDIA 3.

**n8n Telegram capabilities confirmed** on this instance: `TelegramTrigger` supports
`callback_query` + `allowedUpdates`; operations include `answerQuery`, `editMessageText`,
and `inlineKeyboard` reply markup. Inline buttons are buildable natively.

## Decisions taken (from user)

| Topic | Decision |
|---|---|
| Wipe | Back up to a new tab, then wipe all 307 rows |
| Action surface | **Both** dashboard and Telegram inline buttons |
| Dashboard access | SSH tunnel — no Oracle console access, nothing exposed publicly |
| Buttons live on | Reporter bot (Tracker bot stays reserved for phase 2) |
| Reason tags | Separate tag sets for Reject vs Not Suitable |
| Proxy | Naukri only, cheap pilot; behind a config flag until creds exist |
| Career page | Also resolve the real ATS posting, ~15 companies |

---

## Step 1 — Dedupe, cadence, failure alerts (no dependencies)

- `Send Telegram Report`: add an IF gate — send only when ≥1 aggregated job has
  `sheet_action == "appended"`. Nothing new → no message.
- `JobSpy Scrape`: `hours_old` 168 → 3, matching a 30-min cadence.
- Cron `everyHour minute 0` → every 30 minutes.
- `LLM_BUDGET` stays 12. Narrowing the window is what keeps 48 runs/day affordable;
  a 7-day window at 30-min cadence would be ~576 LLM calls/day.
- New `jbfind-error-alert` workflow: n8n Error Trigger → Telegram. A broken run messages
  instead of going silent. This is the actual "sleep peacefully" guarantee.

## Step 2 — Backup, wipe, new schema in one pass

Order matters: fold the new columns in here rather than migrating later.

1. Copy `JOBS` → `JOBS_BAK_2026-09-26`.
2. Clear all 307 data rows.
3. Append 2 columns at the **end** — `Company_URL` (W), `Careers_URL` (X) — so
   `Decision`/`Decision_Reason`/`Decision_Updated` stay at T/U/V and
   `dashboard_server.py:update_decision`'s hardcoded `JOBS!T:V` keeps working untouched.
4. `deploy/scraper_service.py:22-28` — append `company_url`, `careers_url` to `FIELDS`;
   update the "22 columns" comment. The column-letter math in `sheets_upsert` is
   derived from `len(FIELDS)`, so it follows automatically.
5. `deploy/dashboard_server.py:11` `SHEET_RANGE` → `JOBS!A1:X`; `HEADERS` gains the 2 names;
   the header self-heal at line 38 writes `A1:X`.

Side effect: the 16-char vs 8-char Job_ID mismatch disappears, so dedup becomes clean.

## Step 3 — Posted date hardening

- `posted_date` falls back to `discovered_date` when the source provides none (the 18 blanks).
- Surface posted date in the Telegram report so recency is visible at a glance.

## Step 4 — Unbreak the dashboard

- Move the orphaned block `dashboard_server.py:96-102` into `authenticated()` (67-70),
  reading the token from the handler, and delete it from `operations()`.
- `docker-compose.yml:85` mounts `deploy/dashboard.html`, which does not exist (the file is
  at repo root and is never served anyway). Remove the dead mount.
- Remove the `getUpdates` call in `operations()` — it will 409-conflict with the Telegram
  Trigger poller in Step 5. Replace with a cheap `getMe` reachability check.
- Extend the decision vocabulary `dashboard_server.py:51` to `APPLY | REJECT | NOT_SUITABLE | CLEAR`,
  and require a non-empty reason for `REJECT` and `NOT_SUITABLE`.
- `dashboard-app/src/main.jsx`: swap the human `REVIEW` option for `REJECT`; add a
  reason-tag multi-select plus free text. Rebuild the dist.
- Access: SSH tunnel from the laptop, nothing opened in the cloud.

## Step 5 — Telegram inline buttons (Reporter bot)

Callback payloads stay under Telegram's 64-byte limit: `d:<job_id>:<DECISION>`.

1. `Send Telegram Report` — attach an `inlineKeyboard` to each recommended job:
   `Apply | Reject | Not suitable`.
2. `TelegramTrigger` on the Reporter bot, `allowedUpdates` including `callback_query`.
3. Route by callback data:
   - `d:<id>:APPLY` → write decision, `editMessageText` to confirm inline.
   - `d:<id>:REJECT` / `d:<id>:NOT_SUITABLE` → `answerQuery`, then `sendMessage` the
     tag keyboard for that specific decision type + a `Custom reason` button.
   - `t:<id>:<DECISION>:<TAG>` → write decision with that tag.
   - `t:<id>:<DECISION>:custom` → prompt the user to reply with free text; the next
     inbound `message` becomes the reason.
4. Both surfaces write through `dashboard_server.update_decision()` so there is one
   validation path and one vocabulary, not two.

Proposed tag sets (to confirm):

- NOT_SUITABLE (fit): `missing_core_skill`, `seniority_too_high`, `seniority_too_low`,
  `title_mismatch`, `not_mobile_domain`, `outside_target_role`
- REJECT (deal-breakers): `compensation_below_target`, `location_not_remote`,
  `relocation_required`, `company_red_flags`, `role_already_filled`, `too_competitive`

## Step 6 — Sources

- Remove `naukri` + `google` from the live site list until the proxy works — they burn
  ~2 min of scrape time per run for zero rows.
- Test `zip_recruiter`, `glassdoor`, `bayt`, `bdjobs` individually from the VM; keep only
  what actually returns rows.
- Proxy wiring: `scrape_jobs(proxies=...)` applied **only** to the naukri call, so
  LinkedIn/Indeed stay free. Read from env (`JBFIND_NAUKRI_PROXY`), inert when unset.

## Step 7 — Resolve real ATS postings

- Seed the company list from actual sheet frequency (top ~15), not guesswork.
- Auto-discover the board by probing public JSON APIs — no manual slug curation:
  - Greenhouse `boards-api.greenhouse.io/v1/boards/{slug}/jobs`
  - Lever `api.lever.co/v0/postings/{slug}`
  - Workday CCX for the Workday-heavy names (NVIDIA, Accenture, Optum)
- Cache resolved `company → board slug` so probing happens once, not every run.
- Store the employer's own posting in `Careers_URL`; use it as the report's primary link
  with the aggregator link secondary.
- Avoid per-job Google search — fragile, slow, rate-limited.

---

## Blocked on the user

1. **Residential proxy credentials** — provider, `host:port`, username, password.
   Steps 6's naukri re-enable and nothing else waits on this. Recommend a low-bandwidth
   pilot: Naukri's recaptcha is aggressive and may still challenge even via residential.
2. **Laptop tunnel** — the dashboard step needs `ssh -L 8080:localhost:8080` running
   before use. Nothing to install on the VM.

## Risks

- Naukri may stay blocked even behind a residential proxy. Fallback is dropping it and
  leaning on Indeed + the ATS boards in Step 7, which are more reliable anyway.
- Two action surfaces means two writers on the same columns. Mitigated by routing both
  through `update_decision()`.
- 30-min cadence doubles scrape load against Indeed/LinkedIn. Watch for rate limiting;
  `hours_old=3` keeps per-run volume low.
