# IzgoN

**Your devices are sending the same data over and over. IzgoN sends only what changed — and shows you exactly how many bytes you stopped paying for.**

IzgoN sits between your fleet and your backend. Each node POSTs its current state; IzgoN compares it to the last state it saw, and returns either `NO_CHANGE` (0 bytes of payload) or a minimal JSON delta. Every call is logged, so the dashboard can tell you the one number that matters: **bytes you would have sent vs. bytes you actually sent.**

```
       node state              IzgoN                 your backend
   {"t":21.5,"h":60}  ──►  compare + diff  ──►  {"t":21.5}   (delta only)
   {"t":21.5,"h":60}  ──►  compare + diff  ──►  NO_CHANGE    (0 bytes)
```

![IzgoN — 93.9% less data at a 5% change rate, measured](izgon-dashboard.png)

Live demo: <https://izgon-api.onrender.com> — two caveats before you click, because
the free tier is honest about what it is. The first request takes 20–40 seconds to
wake the instance. And the counters reset to zero every time it sleeps: the free
plan has no persistent disk, so the SQLite event log goes with the container. The
image above is the same dashboard with traffic in it. To fill it yourself, point
`benchmark.py` at the demo and watch the number climb.

---

## Why not just use `jsonpatch`?

Fair question, and the honest answer is: **if you enjoy building and running that yourself, use `jsonpatch`. It's free and it works.**

`jsonpatch` gives you a diff function. IzgoN gives you:

- a running server that holds per-node baseline state, so nodes stay stateless
- a free/paid tier gate with offline license validation — no phone-home
- an event log and a dashboard
- **a savings number you can put in front of whoever pays the bill**

The diff is ~50 lines. The other four things are the product.

## Who this is for

You will get value from IzgoN if you have **many nodes reporting frequently, where most of the state doesn't change between reports**:

- IoT or sensor fleets on metered cellular / satellite SIMs
- Field agents or edge devices on constrained links
- Dashboards or monitoring agents polling every few seconds
- Game or simulation servers syncing entity state

You will **not** get value if your payloads are small, infrequent, or change completely every time. Run the benchmark below against your own data before you pay for anything.

---

## Quick start

```bash
git clone https://github.com/izGamber/IZgoN.git
cd IZgoN
cp .env.example .env
docker compose up -d
```

Open <http://localhost:8000> for the dashboard.

Send a state. The node's payload goes inside `state`, and every write needs your
`X-API-Key` — it defaults to `dev-local-key` until you change it in `.env`.

```bash
curl -X POST http://localhost:8000/api/nodes/sensor-01/sync \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-local-key" \
  -d '{"state": {"temp": 21.5, "hum": 60, "batt": 98}}'
```

Send the same state again — `NO_CHANGE`, zero delta bytes:

```json
{"node_id":"sensor-01","status":"NO_CHANGE","checksum":"4680f5c0…","delta":null,"bytes_full":37,"bytes_sent":0}
```

Change one field and only that field comes back:

```bash
curl -X POST http://localhost:8000/api/nodes/sensor-01/sync \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-local-key" \
  -d '{"state": {"temp": 22.1, "hum": 60, "batt": 98}}'
```

---

## Measure it on your own data

Do not take anyone's benchmark on faith, including this one. `benchmark.py` ships in the repo, uses only the Python standard library, and runs against your own IzgoN instance:

```bash
python3 benchmark.py --nodes 50 --rounds 100 --change-rate 0.05
```

It generates realistic node states, replays them, and prints bytes that would have been sent, bytes actually sent, and the saving. Point `--change-rate` at whatever matches your real workload.

Measured results, all reproducible with the commands in [BENCHMARK.md](BENCHMARK.md):

| Change rate | Without IzgoN | With IzgoN | Saved |
|---|---|---|---|
| 5 % | 759.2 KB | 46.5 KB | **93.9 %** |
| 20 % | 758.2 KB | 161.9 KB | **78.6 %** |
| 70 % | 758.4 KB | 548.6 KB | **27.7 %** |

