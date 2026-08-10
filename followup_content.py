"""followup_content.py - angle mapping + template rendering for the 3-step follow-up
sequence (added 2026-07-18). Pure functions, no I/O, no sending - followup_guard.py calls
these to build the email text, then sends it. Kept separate from CLAUDE.md/the Claude CLI
on purpose: this content is fixed/dictated, not something that needs an LLM drafting pass
each time (deterministic string-fill is more reliable and has no timeout risk).

Voice rules mirrored from CLAUDE.md: plain language a contractor would use, no jargon
("speed-to-lead" etc never appears in the actual copy), no em dashes.
"""

import re

# Same 5-angle taxonomy as CLAUDE.md's FIVE PROBLEM ANGLES table. `problem` and `benefit`
# are deliberately PERSON-NEUTRAL noun phrases (no "you"/"your" baked in) because the same
# string gets dropped into three different grammatical frames: 2nd person in stage 1
# ("Are you still having trouble with {problem}?" / "helping you get {benefit}"), 3rd
# person in stage 2's Shazib case study ("Shazib was struggling with {problem}... ended up
# getting {benefit}"), and stage 3 ("a few emails about getting {benefit}"). Baking "you"
# into either string reads fine in stage 1 but breaks (wrong person) in stage 2 - keep them
# neutral and let each template supply its own pronouns. `bullets` stay in generic
# feature-description voice (a "you" there is normal, common phrasing in real case-study
# bullet lists and doesn't clash with anything).
ANGLES = {
    "missed_calls": {
        "problem": "calls slipping through after hours or during a busy job",
        "benefit": "every call answered, even after hours, instead of going to voicemail",
        "bullets": [
            "Someone (or something) answers every call, day or night",
            "An instant text back the second you miss one, so they don't call the next guy",
            "A simple log of every call you would've missed",
        ],
    },
    "slow_follow_up": {
        "problem": "quotes and callbacks that quietly slip through the cracks",
        "benefit": "every quote followed up automatically until it's booked",
        "bullets": [
            "Every new enquiry gets an instant reply, even if you're up a ladder",
            "Quotes get followed up automatically instead of going cold",
            "A simple list so nothing falls through the cracks",
        ],
    },
    "scheduling_gaps": {
        "problem": "slow stretches and one-time jobs that never turn into repeat business",
        "benefit": "old customers and one-time jobs turned into repeat business",
        "bullets": [
            "Past customers get reminded automatically when it's time again",
            "A simple maintenance plan that keeps the slow weeks filled",
            "One-time jobs turned into repeat customers",
        ],
    },
    "weak_reviews": {
        "problem": "not enough reviews showing up in a search",
        "benefit": "more happy customers to actually leave a review",
        "bullets": [
            "Happy customers get asked for a review at the right moment",
            "Your Google Business page kept fresh and accurate",
            "More reviews means more locals finding you first",
        ],
    },
    "owner_bottleneck": {
        "problem": "so much of the day-to-day still riding on one person",
        "benefit": "some of that off one person's plate",
        "bullets": [
            "Invoicing and payment reminders that go out on their own",
            "Simple systems so jobs don't depend on you remembering everything",
            "A bit more time back for the actual work",
        ],
    },
}
DEFAULT_ANGLE = "owner_bottleneck"  # safest generic fallback if nothing else matches

# Subject-tail keyword -> angle, in priority order. Mirrors the exact examples CLAUDE.md's
# own EMAIL TEMPLATE section already uses ("about your open jobs" -> owner_bottleneck etc),
# extended to cover every tail actually seen in send_log.json.
_KEYWORD_ANGLE = [
    (("google review", "online review"), "weak_reviews"),
    (("missed call", "after-hours call", "after hours call"), "missed_calls"),
    (("quote follow", "customer follow", "job follow", "callback", "enquir", "enquiries"), "slow_follow_up"),
    (("quiet season", "one-time job", "one time job"), "scheduling_gaps"),
    (("running it", "handling it", "handling every job", "yourself"), "owner_bottleneck"),
    (("open job",), "owner_bottleneck"),
]


