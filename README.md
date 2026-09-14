# IzgoN

**Your devices are sending the same data over and over. IzgoN sends only what changed — and shows you exactly how many bytes you stopped paying for.**

IzgoN sits between your fleet and your backend. Each node POSTs its current state; IzgoN compares it to the last state it saw, and returns `NO_CHANGE` (0 bytes of payload), a minimal JSON delta, or — when a delta would be bigger than the state it replaces — the state itself. Every call is logged, so the dashboard can tell you the one number that matters: **bytes you would have sent vs. bytes you actually sent.**

```
       node state              IzgoN                 your backend
   {"t":21.5,"h":60}  ──►  compare + diff  ──►  {"t":21.5}   (delta only)
   {"t":21.5,"h":60}  ──►  compare + diff  ──►  NO_CHANGE    (0 bytes)
```

### Which direction is being saved

Two links, and they do not cost the same thing.

Up to v1.3.0 IzgoN shrank the **reply** and nothing else. The device still uploaded its
whole report every cycle. That is the right shape when IzgoN runs on a gateway and the
expensive link is the one going upstream — and it is only half the story when every
device carries its own metered SIM, because the half the device pays for never moved.

v1.4.0 adds **conditional sync**, which shrinks the report as well. A device whose
report is identical to the one it last sent does not send the report: it echoes back the
short token the server gave it, and the server answers `NO_CHANGE` without ever
receiving the state.

```
   report unchanged   {"checksum":"a1b2…"}  ──►  NO_CHANGE   (report never uploaded)
   report changed     {"state":{…}}         ──►  delta / full state as before
   token unknown      {"checksum":"a1b2…"}  ──►  SEND_STATE  (repeat with the state)
```

Measured, same runs as BENCHMARK.md, 5 % change rate — three wiring diagrams, three
different numbers, and picking the flattering one is how a benchmark turns into a lie:

| Your setup | Saved |
|---|---|
| IzgoN on a gateway; the metered link runs from it to your backend | **94.3 %** (reply) |
| Every device on its own metered SIM, reporting upstream | **39.9 %** (report) |
| A client that polls and gets the whole state back each time | **64.9 %** (both) |

A reporting device was never receiving the full state back, so it has nothing to save in
that direction — the per-SIM number is 39.9 %, not 64.9 %.

![IzgoN dashboard — bytes avoided, counted live from real traffic](izgon-dashboard.png)

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

- IoT or sensor fleets on metered cellular / satellite SIMs — **use conditional sync**,
  and expect the report figure (39.9 % at a 5 % change rate), not the reply figure
- A gateway aggregating many devices over a cheap local link, with one metered uplink to
  the backend — this is where the reply figure (94.3 %) is the whole story
- Field agents or edge devices on constrained links
- Dashboards or monitoring agents polling every few seconds
- Game or simulation servers syncing entity state

You will **not** get value if your payloads are small, infrequent, or change completely
every time. Two more honest limits worth knowing before you spend anything:

- **A cellular bill is not payload.** Each packet carries 50–54 bytes of
  Ethernet/IP/UDP/GTP header, billed in both directions, and most operators round each
  session up to a minimum billing unit. `NO_CHANGE` is 0 bytes of *payload*; it is not
  0 bytes on the invoice. Fewer and smaller packets still cost less — just not by the
  percentage a payload-only measurement suggests.
- **If your platform already suppresses unchanged values, this is not new to you.**
  Home Assistant, for one, does it already.

Run the benchmark against your own data before you pay for anything — `--payload-file`
takes an export of your real reports, so it is one command, not a code change.

---

## Quick start

Nothing to clone or build — the image is published:

```bash
docker run -p 8000:8000 -e DATAPULSE_API_KEY=change-me ghcr.io/izgamber/izgon:latest
```

That runs IzgoN on its own. For per-node baselines that survive a restart you
want Redis alongside it, which is what the compose file is for:

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
{"node_id":"sensor-01","status":"NO_CHANGE","checksum":"4680f5c0…","delta":null,"bytes_full":32,"bytes_sent":0}
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

