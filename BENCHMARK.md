# Benchmark

Every number here was produced by `benchmark.py` in this repo. The exact commands are
below — rerun them and you should land within a few percent. Where IzgoN performs badly
is included, because a benchmark that only shows the good case is worth nothing.

## Measure your own payloads first

Everything below is synthetic. The number that should decide whether you pay for IzgoN
is the one produced by **your** reports, and getting it is one command:

```bash
python3 benchmark.py --payload-file my-reports.json
```

A JSON array or JSON Lines export of what your devices actually send. Each device
replays its own real sequence in its real order; the change rate is measured from the
data, not assumed:

```
IzgoN benchmark — replaying 4,812 of your own reports from 37 device(s)
Source: my-reports.json  (device id: device_id)
Observed change rate: 6.4%  — measured from your data
```

The device id is picked up from `node_id`, `device_id`, `deviceId`, `device`, `id` or
`serial`; use `--id-field` to name a different one, and `--state-field` if each record
wraps the payload (`{"device_id": "...", "state": {...}}`). Each run uses its own
node-id namespace, so running it twice cannot inherit the first run's baselines and
inflate the result. If the saving on your data is small, do not buy this.

---

## What the synthetic runs measure

50 nodes, 100 reports each — 5,000 syncs per run. Each node reports a ten-field device
state (temperature, humidity, battery, RSSI, uptime, firmware version, mode, lat, lon,
error count), roughly 155 bytes of JSON. Between reports, each field independently has a
`--change-rate` probability of changing.

- **Without IzgoN** — every report sent in full, which is what most fleets do today.
- **With IzgoN** — bytes actually returned: `0` for an unchanged state, the minimal
  delta when that is smaller, and the whole state when a delta would be bigger.

Both sides are measured as compact JSON. One ruler on both sides is the only way the
difference between them means anything; before v1.1.0 it was two rulers, and the
figures below are correspondingly higher than the ones this file used to carry.

## Results

| Change rate | Unchanged reports | Sent whole | Without IzgoN | With IzgoN | Saved |
|---|---|---|---|---|---|
| 5 % | 3,093 / 5,000 | 50 | 759.2 KB | 43.0 KB | **94.3 %** |
| 20 % | 671 / 5,000 | 50 | 758.2 KB | 147.5 KB | **80.5 %** |
| 70 % | 0 / 5,000 | 104 | 758.4 KB | 490.4 KB | **35.3 %** |

"Sent whole" is a sync where IzgoN sent the complete state rather than a delta. At 5 %
and 20 % those 50 are simply the first report from each of the 50 nodes — there is
nothing to compare a first report against. At 70 % there are 54 more, where so much of
the payload changed at once that the delta would have been larger than the state.

### Read the third row first

At a 70 % change rate no report is ever identical to the one before it, and the saving
falls to 35.3 %. **That is the honest boundary of this tool.** IzgoN pays off when most
reports repeat themselves. If your payloads change substantially every time, a third of
your bytes is unlikely to justify running another service — use `jsonpatch` in your own
code, or nothing at all.

The 5 % row is the case IzgoN was built for: a fleet where the device is mostly idle and
re-reports the same state. Three out of five reports come back as `NO_CHANGE`, carrying
zero payload.

### Latency and throughput

| Change rate | p50 | p95 | Throughput |
|---|---|---|---|
| 5 % | 3.4 ms | 4.4 ms | 277 syncs/s |
| 20 % | 3.4 ms | 4.3 ms | 277 syncs/s |
| 70 % | 3.5 ms | 4.4 ms | 270 syncs/s |

Single uvicorn worker, sequential client, loopback network. Throughput here is bounded
by the single-threaded benchmark client, not by IzgoN — treat these as a latency floor,
not a capacity limit. If you need a capacity number, run a concurrent client against
your own hardware; a 24-thread run against this build sustained 448 req/s.

Latency no longer rises with the change rate the way it did before v1.0.1. That is the
free-tier gate fix: it used to run `COUNT(*)` over the whole event log on every single
sync, so the server got slower the longer it ran.

## Reproduce

```bash
docker compose up -d

python3 benchmark.py --nodes 50 --rounds 100 --change-rate 0.05 --seed 42
python3 benchmark.py --nodes 50 --rounds 100 --change-rate 0.20 --seed 42
python3 benchmark.py --nodes 50 --rounds 100 --change-rate 0.70 --seed 42
```

Redis was flushed between runs so each started with no baselines:

```bash
redis-cli flushall
```

Add `--price-per-mb 0.05` to print what the avoided bytes would have cost on a metered
plan.

## Verified inside Docker

Measured on v1.0.1: the same benchmark against the containerised stack (`izgon` +
`redis:7-alpine`, 30 nodes × 60 rounds, 5 % change rate) gave **93.1 % saved**, p50
4.5 ms, against **93.9 %** for the bare `uvicorn` process — within a point, so
containerisation costs nothing measurable here. The v1.1.0 accounting fix changes both
figures by the same amount; the comparison between them is what this section is for.

## Test environment

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS |
| CPU | Intel Xeon @ 2.10 GHz, 2 cores |
| Memory | 7 GB |
| Python | 3.11.15 |
| Redis | 7.0.15 |
| IzgoN | v1.1.0, single uvicorn worker |

Your hardware will differ. The saving percentage should not — it is a property of your
payloads, not of the machine.
