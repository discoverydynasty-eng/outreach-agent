# Outreach System — operating manual

Autonomous cold-email agent for small owner-run local-service businesses in NYC. Last
updated **2026-08-10**.

Deliberately contains no addresses, keys or lead data — those live in `config.json`,
`.env` and the `*.json` state files, all gitignored. Values are referred to by config key.

---

## 1. How it works

Three stages, each a separate scheduled job so the SEND lands on an exact clock minute
regardless of how long the earlier stages took.

```
 4:35am New York   draft_only.ps1      Claude CLI reads CLAUDE.md, drafts into outbox.json
 5:58am New York   followup_only.ps1   bounce_scan.py, then followup_guard.py sends follow-ups
 6:03am New York   send_only.ps1       send_guard.py sends whatever the draft stage queued
 Mon 8:30am Gulf   weekly_run.ps1      weekly digest
```

**The split matters.** Sourcing takes a variable 15–30 minutes; the send must not drift
with it.

### Times are New York times, and DST is handled automatically

Recipients are Eastern; the machine is Gulf (Arabian Standard Time, UTC+4, **no DST**).
New York is 8h behind in summer and 9h behind in winter, so a fixed Gulf clock time does
**not** mean a fixed New York time:

| | EDT (Mar–Nov) | EST (Nov–Mar) |
|---|---|---|
| Draft — 4:35am NY | 12:35pm Gulf | 1:35pm Gulf |
| Follow-ups — 5:58am NY | 1:58pm Gulf | 2:58pm Gulf |
| **Send — 6:03am NY** | **2:03pm Gulf** | **3:03pm Gulf** |

Each task therefore carries **two daily triggers**, one per regime, and every runner calls
`Test-NewYorkWindow` (`ny_window.ps1`) as its first act — exiting in under a second unless
New York local time is within 20 minutes of the target. The triggers are an hour apart, so
exactly one can ever pass. Verified against both 2026 and 2027 transition dates.

Nothing needs editing in November. That is the point: "shift the tasks twice a year" is
precisely the maintenance that gets forgotten, and every outage this system has had was
silent (§8). The draft's gate runs **before** the lock file is taken, so the off-season
trigger cannot leave a `.run.lock` that blocks the real run an hour later.

Both `cron.log` lines are explicit — `gate OK` or `gate SKIP` with the New York time and
the regime — so "why did nothing send" is answerable from the log alone.

### The one architectural rule

**Claude drafts. It never sends.** `send_guard.py` is the only thing that talks to SMTP,
and it re-checks every rule in real code — cap, dedup, MX, suppression, verification,
CAN-SPAM — so a drifting model cannot bypass them. `followup_guard.py` plays the same role
for follow-ups. Both are deterministic Python; neither calls an LLM.

---

## 2. Files

| File | Role |
|---|---|
| `CLAUDE.md` | Standing instructions the drafting agent reads each run |
| `send_guard.py` | **The only sender.** All hard rules enforced here |
| `followup_guard.py` | The only sender of follow-ups; stage timing, reply detection |
| `followup_content.py` | Follow-up copy, two sets (see §5) |
| `email_verify.py` | Pre-send mailbox verification (Reoon / others) |
| `bounce_scan.py` | Scans Gmail for hard bounces → `suppressed.json` |
| `open_tracking.py` | Optional open-tracking pixel + token map |
| `tracking_pixel.gs` | The endpoint, deployed as a Google Apps Script web app |
| `overpass_source.py` | Free lead sourcing from OpenStreetMap |
| `dob_discover.py` | Finds websites + published emails for NYC DOB licence holders |
| `check_replies.py` | Builds `tracker.csv`, reply/open rates per template |
| `_validate_outbox.py` | Pre-send draft linting (agent-maintained) |
| `ny_window.ps1` | DST gate — keeps the schedule fixed in **New York** time (§1) |
| `register_tasks.ps1` | Registers all four scheduled tasks; rerun to rebuild the schedule |

State (all gitignored): `config.json` `leads.json` `outbox.json` `send_log.json`
`followups.json` `suppressed.json` `open_tokens.json` `tracker.csv`

---

## 3. Current state — 2026-08-10

| | |
|---|---|
| Cold emails sent, lifetime | **275** |
| Follow-up emails sent, lifetime | **220** |
| Suppressed (undeliverable) | 21 |
| Bounce rate | 15 / 275 ≈ **5.5%** |
| Replies | **0** |
| Leads on file | **674** (215 status `new`, ready to work) |
| Lead sources | 393 web/other · 180 OpenStreetMap · 101 DOB-discovered |
| Follow-up contacts | 275 tracked — 58 completed all 3, 4 at stage 2, 38 at stage 1, 175 not started |