def infer_angle(subject):
    """Best-effort angle from a historical subject line (pre-2026-07-18 contacts have no
    stored problem_type). New contacts should just carry problem_type from the outbox item
    instead of going through this at all - use that when present."""
    s = (subject or "").lower()
    for keywords, angle in _KEYWORD_ANGLE:
        if any(k in s for k in keywords):
            return angle
    return DEFAULT_ANGLE


def angle_of(problem_type, subject):
    if problem_type in ANGLES:
        return problem_type
    return infer_angle(subject)


def _name_or_business(owner_name, business):
    if owner_name:
        return owner_name.split()[0]
    return business or "there"


def _footer(optout_sentence, mailing_address):
    return f"\n\n{optout_sentence}\n{mailing_address}"


def render_stage1(contact, optout_sentence, mailing_address):
    a = ANGLES[angle_of(contact.get("problem_type"), contact.get("original_subject"))]
    name = _name_or_business(contact.get("owner_name"), contact.get("business"))
    body = (
        f"Hey {name},\n\n"
        f"Are you still having trouble with {a['problem']}?\n\n"
        f"I sent you a note about helping you get {a['benefit']}.\n\n"
        f'A client of mine, Shazib, had someone on his team put it best: "This video got '
        f'Shazib 140 new leads."\n\n'
        f"Could I show you a quick video of how it works in your market?\n\n"
        f"Best,\nUmar"
    )
    return body + _footer(optout_sentence, mailing_address)


def render_stage2(contact, optout_sentence, mailing_address):
    a = ANGLES[angle_of(contact.get("problem_type"), contact.get("original_subject"))]
    name = _name_or_business(contact.get("owner_name"), contact.get("business"))
    bullets = "\n".join(f"- {b}" for b in a["bullets"])
    body = (
        f"Hey {name},\n\n"
        f"I don't think you want to miss this.\n\n"
        f"I wanted to share a quick case study with you.\n\n"
        f"Shazib was struggling with {a['problem']}. Then he ended up getting {a['benefit']}. "
        f"Here's what we set up:\n\n"
        f"{bullets}\n\n"
        f"And the results: he ended up getting over 140 leads in two months, and made "
        f"over $10,000.\n\n"
        f'If you\'re interested, reply "yes" and I\'ll shoot over a video showing how that '
        f'works. Or reply "unsubscribe" if you don\'t want to hear from me again.\n\n'
        f"Cheers,\nUmar"
    )
    return body + _footer(optout_sentence, mailing_address)


def render_stage3(contact, optout_sentence, mailing_address):
    a = ANGLES[angle_of(contact.get("problem_type"), contact.get("original_subject"))]
    name = _name_or_business(contact.get("owner_name"), contact.get("business"))
    body = (
        f"Hey {name},\n\n"
        f"We're breaking up.\n\n"
        f"I sent you a few emails about getting {a['benefit']}.\n\n"
        f"I haven't heard back, which usually means:\n\n"
        f"You're not interested (totally fine).\n"
        f"You're too busy right now.\n"
        f"My emails aren't reaching you.\n\n"
        f"Just reply with:\n\n"
        f'"Not interested" and I\'ll stop reaching out.\n'
        f'"Too busy" and I\'ll reconnect later.\n'
        f'"Call me" if you want to chat.\n\n'
        f"Either way, all good.\n\n"
        f"Cheers,\nUmar"
    )
    return body + _footer(optout_sentence, mailing_address)


# ---------------------------------------------------------------------------------------
# NEW COPY (operator switched the first-touch template 2026-08-04). The cold email now
# pitches "cost inefficiencies AI could solve" instead of one of the five problem angles,
# so the follow-ups had to move with it - otherwise a prospect gets an AI email followed
# two days later by one about missed calls, which reads as a mail-merge accident.
#
# These are angle-INDEPENDENT on purpose: the new pitch is a single offer, not five
# services mapped to five problems, so there is nothing to vary per contact.
# ---------------------------------------------------------------------------------------

