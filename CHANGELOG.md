# Changelog

## 1.4.1 — 2026-09-14

One correction, and it is the kind worth its own release rather than a quiet
edit: 1.4.0 shipped documentation that got the three-number distinction right
and then printed the wrong one.

### Fixed

- `benchmark.py --conditional` labelled the **both ways** figure as "what a
  device on its own metered SIM pays against". It is not. That figure is
  measured against a baseline where the full state travels in *both*
  directions — a client that polls and gets everything back. A device that only
  reports was never receiving the full state, so it has nothing to save in that
  direction, and its number is the **report** figure: 39.9 % at a 5 % change
  rate, not 64.9 %.

  The benchmark now prints all three, each named with the wiring it belongs to,
  and the same wrong framing is corrected in a comment in `app.py` beside
  `both_ways_saved_pct`. `BENCHMARK.md` and `README.md` already said it
  correctly; the code did not, and code people run is what they believe.

  No behaviour changed — the measured numbers were right, only the sentence
  naming them was wrong. 65 + 30 checks still pass unchanged.

## 1.4.0 — 2026-09-14

The release that fixes what IzgoN was measuring.

Every version up to 1.3.0 shrank the **reply** and left the **report** alone:
the device uploaded its full state on every cycle no matter what came back. On
a gateway, where the metered link is the one going upstream to the backend,
that is the whole job and the published 94.3 % is the right number. For a fleet
where each device carries its own SIM — the case on the front page and in the
video — it was half the transaction, and quoting the reply figure there
overstated what the bill would do. Anyone with a packet capture could have said
so, and would have been right.

### Added

- **Conditional sync.** Send `checksum` instead of `state` and the report never
  goes on the wire. The server compares the token against the baseline it holds
  and answers `NO_CHANGE` — or `SEND_STATE`, and the client repeats the call
  with the state.

  The token is opaque and produced by this server, so there is no canonical-JSON
  spec for a client to reproduce and get subtly wrong in another language: store
  the last `checksum` you were handed, send it back when your own report has not
  changed. A `SEND_STATE` round trip is not logged as a sync event and does not
  count against the free tier — nothing was synchronised, nothing was measured,
  and charging for it would be dishonest.

  Measured, same runs as before, 5 % change rate: reply 94.3 % saved, report
  39.9 %, both directions against a full-state-both-ways baseline 64.9 %. Three
  wiring diagrams, three numbers — **a per-SIM fleet's number is 39.9 %**, not
  64.9 %, because a reporting device was never receiving the full state back and
  so has nothing to save in that direction. The report side also saves less than
  the count of unchanged reports suggests, because the token still travels:
  72 bytes against roughly 180. That is arithmetic, not modesty.

- **`epoch`.** An opaque token for the lifetime of the caller's copy of the
  state. When it changes, the server answers with the whole state instead of a
  delta. This closes a failure that was silent: a backend that lost its mirror
  kept receiving deltas, merged them into nothing, and reported success.
  Sparkplug solves the same problem with a birth/death sequence number.

  Epochs are held in memory, so restarting IzgoN also forces one `FULL_STATE`
  per node using them — it errs towards sending too much, the only safe
  direction. A `SEND_STATE` answer deliberately does not mark the epoch as seen;
  otherwise the retry that carried the state would have been answered with a
  delta the new mirror could not apply, which is the exact bug this prevents.

- **Both directions in the metrics and on the dashboard.** `uplink_saved_pct`,
  `both_ways_saved_pct`, and a second dashboard card. Events logged before this
  version carry no request measurement and are excluded from those figures
  rather than counted as a saving nobody measured. A server whose clients all
  still upload full state reads 0 %, and says why.

- **`benchmark.py --conditional`** measures the request side and prints the
  both-ways figure, so the claim can be reproduced rather than believed.

### Changed

- `checksum` in every reply is now 32 characters rather than 64. A device
  echoing it back pays for every character on its own SIM, and against a
  200-byte report a 64-character receipt was most of the saving. The full
  SHA-256 is still what the engine compares internally; only the token handed to
  clients is shortened. 128 bits is far past what this needs — a collision costs
  one missed update on one node, and the inputs are consecutive reports from the
  same device, not attacker-chosen.

- The reference client keeps the epoch, the last report and the last token per
  node, and **measures both candidate requests before choosing**: if the report
  is smaller than the token would be, it sends the report. Paying more bytes to
  "save" bytes is not a saving — the same rule the engine already applied when
  choosing between a delta and the whole state. `conditional=False` restores the
  old always-send-state behaviour.

- `README.md` and `BENCHMARK.md` now separate reply from report everywhere, and
  say which number belongs to which topology. Two limits are stated that were
  not: a cellular bill carries 50–54 bytes of packet header in both directions
  regardless of payload (`NO_CHANGE` is 0 bytes of payload, not 0 on the
  invoice), and platforms such as Home Assistant already suppress unchanged
  values on their own.