Do not take anyone's benchmark on faith, including this one. `benchmark.py` ships in
the repo and uses only the Python standard library.

**Point it at your own reports. One command:**

```bash
python3 benchmark.py --payload-file my-reports.json
```

The file is a JSON array, or JSON Lines, of reports your devices actually sent —
export a few thousand rows from wherever you already store them. Each device
replays its own real sequence in its real order, and the change rate is *measured
from your data*, not assumed:

```
IzgoN benchmark — replaying 4,812 of your own reports from 37 device(s)
Observed change rate: 6.4%  — measured from your data
```

Common shapes are handled without editing anything. The device id is picked up
automatically from `node_id`, `device_id`, `deviceId`, `device`, `id` or `serial`;
override with `--id-field`. If each record wraps the payload, unwrap it:

```bash
python3 benchmark.py --payload-file logs.jsonl --state-field state --id-field device_id
python3 benchmark.py --payload-file logs.jsonl --price-per-mb 0.05   # prints money, not just bytes
```

Each run uses its own node-id namespace, so a second run starts from a clean
baseline and cannot inherit the first run's state and quietly inflate the result.

**If you have no sample yet**, the synthetic mode shows the shape of the curve:

```bash
python3 benchmark.py --nodes 50 --rounds 100 --change-rate 0.05
```

Measured results, all reproducible with the commands in [BENCHMARK.md](BENCHMARK.md):

| Change rate | Without IzgoN | With IzgoN | Saved |
|---|---|---|---|
| 5 % | 759.2 KB | 43.0 KB | **94.3 %** |
| 20 % | 758.2 KB | 147.5 KB | **80.5 %** |
| 70 % | 758.4 KB | 490.4 KB | **35.3 %** |

**Read the last row.** When almost every report differs from the one before it, the
saving falls to 35.3 % and most of the point of running this disappears. That is the
honest boundary of the tool. Measure your own payloads before you buy anything — if
the number is small for your data, don't.