Caps: `daily_cap` **20** cold + `daily_followup_cap` **20** follow-ups = 40/day ceiling.

---

## 4. The email (set 2026-08-10)

Subject: `<first name>, is AI on your radar at <Company>?`

```
Hi <first name>,

I was on your website and noticed that you <5-8 word description>.

I imagine one of the biggest challenges is <their ONE problem angle>, and <the
quiet cost of that problem>.

I'm reaching out because I help <their trade> companies find cost inefficiencies
like that, which AI can solve.

I've helped local service businesses reclaim 10 to 15 hours a week of admin time
and a few thousand a month in wasted spend, through process automation and
workflow optimisation.

Is adopting AI currently on your radar for <Company>?

Best regards,
Umar
```

Five short lines: **observation → problem + agitate → solution → proof → ask.** The phrase
"cost inefficiencies **like that**" is load-bearing — it ties the offer to the problem just
named, so the email doesn't state a problem then pitch something unrelated.

**No compliments, anywhere.** The observation line stops at the fact; it must not gain
"which stood out" or any praise. `_validate_outbox.py` FAILS a draft containing flattery.

**The agitate clause names a cost, it never scolds.** Phrasings implying the owner is
failing or hasn't noticed ("you're losing", "bleeding money", "you don't realise") are
hard-failed. These are people who have survived decades in a brutal market; naming a real
cost is respectful, telling them they're bad at their job is not.

The proof figures are fixed text and the only claim available — no percentages, named
clients or case studies.

## 5. Follow-up sequence

Three emails, all **threaded onto the original** via `In-Reply-To`/`References` with the
subject forced to `Re: <original>`. Verified by matching Gmail's own `X-GM-THRID`.

```
original ──3d──> stage 1 ──2d──> stage 2 ──3d──> stage 3 ──> done
```

Any reply at any stage permanently stops that contact — checked over IMAP (Inbox **and**
Spam) immediately before each send. Suppressed and bounced contacts are dropped too.

**Two copy sets exist**, because the first-touch template changed and changed back. The AI
template ran as a WINDOW (2026-08-04 to 2026-08-10); only contacts first emailed inside it
get the AI follow-up copy. Everyone before and after gets the problem-angle copy, which
matches the restored template beat for beat. A contact keeps whichever set matches their
FIRST email, for life — switching mid-thread would be worse than either, since email 2
would answer a question email 1 never asked.

## 6. Deliverability

Everything here was measured, not assumed.

