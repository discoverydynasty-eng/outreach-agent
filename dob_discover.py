#!/usr/bin/env python3
"""
dob_discover.py - turn the NYC DOB licence list into workable leads by finding each
business's real website and a PUBLISHED email on it.

Context: `_dob_clean.json` holds 1,263 licensed NYC electricians and master plumbers,
parsed from the DOB PDFs. Every row already has the business name, street address, phone,
borough AND the licence holder's first/last name - i.e. the owner name the drafting agent
otherwise spends most of its research budget hunting. The single missing field is an email,
and guardrail #3 forbids guessing one. That makes this list the largest untapped pool the
project has, and the only thing standing between it and being usable is this pass.

Method, in cheapest-first order so the expensive step runs on the fewest candidates:
  1. derive candidate domains from the business name (drop INC/LLC/CORP, try .com/.net/.nyc)
  2. DNS-resolve them - free, fast, and kills most candidates immediately
  3. fetch the homepage of whatever survives
  4. PROVE the site is really that business before trusting anything on it (see below)
  5. scrape mailto:/text emails from the homepage and its contact/about pages

Step 4 is the part that matters. A guessed domain that happens to resolve is worthless -
worse than worthless, since sending to it burns reputation. The DOB row gives us the
business's real phone number, so a page showing that exact number is near-conclusive proof
of identity; failing that, several distinctive name tokens must appear. Parked and
for-sale domains are rejected outright.

Emails found this way are published on the business's own website, which is exactly what
guardrail #3 asks for - unlike the OSM hints, which are community-edited and untrusted.

Usage:
    python dob_discover.py --limit 50 --dry-run   # measure hit rate on a sample
    python dob_discover.py --limit 300            # process and append to leads.json
    python dob_discover.py                        # the whole list
"""

import argparse
import concurrent.futures as futures
import json
import re
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import dns.resolver

import send_guard as sg

HERE = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

SUFFIXES = re.compile(r"\b(inc|llc|corp|corporation|co|ltd|company|svcs|services|service|"
                      r"contracting|contractors|contractor|the|and|of|group|enterprises|"
                      r"enterprise|associates|assoc|sons|son|bros|brothers)\b", re.I)
NONWORD = re.compile(r"[^a-z0-9]+")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Addresses that are never a business owner's inbox.
JUNK_EMAIL = re.compile(
    r"(example|test|your|email|name|domain|sentry|wix|squarespace|godaddy|wordpress|"
    r"jquery|\.png|\.jpg|\.gif|\.webp|\.svg|noreply|no-reply|donotreply|abuse@|"
    r"postmaster@|webmaster@|privacy@|dmca@|sales@godaddy)", re.I)
# Signs a domain resolves but is parked / for sale / not actually their site.
PARKED = re.compile(r"(domain (is )?for sale|buy this domain|parked (free )?(at|by)|"
                    r"this domain may be for sale|under construction|coming soon|"
                    r"godaddy\.com/forsale|hugedomains|afternic|sedo)", re.I)
CONTACT_PATHS = ("/contact", "/contact-us", "/contactus", "/about", "/about-us")


def tokens(business):
    """Distinctive lowercase word tokens from a business name (suffixes stripped)."""
    s = SUFFIXES.sub(" ", business.lower())
    return [t for t in NONWORD.sub(" ", s).split() if len(t) > 2]


def candidate_domains(business):
    """Plausible domains for this business, most likely first. Kept deliberately small -
    each extra guess is another DNS lookup across 1,263 businesses."""
    ts = tokens(business)
    if not ts:
        return []
    joined = "".join(ts)
    out = []
    # Deliberately NO single-token candidate. "INLINE ELECTRICAL CORP" -> inline.com,
    # "VERTEX ELECTRIC CORP" -> vertex.net, "PEGASUS ELECTRICAL" -> pegasus.com: those are
    # real, resolving sites belonging to entirely unrelated companies, and a first pass
    # happily harvested addresses off them. One-word dictionary domains are almost never a
    # small NYC contractor's site, so the guess is not worth the false-positive risk.
    bases = [joined,
             "-".join(ts),                                  # baytech-electric.com
             "".join(ts[:2]) if len(ts) > 2 else None,
             joined + "nyc" if len(joined) <= 20 else None]  # ...nyc.com is common here
    for base in filter(None, bases):
        if len(base) < 4 or len(base) > 32:
            continue
        for tld in (".com", ".net", ".nyc"):
            d = base + tld
            if d not in out:
                out.append(d)
    return out[:10]


