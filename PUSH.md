# Kako objaviti ovo na GitHub

Commit je već napravljen i tag `v1.0.0` je postavljen. Nemam pristup tvom GitHub nalogu,
pa zadnji korak — push — moraš ti.

## Varijanta A — imaš repo lokalno klonan

Raspakuj `izgon-v1.0.0.zip`, pa iz tog foldera:

```bash
git remote -v                    # provjeri da pokazuje na izGamber/IZgoN
git push origin main
git push origin v1.0.0
```

## Varijanta B — nemaš ga lokalno

```bash
git clone https://github.com/izGamber/IZgoN.git
cd IZgoN
```

Prekopiraj sve fajlove iz zipa preko (uključujući izmijenjeni `app.py`), pa:

```bash
git add -A
git commit -m "v1.0.0: docs, Docker packaging, measured benchmark, licence tooling"
git tag -a v1.0.0 -m "IzgoN v1.0.0"
git push origin main
git push origin v1.0.0
```

## Ako traži lozinku

GitHub više ne prihvata lozinku za push. Treba ti Personal Access Token:

1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token, scope: **repo**
3. Kod `git push`, kao username upiši `izGamber`, a kao password **zalijepi token**

Token je lozinka — ne stavljaj ga ni u jedan fajl u repou.

## Poslije pusha, u GitHub UI

1. **About** (zupčanik gore desno) → opis:
   `Delta-sync server for device fleets — send only what changed, and see how many bytes you saved.`
2. **Topics**: `delta-sync` `iot` `telemetry` `bandwidth` `self-hosted` `fastapi` `redis` `python`
3. **Releases** → Draft a new release → izaberi tag `v1.0.0` → naslov `v1.0.0` →
   u opis prekopiraj tabelu iz `BENCHMARK.md`.

---

## Šta je promijenjeno u `app.py`

Samo četiri linije. Ništa u logici.

| Linija | Prije | Poslije |
|---|---|---|
| 2 | docstring spominje PayPal invoice | spominje v1.0.0 i offline licencu |
| 216 | `DATAPULSE_FREE_TIER_LIMIT` default `"100"` | `"10000"` |
| 271 | `PURCHASE_URL` hardkodiran PayPal invoice link | čita env, default vodi na README |
| 284 | `version="0.2.0"` | `version="1.0.0"` |

`python3 -m py_compile app.py` prolazi, a server je pokrenut i testiran nakon izmjene.

## Šta je testirano, a šta nije

**Testirano na živom IzgoN-u** — server pokrenut sa Redisom, 15.000 sync poziva:

- `benchmark.py` na tri change rate-a; brojke u `BENCHMARK.md` su stvarne
- oblik zahtjeva i odgovora (`{"state": {...}}`, `X-API-Key`, `NO_CHANGE` sa `bytes_sent: 0`)
- `issue_license.py` generiše ključ i piše `sales.log`
- `app.py` se kompajlira i radi nakon izmjena

**Testirano i u Dockeru** (10.09.2026) — slika izgrađena na `python:3.12-slim`,
pokrenuta uz `redis:7-alpine`:

- pinovane verzije iz `requirements.txt` se instaliraju bez konflikta
- `/healthz` vraća `{"redis_reachable":true}`, sync radi kroz kontejner
- kontejner se izvršava kao non-root (`uid=10001 izgon`)
- benchmark kroz Docker: **93,1 % ušteda**, p50 4,5 ms — isto kao bez Dockera

Usput popravljeno: healthcheck je koristio `localhost` kroz obični `urlopen`, pa bi
na mašini iza korporativnog proxyja kontejner zauvijek pisao `unhealthy` iako servis
radi. Sada ignoriše proxy iz okruženja.

Svejedno pokreni `docker compose up -d` jednom kod sebe da potvrdiš na svom Dockeru —
to je prva komanda koju će svaki posjetilac otkucati.
