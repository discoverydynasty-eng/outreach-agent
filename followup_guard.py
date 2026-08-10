#!/usr/bin/env python3
"""
followup_guard.py - the enforced gate for the 3-step follow-up sequence (added 2026-07-18).

Mirrors send_guard.py's role: nothing here is drafted by the Claude CLI either, because
the 3 follow-up emails are fixed, dictated copy (see followup_content.py) with only a
handful of fields filled in per contact - deterministic string-fill is more reliable than
another LLM pass and has no background-task timeout risk. This script is the ONLY thing
that sends these.

Every send threads onto the ORIGINAL cold email via In-Reply-To/References (the same
Message-ID chain) and keeps the ORIGINAL subject as "Re: <original subject>" - the hard
rule is that every follow-up stays in the same Gmail thread as email one, which needs an
unbroken References chain far more than it needs a creative subject line.

Stages (computed off real dates - this is what makes "start immediately" for the whole
day-one backlog and the normal cadence for brand-new contacts literally the same rule):
  0 -> 1: due once >= followup_initial_gap_days since the ORIGINAL cold email
  1 -> 2: due once >= 2 days since stage 1 was sent   (Umar: "follow up... 2 days later")
  2 -> 3: due once >= 3 days since stage 2 was sent   (Umar: "follow up... 3 days later")
  3: sequence complete, never sent again

A contact drops out of the sequence permanently, at any stage, the moment they reply
(checked over IMAP right before each send) or becomes suppressed - a real code check,
not a prompt.

Usage:
    python followup_guard.py            # send what's due (respects AUTO/REVIEW + cap)
    python followup_guard.py --dry-run  # print decisions, send/write nothing except the
                                         # one-time Message-ID backfill cache
"""

import argparse
import datetime
import email as email_lib
import imaplib

import followup_content as fc
import send_guard as sg  # reuse config/state helpers, SMTP + send_email, validators

IMAP_HOST = "imap.gmail.com"


def today():
    return datetime.date.today()


def days_since(date_iso):
    try:
        d = datetime.date.fromisoformat(date_iso)
    except Exception:
        return 0
    return (today() - d).days


def _next_target_date(c, initial_gap):
    """The date this contact's NEXT follow-up is supposed to go out (None if finished)."""
    st = c.get("stage", 0)
    if st == 0 and c.get("original_date_iso"):
        return datetime.date.fromisoformat(c["original_date_iso"]) + datetime.timedelta(days=initial_gap)
    if st == 1 and c.get("stage1_sent_date"):
        return datetime.date.fromisoformat(c["stage1_sent_date"]) + datetime.timedelta(days=2)
    if st == 2 and c.get("stage2_sent_date"):
        return datetime.date.fromisoformat(c["stage2_sent_date"]) + datetime.timedelta(days=3)
    return None


