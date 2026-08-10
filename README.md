# Outreach agent

An autonomous daily cold-email system for small owner-run local-service businesses.
It sources prospects, researches each one, writes a genuinely specific email, and sends
it on a schedule, unattended, every morning.

Built and run in production for Fad Branding. The niche started as HVAC and now covers
electricians, plumbing, roofing, gutter work, water/mould/fire damage restoration, tree
removal and garage-door service.

The interesting part is not that an LLM writes the emails. It is everything built around
the LLM to make that safe.

## The architecture in one idea

**The agent drafts. It never sends.**

The drafting stage is a Claude Code run: it reads its instructions, sources and researches
leads, and writes proposed emails into `outbox.json`. It has no send capability at all.

A separate deterministic Python script, `send_guard.py`, is the only thing in the system
that can put mail on the wire. It re-checks every rule itself rather than trusting the
draft. Whether it sends immediately or holds for approval is a config value the drafting
side cannot read or influence:

| Mode | Behaviour |
|---|---|
| `REVIEW` (default) | The guard holds every draft until a human approves with `approve.ps1` |
| `AUTO` | The guard sends whatever passes its checks on the scheduled run |

The generative half and the irreversible half are different processes with different
privileges. A bad draft is a wasted lead; it is not an email to a stranger. That property
is the point of the design, and everything else follows from it.

## Problems that turned out to be harder than they look

**Time zones with a moving target.** Recipients are in New York. The machine runs on Gulf
time, UTC+4, which observes no DST. New York is 8 hours behind in summer and 9 in winter,
so a fixed local clock time is not a fixed New York time, and a send scheduled for 6:03am
Eastern silently drifts by an hour twice a year. The fix is two daily triggers per job,
one per DST regime, with every runner calling `Test-NewYorkWindow` as its first act. It
exits in under a second unless New York local time is within 20 minutes of the target, so
the wrong-regime trigger costs nothing and the right one always fires.

**Sourcing without a billing card.** `overpass_source.py` pulls candidate businesses from
OpenStreetMap through the Overpass API, as a free replacement for Google Places.
`dob_discover.py` takes the New York City Department of Buildings licence list and turns
each contractor into a workable lead by finding their real website and an email actually
published on it.

**Proving an address exists.** A valid MX record proves the domain accepts mail. It cannot
prove a specific mailbox is real, so `email_verify.py` optionally verifies the mailbox
before the guard will send.

**Bounces that repeat.** `suppressed.json` originally collected only verification failures
and no-MX addresses, so a mailbox that hard bounced was never suppressed and the follow-up
sequence cheerfully mailed it again. `bounce_scan.py` closes that: it scans for delivery
failures and suppresses the dead addresses. It suppresses **permanent** 5.x.x failures
only. Temporary 4.x.x failures, meaning greylisting or transient server trouble, are left
alone deliberately, because those usually deliver on retry and suppressing them throws
away real leads.

**Splitting the schedule.** Sourcing takes a variable 15 to 30 minutes. If drafting and
sending are one job, the send time drifts with however long research happened to take. So
they are three separate scheduled jobs, and the send lands on an exact clock minute
regardless.

| When (New York) | Job | What happens |
|---|---|---|
| 4:35am | `draft_only.ps1` | The agent researches and drafts into `outbox.json` |
| 5:58am | `followup_only.ps1` | `bounce_scan.py`, then `followup_guard.py` sends follow-ups |
| 6:03am | `send_only.ps1` | `send_guard.py` sends whatever the draft stage queued |
| Mon 8:30am Gulf | `weekly_run.ps1` | Weekly digest so the system can be monitored from an inbox |

## Guardrails

Cold email is a domain where being careless is both ineffective and obnoxious. These are
enforced in the guards, not left to the drafting agent's judgement:

1. **A daily cap** the agent cannot raise. Only the operator does, as the sending account
   warms up.
2. **Never contact a business twice, and never re-draft a dead address.** Every candidate
   is deduped against the send log and the suppression list before it is queued.
3. **Never send to a guessed address.** It has to be published on the business's own site
   or a listing they control, and the domain needs a valid MX record. Pattern-guessing
   `info@` or `office@` is what produces "address not found" bounces and burns sender
   reputation.
4. **Every email carries an opt-out line and a physical postal address.** This is a
   CAN-SPAM requirement. No opt-out line means no send, enforced at the guard.
5. **One initial email per business, ever.** Follow-ups exist, but they run through a
   separate deterministic pipeline. The drafting agent never writes a "just checking in".

There is also a quality rule the drafting stage holds itself to: if an email cannot be made
specific and human, skip the lead rather than send something templated. The observation
that opens each email has to be something true about that business in particular.

## The email

A three-beat problem-agitate-solve structure:

```
noticed   something specific and true about this business, observed first-hand
imagine   the problem that observation implies, named without exaggerating it
helps     what we do about it, in the business owner's own vocabulary
```

`_build_outbox.py` documents the shape with three worked examples.

## Repository layout

| Path | Role |
|---|---|
| `send_guard.py` | The enforced gate between drafts and the outside world |
| `followup_guard.py` | The same role for the three-step follow-up sequence |
| `overpass_source.py` | Free lead sourcing from OpenStreetMap |
| `dob_discover.py` | NYC DOB licence list to leads with a real site and published email |
| `email_verify.py` | Optional pre-send mailbox verification |
| `bounce_scan.py` | Delivery-failure scanning and suppression |
| `check_replies.py` | Builds a reply tracker from the send log |
| `weekly_summary.py` | The Monday digest |
| `open_tracking.py` | Optional 1x1 pixel, off unless configured |
| `_build_outbox.py` | The email template, with synthetic examples |
| `*.ps1` | Scheduled-task runners and the DST window gate |

Python for the pipeline, PowerShell for scheduling on Windows, Gmail SMTP for delivery.
`_mac_reference/` keeps the launchd equivalents from the original macOS install.

## On the data in this repo

**Every business, name and email address in `_build_outbox.py` is synthetic.** They exist
to document the email template. Nobody was contacted at `owner@example.com`.

Real prospect data never enters version control. `.gitignore` excludes `.env`, every
`*.json` state file including `config.json`, the reply tracker, and the entire `_*`
prefix. That last rule matters more than it looks: the unattended drafting run writes
throwaway helper scripts, scraped licence PDFs and raw lead dumps straight into the project
directory, and those contain real business contact data. Explicit `*.bak` and `*.pdf`
exclusions catch what a plain `*.json` rule would miss.

This repository is published as a fresh history for that reason. The full working history
is kept private.

## Honest limitations

- **Windows-specific.** Scheduling depends on Windows scheduled tasks. The macOS launchd
  files are reference only and are not maintained.
- **Single machine.** Nothing fires if the machine is off at the scheduled minute.
- **Paths are hardcoded** to the original install directory in the `.ps1` runners.
- **Sender reputation is the real constraint**, not throughput. The daily cap exists
  because volume is the fastest way to ruin a sending domain.

## Further reading

`SYSTEM.md` is the full operating manual, including failure modes that have actually
happened. `CLAUDE.md` is the agent's own standing instructions. `SETUP_WINDOWS.md` covers
installation.