**Read the last row.** When every report differs from the one before it, the saving falls to 27.7 % and IzgoN stops being worth running. That is the honest boundary of this tool. Replace `make_state()` in the benchmark with a sample of your own payloads before you buy anything — if the number is small for your data, don't.

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/nodes/{id}/sync` | API key | Submit node state, get `NO_CHANGE` or delta |
| `GET` | `/api/nodes` | API key | List known nodes and their baselines |
| `GET` | `/api/metrics` | — | Live totals: bytes full, bytes sent, savings |
| `GET` | `/api/license` | — | Current tier and remaining free syncs |
| `GET` | `/healthz` | — | Redis reachability |
| `GET` | `/` | — | Dashboard |

### Response shape

```json
{
  "node_id": "sensor-01",
  "status": "SYNC_REQUIRED",
  "checksum": "e163cdfd20a3617d2fe560a3dd849c2fb8e7c8041c7a49fd225bea93be26ad33",
  "delta": { "temp": 22.1 },
  "bytes_full": 37,
  "bytes_sent": 14
}
```

`status` is `NO_CHANGE` or `SYNC_REQUIRED`. On `NO_CHANGE`, `delta` is `null` and `bytes_sent` is `0`. `checksum` is a SHA-256 of the stored state, so a client can confirm both sides agree without transferring anything. Nested objects are diffed recursively. **Lists are compared as a whole, not element by element** — if one item in a list changes, the whole list is sent. This is a deliberate limitation; see [Limitations](#limitations).

---

## Configuration

All settings are environment variables. Copy `.env.example` to `.env` and edit.

| Variable | Default | Meaning |
|---|---|---|
| `DATAPULSE_REDIS_URL` | `redis://localhost:6379/0` | Redis connection. Holds per-node baseline state. |
| `DATAPULSE_DB_PATH` | `datapulse_events.db` | SQLite file for the event log. |
| `DATAPULSE_API_KEY` | `dev-local-key` | Key for authenticated endpoints. **Change this.** |
| `DATAPULSE_FREE_TIER_LIMIT` | `10000` | Free syncs before `402`. Enough to run the benchmark and evaluate. |
| `DATAPULSE_LICENSE_KEY` | — | Paid licence key. |
| `DATAPULSE_LICENSE_SECRET` | — | HMAC secret used to validate the key offline. |
| `DATAPULSE_ALLOWED_ORIGINS` | `*` | CORS origins. **Narrow this in production.** |

---

## Licence and price

IzgoN is **source-available, not open source.** You can read, run, and modify it for yourself.

- **Free tier** — 10,000 syncs, no key needed. Enough to run the benchmark on your own payloads and decide.
- **Commercial licence** — one-time payment, no subscription, no phone-home. The key is validated offline with HMAC-SHA256, so your instance never talks to a licence server.

**Start on the free tier.** 10,000 syncs is enough to run the benchmark against your
own payloads and decide whether this is worth anything to you. No account, no card,
no sign-up — `git clone` and `docker compose up -d`.

Checkout is being set up. Until it is live, open an issue titled `licence` and I will
send you one; the price does not change.

A licence is two values — a key and a signing secret — and both go in your `.env`:

```
DATAPULSE_LICENSE_KEY=DPC-....................=.........
DATAPULSE_LICENSE_SECRET=<64 hex characters>
```

Restart, then check `GET /api/license` shows `"licensed": true`. The key on its own
does nothing: validation is local HMAC-SHA256, so your server needs the secret to
verify it and never contacts a licence server.

---

## Limitations

Stated plainly, because you will find them anyway:

- **Lists are not diffed element by element.** Change one entry in a 500-item array and the whole array is sent. If your payloads are list-heavy, savings will be much lower than the benchmark suggests.
- **Baseline state lives in Redis.** If Redis is wiped, every node sends full state once to re-establish its baseline. Use a persistent Redis volume — the shipped `docker-compose.yml` does.
- **Single instance.** There is no clustering. One IzgoN process owns the baselines.
- **No client SDK yet.** Integration is a plain HTTP POST; there is no packaged library.

---

## Running without Docker

```bash
pip install -r requirements.txt
export DATAPULSE_REDIS_URL=redis://localhost:6379/0
uvicorn app:app --host 0.0.0.0 --port 8000
```

Requires a reachable Redis.

---

## Status

Version 1.0.0. Built and maintained by one person. If something is broken, open an issue and say what you sent and what you got back.
