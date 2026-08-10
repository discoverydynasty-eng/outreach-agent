# Setup Guide - HVAC Daily Outreach (Windows, hardened build)

Architecture: **Task Scheduler -> Claude Code (drafts only) -> send_guard.py (validates + sends).**
Claude never sends. A Python gate re-checks every rule in real code and is the only
thing that can put mail on the wire.

This is the Windows version of the macOS `SETUP.md` (kept for reference in
`_mac_reference\SETUP_macos.md`). On Windows there is no cron or launchd, so the two
jobs run via **PowerShell runners** (`daily_run.ps1`, `weekly_run.ps1`) scheduled with
**Task Scheduler**. Do these once and it runs itself.

Folder is already at: `C:\Users\Lenovo\hvac-outreach`

---

## 0. REVIEW vs AUTO mode (how much do you trust it to send?)
Set by `config.json` -> `autonomy_mode`. This is enforced inside `send_guard.py`, in
real code, so it holds even if the model drifts.

- **`REVIEW`** *(default)* - the daily run drafts emails into `outbox.json` and the guard
  **HOLDS them**. Nothing is sent. It writes `review_pending.txt` (a readable list of
  exactly what would send). You review, then run `approve.ps1` to send the ones that pass
  every check. This mirrors the "prepare everything, a human confirms before it goes live"
  gate on your other agents. Use this for at least the first couple of weeks.
- **`AUTO`** - the guard sends unattended on each scheduled run, no approval step. Switch
  to this only once you trust the copy and deliverability.

To send only some of the held drafts: open `outbox.json`, delete the items you don't
want, then run `approve.ps1`. To send none: do nothing; they roll into the next run.
Either way the daily cap, dedup, MX, and opt-out checks still apply at approval time.

---

## 1. Fill in config.json  (REQUIRED - the #1 blocker)
Open `config.json` and set all three REPLACE fields:
- `sender_email` - the exact Gmail address you send from (e.g. you@gmail.com).
- `physical_mailing_address` - a REAL address. CAN-SPAM legally requires a physical
  mailing address in every commercial email, and `send_guard.py` will **refuse to send
  anything** while this still says `REPLACE`. A registered-agent address, PO box, or
  business address all work.
- `target_areas` - currently Manhattan / Brooklyn / Queens / Bronx. Adjust if needed.
- Leave `daily_cap` at 8 for week one (see Warm-up below).

The opt-out sentence is already set and must appear verbatim in every email; the guard
rejects any email missing it.

---

## 2. Set up Gmail sending (SMTP + App Password)
`send_guard.py` sends through **Gmail SMTP** using an **App Password** (no Google Cloud
project, no browser consent, works headless). One-time:
1. On the sending Gmail account, turn on **2-Step Verification**
   (myaccount.google.com/security).
2. Create an **App Password** at myaccount.google.com/apppasswords (name it e.g.
   "HVAC outreach"). Google shows a 16-character password.
3. Put it in the folder's `.env` (the runners load `.env`):

       GMAIL_APP_PASSWORD=your16charapppassword

   Spaces are fine (the guard strips them). Keep `.env` private; it is not committed.
4. Make sure `config.json` `sender_email` is the exact Gmail address that owns that App
   Password. On a bad/missing password the guard aborts and sends nothing (outbox is
   preserved) rather than failing silently.
Set the Gmail account's display name to "Umar" (Google Account settings) so your name
shows on outgoing mail. `sender_name` in config.json (now "Umar") also sets the From
display name. Every email is signed "Umar" with no company name; the guard rejects any
draft that contains "Fad Branding".

(The optional weekly digest `weekly_summary.py` still reads Gmail via the Google API and
would need its own `credentials.json`; it is not required for sending and can be skipped.)

---

## 3. Create the Python environment
This build uses `send_guard.py` + `weekly_summary.py`, which need the Gmail + DNS
libraries. From this folder in PowerShell:

    python -m venv venv
    .\venv\Scripts\python.exe -m pip install --upgrade pip
    .\venv\Scripts\python.exe -m pip install -r requirements.txt

The runners (`daily_run.ps1`, `weekly_run.ps1`) call `venv\Scripts\python.exe`
specifically, so everything stays self-contained. They also set `PYTHONUTF8=1` so
emoji / accented business names in `outbox.json` round-trip correctly on Windows.

---

## 4. (Optional) Add your Google Places API key
Sourcing works without it (falls back to web search), but the key finds more leads.
1. Google Cloud Console -> enable **Places API** -> create an API key.
2. Create a file named `.env` in this folder containing exactly:

       PLACES_API_KEY=paste_your_key_here

`daily_run.ps1` loads this automatically into the environment for Stage 1.

---

## 5. TEST in dry-run first (nothing sends)
Do a full dry run before you ever send for real.

    # let Claude fill the outbox (Stage 1 only)
    powershell -ExecutionPolicy Bypass -File .\daily_run.ps1

