#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Issue one licence pair (secret + key) for ONE IzgoN buyer.

    python3 issue_license.py --email buyer@example.com --order LS-1234

Two decisions worth knowing about:

1. Every buyer gets their OWN secret, generated here, at random. There is no
   single shared secret that would break the whole scheme if it leaked. The
   buyer's server needs the secret to validate the key at all — without it,
   validation returns None and the key does nothing. A leaked pair therefore
   unlocks exactly one install, and the buyer's email is inside the key.

2. It produces only the encoding the server actually accepts: urlsafe base64
   WITH padding. Unpadded variants fail base64 decoding on the server side.
   The key is verified here, with the same procedure the server uses, before
   anything is printed — if that check fails, nothing is sent.

Prints a ready-to-send email and appends a row to sales.log (already in
.gitignore). That log is the only record of the secrets: without it you cannot
re-send a buyer their key.
"""

import argparse
import base64
import hashlib
import hmac
import json
import pathlib
import secrets
import sys
import time


def make_pair(email: str, product: str = "izgon-pro"):
    """Return (secret, key, payload) for this buyer."""
    secret = secrets.token_hex(32)
    payload = {"email": email, "product": product, "issued": int(time.time())}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(raw).decode()          # padding is KEPT
    sig = hmac.new(secret.encode(), b64.encode(), hashlib.sha256).hexdigest()[:24]
    return secret, f"DPC-{b64}.{sig}", payload


def verify(key: str, secret: str) -> bool:
    """The same procedure the server runs. If this fails, do not send the key."""
    try:
        if not key.startswith("DPC-") or "." not in key:
            return False
        b64, sig = key[4:].rsplit(".", 1)
        expected = hmac.new(secret.encode(), b64.encode(), hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(sig, expected):
            return False
        json.loads(base64.urlsafe_b64decode(b64.encode()).decode())
        return True
    except Exception:
        return False


EMAIL = """Subject: Your IzgoN licence key

Hello,

Thank you for buying an IzgoN commercial licence. Below are the two values
your instance needs. Both are required - the key alone does nothing.

Add these two lines to your .env file:

    DATAPULSE_LICENSE_KEY={key}
    DATAPULSE_LICENSE_SECRET={secret}

Then restart and confirm:

    docker compose up -d
    curl http://localhost:8000/api/license

You should see "licensed": true.

Copy the key including the trailing "=" - it is part of the key.
These two values are tied to {email} and are yours alone. Keep them out of
git and out of public issues.

Setup guide, benchmark instructions and the stated limitations are in the
file attached to your order. Anything unclear, or it does not validate:
https://github.com/izGamber/IZgoN/issues

Adnan Drndic
IzgoN
"""


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", required=True, help="buyer email from the order")
    p.add_argument("--order", default="", help="order number, for your own records")
    p.add_argument("--product", default="izgon-pro")
    a = p.parse_args()

    secret, key, _payload = make_pair(a.email, a.product)

    if not verify(key, secret):
        print("ERROR: the key failed its own verification. Do not send it.",
              file=sys.stderr)
        return 1

    print("\n" + "=" * 68)
    print("  VERIFIED - this pair validates against the server")
    print("=" * 68)
    print(EMAIL.format(key=key, secret=secret, email=a.email))
    print("=" * 68)

    log = pathlib.Path("sales.log")
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "email": a.email,
            "order": a.order,
            "product": a.product,
            "key": key,
            "secret": secret,
        }, ensure_ascii=False) + "\n")
    print(f"\n  written to {log.resolve()}")
    print("  sales.log is excluded by .gitignore - keep it, it is your only")
    print("  record of the secrets you issued\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
