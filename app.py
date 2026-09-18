"""
IzgoN - single-file deploy build (v1.4.2 - honest byte accounting: one compact
ruler on both sides of the comparison, and a delta is never sent when it would
be bigger than the state it replaces).

This file is kept intentionally compact to preserve the single-file deploy story,
while the repo still carries the corresponding documentation and checks in the
normal, reviewable layout.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Optional

import redis
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

# ============================== engine.py ==============================

_DELETED = "__deleted__"
_MISSING = object()


def _hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Length of the token that goes on the wire. The full SHA-256 is 64 characters,
# and a device echoing it back on a conditional sync pays for every one of them
# on its own SIM - against a 200-byte report that is most of the saving spent on
# the receipt. 128 bits is far beyond what this needs: the only consequence of a
# collision is one missed update on one node, and the states being compared are
# consecutive reports from the same device, not attacker-chosen inputs.
#
# The full digest is still what the engine compares internally. Only the token
# handed to clients is shortened.
_TOKEN_CHARS = 32


def _token(payload: Optional[dict]) -> Optional[str]:
    """The opaque handle a client echoes back to say 'nothing changed'."""
    if payload is None:
        return None
    return _hash(payload)[:_TOKEN_CHARS]


def compute_delta(old_state: Optional[dict], new_state: dict) -> dict:
    """Return the minimal delta needed to bring old_state to new_state.

    - Fields present in new_state with a new or changed value are included.
    - Fields present in old_state but missing from new_state are marked
      deleted (so the receiver can actually remove them - the earlier
      prototype silently dropped this case).
    - Nested dict values are diffed recursively. Non-dict values (including
      lists) are compared by equality, not diffed element-by-element - see
      README for that limitation.
    """
    old_state = old_state or {}
    delta: dict[str, Any] = {}

    for key, new_value in new_state.items():
        old_value = old_state.get(key, _MISSING)
        if isinstance(new_value, dict) and isinstance(old_value, dict):
            nested = compute_delta(old_value, new_value)
            if nested:
                delta[key] = nested
        elif old_value is _MISSING or old_value != new_value:
            delta[key] = new_value

    for key in old_state:
        if key not in new_state:
            delta[key] = {_DELETED: True}

    return delta


def _wire_bytes(obj) -> bytes:
    """Serialize the way anything paying for bandwidth actually would.

    json.dumps() defaults to ", " and ": " separators, which adds two bytes per
    field to every measurement. No device on a metered SIM sends those spaces,
    and mixing this ruler with a compact one on the other side of the
    comparison produces a savings figure that is simply wrong. One ruler,
    compact, on both sides.

    ensure_ascii=False for the same reason. The default escapes every non-ASCII
    character to \uXXXX, so {"grad":"日本東京"} was counted as 75 bytes when the
    wire carries 43. Any fleet reporting Chinese, Japanese, Cyrillic or our own
    diacritics had its byte totals overstated by most of half.
    """
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class DeltaSyncEngine:
    """Stateless per call: the caller supplies old_state (fetched from
    wherever it's persisted) and gets back a sync decision plus the real
    byte sizes involved, so callers can measure actual savings instead of
    asserting a number."""

    def evaluate(self, node_id: str, old_state: Optional[dict], new_state: dict) -> dict:
        new_hash = _hash(new_state)
        old_hash = _hash(old_state) if old_state is not None else None
        full_bytes = _wire_bytes(new_state)

        if old_hash == new_hash:
            return {
                "node_id": node_id,
                "status": "NO_CHANGE",
                "checksum": new_hash[:_TOKEN_CHARS],
                "delta": None,
                "bytes_full": len(full_bytes),
                "bytes_sent": 0,
            }

        delta = compute_delta(old_state, new_state)
        delta_bytes = _wire_bytes(delta)

        # A delta is not always smaller. Drop enough keys at once and the
        # tombstones outweigh what is left: {"a":1} is 7 bytes, while the delta
        # that removes four sibling keys is 101. Sending the delta there would
        # cost the customer money to save them nothing, so send the state and
        # say so. bytes_sent can therefore never exceed bytes_full, and the
        # savings figure can never go negative.
        if len(delta_bytes) >= len(full_bytes):
            return {
                "node_id": node_id,
                "status": "FULL_STATE",
                "checksum": new_hash[:_TOKEN_CHARS],
                "delta": new_state,
                "bytes_full": len(full_bytes),
                "bytes_sent": len(full_bytes),
            }

        return {
            "node_id": node_id,
            "status": "SYNC_REQUIRED",
            "checksum": new_hash[:_TOKEN_CHARS],
            "delta": delta,
            "bytes_full": len(full_bytes),
            "bytes_sent": len(delta_bytes),
        }

# ============================== storage.py ==============================

REDIS_URL = os.environ.get("DATAPULSE_REDIS_URL", "redis://localhost:6379/0")
_PREFIX = "datapulse:state:"

_client = redis.from_url(REDIS_URL, decode_responses=True)

# Memory fallback.
#
# `docker run ghcr.io/izgamber/izgon:latest` starts this container and nothing
# else. Without a fallback the first sync died with an unexplained HTTP 500,
# because every read went straight to a Redis that was not there - so the one
# command we tell people to try was the one path that could not work.
#
# When Redis is unreachable the last-known state is kept in this process
# instead. Nothing is lost that matters: a restart simply means every node
# resyncs FULL_STATE once, which is what a cold Redis would have produced
# anyway. It is not silent - /healthz reports the degraded storage, the
# dashboard shows a banner, and a line goes to the log the first time.
#
# This is safe here only because IzgoN is single-instance by design (see the
# Limits section of the README). With several replicas sharing one Redis, an
# in-memory fallback would let them diverge, and the right answer would be to
# fail loudly instead.
ALLOW_MEMORY_FALLBACK = os.environ.get("DATAPULSE_ALLOW_MEMORY_FALLBACK", "1") != "0"

_memory_state: dict[str, str] = {}
_degraded = False
_state_lock_map: dict[str, threading.RLock] = {}
_state_lock_guard = threading.Lock()


def _node_lock(node_id: str) -> threading.RLock:
    with _state_lock_guard:
        return _state_lock_map.setdefault(node_id, threading.RLock())


def _degrade(exc: Exception) -> None:
    """Switch to the in-memory store, once, loudly."""
    global _degraded
    if not _degraded:
        _degraded = True
        print(
            f"[izgon] WARNING: Redis is unreachable ({exc.__class__.__name__}: {exc}). "
            f"Keeping node state in memory instead - it is lost when this process "
            f"stops, and every node will resync in full once after a restart. "
            f"Start Redis (see docker-compose.yml) for a setup that survives "
            f"restarts, or set DATAPULSE_ALLOW_MEMORY_FALLBACK=0 to fail instead.",
            flush=True,
        )


def _recover_memory_to_redis() -> bool:
    """Copy any in-memory fallback state back into Redis once Redis responds again."""
    global _degraded
    if _degraded and not _memory_state:
        _degraded = False
        return True
    if not _memory_state:
        return True
    try:
        pipe = _client.pipeline(transaction=False)
        for key, blob in list(_memory_state.items()):
            pipe.set(key, blob)
        pipe.execute()
        _memory_state.clear()
        _degraded = False
        print("[izgon] Redis recovered; syncing the in-memory fallback back into Redis.", flush=True)
        return True
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
        return False


def storage_mode() -> str:
    return "memory (Redis unreachable)" if _degraded else "redis"


def _redis_or_memory(redis_call, memory_call):
    """Try Redis; on connection failure fall back, unless told not to."""
    try:
        result = redis_call()
        if _degraded:
            _recover_memory_to_redis()
        return result
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        if not ALLOW_MEMORY_FALLBACK:
            raise
        _degrade(exc)
        return memory_call()


def _key(node_id: str) -> str:
    return f"{_PREFIX}{node_id}"


def _memory_get(node_id: str) -> Optional[str]:
    raw = _memory_state.get(_key(node_id))
    if raw is None:
        raw = _sqlite_get_state(node_id)
        if raw is not None:
            _memory_state[_key(node_id)] = raw
    return raw


def get_state(node_id: str) -> Optional[dict]:
    raw = _redis_or_memory(
        lambda: _client.get(_key(node_id)),
        lambda: _memory_get(node_id),
    )
    return json.loads(raw) if raw else None


def _memory_set(node_id: str, blob: str) -> None:
    _memory_state[_key(node_id)] = blob
    _sqlite_put_state(node_id, blob)


def set_state(node_id: str, state: dict) -> None:
    blob = json.dumps(state)
    _redis_or_memory(
        lambda: _client.set(_key(node_id), blob),
        lambda: _memory_set(node_id, blob),
    )


def list_node_ids() -> list[str]:
    keys = _redis_or_memory(
        lambda: list(_client.scan_iter(match=f"{_PREFIX}*")),
        lambda: list({*_memory_state.keys(), *(_key(n) for n in _sqlite_node_ids())}),
    )
    return [k[len(_PREFIX):] for k in keys]


def ping() -> bool:
    try:
        return bool(_client.ping())
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
        return False


def _sqlite_put_state(node_id: str, blob: str) -> None:
    """Write the baseline through to SQLite while Redis is unreachable.

    The memory fallback added in 1.1.2 kept the server answering, but a restart
    still made every node resync in full. Nothing is lost when that happens -
    it is just paid for, in exactly the bytes this product exists to save."""
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO node_states (node_id, state, ts) VALUES (?,?,?) "
                "ON CONFLICT(node_id) DO UPDATE SET state=excluded.state, ts=excluded.ts",
                (node_id, blob, time.time()),
            )
            conn.commit()
    except sqlite3.Error:
        pass  # the sync itself must not fail because the spare copy did


def _sqlite_get_state(node_id: str) -> Optional[str]:
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT state FROM node_states WHERE node_id = ?", (node_id,)
            ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _sqlite_node_ids() -> list[str]:
    try:
        with _conn() as conn:
            return [r[0] for r in conn.execute("SELECT node_id FROM node_states")]
    except sqlite3.Error:
        return []


def flush_all_state() -> None:
    """Test/dev helper only - clears all node state."""
    def _redis_flush():
        for k in _client.scan_iter(match=f"{_PREFIX}*"):
            _client.delete(k)
        # The counters too. Without this a test suite run on a machine that has
        # Redis inherits the previous run's totals and every byte assertion in
        # it fails for a reason that has nothing to do with the code.
        for k in _client.scan_iter(match=f"{_MPREFIX}*"):
            _client.delete(k)

    def _memory_flush():
        _memory_state.clear()
        try:
            with _conn() as conn:
                conn.execute("DELETE FROM node_states")
                conn.commit()
        except sqlite3.Error:
            pass

    _redis_or_memory(_redis_flush, _memory_flush)

# ============================== metrics_db.py ==============================

DB_PATH = os.environ.get("DATAPULSE_DB_PATH", "datapulse_events.db")

_SCHEMA = """
-- bytes_full / bytes_sent measure the REPLY: what the server would have had to
-- send back versus what it did. uplink_full / uplink_sent measure the REQUEST
-- the same way, which is the half a device pays for on its own SIM. Rows
-- written before v1.4.0 carry 0 in both uplink columns and are excluded from
-- the uplink figures rather than counted as a saving nobody measured.
CREATE TABLE IF NOT EXISTS sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    bytes_full INTEGER NOT NULL,
    bytes_sent INTEGER NOT NULL,
    uplink_full INTEGER NOT NULL DEFAULT 0,
    uplink_sent INTEGER NOT NULL DEFAULT 0,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_events_node ON sync_events (node_id);
CREATE INDEX IF NOT EXISTS idx_sync_events_ts ON sync_events (ts);

-- Baselines, so a restart without Redis does not make every node resync in
-- full. Redis stays the primary store; this is what the memory fallback
-- writes through to, and reads from when the process starts cold.
CREATE TABLE IF NOT EXISTS node_states (
    node_id TEXT PRIMARY KEY,
    state   TEXT NOT NULL,
    ts      REAL NOT NULL
);
"""


@contextmanager
def _conn():
    # timeout: wait for a competing writer instead of raising
    # "database is locked" the moment two syncs overlap.
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    try:
        # WAL lets the dashboard read /api/metrics while a sync is being
        # written. Without it every read blocks every write and back again.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
    finally:
        conn.close()


def _migrate(conn) -> None:
    """Add columns a database created by an older version does not have.

    CREATE TABLE IF NOT EXISTS leaves an existing table exactly as it was, so
    upgrading in place over a log with real history would otherwise fail on the
    first INSERT naming the new columns.
    """
    have = {row[1] for row in conn.execute("PRAGMA table_info(sync_events)")}
    for column in ("uplink_full", "uplink_sent"):
        if column not in have:
            conn.execute(
                f"ALTER TABLE sync_events ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
            )


def init_db(db_path: Optional[str] = None) -> None:
    global DB_PATH
    if db_path:
        DB_PATH = db_path
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
    _reset_event_count()
    # Before any traffic. See the note on _seed_counters_if_empty for why the
    # ordering is the whole safety of it.
    _seed_counters_if_empty()


# ---------------------------- counters in Redis ----------------------------
#
# The event log is a SQLite file, and a SQLite file lives on a disk. On a host
# with no persistent disk - Render's free plan, `docker run` with no volume,
# any ephemeral container - that file is gone the moment the process stops. The
# node states survive, because those are in Redis; the counters do not, so the
# dashboard resets to zero and the one page meant to prove the saving proves
# nothing instead. Refilling it by hand is not a fix, it is a treadmill.
#
# So the totals live beside the state, in Redis. Nine integers and one set,
# incremented in a single pipeline per event. Not a copy of the log - the log
# stays in SQLite, per row, for anyone who wants to query it - just the sums
# the dashboard actually reads.
#
# SQLite remains the answer when there is no Redis. Which source produced a
# given reading is reported in `counters`, because a number that quietly halves
# after a reconnect looks like data loss, and a reader deserves to know it is
# not.
_MPREFIX = "datapulse:metrics:"
_M_SEEDED = _MPREFIX + "seeded"

# Nine counters, because `both_ways_saved_pct` needs the reply bytes restricted
# to the events where the request was measured too - summing all of them would
# mix in rows that never had an uplink figure.
_M_KEYS = (
    "events",       # total sync events
    "no_change",    # how many answered NO_CHANGE
    "bytes_full",   # reply: what the naive server would have sent
    "bytes_sent",   # reply: what it did send
    "up_rows",      # events where the request itself was measured
    "up_full",      # request: what a device sending the state would have sent
    "up_sent",      # request: what it did send
    "d_full",       # reply bytes, only over the up_rows events
    "d_sent",       # reply bytes actually sent, only over the up_rows events
)


def _mkey(name: str) -> str:
    return _MPREFIX + name


def _metrics_incr(node_id: str, status: str, bytes_full: int, bytes_sent: int,
                 uplink_full: int, uplink_sent: int) -> None:
    """Add one event to the Redis counters. Silent no-op without Redis."""
    def _do():
        pipe = _client.pipeline(transaction=False)
        pipe.incr(_mkey("events"))
        if status == "NO_CHANGE":
            pipe.incr(_mkey("no_change"))
        pipe.incrby(_mkey("bytes_full"), bytes_full)
        pipe.incrby(_mkey("bytes_sent"), bytes_sent)
        if uplink_full > 0:
            pipe.incr(_mkey("up_rows"))
            pipe.incrby(_mkey("up_full"), uplink_full)
            pipe.incrby(_mkey("up_sent"), uplink_sent)
            pipe.incrby(_mkey("d_full"), bytes_full)
            pipe.incrby(_mkey("d_sent"), bytes_sent)
        pipe.sadd(_mkey("nodes"), node_id)
        pipe.execute()

    _redis_or_memory(_do, lambda: None)


def _seed_counters_if_empty() -> None:
    """First run against a Redis that has no counters yet: carry the SQLite
    totals over, so upgrading an existing instance does not reset its history
    to zero.

    Called from init_db, i.e. at start-up, before a single event can have been
    logged. That ordering is the whole safety of it: log_event writes to BOTH
    SQLite and Redis, so seeding after any traffic would add those events a
    second time and inflate every figure on the dashboard. Hence the second
    guard below - if the counter is already moving, this process has served
    traffic and the moment to seed has passed. Skipping then costs the old
    history; seeding anyway would cost the truth.

    SET NX makes it happen once even if two callers race.
    """
    try:
        if int(_client.get(_mkey("events")) or 0) > 0:
            return
        if not _client.set(_M_SEEDED, "1", nx=True):
            return
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
        return
    try:
        with _conn() as conn:
            ev, bf, bs, nc = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes_full),0), "
                "COALESCE(SUM(bytes_sent),0), "
                "COALESCE(SUM(CASE WHEN status='NO_CHANGE' THEN 1 ELSE 0 END),0) "
                "FROM sync_events"
            ).fetchone()
            ur, uf, us, df, ds = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(uplink_full),0), "
                "COALESCE(SUM(uplink_sent),0), COALESCE(SUM(bytes_full),0), "
                "COALESCE(SUM(bytes_sent),0) "
                "FROM sync_events WHERE uplink_full > 0"
            ).fetchone()
            imena = [r[0] for r in conn.execute(
                "SELECT DISTINCT node_id FROM sync_events")]
        if not ev:
            return
        pipe = _client.pipeline(transaction=False)
        for k, v in (("events", ev), ("no_change", nc), ("bytes_full", bf),
                     ("bytes_sent", bs), ("up_rows", ur), ("up_full", uf),
                     ("up_sent", us), ("d_full", df), ("d_sent", ds)):
            pipe.incrby(_mkey(k), int(v))
        if imena:
            pipe.sadd(_mkey("nodes"), *imena)
        pipe.execute()
        print(f"[izgon] carried {ev} existing events from the SQLite log into "
              f"the Redis counters, so the dashboard keeps its history.")
    except (sqlite3.Error, redis.exceptions.RedisError):
        # Seeding is best-effort. Losing it costs history on the dashboard,
        # never correctness of what comes after.
        pass


def _counters_from_redis() -> Optional[dict]:
    """The nine totals and the node count, or None if Redis is not answering.

    Does not seed - that happens once, in init_db, before any traffic. Seeding
    from here would run after events had already been counted and add the
    SQLite rows on top of them."""
    def _do():
        pipe = _client.pipeline(transaction=False)
        for k in _M_KEYS:
            pipe.get(_mkey(k))
        pipe.scard(_mkey("nodes"))
        vals = pipe.execute()
        out = {k: int(v or 0) for k, v in zip(_M_KEYS, vals[:-1])}
        out["nodes"] = int(vals[-1] or 0)
        return out

    try:
        return _redis_or_memory(_do, lambda: None)
    except redis.exceptions.RedisError:
        return None


# The free-tier gate needs the number of logged events on every single sync.
# Reading it with COUNT(*) scans the whole table, so the server gets slower the
# longer it runs. Read it once - from Redis when there is one, because that is
# the count that survives a restart - then keep it in memory.
_event_count: Optional[int] = None
_count_lock = threading.Lock()


def _reset_event_count() -> None:
    global _event_count
    with _count_lock:
        _event_count = None


def event_count() -> int:
    """Total sync events logged. O(1) after the first call."""
    global _event_count
    with _count_lock:
        if _event_count is None:
            # Redis first: on a host with no persistent disk the SQLite file is
            # new after every restart, and counting it would hand a paying
            # customer their free tier back on every deploy.
            try:
                with _conn() as conn:
                    iz_baze = conn.execute(
                        "SELECT COUNT(*) FROM sync_events"
                    ).fetchone()[0]
            except sqlite3.Error:
                # Asked before init_db has run, or the file is not readable.
                # Redis may still know the answer; a crash here would refuse
                # every sync over a counter.
                iz_baze = 0
            c = _counters_from_redis()
            # The larger of the two, always. Undercounting here is the one
            # direction that costs money: it hands a customer past the free
            # tier their free tier back.
            _event_count = max(iz_baze, c["events"]) if c is not None else iz_baze
        return _event_count


def log_event(node_id: str, status: str, bytes_full: int, bytes_sent: int,
              uplink_full: int = 0, uplink_sent: int = 0) -> None:
    global _event_count
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sync_events "
            "(node_id, status, bytes_full, bytes_sent, uplink_full, uplink_sent, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (node_id, status, bytes_full, bytes_sent,
             uplink_full, uplink_sent, time.time()),
        )
        conn.commit()
    _metrics_incr(node_id, status, bytes_full, bytes_sent, uplink_full, uplink_sent)
    with _count_lock:
        if _event_count is not None:
            _event_count += 1


def _counters_from_sqlite() -> dict:
    with _conn() as conn:
        ev, bf, bs, nc = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(bytes_full),0), COALESCE(SUM(bytes_sent),0), "
            "COALESCE(SUM(CASE WHEN status='NO_CHANGE' THEN 1 ELSE 0 END),0) "
            "FROM sync_events"
        ).fetchone()
        nodes = conn.execute(
            "SELECT COUNT(DISTINCT node_id) FROM sync_events"
        ).fetchone()[0]
        # Only rows that actually measured the request. Rows from before v1.4.0
        # have 0 there; averaging them in would invent a saving.
        ur, uf, us, df, ds = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(uplink_full),0), COALESCE(SUM(uplink_sent),0), "
            "COALESCE(SUM(bytes_full),0), COALESCE(SUM(bytes_sent),0) "
            "FROM sync_events WHERE uplink_full > 0"
        ).fetchone()
    return {"events": ev, "no_change": nc, "bytes_full": bf, "bytes_sent": bs,
            "up_rows": ur, "up_full": uf, "up_sent": us,
            "d_full": df, "d_sent": ds, "nodes": nodes}


def real_metrics() -> dict:
    # Redis holds the totals that survive a restart; SQLite holds whatever this
    # process has logged since it started. On a host with a real disk they agree.
    # On one without, only the first is the truth, and saying which was used
    # keeps a reader from reading a reconnect as lost data.
    c = _counters_from_redis()
    izvor = "redis"
    if c is None:
        c = _counters_from_sqlite()
        izvor = "sqlite"
    elif c["events"] == 0:
        # Redis is answering but empty, and this process has a log with rows in
        # it. That is a Redis that was flushed or replaced under a running
        # server. Seeding it from here would land on top of whatever gets
        # counted next and inflate every figure, so the counters are left alone
        # and this one reading comes from the log instead - which is behind, but
        # behind and labelled beats a confident zero. A restart repairs Redis.
        sq = _counters_from_sqlite()
        if sq["events"] > 0:
            c, izvor = sq, "sqlite"

    total_events = c["events"]
    total_full, total_sent, no_change = c["bytes_full"], c["bytes_sent"], c["no_change"]
    nodes = c["nodes"]
    up_rows, up_full, up_sent = c["up_rows"], c["up_full"], c["up_sent"]
    d_full, d_sent = c["d_full"], c["d_sent"]

    if not total_events:
        return {
            "total_sync_events": 0,
            "active_nodes": 0,
            "no_change_events": 0,
            "bytes_full_if_naive": 0,
            "bytes_actually_sent": 0,
            "bandwidth_saved_pct": None,
            "uplink_bytes_if_naive": 0,
            "uplink_bytes_actually_sent": 0,
            "uplink_saved_pct": None,
            "both_ways_saved_pct": None,
            "note": "No sync events logged yet - this is real, not a placeholder. "
                    "Run traffic through POST /api/nodes/{id}/sync to generate metrics.",
            "counters": izvor,
        }

    saved_pct = round((1 - (total_sent / total_full)) * 100, 2) if total_full else 0.0
    up_pct = round((1 - (up_sent / up_full)) * 100, 2) if up_full else None
    # Both directions of the same transaction, over the events where both were
    # measured - against a baseline where the full state travels each way.
    #
    # That baseline is a client that polls and gets everything back, NOT a
    # device that only reports: a reporting device was never receiving the full
    # state, so it has nothing to save in that direction and its figure is
    # uplink_saved_pct. Naming the wrong one is how an honest measurement turns
    # into a misleading claim.
    both = None
    if up_rows and up_full:
        denom = up_full + d_full
        if denom:
            both = round((1 - ((up_sent + d_sent) / denom)) * 100, 2)

    return {
        "total_sync_events": total_events,
        "active_nodes": nodes,
        "no_change_events": no_change or 0,
        "bytes_full_if_naive": total_full,
        "bytes_actually_sent": total_sent,
        "bandwidth_saved_pct": saved_pct,
        "uplink_events_measured": up_rows,
        "uplink_bytes_if_naive": up_full,
        "uplink_bytes_actually_sent": up_sent,
        "uplink_saved_pct": up_pct,
        "both_ways_saved_pct": both,
        # Where these totals came from. "redis" survives a restart; "sqlite" is
        # only what this process logged since it started, which on a host with
        # no persistent disk means since the last deploy.
        "counters": izvor,
    }

# ============================== licensing.py ==============================
#
# Licence keys are signed by the seller with an Ed25519 private key, and
# verified here with the matching PUBLIC key, which is the only half that ships.
#
# The previous scheme signed with HMAC and asked the buyer to put both the key
# and the signing secret into their own .env. Anything the buyer holds, the
# buyer can also generate: a matching pair took ten seconds to make with a
# script that sat in the public repository. The gate stopped nobody.
#
# Ed25519 fixes the half that can be fixed. Nobody can forge a key without the
# private half, and the private half never leaves the seller. What it does not
# do - and no scheme that ships its own source can - is stop someone from
# deleting these lines. That is a licence violation with a legal remedy, not a
# hole left open by the design.

SELLER_PUBLIC_KEY = os.environ.get(
    "DATAPULSE_LICENSE_PUBKEY",
    # Ed25519 public key, base64url. Public by design - it only verifies.
    "G-7lp8KpNF0PKKwO7JSt0j0Cw3_q9ubPPYGImHVzCsA=",
)
FREE_TIER_SYNC_LIMIT = int(os.environ.get("DATAPULSE_FREE_TIER_LIMIT", "10000"))

KEY_PREFIX = "IZG2-"


def _b64norm(text: str) -> str:
    """Canonical base64url padding. Every licence key ends in '==', and
    trailing '=' is the character most likely to be lost on the way to a
    customer's .env - trimmed by hand, eaten by a form, cut by a shell. The
    signature is made over the padded payload string, so a key that lost its
    padding would fail to verify even though nothing about it was forged.
    Padding carries no information, so restoring it is safe."""
    stripped = text.strip().rstrip("=")
    return stripped + "=" * (-len(stripped) % 4)


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(_b64norm(text).encode())


def _load_public_key(b64: str):
    """None rather than an exception: a mangled key must degrade to
    'unlicensed', never take the server down at import time."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        raw = _b64d(b64)
        if len(raw) != 32:
            return None
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        return None


def validate_license(key: Optional[str], public_key_b64: Optional[str] = None) -> Optional[dict]:
    """Returns the decoded payload if the key carries a valid seller signature,
    None otherwise. Never raises."""
    if not key:
        return None

    # A key copied out of an email arrives with whitespace around it more often
    # than not, and sometimes with quotes from a .env line.
    key = key.strip().strip('"').strip("'").strip()

    if key.startswith("DPC-"):
        # A key from the old HMAC scheme. Refused rather than honoured: that
        # format was forgeable by whoever held it.
        print(
            "[izgon] This licence key is in the retired DPC- format and is no "
            "longer accepted. Write to the seller for a replacement - it is free "
            "and takes a minute.",
            flush=True,
        )
        return None

    pub = _load_public_key(public_key_b64 or SELLER_PUBLIC_KEY)
    if pub is None:
        return None

    try:
        from cryptography.exceptions import InvalidSignature
        if not key.startswith(KEY_PREFIX) or "." not in key:
            return None
        payload_b64, sig_b64 = key[len(KEY_PREFIX):].rsplit(".", 1)
        # Verify against the canonical padded form, which is what was signed.
        payload_b64 = _b64norm(payload_b64)
        try:
            pub.verify(_b64d(sig_b64), payload_b64.encode())
        except InvalidSignature:
            return None
        return json.loads(_b64d(payload_b64).decode())
    except Exception:
        return None


# ============================== inline static assets ==============================
INDEX_HTML = '<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n    <title>IzgoN // Dashboard</title>\n    <style>body{font-family:ui-sans-serif,system-ui,sans-serif;background:#0b1020;color:#edf3ff;padding:2rem}h1{font-size:2rem}table{border-collapse:collapse;width:100%;margin-top:1rem}th,td{padding:.7rem;border-bottom:1px solid #2a3759;text-align:left}code{background:#101b30;padding:.15rem .35rem;border-radius:6px}</style>\n</head>\n<body>\n  <h1>IzgoN Dashboard</h1>\n  <p>Live metrics and node state will appear here.</p>\n  <table>\n    <tr><th>Metric</th><th>Value</th></tr>\n    <tr><td>total_sync_events</td><td>__TOTAL_EVENTS__</td></tr>\n    <tr><td>bandwidth_saved_pct</td><td>__BANDWIDTH_SAVED__</td></tr>\n    <tr><td>storage</td><td>__STORAGE__</td></tr>\n  </table>\n</body>\n</html>'
MANIFEST_JSON = '{\n    "id": "/",\n    "name": "IzgoN Dashboard",\n    "short_name": "IzgoN",\n    "description": "Live dashboard for a self-hosted delta-sync service - real metrics only, no placeholder claims.",\n    "start_url": "/",\n    "display": "standalone",\n    "background_color": "#0b1020",\n    "theme_color": "#0ea5e9"\n}'
SW_JS = '// IzgoN - service worker.\n// A minimal shell-only cache to keep the dashboard responsive between reloads.\nself.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));\nself.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));\nself.addEventListener("fetch", (event) => {\n  if (event.request.method !== "GET") return;\n  if (event.request.url.includes("/api/")) return;\n  event.respondWith(fetch(event.request));\n});\n'
ICON_192 = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAYAAABS3GwHAAAH70lEQVR4nO2dS27dRhBF7wsMGJ54QVlFgCwl68hSDGQVXpAmgkbKQKbxfhS7m/2pzzkjD6wnsuueruIjTV+0gK/fvr+v+L1gn7fXl8vM3zfllxF4aGW0EMxNcB2z5ZgHhAgJ1/9bT2Te2OuDb0Y6SiLZg7Uig2gYC+X5Cu50uBfxWdWu1tEw8C52wKImYQ8qMxGZec4iRkV5ZhTjEw1L8e6dTC/8f8q/3Q5aE1H3tCzGU7+zARyT9cE9mL4F4KljxX4n2qk2uQfH9kpZzM4vD8yqGYZnsggAAABJRU5ErkJggg==')
ICON_512 = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAAYmklEQVR4nO3dS44bybkF4CyhAUETLahXYcBL8Tq8FANehRfUE6FH8kBgdz1YVSQzHn/E+b759U0mI+Oc+JOlfjpo5uu37z9nXwPA7v788cfT7tubJb+VgD2M7jTz/ez8z4zJpnqT4kP2nTIfscQCAJbWoS0NAGK3uN3y7KQ5cwYTM2fA2M4uzVSNNu2gj02OzuKA0w7Q3dP8N1bZi3g2e98zGm0ozUhvdv7zS77kL1G3uvYlgP+0u9c9XKX5IYlN7blJpYVHDo9EXj1Mc1561FGwJ9gE5f0iK9kYIYdU1Wm+O8f9n7c9zZvSU2HnK4yQK8v1O//4pYX4swtrIB+g84bQfW9dKkYJ4P4d9BDrI5g==')
ICON_512_MASKABLE = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAATMElEQVR4nO3dS44bybkF4CyhAUETLahXYcBL8Tq8FANehRfUE6FH8kBgdz1YVSQzHn/E+b759U0mI+Oc+JOlfjpo5uu37z9nXwPA7v788cfT7tubJb+VgD2M7jTz/ez8z4zJpnqT4kP2nTIfscQCAJbWoS0NAGK3uN3y7KQ5cwYTM2fA2M4uzVSNNu2gj02OzuKA0w7Q3dP8N1bZi3g2e98zGm0ozUhvdv7zS77kL1G3uvYlgP+0u9c9XKX5IYlN7blJpYVHDo9EXj1Mc1561FGwJ9gE5f0iK9kYIYdU1Wm+O8f9n7c9zZvSU2HnK4yQK8v1O//4pYX4swtrIB+g84bQfW9dKkYJ4P4d9BDrI5g==')

# ============================== main.py (app) ==============================
API_KEY = os.environ.get("DATAPULSE_API_KEY", "dev-local-key")
LICENSE_KEY = os.environ.get("DATAPULSE_LICENSE_KEY")
# Checkout link shown when the free tier is exhausted.
# Set DATAPULSE_PURCHASE_URL to your Lemon Squeezy checkout before deploying.
PURCHASE_URL = os.environ.get(
    "DATAPULSE_PURCHASE_URL",
    "https://github.com/izGamber/IZgoN#licence-and-price",
)
ALLOWED_ORIGINS = os.environ.get("DATAPULSE_ALLOWED_ORIGINS", "*").split(",")


# ---------------------------- silence alerting ----------------------------
#
# The second thing a fleet operator asks for after the data itself is "tell me
# when a device stops reporting". Off unless DATAPULSE_ALERT_URL is set.
#
# Three things it deliberately does NOT do, because each of them is how this
# kind of watchdog turns into noise nobody reads:
#   * it does not alert per loop, only on the transition into silence and back;
#   * it does not alert for nodes it has never seen report;
#   * it does not fire a storm for the window the server itself was down - the
#     first pass after start only records who is already silent.
ALERT_URL = os.environ.get("DATAPULSE_ALERT_URL", "")
ALERT_AFTER = float(os.environ.get("DATAPULSE_ALERT_AFTER", "300"))
ALERT_EVERY = float(os.environ.get("DATAPULSE_ALERT_EVERY", "30"))

_silent: set[str] = set()
_alert_primed = False
_alerts_sent = 0


def last_seen() -> dict[str, float]:
    try:
        with _conn() as conn:
            return {r[0]: r[1] for r in conn.execute(
                "SELECT node_id, MAX(ts) FROM sync_events GROUP BY node_id")}
    except sqlite3.Error:
        return {}


def _post_alert(payload: dict) -> None:
    global _alerts_sent
    import urllib.request
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        ALERT_URL, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "IzgoN"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
        _alerts_sent += 1
    except Exception as exc:  # a dead webhook must not take the server with it
        print(f"[izgon] alert POST failed: {exc.__class__.__name__}: {exc}", flush=True)


def check_silence(now: Optional[float] = None) -> list[dict]:
    """One pass. Returns the alerts it decided to send, so it can be tested
    without a webhook and without waiting."""
    global _alert_primed
    now = time.time() if now is None else now
    seen = last_seen()
    out = []
    quiet_now = {n for n, ts in seen.items() if now - ts > ALERT_AFTER}

    if not _alert_primed:
        # First pass after start: record the state, announce nothing.
        _silent.clear()
        _silent.update(quiet_now)
        _alert_primed = True
        return []

    for n in sorted(quiet_now - _silent):
        out.append({"event": "silent", "node_id": n,
                        "silent_for_seconds": round(now - seen[n], 1),
                        "threshold_seconds": ALERT_AFTER})
    for n in sorted(_silent - quiet_now):
        if n in seen:
            out.append({"event": "recovered", "node_id": n,
                            "last_seen_seconds_ago": round(now - seen[n], 1)})
    _silent.clear()
    _silent.update(quiet_now)
    return out


async def _alert_loop():
    import asyncio
    while True:
        try:
            for a in check_silence():
                if ALERT_URL:
                    _post_alert(a)
        except Exception as exc:
            print(f"[izgon] alert loop error: {exc.__class__.__name__}: {exc}", flush=True)
        await asyncio.sleep(ALERT_EVERY)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    init_db()
    task = None
    if ALERT_URL:
        print(f"[izgon] silence alerts on: POST to {ALERT_URL} when a node goes "
              f"quiet for more than {ALERT_AFTER:.0f}s", flush=True)
        task = asyncio.create_task(_alert_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="IzgoN", version="1.4.2", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)
engine = DeltaSyncEngine()


def _check_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    # compare_digest, not ==. A plain comparison returns as soon as two
    # characters differ, which hands the key to anyone patient enough to time
    # the responses. This one always takes the same time.
    if x_api_key is None or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def _license_status() -> dict:
    payload = validate_license(LICENSE_KEY)
    return {"licensed": payload is not None, "details": payload}


class SyncRequest(BaseModel):
    state: Optional[dict] = None
    checksum: Optional[str] = None
    epoch: Optional[str] = None
    interval: Optional[float] = None


ADAPTIVE = os.environ.get("DATAPULSE_ADAPTIVE", "1") != "0"
QUIET_AFTER = int(os.environ.get("DATAPULSE_QUIET_AFTER", "3"))
MAX_INTERVAL = float(os.environ.get("DATAPULSE_MAX_INTERVAL", "300"))
INTERVAL_FACTOR = float(os.environ.get("DATAPULSE_INTERVAL_FACTOR", "2"))

_quiet_runs: dict[str, int] = {}
_quiet_lock = threading.Lock()


def _advise_interval(node_id: str, status: str, current: Optional[float]) -> Optional[dict]:
    if not ADAPTIVE:
        return None
    with _quiet_lock:
        if status == "NO_CHANGE":
            n = _quiet_runs.get(node_id, 0) + 1
            _quiet_runs[node_id] = n
        else:
            n = 0
            _quiet_runs.pop(node_id, None)
    if current is None or current <= 0:
        return None
    if status != "NO_CHANGE":
        return {"next_interval": current, "reason": "changed", "max_staleness": current}
    if n < QUIET_AFTER:
        return {"next_interval": current, "reason": "settling", "max_staleness": current}
    steps = n - QUIET_AFTER + 1
    proposed = min(MAX_INTERVAL, current * (INTERVAL_FACTOR ** steps))
    return {
        "next_interval": round(proposed, 3),
        "reason": f"{n} identical reports in a row",
        "max_staleness": round(proposed, 3),
    }


_epoches: dict[str, str] = {}
_epoch_lock = threading.Lock()
_EPOCH_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _epoch_is_new(node_id: str, epoch: Optional[str]) -> bool:
    if epoch is None:
        return False
    with _epoch_lock:
        return _epoches.get(node_id) != epoch


def _remember_epoch(node_id: str, epoch: Optional[str]) -> None:
    if epoch is None:
        return
    with _epoch_lock:
        _epoches[node_id] = epoch


_NODE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MAX_STATE_DEPTH = int(os.environ.get("DATAPULSE_MAX_STATE_DEPTH", "32"))
MAX_STATE_BYTES = int(os.environ.get("DATAPULSE_MAX_STATE_BYTES", str(1024 * 1024)))


def _too_deep(obj, limit: int) -> bool:
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(node, dict):
            children = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, depth + 1))
    return False


