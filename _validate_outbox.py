import json, re

cfg = json.load(open("config.json"))
OPTOUT = cfg["optout_sentence"].strip()
ADDR = cfg["physical_mailing_address"].strip()
CAP = cfg["daily_cap"]

guard = open("send_guard.py").read()
FREE = set(re.findall(r'"([^"]+)"', re.search(r"FREE_EMAIL_DOMAINS = \{(.*?)\}", guard, re.S).group(1)))
PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]|<[^>]+>")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

sl = json.load(open("send_log.json"))["sent"]
sent_emails = {(r.get("email") or "").lower() for r in sl if r.get("email")}
sent_domains = {(r.get("email") or "").lower().split("@")[-1] for r in sl if r.get("email")}
suppressed = {e.lower() for e in json.load(open("suppressed.json"))["emails"]}

ob = json.load(open("outbox.json"))["pending"]
fails, warns = [], []
seen = set()

for i, it in enumerate(ob, 1):
    b, e = it["business"], (it.get("email") or "").strip()
    subj, body = it.get("subject") or "", it.get("body") or ""
    owner = (it.get("owner_name") or "").strip()
    first = owner.split()[0] if owner else ""
    d = e.lower().split("@")[-1]

    def bad(m): fails.append(f"[{i}] {b}: {m}")

    if not EMAIL_RE.match(e): bad("invalid email format")
    if e.lower() in suppressed: bad("SUPPRESSED address")
    if e.lower() in sent_emails: bad("email already contacted")
    if d in sent_domains and d not in FREE: bad("domain already contacted (non-free)")
    if e.lower() in seen: bad("duplicate within this outbox")
    seen.add(e.lower())
    if OPTOUT not in body: bad("opt-out sentence missing")
    if ADDR not in body: bad("mailing address missing")
    if PLACEHOLDER_RE.search(subj): bad("placeholder in SUBJECT")
    if PLACEHOLDER_RE.search(body): bad("placeholder in BODY")
    if "fad" in (subj + " " + body).lower(): bad("contains 'Fad'")
    if first and first.lower() not in subj.lower(): bad(f"owner '{first}' missing from subject")
    for ch in ["—", "–"]:
        if ch in subj + body: bad("contains em/en dash")
    # Template set by the operator 2026-08-10: their AI wording with a PROBLEM -> AGITATE
    # beat inserted as line 2. Subject mirrors the closing question.
    if "is ai on your radar at" not in subj.lower(): bad("subject is not the locked 2026-08-10 form")
    if not body.startswith("Hi "): bad("body does not open with 'Hi '")
    if "\nUmar\n" not in body: bad("not signed Umar")
    for jargon in ["speed-to-lead", "omnichannel", "AI-powered", "game-chang", "revolutionary", "synergy"]:
        if jargon.lower() in body.lower(): bad("jargon: " + jargon)
    for beat in ["I was on your website and noticed that you",
                 "I imagine one of the biggest challenges is",
                 "find cost inefficiencies like that",
                 "reclaim 10 to 15 hours a week",
                 "Is adopting AI currently on your radar for"]:
        if beat not in body: bad("missing beat: " + beat)
    # NO COMPLIMENTS (operator instruction). The observation line stops at the fact.
    if "which stood out" in body.lower():
        bad("observation line carries 'which stood out' - no compliments allowed")
    for flattery in ["impressive", "amazing", "incredible", "outstanding", "fantastic",
                     "love what you", "great work", "very professional", "website looks great",
                     "top notch", "second to none", "a testament to", "speaks volumes",
                     "hats off", "kudos", "well deserved", "you should be proud"]:
        if flattery in body.lower():
            bad(f"compliment in body: '{flattery}' - no compliments allowed")
    # The agitate clause must not scold. These phrasings imply the owner is failing.
    for scold in ["you're losing", "you are losing", "costing you thousands", "you don't realise",
                  "you don't realize", "you're missing out", "you have no idea", "bleeding money"]:
        if scold in body.lower():
            bad(f"agitate line scolds the owner: '{scold}' - name the cost, do not accuse")
    # Retired phrasings must not resurface.
    for stale in ["which is why I wanted to reach out", "claw back 10 to 15"]:
        if stale.lower() in body.lower(): bad("retired phrasing: " + stale)
    # Contractions, per the VOICE rules.
    problem_line = next((p for p in body.split("\n\n") if p.startswith("I imagine")), "")
    for stiff in ["that is ", "you are ", "it is ", "they are ", "do not ", "does not ",
                  "did not ", "have not ", "cannot "]:
        if stiff in problem_line.lower():
            warns.append(f"[{i}] {b}: uncontracted '{stiff.strip()}' in the problem line")
    # 4 body lines between greeting and signoff
    paras = [p for p in body.split("\n\n") if p.strip()]
    core = [p for p in paras if p.startswith(("I was on your website", "I imagine", "I am reaching out", "I'm reaching out", "I've helped", "Is adopting AI"))]
    if len(core) != 5: warns.append(f"[{i}] {b}: {len(core)} core lines (expected 5)")
    if d not in FREE and not re.match(r"^[a-z]+@", e.lower()):
        warns.append(f"[{i}] {b}: non-free domain {d}, confirm owner-personal")

print("DRAFTS:", len(ob), "| CAP:", CAP, "| over cap:", len(ob) > CAP)
print("\nFAILURES:", len(fails))
for f in fails: print("  FAIL", f)
print("\nWARNINGS:", len(warns))
for w in warns: print("  warn", w)

print("\n%-46s %-34s %s" % ("BUSINESS", "EMAIL", "SUBJECT"))
for it in ob:
    print("%-46s %-34s %s" % (it["business"][:45], it["email"][:33], it["subject"]))
print("\nRESULT:", "ALL CLEAR" if not fails else "BLOCKED - fix failures")
