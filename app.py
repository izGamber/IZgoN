"""
IzgoN - single-file deploy build (v1.0.1 - hardened: timing-safe auth, WAL,
O(1) free-tier gate, bounded node ids).

Mechanically merged from engine.py, storage.py, metrics_db.py, licensing.py
and main.py in the canonical multi-file repo, with static assets inlined.
This exists only to work around a one-time GitHub upload limitation on
mobile - the canonical, human-editable source stays in the multi-file layout.
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


class DeltaSyncEngine:
    """Stateless per call: the caller supplies old_state (fetched from
    wherever it's persisted) and gets back a sync decision plus the real
    byte sizes involved, so callers can measure actual savings instead of
    asserting a number."""

    def evaluate(self, node_id: str, old_state: Optional[dict], new_state: dict) -> dict:
        new_hash = _hash(new_state)
        old_hash = _hash(old_state) if old_state is not None else None
        full_bytes = json.dumps(new_state).encode("utf-8")

        if old_hash == new_hash:
            return {
                "node_id": node_id,
                "status": "NO_CHANGE",
                "checksum": new_hash,
                "delta": None,
                "bytes_full": len(full_bytes),
                "bytes_sent": 0,
            }

        delta = compute_delta(old_state, new_state)
        delta_bytes = json.dumps(delta).encode("utf-8")
        return {
            "node_id": node_id,
            "status": "SYNC_REQUIRED",
            "checksum": new_hash,
            "delta": delta,
            "bytes_full": len(full_bytes),
            "bytes_sent": len(delta_bytes),
        }

# ============================== storage.py ==============================

REDIS_URL = os.environ.get("DATAPULSE_REDIS_URL", "redis://localhost:6379/0")
_PREFIX = "datapulse:state:"

_client = redis.from_url(REDIS_URL, decode_responses=True)


def _key(node_id: str) -> str:
    return f"{_PREFIX}{node_id}"


def get_state(node_id: str) -> Optional[dict]:
    raw = _client.get(_key(node_id))
    return json.loads(raw) if raw else None


def set_state(node_id: str, state: dict) -> None:
    _client.set(_key(node_id), json.dumps(state))


def list_node_ids() -> list[str]:
    return [k[len(_PREFIX):] for k in _client.scan_iter(match=f"{_PREFIX}*")]


def ping() -> bool:
    try:
        return bool(_client.ping())
    except redis.exceptions.ConnectionError:
        return False


def flush_all_state() -> None:
    """Test/dev helper only - clears all node state."""
    for k in _client.scan_iter(match=f"{_PREFIX}*"):
        _client.delete(k)

# ============================== metrics_db.py ==============================

DB_PATH = os.environ.get("DATAPULSE_DB_PATH", "datapulse_events.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    bytes_full INTEGER NOT NULL,
    bytes_sent INTEGER NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_events_node ON sync_events (node_id);
CREATE INDEX IF NOT EXISTS idx_sync_events_ts ON sync_events (ts);
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


def init_db(db_path: Optional[str] = None) -> None:
    global DB_PATH
    if db_path:
        DB_PATH = db_path
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
    _reset_event_count()


# The free-tier gate needs the number of logged events on every single sync.
# Reading it with COUNT(*) scans the whole table, so the server gets slower the
# longer it runs. Read it from SQLite once, then keep it in memory.
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
            with _conn() as conn:
                _event_count = conn.execute(
                    "SELECT COUNT(*) FROM sync_events"
                ).fetchone()[0]
        return _event_count


def log_event(node_id: str, status: str, bytes_full: int, bytes_sent: int) -> None:
    global _event_count
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sync_events (node_id, status, bytes_full, bytes_sent, ts) "
            "VALUES (?,?,?,?,?)",
            (node_id, status, bytes_full, bytes_sent, time.time()),
        )
        conn.commit()
    with _count_lock:
        if _event_count is not None:
            _event_count += 1


def real_metrics() -> dict:
    with _conn() as conn:
        total_events, total_full, total_sent, no_change = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(bytes_full),0), COALESCE(SUM(bytes_sent),0), "
            "SUM(CASE WHEN status='NO_CHANGE' THEN 1 ELSE 0 END) FROM sync_events"
        ).fetchone()
        nodes = conn.execute(
            "SELECT COUNT(DISTINCT node_id) FROM sync_events"
        ).fetchone()[0]

    if not total_events:
        return {
            "total_sync_events": 0,
            "active_nodes": 0,
            "no_change_events": 0,
            "bytes_full_if_naive": 0,
            "bytes_actually_sent": 0,
            "bandwidth_saved_pct": None,
            "note": "No sync events logged yet - this is real, not a placeholder. "
                    "Run traffic through POST /api/nodes/{id}/sync to generate metrics.",
        }

    saved_pct = round((1 - (total_sent / total_full)) * 100, 2) if total_full else 0.0
    return {
        "total_sync_events": total_events,
        "active_nodes": nodes,
        "no_change_events": no_change or 0,
        "bytes_full_if_naive": total_full,
        "bytes_actually_sent": total_sent,
        "bandwidth_saved_pct": saved_pct,
    }

# ============================== licensing.py ==============================

LICENSE_SECRET = os.environ.get("DATAPULSE_LICENSE_SECRET", "")
FREE_TIER_SYNC_LIMIT = int(os.environ.get("DATAPULSE_FREE_TIER_LIMIT", "10000"))


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:24]


def generate_license(customer_email: str, product: str = "izgon-selfhosted", secret: Optional[str] = None) -> str:
    """Sold to a paying customer. Requires DATAPULSE_LICENSE_SECRET to be set
    (the seller's private signing secret - never ship this to customers)."""
    secret = secret or LICENSE_SECRET
    if not secret:
        raise RuntimeError(
            "DATAPULSE_LICENSE_SECRET is not set. This is the seller's private "
            "signing key - generate one (e.g. `openssl rand -hex 32`) and keep "
            "it secret. Never include it in anything shipped to customers."
        )
    payload = {"email": customer_email, "product": product, "issued": int(time.time())}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    signature = _sign(payload_b64, secret)
    return f"DPC-{payload_b64}.{signature}"


def validate_license(key: Optional[str], secret: Optional[str] = None) -> Optional[dict]:
    """Returns the decoded payload if valid, None if missing/invalid/tampered.
    Never raises - a bad key should degrade to 'unlicensed', not crash the app."""
    secret = secret or LICENSE_SECRET
    if not key or not secret:
        return None
    try:
        if not key.startswith("DPC-") or "." not in key:
            return None
        body = key[len("DPC-"):]
        payload_b64, signature = body.rsplit(".", 1)
        expected = _sign(payload_b64, secret)
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode()).decode())
        return payload
    except Exception:
        return None