| Control | State |
|---|---|
| Authentication | SPF + DKIM + DMARC all pass, automatically (Gmail SMTP from a @gmail.com address) |
| Content | Plain text, **no links**, personalised, CAN-SPAM compliant |
| `List-Unsubscribe` | Present (mailto: only — no RFC 8058 one-click, since there's no HTTPS POST endpoint and advertising one without honouring it is worse than none) |
| Pre-send verification | Reoon, free tier, POWER mode (real SMTP mailbox check) |
| Bounce suppression | `bounce_scan.py` daily, permanent 5.x.x only — temporary 4.x.x deliver on retry |
| mail-tester | **10/10** without open tracking, **8.1/10** with it |

**Never guess an address.** Only a published or owner-confirmed inbox with valid MX. This
rule exists because guessed `info@`/`office@` prefixes bounced ~25% while verified batches
bounce ~0%.

### Open tracking — ON TRIAL until 2026-08-22

1×1 pixel → Apps Script web app → Google Sheet. The URL carries an **opaque HMAC token,
never an address**; the token map stays local in `open_tokens.json`.

Cost, measured: **10/10 → 8.1/10**. Almost all of it is `HTML_IMAGE_ONLY_12` (−1.63),
which fires because the emails are deliberately *short* and now contain an image. The
remote image itself costs −0.01. Clearing that rule would mean padding the copy, which
would wreck the template.

Damage limits: a tracked email is `multipart/alternative` with the **plain-text part
byte-identical** to the untracked version, and the HTML alternative is bare — no styling,
no links, no fonts.

**To revert: set `open_tracking_url` back to `""`.** One edit, restores plain text and
10/10, no code change. Scheduled task `hvac-open-tracking-trial-review` fires 2026-08-22
to make that call on evidence.

Open rate is measured against **tracked and delivered** mail only. Everything sent before
the trial has no pixel and would otherwise report a structural, meaningless 0%.

> Opens are a weak signal. Gmail's image proxy and Apple Mail Privacy Protection fetch
> pixels with no human involved, and image-blockers never register at all. **Replies are
> the metric that converts.**

---

## 7. Lead sources

| Source | Yield | Notes |
|---|---|---|
| Web search (agent) | 393 | The original method; slow, and the pool thinned badly |
| **OpenStreetMap** (`overpass_source.py`) | 180 | Free, no key, no card. NYC + Long Island + Westchester. **One-time injection** — OSM has ~200 mapped trade businesses total, so re-running yields ~0 |
| **NYC DOB licences** (`dob_discover.py`) | 101 | 1,263 licensed electricians/plumbers, each with the **licence holder's name**. Finds their site and scrapes a published email. ~11.7% hit rate |
| Google Places | 0 | **Blocked** — key installed and API enabled, but Google requires a billing card even for the free tier. Starts working the moment one is attached; no code change |

**DOB leads are graded.** `published_site` = the site printed the business's DOB phone
number, near-certain. `published_site_unverified_match` = matched on name tokens only —
the agent must open the site and confirm before writing. An early version matched INLINE
ELECTRICAL to `inline.com`; the grading exists because of that.

---

## 8. Failure modes that have actually happened

All three were **silent** — the system looked healthy while sending nothing. Each now
announces itself.

| What happened | Symptom | Fix |
|---|---|---|
| **Stale lock** (7/20–7/25, 6 days) | A killed draft run left `.run.lock` behind; every later run aborted with "previous run still active". 0 sent/day, no error | `draft_only.ps1` auto-clears any lock older than 2h |
| **Draft timeout** (7/16) | Sourcing subagents still running at the CLI's 600s background-task ceiling; run terminated before writing the outbox, and still exited 0 | `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=1500000` (25 min) |
| **OAuth expiry** (8/2–8/6, 4 days) | Claude CLI subscription session expired; every draft died in ~6 seconds. Zero sends were misread as an exhausted lead pool | `draft_only.ps1` now names the cause in `cron.log`; fix is `claude auth login` |

**Lesson:** success-shaped silence is the expensive failure. An empty outbox now always
logs *why* — auth, credit, network, timeout, or a genuinely dry pool.

**First thing to check when sends stop:** `logs/cron.log` for `** DRAFT FAILED`.

---

## 9. Runbook

```bash
# Is it authenticated? (first check when drafting fails)
claude auth status

# Send today's batch by hand. -Force is REQUIRED off-schedule: without it the runner
# checks New York local time and exits (see the DST gate in section 1).
powershell -ExecutionPolicy Bypass -File .\draft_only.ps1 -Force     # ~25 min
powershell -ExecutionPolicy Bypass -File .\send_only.ps1 -Force

# Follow-ups (also runs bounce_scan first)
powershell -ExecutionPolicy Bypass -File .\followup_only.ps1 -Force

# Reply + open rates per template
powershell -ExecutionPolicy Bypass -File .\check_replies.ps1

# Preview without sending
venv\Scripts\python.exe send_guard.py --dry-run
venv\Scripts\python.exe followup_guard.py --dry-run
venv\Scripts\python.exe bounce_scan.py --dry-run

# Refill leads
venv\Scripts\python.exe overpass_source.py            # --include-nj for New Jersey
venv\Scripts\python.exe dob_discover.py --limit 300

# Pause everything that sends
Disable-ScheduledTask -TaskName 'HVAC Send','HVAC Followups'
```

Run Python with `PYTHONUTF8=1`, and load `.env` first (the `.ps1` runners do both).

---

## 10. Open items

1. **Open-tracking trial** — decide 2026-08-22 on the evidence. Revert = one config edit.
2. **Zero replies in 275 sends.** Not a spam problem (10/10, no spam-blocks in any bounce).
   Contributing factors: 29% of early sends went to unmonitored role inboxes, and the old
   template is now replaced. The new template's cohort is the real test.
3. **Lead pool is finite.** ~215 workable leads left. OSM is exhausted; DOB can be re-run
   against other trade registries (it currently covers only electricians and master
   plumbers). A billing card unlocks Places, the strongest source.
4. **Places API** — installed, enabled, blocked on billing only.
5. **REVIEW mode is only partly wired for follow-ups** — it prints and skips rather than
   writing a hold file. Fine while `autonomy_mode` is AUTO; needs finishing if that changes.

---

## 11. A/B in flight

The first-touch template switched on 2026-08-04. `check_replies.py` reports both cohorts
side by side.

Baseline to beat: **old template — 245 delivered, 0 replies, 0.0%.**

Two honest caveats. Below ~50 delivered, one reply swings the rate by whole percentage
points. And the cohorts differ by more than copy — the new one draws on OSM/DOB leads with
real websites and owner names, where much of the old batch was role inboxes. If replies
improve, better targeting deserves some of the credit.