Wait - `daily_run.ps1` runs BOTH stages. For the very first test, run the two stages
by hand so you can inspect the drafts before the guard even considers them:

    # Stage 1: Claude drafts into outbox.json (resolve your claude.exe path or run
    # this from an interactive Claude session in this folder):
    claude -p "Read CLAUDE.md and run today's job through Step 6. Queue emails into outbox.json. Do not send." --dangerously-skip-permissions

    # Stage 2: see what the guard WOULD do, without sending:
    .\venv\Scripts\python.exe send_guard.py --dry-run

Open `outbox.json` and read 2-3 drafts. Do they sound human and specific? Check the
dry-run output: with the address still `REPLACE`, the guard should print
`BLOCKED: set a real physical_mailing_address` - that proves the CAN-SPAM gate works.
Once config.json is real, the dry run should accept the good ones and reject anything
bad for the right reason.

---

## 6. First REAL send by hand
When dry-run looks right and config.json is filled in, run the whole job once:

    powershell -ExecutionPolicy Bypass -File .\daily_run.ps1

In **REVIEW** mode (the default) this drafts and HOLDS - nothing sends yet. Open
`review_pending.txt` and read exactly what would go out. When it looks right, send them:

    powershell -ExecutionPolicy Bypass -File .\approve.ps1

(In **AUTO** mode `daily_run.ps1` sends directly and you skip `approve.ps1`.)

Then check: your Gmail "Sent" folder, `send_log.json`, and `rejected.json`. Confirm the
emails actually landed and read well. In REVIEW mode every scheduled run stops at the
hold, so `approve.ps1` is your daily gate; in AUTO mode it runs unattended after this.

---

## 7. Schedule both jobs with Task Scheduler (the cron/launchd replacement)
Only after step 6 passes. Run this ONCE in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\register_tasks.ps1

It registers two tasks:
- **HVAC Daily Outreach** -> `daily_run.ps1`, every day at 8:05am
- **HVAC Weekly Summary** -> `weekly_run.ps1`, every Monday at 8:30am

Both use `-StartWhenAvailable` (catches up a missed run, like launchd) and `-WakeToRun`
(wakes the machine from sleep). They run in your logged-in user context so the Claude
Code CLI and the venv are available. Keep the machine on / logged in at those times, or
edit the `-At` times in `register_tasks.ps1`.

Verify:

    Get-ScheduledTask -TaskName 'HVAC*'
    Start-ScheduledTask -TaskName 'HVAC Daily Outreach'   # run once on demand

---

## 8. Kill switch
Pause everything immediately (no data lost, pipeline resumes when re-enabled):

    Disable-ScheduledTask -TaskName 'HVAC Daily Outreach'
    Disable-ScheduledTask -TaskName 'HVAC Weekly Summary'

Resume with `Enable-ScheduledTask`, or remove with
`Unregister-ScheduledTask -TaskName 'HVAC Daily Outreach' -Confirm:$false`.

---

## 9. Warm-up schedule (raise the cap by hand)
Sends come from your live Gmail with no review, so ramp slowly:
- Week 1: `daily_cap` 8
- Week 2: `daily_cap` 12
- Week 3 onward: `daily_cap` up to 20

Change the number in `config.json`. Nothing else to touch. The guard enforces whatever
number is there; do not raise it faster than deliverability allows.

---

## What to watch in the first two weeks
- Check Gmail daily for bounce-backs and spam replies. A spike is the early warning that
  deliverability is slipping - throttle `daily_cap` down if you see it.
- Skim `rejected.json` occasionally; it tells you WHY things didn't send (bad domains,
  dupes, cap hits). Lots of "no MX" rejects means sourcing is finding junk emails.
- Replies from real prospects are handled by YOU personally. First-touch sends only,
  no auto follow-ups.

## Why this build is safer than sending from Claude directly
The daily cap, never-contact-twice, dead-domain (MX) check, and mandatory opt-out line
are enforced in `send_guard.py` as real code. Even if the model drifts, miscounts, or
hallucinates, it physically cannot send - it can only propose into `outbox.json`. The
guard has the only key to the mailbox.

---

## Files
- `CLAUDE.md`          - standing instructions for Stage 1 (Claude drafts, never sends)
- `config.json`        - autonomy_mode, cap, target areas, sender identity, address, opt-out
- `send_guard.py`      - Stage 2: the enforced gate; the ONLY thing that sends
- `weekly_summary.py`  - Monday digest email
- `daily_run.ps1`      - Windows runner: Stage 1 (Claude) + Stage 2 (guard)
- `approve.ps1`        - REVIEW mode: approve + send the held drafts
- `weekly_run.ps1`     - Windows runner for the weekly digest
- `register_tasks.ps1` - registers both Task Scheduler jobs
- `requirements.txt`   - Python deps for the venv
- `leads.json` / `outbox.json` / `send_log.json` / `rejected.json` - pipeline state
- `review_pending.txt` - (REVIEW mode) the readable hold summary of what would send
- `_mac_reference\`    - the original macOS bash + launchd files, for reference only

## Logs
- `logs\cron.log`         - one line per run (start / stage markers / summary)
- `logs\run_<stamp>.log`  - full transcript of each headless Claude + guard run
- `logs\summary_<stamp>.log` - each weekly digest run