_CHECKSUM_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")


def _check_node_id(node_id: str) -> None:
    if not _NODE_ID_RE.match(node_id):
        raise HTTPException(
            status_code=422,
            detail="node_id must be 1-128 characters of A-Z a-z 0-9 . _ : -",
        )


def _check_free_tier() -> None:
    if _license_status()["licensed"]:
        return
    if event_count() >= FREE_TIER_SYNC_LIMIT:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Free evaluation limit reached ({FREE_TIER_SYNC_LIMIT} sync "
                f"events). Buy a license and set DATAPULSE_LICENSE_KEY to "
                f"continue: {PURCHASE_URL}"
            ),
        )


def _check_state_limits(state: dict) -> int:
    if _too_deep(state, MAX_STATE_DEPTH):
        raise HTTPException(
            status_code=422,
            detail=(
                f"state is nested deeper than {MAX_STATE_DEPTH} levels; raise "
                "DATAPULSE_MAX_STATE_DEPTH if you really report structures that deep"
            ),
        )
    state_bytes = len(_wire_bytes(state))
    if state_bytes > MAX_STATE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"state is {state_bytes} bytes, limit is {MAX_STATE_BYTES}; raise "
                "DATAPULSE_MAX_STATE_BYTES if your reports are genuinely this large"
            ),
        )
    return state_bytes