def health_report(contacts, history, send_log, cap, initial_gap, day):
    """Queue-health diagnostics printed at the end of every run.

    Deliberately does NOT just alarm on "queue is big" - the day-one backfill made it 200+
    by definition, so that would cry wolf every single run. Three sharper signals instead:

      1. CADENCE   - contacts already mid-sequence (stage 1 or 2) whose next email is past
                     its target date. This is the real harm: the spec is stage2 exactly 2
                     days after stage1, and a saturated cap silently stretches that out.
                     Stage-0 (never-touched) backlog is reported separately as context, not
                     as an alarm, because it's expected to be large while the backlog drains.
      2. TREND     - today's waiting count vs ~a week ago, from queue_history. Directly
                     answers "is the queue growing?"
      3. STRUCTURAL- every contact eventually costs 3 follow-up sends, so cap/3 is the
                     ceiling on new contacts/day. If cold outreach sustains more than that,
                     the queue grows without bound no matter how long you wait - the useful
                     warning fires BEFORE the lag becomes visible.
    """
    lines, warnings = [], []
    active = [c for c in contacts.values() if c.get("status") == "active"]

    waiting, not_started, late = 0, 0, []
    for c in active:
        t = _next_target_date(c, initial_gap)
        if not t:
            continue
        if t <= day:
            waiting += 1
            if c.get("stage", 0) == 0:
                not_started += 1
            else:
                late.append(((day - t).days, c.get("stage")))
    lines.append(f"  waiting now : {waiting} due ({not_started} never contacted, "
                 f"{waiting - not_started} mid-sequence) | cap {cap}/day")

    # 1. cadence
    slipped = [lag for lag, _ in late if lag >= 1]
    if slipped:
        worst = max(slipped)
        lines.append(f"  cadence     : {len(slipped)} mid-sequence contact(s) past their target "
                     f"date, worst {worst}d late")
        if worst >= 3:
            warnings.append(f"cadence slipping - someone is {worst} days late for their next "
                            f"email (spec is 2d after stage 1, 3d after stage 2). The daily cap "
                            f"is being spent on older contacts before they get their turn.")
    else:
        lines.append("  cadence     : on time (nobody mid-sequence is overdue)")

    # 2. trend
    prior = [h for h in history if (day - datetime.date.fromisoformat(h["date"])).days >= 7]
    if prior:
        then = prior[-1]
        delta = waiting - then["waiting"]
        arrow = "GROWING" if delta > 0 else ("shrinking" if delta < 0 else "flat")
        lines.append(f"  trend       : {then['waiting']} on {then['date']} -> {waiting} today "
                     f"({delta:+d}, {arrow})")
        if delta > 0:
            warnings.append(f"queue grew {delta:+d} over the last week ({then['waiting']} -> "
                            f"{waiting}). Follow-ups are being created faster than they go out.")
    else:
        lines.append(f"  trend       : building history ({len(history)} day(s) recorded, need 7)")

    # 3. structural intake rate
    recent = [e for e in send_log
              if e.get("date_iso") and 0 <= (day - datetime.date.fromisoformat(e["date_iso"])).days < 7]
    cold_rate = len(recent) / 7.0
    sustainable = cap / 3.0
    verdict = "OK" if cold_rate <= sustainable else "TOO FAST"
    lines.append(f"  intake      : cold outreach ~{cold_rate:.1f}/day vs {sustainable:.1f}/day "
                 f"sustainable at cap {cap} -> {verdict}")
    if cold_rate > sustainable:
        need = int(round(cold_rate * 3))
        warnings.append(f"cold outreach (~{cold_rate:.1f}/day) exceeds what a {cap}/day follow-up "
                        f"cap can sustain ({sustainable:.1f}/day, since each contact costs 3 sends). "
                        f"The queue will grow indefinitely. Fix: raise daily_followup_cap to ~{need}, "
                        f"or lower daily_cap to ~{int(sustainable)}, or accept a growing lag.")
    return lines, warnings, waiting


def imap_connect(sender_email, app_password):
    M = imaplib.IMAP4_SSL(IMAP_HOST)
    M.login(sender_email, app_password)
    return M


