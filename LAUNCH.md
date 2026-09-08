# IzgoN — plan lansiranja

Uputstva su na bosanskom, tekstovi za objavu su na engleskom jer je publika međunarodna.
Redoslijed nije proizvoljan — svaki korak otključava sljedeći.

---

## Dan 1 — naplata (1 večer)

PayPal iz BiH ti ne radi. Lemon Squeezy podržava BiH i isplaćuje na bh. bankovni račun.

1. Otvori nalog na **lemonsqueezy.com**, izaberi Bosnia and Herzegovina kao zemlju.
2. Napravi Store, pa u njemu **Product**:
   - Type: *Single payment* (ne pretplata)
   - Name: `IzgoN — Commercial Licence`
   - Price: `29 USD`
   - Description: prva rečenica iz README-a, ništa više
3. U product settings uključi **"Send a custom thank-you note"** i tu napiši:
   > Thanks. Your licence key will arrive by email within 12 hours.
   Ručna isporuka je sasvim u redu za prvih dvadeset kupaca. Ne gradi webhook dok ne moraš.
4. Kopiraj **checkout link** i zalijepi ga u README na mjesto `[link goes here]`.
5. Isplata: Settings → Payouts → bankovni račun. Provjeri koji SWIFT/IBAN traže.

**Kad stigne narudžba:**

```bash
export DATAPULSE_LICENSE_SECRET="tvoj-tajni-string"
python3 issue_license.py --email kupac@example.com --order LS-1234
```

Skripta zapisuje svaku izdatu licencu u `sales.log` — to ti je i evidencija za računovođu.

> **Prije prve prodaje** izgeneriši ključ za sebe, postavi ga kao `DATAPULSE_LICENSE_KEY`,
> restartuj i provjeri da `GET /api/license` vraća `licensed: true`. Ako ne vrati,
> skripta ispisuje četiri varijante base64 kodiranja — probaj sljedeću.

---

## Dan 2 — repo (1 večer)

Repozitorij trenutno ima dva commita, jedan fajl i nijedan README. Za nekoga ko naiđe
na njega, to izgleda kao napušten projekat, bez obzira što kod radi.

Dodaj u root:

```
README.md            <- zamjenjuje prazninu
Dockerfile
docker-compose.yml
requirements.txt
.env.example
benchmark.py
issue_license.py
BENCHMARK.md         <- napraviš ga u Danu 3
LICENSE.md           <- napiši uslove: source-available, nije open source
.gitignore           <- mora sadržavati .env, sales.log, *.db
```

Zatim u GitHub UI:

- **About** → opis: `Delta-sync server for device fleets — send only what changed, and see how many bytes you saved.`
- **Topics**: `delta-sync` `iot` `telemetry` `bandwidth` `self-hosted` `fastapi` `redis` `python`
- **Releases** → napravi tag **v1.0.0**. Verzija `0.2.0` sa dva commita govori „nedovršeno".

---

## Dan 2b — jedina provjera koju nisam mogao uraditi (10 minuta)

`docker-compose.yml` je sintaksno validiran, ali **Docker build nikad nije pokrenut** —
u okruženju gdje je sve ovo pisano nije bilo Docker daemona. Sve ostalo je testirano na
živom IzgoN-u; ovo nije.

Prije nego išta objaviš, na svom laptopu:

```bash
docker compose up -d
docker compose ps          # oba servisa moraju biti healthy
curl -s http://localhost:8000/healthz
```

Ako build padne, javi grešku — vjerovatno je sitnica u `requirements.txt` ili verziji
Pythona. Ali **ne linkaj repo dok `docker compose up -d` ne prođe čisto**, jer je to
prva komanda koju će svaki posjetilac otkucati.

---

## Dan 3 — benchmark (2 sata)

**Ovo je već urađeno** — `BENCHMARK.md` u repou sadrži stvarna mjerenja sa živog
IzgoN-a, ne procjene:

| Change rate | Bez IzgoN-a | Sa IzgoN-om | Ušteda |
|---|---|---|---|
| 5 % | 759,2 KB | 46,5 KB | **93,9 %** |
| 20 % | 758,2 KB | 161,9 KB | **78,6 %** |
| 70 % | 758,4 KB | 548,6 KB | **27,7 %** |

Ponovi na svojoj mašini da potvrdiš i zamijeni tabelu svojim brojkama:

```bash
redis-cli flushall
python3 benchmark.py --nodes 50 --rounds 100 --change-rate 0.05 --seed 42
```

