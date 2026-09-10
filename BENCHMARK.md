# Benchmark

Every number here was produced by `benchmark.py` in this repo. The exact commands are
below — rerun them and you should land within a few percent. Where IzgoN performs badly
is included, because a benchmark that only shows the good case is worth nothing.

## What was measured

50 nodes, 100 reports each — 5,000 syncs per run. Each node reports a ten-field device
state (temperature, humidity, battery, RSSI, uptime, firmware version, mode, lat, lon,
error count), roughly 155 bytes of JSON. Between reports, each field independently has a
`--change-rate` probability of changing.

- **Without IzgoN** — every report sent in full, which is what most fleets do today.
- **With IzgoN** — bytes actually returned: `0` for an unchanged state, otherwise the
  minimal delta.

## Results

| Change rate | Unchanged reports | Without IzgoN | With IzgoN | Saved |
|---|---|---|---|---|
| 5 % | 3,093 / 5,000 | 759.2 KB | 46.5 KB | **93.9 %** |
| 20 % | 671 / 5,000 | 758.2 KB | 161.9 KB | **78.6 %** |
| 70 % | 0 / 5,000 | 758.4 KB | 548.6 KB | **27.7 %** |

### Read the third row first

At a 70 % change rate no report is ever identical to the one before it, and the saving
collapses to 27.7 %. **That is the honest boundary of this tool.** IzgoN pays off when
most reports repeat themselves. If your payloads change substantially every time, the
savings will not justify running another service — use `jsonpatch` in your own code, or
nothing at all.

The 5 % row is the case IzgoN was built for: a fleet where the device is mostly idle and
re-reports the same state. Two out of three reports come back as `NO_CHANGE`, carrying
zero payload.

### Latency and throughput

| Change rate | p50 | p95 | Throughput |
|---|---|---|---|
| 5 % | 3.5 ms | 4.9 ms | 265 syncs/s |
| 20 % | 5.2 ms | 6.6 ms | 184 syncs/s |
| 70 % | 6.4 ms | 8.1 ms | 152 syncs/s |

Single uvicorn worker, sequential client, loopback network. Throughput here is bounded by
the single-threaded benchmark client, not by IzgoN — treat these as a latency floor, not
a capacity limit. If you need a capacity number, run a concurrent client against your own
hardware.

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

## Verified inside Docker too

The numbers above were measured against a bare `uvicorn` process. Re-running the same
benchmark against the containerised stack (`izgon` + `redis:7-alpine`, 30 nodes ×
60 rounds, 5 % change rate) gave **93.1 % saved**, p50 4.5 ms — within a point of the
bare-metal figure, so containerisation costs nothing measurable here.

## Test environment

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS |
| CPU | Intel Xeon @ 2.80 GHz, 2 cores |
| Memory | 7 GB |
| Python | 3.11.15 |
| Redis | 7.0.15 |
| IzgoN | v1.0.0, single uvicorn worker |

## Measure your own payloads

These are synthetic states. The number that should decide whether you pay for IzgoN is
the one produced by **your** data. Open `benchmark.py`, replace `make_state()` with a
sample of your real reports, and rerun. If the saving is small, do not buy it.
