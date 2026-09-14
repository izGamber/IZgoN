#!/usr/bin/env python3
"""
IzgoN reference client — ~60 lines, standard library only.

This is not an SDK. It is the smallest correct implementation of the protocol,
here so you can read it in one sitting and copy the two functions that matter
into whatever language you actually use.

The one thing worth getting right: `status` has three values and each means a
different operation on your local copy of the state.

    NO_CHANGE      both sides already agree      do nothing
    SYNC_REQUIRED  delta holds the changed keys  MERGE it in
    FULL_STATE     delta holds the whole state   REPLACE with it

Treat any status you do not recognise as "resync from scratch".

    from izgon_client import IzgonClient

    c = IzgonClient("http://localhost:8000", "dev-local-key")
    mirror = {}
    for report in my_reports:
        mirror = c.sync("sensor-01", report, mirror)
        # mirror now equals report, having transferred only what changed
"""

import json
import urllib.request
from typing import Optional

DELETED = "__deleted__"


def apply_delta(state: dict, delta: dict) -> dict:
    """Merge a SYNC_REQUIRED delta into a known state. Returns a new dict.

    Nested objects are merged recursively; {"__deleted__": true} removes a key.
    """
    out = dict(state)
    for key, value in delta.items():
        if isinstance(value, dict) and value.get(DELETED) is True:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = apply_delta(out[key], value)
        else:
            out[key] = value
    return out


class IzgonClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout

    def sync(self, node_id: str, state: dict, known: Optional[dict] = None) -> dict:
        """POST the current state; return what the server says the node now holds.

        `known` is your local mirror of that node's state. Pass it in and the
        return value is the updated mirror.
        """
        body = json.dumps({"state": state}, separators=(",", ":")).encode()
        req = urllib.request.Request(
            f"{self.base}/api/nodes/{node_id}/sync",
            data=body,
            headers={"Content-Type": "application/json", "X-API-Key": self.key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read())

        status = result.get("status")
        known = {} if known is None else known

        if status == "NO_CHANGE":
            return known
        if status == "SYNC_REQUIRED":
            return apply_delta(known, result["delta"])
        if status == "FULL_STATE":
            return dict(result["delta"])

        # Unknown status: do not guess. Ask for everything.
        raise RuntimeError(
            f"unknown sync status {status!r} — resync this node from scratch"
        )
