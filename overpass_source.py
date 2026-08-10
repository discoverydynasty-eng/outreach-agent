#!/usr/bin/env python3
"""
overpass_source.py - free lead sourcing from OpenStreetMap via the Overpass API.

Built 2026-08-03 as the no-cost alternative to Google Places, which needs a billing card
even for its free tier (the key is installed but the project has no billing attached, so
every call 403s). Overpass needs no key, no account and no card.

Why this specifically: the drafting agent had already mined NYC DOB licence PDFs and has
~1,263 licensed electricians/plumbers on file, but those are name-and-phone only - they
solve "who exists", not "how do I email them". OSM is the opposite: fewer businesses, but
a large share carry a **website** (so a published email is findable) or an outright
**email** tag. That is exactly the missing input, since guardrail #3 forbids guessing an
address.

Two complementary passes:
  1. craft=* tags   - the canonical way trades are mapped (craft=plumber, craft=hvac, ...)
  2. shop/office + name regex - catches contractors mapped as a company/shop rather than a
     craft. Constrained by an indexed tag on purpose: a bare name-regex over the NYC bbox
     is expensive enough that public Overpass instances reject it outright.

Etiquette matters here - Overpass is donated infrastructure. Identifying User-Agent, one
query at a time with a pause between, generous timeouts, and automatic fallback across
mirrors when an instance is busy.

Usage:
    python overpass_source.py --dry-run      # show what it found, write nothing
    python overpass_source.py                # append new leads to leads.json
"""

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse

import send_guard as sg

# Public Overpass instances, tried in order. The main one is frequently saturated.
ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "hvac-outreach-lead-sourcing/1.0 (small-business outreach; contact via project owner)"

# Queried one region at a time rather than as one giant bbox: Overpass cost scales with
# area, and a single tri-state box reliably trips the dispatcher timeout on public
# instances. Per-region also means one busy region cannot lose the whole run.
# NJ/CT are OFF by default (--include-nj): crossing a state line is a real market decision,
# and with autonomy_mode=AUTO anything added here starts sending on the next scheduled run.
# (Heads up: the NYC box already reaches -74.27, so it clips Jersey City/Hoboken regardless.)
REGIONS = {
    "NYC (5 boroughs)":        "40.49,-74.27,40.92,-73.68",
    "Long Island (Nassau)":    "40.58,-73.77,40.94,-73.42",
    "Long Island (Suffolk)":   "40.73,-73.42,41.16,-72.30",
    "Westchester/Rockland":    "40.90,-74.05,41.37,-73.48",
}
REGIONS_NJ = {
    "North NJ (Bergen/Hudson)": "40.66,-74.25,41.05,-73.90",
    "North NJ (Essex/Union)":   "40.58,-74.42,40.88,-74.15",
}

# OSM craft=* -> the operator's own niche names (CLAUDE.md Step 1 order). Only trades that
# genuinely map to the established ICP of owner-run local service businesses; adjacent
# crafts OSM also carries (caterer, tailor, photographer, brewery...) are deliberately out.
CRAFT_TRADES = {
    "hvac": "HVAC",
    "electrician": "electrician",
    "plumber": "plumber",
    "roofer": "roofer",
    "gardener": "tree removal",
    "carpenter": "carpenter",
    "window_construction": "window/door",
    "glaziery": "window/door",
    "stonemason": "masonry",
    "metal_construction": "metalwork",
    "cleaning": "restoration/cleaning",
}
# Trades matching the ICP most tightly - used to sort the best leads to the front.
CORE_TRADES = {"HVAC", "electrician", "plumber", "roofer", "tree removal"}

NAME_RE = ("plumb|hvac|heating|cooling|air condition|roofing|electric|gutter|restoration|"
           "mold|water damage|tree service|garage door|chimney|boiler|sewer|drain")
# Retail/wholesale, not contractors. A supply house is not a lead.
EXCLUDE_RE = re.compile(
    r"\b(supply|supplies|depot|store|hardware|showroom|wholesale|distributor|equipment|"
    r"museum|school|academy|university|library|park)\b", re.I)

# Word boundaries are load-bearing, not cosmetic: a bare "tree" matches S-TREE-t, which
# classified "Knapp Street Pizza" and "Stone Street Coffee Company" as tree-removal firms.
# Same trap with "mold" inside "molding". Only applied when the craft tag did not already
# answer the question, so a real craft=* mapping always wins.
TRADE_FROM_NAME = [
    (re.compile(r"\b(plumb\w*|sewer|drain\w*|boiler\w*)\b", re.I), "plumber"),
    (re.compile(r"\b(hvac|heating|cooling|air.?condition\w*|refrigerat\w*)\b", re.I), "HVAC"),
    (re.compile(r"\b(roof\w*|gutter\w*)\b", re.I), "roofer"),
    (re.compile(r"\b(electric\w*)\b", re.I), "electrician"),
    (re.compile(r"\b(tree|arborist\w*|arbor)\b", re.I), "tree removal"),
    (re.compile(r"\bgarage.?door\w*\b", re.I), "garage door"),
    (re.compile(r"\b(mold|remediation|restoration)\b|\bwater damage\b", re.I), "restoration"),
    (re.compile(r"\bchimney\w*\b", re.I), "chimney"),
]


