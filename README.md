# Outreach agent

Autonomous daily cold-email agent for small owner-run local-service businesses in the New
York area, run for Fad Branding.

Despite the repo name it is no longer HVAC only. The pool now covers electricians,
plumbing, roofing, gutter work, water/mould/fire damage restoration, tree removal and
garage-door service. Same model and the same five problem angles apply to any local trade.

**Full operating manual: [`SYSTEM.md`](SYSTEM.md). Agent instructions:
[`CLAUDE.md`](CLAUDE.md). Windows install: [`SETUP_WINDOWS.md`](SETUP_WINDOWS.md).** This
file is the orientation layer over those.

## The one idea worth understanding

**The agent drafts. It never sends.**

Claude reads `CLAUDE.md`, sources and researches leads, and writes proposed emails into
`outbox.json`. A separate deterministic script, `send_guard.py`, is the only thing in the
system that can put mail on the wire. Whether it sends immediately or holds for approval
is a config switch, not something the drafting side can see or influence:

- `REVIEW`, the default, holds every draft until the operator runs `approve.ps1`
- `AUTO` sends whatever passes the guard on the scheduled run

That separation is the whole safety design. The generative half and the irreversible half
are different processes, and the guard enforces the rules even if a draft ignores them.

## Three stages, three scheduled jobs

The stages are split so the send lands on an exact clock minute regardless of how long
sourcing took. Sourcing varies by 15 to 30 minutes; the send must not drift with it.

| When (New York) | Job | What happens |
|---|---|---|
| 4:35am | `draft_only.ps1` | Claude reads `CLAUDE.md` and drafts into `outbox.json` |
| 5:58am | `followup_only.ps1` | `bounce_scan.py`, then `followup_guard.py` sends follow-ups |
| 6:03am | `send_only.ps1` | `send_guard.py` sends whatever the draft stage queued |
| Mon 8:30am Gulf | `weekly_run.ps1` | Weekly digest to the operator's inbox |

### Times are New York times, and DST is handled

Recipients are Eastern. The machine is Gulf time, UTC+4, which has no DST. New York is 8
hours behind in summer and 9 in winter, so a fixed Gulf clock time is **not** a fixed New
York time.

Each task therefore carries **two daily triggers**, one per DST regime, and every runner
calls `Test-NewYorkWindow` in `ny_window.ps1` as its first act. It exits in under a second
unless New York local time is within 20 minutes of the target, so the wrong-regime trigger
costs nothing. `register_tasks.ps1` installs them.

## Hard guardrails

Enforced in the guards, not left to the drafting agent's judgement:

1. **Daily cap** from `config.json`. The agent does not raise it; the operator does, as the
   sending account warms up.
2. **Never double-contact, never re-draft a dead address.** Dedupe against the send log and
   the suppression list before queuing.
3. **Never send to a guessed address.** It must be published on their own site or listing,
   and the domain must have a valid MX record. Pattern-guessing `info@` or `office@` is
   what produces "address not found" bounces.
4. **Every email carries the opt-out line and physical address.** This is a CAN-SPAM
   requirement. No opt-out line means no send.
5. **One initial email per business, ever.** Follow-ups exist but run entirely through the
   separate deterministic `followup_guard.py` pipeline. The agent never drafts a
   "checking in".

## Scripts

| Script | Role |
|---|---|
| `send_guard.py` | The enforced gate between drafts and the outside world |
| `followup_guard.py` | The same role for the three-step follow-up sequence |
| `overpass_source.py` | Free lead sourcing from OpenStreetMap, the no-cost alternative to Google Places |
| `dob_discover.py` | Turns the NYC DOB licence list into leads with a real site and a published email |
| `email_verify.py` | Optional pre-send mailbox verification. MX proves the domain, not the mailbox |
| `bounce_scan.py` | Scans Gmail for delivery failures and suppresses the dead addresses |
| `check_replies.py` | Builds `tracker.csv` from the send log |
| `weekly_summary.py` | The Monday digest, so the system can be monitored from an inbox |
| `open_tracking.py` | Optional 1x1 pixel. Off unless configured, and there is a trade-off documented in the file |

## What is not in this repo, on purpose

No addresses, no keys, no lead data. `.gitignore` excludes `.env`, every `*.json` state
file including `config.json` (which carries a real sender email and physical mailing
address), `tracker.csv`, and the whole `_*` prefix.

**Every business, name and email address you see in `_build_outbox.py` is synthetic.** It
is there to document the email template, not because anyone was contacted at
`owner@example.com`. Real prospect data exists only in `outbox.json` and the other
gitignored state files, and never enters version control.

That last one matters: the unattended drafting run writes throwaway helper scripts,
scraped licence PDFs and raw lead dumps straight into the project directory, and those
contain **real business contact data**. The prefix rule plus explicit `*.bak` and `*.pdf`
exclusions catch what a plain `*.json` rule would miss. `SYSTEM.md` refers to config by
key name for the same reason.
