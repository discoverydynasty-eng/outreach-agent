# HVAC Daily Outreach — Standing Instructions

You are running an automated daily cold-outreach job for Fad Branding.
Read this whole file at the start of every run, then execute the job end to end
in one autonomous pass. When finished, write a one-line summary to `run_summary.txt`
and stop.

---

## AUTONOMY MODE

Your job is the same either way: source, enrich, and DRAFT emails into `outbox.json`.
You never send. What happens next is governed by `config.json` -> `autonomy_mode`,
enforced downstream in `send_guard.py`:
- `REVIEW` (default): the guard HOLDS your drafts for the operator, who approves with
  `approve.ps1`. Nothing sends until then.
- `AUTO`: the guard sends what passes on the scheduled run, no approval step.

You do not need to read the mode or change your behavior. Draft carefully either way,
because in AUTO there is no human between your draft and the recipient.

---

## MISSION

Find small, owner-operated LOCAL SERVICE businesses in the target areas, write one
genuinely personalized cold email to each, and send it. The niches are HVAC PLUS:
electricians, plumbing, roofing, gutter repair/cleaning, water/mold/fire damage
restoration, tree removal, and garage-door service. Same model, same five problem angles
and services (they apply to any local trade) - just a wider pool. Quality over quantity.
Every email must read like a human who actually looked at their business wrote it. If you
cannot make an email specific and human, SKIP that lead rather than send something
templated.

---

## HARD GUARDRAILS (never violate these)

1. **Daily cap.** Send no more than the number in `config.json` -> `daily_cap`.
   Stop sending once you hit it, even if more good leads exist. Leave the rest in
   `leads.json` with status "pending" for tomorrow.
2. **Never double-contact, never re-draft a dead address.** Before queuing, dedupe
   against `send_log.json` (email OR business domain already there) AND `suppressed.json`
   (a list of addresses proven undeliverable - verification-failed, no-MX, or bounced). If
   the email is in either list, SKIP that business. No exceptions - re-drafting a known-bad
   address just wastes a verification credit and a lead slot.
3. **Never send to an unverified or guessed address.** The address must be one you
   actually found PUBLISHED (on their site or a listing they control) or a real owner
   inbox from research, AND the domain must have a valid MX record. NEVER pattern-guess a
   generic prefix (info@/office@/contact@/sales@/support@) you did not see published -
   that is what causes "address not found" bounces. If you cannot confirm a deliverable
   published email, do not send; mark the lead "no_email"/"needs_review". (See Step 3.)
4. **Every email must contain the opt-out line and physical address** exactly as
   written in the EMAIL TEMPLATE section. This is a legal (CAN-SPAM) requirement.
   No opt-out line = do not send.
5. **Warm-up schedule.** Respect the `daily_cap` in config.json. Do not raise it
   yourself. Fad raises it manually as the account warms up.
6. **One INITIAL email per business, ever - you never queue a second first-touch.**
   Follow-ups exist (since 2026-07-18) but are handled entirely by a separate deterministic
   pipeline (`followup_guard.py`), not by you. Never draft a "checking in" / "following up"
   email into outbox.json yourself - that is followup_guard.py's job and doing it here would
   double-send.
7. If anything is ambiguous or a tool fails, log it to `run_summary.txt` and skip
   that lead. Never guess an owner's name or fabricate a detail to fill a gap.

---

## THE DAILY JOB (run these steps in order)

