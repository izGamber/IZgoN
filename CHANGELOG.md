# Changelog

## 1.2.0 — 2026-09-11

The licence check did not check anything. This release makes it real, and it is
a breaking change to the key format.

### Fixed

- **Anyone could issue themselves a licence.** Keys were signed with HMAC, and
  the buyer's server needed the same secret that signed them — so the buyer held
  both halves and could mint a matching pair in ten seconds, with a script that
  was sitting in this repository. The gate stopped nobody who read the code.

  Keys are now signed with **Ed25519**. The private half never leaves the seller;
  IzgoN ships only the public half, which can verify a signature and cannot
  produce one. Verification stays entirely offline — no phone-home, same as
  before.

  What this does not do, and no scheme that publishes its own source can, is
  stop someone from deleting the check. That is a licence violation with a legal
  remedy, not a hole left open in the design. Said plainly here so nobody buys
  on a wrong idea of what the key is for.

### Changed

- **Buyers now set one variable, not two.** `DATAPULSE_LICENSE_KEY` and nothing
  else; `DATAPULSE_LICENSE_SECRET` is gone, along with the confusion of asking a
  customer to store a "secret" that was never secret.
- Keys in the old `DPC-` format are refused, with a message telling the holder
  to ask for a replacement. Nobody has bought one yet, so nobody is affected.
- `cryptography` is now a dependency.
- The licence text gained an ownership clause, a requirement to keep the
  copyright notice intact, and a governing-law clause.

## 1.1.2 — 2026-09-11

Found by running the one command this project tells people to run, on a machine
with nothing else on it.

### Fixed

- **`docker run ghcr.io/izgamber/izgon:latest` could not work.** That command
  starts this container and nothing else, so there was no Redis — and every
  state read went straight to it. The dashboard loaded, `/healthz` admitted
  `redis_reachable: false`, and then the first sync died with an unexplained
  HTTP 500. The one path advertised as "no cloning, no building" was the one
  path guaranteed to fail in front of whoever tried it first.

  When Redis is unreachable the last known state is now kept in this process
  instead. Nothing of value is lost: a restart means every node resyncs
  `FULL_STATE` once, which is exactly what a cold Redis would have produced.
  It is not silent — `/healthz` and `/api/metrics` report
  `storage: "memory (Redis unreachable)"`, the dashboard shows a banner, and
  one warning line goes to the log explaining how to get a setup that survives
  restarts.

  This is only safe because IzgoN is single-instance by design; with several
  replicas behind one Redis, an in-memory fallback would let them disagree
  about the same node. Set `DATAPULSE_ALLOW_MEMORY_FALLBACK=0` to refuse to
  start down that road and let the error surface instead.

### Changed

- `/healthz` and `/api/metrics` both carry a `storage` field now.
  `redis_reachable` is unchanged, for anything already reading it.

## 1.1.1 — 2026-09-11

Three holes found by attacking the running server rather than reading the code.

### Fixed

- **Byte counts were wrong for any non-ASCII payload.** `json.dumps` escapes
  every non-ASCII character to `\uXXXX`, so `{"grad":"日本東京"}` was counted as
  75 bytes while the wire carries 43. Every fleet reporting Chinese, Japanese,
  Cyrillic or Bosnian diacritics had its totals overstated by most of half —
  and the `--price-per-mb` figure with them. `ensure_ascii=False` now, verified
  against the real UTF-8 length.
- **A deeply nested payload returned HTTP 500.** 1.8 KB nested 300 levels was
  enough to blow the recursion limit inside serialisation. `state` deeper than
  `DATAPULSE_MAX_STATE_DEPTH` (32) is now refused with `422` and a message
  saying which knob to turn. The depth check is iterative, because recursing
  there would be the same bug.
- **There was no limit on payload size.** A 5 MB state was accepted and stored.
  Now `413` past `DATAPULSE_MAX_STATE_BYTES` (1 MB default). Both limits are
  checked before anything touches the state.

### Added

- **Published Docker image.** `docker run -p 8000:8000 ghcr.io/izgamber/izgon:latest`
  — no clone, no build. A GitHub Actions workflow builds and pushes it on every
  push to `main` and every `v*` tag.

## 1.1.0 — 2026-09-10

The measurement was wrong. This fixes it, and the published numbers move up as a
result.

### Fixed

- **One ruler on both sides of the comparison.** `bytes_full` was serialised with
  compact JSON while `bytes_sent` used Python's default `json.dumps` spacing,
  which adds two bytes per field. Every saving figure IzgoN has ever printed was
  therefore computed against two different rulers and understated by roughly two
  bytes per changed field. Both are compact now. On the standard benchmark:
  **5 % change rate 93.9 % → 94.3 %, 20 % 78.6 % → 80.5 %, 70 % 27.7 % → 35.3 %.**
  Anything published before today understates the tool rather than overstating it.
