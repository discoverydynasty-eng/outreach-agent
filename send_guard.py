#!/usr/bin/env python3
"""
send_guard.py — the enforced gate between Claude's drafts and the outside world.

Claude Code writes proposed emails to outbox.json. This script is the ONLY thing that
actually sends mail. It re-checks every hard rule in real code, so a drifting or
confused model cannot bypass the daily cap, contact someone twice, send to a dead
domain, or omit the legally required opt-out line. Anything that fails a check is moved
to rejected.json and never sent.

Usage:
    python3 send_guard.py            # validate outbox.json and send what passes
    python3 send_guard.py --dry-run  # print decisions, send nothing (use when testing)
"""

import argparse
import datetime
import json
import os
import re
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path

import dns.resolver

import email_verify  # local module; optional pre-send mailbox verification
import open_tracking  # local module; optional 1x1 open-tracking pixel

HERE = Path(__file__).resolve().parent
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # implicit TLS (SMTPS)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Catches an unfilled template placeholder that leaked into a real draft, e.g. "[name]",
# "[first name]", "<first name>", "<problem>", "{{first_name}}". A real email never
# contains these. The {{...}} form was added 2026-08-04 with the new outreach template,
# which is written with {{first_name}} - without this, an unfilled merge tag would have
# sailed straight past the guard and out to a real prospect.
PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]|<[^>]+>|\{\{[^}]*\}\}")

# Shared free-mail providers: many small businesses run on these, so "domain already
# contacted" must NOT block them (only the exact address counts as a duplicate). Without
# this, emailing one gmail.com shop would lock out every other gmail.com business.
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "rocketmail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "aol.com",
    "icloud.com", "me.com", "mac.com", "mail.com", "gmx.com", "gmx.us",
    "proton.me", "protonmail.com", "zoho.com", "yandex.com",
    # Consumer ISP mailboxes. Same rationale as above and NOT optional in this market:
    # older NYC trade shops run on these constantly, so without them one @verizon.net
    # shop locks out every other one. (Added 2026-07-28 after Holly Expert Tree Care was
    # falsely blocked as "domain already contacted" by an unrelated roofer on verizon.net.)
    "verizon.net", "optonline.net", "att.net", "sbcglobal.net", "bellsouth.net",
    "comcast.net", "earthlink.net", "juno.com", "cox.net", "frontier.com",
    "rr.com", "nyc.rr.com", "si.rr.com", "netzero.net", "aim.com",
}


def load_json(name, default):
    p = HERE / name
    if not p.exists():
        return default
    with open(p) as f:
        return json.load(f)


def save_json(name, data):
    with open(HERE / name, "w") as f:
        json.dump(data, f, indent=2)


def today_iso():
    return datetime.date.today().isoformat()


def valid_format(email):
    return bool(EMAIL_RE.match(email or ""))


def domain_of(email):
    return email.split("@", 1)[1].lower() if "@" in (email or "") else ""


def domain_has_mx(email):
    """True only if the domain can actually receive mail. Prevents bounce storms."""
    domain = domain_of(email)
    if not domain:
        return False
    try:
        answers = dns.resolver.resolve(domain, "MX")
        return len(answers) > 0
    except Exception:
        return False


def get_app_password():
    """Gmail App Password from the environment (loaded from .env by the runner).
    Never stored in config.json or code. Spaces (Google shows it as 4x4) are stripped."""
    return (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()


def open_smtp(sender_email, app_password):
    """Log in to Gmail SMTP over implicit TLS. Raises on bad credentials so the caller
    can abort the whole batch instead of marking every recipient as a send failure."""
    context = ssl.create_default_context()
    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30)
    server.login(sender_email, app_password)
    return server


