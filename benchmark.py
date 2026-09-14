#!/usr/bin/env python3
"""
IzgoN benchmark — measures how many bytes delta-sync actually saves.

Standard library only. No install step.

Two ways to run it.

1. On your own data — the only number worth trusting:

       python3 benchmark.py --payload-file my-reports.json

   The file is a JSON array, or JSON Lines (one JSON object per line), of
   reports your devices actually sent. Each device replays its own real
   sequence in its real order, and the change rate is measured from the
   data rather than assumed. --change-rate is ignored in this mode.

2. On synthetic states, when you have no sample yet:

       python3 benchmark.py --nodes 50 --rounds 200 --change-rate 0.10

   Point --change-rate at what your fleet does. If most reports are
   identical to the previous one, use a low value; if every report differs,
   use a high one. This tells you the shape of the curve, not your number.
"""

import argparse
import json
import os
import pathlib
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

FIELDS = [
    ("temp_c", lambda r: round(r.uniform(-10, 45), 2)),
    ("humidity", lambda r: round(r.uniform(20, 95), 1)),
    ("battery", lambda r: round(r.uniform(0, 100), 1)),
    ("rssi", lambda r: r.randint(-110, -50)),
    ("uptime_s", lambda r: r.randint(0, 5_000_000)),
    ("fw_version", lambda r: r.choice(["1.4.2", "1.4.3", "1.5.0"])),
    ("mode", lambda r: r.choice(["idle", "active", "sleep", "fault"])),
    ("lat", lambda r: round(r.uniform(42.5, 45.3), 6)),
    ("lon", lambda r: round(r.uniform(15.7, 19.6), 6)),
    ("errors", lambda r: r.randint(0, 3)),
]


def make_state(rng):
    """A plausible device report, used when you have no sample of your own."""
    return {name: gen(rng) for name, gen in FIELDS}


def mutate(state, rng, change_rate):
    """Change each field with probability change_rate. Returns a new dict."""
    new = dict(state)
    for name, gen in FIELDS:
        if rng.random() < change_rate:
            new[name] = gen(rng)
    return new


# --------------------------------------------------------------------------
# Your own payloads
# --------------------------------------------------------------------------

_ID_FIELDS = ("node_id", "device_id", "deviceId", "device", "id", "serial")

# The server accepts these characters in a node id and rejects the rest.
_ALLOWED_ID = re.compile(r"[^A-Za-z0-9._:-]")


def slugify_id(value):
    """Make a device id the server will accept, without losing which is which."""
    slug = _ALLOWED_ID.sub("_", str(value)).strip("_")
    return slug[:80] or "device"


def load_payloads(path, id_field=None, state_field=None):
    """Read your own reports and group them per device.

    Accepts a JSON array of objects, or JSON Lines (one object per line).
    Records are grouped by the first id-looking field found, so each device
    replays its own real sequence in its real order. That is the only way the
    resulting number means anything: with real data the change rate is
    whatever your devices actually do, not a number anyone picked.

    Returns (grouped, id_field, renamed) where renamed maps a device id that
    had to be rewritten for the server to the original.
    """
    raw = pathlib.Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"{path} is empty")

    records = None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            records = [parsed]
        else:
            raise ValueError(
                f"{path}: expected a JSON array of objects, got {type(parsed).__name__}"
            )
    except json.JSONDecodeError:
        records = []
        for lineno, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path} line {lineno}: not valid JSON, and the file as a "
                    f"whole is not a JSON array either — {exc}"
                ) from None

    if not records:
        raise ValueError(f"{path} contains no records")
    if not all(isinstance(r, dict) for r in records):
        raise ValueError(f"{path}: every record must be a JSON object")

    # Work out the device id BEFORE unwrapping, because in wrapped shapes like
    # {"device_id": "a", "state": {...}} the id lives outside the payload.
    if id_field is None:
        for cand in _ID_FIELDS:
            if all(cand in r for r in records):
                id_field = cand
                break
    elif not all(id_field in r for r in records):
        raise ValueError(f"{path}: --id-field {id_field!r} missing from a record")

    ids = [str(r[id_field]) for r in records] if id_field else None

    # Optional unwrap: {"state": {...}} or any wrapper key you name.
    if state_field:
        try:
            states = [r[state_field] for r in records]
        except KeyError:
            raise ValueError(
                f"{path}: --state-field {state_field!r} missing from a record"
            ) from None
        if not all(isinstance(s, dict) for s in states):
            raise ValueError(f"{path}: {state_field!r} must hold an object")
    else:
        # Strip the id out of the payload; it is routing, not state.
        states = [
            {k: v for k, v in r.items() if k != id_field} if id_field else dict(r)
            for r in records
        ]

    grouped = {}
    renamed = {}
    if ids is not None:
        slugs = {}
        taken = set()
        for original, state in zip(ids, states):
            slug = slugs.get(original)
            if slug is None:
                slug = slugify_id(original)
                # Two different originals must not collapse into one node.
                if slug in taken:
                    n = 2
                    while f"{slug}-{n}" in taken:
                        n += 1
                    slug = f"{slug}-{n}"
                taken.add(slug)
                if slug != original:
                    renamed[slug] = original
                slugs[original] = slug
            grouped.setdefault(slug, []).append(state)
    else:
        # No id field: treat the file as one device's history. Still honest,
        # just a fleet of one.
        grouped["your-device"] = states

    empty = [nid for nid, seq in grouped.items() if not any(seq)]
    if len(empty) == len(grouped):
        raise ValueError(f"{path}: every payload is empty after parsing")

    return grouped, id_field, renamed