def overpass(query, attempts_per_endpoint=2):
    """Run an Overpass QL query, falling back across mirrors when one is busy."""
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for ep in ENDPOINTS:
        for attempt in range(attempts_per_endpoint):
            try:
                req = urllib.request.Request(ep, data=body,
                                             headers={"User-Agent": UA,
                                                      "Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    raw = r.read().decode("utf-8", "ignore")
                if raw.lstrip().startswith("{"):
                    return json.loads(raw)
                last = f"{ep}: non-JSON (server busy)"
            except Exception as e:
                last = f"{ep}: {e}"
            time.sleep(4)  # back off before retrying; these are donated servers
    raise RuntimeError(f"all Overpass endpoints failed. last: {last}")


def q_craft(bbox):
    return f"""[out:json][timeout:180];
(
  node["craft"]({bbox});
  way["craft"]({bbox});
);
out tags;"""


def q_named(bbox):
    return f"""[out:json][timeout:180];
(
  nwr["shop"]["name"~"{NAME_RE}",i]({bbox});
  nwr["office"]["name"~"{NAME_RE}",i]({bbox});
);
out tags;"""


def domain_of(url):
    if not url:
        return ""
    if not url.startswith("http"):
        url = "http://" + url
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def trade_of(tags):
    craft = tags.get("craft")
    if craft in CRAFT_TRADES:
        return CRAFT_TRADES[craft]
    name = tags.get("name") or ""
    for rx, trade in TRADE_FROM_NAME:
        if rx.search(name):
            return trade
    return None


_STATE_NAMES = {"new york": "NY", "ny": "NY", "new jersey": "NJ", "nj": "NJ",
                "connecticut": "CT", "ct": "CT", "pennsylvania": "PA", "pa": "PA"}


def state_of(tags):
    """The element's real state from OSM, or '' if unmapped.

    Necessary because bounding boxes do not respect state lines: the Westchester/Rockland
    box clips Norwalk and Fairfield CT to the east and Hillsdale NJ to the west, and the
    first widened run happily labelled all of them ', NY'. Never infer the state from which
    region's bbox found it.
    """
    return _STATE_NAMES.get((tags.get("addr:state") or "").strip().lower(), "")


def area_of(tags, region):
    city = (tags.get("addr:city") or "").strip()
    borough = {"brooklyn": "Brooklyn, NY", "bronx": "Bronx, NY", "the bronx": "Bronx, NY",
               "queens": "Queens, NY", "staten island": "Staten Island, NY",
               "manhattan": "Manhattan, NY", "new york": "Manhattan, NY"}.get(city.lower())
    if borough:
        return borough
    state = state_of(tags)
    if city:
        return f"{city}, {state}" if state else f"{city} ({region})"
    return region


def collect(elements, seen_osm, region, allowed_states, dropped):
    out = []
    for e in elements:
        t = e.get("tags") or {}
        name = (t.get("name") or "").strip()
        if not name or EXCLUDE_RE.search(name):
            continue
        # Bounding boxes cross state lines; the operator's market does not. Drop anything
        # OSM says is outside the allowed states. Unmapped state ('') is kept, since most
        # of the NYC set has no addr:state and excluding it would throw the pool away.
        st = state_of(t)
        if st and st not in allowed_states:
            dropped[st] = dropped.get(st, 0) + 1
            continue
        trade = trade_of(t)
        if not trade:
            continue
        key = (e.get("type"), e.get("id"))
        if key in seen_osm:
            continue
        seen_osm.add(key)
        website = t.get("website") or t.get("contact:website") or ""
        email = (t.get("email") or t.get("contact:email") or "").strip().lower()
        phone = t.get("phone") or t.get("contact:phone") or ""
        # No website AND no email means nothing to reach them by - the DOB licence lists
        # already supply plenty of name+phone-only rows, so those add nothing here.
        if not website and not email:
            continue
        # NOTE: an email tag in OSM is deliberately NOT written to the `email` field.
        # Guardrail #3 requires an address published on the business's own site or a
        # listing THEY control - OSM is community-edited, so it is neither. Spot-checking
        # four OSM emails through Reoon returned one "disabled", two "catch_all" and one
        # timeout, i.e. zero confirmed: the data is often years stale. So it travels as a
        # HINT in notes, and the drafting agent must still confirm a live address on the
        # site before queueing anything. The website is the real prize here.
        hint = f" OSM also lists '{email}' - treat as a HINT ONLY, confirm it on their own site first." if email else ""
        out.append({
            "business": name, "trade": trade, "area": area_of(t, region),
            "website": website or None, "phone": phone, "owner_name": None,
            "email": None, "email_confidence": None,
            "status": "new", "problem_type": None, "problem": None,
            "source": f"openstreetmap:{e.get('type')}/{e.get('id')}",
            "osm_email_hint": email or None,
            "notes": "Sourced from OpenStreetMap via Overpass (free, no API key). "
                     "Visit the website and use a PUBLISHED address only; never guess a "
                     "generic prefix." + hint,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="cap how many new leads to keep")
    ap.add_argument("--include-nj", action="store_true",
                    help="also source Northern NJ (off by default: different state/market)")
    args = ap.parse_args()

    send_log = sg.load_json("send_log.json", {"sent": []}).get("sent", [])
    suppressed = {e.lower() for e in sg.load_json("suppressed.json", {"emails": []}).get("emails", [])}
    leads_doc = sg.load_json("leads.json", {"leads": []})
    leads = leads_doc.get("leads", [])

    sent_emails = {(e.get("email") or "").lower() for e in send_log if e.get("email")}
    sent_domains = {(e.get("email") or "").split("@")[-1].lower() for e in send_log if e.get("email")}
    known_names = {(l.get("business") or "").strip().lower() for l in leads}
    known_names |= {(e.get("business") or "").strip().lower() for e in send_log}
    known_sources = {l.get("source") for l in leads if l.get("source")}

    regions = dict(REGIONS)
    allowed_states = {"NY"}
    if args.include_nj:
        regions.update(REGIONS_NJ)
        allowed_states.add("NJ")

    seen_osm, found, dropped = set(), [], {}
    for region, bbox in regions.items():
        print(f"\n--- {region} ---")
        for label, q in (("craft", q_craft(bbox)), ("shop/office+name", q_named(bbox))):
            try:
                data = overpass(q)
            except RuntimeError as e:
                print(f"  {label:<18} SKIPPED: {e}")
                continue
            els = data.get("elements", [])
            got = collect(els, seen_osm, region, allowed_states, dropped)
            print(f"  {label:<18} {len(els):>5} elements -> {len(got)} reachable")
            found += got
            time.sleep(3)  # donated infrastructure; pace the requests
    if dropped:
        print(f"\ndropped as out-of-market (bbox crossed a state line): {dropped}")

    fresh, dup = [], 0
    for lead in found:
        name = lead["business"].strip().lower()
        dom = domain_of(lead.get("website"))
        hint = (lead.get("osm_email_hint") or "").lower()
        if (lead.get("source") in known_sources or name in known_names
                or (hint and (hint in sent_emails or hint in suppressed))
                or (dom and dom in sent_domains)):
            dup += 1
            continue
        known_names.add(name)
        fresh.append(lead)

    # Core ICP trades first; a website is what actually makes a lead workable.
    fresh.sort(key=lambda l: (l["trade"] not in CORE_TRADES, l.get("website") is None))
    if args.limit:
        fresh = fresh[:args.limit]

    with_site = sum(1 for l in fresh if l.get("website"))
    with_hint = sum(1 for l in fresh if l.get("osm_email_hint"))
    print(f"\n{len(found)} reachable found | {dup} already known | {len(fresh)} NEW")
    print(f"  with a website (the workable ones): {with_site}")
    print(f"  carrying an unconfirmed OSM email hint: {with_hint}")
    by_trade, by_area = {}, {}
    for l in fresh:
        by_trade[l["trade"]] = by_trade.get(l["trade"], 0) + 1
        by_area[l["area"]] = by_area.get(l["area"], 0) + 1
    print(f"  by trade: {dict(sorted(by_trade.items(), key=lambda kv: -kv[1]))}")
    print("  by area :")
    for a, n in sorted(by_area.items(), key=lambda kv: -kv[1])[:14]:
        print(f"     {a:<28}{n}")
    print("\n  first 12:")
    for l in fresh[:12]:
        print(f"    {l['business'][:38]:<40} {l['trade']:<14} "
              f"{(l.get('website') or '(no site)')[:46]}")

    if args.dry_run:
        print("\nDRY RUN: leads.json unchanged.")
        return
    leads.extend(fresh)
    sg.save_json("leads.json", {"leads": leads})
    print(f"\nleads.json: +{len(fresh)} -> {len(leads)} total (status 'new')")


if __name__ == "__main__":
    main()