# ============================== inline static assets ==============================
INDEX_HTML = '<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n    <title>IzgoN // Dashboard</title>\n    <link rel="manifest" href="/manifest.json">\n    <link rel="icon" href="/icon-192.png">\n    <link rel="apple-touch-icon" href="/icon-192.png">\n    <meta name="theme-color" content="#05060b">\n    <link rel="preconnect" href="https://fonts.googleapis.com">\n    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">\n    <style>\n        :root {\n            --bg: #05060b;\n            --card-bg: rgba(18, 22, 38, 0.55);\n            --card-border: rgba(120, 170, 255, 0.18);\n            --cyan: #37e6ff;\n            --violet: #a78bfa;\n            --magenta: #ff5fd8;\n            --green: #34ffb0;\n            --amber: #ffb454;\n            --text-main: #eef4ff;\n            --text-muted: #8fa0c4;\n        }\n        * { box-sizing: border-box; margin: 0; padding: 0; }\n        html, body { height: 100%; }\n        body {\n            background: var(--bg);\n            color: var(--text-main);\n            font-family: \'Space Mono\', ui-monospace, monospace;\n            overflow-x: hidden;\n            position: relative;\n            min-height: 100vh;\n            padding: 28px 20px 60px;\n        }\n\n        /* ---- animated holographic orb backdrop, same spirit as the\n           pulsing gradient orb shown during voice mode ---- */\n        .orb-field {\n            position: fixed;\n            inset: 0;\n            z-index: -2;\n            overflow: hidden;\n            pointer-events: none;\n        }\n        .orb {\n            position: absolute;\n            width: 60vmax;\n            height: 60vmax;\n            border-radius: 50%;\n            filter: blur(80px);\n            opacity: 0.45;\n            mix-blend-mode: screen;\n            animation: drift 22s ease-in-out infinite alternate;\n        }\n        .orb.a { background: radial-gradient(circle, var(--cyan), transparent 65%); top: -20%; left: -15%; animation-duration: 26s; }\n        .orb.b { background: radial-gradient(circle, var(--violet), transparent 65%); bottom: -25%; right: -10%; animation-duration: 30s; animation-delay: -6s; }\n        .orb.c { background: radial-gradient(circle, var(--magenta), transparent 65%); top: 30%; right: 20%; animation-duration: 20s; animation-delay: -12s; opacity: 0.3; }\n        @keyframes drift {\n            0%   { transform: translate(0, 0) scale(1) rotate(0deg); }\n            50%  { transform: translate(6%, -4%) scale(1.12) rotate(8deg); }\n            100% { transform: translate(-5%, 5%) scale(0.95) rotate(-6deg); }\n        }\n        .grid-overlay {\n            position: fixed;\n            inset: 0;\n            z-index: -1;\n            background-image:\n                linear-gradient(rgba(120,170,255,0.05) 1px, transparent 1px),\n                linear-gradient(90deg, rgba(120,170,255,0.05) 1px, transparent 1px);\n            background-size: 42px 42px;\n            mask-image: radial-gradient(ellipse 80% 60% at 50% 0%, black 40%, transparent 100%);\n            pointer-events: none;\n        }\n\n        header { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 14px; margin-bottom: 28px; }\n        .logo-area h1 {\n            font-family: \'Orbitron\', sans-serif;\n            font-weight: 900;\n            font-size: 30px;\n            letter-spacing: 2px;\n            background: linear-gradient(100deg, var(--cyan), var(--violet) 45%, var(--magenta) 90%);\n            -webkit-background-clip: text;\n            background-clip: text;\n            color: transparent;\n            background-size: 200% auto;\n            animation: shimmer 6s linear infinite;\n            text-shadow: 0 0 40px rgba(55, 230, 255, 0.25);\n        }\n        @keyframes shimmer { to { background-position: 200% center; } }\n        .logo-area p { font-size: 11.5px; color: var(--text-muted); margin-top: 6px; max-width: 420px; line-height: 1.5; }\n\n        .system-status {\n            display: flex; align-items: center; gap: 10px;\n            padding: 9px 18px; border-radius: 30px; font-size: 11px; font-weight: 700;\n            letter-spacing: 1px;\n            backdrop-filter: blur(10px);\n            border: 1px solid var(--card-border);\n        }\n        .system-status.live { background: rgba(52, 255, 176, 0.08); border-color: var(--green); color: var(--green); box-shadow: 0 0 24px rgba(52,255,176,0.25); }\n        .system-status.no-data { background: rgba(143, 160, 196, 0.08); border-color: var(--text-muted); color: var(--text-muted); }\n        .system-status.down { background: rgba(255, 95, 95, 0.08); border-color: #ff5f5f; color: #ff5f5f; }\n        .pulse-dot { width: 8px; height: 8px; background: currentColor; border-radius: 50%; box-shadow: 0 0 12px currentColor; animation: pulse 1.6s ease-in-out infinite; }\n        @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.7); } }\n\n        .buy-btn {\n            display: inline-flex; align-items: center; gap: 8px;\n            padding: 12px 26px;\n            border-radius: 14px;\n            font-family: \'Orbitron\', sans-serif;\n            font-weight: 700;\n            font-size: 13px;\n            letter-spacing: 1px;\n            text-decoration: none;\n            color: #05060b;\n            background: linear-gradient(100deg, var(--cyan), var(--violet), var(--magenta));\n            background-size: 200% auto;\n            box-shadow: 0 0 30px rgba(167, 139, 250, 0.45);\n            transition: transform 0.2s ease, box-shadow 0.2s ease;\n            animation: shimmer 5s linear infinite;\n        }\n        .buy-btn:active { transform: scale(0.97); }\n        .buy-note { font-size: 10.5px; color: var(--text-muted); margin-top: 8px; max-width: 280px; }\n\n        .note-box {\n            background: rgba(255, 180, 84, 0.06);\n            border: 1px solid var(--amber);\n            color: var(--amber);\n            padding: 14px 18px;\n            border-radius: 12px;\n            font-size: 12.5px;\n            margin-bottom: 25px;\n            backdrop-filter: blur(10px);\n        }\n\n        .grid-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 18px; margin-bottom: 26px; }\n        .metric-card {\n            position: relative;\n            background: var(--card-bg);\n            border: 1px solid var(--card-border);\n            border-radius: 16px;\n            padding: 22px;\n            backdrop-filter: blur(16px);\n            overflow: hidden;\n        }\n        .metric-card::before {\n            content: \'\';\n            position: absolute; inset: -1px;\n            border-radius: 16px;\n            padding: 1px;\n            background: conic-gradient(from var(--angle, 0deg), var(--cyan), var(--violet), var(--magenta), var(--cyan));\n            -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);\n            -webkit-mask-composite: xor;\n            mask-composite: exclude;\n            opacity: 0.55;\n            animation: spin 6s linear infinite;\n        }\n        @keyframes spin { to { --angle: 360deg; } }\n        @property --angle { syntax: \'<angle>\'; initial-value: 0deg; inherits: false; }\n        .metric-title { font-size: 10.5px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1.5px; }\n        .metric-value { font-family: \'Orbitron\', sans-serif; font-size: 30px; font-weight: 700; margin-top: 10px; color: #fff; text-shadow: 0 0 20px rgba(55,230,255,0.2); }\n        .metric-sub { font-size: 10.5px; color: var(--cyan); margin-top: 6px; opacity: 0.85; }\n\n        table { width: 100%; border-collapse: collapse; background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 16px; overflow: hidden; backdrop-filter: blur(16px); }\n        th, td { text-align: left; padding: 13px 16px; font-size: 12px; border-bottom: 1px solid var(--card-border); }\n        th { color: var(--text-muted); text-transform: uppercase; font-size: 10px; letter-spacing: 1px; }\n        td { color: var(--text-main); }\n        .section-title { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1.5px; margin: 28px 0 12px; }\n\n        details.about {\n            margin-top: 30px;\n            background: var(--card-bg);\n            border: 1px solid var(--card-border);\n            border-radius: 14px;\n            padding: 16px 20px;\n            backdrop-filter: blur(16px);\n            font-size: 12.5px;\n            line-height: 1.6;\n            color: var(--text-muted);\n        }\n        details.about summary { cursor: pointer; color: var(--text-main); font-weight: 700; letter-spacing: 0.5px; }\n        details.about ul { margin: 10px 0 0 18px; }\n\n        footer { margin-top: 30px; font-size: 10.5px; color: var(--text-muted); text-align: center; opacity: 0.7; }\n    </style>\n</head>\n<body>\n    <div class="orb-field">\n        <div class="orb a"></div>\n        <div class="orb b"></div>\n        <div class="orb c"></div>\n    </div>\n    <div class="grid-overlay"></div>\n\n    <header>\n        <div class="logo-area">\n            <h1>IzgoN</h1>\n            <p>Delta-sync engine &mdash; source-available, self-hosted. Every figure below is read live from /api/metrics.</p>\n        </div>\n        <div style="display:flex; flex-direction:column; align-items:flex-end; gap:10px;">\n            <div id="statusPill" class="system-status no-data">\n                <div class="pulse-dot"></div>\n                <span id="statusText">CHECKING&hellip;</span>\n            </div>\n            <a class="buy-btn" href="__PURCHASE_URL__" target="_blank" rel="noopener noreferrer">Buy a licence &mdash; $29</a>\n        </div>\n    </header>\n\n    <div id="noDataNote" class="note-box" style="display:none;">\n        This instance has had no traffic yet, so the counters below read zero. Measured results at three change rates are published in BENCHMARK.md &mdash; 93.9% saved at a 5% change rate, and 27.7% at 70%, where this stops being worth running. These numbers are real, not placeholders &mdash; they will populate once traffic\n        goes through <code>POST /api/nodes/{id}/sync</code>. Run <code>python benchmark.py</code> to generate a real sample.\n    </div>\n\n    <div class="grid-metrics">\n        <div class="metric-card">\n            <div class="metric-title">Bandwidth Saved</div>\n            <div class="metric-value" id="mSaved">&mdash;</div>\n            <div class="metric-sub">vs. sending full state every time</div>\n        </div>\n        <div class="metric-card">\n            <div class="metric-title">Sync Events Logged</div>\n            <div class="metric-value" id="mEvents">&mdash;</div>\n            <div class="metric-sub" id="mNoChange">&mdash;</div>\n        </div>\n        <div class="metric-card">\n            <div class="metric-title">Active Nodes</div>\n            <div class="metric-value" id="mNodes">&mdash;</div>\n            <div class="metric-sub">distinct node_id values seen</div>\n        </div>\n        <div class="metric-card">\n            <div class="metric-title">Bytes: Naive vs. Actual</div>\n            <div class="metric-value" id="mBytes">&mdash;</div>\n            <div class="metric-sub">total bytes, measured</div>\n        </div>\n    </div>\n\n    <div class="section-title">Known Nodes (requires API key &mdash; open with ?key= to load)</div>\n    <table>\n        <thead><tr><th>Node ID</th><th>Last known state</th></tr></thead>\n        <tbody id="nodesBody"><tr><td colspan="2" style="color:var(--text-muted)">Set API key via ?key= query param to load.</td></tr></tbody>\n    </table>\n\n    <details class="about">\n        <summary>O projektu / What is IzgoN?</summary>\n        <p style="margin-top:8px;">IzgoN sends only what changed instead of the full state every sync &mdash; a hash-based delta-sync engine you self-host, built for fleets that report state often over metered links &mdash; IoT and sensor devices on cellular SIMs, edge agents on constrained connections, monitoring agents polling every few seconds &mdash; where most fields stay identical between reports.</p>\n        <ul>\n            <li>Real FastAPI backend, Redis-backed state, SQLite event log &mdash; nothing here is simulated.</li>\n            <li>Offline license verification &mdash; no phone-home server required after purchase.</li>\n            <li>Source-available licence &mdash; read, run and modify it yourself. Free up to 10,000 syncs; a one-time licence beyond that. Not open source: see LICENSE.md.</li>\n        </ul>\n    </details>\n\n    <footer>IzgoN &mdash; source-available delta-sync utility. See LICENSE.md.</footer>\n\n    <script>\n        const params = new URLSearchParams(window.location.search);\n        const apiKey = params.get(\'key\') || \'\';\n        // Keep the key out of the address bar. A key left in the URL ends up in\n        // browser history, in any proxy log on the way, and in the Referer header\n        // of every outbound request from this page. Read it once, then scrub it.\n        if (apiKey) {\n            try { history.replaceState(null, \'\', window.location.pathname); } catch (e) {}\n        }\n\n        async function refreshMetrics() {\n            try {\n                const res = await fetch(\'/api/metrics\');\n                const m = await res.json();\n                const pill = document.getElementById(\'statusPill\');\n                const text = document.getElementById(\'statusText\');\n                const note = document.getElementById(\'noDataNote\');\n\n                if (m.total_sync_events === 0) {\n                    pill.className = \'system-status no-data\';\n                    text.textContent = \'NO DATA YET\';\n                    note.style.display = \'block\';\n                    document.getElementById(\'mSaved\').textContent = \'—\';\n                    document.getElementById(\'mEvents\').textContent = \'0\';\n                    document.getElementById(\'mNoChange\').textContent = \'no NO_CHANGE events yet\';\n                    document.getElementById(\'mNodes\').textContent = \'0\';\n                    document.getElementById(\'mBytes\').textContent = \'—\';\n                    return;\n                }\n\n                pill.className = \'system-status live\';\n                text.textContent = \'LIVE — REAL DATA\';\n                note.style.display = \'none\';\n                document.getElementById(\'mSaved\').textContent = m.bandwidth_saved_pct + \'%\';\n                document.getElementById(\'mEvents\').textContent = m.total_sync_events;\n                document.getElementById(\'mNoChange\').textContent = m.no_change_events + \' were NO_CHANGE (0 bytes)\';\n                document.getElementById(\'mNodes\').textContent = m.active_nodes;\n                document.getElementById(\'mBytes\').textContent =\n                    m.bytes_actually_sent + \' / \' + m.bytes_full_if_naive + \' B\';\n            } catch (e) {\n                const pill = document.getElementById(\'statusPill\');\n                pill.className = \'system-status down\';\n                document.getElementById(\'statusText\').textContent = \'API UNREACHABLE\';\n            }\n        }\n\n        async function refreshNodes() {\n            if (!apiKey) return;\n            try {\n                const res = await fetch(\'/api/nodes\', { headers: { \'X-API-Key\': apiKey } });\n                if (!res.ok) throw new Error(\'unauthorized\');\n                const data = await res.json();\n                const body = document.getElementById(\'nodesBody\');\n                body.innerHTML = \'\';\n                if (data.nodes.length === 0) {\n                    body.innerHTML = \'<tr><td colspan="2" style="color:var(--text-muted)">No nodes yet.</td></tr>\';\n                }\n                for (const n of data.nodes) {\n                    const tr = document.createElement(\'tr\');\n                    tr.innerHTML = `<td>${n.node_id}</td><td><code>${JSON.stringify(n.state)}</code></td>`;\n                    body.appendChild(tr);\n                }\n            } catch (e) {\n                document.getElementById(\'nodesBody\').innerHTML =\n                    \'<tr><td colspan="2" style="color:var(--text-muted)">Invalid/missing API key.</td></tr>\';\n            }\n        }\n\n        refreshMetrics();\n        refreshNodes();\n        setInterval(refreshMetrics, 3000);\n        setInterval(refreshNodes, 5000);\n\n        if (\'serviceWorker\' in navigator) {\n            window.addEventListener(\'load\', () => {\n                navigator.serviceWorker.register(\'/sw.js\').catch(() => {});\n            });\n        }\n    </script>\n</body>\n</html>\n'
MANIFEST_JSON = '{\n    "id": "/",\n    "name": "IzgoN Dashboard",\n    "short_name": "IzgoN",\n    "description": "Live dashboard for a self-hosted delta-sync service - real metrics only, no placeholders.",\n    "start_url": "/?source=pwa",\n    "scope": "/",\n    "display": "standalone",\n    "orientation": "portrait",\n    "background_color": "#05060b",\n    "theme_color": "#05060b",\n    "categories": ["utilities", "developer"],\n    "icons": [\n        { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },\n        { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" },\n        { "src": "/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }\n    ]\n}'
SW_JS = '// DataPulse Core - service worker.\n// Caches only the static app shell so the dashboard installs and opens\n// offline. API calls (/api/*) are always fetched fresh from the network -\n// caching real metrics would risk showing stale numbers as if they were\n// live, which is exactly the kind of misleading behavior this project is\n// trying to get away from.\nconst CACHE_NAME = "datapulse-shell-v1";\nconst SHELL_FILES = [\n  "/",\n  "/manifest.json",\n  "/icon-192.png",\n  "/icon-512.png",\n];\n\nself.addEventListener("install", (event) => {\n  event.waitUntil(\n    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))\n  );\n  self.skipWaiting();\n});\n\nself.addEventListener("activate", (event) => {\n  event.waitUntil(\n    caches.keys().then((keys) =>\n      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))\n    )\n  );\n  self.clients.claim();\n});\n\nself.addEventListener("fetch", (event) => {\n  const url = new URL(event.request.url);\n\n  // Never cache API responses - always real, always fresh.\n  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") {\n    return;\n  }\n\n  event.respondWith(\n    caches.match(event.request).then((cached) => cached || fetch(event.request))\n  );\n});\n'
ICON_192 = base64.b64decode('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')
ICON_512 = base64.b64decode('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')
ICON_512_MASKABLE = base64.b64decode('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')

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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="IzgoN", version="1.0.1", lifespan=lifespan)
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
    state: dict