def find_original_message_id(M, address):
    """Historical contacts (sent before 2026-07-18) never had their Message-ID captured
    at send time. Look it up once from Sent Mail; every contact sent from now on already
    carries it in send_log.json, so this only ever runs against the pre-fix backlog."""
    M.select('"[Gmail]/Sent Mail"')
    typ, data = M.search(None, "TO", '"%s"' % address)
    if typ != "OK" or not data or not data[0]:
        return None
    ids = data[0].split()
    t, d = M.fetch(ids[-1], "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
    if t != "OK" or not d or not d[0]:
        return None
    msg = email_lib.message_from_bytes(d[0][1])
    mid = msg.get("Message-ID")
    return mid.strip() if mid else None


def has_replied(M, address):
    # All Mail covers every category tab (Primary/Promotions/Social/Updates); Spam is a
    # separate mailbox in Gmail's IMAP and has to be checked on its own.
    for folder in ('"[Gmail]/All Mail"', '"[Gmail]/Spam"'):
        M.select(folder)
        typ, data = M.search(None, "FROM", '"%s"' % address)
        if typ == "OK" and data and data[0]:
            return True
    return False


def sync_from_send_log(state, send_log_sent, suppressed):
    """Every send_log contact not suppressed gets a followups.json entry. Additive only -
    never touches or overwrites an existing contact's progress."""
    contacts = state["contacts"]
    added = 0
    for e in send_log_sent:
        addr = (e.get("email") or "").lower()
        if not addr or addr in suppressed or addr in contacts:
            continue
        contacts[addr] = {
            "business": e.get("business"), "email": e.get("email"),
            "owner_name": e.get("owner_name"), "original_subject": e.get("subject"),
            "original_date_iso": e.get("date_iso"), "original_message_id": e.get("message_id"),
            "problem_type": e.get("problem_type"), "status": "active", "stage": 0,
            "stage1_sent_date": None, "stage1_message_id": None,
            "stage2_sent_date": None, "stage2_message_id": None,
            "stage3_sent_date": None, "stage3_message_id": None,
        }
        added += 1
    return added


def backfill_message_ids(state, M):
    found, missing = 0, 0
    for c in state["contacts"].values():
        if c.get("original_message_id") or c.get("status") != "active":
            continue
        mid = find_original_message_id(M, c["email"])
        if mid:
            c["original_message_id"] = mid
            found += 1
        else:
            missing += 1
    return found, missing


def due_candidates(state, initial_gap_days):
    due = []
    for addr, c in state["contacts"].items():
        if c.get("status") != "active":
            continue
        stage = c.get("stage", 0)
        if stage == 0 and c.get("original_message_id"):
            if days_since(c["original_date_iso"]) >= initial_gap_days:
                due.append((c["original_date_iso"], addr, 1))
        elif stage == 1 and c.get("stage1_sent_date") and days_since(c["stage1_sent_date"]) >= 2:
            due.append((c["original_date_iso"], addr, 2))
        elif stage == 2 and c.get("stage2_sent_date") and days_since(c["stage2_sent_date"]) >= 3:
            due.append((c["original_date_iso"], addr, 3))
    due.sort(key=lambda t: t[0])  # oldest original contact goes first - most overdue
    return [(addr, stage) for _, addr, stage in due]


def build_thread_headers(contact, own_new_mid_placeholder=None):
    refs = [contact["original_message_id"]]
    for k in ("stage1_message_id", "stage2_message_id"):
        if contact.get(k):
            refs.append(contact[k])
    return {"In-Reply-To": refs[-1], "References": " ".join(refs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = sg.load_json("config.json", {})
    mode = (config.get("autonomy_mode") or "REVIEW").strip().upper()
    cap = int(config.get("daily_followup_cap", 0))
    initial_gap = int(config.get("followup_initial_gap_days", 3))
    mailing = (config.get("physical_mailing_address") or "").strip()
    optout = (config.get("optout_sentence") or "").strip()
    sender_email = (config.get("sender_email") or "").strip()
    sender_name = (config.get("sender_name") or "").strip()
    from_display = sg.formataddr((sender_name, sender_email)) if sender_email else None

    if not mailing or not optout or not sender_email:
        print("BLOCKED: config.json is missing mailing address / opt-out / sender_email. Nothing sent.")
        return
    if cap <= 0:
        print("Follow-ups disabled: config.daily_followup_cap is 0. Nothing to do.")
        return

    send_log = sg.load_json("send_log.json", {"sent": []}).get("sent", [])
    suppressed = {e.lower() for e in sg.load_json("suppressed.json", {"emails": []}).get("emails", [])}
    state = sg.load_json("followups.json", {"contacts": {}})
    added = sync_from_send_log(state, send_log, suppressed)
    if added:
        print(f"followups.json: added {added} new contact(s) from send_log.json")

    app_password = sg.get_app_password()
    if not app_password:
        print("BLOCKED: GMAIL_APP_PASSWORD not set. Nothing sent.")
        return

    M = imap_connect(sender_email, app_password)
    try:
        found, missing = backfill_message_ids(state, M)
        if found or missing:
            print(f"Message-ID backfill: {found} found via Sent Mail, {missing} still missing (retried next run)")
        sg.save_json("followups.json", state)  # keep backfill progress even on a dry run

        candidates = due_candidates(state, initial_gap)
        print(f"{len(candidates)} contact(s) due for a follow-up today (daily_followup_cap {cap})")

        sent_today_count = sum(
            1 for c in state["contacts"].values()
            if sg.today_iso() in (c.get("stage1_sent_date"), c.get("stage2_sent_date"), c.get("stage3_sent_date"))
        )

        server = None
        if not args.dry_run and mode == "AUTO":
            server = sg.open_smtp(sender_email, app_password)

        sent_this_run = 0
        try:
            for addr, stage in candidates:
                if sent_today_count + sent_this_run >= cap:
                    print(f"STOP    daily_followup_cap of {cap} reached")
                    break
                c = state["contacts"][addr]

                if addr in suppressed:
                    c["status"] = "suppressed"
                    print(f"SKIP    {addr} -> suppressed since initial contact")
                    continue
                if has_replied(M, addr):
                    c["status"] = "replied"
                    print(f"SKIP    {addr} -> replied, sequence stopped")
                    continue

                body = fc.render(stage, c, optout, mailing)
                subject = "Re: " + (c.get("original_subject") or "")
                if fc.PLACEHOLDER_RE.search(body):
                    print(f"REJECT  {addr} -> stage {stage} body has an unfilled placeholder (bug, not sent)")
                    continue

                if args.dry_run:
                    print(f"WOULD SEND  {addr}  stage {stage}  subject={subject!r}")
                    sent_this_run += 1  # so the preview actually stops at cap, like a real run would
                    continue
                if mode != "AUTO":
                    print(f"HOLD    {addr} stage {stage} - autonomy_mode is {mode}, follow-ups only auto-send in AUTO")
                    continue

                new_mid = sg.make_msgid(domain="gmail.com")
                headers = build_thread_headers(c)
                sg.send_email(server, sender_email, addr, subject, body, from_display,
                               extra_headers=headers, message_id=new_mid)
                c[f"stage{stage}_sent_date"] = sg.today_iso()
                c[f"stage{stage}_message_id"] = new_mid
                c["stage"] = stage
                sent_this_run += 1
                print(f"SENT    {addr}  stage {stage}")
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass
    finally:
        try:
            M.logout()
        except Exception:
            pass

    # Queue health, measured AFTER this run's sends so it reflects the true remaining state.
    history = state.get("queue_history", [])
    lines, warnings, waiting_now = health_report(
        state["contacts"], history, send_log, cap, initial_gap, today())

    if not args.dry_run:
        # One datapoint per day (overwrite if this is a re-run), capped at 90 days, so the
        # week-over-week trend above has something real to compare against.
        history = [h for h in history if h["date"] != sg.today_iso()]
        history.append({"date": sg.today_iso(), "waiting": waiting_now, "sent": sent_this_run})
        state["queue_history"] = history[-90:]

    sg.save_json("followups.json", state)

    if args.dry_run:
        print("\nDRY RUN: nothing sent. followups.json only updated with Message-ID backfill.")
    else:
        print(f"\nDone. Sent {sent_this_run} follow-up(s) today "
              f"({sent_today_count + sent_this_run}/{cap} follow-up cap).")

    print("QUEUE HEALTH")
    for ln in lines:
        print(ln)
    # Warnings last so they survive the log tail that followup_only.ps1 pipes into cron.log.
    for w in warnings:
        print(f"** WARNING: {w}")
    if not warnings:
        print("** OK: follow-up queue is keeping up.")


if __name__ == "__main__":
    main()