# The AI-framed first touch has run since 2026-08-04. It was briefly reverted to the
# problem-angle template on 2026-08-10 and restored the same day (with a problem/agitate
# beat added); ZERO emails went out during that revert, so there is no orphaned cohort and
# the cutover stays a single clean date.
CUTOVER_ISO = "2026-08-04"


def uses_new_copy(contact):
    """True for contacts whose FIRST email was AI-framed, i.e. everyone from the cutover on.

    Anyone part-way through a sequence keeps the copy matching what they were first sent,
    for life. Switching mid-thread would be worse than either version on its own: email 1
    promised one thing, and email 2 would answer a question they were never asked. The 260
    contacts first emailed before the cutover keep the legacy problem-angle copy forever.
    """
    return (contact.get("original_date_iso") or "") >= CUTOVER_ISO


def render_new_stage1(contact, optout_sentence, mailing_address):
    name = _name_or_business(contact.get("owner_name"), contact.get("business"))
    company = contact.get("business") or "your business"
    body = (
        f"Hey {name},\n\n"
        f"Is adopting AI still something you're weighing up at {company}?\n\n"
        f"I sent you a note about finding the cost inefficiencies AI could take off your "
        f"plate.\n\n"
        f"A client of mine, Shazib, had someone on his team put it best: \"This video got "
        f"Shazib 140 new leads.\"\n\n"
        f"Could I show you a quick video of how it works in your market?\n\n"
        f"Best,\nUmar"
    )
    return body + _footer(optout_sentence, mailing_address)


def render_new_stage2(contact, optout_sentence, mailing_address):
    name = _name_or_business(contact.get("owner_name"), contact.get("business"))
    body = (
        f"Hey {name},\n\n"
        f"I don't think you want to miss this.\n\n"
        f"I wanted to share a quick case study with you.\n\n"
        f"Shazib was losing his evenings to admin, quoting, chasing invoices, returning "
        f"calls he'd missed on site. Here's what we set up:\n\n"
        f"- Every call and message answered, day or night, without him picking up\n"
        f"- Quotes and invoices that go out and chase themselves\n"
        f"- The repetitive admin handled in the background, not after dinner\n\n"
        f"And the results: he got about 12 hours a week back, and over 140 new leads in "
        f"two months.\n\n"
        f"If you're interested, reply \"yes\" and I'll shoot over a video showing how that "
        f"works. Or reply \"unsubscribe\" if you don't want to hear from me again.\n\n"
        f"Cheers,\nUmar"
    )
    return body + _footer(optout_sentence, mailing_address)


def render_new_stage3(contact, optout_sentence, mailing_address):
    name = _name_or_business(contact.get("owner_name"), contact.get("business"))
    body = (
        f"Hey {name},\n\n"
        f"We're breaking up.\n\n"
        f"I sent you a few emails about where AI could cut costs in the business.\n\n"
        f"I haven't heard back, which usually means:\n\n"
        f"You're not interested (totally fine).\n"
        f"You're too busy right now.\n"
        f"My emails aren't reaching you.\n\n"
        f"Just reply with:\n\n"
        f"\"Not interested\" and I'll stop reaching out.\n"
        f"\"Too busy\" and I'll reconnect later.\n"
        f"\"Call me\" if you want to chat.\n\n"
        f"Either way, all good.\n\n"
        f"Cheers,\nUmar"
    )
    return body + _footer(optout_sentence, mailing_address)


RENDERERS = {1: render_stage1, 2: render_stage2, 3: render_stage3}
RENDERERS_NEW = {1: render_new_stage1, 2: render_new_stage2, 3: render_new_stage3}


def render(stage, contact, optout_sentence, mailing_address):
    """The single entry point: picks the copy set matching this contact's first email."""
    table = RENDERERS_NEW if uses_new_copy(contact) else RENDERERS
    return table[stage](contact, optout_sentence, mailing_address)


PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]|<[^>]+>|\{\{[^}]*\}\}")
