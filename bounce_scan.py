#!/usr/bin/env python3
"""
bounce_scan.py - scan Gmail for delivery failures and suppress the dead addresses.

Closes a real gap found 2026-07-26: `suppressed.json` only ever collected
verification-failures and no-MX addresses, so mailboxes that actually HARD BOUNCED were
never suppressed. The follow-up sequence then cheerfully mailed them again - one address
bounced on its cold email (2026-07-08) and bounced a second time on its follow-up
(2026-07-27). Repeatedly mailing addresses that return 5xx is one of the
strongest negative sender-reputation signals there is, which matters a lot more once
follow-ups triple the number of times each address gets touched.

Only PERMANENT failures (5.x.x) are suppressed. Temporary 4.x.x failures (greylisting,
transient server trouble, "try again later") are deliberately left alone - those usually
deliver on a retry, and suppressing them would throw away real leads.

Usage:
    python bounce_scan.py             # scan + suppress
    python bounce_scan.py --dry-run   # report only, change nothing
"""

import argparse
import email
import imaplib
import re

import send_guard as sg

IMAP_HOST = "imap.gmail.com"
FOLDERS = ('"INBOX"', '"[Gmail]/Spam"')
SINCE = "01-Jun-2026"

NDR_FROM = ("mailer-daemon", "postmaster")
NDR_SUBJECT = ("delivery status", "undeliverable", "delivery incomplete",
               "failure notice", "returned mail", "delivery has failed",
               "not delivered", "delivery failure")
ADDR_RE = re.compile(r"[\w\.\-\+]+@[\w\.\-]+\.\w+")


def is_ndr(msg):
    frm = (msg.get("From") or "").lower()
    subj = (msg.get("Subject") or "").lower()
    return any(k in frm for k in NDR_FROM) or any(k in subj for k in NDR_SUBJECT)


def extract_failures(msg):
    """-> {address: (status, diagnostic)} for every recipient named in this NDR.

    Prefers the machine-readable RFC 3464 message/delivery-status part; falls back to
    scraping the text when a provider sends a human-only bounce.
    """
    out = {}
    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        payload = part.get_payload()
        if not isinstance(payload, list):
            continue
        for block in payload:
            if not hasattr(block, "get"):
                continue
            fr = block.get("Final-Recipient") or block.get("Original-Recipient")
            if not fr:
                continue
            addr = fr.split(";")[-1].strip().strip("<>").lower()
            status = (block.get("Status") or "").strip()
            diag = (block.get("Diagnostic-Code") or "").strip().replace("\n", " ")
            out[addr] = (status, diag[:160])
    if out:
        return out

    # Fallback: pull the status code and any addresses out of the raw text.
    try:
        raw = msg.as_string()
    except Exception:
        return out
    m = re.search(r"\b([45]\.\d{1,3}\.\d{1,3})\b", raw)
    status = m.group(1) if m else ""
    if not status:
        return out
    for a in ADDR_RE.findall(raw):
        a = a.lower()
        if "mailer-daemon" in a or "postmaster" in a or a.endswith("@gmail.com"):
            continue
        out[a] = (status, "(parsed from bounce text)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = sg.load_json("config.json", {})
    sender = (config.get("sender_email") or "").strip()
    pw = sg.get_app_password()
    if not sender or not pw:
        print("BLOCKED: sender_email or GMAIL_APP_PASSWORD missing. Nothing scanned.")
        return

    send_log = sg.load_json("send_log.json", {"sent": []}).get("sent", [])
    contacted = {(e.get("email") or "").lower() for e in send_log if e.get("email")}
    suppressed = {e.lower() for e in sg.load_json("suppressed.json", {"emails": []}).get("emails", [])}
    followups = sg.load_json("followups.json", {"contacts": {}})

    before = len(suppressed)
    hard, soft, seen_ndrs = {}, {}, 0

    M = imaplib.IMAP4_SSL(IMAP_HOST)
    M.login(sender, pw)
    try:
        for folder in FOLDERS:
            typ, _ = M.select(folder, readonly=True)
            if typ != "OK":
                continue
            typ, data = M.search(None, "SINCE", SINCE)
            ids = data[0].split() if data and data[0] else []
            for mid in ids:
                t, d = M.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                if t != "OK" or not d or not d[0]:
                    continue
                if not is_ndr(email.message_from_bytes(d[0][1])):
                    continue
                seen_ndrs += 1
                t2, d2 = M.fetch(mid, "(BODY.PEEK[])")
                if t2 != "OK" or not d2 or not d2[0]:
                    continue
                full = email.message_from_bytes(d2[0][1])
                for addr, (status, diag) in extract_failures(full).items():
                    # Only ever act on addresses we actually mailed.
                    if addr not in contacted:
                        continue
                    if status.startswith("5"):
                        hard[addr] = (status, diag)
                    elif status.startswith("4"):
                        soft[addr] = (status, diag)
    finally:
        try:
            M.logout()
        except Exception:
            pass

    print(f"scanned {seen_ndrs} bounce message(s) since {SINCE}")
    print(f"  permanent (5.x.x) failures : {len(hard)}")
    print(f"  temporary (4.x.x) failures : {len(soft)}  <- left alone on purpose, these retry")

    new = sorted(a for a in hard if a not in suppressed)
    print(f"  already suppressed         : {len(hard) - len(new)}")
    print(f"  NEWLY suppressed           : {len(new)}")
    for a in new:
        st, diag = hard[a]
        print(f"     + {a}  [{st}] {diag[:90]}")

    if args.dry_run:
        print("\nDRY RUN: nothing written.")
        return

    suppressed.update(new)
    sg.save_json("suppressed.json", {"emails": sorted(suppressed)})

    # Stop the follow-up sequence for anyone who bounced, so they don't get emails 2 and 3.
    stopped = 0
    for addr in hard:
        c = followups.get("contacts", {}).get(addr)
        if c and c.get("status") == "active":
            c["status"] = "bounced"
            stopped += 1
    if stopped:
        sg.save_json("followups.json", followups)

    print(f"\nsuppressed.json: {before} -> {len(suppressed)} address(es)")
    print(f"follow-up sequences stopped for {stopped} bounced contact(s)")


if __name__ == "__main__":
    main()
