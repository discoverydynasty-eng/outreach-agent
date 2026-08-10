# Setup Guide — HVAC Daily Outreach (hardened build)

Architecture: **cron/launchd -> Claude Code (drafts only) -> send_guard.py (validates + sends).**
Claude never sends. A Python gate re-checks every rule in real code and is the only
thing that can put mail on the wire. ~40 minutes to set up once.

---

## 0. Put the folder somewhere permanent
Move this whole `hvac-outreach` folder to your home directory, e.g.
`/Users/YOURNAME/hvac-outreach`. Not Downloads, not a temp folder.

---

## 1. Fill in config.json
Open `config.json` and set all three REPLACE fields:
- `sender_email` — the exact Gmail address you'll send from (e.g. you@gmail.com).
- `physical_mailing_address` — a REAL address. CAN-SPAM legally requires one in every
  commercial email. PO box, registered agent, or business address all work.
- Leave `daily_cap` at 8 for week one (see Warm-up).
The guard will REFUSE to send anything until the mailing address is real, on purpose.

---

## 2. Set up Gmail API sending (this replaces the MCP connector)
Because the Python guard now owns sending, it needs Gmail API access:
1. In Google Cloud Console (same project you'll use for Places), enable "Gmail API".
2. Create an OAuth client ID of type "Desktop app".
3. Download its JSON and save it in this folder as `credentials.json`.
4. The first time send_guard.py runs, it opens a browser once to authorize the
   account. After you approve, it saves `token.json` and never prompts again.
Set the Gmail account's display name to "Fad" (in Google Account settings) so your
name shows on outgoing mail.

---

## 3. Create the Python environment
From inside the folder:

    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt

This installs the Gmail + DNS libraries the guard needs. The runner script calls
`venv/bin/python` specifically, so everything stays self-contained.

---

## 4. Add your Google Places API key (primary sourcing)
1. In Google Cloud Console, enable the "Places API".
2. Create an API key.
3. In this folder, create a file named `.env` containing exactly:

       PLACES_API_KEY=paste_your_key_here

Skippable, the job falls back to web search for sourcing if the key is absent.

---

## 5. Point the scripts at your folder
Edit the `PROJECT_DIR` line in `daily_run.sh` to the real absolute path.
Edit the three `/Users/YOURNAME/...` paths in `com.fadbranding.hvacoutreach.plist` too.
Then:

    chmod +x daily_run.sh

---

## 6. TEST in dry-run first (nothing sends)
Do a full dry run before you ever send for real:

    # let Claude fill the outbox
    claude -p "Read CLAUDE.md and run today's job through Step 6. Queue emails into outbox.json. Do not send." --dangerously-skip-permissions

    # then inspect what the guard WOULD do, without sending
    ./venv/bin/python send_guard.py --dry-run

Now open `outbox.json` and read 2-3 of the drafted emails. Do they sound human and
specific, like the Figlia email? Check the dry-run output, does the guard accept the
good ones and reject anything bad for the right reason? If the copy is off, tell me
and we tune CLAUDE.md before a single real send.

---

## 7. First REAL send by hand
When dry-run looks right, run the whole thing once manually:

    ./daily_run.sh

Check: your Gmail "Sent" folder, `send_log.json`, and `rejected.json`. Confirm the
emails actually landed and read well. This hand-run is your one real review gate,
after this it runs unattended.

---

## 8. Schedule it with launchd (recommended over cron)
launchd runs a missed job when the Mac next wakes; cron just skips it. Install:

    cp com.fadbranding.hvacoutreach.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.fadbranding.hvacoutreach.plist

It now runs daily at 8:05am (or at wake, if asleep then). On first run macOS may ask
permission for the script to control apps, approve it.

Prefer plain cron instead? Run `crontab -e` and add:

    5 8 * * * /Users/YOURNAME/hvac-outreach/daily_run.sh

---

## 9. Warm-up schedule (raise the cap by hand)
Live Gmail, no review, so ramp slowly:
- Week 1: daily_cap 8
- Week 2: daily_cap 12
- Week 3+: up to 20
Just change the number in `config.json`.

---

## 10. Kill switch
Pause everything instantly:

    launchctl unload ~/Library/LaunchAgents/com.fadbranding.hvacoutreach.plist

(or comment out the cron line). Reload to resume. No data lost, the pipeline continues
where it left off.

---

## 11. Weekly digest email (optional but recommended)
`weekly_summary.py` emails you a once-a-week snapshot (sent, replies, bounces) so you
can monitor from your inbox instead of opening JSON. It reuses the same Gmail
`credentials.json`, but authorizes its own read-access token the first time you run it.

Test it once by hand:

    ./venv/bin/python weekly_summary.py --dry-run   # prints the digest, sends nothing

The first real run opens a browser to approve read access (separate from the send
token, on purpose, so the send guard stays send-only). Then schedule it:

    cp com.fadbranding.hvacsummary.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.fadbranding.hvacsummary.plist

It now emails you every Monday at 8:30am. Reply/bounce counts are inbox-scan estimates,
useful as a trend, not an exact audit.

---

## What to watch the first two weeks
- Check Gmail daily for bounce-backs and spam replies. A spike is the early warning
  that deliverability is slipping, throttle the cap down if you see it.
- Skim `rejected.json` occasionally, it tells you WHY things didn't send (bad domains,
  dupes, cap hits). Lots of "no MX" rejects means your sourcing is finding junk emails.
- Replies from real prospects are handled by YOU personally. This system is first-touch
  sends only, no auto follow-ups.

## Why this build is safer than sending from Claude directly
The daily cap, the never-contact-twice rule, the dead-domain check, and the mandatory
opt-out line are enforced in `send_guard.py` as real code. Even if the model drifts,
miscounts, or hallucinates, it physically cannot send, it can only propose into
outbox.json. The guard has the only key to the mailbox.