def send_email(server, sender_email, to, subject, body, from_display=None,
               extra_headers=None, message_id=None, tracking_url=None, tracking_token=None):
    """Sends and returns the Message-ID actually used, so the caller can persist it.
    Storing this on every send (since 2026-07-18) is what lets follow-ups thread onto
    this exact message later via In-Reply-To/References, with no IMAP lookup needed for
    any contact sent from now on (only the pre-2026-07-18 backlog needs that)."""
    if tracking_url and tracking_token:
        # multipart/alternative: the plain-text part stays byte-identical to the untracked
        # version, so anything that prefers text (or blocks images) sees exactly the email
        # that scores 10/10 today. Only the HTML alternative carries the pixel.
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(open_tracking.html_body(body, tracking_url, tracking_token),
                            "html", "utf-8"))
    else:
        msg = MIMEText(body, _charset="utf-8")
    msg["From"] = from_display or sender_email
    msg["To"] = to
    msg["Subject"] = subject
    mid = message_id or make_msgid(domain="gmail.com")
    msg["Message-ID"] = mid
    # List-Unsubscribe (RFC 2369) gives Gmail/Outlook a native "Unsubscribe" button. The
    # point is not the button itself - it is that people who want out click THAT instead of
    # "Report spam", and spam complaints are the single most damaging sender-reputation
    # signal there is. mailto: only, deliberately no List-Unsubscribe-Post: RFC 8058
    # one-click requires an HTTPS endpoint that accepts POST and there isn't one, and
    # advertising one-click without honouring it is worse than not offering it. Replying is
    # already the documented opt-out in the body, so this just surfaces the same mechanism
    # in the mail client's UI.
    msg["List-Unsubscribe"] = f"<mailto:{sender_email}?subject=unsubscribe>"
    for k, v in (extra_headers or {}).items():
        msg[k] = v
    server.sendmail(sender_email, [to], msg.as_string())
    return mid


