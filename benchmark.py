#!/usr/bin/env python3
"""
IzgoN benchmark — measures how many bytes delta-sync actually saves.

Standard library only. No install step.

    python3 benchmark.py --nodes 50 --rounds 200 --change-rate 0.10

Point --change-rate at what your real fleet does. If most reports are
identical to the previous one, use a low value; if every report differs,
use a high one. The honest test is to replace make_state() below with a
sample of your own payloads.
"""

import argparse
import json
import os
import random
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
    """A plausible device report. Replace this with your own payload shape."""
    return {name: gen(rng) for name, gen in FIELDS}


def mutate(state, rng, change_rate):
    """Change each field with probability change_rate. Returns a new dict."""
    new = dict(state)
    for name, gen in FIELDS:
        if rng.random() < change_rate:
            new[name] = gen(rng)
    return new


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
    p.add_argument("--nodes", type=int, default=50, help="how many distinct nodes")
    p.add_argument("--rounds", type=int, default=200, help="reports per node")
    p.add_argument("--change-rate", type=float, default=0.10,
                   help="probability that any single field changes between reports (0.0-1.0)")
    p.add_argument("--seed", type=int, default=1, help="RNG seed, so runs are reproducible")
    p.add_argument("--api-key", default=os.environ.get("DATAPULSE_API_KEY", "dev-local-key"),
                   help="X-API-Key header; defaults to $DATAPULSE_API_KEY")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--price-per-mb", type=float, default=0.0,
                   help="your data cost per MB, to print a money figure (e.g. 0.05)")
    p.add_argument("--currency", default="USD")
    args = p.parse_args()

    if not 0.0 <= args.change_rate <= 1.0:
        p.error("--change-rate must be between 0.0 and 1.0")

    rng = random.Random(args.seed)
    base = args.url.rstrip("/")

    # Fail fast with a clear message rather than a stack trace.
    try:
        with urllib.request.urlopen(f"{base}/healthz", timeout=args.timeout) as r:
            r.read()
    except Exception as exc:
        print(f"Cannot reach IzgoN at {base} — {exc}", file=sys.stderr)
        print("Start it first:  docker compose up -d", file=sys.stderr)
        return 2

    states = {f"bench-node-{i:04d}": make_state(rng) for i in range(args.nodes)}

    naive_bytes = 0      # what you would send with no delta sync
    actual_bytes = 0     # what came back over the wire
    no_change = 0
    changed = 0
    latencies = []
    total_calls = args.nodes * args.rounds

    print(f"IzgoN benchmark — {args.nodes} nodes x {args.rounds} rounds "
          f"= {total_calls:,} syncs, change rate {args.change_rate:.0%}")
    print(f"Target: {base}\n")

    started = time.perf_counter()
    done = 0

    for rnd in range(args.rounds):
        for node_id, state in states.items():
            if rnd > 0:
                state = mutate(state, rng, args.change_rate)
                states[node_id] = state

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

            if parsed.get("status") == "NO_CHANGE" or sent == 0:
                no_change += 1
            else:
                changed += 1

            done += 1
            if done % 500 == 0 or done == total_calls:
                pct = done / total_calls * 100
                print(f"\r  {done:,}/{total_calls:,} ({pct:.0f}%)", end="", flush=True)

    wall = time.perf_counter() - started
    saved = naive_bytes - actual_bytes
    pct_saved = (saved / naive_bytes * 100) if naive_bytes else 0.0

    print("\n")
    print("=" * 58)
    print(f"{'Syncs':<28}{total_calls:>28,}")
    print(f"{'  unchanged (0 bytes back)':<28}{no_change:>28,}")
    print(f"{'  changed (delta sent)':<28}{changed:>28,}")
    print("-" * 58)
    print(f"{'Without IzgoN':<28}{human(naive_bytes):>28}")
    print(f"{'With IzgoN':<28}{human(actual_bytes):>28}")
    print(f"{'Saved':<28}{human(saved) + f'  ({pct_saved:.1f}%)':>28}")
    print("-" * 58)
    print(f"{'Wall time':<28}{f'{wall:.1f} s':>28}")
    print(f"{'Throughput':<28}{f'{total_calls / wall:,.0f} syncs/s':>28}")
    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"{'Latency p50 / p95':<28}{f'{p50:.1f} / {p95:.1f} ms':>28}")
    if args.price_per_mb > 0:
        money = saved / (1024 * 1024) * args.price_per_mb
        print("-" * 58)
        print(f"{'Cost avoided at this volume':<28}"
              f"{f'{money:,.2f} {args.currency}':>28}")
    print("=" * 58)
    print("\nThis measures synthetic states. For a number you can trust,")
    print("replace make_state() with a sample of your own payloads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