**Red sa 70 % ostaje u README-u.** To je onaj gdje ušteda pada na 27,7 % i gdje IzgoN
prestaje da se isplati. Izgleda kao da sam sebi radiš protiv — nije. Publika na HN-u i
r/selfhosted te testira upravo na tome hoćeš li sam reći gdje ne valja. Ako prešutiš,
neko to nađe u komentaru i tema je gotova.

---

## Dan 4 — demo koji ne spava

Render besplatni plan uspavljuje servis nakon perioda neaktivnosti. Ako demo spava kad
stigne prvi promet sa Hacker Newsa, izgubio si ga i druge šanse nema.

1. Provjeri koliko traje buđenje: otvori demo, sačekaj sat vremena, pa opet.
2. Ako je duže od 5 sekundi — **ne linkaj živi demo.** Umjesto toga snimi GIF dashboarda
   (10–15 sekundi, vidi se kako brojka uštede raste) i stavi ga na vrh README-a.
   GIF u README-u konvertuje bolje od demoa koji se učitava, ionako.

---

## Dan 5 — objave

### r/selfhosted

Pravila subreddita traže da se autor deklariše. Ne pokušavaj to zaobići.

> **Title:** I built a delta-sync server for device fleets — it sends only what changed, and tells you how many bytes you saved
>
> I run a small fleet of devices that report state every few seconds. Most reports were
> byte-for-byte identical to the previous one, and I was paying for all of them on metered
> SIMs. So I wrote a server that keeps the last known state per node, returns either
> `NO_CHANGE` or a minimal JSON delta, and logs what it saved.
>
> The diff itself is about 50 lines — nothing clever, and `jsonpatch` does the same thing
> for free. What I actually wanted was the rest: per-node baselines so devices stay
> stateless, an event log, and a dashboard that shows bytes-I-would-have-sent versus
> bytes-I-did-send. That number turned out to be the whole point.
>
> Runs with `docker compose up`. Redis for baselines, SQLite for the log, FastAPI in front.
> There's a benchmark script in the repo that uses only the standard library — run it
> against your own payload shape before you believe any number I publish. Results at
> three different change rates are in BENCHMARK.md, including the one where savings
> mostly disappear.
>
> Known limits, up front: lists are compared whole rather than element by element, it's a
> single instance with no clustering, and there's no client SDK yet — integration is a
> plain HTTP POST.
>
> Free up to 10k syncs. Beyond that it's a one-time licence — I'm the author, so treat
> this as self-promotion, but the code and the benchmark are there to check.
>
> https://github.com/izGamber/IZgoN

### Show HN

Naslov mora biti tačno u ovom obliku, HN je strog oko toga:

> **Show HN: IzgoN – delta-sync server that shows you the bandwidth you stopped paying for**

Prvi komentar (ti ga postaviš odmah nakon objave):

> Author here. This started because my devices were re-sending identical state over
> metered connections. The diffing is trivial and `jsonpatch` covers it; the parts I
> wanted were per-node baselines, an event log, and one number — bytes avoided — that I
> could show to whoever pays the data bill.
>
> Benchmark script is stdlib-only and in the repo, so you can run it against your own
> payloads instead of trusting mine. I published the change rate where the savings
> largely vanish too, because that's the honest boundary of where this is useful.
>
> Limits: lists aren't diffed element-wise, single instance, no SDK. Happy to answer
> anything.

**Kad objaviti:** utorak–četvrtak, oko 15:00–17:00 po našem vremenu (rano jutro u SAD).
Ostani za tastaturom naredna 3 sata i odgovaraj na svaki komentar. Objava bez prisutnog
autora propada.

### awesome-selfhosted

Pull request na `awesome-selfhosted/awesome-selfhosted`. Provjeri njihove uslove — traže
određenu starost projekta i dokumentaciju. Format unosa:

```
- [IzgoN](https://github.com/izGamber/IZgoN) - Delta-sync server for device fleets; sends only changed state and reports bandwidth saved. `Source available` `Python`
```

---

## Šta NE raditi prije prve prodaje

Sve ovo izgleda korisno i sve je zamka:

- Play Store — BiH ne može biti merchant, tri sedmice rada, dvanaest testera
- Client SDK za Python i JavaScript
- Batch endpoint
- Prometheus metrike
- Redizajn dashboarda
- Vlastita domena i brendiranje

Svaka od tih stavki dobija zeleno svjetlo tek kad je neko traži **poslije** što je platio.

---

## Tačka prekida

**Ako do 31.10.2026. niko nije platio 29 $, IzgoN nije proizvod nego uzorak rada.**

To nije poraz. To je najbolji tehnički dokaz koji imaš i kao takav ide u CV i u portfolio.
Ali tada prestaje ulaganje i fokus se vraća na ono što ima kupca.