def write_review_pending(approved, rejects, mode, cap, sent_today):
    """In REVIEW mode, write a human-readable hold summary the operator reads before
    approving. Nothing here sends; this only describes what WOULD send on approval."""
    out = []
    out.append(f"REVIEW HOLD - {today_iso()}")
    out.append(f"autonomy_mode = {mode}. Nothing has been sent.")
    out.append(f"{len(approved)} draft(s) pass the guard and will send once you approve.")
    out.append(f"daily_cap = {cap}, already sent today = {sent_today}.")
    out.append("")
    out.append("APPROVE (send them now):")
    out.append("    powershell -ExecutionPolicy Bypass -File .\\approve.ps1")
    out.append("SEND ONLY SOME: edit outbox.json, delete the ones you don't want, then approve.")
    out.append("SEND NONE: do nothing; they roll into the next run.")
    out.append("")
    out.append("================ WILL SEND ================")
    if not approved:
        out.append("(none)")
    for i, it in enumerate(approved, 1):
        out.append("")
        out.append(f"[{i}] To: {it.get('email')}   ({it.get('business')})")
        out.append(f"Subject: {it.get('subject')}")
        out.append("")
        out.append(it.get("body", ""))
        out.append("-" * 50)
    if rejects:
        out.append("")
        out.append("============== REJECTED (won't send) ==============")
        for it in rejects:
            out.append(f"- {it.get('email') or '(no email)'}  ->  {it.get('reject_reason')}")
    with open(HERE / "review_pending.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print decisions, send nothing")
    ap.add_argument("--approve", action="store_true",
                    help="operator approval to send in REVIEW mode (no effect in AUTO)")
    args = ap.parse_args()

    config = load_json("config.json", {})
    # REVIEW (default) drafts and holds for a human; AUTO sends unattended.
    mode = (config.get("autonomy_mode") or "REVIEW").strip().upper()
    cap = int(config.get("daily_cap", 8))
    mailing = (config.get("physical_mailing_address") or "").strip()
    optout = (config.get("optout_sentence") or "").strip()
    sender_email = (config.get("sender_email") or "").strip()
    sender_name = (config.get("sender_name") or "").strip()
    from_display = formataddr((sender_name, sender_email)) if sender_email else None
    # Optional pre-send mailbox verification (off unless a provider + key are set).
    verify_provider = (config.get("email_verify_provider") or "").strip()
    verify_key = (os.environ.get("EMAIL_VERIFY_API_KEY") or "").strip()
    verify_skip_risky = bool(config.get("email_verify_skip_risky"))
    # Optional open tracking. Empty/absent = OFF, and the email stays pure plain text.
    tracking_url = (config.get("open_tracking_url") or "").strip()
    if tracking_url and "REPLACE" in tracking_url.upper():
        tracking_url = ""

    outbox = load_json("outbox.json", {"pending": []}).get("pending", [])
    send_log = load_json("send_log.json", {"sent": []})
    sent_list = send_log.get("sent", [])
    rejected = load_json("rejected.json", {"rejected": []}).get("rejected", [])
    # Addresses proven undeliverable before (verification-failed or no-MX / bounced). Never
    # retry them - saves verification credits and stops re-drafting known-bad addresses.
    suppressed = {e.lower() for e in load_json("suppressed.json", {"emails": []}).get("emails", [])}

    already_emails = {(e.get("email") or "").lower() for e in sent_list}
    already_domains = {domain_of(e.get("email", "")) for e in sent_list if e.get("email")}
    sent_today = sum(1 for e in sent_list if e.get("date_iso") == today_iso())

    # HARD STOP 1: refuse to send anything if there's no real mailing address (CAN-SPAM).
    if (not mailing) or ("REPLACE" in mailing.upper()):
        for item in outbox:
            item["reject_reason"] = "config.physical_mailing_address is not set (CAN-SPAM requires one)"
            rejected.append(item)
        save_json("rejected.json", {"rejected": rejected})
        save_json("outbox.json", {"pending": []})
        print("BLOCKED: set a real physical_mailing_address in config.json. Nothing sent.")
        return

    # HARD STOP 2: refuse if no opt-out sentence is configured to check for.
    if not optout:
        print("BLOCKED: config.optout_sentence is empty. Nothing sent.")
        return

    approved = []
    for item in outbox:
        email = (item.get("email") or "").strip()
        body = item.get("body") or ""
        subject = item.get("subject") or ""
        owner = (item.get("owner_name") or "").strip()
        owner_first = owner.split()[0] if owner else ""
        reason = None

        if not valid_format(email):
            reason = "invalid email format"
        elif email.lower() in suppressed:
            reason = "address previously undeliverable (suppressed)"
        elif email.lower() in already_emails:
            reason = "email already contacted (in send_log)"
        elif domain_of(email) in already_domains and domain_of(email) not in FREE_EMAIL_DOMAINS:
            reason = "domain already contacted (in send_log)"
        elif not domain_has_mx(email):
            reason = "domain has no MX record (cannot receive mail)"
        elif optout not in body:
            reason = "opt-out sentence missing from body"
        elif mailing not in body:
            reason = "physical mailing address missing from body"
        elif PLACEHOLDER_RE.search(subject) or PLACEHOLDER_RE.search(body):
            reason = "contains an unfilled placeholder (e.g. [name]); a real name/detail is missing"
        elif "fad branding" in (subject + " " + body).lower():
            reason = "contains 'Fad Branding' (must be removed; sign as Umar)"
        elif owner_first and owner_first.lower() not in subject.lower():
            reason = f"owner name '{owner_first}' missing from subject line"
        elif (sent_today + len(approved)) >= cap:
            reason = f"daily cap of {cap} reached"

        if reason:
            item["reject_reason"] = reason
            rejected.append(item)
            if "no MX" in reason:  # dead domain -> never retry this address
                suppressed.add(email.lower())
            print(f"REJECT  {email or '(no email)'} -> {reason}")
        else:
            approved.append(item)
            print(f"OK      {email} -> will send")

    if args.dry_run:
        print(f"\nDRY RUN: {len(approved)} would send, {len(outbox) - len(approved)} rejected. "
              f"Nothing was sent. send_log and outbox unchanged.")
        # Still surface rejects so you can inspect them, but don't consume the outbox.
        save_json("rejected.json", {"rejected": rejected})
        save_json("suppressed.json", {"emails": sorted(suppressed)})
        return

    # REVIEW GATE: unless AUTO or the operator explicitly approved, hold everything for
    # a human. Nothing is sent, outbox.json is preserved for a later approved run, and a
    # readable summary is written to review_pending.txt. This is enforced here in real
    # code, so a drifting model cannot send by skipping the review step.
    if mode != "AUTO" and not args.approve:
        this_run_rejects = [it for it in outbox if it.get("reject_reason")]
        write_review_pending(approved, this_run_rejects, mode, cap, sent_today)
        print(f"\nREVIEW mode: {len(approved)} draft(s) pass the guard and are HELD for "
              f"approval; {len(this_run_rejects)} rejected. Nothing sent, outbox preserved.")
        print("Read review_pending.txt (or outbox.json), then approve with:")
        print("    powershell -ExecutionPolicy Bypass -File .\\approve.ps1")
        return

    if approved:
        # Pre-flight the sender credentials. Fail BEFORE consuming the outbox so the
        # held drafts survive if something is missing and the operator can just re-run.
        app_password = get_app_password()
        if not sender_email or "REPLACE" in sender_email.upper():
            sys.exit("BLOCKED: set a real sender_email in config.json. Nothing sent; outbox preserved.")
        if not app_password:
            sys.exit("BLOCKED: GMAIL_APP_PASSWORD is not set. Add it to .env "
                     "(see SETUP_WINDOWS.md step 2). Nothing sent; outbox preserved.")
        try:
            server = open_smtp(sender_email, app_password)
        except Exception as e:
            sys.exit(f"BLOCKED: Gmail SMTP login failed ({e}). Check sender_email and "
                     "GMAIL_APP_PASSWORD (needs 2-Step Verification + an App Password). "
                     "Nothing sent; outbox preserved.")
        try:
            for item in approved:
                # Verify the mailbox actually exists right before sending (only on real
                # sends, so dry-runs don't burn credits). Undeliverable = skip, never send.
                if verify_provider and verify_key:
                    verdict, detail = email_verify.check(item["email"], verify_provider, verify_key)
                    if verdict == "undeliverable" or (verdict == "risky" and verify_skip_risky):
                        item["reject_reason"] = f"verification: {verdict} ({detail})"
                        rejected.append(item)
                        suppressed.add(item["email"].lower())  # never draft/verify this again
                        print(f"SKIP    {item['email']} -> verification {verdict} ({detail})")
                        continue
                    if verdict == "error":
                        print(f"WARN    verification unavailable for {item['email']} ({detail}); sending anyway")
                try:
                    tok = (open_tracking.register(item["email"], today_iso(), "cold")
                           if tracking_url else None)
                    mid = send_email(server, sender_email, item["email"], item.get("subject", ""),
                                      item["body"], from_display,
                                      tracking_url=tracking_url, tracking_token=tok)
                    sent_list.append({
                        "business": item.get("business"),
                        "email": item.get("email"),
                        "owner_name": item.get("owner_name"),
                        "date_iso": today_iso(),
                        "subject": item.get("subject"),
                        "status": "sent",
                        "message_id": mid,
                        "problem_type": item.get("problem_type"),
                        "body": item["body"],
                    })
                    print(f"SENT    {item.get('email')}")
                    # Update dedup sets live so the cap can't be exceeded within one run.
                    already_emails.add(item["email"].lower())
                    already_domains.add(domain_of(item["email"]))
                except Exception as e:
                    item["reject_reason"] = f"send failed: {e}"
                    rejected.append(item)
                    print(f"SEND FAIL {item.get('email')} -> {e}")
        finally:
            try:
                server.quit()
            except Exception:
                pass

    save_json("send_log.json", {"sent": sent_list})
    save_json("rejected.json", {"rejected": rejected})
    save_json("suppressed.json", {"emails": sorted(suppressed)})
    save_json("outbox.json", {"pending": []})  # outbox consumed

    # The hold summary is now stale (these were just approved + sent).
    stale = HERE / "review_pending.txt"
    if stale.exists():
        stale.unlink()

    sent_now = sum(1 for e in sent_list if e.get("date_iso") == today_iso()) - sent_today
    print(f"\nDone. Sent {sent_now} today ({sent_today + sent_now}/{cap} total for today). "
          f"{len(rejected)} lifetime rejects logged.")


if __name__ == "__main__":
    main()