def _send_state(node_id: str, reason: str) -> dict:
    """Ask for the whole state. Deliberately not logged as a sync event.

    Nothing was synchronised and nothing was measured, so counting it would
    inflate the savings figure with round trips that carried no data and would
    burn a free-tier sync the customer got nothing for.
    """
    return {
        "node_id": node_id,
        "status": "SEND_STATE",
        "checksum": None,
        "delta": None,
        "bytes_full": 0,
        "bytes_sent": 0,
        "reason": reason,
    }


@app.post("/api/nodes/{node_id}/sync")
def sync_node(node_id: str, body: SyncRequest, _=Depends(_check_key)) -> dict:
    _check_node_id(node_id)

    if (body.state is None) == (body.checksum is None):
        raise HTTPException(
            status_code=422,
            detail=(
                "send exactly one of `state` (the full report) or `checksum` "
                "(the token from this server's last reply, when nothing has "
                "changed on the device)"
            ),
        )
    if body.epoch is not None and not _EPOCH_RE.match(body.epoch):
        raise HTTPException(
            status_code=422,
            detail="epoch must be 1-128 characters of A-Z a-z 0-9 . _ : -",
        )
    if body.checksum is not None and not _CHECKSUM_RE.match(body.checksum):
        raise HTTPException(
            status_code=422,
            detail="checksum must be the token from a previous reply of this server",
        )

    with _node_lock(node_id):
        fresh_mirror = _epoch_is_new(node_id, body.epoch)
        sent_bytes = len(_wire_bytes(body.model_dump(exclude_none=True)))

        # ---- conditional call: the state never left the device ----
        if body.checksum is not None:
            old_state = get_state(node_id)
            if fresh_mirror:
                return _send_state(node_id, "epoch changed - this copy of the state is new")
            if old_state is None:
                return _send_state(node_id, "no baseline held for this node")
            if _token(old_state) != body.checksum:
                return _send_state(node_id, "checksum does not match the baseline held here")

            _check_free_tier()
            baseline_bytes = len(_wire_bytes(old_state))
            result = {
                "node_id": node_id,
                "status": "NO_CHANGE",
                "checksum": body.checksum,
                "delta": None,
                "bytes_full": baseline_bytes,
                "bytes_sent": 0,
            }
            # What the same call would have weighed had it carried the state.
            would_be = dict(body.model_dump(exclude_none=True))
            would_be.pop("checksum", None)
            would_be["state"] = old_state
            log_event(
                node_id, "NO_CHANGE", baseline_bytes, 0,
                uplink_full=len(_wire_bytes(would_be)), uplink_sent=sent_bytes,
            )
            _remember_epoch(node_id, body.epoch)
            savjet = _advise_interval(node_id, "NO_CHANGE", body.interval)
            if savjet:
                result["polling"] = savjet
            return result

        # ---- classic call: the full state is here ----
        _check_state_limits(body.state)
        _check_free_tier()
        old_state = None if fresh_mirror else get_state(node_id)
        result = engine.evaluate(node_id, old_state, body.state)
        set_state(node_id, body.state)
        log_event(
            node_id, result["status"], result["bytes_full"], result["bytes_sent"],
            uplink_full=sent_bytes, uplink_sent=sent_bytes,
        )
        _remember_epoch(node_id, body.epoch)
        savjet = _advise_interval(node_id, result["status"], body.interval)
        if savjet:
            result["polling"] = savjet
        return result


