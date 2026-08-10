#!/usr/bin/env python3
"""
check_replies.py - reply tracker for the HVAC outreach.

Builds/updates tracker.csv from send_log.json (everyone the guard has emailed), then
scans the Gmail inbox over IMAP for replies from those contacts and marks who wrote
back. Reuses the SAME Gmail App Password as the send guard (GMAIL_APP_PASSWORD in .env),
so there is no extra login. tracker.csv opens directly in Google Sheets (drag it into
Drive, or File > Import).

Usage:
    python check_replies.py            # rebuild/refresh tracker.csv + check for replies
    python check_replies.py --no-imap  # just refresh the sheet from send_log, skip Gmail
"""

import argparse
import csv
import email
import imaplib
import json
import os
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRACKER = HERE / "tracker.csv"
IMAP_HOST = "imap.gmail.com"
FIELDS = ["Business", "Area", "Contact", "Email", "Subject", "Sent Date", "Template",
          "Status", "Delivered", "Tracked", "Opened", "Open Date", "Replied", "Reply Date",
          "Notes"]


def tracked_addresses():
    """Addresses that were actually sent WITH a tracking pixel.

    Everything sent before open tracking was switched on physically cannot register an
    open, so counting it in the denominator would report a permanent 0% and read as "nobody
    opens our email" rather than "we weren't measuring yet". Derived from the local token
    map, which only ever gets an entry when a pixel was really embedded.
    """
    store = load_json("open_tokens.json", {})
    return {(v.get("email") or "").lower() for v in (store.get("tokens") or {}).values()}

# The first-touch template changed on this date (problem-angle -> AI cost-efficiency).
# Imported rather than duplicated so the tracker and the follow-up engine can never
# disagree about which cohort a contact belongs to.
try:
    from followup_content import CUTOVER_ISO
except Exception:
    CUTOVER_ISO = "2026-08-04"
OLD_LABEL = "old (problem-angle)"
NEW_LABEL = "new (AI)"


def template_for(date_iso):
    return NEW_LABEL if (date_iso or "") >= CUTOVER_ISO else OLD_LABEL


def load_json(name, default):
    p = HERE / name
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def area_lookup():
    m = {}
    for lead in load_json("leads.json", {"leads": []}).get("leads", []):
        b = (lead.get("business") or "").strip()
        if b and b not in m:
            m[b] = lead.get("area") or ""
    return m


