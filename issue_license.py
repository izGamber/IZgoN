#!/usr/bin/env python3
"""
Issue an IzgoN licence key after a sale.

    export DATAPULSE_LICENSE_SECRET="the-same-secret-your-server-uses"
    python3 issue_license.py --email kupac@example.com

Key format is  DPC-<base64(payload)>.<hmac-sha256 hex, first 24 chars>
Payload is     {"email": ..., "product": ..., "issued": <unix ts>}

The script tries to import app.py and validate the key it just made, so you
never email a key that your own server would reject. If app.py can't be
imported here, it prints both base64 variants and tells you how to test.

Keep DATAPULSE_LICENSE_SECRET out of git. Anyone holding it can mint keys.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time


def sign(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:24]


def build(payload: dict, secret: str, urlsafe: bool, strip_padding: bool) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    enc = base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)
    payload_b64 = enc.decode()
    if strip_padding:
        payload_b64 = payload_b64.rstrip("=")
    return f"DPC-{payload_b64}.{sign(payload_b64, secret)}"


def try_validate(key: str, secret: str):
    """Validate against app.py's own validator when we can reach it."""
    try:
        os.environ.setdefault("DATAPULSE_LICENSE_SECRET", secret)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import app  # noqa: F401
    except Exception:
        return None
    validator = getattr(app, "validate_license", None)
    if validator is None:
        return None
    try:
        return validator(key)
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", required=True, help="customer email from the order")
    p.add_argument("--product", default="izgon-pro", help="product identifier stored in the key")
    p.add_argument("--secret", default=os.environ.get("DATAPULSE_LICENSE_SECRET", ""),
                   help="defaults to $DATAPULSE_LICENSE_SECRET")
    p.add_argument("--order", default="", help="order id, recorded in sales.log only")
    args = p.parse_args()

    if not args.secret:
        print("No secret. Set DATAPULSE_LICENSE_SECRET or pass --secret.", file=sys.stderr)
        return 2

    payload = {"email": args.email, "product": args.product, "issued": int(time.time())}

    variants = [
        ("urlsafe, no padding", build(payload, args.secret, True, True)),
        ("urlsafe, padded", build(payload, args.secret, True, False)),
        ("standard, no padding", build(payload, args.secret, False, True)),
        ("standard, padded", build(payload, args.secret, False, False)),
    ]

    accepted = [(label, key) for label, key in variants if try_validate(key, args.secret)]

    print()
    if accepted:
        label, key = accepted[0]
        print("Key (verified against app.py — this one works):")
        print(f"\n    {key}\n")
        print(f"  encoding: {label}")
    else:
        print("Could not import app.py to verify, so here are all variants.")
        print("Test one before sending it: set it as DATAPULSE_LICENSE_KEY,")
        print("restart, and check GET /api/license shows licensed: true.\n")
        for label, key in variants:
            print(f"  [{label}]\n    {key}\n")

    print(f"  email:   {payload['email']}")
    print(f"  product: {payload['product']}")
    print(f"  issued:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(payload['issued']))}")

    # Append to a local sales log so you always know who holds which key.
    chosen = accepted[0][1] if accepted else variants[0][1]
    line = json.dumps({
        "issued_at": payload["issued"],
        "email": payload["email"],
        "product": payload["product"],
        "order": args.order,
        "key": chosen,
    }, ensure_ascii=False)
    with open("sales.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print("\n  recorded in sales.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