def observed_change_rate(sequences):
    """How often a field actually differs from the previous report. Measured
    from your data, not assumed. Comparable to --change-rate."""
    changed = total = 0
    for seq in sequences.values():
        for prev, cur in zip(seq, seq[1:]):
            keys = set(prev) | set(cur)
            if not keys:
                continue
            total += len(keys)
            changed += sum(1 for k in keys if prev.get(k) != cur.get(k))
    return (changed / total) if total else 0.0


# --------------------------------------------------------------------------
# Plans — what gets sent, in what order
# --------------------------------------------------------------------------

def synthetic_plan(args, rng):
    """Generated states. Yields (node_id, state) lazily so large runs don't
    hold every payload in memory."""
    states = {
        f"{args.node_prefix}-node-{i:04d}": make_state(rng)
        for i in range(args.nodes)
    }
    for rnd in range(args.rounds):
        for node_id in list(states):
            if rnd > 0:
                states[node_id] = mutate(states[node_id], rng, args.change_rate)
            yield node_id, states[node_id]


def replay_plan(grouped, prefix):
    """Your reports, interleaved across devices the way a fleet actually
    reports — but each device keeps its own order."""
    seqs = list(grouped.items())
    longest = max(len(seq) for _, seq in seqs)
    for i in range(longest):
        for nid, seq in seqs:
            if i < len(seq):
                yield f"{prefix}{nid}", seq[i]