class BatchSyncRequest(BaseModel):
    states: list[dict]
    epoch: Optional[str] = None
    interval: Optional[float] = None


MAX_BATCH = int(os.environ.get("DATAPULSE_MAX_BATCH", "500"))


@app.post("/api/nodes/{node_id}/sync/batch")
def sync_node_batch(node_id: str, body: BatchSyncRequest, _=Depends(_check_key)) -> dict:
    """Replay a buffer in one request."""
    if not _NODE_ID_RE.match(node_id):
        raise HTTPException(status_code=422,
                            detail="node_id must be 1-128 characters of A-Z a-z 0-9 . _ : -")
    if not body.states:
        raise HTTPException(status_code=422, detail="states is empty")
    if len(body.states) > MAX_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"{len(body.states)} reports in one batch, limit is {MAX_BATCH}; "
                   "raise DATAPULSE_MAX_BATCH or send fewer per request")

    for s in body.states:
        if _too_deep(s, MAX_STATE_DEPTH):
            raise HTTPException(status_code=422,
                                detail=f"a state is nested deeper than {MAX_STATE_DEPTH} levels")
        if len(_wire_bytes(s)) > MAX_STATE_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"a state exceeds {MAX_STATE_BYTES} bytes")

    if not _license_status()["licensed"]:
        if event_count() + len(body.states) > FREE_TIER_SYNC_LIMIT:
            raise HTTPException(
                status_code=402,
                detail=(f"Free evaluation limit reached ({FREE_TIER_SYNC_LIMIT} sync "
                        f"events). Buy a license and set DATAPULSE_LICENSE_KEY to "
                        f"continue: {PURCHASE_URL}"))

    if body.epoch is not None and not _EPOCH_RE.match(body.epoch):
        raise HTTPException(status_code=422,
                            detail="epoch must be 1-128 characters of A-Z a-z 0-9 . _ : -")

    with _node_lock(node_id):
        results = []
        state = None if _epoch_is_new(node_id, body.epoch) else get_state(node_id)
        for s in body.states:
            r = engine.evaluate(node_id, state, s)
            up = len(_wire_bytes(s))
            log_event(node_id, r["status"], r["bytes_full"], r["bytes_sent"],
                      uplink_full=up, uplink_sent=up)
            results.append({"status": r["status"], "bytes_full": r["bytes_full"],
                            "bytes_sent": r["bytes_sent"]})
            state = s
        set_state(node_id, state)
        _remember_epoch(node_id, body.epoch)

        sent = sum(r["bytes_sent"] for r in results)
        full = sum(r["bytes_full"] for r in results)
        last = results[-1]
        out = {
            "node_id": node_id,
            "accepted": len(results),
            "results": results,
            "bytes_full": full,
            "bytes_sent": sent,
            "saved_pct": round(100 * (1 - sent / full), 2) if full else 0.0,
            "checksum": _hash(state),
        }
        advice = _advise_interval(node_id, last["status"], body.interval)
        if advice:
            out["polling"] = advice
        return out


