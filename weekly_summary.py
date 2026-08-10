#!/usr/bin/env python3
"""
weekly_summary.py — emails Fad a once-a-week status digest so he can monitor the
outreach system from his inbox instead of opening JSON files.

It reports, for the last 7 days:
  - how many emails were sent (from send_log.json)
  - how many contacted prospects replied (read from Gmail)
  - how many bounced / had delivery failures (read from Gmail)
  - lifetime totals and how many the guard rejected

Read access uses its OWN token (token_reader.json) so the send guard can stay
locked to send-only. First run opens a browser once to authorize, then never again.

Usage:
    python3 weekly_summary.py
    python3 weekly_summary.py --dry-run   # print the digest, don't email it
"""

import argparse
import base64
import datetime
import json
from collections import Counter
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

HERE = Path(__file__).resolve().parent
# Needs readonly (to count replies/bounces) and send (to email the digest).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
INBOX_SCAN_CAP = 300  # how many recent inbox messages to scan for replies


def load_json(name, default):
    p = HERE / name
    if not p.exists():
        return default
    with open(p) as f:
        return json.load(f)


def get_service():
    creds = None
    token_path = HERE / "token_reader.json"
    cred_path = HERE / "credentials.json"  # same OAuth client as the send guard
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not cred_path.exists():
                raise SystemExit("Missing credentials.json. See SETUP.md step 2 (Gmail API).")
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def count_query(service, q):
    """Count messages matching a Gmail search query (paginated)."""
    n = 0
    req = service.users().messages().list(userId="me", q=q, maxResults=100)
    while req is not None:
        resp = req.execute()
        n += len(resp.get("messages", []))
        req = service.users().messages().list_next(req, resp)
    return n


def list_ids(service, q, cap):
    ids = []
    req = service.users().messages().list(userId="me", q=q, maxResults=100)
    while req is not None and len(ids) < cap:
        resp = req.execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        req = service.users().messages().list_next(req, resp)
    return ids[:cap]


def from_addresses(service, ids):
    out = []
    for mid in ids:
        msg = service.users().messages().get(
            userId="me", id=mid, format="metadata", metadataHeaders=["From"]
        ).execute()
        for h in msg.get("payload", {}).get("headers", []):
            if h.get("name", "").lower() == "from":
                _, addr = parseaddr(h.get("value", ""))
                if addr:
                    out.append(addr.lower())
    return out


def send_digest(service, sender, to, subject, body):
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    if sender:
        msg["from"] = sender
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = load_json("config.json", {})
    to = (config.get("sender_email") or "").strip()
    sender_name = (config.get("sender_name") or "").strip()
    sender = f"{sender_name} <{to}>" if to else None
    if not to or "REPLACE" in to.upper():
        raise SystemExit("Set a real sender_email in config.json first.")

    today = datetime.date.today()
    week_ago = today - datetime.timedelta(days=7)

    # --- sends from the log ---
    sent_all = load_json("send_log.json", {"sent": []}).get("sent", [])

    def in_window(e):
        try:
            return datetime.date.fromisoformat(e.get("date_iso", "")) >= week_ago
        except Exception:
            return False

    sent_week = [e for e in sent_all if in_window(e)]
    rejected_all = load_json("rejected.json", {"rejected": []}).get("rejected", [])

    # --- Gmail: bounces and replies ---
    service = get_service()

    bounce_q = ('newer_than:8d (from:mailer-daemon OR from:postmaster) '
                '(subject:delivery OR subject:undeliverable OR subject:failure '
                'OR subject:returned OR subject:"not delivered")')
    bounces = count_query(service, bounce_q)

    contacted = {(e.get("email") or "").lower() for e in sent_all if e.get("email")}
    inbox_ids = list_ids(service, "newer_than:8d in:inbox", INBOX_SCAN_CAP)
    froms = from_addresses(service, inbox_ids)
    reply_senders = sorted({f for f in froms if f in contacted})

    # daily breakdown of the week
    by_day = Counter(e.get("date_iso") for e in sent_week)
    day_lines = "\n".join(f"  {d}: {by_day[d]}" for d in sorted(by_day)) or "  (none)"
    reply_lines = "\n".join(f"  {r}" for r in reply_senders) or "  none yet"

    body = f"""Weekly HVAC outreach digest
{week_ago} to {today}

Sent this week: {len(sent_week)}
Replies from prospects you contacted: {len(reply_senders)}
Bounces / delivery failures (approx): {bounces}

Sent per day:
{day_lines}

Who replied (follow up personally):
{reply_lines}

Lifetime sent: {len(sent_all)}
Rejected by the guard, all time: {len(rejected_all)}

Quick reads:
- Bounces climbing? Lower daily_cap in config.json and check your sourcing quality.
- Replies above are real people. This system does first-touch only, so those are
  yours to answer.
- Reply and bounce counts are estimates from an inbox scan, treat them as a trend
  signal, not an exact audit.
"""

    if args.dry_run:
        print(body)
        print("DRY RUN: digest not emailed.")
        return

    send_digest(service, sender, to, "your weekly HVAC outreach numbers", body)
    print(f"Digest emailed to {to}.")


if __name__ == "__main__":
    main()