> These figures moved up in v1.1.0, and the reason matters more than the numbers.
> Until v1.1.0 the "would have sent" side was measured with compact JSON while the
> "actually sent" side used Python's default `json.dumps` spacing — two rulers, so
> every published saving was wrong, understated by roughly two bytes per field.
> Both sides are compact now. The old figures (93.9 / 78.6 / 27.7 %) were too low,
> not too high; anything published before this date understates the tool.

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/nodes/{id}/sync` | API key | Submit `state` — or just `checksum` when nothing changed — and get `NO_CHANGE`, a delta, the full state, or `SEND_STATE` |
| `POST` | `/api/nodes/{id}/sync/batch` | API key | Replay a buffered queue, oldest first, in one request |
| `GET` | `/api/nodes` | API key | List known nodes and their baselines (paged, `?limit=` up to 5000) |
| `GET` | `/api/metrics` | — | Live totals for both directions: `bandwidth_saved_pct` (reply), `uplink_saved_pct` (report), `both_ways_saved_pct` |
| `GET` | `/api/license` | — | Current tier and remaining free syncs |
| `GET` | `/healthz` | — | Redis reachability, storage mode, alert status |
| `GET` | `/` | — | Dashboard |

### Response shape

```json
{
  "node_id": "sensor-01",
  "status": "SYNC_REQUIRED",
  "checksum": "e163cdfd20a3617d2fe560a3dd849c2fb8e7c8041c7a49fd225bea93be26ad33",
  "delta": { "temp": 22.1 },
  "bytes_full": 32,
  "bytes_sent": 13
}
```

`node_id` must be 1–128 characters of `A–Z a–z 0–9 . _ : -`. Anything else is
rejected with `422`: node ids become Redis keys and log rows, so an unbounded id
is an unbounded memory cost.

`status` is one of four values, and a client must handle all four:

| `status` | `delta` holds | What the client does |
|---|---|---|
| `NO_CHANGE` | `null`, `bytes_sent` is `0` | nothing — both sides already agree |
| `SYNC_REQUIRED` | only the changed keys | **merge** it into the known state |
| `FULL_STATE` | the complete new state | **replace** the known state with it |
| `SEND_STATE` | `null` | repeat the call carrying `state` |

`FULL_STATE` exists because a delta is not always smaller. Drop enough keys at once
and the deletion markers outweigh what is left — `{"a":1}` is 7 bytes, while the
delta that removes four sibling keys is 101. Sending that delta would cost you money
to save you nothing, so IzgoN sends the state instead and says so. `bytes_sent` can
therefore never exceed `bytes_full`, and a measured saving can never come out
negative. A client that only knows the first two statuses should treat an unknown
one as "resync from scratch", which is exactly right.

`checksum` is an opaque 32-character token for the stored state. Two uses: confirm both
sides agree without transferring anything, and — the reason it is short — hand it back
on the next call when nothing has changed, so the report itself never goes on the wire.
`bytes_full` and `bytes_sent` are both compact JSON — one ruler on both sides, so the
difference between them is a real number.

### Conditional sync — not sending the report at all

Send `checksum` instead of `state` and the request carries about 72 bytes rather than
the whole report:

```jsonc
// nothing changed since the last call
{ "checksum": "e163cdfd20a3617d2fe560a3dd849c2f", "epoch": "boot-7a41" }
```

* matches what the server holds → `NO_CHANGE`, and the report was never uploaded
* does not match, or the server has no baseline → `SEND_STATE`, and the client repeats
  the call with `state`

A `SEND_STATE` round trip is **not** logged as a sync event and does **not** count
against the free tier. Nothing was synchronised and nothing was measured; charging for
it would be dishonest.

The token is opaque — produced here, compared only here. There is no canonical-JSON
spec for your client to reproduce and get subtly wrong in another language. Store the
last one you were given, and send it back when your own report is unchanged.

Do not send it when your report is smaller than the token would be. The reference
client measures both and sends whichever is smaller, for the same reason the engine
picks `FULL_STATE` over an oversized delta: spending more bytes to "save" bytes is not
a saving.

### `epoch` — so a lost mirror never receives a delta

Optional, and the failure it prevents is silent. A backend that lost its copy of the
state keeps receiving deltas, merges them into nothing, and believes it is in sync.

Generate a token at start-up, send the same value on every call. When the server sees a
value it has not seen for that node, it answers with the whole state instead of a delta:

```jsonc
{ "state": { "temp": 22.1 }, "epoch": "boot-7a41" }
```

Epochs are held in memory, so restarting **IzgoN** also forces one `FULL_STATE` per node
using them. That errs towards sending too much, which is the only safe direction for
this to fail in. Omit the field and behaviour is exactly as it was before v1.4.0.

Nested objects are diffed recursively. **Lists are compared as a whole, not element
by element** — if one item in a list changes, the whole list is sent. This is a
deliberate limitation; see [Limitations](#limitations).

### Applying what comes back

`izgon_client.py` in the repo is a stdlib-only reference client. It is not an SDK — it
is the smallest correct implementation of the four statuses, short enough to read in one
sitting and copy into whatever language you actually use:

```python
from izgon_client import IzgonClient

c = IzgonClient("http://localhost:8000", "dev-local-key")
for report in my_reports:
    mirror = c.sync("sensor-01", report)
    # mirror now equals report, having transferred only what changed —
    # and on an unchanged report, having sent no report at all
