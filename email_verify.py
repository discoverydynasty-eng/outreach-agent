"""email_verify.py - optional pre-send mailbox verification via a 3rd-party API.

Confirms a mailbox actually EXISTS before the guard sends, to cut bounces at volume
(MX only proves the domain accepts mail; it cannot prove a specific mailbox is real).

OFF unless configured: set config.json "email_verify_provider" to one of
"reoon" | "millionverifier" | "zerobounce" | "neverbounce" | "mails.so", and put the key
in .env as EMAIL_VERIFY_API_KEY. Then send_guard.py checks each address right before sending.
("reoon" has a genuinely-free ~20/day tier and uses POWER mode = real SMTP mailbox check.)

check() returns (verdict, detail):
  "deliverable"   -> mailbox confirmed, send
  "undeliverable" -> bad/does-not-exist/spamtrap, do NOT send
  "risky"         -> catch-all / unknown (can't confirm); guard sends unless skip_risky
  "off"           -> not configured (no key/provider) -> guard behaves as before
  "error"         -> API/network problem -> guard fails OPEN (sends) so an outage can't
                     silently halt everything; the error is logged.
"""
import json
import urllib.parse
import urllib.request


def _get_json(url, timeout, headers=None):
    # MillionVerifier (and others) sit behind Cloudflare, which blocks non-browser
    # User-Agents with HTTP 403 "error code: 1010" BEFORE the API key is ever checked.
    # A normal browser UA sails through. (Diagnosed 2026-07-18 on the mails.so->MV switch.)
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
         "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check(email, provider, api_key, timeout=15):
    provider = (provider or "").lower().strip()
    if not provider or not api_key:
        return ("off", "verification not configured")
    try:
        if provider in ("mails.so", "mailsso", "mails"):
            url = "https://api.mails.so/v1/validate?" + urllib.parse.urlencode({"email": email})
            d = _get_json(url, timeout, headers={"x-mails-api-key": api_key})
            data = d.get("data", d)
            res = (data.get("result") or "").lower()
            return ({"deliverable": "deliverable", "undeliverable": "undeliverable",
                     "risky": "risky", "unknown": "risky"}.get(res, "risky"),
                    f"mails.so:{res or data.get('reason', '?')}")
        if provider == "reoon":
            # POWER mode = full SMTP handshake (catches nonexistent mailboxes even on
            # catch-all-looking Gmail domains); QUICK mode is syntax/MX/disposable only.
            url = "https://emailverifier.reoon.com/api/v1/verify?" + urllib.parse.urlencode(
                {"email": email, "key": api_key, "mode": "power"})
            d = _get_json(url, timeout)
            s = (d.get("status") or "").lower()
            return ({"safe": "deliverable", "valid": "deliverable",
                     "invalid": "undeliverable", "disabled": "undeliverable",
                     "disposable": "undeliverable", "spamtrap": "undeliverable",
                     "catch_all": "risky", "inbox_full": "risky",
                     "role_account": "risky", "unknown": "risky"}.get(s, "risky"),
                    f"reoon:{s or d.get('error', '?')}")
        if provider == "millionverifier":
            url = "https://api.millionverifier.com/api/v3/?" + urllib.parse.urlencode(
                {"api": api_key, "email": email, "timeout": 10})
            d = _get_json(url, timeout)
            r = (d.get("result") or "").lower()
            return ({"ok": "deliverable", "invalid": "undeliverable",
                     "catch_all": "risky", "unknown": "risky",
                     "disposable": "undeliverable"}.get(r, "risky"),
                    f"millionverifier:{r or d.get('error', '?')}")
        if provider == "zerobounce":
            url = "https://api.zerobounce.net/v2/validate?" + urllib.parse.urlencode(
                {"api_key": api_key, "email": email})
            d = _get_json(url, timeout)
            s = (d.get("status") or "").lower()
            return ({"valid": "deliverable", "invalid": "undeliverable",
                     "catch-all": "risky", "unknown": "risky",
                     "do_not_mail": "undeliverable", "spamtrap": "undeliverable",
                     "abuse": "undeliverable"}.get(s, "risky"),
                    f"zerobounce:{s or '?'}")
        if provider == "neverbounce":
            url = "https://api.neverbounce.com/v4/single/check?" + urllib.parse.urlencode(
                {"key": api_key, "email": email})
            d = _get_json(url, timeout)
            r = (d.get("result") or "").lower()
            return ({"valid": "deliverable", "invalid": "undeliverable",
                     "disposable": "undeliverable", "catchall": "risky",
                     "unknown": "risky"}.get(r, "risky"),
                    f"neverbounce:{r or d.get('message', '?')}")
        return ("error", f"unknown provider '{provider}'")
    except Exception as e:
        return ("error", f"{provider} api error: {e}")