@app.get("/api/license")
def license_status() -> dict:
    return _license_status()


@app.get("/api/nodes")
def list_nodes(limit: int = 500, _=Depends(_check_key)) -> dict:
    """Known nodes and their baselines. Capped: a fleet can hold far more
    baselines than anyone wants in one JSON response."""
    limit = max(1, min(limit, 5000))
    ids = sorted(list_node_ids())
    page = ids[:limit]
    return {
        "nodes": [{"node_id": nid, "state": get_state(nid)} for nid in page],
        "returned": len(page),
        "total": len(ids),
        "truncated": len(ids) > len(page),
    }


@app.get("/api/metrics")
def get_metrics() -> dict:
    return {**real_metrics(), "storage": storage_mode()}


@app.get("/healthz")
def healthz() -> dict:
    reachable = ping()
    if reachable and not _degraded:
        mode = "redis"
    elif ALLOW_MEMORY_FALLBACK:
        mode = "memory (Redis unreachable)"
    else:
        mode = "unavailable (Redis unreachable, fallback disabled)"
    out = {"redis_reachable": reachable, "storage": mode}
    if ALERT_URL:
        out["alerts"] = {
            "enabled": True,
            "silent_after_seconds": ALERT_AFTER,
            "currently_silent": sorted(_silent),
            "sent": _alerts_sent,
        }
    return out


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return INDEX_HTML.replace("__TOTAL_EVENTS__", str(event_count())).replace("__BANDWIDTH_SAVED__", str(real_metrics().get("bandwidth_saved_pct", 0))).replace("__STORAGE__", storage_mode())


@app.get("/manifest.json")
def manifest() -> Response:
    return Response(content=MANIFEST_JSON, media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> Response:
    return Response(
        content=SW_JS.replace("__APP_VERSION__", app.version),
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/icon-192.png")
def icon_192() -> Response:
    return Response(content=ICON_192, media_type="image/png")


@app.get("/icon-512.png")
def icon_512() -> Response:
    return Response(content=ICON_512, media_type="image/png")


@app.get("/icon-512-maskable.png")
def icon_512_maskable() -> Response:
    return Response(content=ICON_512_MASKABLE, media_type="image/png")