```

It keeps the epoch, the last report it sent and the last token per node, uses the
conditional call when that is genuinely smaller, recovers from `SEND_STATE` inside the
same `sync()` call, and raises on a status it does not recognise rather than guessing.
Pass `conditional=False` to get the pre-v1.4.0 behaviour of always sending the state.

The merge rule it implements: nested objects merge recursively, and
`{"__deleted__": true}` removes a key. Verified by replaying 720 randomised
mutations — nested objects, lists, nulls, booleans, keys appearing and
disappearing — and asserting after **every** sync that the reconstructed state is
byte-identical to what was sent.

---

## Telling a device to report less often

Send your current interval with the state and the reply carries a suggestion:

```bash
curl -X POST $IZGON/api/nodes/truck-042/sync \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"interval": 5, "state": {"lat": 43.8563, "fuel": 62}}'
```

```json
{
  "status": "NO_CHANGE",
  "bytes_sent": 0,
  "polling": { "next_interval": 40, "reason": "6 identical reports in a row", "max_staleness": 40 }
}
```

Two things worth being clear about before you build on it:

**It is advice, not a command.** The server has no way to make a device do
anything. Your firmware reads `next_interval` and decides. A fleet already in
the field will keep its old rate until it is reflashed, and reflashing a
deployed fleet is the most expensive thing in this business — so treat this as
something to design into the next firmware, not as a saving you already have.

**Backing off costs freshness.** At `next_interval: 40`, a change that happens
one second after a report is heard about 39 seconds late. That is the whole
trade, so the reply states it as `max_staleness` instead of leaving you to
work it out. If a device must never be that stale, leave `interval` out of the
body and no suggestion is made, or set `DATAPULSE_ADAPTIVE=0` server-wide.

## When a device stops reporting

Set a webhook and IzgoN tells you when a node goes quiet, and again when it
comes back:

```bash
DATAPULSE_ALERT_URL=https://hooks.example.com/izgon
DATAPULSE_ALERT_AFTER=600
```

```json
{ "event": "silent", "node_id": "truck-042", "silent_for_seconds": 640.2, "threshold_seconds": 600 }
```

One alert per transition, never per check. Nodes that have never reported are
never alerted about. And the first pass after the server starts only records
who is already quiet — restarting IzgoN does not fire a burst of alerts about
the window IzgoN itself was down.

## Devices that were offline

A device that buffered while it had no signal can flush the queue in one
request instead of one round trip per report:

```bash
curl -X POST $IZGON/api/nodes/truck-042/sync/batch \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"states": [{"fuel": 50}, {"fuel": 50}, {"fuel": 49}]}'
```

Each report is compared with the one before it, so the byte totals are what
they would have been had the reports arrived live. Collapsing the queue to
first-versus-last would make the saving look better and mean nothing.

The buffer itself belongs in your device code, not here — IzgoN does not ship a
client, and a queue that has to survive a power cut is the device's job.

## Configuration

All settings are environment variables. Copy `.env.example` to `.env` and edit.

| Variable | Default | Meaning |
|---|---|---|
| `DATAPULSE_REDIS_URL` | `redis://localhost:6379/0` | Redis connection. Holds per-node baseline state. |
| `DATAPULSE_DB_PATH` | `datapulse_events.db` | SQLite file for the event log. |
| `DATAPULSE_API_KEY` | `dev-local-key` | Key for authenticated endpoints. **Change this.** |
| `DATAPULSE_FREE_TIER_LIMIT` | `10000` | Free syncs before `402`. Enough to run the benchmark and evaluate. |
| `DATAPULSE_LICENSE_KEY` | — | Paid licence key. The only licence value you set. |
| `DATAPULSE_LICENSE_PUBKEY` | built in | Seller's Ed25519 public key. Only override it if you were told to. |
| `DATAPULSE_ALLOW_MEMORY_FALLBACK` | `1` | Keep node state in memory when Redis is unreachable. `0` fails instead. |
| `DATAPULSE_ALLOWED_ORIGINS` | `*` | CORS origins. **Narrow this in production.** |
| `DATAPULSE_MAX_STATE_DEPTH` | `32` | Reject `state` nested deeper than this with `422`. |
| `DATAPULSE_MAX_STATE_BYTES` | `1048576` | Reject a `state` larger than this with `413`. |
| `DATAPULSE_MAX_BATCH` | `500` | Reports accepted in one `/sync/batch` call. |
| `DATAPULSE_ALERT_URL` | — | Webhook for silence alerts. Alerting is **off** unless this is set. |
| `DATAPULSE_ALERT_AFTER` | `300` | Seconds without a sync before a node counts as silent. |
| `DATAPULSE_ALERT_EVERY` | `30` | How often the watchdog checks. |
| `DATAPULSE_ADAPTIVE` | `1` | Answer with a suggested reporting interval. `0` never suggests one. |
| `DATAPULSE_QUIET_AFTER` | `3` | Identical reports in a row before the suggestion backs off. |
| `DATAPULSE_MAX_INTERVAL` | `300` | Ceiling for the suggested interval, in seconds. |
| `DATAPULSE_INTERVAL_FACTOR` | `2` | How fast the suggestion backs off. |