### Fixed

- Databases created by an older version are migrated in place. `CREATE TABLE IF
  NOT EXISTS` leaves an existing table exactly as it was, so upgrading over a
  log with real history would otherwise have failed on the first insert naming
  the new columns.

### Verified

65 checks covering conditional sync, epochs, the migration, both-direction
accounting and the reference client's state machine, plus full regression of
everything in 1.3.0 — which still passes its own 30 checks unchanged. The
benchmark reproduces 94.3 / 80.5 / 35.3 on the reply side exactly as before.

## 1.3.0 — 2026-09-14

Four additions, each one a thing a fleet operator asked for before a feature
list did. Nothing here changes an existing client: every new field is additive
and every new knob is off or advisory by default.

### Added

- **Silence alerting.** A background pass compares each node's last sync
  against `DATAPULSE_ALERT_AFTER` and POSTs to `DATAPULSE_ALERT_URL` when one
  goes quiet, and again when it comes back. Off unless that URL is set.

  It deliberately does not alert per loop, only on the transition in and out of
  silence; it never alerts for a node it has not seen report; and the first pass
  after start only records who is already quiet, so restarting the server does
  not fire a storm about the window the server itself was down. `/healthz`
  reports which nodes are currently silent and how many alerts have been sent.

- **Adaptive reporting interval.** Send your current interval as `interval` in
  the sync body and the reply carries `polling.next_interval` — the same value
  while anything is changing, and a backed-off one after
  `DATAPULSE_QUIET_AFTER` identical reports in a row, doubling to a ceiling of
  `DATAPULSE_MAX_INTERVAL`. One changed field puts it straight back.

  Two things said plainly, because this trades freshness for battery and bytes.
  It is **advice**: the device's firmware decides whether to obey, and a fleet
  already in the field will not until it is reflashed. And backing off means a
  change can be reported up to that interval late, which is why the reply also
  carries `max_staleness` rather than leaving anyone to work it out. Omit
  `interval` and no advice is given at all — the server will not invent a
  number it cannot know.

- **Batch sync, for devices that were offline.** `POST
  /api/nodes/{id}/sync/batch` takes `{"states": [...]}` oldest first and answers
  once. A truck coming out of a tunnel flushes its queue in one request instead
  of one round trip per report over the link that just failed.

  Each report is still evaluated against the one before it, so the byte
  accounting is what it would have been had they arrived live. Collapsing the
  queue to first-versus-last would have made the saving look better and the
  number meaningless.

- **Baselines survive a restart without Redis.** 1.1.2 kept the server
  answering when Redis was unreachable by holding state in memory; a restart
  then made every node resync in full. State now also writes through to SQLite
  in that mode and is read back on a cold start. Redis is still the primary
  store and nothing changes when it is up.

### Site

- **A sandbox on the page.** Paste two consecutive reports from one of your own
  devices and see exactly what the server would answer — status, delta, and the
  byte counts. It runs in the browser: no API key in the page, no load on the
  demo, and it works while the free demo instance is asleep.

  The browser implementation is checked against the server's own engine on 200
  randomised state pairs — nested objects, lists, nulls, disappearing keys and
  non-ASCII text — and must agree on status, delta and both byte counts. That
  check caught a real bug before this shipped: the page was writing a deleted
  field as `"__deleted__"` where the server writes `{"__deleted__": true}`, 7
  bytes lighter per removed field. It would have quietly understated the
  product to anyone who compared the two.

### Verified

30 checks on the server covering all four additions plus the existing
protocol, auth, node-id validation, licensing and metrics. The benchmark
reproduces 94.2 % at a 5 % change rate, unchanged from 1.2.2.


## 1.2.2 — 2026-09-12

### Fixed

- **A licence key that lost its trailing `=` was refused as a forgery.** Every
  key ends in `==`, and that is exactly the character an email client trims, a
  web form eats, or a customer deletes by hand because it looks like leftover
  punctuation. The signature is made over the padded payload string, so a key
  missing its padding failed verification and came back "unlicensed" — a paying
  customer looking at the same screen as someone who never paid, with nothing on
  either side to explain why. Padding carries no information, so it is now
  restored before the signature is checked. Surrounding whitespace and the
  quotes people copy along with a `.env` line are stripped too.

  Found by feeding the validator a key mangled every way a key gets mangled
  between an invoice and a server. Forgery detection is untouched and was
  re-tested against it: a key signed by any other private key is still refused,
  trimmed or not.

- The seller's issuing tool checks keys the same way, so `check` can no longer
  say a key is good when the buyer's server would say otherwise.

## 1.2.1 — 2026-09-12

- **The seller's public key ships for real.** 1.2.0 went out with a placeholder
  where the verifying key belongs, so no licence could have validated against
  it. The real Ed25519 public key is in place now. Verified: a licence signed by
  any other private key is rejected, a correctly signed one validates, and a
  missing key simply reads as unlicensed.

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
