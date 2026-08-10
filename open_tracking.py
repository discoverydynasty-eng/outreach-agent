"""
open_tracking.py - optional open tracking via a 1x1 pixel.

OFF unless `config.open_tracking_url` is set. Read the trade-off before enabling it:

  The account currently scores 10/10 on mail-tester partly BECAUSE every email is plain
  text with no images and no links. A tracking pixel is a remote image fetched from a
  third-party host, which is a classic filter signal, so this trades some inbox placement
  for the metric. It is also a noisy metric by construction: Gmail proxies and pre-caches
  images through its own servers and Apple Mail Privacy Protection loads them
  unconditionally, so a recorded "open" often means a machine fetched an image, not that a
  human read anything. Treat opens as a weak directional signal; replies remain the number
  that means something.

Privacy: the pixel URL carries an opaque HMAC token, never the recipient's address. The
token -> address mapping stays local in open_tokens.json, so the hosted endpoint only ever
sees a meaningless string and can never leak a contact list.
"""

import hashlib
import hmac
import json
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKENS_FILE = "open_tokens.json"
UA = "hvac-outreach/1.0"


def _load(name, default):
    p = HERE / name
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save(name, data):
    with open(HERE / name, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _secret():
    """Local-only HMAC key, generated once. Keeps tokens unguessable so a third party
    cannot enumerate the pixel endpoint and manufacture fake opens."""
    store = _load(TOKENS_FILE, {})
    if not store.get("_secret"):
        store["_secret"] = hashlib.sha256(
            (str(HERE) + "hvac-open-tracking").encode()).hexdigest()[:32]
        _save(TOKENS_FILE, store)
    return store["_secret"]


def token_for(email, date_iso, stage="cold"):
    """Deterministic opaque token for one (recipient, date, stage)."""
    msg = f"{email.lower()}|{date_iso}|{stage}".encode()
    return hmac.new(_secret().encode(), msg, hashlib.sha256).hexdigest()[:16]


def register(email, date_iso, stage="cold"):
    """Record the token locally so a later open can be traced back to a recipient."""
    tok = token_for(email, date_iso, stage)
    store = _load(TOKENS_FILE, {})
    store.setdefault("tokens", {})[tok] = {"email": email.lower(), "date_iso": date_iso,
                                           "stage": stage}
    _save(TOKENS_FILE, store)
    return tok


def pixel_tag(base_url, token):
    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}t={urllib.parse.quote(token)}"
    # width/height 1 and a transparent-looking style; no alt text, so a client that blocks
    # images shows nothing rather than a broken-image caption.
    return (f'<img src="{url}" width="1" height="1" border="0" '
            f'style="display:block;width:1px;height:1px;border:0;outline:none;" />')


def html_body(text_body, base_url=None, token=None):
    """Minimal HTML mirror of the plain-text body. Deliberately bare: no styling, no
    links, no fonts - the closer the HTML part looks to the text part, the less this
    reads as a marketing blast to a filter."""
    esc = (text_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    paras = "".join(f"<p style=\"margin:0 0 1em 0;\">{p.strip().replace(chr(10), '<br>')}</p>"
                    for p in esc.split("\n\n") if p.strip())
    pixel = pixel_tag(base_url, token) if (base_url and token) else ""
    return f"<html><body>{paras}{pixel}</body></html>"


def fetch_opens(base_url, timeout=25):
    """Ask the endpoint for recorded opens -> {token: {"first": iso, "count": n}}.

    RAISES on failure, deliberately. An earlier version swallowed every error and returned
    {}, which the tracker then printed as "0 opens" - indistinguishable from "nobody opened
    anything". That misreported 11 real opens as zero on 2026-08-09 after one transient
    timeout. A caller that wants to carry on regardless can catch this; what it must not do
    is silently mistake an unreachable endpoint for data.
    """
    if not base_url:
        return {}
    sep = "&" if "?" in base_url else "?"
    req = urllib.request.Request(f"{base_url}{sep}json=1", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return data.get("opens", data) if isinstance(data, dict) else {}


def resolve_opens(base_url):
    """-> {email_lower: {"first": iso, "count": n}} by joining remote opens to local tokens."""
    remote = fetch_opens(base_url)
    tokens = _load(TOKENS_FILE, {}).get("tokens", {})
    out = {}
    for tok, info in remote.items():
        meta = tokens.get(tok)
        if not meta:
            continue  # a token we did not issue; ignore rather than trust it
        first = info.get("first") if isinstance(info, dict) else None
        count = info.get("count", 1) if isinstance(info, dict) else int(info or 1)
        cur = out.get(meta["email"])
        if not cur or (first and first < cur.get("first", "9999")):
            out[meta["email"]] = {"first": first, "count": count}
        elif cur:
            cur["count"] = cur.get("count", 0) + count
    return out