---

## Licence and price

IzgoN is **source-available, not open source.** You can read, run, and modify it for yourself.

- **Free tier** — 10,000 syncs, no key needed. Enough to run the benchmark on your own payloads and decide.
- **Commercial licence** — one-time payment, no subscription, no phone-home. The key carries an Ed25519 signature which your instance verifies locally against a public key shipped inside IzgoN, so it never talks to a licence server.

**Start on the free tier.** 10,000 syncs is enough to run the benchmark against your
own payloads and decide whether this is worth anything to you. No account, no card,
no sign-up — `git clone` and `docker compose up -d`.

Checkout is being set up. Until it is live, open an issue titled `licence` and I will
send you one; the price does not change.

A licence is one value. Put it in your `.env`:

```
DATAPULSE_LICENSE_KEY=IZG2-....................=..........
```

Restart, then check `GET /api/license` shows `"licensed": true`. There is nothing
else to configure: the key is signed by the seller, and IzgoN verifies that
signature offline with a public key it already carries.

What this does and does not do, stated plainly: nobody can forge a key without the
seller's private half, but IzgoN publishes its own source, so anyone can delete the
check. That is a breach of the licence with a legal remedy, not a technical
impossibility. The key is an honest record of who paid, not a lock.

---

## Security notes

Read these before you put it on anything reachable from outside:

- **`DATAPULSE_API_KEY` protects every write.** It ships as `dev-local-key`.
  Change it. The comparison is constant-time, so the key cannot be recovered by
  timing the responses.
- **`/api/metrics` and `/healthz` are deliberately unauthenticated** so the
  dashboard and your monitoring can read them. They expose event counts, node
  counts and byte totals — no state, no keys. If that is too much for your
  deployment, put them behind your reverse proxy.
- **`DATAPULSE_ALLOWED_ORIGINS` defaults to `*`.** Narrow it to your own origin
  before exposing the dashboard publicly.
- **There is no built-in rate limiting.** Put it behind nginx, Caddy or your
  cloud load balancer if it faces the internet.
- **`state` is bounded in depth and size** (32 levels, 1 MB) and both limits are
  checked before anything touches the payload. Without them a 1.8 KB body nested
  300 levels deep was enough to return a `500`.
- **Your licence key and signing secret belong in `.env`, never in git.** The
  shipped `.gitignore` already excludes `.env`, `sales.log` and `*.db`.

## Limitations

Stated plainly, because you will find them anyway:

- **Lists are not diffed element by element.** Change one entry in a 500-item array and the whole array is sent. If your payloads are list-heavy, savings will be much lower than the benchmark suggests.
- **A shrinking payload saves you nothing.** When keys disappear, the deletion markers can be bigger than the state that is left. IzgoN detects that and sends the state whole (`FULL_STATE`), so you never pay *more* than sending everything — but you save nothing on that sync either.
- **The first report from any node is always sent in full.** There is nothing to compare it against. On a short sample this drags the average down, correctly.
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

Version 1.1.1 — see [CHANGELOG.md](CHANGELOG.md). Built and maintained by one person.
If something is broken, open an issue and say what you sent and what you got back.