def post(url, payload, timeout, api_key):
    body = json.dumps({"state": payload}, separators=(",", ":")).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return raw, elapsed_ms


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n):,} B"
        n /= 1024


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8000", help="IzgoN base URL")
    p.add_argument("--payload-file", default=None, metavar="PATH",
                   help="JSON array or JSON Lines file of YOUR OWN reports. "
                        "Devices replay their real sequences and the change rate "
                        "is measured, not assumed. This is the number worth trusting.")
    p.add_argument("--id-field", default=None,
                   help="field naming the device; auto-detected from: "
                        + ", ".join(_ID_FIELDS))
    p.add_argument("--state-field", default=None,
                   help="if each record wraps the payload, e.g. --state-field state")
    p.add_argument("--nodes", type=int, default=50,
                   help="how many distinct nodes (synthetic mode only)")
    p.add_argument("--rounds", type=int, default=200,
                   help="reports per node (synthetic mode only)")
    p.add_argument("--change-rate", type=float, default=0.10,
                   help="probability that any single field changes between reports "
                        "(0.0-1.0; synthetic mode only)")
    p.add_argument("--seed", type=int, default=1, help="RNG seed, so runs are reproducible")
    p.add_argument("--node-prefix", default="bench",
                   help="prefix for the node ids this benchmark creates")
    p.add_argument("--api-key", default=os.environ.get("DATAPULSE_API_KEY", "dev-local-key"),
                   help="X-API-Key header; defaults to $DATAPULSE_API_KEY")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--price-per-mb", type=float, default=0.0,
                   help="your data cost per MB, to print a money figure (e.g. 0.05)")
    p.add_argument("--currency", default="USD")
    args = p.parse_args()

    if not 0.0 <= args.change_rate <= 1.0:
        p.error("--change-rate must be between 0.0 and 1.0")
    if not _ALLOWED_ID.sub("", args.node_prefix) == args.node_prefix:
        p.error("--node-prefix may contain only letters, digits and . _ : -")

    rng = random.Random(args.seed)
    base = args.url.rstrip("/")

    # Read the payload file before touching the network: a typo in the path
    # should not cost you a server round trip or a half-finished run.
    grouped = renamed = None
    replay_prefix = ""
    if args.payload_file:
        try:
            grouped, id_field, renamed = load_payloads(
                args.payload_file, args.id_field, args.state_field
            )
        except (OSError, ValueError) as exc:
            print(f"Could not read {args.payload_file} — {exc}", file=sys.stderr)
            return 7
        # Namespace this run so every device starts from a clean baseline.
        # Without it a second run would compare its first report against the
        # first run's last one and report a delta instead of a full send —
        # which quietly inflates the saving. Deliberately NOT from the seeded
        # RNG: --seed must not make two runs share a namespace.
        replay_prefix = f"{args.node_prefix}-{os.urandom(4).hex()}-"

    # Fail fast with a clear message rather than a stack trace.
    try:
        with urllib.request.urlopen(f"{base}/healthz", timeout=args.timeout) as r:
            r.read()
    except Exception as exc:
        print(f"Cannot reach IzgoN at {base} — {exc}", file=sys.stderr)
        print("Start it first:  docker compose up -d", file=sys.stderr)
        return 2

    if grouped is not None:
        records = sum(len(seq) for seq in grouped.values())
        rate = observed_change_rate(grouped)
        singles = sum(1 for seq in grouped.values() if len(seq) < 2)
        plan = replay_plan(grouped, replay_prefix)
        total_calls = records

        print(f"IzgoN benchmark — replaying {records:,} of your own reports "
              f"from {len(grouped):,} device(s)")
        print(f"Source: {args.payload_file}"
              + (f"  (device id: {id_field})" if id_field else
                 "  (no device id field found — treated as one device)"))
        print(f"Observed change rate: {rate:.1%}  — measured from your data")
        if args.change_rate != p.get_default("change_rate"):
            print("--change-rate is ignored with --payload-file: your data "
                  "already says what the rate is.")
        if renamed:
            shown = ", ".join(f"{v} → {k}" for k, v in list(renamed.items())[:3])
            more = f" (+{len(renamed) - 3} more)" if len(renamed) > 3 else ""
            print(f"Renamed {len(renamed)} device id(s) to fit the server's "
                  f"id rules: {shown}{more}")
        if singles:
            print(f"Warning: {singles} device(s) have a single report. A first "
                  "report is always sent in full, so those contribute 0% saving "
                  "— which is correct, not a bug.")
        print(f"Target: {base}\n")
    else:
        plan = synthetic_plan(args, rng)
        total_calls = args.nodes * args.rounds
        print(f"IzgoN benchmark — {args.nodes} nodes x {args.rounds} rounds "
              f"= {total_calls:,} syncs, change rate {args.change_rate:.0%}")
        print("These are generated states. For your real number, rerun with "
              "--payload-file pointing at a sample of your own reports.")
        print(f"Target: {base}\n")

    naive_bytes = 0      # what you would send with no delta sync
    actual_bytes = 0     # what came back over the wire
    no_change = 0
    changed = 0
    full_state = 0
    latencies = []

    started = time.perf_counter()
    done = 0

    for node_id, state in plan:
        payload_bytes = len(json.dumps(state, separators=(",", ":")).encode())
        naive_bytes += payload_bytes

        try:
            raw, ms = post(f"{base}/api/nodes/{node_id}/sync", state,
                           args.timeout, args.api_key)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                print("\nHTTP 401 — wrong or missing API key.")
                print("Pass --api-key or export DATAPULSE_API_KEY to match the server.",
                      file=sys.stderr)
                return 6
            if exc.code == 402:
                print("\nFree tier limit reached (HTTP 402).")
                print("Raise DATAPULSE_FREE_TIER_LIMIT or set a licence key, then rerun.",
                      file=sys.stderr)
                return 3
            if exc.code == 422:
                print(f"\nHTTP 422 on {node_id}: the server rejected this "
                      "request. If your payload came from --payload-file, check "
                      "that each record is a flat JSON object; use --state-field "
                      "if it is wrapped.", file=sys.stderr)
                return 4
            print(f"\nHTTP {exc.code} on {node_id}: {exc.read()[:200]!r}", file=sys.stderr)
            return 4
        except Exception as exc:
            print(f"\nRequest failed on {node_id}: {exc}", file=sys.stderr)
            return 5

        latencies.append(ms)

        # Prefer the server's own accounting; fall back to what we measured.
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {}

        sent = parsed.get("bytes_sent")
        if sent is None:
            delta = parsed.get("delta")
            sent = 0 if delta in (None, {}) else len(
                json.dumps(delta, separators=(",", ":")).encode()
            )
        actual_bytes += sent

        status = parsed.get("status")
        if status == "NO_CHANGE" or sent == 0:
            no_change += 1
        else:
            changed += 1
            if status == "FULL_STATE":
                full_state += 1

        done += 1
        if done % 500 == 0 or done == total_calls:
            pct = done / total_calls * 100 if total_calls else 100.0
            print(f"\r  {done:,}/{total_calls:,} ({pct:.0f}%)", end="", flush=True)

    wall = time.perf_counter() - started
    saved = naive_bytes - actual_bytes
    pct_saved = (saved / naive_bytes * 100) if naive_bytes else 0.0

    print("\n")
    print("=" * 58)
    print(f"{'Syncs':<28}{done:>28,}")
    print(f"{'  unchanged (0 bytes back)':<28}{no_change:>28,}")
    print(f"{'  changed (delta sent)':<28}{changed - full_state:>28,}")
    print(f"{'  sent whole (delta bigger)':<28}{full_state:>28,}")
    print("-" * 58)
    print(f"{'Without IzgoN':<28}{human(naive_bytes):>28}")
    print(f"{'With IzgoN':<28}{human(actual_bytes):>28}")
    print(f"{'Saved':<28}{human(saved) + f'  ({pct_saved:.1f}%)':>28}")
    print("-" * 58)
    print(f"{'Wall time':<28}{f'{wall:.1f} s':>28}")
    if wall > 0:
        print(f"{'Throughput':<28}{f'{done / wall:,.0f} syncs/s':>28}")
    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"{'Latency p50 / p95':<28}{f'{p50:.1f} / {p95:.1f} ms':>28}")
    if args.price_per_mb > 0:
        money = saved / (1024 * 1024) * args.price_per_mb
        # Below a cent, two decimals would print 0.00 and look broken. Show the
        # real figure instead — a small sample really does save a small amount.
        shown = f"{money:,.2f}" if money >= 0.01 else f"{money:,.5f}"
        print("-" * 58)
        print(f"{'Cost avoided at this volume':<28}"
              f"{f'{shown} {args.currency}':>28}")
    print("=" * 58)

    if grouped is not None:
        rate = observed_change_rate(grouped)
        print(f"\nThis is your data: {total_calls:,} real reports from "
              f"{len(grouped):,} device(s), change rate {rate:.1%} as measured.")
        if pct_saved < 20:
            print("At this saving IzgoN is not worth running on this workload. "
                  "That is a real answer — don't buy it.")
        elif pct_saved < 50:
            print("A moderate saving. Worth it only if your bandwidth is "
                  "genuinely expensive — do the arithmetic with --price-per-mb.")
    else:
        print("\nThis measured synthetic states. For a number you can trust, rerun as:")
        print("  python3 benchmark.py --payload-file your-reports.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
