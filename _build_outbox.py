"""Build outbox.json from a hand-written batch of drafts.

The entries below are SYNTHETIC EXAMPLES. They exist to document the email shape,
not to be sent. Real lead data never lives in a tracked file: the live pipeline
writes it to outbox.json, which is gitignored along with every other *.json state
file.

The template is a problem-agitate-solve structure in three beats:

    noticed  something specific and true about this business, observed first-hand
    imagine  the problem that observation implies, named without exaggerating it
    helps    what we do about it, in the business owner's own vocabulary

The "noticed" line is the one that cannot be faked. If it could be written about
any business in the niche, the lead gets skipped rather than sent something
templated.
"""
import json

OPTOUT = "If you'd rather not hear from me, just reply and I'll take you off my list."
ADDR = "<POSTAL_ADDRESS>"  # CAN-SPAM requires a real physical address on every send.


def body(greet, noticed, imagine, helps):
    return (
        "Hi " + greet + ",\n\n"
        + noticed + "\n\n"
        + imagine + "\n\n"
        + helps + "\n\n"
        "Would it be worth a quick 10-minute chat sometime next week to see if there's anything useful I can share?\n\n"
        "Best regards,\nUmar\n\n---\n" + OPTOUT + "\n" + ADDR + "\n"
    )


D = []


def add(business, owner, greet, email, subject, noticed, imagine, helps):
    D.append({
        "business": business,
        "owner_name": owner,
        "email": email,
        "subject": subject,
        "body": body(greet, noticed, imagine, helps),
    })


# ---------------- Angle 1: the reputation gap ----------------
add("Example Heating & Cooling LLC", "Alex", "Alex", "owner@example.com",
    "Hey Alex, about your online reviews",
    "I noticed you handle the ductless installs yourself rather than subbing them out, and that every review you have collected is a five star, which stood out.",
    "I imagine one of the biggest challenges is that after years of good work, only a handful of those happy customers ever leave anything online, so people searching your area find shops with hundreds of reviews instead of yours.",
    "I help owner-run heating and cooling shops ask every happy customer for a review at the right moment and keep their Google listing tidy, so the rating you have already earned is the one local people actually see.")

# ---------------- Angle 2: the owner is the bottleneck ----------------
add("Example Electrical Corp", "Sam", "Sam", "contact@example.com",
    "Hey Sam, about running it all yourself",
    "I noticed your customers talk about you by name rather than the company, the sort of thing you only get when the owner is on the job himself, which stood out.",
    "I imagine one of the biggest challenges is that you list yourself as available around the clock, so every quote, every booking and every bit of paperwork lands on you whatever the hour.",
    "I help owner-run electrical shops put the back office on rails, with estimates, invoicing and payment follow up handled automatically, so the work does not stall whenever you are on the tools.")

# ---------------- Angle 3: the enquiry that goes cold ----------------
add("Example Plumbing Inc", "Jordan", "Jordan", "info@example.com",
    "Hey Jordan, about your quote follow-ups",
    "I noticed you take on the awkward jobs a lot of shops pass on, sewer laterals and septic systems included, which stood out.",
    "I imagine one of the biggest challenges is that when you are under a house all day, a call asking for a quote can sit for a few days, and by then that homeowner has usually booked someone else.",
    "I help owner-run plumbing shops reply to every enquiry within seconds and keep following up automatically until the job is booked, so a quote request never goes cold while you are on a job.")

json.dump({"pending": D}, open("outbox.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("wrote " + str(len(D)) + " drafts to outbox.json")