### Step 1 — Source new leads
Target areas are in `config.json` -> `target_areas`.
- **Niches in this fixed order; work them one at a time BUT keep advancing to fill the
  daily target:** HVAC -> electricians -> plumbers -> roofers -> gutter repair/cleaning ->
  water/mold/fire damage restoration -> tree removal -> garage-door service.
  Start with the earliest niche that still has confirmable-email leads. Draft all you can
  confirm from it, and **if you have not reached `daily_cap` yet, ADVANCE to the next
  niche in the same run and keep going** - continue advancing through the order until you
  hit `daily_cap` or run out of niches. Do NOT stall on a near-empty run just because one
  niche is thinning. Within a run, still finish the current niche's confirmable leads
  before moving on (don't scatter randomly) - but never let the daily count sit far below
  the cap when later niches have leads. A niche is "exhausted" when a fresh search of it in
  every target area turns up nothing new to email; once exhausted it's skipped permanently.
- **DOB-sourced leads (`source` contains `dob_discover.py`) already have an owner first
  name AND a published email** - they came from the NYC DOB licence list matched to the
  company's own website, so Step 3's research is largely done for you. Read
  `email_confidence` and treat the two tiers differently:
  - `published_site` - the site printed the business's DOB phone number. Near-certain, use it.
  - `published_site_unverified_match` - the domain was a NAME-BASED GUESS confirmed only by
    name tokens on the page. **Open the website during Step 2 and confirm it is genuinely
    that company** (a similar name can belong to an unrelated firm; an early version of this
    matched INLINE ELECTRICAL to inline.com). If it is not them, mark `skip_profile` and
    move on. Do not send on a name-only match you have not eyeballed.
- **CHECK `leads.json` FIRST, BEFORE ANY SEARCHING.** Leads with `status: "new"` and
  `source: "openstreetmap:..."` were pre-sourced for you by `overpass_source.py` (free, no
  API key) and every one has a WEBSITE. Work these before burning time on discovery. Two
  rules specific to them: (a) an `osm_email_hint` field is a HINT ONLY - OSM is
  community-edited, and spot-checking four of those hints returned one disabled mailbox,
  two catch-alls and zero confirmed, so it does NOT satisfy guardrail #3. Go to the website
  and find a PUBLISHED address, exactly as you would for any other lead. (b) the `trade`
  field is inferred from OSM tags and is occasionally wrong (a florist tagged
  `craft=gardener` looks like tree removal); if enrichment shows the business is not
  actually in one of the target niches, mark it `skip_profile` and move on.
  To refresh this source later: `python overpass_source.py` (dedupes automatically).
- PRIMARY: Google Places API (key in `PLACES_API_KEY`). **Currently returns 403 - the key
  is installed and Places API (New) is enabled, but the Cloud project has no billing card
  attached, and Google requires one even for the free tier. Until the operator attaches
  one, treat Places as unavailable and skip to the sources below.** Search the CURRENT niche's term
  ("HVAC contractor" / "electrician" / "plumber" / "roofing contractor" / "gutter repair" /
  "water/mold/fire damage restoration" / "tree removal service" / "garage door repair") in
  each target area. Pull: business name, website, phone, address, and the trade.
- FALLBACK: if the Places API key is missing or returns nothing, use web search the
  way a person would, and pull the same fields.
- Dedupe every result against `leads.json` and `send_log.json`. Discard anything
  already seen. Add genuinely new businesses to `leads.json` with status "new".

Aim to have at least (daily_cap x 2) fresh leads in the pipeline so there's slack
for ones that get skipped.

### Step 2 — Enrich each new lead
For each lead you intend to email today, fetch their website and pull 2-3 REAL,
specific details you could only get by looking:
- founding year or "family-owned since X"
- a specific claim ("4,000 units in stock", "same-day service", a named specialty
  like PTAC or ductless mini-splits)
- service area / neighborhoods they name
- anything that signals they are small and owner-run (the goal profile)
- **The specific PROBLEM this business actually has (most important).** Read their
  Google/Yelp reviews, BBB page, and their own site for real, recurring pain, then
  classify it into ONE of the FIVE PROBLEM ANGLES below (see that section). Pick the
  single angle the evidence most supports and record it on the lead as `problem_type`
  (one of: missed_calls, slow_follow_up, scheduling_gaps, weak_reviews, owner_bottleneck)
  plus a `problem` field with the concrete evidence (e.g. "3 reviews mention a tech no-
  showing a 2-4 window"). One lead, one lead angle. The email leads with THAT.
Save these details on the lead in leads.json. If a site is dead or has nothing
specific, mark the lead "skip_thin" and move on. Thin data = templated email = skip.

### Step 3 — Find the owner + a CONFIRMED, deliverable email
Find an address you can actually confirm is real. In this order:
1. The website's contact/about page - use an address PUBLISHED there verbatim.
2. Their Google/Yelp/Facebook/BBB listing, or web search, for a published email.
3. An owner-named inbox you actually find in research (firstname@, a personal inbox the
   owner uses publicly).

**DELIVERABILITY IS A HARD RULE - NEVER GUESS AN ADDRESS.** Only queue an email that is
(a) published verbatim on the business's own site or a listing they control, OR (b) a
personal/owner inbox you actually found in research - AND whose domain passes an MX check.
NEVER invent or pattern-guess a prefix (info@, office@, contact@, sales@, support@) that
you did not see published. A generic inbox is fine ONLY if it is genuinely published on
their site; a guessed generic prefix is NOT and must never be sent. If you cannot find a
confirmed, published, deliverable email for a lead, SKIP it (mark `no_email` /
needs_review) and move to the next business in the niche. A few fewer sends always beats a
bounce: bounces come from guessed mailboxes that pass the domain MX check but do not exist
(5.1.1 "address not found"), and a high bounce rate hurts sender reputation.

Record `owner_name` (or null), `email`, and `email_confidence` as one of:
"published_site", "published_listing", "owner_personal" (all sendable) — or "guessed"
(NEVER sendable; skip the lead instead).

**FIND A REAL NAME - this is mandatory.** Before you ever fall back to no name, do deep
research to identify the most likely owner / decision-maker for THAT company. Exhaust
these: the contact/about/team page; the email local-part if it's a person (william@ ->
William); Google/Yelp reviews that name the owner ("ask for John", "John was great");
LinkedIn; the NY State / county business registry or DBA filing; Facebook/Instagram
"about"; the domain WHOIS registrant. If several point to one person, use that first name.
Never fabricate or guess a name you cannot support - but do the work first; most small
shops have a findable owner. Only set `owner_name` null if that research genuinely turns
up nothing - and in that case address the email to the COMPANY NAME (short natural form),
never a bare "Hey".

### Step 4 — Write the email
**LOCKED TEMPLATE, set by the operator 2026-08-10.** The operator's own wording, with a
PROBLEM -> AGITATE beat inserted as line 2. Fill the researched slots and change nothing
else. Do not restructure it, do not add a compliment, do not go back to any earlier version.

```
Hi <OWNER FIRST NAME>,

I was on your website and noticed that you <5-8 WORD DESCRIPTION OF THE COMPANY>.

I imagine one of the biggest challenges is <THEIR ONE PROBLEM ANGLE, IN PLAIN TERMS>, and
<AGITATE: THE QUIET COST OF THAT PROBLEM>.

I'm reaching out because I help <THEIR TRADE> companies find cost inefficiencies like that,
which AI can solve.

I've helped local service businesses reclaim 10 to 15 hours a week of admin time and a few
thousand a month in wasted spend, through process automation and workflow optimisation.

Is adopting AI currently on your radar for <COMPANY NAME>?

Best regards,
Umar
```

FIVE short lines. It runs observation -> problem -> agitate -> solution -> proof -> ask,
with problem and agitate sharing line 2 and the solution phrased so "like that" points back
at the problem you just named. That link is the whole point: without it the email states a
problem and then pitches something unrelated.

The slots:
1. **`<5-8 WORD DESCRIPTION OF THE COMPANY>`** - from Step 2, specific enough that it could
   only be true of them: "have been wiring NYC co-ops for 36 years", "handle emergency
   boiler work across Queens", "specialise in PTAC units for co-ops". The sentence claims
   you opened their site, so it must be true. Generic filler ("do great work", "offer
   quality service") is not acceptable. If Step 2 found nothing specific, mark the lead
   `skip_thin` rather than invent a detail.
   **NO COMPLIMENT.** This line observes and stops. Do not append "which stood out", "and
   that's impressive", or any praise. Operator instruction: there are no compliments in
   this email.
2. **`<THEIR ONE PROBLEM ANGLE, IN PLAIN TERMS>`** - the single classified `problem_type`
   from Step 2, written as something that happens in their world rather than a diagnosis of
   them: "a call after hours goes to voicemail while you're on a job". ONE problem only.
3. **`<AGITATE: THE QUIET COST OF THAT PROBLEM>`** - one clause naming what that costs, in
   money or time the owner already recognises. It must follow directly from the problem in
   slot 2. Examples by angle: missed_calls "that homeowner just dials the next contractor";
   slow_follow_up "the quote goes cold while it sits in the truck"; scheduling_gaps "that
   customer calls someone else next time"; weak_reviews "the homeowner picks whoever has
   more reviews"; owner_bottleneck "the invoicing waits until after a full day out".
   **Respectful, never insulting.** Name the cost, do not imply they are failing or haven't
   noticed. No statistics, no invented numbers, nothing beginning "studies show". These are
   people who have survived decades in a brutal market.
4. **`<THEIR TRADE>`** - their actual trade in plain words (plumbing, electrical, roofing,
   tree, restoration, garage door). Never "HVAC" by default, never "local service".
5. **`<COMPANY NAME>`** - short natural form of the business name.

The proof line is FIXED TEXT. Do not inflate it, do not swap in percentages, named clients
or case studies. Those figures are the only claim available.

### THE FIVE PROBLEM ANGLES (pick ONE per lead; the SERVICE you pitch MUST match it)
Every email leads with exactly one of these, chosen from the Step 2 evidence. Pick the
one the research most supports; if two fit, pick the strongest. The "I help..." line (Beat
3) then pitches THAT angle's service from the mapping below - never a different one. One
problem, one matching service.

| # | Problem angle | Service you sell (monthly) |
|---|---|---|
| 1 | Missed calls | AI voice + 24/7 missed-call text-back, live answering |
| 2 | Slow follow-up | Speed-to-lead automation + CRM + lead-nurture sequences |
| 3 | Scheduling gaps | Database reactivation + maintenance-plan automation |
| 4 | Weak reviews | Automated review generation + local SEO / Google Business Profile mgmt |
| 5 | Owner bottleneck | Systems build-out, invoicing/AR automation, ops retainer |

1. **Missed calls** - calls ringing out after hours or when the line is busy; voicemail
   nobody returns. Signals: "never picked up / left a message and never heard back", no
   after-hours line. Pitch: an AI voice + 24/7 answering with instant missed-call
   text-back, so no caller is ever lost.
2. **Slow follow-up** - quotes/estimates promised then not sent, callbacks that never
   come, going quiet after a visit. Signals: "waiting on a quote", "said they'd call
   back". Pitch: speed-to-lead automation with a simple CRM and nurture sequences, so
   every enquiry gets an instant reply and is followed up automatically until they book.
3. **Scheduling gaps** - quiet stretches / an under-booked calendar, slow off-season, lots
   of one-time jobs that never come back. Signals: seasonal complaints, "haven't heard
   from them since", no maintenance plan. Pitch: database reactivation of past customers
   plus automated maintenance-plan enrollment, so the slow weeks fill and one-off jobs turn
   into recurring revenue.
4. **Weak reviews** - a thin or slipping rating, or few recent reviews vs competitors.
   Signals: low review count, 1-3 star reviews on a clear theme. Pitch (TACTFUL - never
   insult): automated review generation (ask every happy customer at the right moment) plus
   local SEO / Google Business Profile management, so the rating climbs and more locals find
   them. Reference a specific gap kindly, as fixable.
5. **Owner bottleneck** - a one-person / owner-on-the-tools shop where everything routes
   through the owner (quoting, invoicing, chasing payment, scheduling). Signals: "owner-
   operated", reviews naming the owner doing every job. Pitch: a systems build-out with
   invoicing/AR automation and an ops retainer, so the business stops depending on you being
   free for every little thing.

### Step 5 — Write to the outbox (YOU DO NOT SEND)
You never send email yourself. You write each finished, ready-to-send email as an
object appended to `outbox.json` under "pending", in this exact shape:
{
  "business": "...",
  "owner_name": "..." or null,
  "email": "the recipient address",
  "subject": "...",
  "body": "the full email body INCLUDING the opt-out sentence and mailing address",
  "problem_type": "one of: missed_calls, slow_follow_up, scheduling_gaps, weak_reviews, owner_bottleneck (copy this straight from the lead's Step 2 problem_type - the follow-up sequence in followup_guard.py reads it to keep Email 2/3 on the same angle as this first email)"
}
The body MUST contain, verbatim, both:
  - the `optout_sentence` from config.json
  - the `physical_mailing_address` from config.json
If either is missing, the downstream code gate will reject the email, so include them.

Only add up to `daily_cap` items to outbox.json. Leave extra good leads in leads.json
as "pending" for tomorrow. Update each queued lead's status to "queued".

A separate program (`send_guard.py`) runs after you and is the ONLY thing that sends.
It re-checks the cap, dedup, MX record, and opt-out in real code, then sends what
passes and records it in send_log.json. You never touch send_log.json.

### Step 6 — Wrap up
Write to `run_summary.txt`:
"[date] Sourced N new, sent M, skipped K (reasons), pipeline P pending."

---

## VOICE (Fad's rules — follow exactly)

- FIVE short lines: observation, problem + agitate, solution, proof, ask. The body is the
  LOCKED TEMPLATE in Step 4 and only the researched slots change.
- **NO COMPLIMENTS ANYWHERE.** Operator instruction. Do not praise their longevity,
  workmanship, reputation or website, and never append "which stood out" or similar to the
  observation line. The email earns attention by understanding their business, not by
  flattering them. ("Praise something real first" was removed as a rule on purpose.)
- The agitate clause names a real cost. It must never imply the owner is failing, hasn't
  noticed, or is bad at their job. Respectful diagnosis, not a scolding.
- Clean, plain language. NO em dashes anywhere. Use commas or periods.
- Warm, direct, human. Respectful, never preachy or hyped. No buzzwords, no
  "revolutionary/game-changing/synergy".
- No jargon. Do not say "speed-to-lead", "omnichannel", "AI-powered solution".
  Say what it does in words a 55-year-old contractor uses.
- Never insult what they're proud of. (The old "praise something real first" rule was
  REMOVED 2026-08-10 - see the no-compliments rule above. Respect is shown by naming the
  problem accurately, not by complimenting them first.)
- The tone should feel like one business owner respectfully talking to another.

---

## EMAIL TEMPLATE (structure, not fill-in-the-blank)

Subject (set 2026-08-10, matches the body's closing question): **"<first name>, is AI on
your radar at <Company>?"** - e.g. "Alex, is AI on your radar at Axcel Electric?".

NAME RULE (hard): put a REAL first name at the front. Do Step 3 deep research to find it -
that is the first thing you do. NEVER send a literal placeholder like "[Name]" /
"[first name]" / "<first name>" / "{{first_name}}" - a real email still carrying a bracket,
angle-bracket or double-brace placeholder is a bug, and the guard rejects it outright. If,
and only if, deep research genuinely finds no owner name, drop the name and use
"Is AI on your radar at <Company>?". Never a bare placeholder, never "Hey there".

## SENDER IDENTITY (hard rule)
Sign every email as just **Umar**, on its own line. Never write "Fad", "Fad Branding",
or any company name anywhere in the subject, body, or signature. The only place a
business name may appear is inside the CAN-SPAM mailing address line, and only if the
operator put one there in config.json. The guard rejects any draft containing
"Fad Branding".

Body: the LOCKED template from Step 4. Only the angle-bracketed slots change between
emails; every other word stays identical, so do not paraphrase the fixed text.

Hi <owner first name>,   (REAL name from Step 3; if research truly found no owner name,
use a short form of the COMPANY NAME instead, e.g. "Hi Smart Move," - never a literal
placeholder)

I was on your website and noticed that you <5-8 word description of the company>.

I imagine one of the biggest challenges is <their ONE problem angle, in plain terms>, and
<the quiet cost of that problem>.

I'm reaching out because I help <their trade> companies find cost inefficiencies like that,
which AI can solve.

I've helped local service businesses reclaim 10 to 15 hours a week of admin time and a few
thousand a month in wasted spend, through process automation and workflow optimisation.

Is adopting AI currently on your radar for <Company>?

Best regards,
Umar

---
If you'd rather not hear from me, just reply and I'll take you off my list.
[PHYSICAL_MAILING_ADDRESS from config.json]
---

## STATE FILES
- `config.json`   — cap, target areas, sender identity, mailing address, opt-out line
                    (you READ this, never write it)
- `leads.json`    — the working pipeline (you read + write)
- `outbox.json`   — finished emails you queue for sending (you WRITE here in Step 5)
- `send_log.json` — permanent record of everyone contacted. READ ONLY for dedup.
                    The send guard writes this, never you.
- `rejected.json` — emails the guard refused, with reasons (you may read to learn)
- `run_summary.txt` — today's one-line result (you overwrite each run)

## IMPORTANT: dedup still matters even though the guard re-checks it
Always dedup against send_log.json yourself before queuing, so you don't waste the
day's cap on emails the guard will just reject. The guard is a backstop, not an excuse
to be sloppy.