# Node ids become Redis keys and land in the event log. Without a bound, one
# caller can grow memory without limit and fill the log with junk.
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@app.post("/api/nodes/{node_id}/sync")
def sync_node(node_id: str, body: SyncRequest, _=Depends(_check_key)) -> dict:
    if not _NODE_ID_RE.match(node_id):
        raise HTTPException(
            status_code=422,
            detail=(
                "node_id must be 1-128 characters of A-Z a-z 0-9 . _ : -"
            ),
        )
    if not _license_status()["licensed"]:
        # event_count() is O(1); real_metrics() would scan the table on every
        # single sync and get slower as the log grows.
        if event_count() >= FREE_TIER_SYNC_LIMIT:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Free evaluation limit reached ({FREE_TIER_SYNC_LIMIT} sync "
                    f"events). Buy a license and set DATAPULSE_LICENSE_KEY to "
                    f"continue: {PURCHASE_URL}"
                ),
            )
    old_state = get_state(node_id)
    result = engine.evaluate(node_id, old_state, body.state)
    set_state(node_id, body.state)
    log_event(node_id, result["status"], result["bytes_full"], result["bytes_sent"])
    return result


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
    return real_metrics()


@app.get("/healthz")
def healthz() -> dict:
    return {"redis_reachable": ping()}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return INDEX_HTML.replace("__PURCHASE_URL__", PURCHASE_URL)


@app.get("/manifest.json")
def manifest() -> Response:
    return Response(content=MANIFEST_JSON, media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> Response:
    return Response(content=SW_JS, media_type="application/javascript")


@app.get("/icon-192.png")
def icon_192() -> Response:
    return Response(content=ICON_192, media_type="image/png")


@app.get("/icon-512.png")
def icon_512() -> Response:
    return Response(content=ICON_512, media_type="image/png")


@app.get("/icon-512-maskable.png")
def icon_512_maskable() -> Response:
    return Response(content=ICON_512_MASKABLE, media_type="image/png")