def read_tracker():
    rows = {}
    if TRACKER.exists():
        with open(TRACKER, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                key = (r.get("Email") or "").lower()
                if key:
                    rows[key] = r
    return rows


def write_tracker(rows):
    ordered = sorted(rows.values(), key=lambda r: (r.get("Sent Date", ""), r.get("Business", "")))
    with open(TRACKER, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in ordered:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def sync_from_sendlog(rows, areas):
    # Addresses proven undeliverable (hard bounce / verification failure). They were sent
    # but never arrived, so they must not sit in the denominator of a reply rate - counting
    # them would flatter or punish a template for something it had no part in.
    undeliverable = {a.lower() for a in load_json("suppressed.json", {"emails": []}).get("emails", [])}
    tracked = tracked_addresses()
    for e in load_json("send_log.json", {"sent": []}).get("sent", []):
        addr = (e.get("email") or "").strip()
        if not addr:
            continue
        key = addr.lower()
        row = rows.get(key, {"Replied": "", "Reply Date": "", "Notes": ""})
        row["Business"] = e.get("business") or row.get("Business", "")
        row["Area"] = areas.get(e.get("business", ""), row.get("Area", ""))
        row["Contact"] = e.get("owner_name") or row.get("Contact", "")
        row["Email"] = addr
        row["Subject"] = e.get("subject") or row.get("Subject", "")
        row["Sent Date"] = e.get("date_iso") or row.get("Sent Date", "")
        row["Template"] = template_for(e.get("date_iso"))
        row["Status"] = e.get("status") or "sent"
        row["Delivered"] = "no" if key in undeliverable else "yes"
        row["Tracked"] = "yes" if key in tracked else "no"
        rows[key] = row
    return rows


def check_replies(rows):
    sender = (load_json("config.json", {}).get("sender_email") or "").strip()
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()
    if not sender or not pw:
        print("Skipping reply check: sender_email or GMAIL_APP_PASSWORD not available.")
        return rows, 0
    newly = 0
    M = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        M.login(sender, pw)
        # Spam is searched as well as INBOX: a genuine reply that Gmail misfiled would
        # otherwise be invisible here, and an undercounted reply is the one error that
        # would make a good template look like a failing one.
        for folder in ('"INBOX"', '"[Gmail]/Spam"'):
            typ, _ = M.select(folder, readonly=True)
            if typ != "OK":
                continue
            for key in list(rows):
                if rows[key].get("Replied") == "yes":
                    continue
                typ, data = M.search(None, "FROM", '"%s"' % key)
                if typ != "OK" or not data or not data[0]:
                    continue
                ids = data[0].split()
                reply_date = ""
                for mid in ids[-1:]:  # newest matching message
                    t, d = M.fetch(mid, "(BODY.PEEK[HEADER])")
                    if t == "OK" and d and d[0]:
                        msg = email.message_from_bytes(d[0][1])
                        try:
                            reply_date = parsedate_to_datetime(msg.get("Date")).date().isoformat()
                        except Exception:
                            reply_date = ""
                newly += 1
                rows[key]["Replied"] = "yes"
                if reply_date:
                    rows[key]["Reply Date"] = reply_date
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return rows, newly


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-imap", action="store_true", help="refresh sheet only, skip Gmail")
    args = ap.parse_args()

    rows = sync_from_sendlog(read_tracker(), area_lookup())
    newly = 0
    if not args.no_imap:
        rows, newly = check_replies(rows)

    # Opens, if the pixel endpoint is configured. Joined locally by token, so the hosted
    # endpoint never learns a single email address.
    cfg = load_json("config.json", {})
    track_url = (cfg.get("open_tracking_url") or "").strip()
    if track_url and "REPLACE" not in track_url.upper():
        try:
            import open_tracking
            opens = open_tracking.resolve_opens(track_url)
        except Exception as e:
            # track_url is cleared so the summary prints "untracked" rather than a
            # confident 0.0% - an unreachable endpoint is not evidence of no opens.
            opens, track_url = {}, ""
            print(f"!! OPEN TRACKING UNREACHABLE ({e})")
            print("   Open figures are OMITTED from the summary below, not reported as zero.")
        for addr, info in (opens or {}).items():
            if addr in rows:
                rows[addr]["Opened"] = "yes"
                rows[addr]["Open Date"] = (info.get("first") or "")[:10]
    write_tracker(rows)

    replied = sum(1 for r in rows.values() if r.get("Replied") == "yes")
    print(f"tracker.csv: {len(rows)} contacted, {replied} replied ({newly} new this run). "
          f"Open in Google Sheets (drag into Drive, or File > Import).")

    # --- A/B: reply rate by first-touch template -------------------------------------
    # Reply rate is measured against DELIVERED, not sent. A bounce never reached a human,
    # so including it would judge the copy on something the copy did not cause.
    print(f"\nRATES BY TEMPLATE (cutover {CUTOVER_ISO})")
    hdr = f"{'template':<22}{'sent':>6}{'bounced':>9}{'delivered':>11}"
    hdr += f"{'tracked':>9}{'opened':>8}{'open rate':>11}" if track_url else ""
    hdr += f"{'replied':>9}{'reply rate':>12}"
    print(hdr)
    print("-" * len(hdr))
    for label in (OLD_LABEL, NEW_LABEL):
        cohort = [r for r in rows.values() if r.get("Template") == label]
        sent = len(cohort)
        bounced = sum(1 for r in cohort if r.get("Delivered") == "no")
        delivered = sent - bounced
        reps = sum(1 for r in cohort if r.get("Replied") == "yes")
        line = f"{label:<22}{sent:>6}{bounced:>9}{delivered:>11}"
        if track_url:
            # Denominator is TRACKED and delivered - mail sent before tracking existed
            # carries no pixel and could never register, so including it would report a
            # permanent and meaningless 0%.
            trk = sum(1 for r in cohort if r.get("Tracked") == "yes" and r.get("Delivered") != "no")
            op = sum(1 for r in cohort if r.get("Opened") == "yes")
            line += f"{trk:>9}{op:>8}"
            line += f"{100*op/trk:>10.1f}%" if trk else f"{'untracked':>11}"
        rate = f"{100*reps/delivered:.1f}%" if delivered else "n/a"
        line += f"{reps:>9}{rate:>12}"
        print(line)
    if track_url:
        print("\n  Opens are a WEAK signal: Gmail's image proxy and Apple Mail Privacy"
              "\n  Protection fetch the pixel with no human involved, and anyone blocking"
              "\n  images never registers at all. Use it directionally; replies are real.")
    new_delivered = sum(1 for r in rows.values()
                        if r.get("Template") == NEW_LABEL and r.get("Delivered") != "no")
    if new_delivered < 50:
        print(f"\n  NOTE: only {new_delivered} delivered on the new template so far. Reply rates "
              f"below ~50 sends\n  are mostly noise - one reply either way swings the number by "
              f"percentage points.\n  Treat the comparison as meaningful once both cohorts clear 50.")


if __name__ == "__main__":
    main()
