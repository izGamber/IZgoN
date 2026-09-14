#!/usr/bin/env python3
"""
IzgoN reference client — standard library only.

This is not an SDK. It is the smallest correct implementation of the protocol,
here so you can read it in one sitting and copy the parts that matter into
whatever language you actually use.

`status` has four values and each means a different operation:

    NO_CHANGE      both sides already agree      do nothing
    SYNC_REQUIRED  delta holds the changed keys  MERGE it in
    FULL_STATE     delta holds the whole state   REPLACE with it
    SEND_STATE     the server needs the state    repeat the call with `state`

Treat any status you do not recognise as "resync from scratch".

Two things this client does that are worth copying:

1. CONDITIONAL SYNC. If the state you are about to report is identical to the
   one you last sent, the state does not go on the wire at all — you send back
   the `checksum` the server returned last time, and the server answers
   NO_CHANGE. This is the half of the saving that matters if the device itself
   is on a metered SIM: without it, the device uploads its full report every
   cycle no matter what IzgoN does with the reply.

   The checksum is opaque. You never compute it, you only store the last one
   the server gave you and hand it back. There is no canonical-JSON spec to
   get wrong in your language.

2. EPOCH. A token identifying the lifetime of THIS process's copy of the
   state. Generate one at start-up, send the same value every call. When the
   server sees a new one it answers with the whole state instead of a delta —
   because your mirror is empty and a delta merged into nothing looks like
   success while quietly losing every field it did not mention.

    from izgon_client import IzgonClient

    c = IzgonClient("http://localhost:8000", "dev-local-key")
    for report in my_reports:
        mirror = c.sync("sensor-01", report)
        # mirror now equals report, having transferred only what changed
"""

import json
import urllib.request
import uuid
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
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0,
                 conditional: bool = True, epoch: Optional[str] = None):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout
        self.conditional = conditional
        # One per process lifetime. Anything unique will do.
        self.epoch = epoch or uuid.uuid4().hex
        # Per node: the last state we sent, the checksum the server gave for
        # it, and our mirror of what the server holds.
        self._last_sent: dict[str, dict] = {}
        self._checksum: dict[str, str] = {}
        self._mirror: dict[str, dict] = {}

    # ------------------------------------------------------------------
    @staticmethod
    def _weigh(body: dict) -> int:
        """Bytes this request body puts on the wire."""
        return len(json.dumps(body, separators=(",", ":"),
                              ensure_ascii=False).encode("utf-8"))

    def _post(self, node_id: str, body: dict) -> dict:
        raw = json.dumps(body, separators=(",", ":")).encode()
        req = urllib.request.Request(
            f"{self.base}/api/nodes/{node_id}/sync",
            data=raw,
            headers={"Content-Type": "application/json", "X-API-Key": self.key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _absorb(self, node_id: str, state: dict, result: dict) -> dict:
        status = result.get("status")
        known = self._mirror.get(node_id, {})

        if status == "NO_CHANGE":
            pass
        elif status == "SYNC_REQUIRED":
            known = apply_delta(known, result["delta"])
        elif status == "FULL_STATE":
            known = dict(result["delta"])
        else:
            # Do not guess. A status we do not understand means our mirror is
            # not trustworthy, and pretending otherwise is how silent drift
            # starts.
            raise RuntimeError(
                f"unknown sync status {status!r} — resync this node from scratch"
            )

        self._mirror[node_id] = known
        self._last_sent[node_id] = json.loads(json.dumps(state))
        if result.get("checksum"):
            self._checksum[node_id] = result["checksum"]
        return known

    # ------------------------------------------------------------------
    def sync(self, node_id: str, state: dict) -> dict:
        """Report the current state. Returns our mirror of what the server holds.

        Sends the state only when it has to.
        """
        checksum = self._checksum.get(node_id)
        unchanged = (
            self.conditional
            and checksum is not None
            and self._last_sent.get(node_id) == state
        )

        if unchanged:
            # A conditional call is not automatically cheaper. Against a report
            # of a handful of small fields, the token plus its JSON wrapper can
            # weigh more than the report it replaces - and then "saving data"
            # costs the customer money. Same rule the server applies when it
            # decides between a delta and the whole state: measure both, send
            # the smaller, never claim a saving that is not there.
            skraceno = {"checksum": checksum, "epoch": self.epoch}
            puno = {"state": state, "epoch": self.epoch}
            if self._weigh(skraceno) >= self._weigh(puno):
                unchanged = False

        if unchanged:
            result = self._post(node_id, {"checksum": checksum, "epoch": self.epoch})
            if result.get("status") != "SEND_STATE":
                return self._absorb(node_id, state, result)
            # The server does not hold what we thought it did. Fall through and
            # send everything — this is the path that keeps the two sides from
            # drifting apart, so it must never be skipped or retried blindly.
            self._checksum.pop(node_id, None)
            self._last_sent.pop(node_id, None)
            self._mirror.pop(node_id, None)

        result = self._post(node_id, {"state": state, "epoch": self.epoch})
        if result.get("status") == "SEND_STATE":
            raise RuntimeError(
                "server asked for the state on a call that already carried it"
            )
        return self._absorb(node_id, state, result)