def resolves(domain):
    try:
        dns.resolver.resolve(domain, "A")
        return True
    except Exception:
        try:
            dns.resolver.resolve(domain, "MX")
            return True
        except Exception:
            return False


def fetch(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept": "text/html,*/*"})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # many small-contractor sites have broken certs
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            if r.status != 200:
                return ""
            raw = r.read(400_000)
        return raw.decode("utf-8", "ignore")
    except Exception:
        return ""


def digits(s):
    return re.sub(r"\D", "", s or "")


def identity_confirmed(html, row):
    """Is this page actually THIS business? A resolving guessed domain proves nothing.

    Tuned for PRECISION over volume, on purpose. Emailing the wrong company is worse than
    finding no email at all: it bounces or gets marked spam, it wastes the daily cap, and
    on client-facing outreach it is simply embarrassing. So the bar is the DOB phone number
    appearing on the page - we know their real number, and a page printing it is
    near-conclusive. A name-token match alone is NOT enough (that is what let inline.com
    and vertex.net through); it is accepted only for names distinctive enough that
    coincidence is implausible.
    """
    if not html or PARKED.search(html[:6000]):
        return False, "parked/for-sale"
    text = re.sub(r"<[^>]+>", " ", html).lower()
    phone = digits(row.get("phone"))
    if phone and len(phone) == 10 and phone in digits(text):
        return True, "phone match"
    ts = tokens(row.get("business", ""))
    # Every candidate domain is now built from at least two joined name tokens (single-word
    # guesses are banned above), so the domain string itself already encodes the business
    # name - "baytechelectric.com" is not a coincidence the way "inline.com" was. On top of
    # that, require EVERY token to appear on the page.
    if len(ts) >= 2 and all(t in text for t in ts):
        return True, f"full name match ({len(ts)} tokens)"
    return False, "no identity match"


FREEMAIL = {"gmail.com", "yahoo.com", "aol.com", "hotmail.com", "outlook.com",
            "msn.com", "verizon.net", "optonline.net", "icloud.com", "me.com",
            "att.net", "comcast.net", "mail.com", "earthlink.net", "nyc.rr.com"}


def emails_from(html, domain):
    """Only addresses that plausibly belong to THIS business.

    Accepts an address on the site's own domain, or a free-provider address (very common
    for small contractors: a gmail address on their own site is genuinely theirs).
    Everything else is rejected - a page can carry a web designer's address, an ad
    network's, or, as seen in the first pass, an unrelated address from a compromised
    page. Those are not leads.
    """
    base = domain.rsplit(".", 1)[0].replace("-", "")
    out = []
    for m in EMAIL_RE.findall(html or ""):
        e = m.strip().strip(".").lower()
        if JUNK_EMAIL.search(e) or len(e) > 60:
            continue
        edom = e.split("@")[-1]
        same_site = edom == domain or edom.endswith("." + domain) or base in edom.replace("-", "")
        if same_site or edom in FREEMAIL:
            out.append((0 if same_site else 1, e))
    seen, ordered = set(), []
    for _, e in sorted(out):
        if e not in seen:
            seen.add(e)
            ordered.append(e)
    return ordered


def investigate(row):
    """-> dict with what we found for one DOB business (or None if nothing usable)."""
    for domain in candidate_domains(row.get("business", "")):
        if not resolves(domain):
            continue
        for scheme in ("https://", "http://"):
            html = fetch(scheme + domain)
            if not html:
                continue
            ok, why = identity_confirmed(html, row)
            if not ok:
                break  # domain resolves but isn't them; don't try other schemes
            found = emails_from(html, domain)
            if not found:
                for path in CONTACT_PATHS:
                    more = fetch(scheme + domain + path)
                    found = emails_from(more, domain)
                    if found:
                        break
            if found:
                return {"row": row, "domain": domain, "url": scheme + domain,
                        "email": found[0], "all_emails": found[:4], "why": why}
            return {"row": row, "domain": domain, "url": scheme + domain,
                    "email": None, "all_emails": [], "why": why + ", no email published"}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = json.loads((HERE / "_dob_clean.json").read_text(encoding="utf-8"))
    send_log = sg.load_json("send_log.json", {"sent": []}).get("sent", [])
    suppressed = {e.lower() for e in sg.load_json("suppressed.json", {"emails": []}).get("emails", [])}
    leads_doc = sg.load_json("leads.json", {"leads": []})
    leads = leads_doc.get("leads", [])

    sent_emails = {(e.get("email") or "").lower() for e in send_log if e.get("email")}
    sent_domains = {(e.get("email") or "").split("@")[-1].lower() for e in send_log if e.get("email")}
    known_names = {(l.get("business") or "").strip().lower() for l in leads}
    known_names |= {(e.get("business") or "").strip().lower() for e in send_log}

    todo = [r for r in rows if (r.get("business") or "").strip().lower() not in known_names]
    print(f"{len(rows)} DOB businesses | {len(rows) - len(todo)} already known | {len(todo)} to investigate")
    if args.limit:
        todo = todo[:args.limit]
        print(f"limited to {len(todo)} this run")

    socket.setdefaulttimeout(15)
    results, checked = [], 0
    with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(investigate, todo):
            checked += 1
            if res:
                results.append(res)
            if checked % 25 == 0:
                got = sum(1 for r in results if r.get("email"))
                print(f"  ...{checked}/{len(todo)} checked, {len(results)} sites identified, {got} with email")

    with_email = [r for r in results if r.get("email")]
    print(f"\nchecked {checked} | sites positively identified: {len(results)} "
          f"| PUBLISHED EMAIL FOUND: {len(with_email)}")
    if checked:
        print(f"  site hit rate  : {100*len(results)/checked:.1f}%")
        print(f"  email hit rate : {100*len(with_email)/checked:.1f}%")

    fresh, batch_emails = [], set()
    for r in with_email:
        row, email = r["row"], r["email"]
        dom = email.split("@")[-1].lower()
        if email in sent_emails or email in suppressed or dom in sent_domains:
            continue
        # Dedupe WITHIN this batch too. The DOB list is keyed by licence holder, not by
        # company, so one firm can appear several times with different licensees - Kleinberg
        # Electric showed up 3x and Atlas-Acon 2x, all resolving to the same inbox. The
        # guard would refuse the repeats at send time, but they would silently eat lead
        # slots and clutter leads.json until it did.
        if email in batch_emails:
            continue
        batch_emails.add(email)
        owner = (row.get("first") or "").strip().title() or None
        # A phone match is near-conclusive; a name match is circumstantial and has been
        # wrong before (a two-token name can collide with an unrelated company on the same
        # domain). Downgrade those so the drafting agent treats them as needing a second
        # look rather than as an established fact.
        strong = "phone match" in r["why"]
        fresh.append({
            "business": row.get("business"), "trade": row.get("trade") or "electrician/plumber",
            "area": row.get("area"), "website": r["url"], "phone": row.get("phone"),
            "owner_name": owner, "email": email,
            "email_confidence": "published_site" if strong else "published_site_unverified_match",
            "status": "new", "problem_type": None, "problem": None,
            "source": "NYC DOB licence list + own site (dob_discover.py)",
            "notes": f"Website matched to the DOB record by {r['why']}. Email was scraped "
                     f"from that site, so it is PUBLISHED (guardrail #3 satisfied). Owner "
                     f"first name is the licence holder from the DOB record - verify it is "
                     f"still the right contact before using it."
                     + ("" if strong else
                        " CAUTION: the domain was a NAME-BASED GUESS confirmed only by name "
                        "tokens on the page, not by the DOB phone number. Before writing, "
                        "open the site and confirm it is genuinely this company - a similar "
                        "name can belong to an unrelated firm. If it does not match, mark "
                        "the lead skip_profile.")
                     + f" Other addresses on the site: {', '.join(r['all_emails'][1:]) or 'none'}.",
        })

    print(f"\n{len(fresh)} new leads with a published email")
    for f in fresh[:15]:
        print(f"   {f['business'][:34]:<36} {f['owner_name'] or '-':<12} {f['email']}")

    if args.dry_run:
        print("\nDRY RUN: leads.json unchanged.")
        return
    if fresh:
        leads.extend(fresh)
        sg.save_json("leads.json", {"leads": leads})
        print(f"\nleads.json: +{len(fresh)} -> {len(leads)} total")


if __name__ == "__main__":
    main()