- **The dashboard stopped updating after an upgrade.** The service worker
  cached the HTML shell cache-first under a cache name that never changed, so
  once a browser had seen the dashboard it kept serving that copy forever — an
  upgraded server showed the old page, old figures and all. Found by upgrading a
  live instance and watching it serve the previous release's numbers. The shell
  is network-first now, cache is the offline fallback only, the cache name
  carries the app version, and `/sw.js` is served with `Cache-Control: no-store`
  so a browser can actually learn that the name changed.
- **A delta is never sent when it would be bigger than the state it replaces.**
  When keys disappear, the `__deleted__` tombstones can outweigh what is left:
  `{"a":1}` is 7 bytes, and the delta that removes four sibling keys is 101. The
  old code sent the 101 bytes. It now sends the state whole under the new status
  `FULL_STATE`, so `bytes_sent` can never exceed `bytes_full` and a measured
  saving can never come out negative.

### Added

- **`benchmark.py --payload-file`** — run the benchmark on a JSON array or JSON
  Lines export of your own reports instead of generated ones. Each device replays
  its own real sequence in its real order, and the change rate is measured from
  the data rather than assumed. `--id-field` and `--state-field` handle wrapped
  and non-standard shapes; the device id is auto-detected from `node_id`,
  `device_id`, `deviceId`, `device`, `id` or `serial`. `--change-rate` is ignored
  in this mode and says so. Previously this required editing `make_state()` in
  the script — the step that most evaluations were never going to take.
- Each `--payload-file` run gets its own node-id namespace, so a repeat run
  starts from a clean baseline instead of inheriting the previous run's last
  state and quietly inflating the result. This was found by running the same file
  three times and noticing the first number differ from the next two.
- The benchmark reports how many syncs were sent whole because a delta would have
  been bigger.
- **`izgon_client.py`** — a ~60-line stdlib-only reference client that implements
  all three statuses correctly. Not an SDK; the smallest readable statement of the
  protocol, because a server that returns a delta and never says how to apply one
  is only half documented.

### Changed

- `status` now has three values: `NO_CHANGE`, `SYNC_REQUIRED`, `FULL_STATE`. A
  client that only knows the first two should treat an unknown status as "resync
  from scratch". A new status value was chosen over a new boolean flag precisely
  because ignoring it fails loudly rather than silently merging a full state onto
  a stale one.

### Verified

66 checks. The 1.0.1 suite plus new coverage for the `FULL_STATE` guard, compact
accounting, benchmark payload parsing (JSON array, JSON Lines, wrapped payloads,
unusable device ids, single-report devices) and eight error paths. The synthetic
mode still reproduces the 1.0.1 byte totals exactly against a 1.0.1 server, so the
change in the published table comes from the accounting fix and nothing else.

Plus a property test the protocol did not have before: 720 randomised mutations
across 12 nodes — nested objects, lists, nulls, booleans, keys appearing and
disappearing — reconstructing the state from whatever the server returned and
asserting after every sync that it matches what was sent. All three statuses were
exercised (213 `NO_CHANGE`, 230 `SYNC_REQUIRED`, 277 `FULL_STATE`), zero
mismatches, and `bytes_sent` never exceeded `bytes_full`.

## 1.0.1 — 2026-09-10

Hardening pass. No behaviour change for a correct client; four of these are
things a production deployment would have hit.

### Fixed

- **Constant-time API key comparison.** `_check_key` compared the header with
  `!=`, which returns as soon as two characters differ and leaks the key one
  character at a time to anyone timing the responses. Now `hmac.compare_digest`.
- **SQLite runs in WAL mode with a 15-second busy timeout.** Before, a read of
  `/api/metrics` blocked every concurrent write and vice versa, and an overlap
  raised `database is locked`. Measured: 900 requests over 32 threads went from
  327 req/s to 468 req/s.
- **The free-tier gate is O(1) instead of O(n).** It called `COUNT(*)` over the
  whole event log on *every* sync, so the server got slower the longer it ran.
  The count is now read once and kept in memory. Measured on a 250,000-row log:
  **p50 72.0 ms → 2.9 ms, p95 88.2 ms → 3.9 ms.**
- **`node_id` is validated:** 1–128 characters of `A-Za-z0-9._:-`, else `422`.
  Node ids become Redis keys and log rows; without a bound one caller could grow
  memory without limit and fill the log with junk.
- **Indexes on `sync_events (node_id)` and `(ts)`.**
- **`/api/nodes` is paged** (`?limit=`, default 500, max 5000) and reports
  `total` and `truncated`. It used to serialise every baseline in one response.
- **The dashboard scrubs `?key=` from the address bar** after reading it, so the
  API key stops landing in browser history, proxy logs and `Referer` headers.

### Verified

45 checks across authentication, node-id validation, delta correctness, nested
diffing, metrics, paging, the free-tier gate, licence unlock, concurrency, WAL,
and durability across a restart. All pass.
