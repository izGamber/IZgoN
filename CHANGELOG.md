# Changelog

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
