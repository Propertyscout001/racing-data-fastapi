# Racing data FastAPI backend (Australian odds API proxy)

A FastAPI service that sits in front of the [PuntersEdge](https://puntersedge.online/api?utm_source=racing-data-fastapi&utm_medium=readme)
Australian racing and sports odds API and gives your own frontend a small, cached,
stable contract to call.

It is the reference answer to "I have an odds API key, how do I build a backend on
it" — one shared HTTP connection pool, a TTL cache with single-flight so a hundred
concurrent users cost one upstream call, the upstream key held server-side where the
browser can never see it, and RFC 9457 `problem+json` errors that distinguish "come
back in 37 seconds" from "there is no point coming back". Racing data is Australian
and New Zealand thoroughbred, harness and greyhound; sports are AFL, NRL, NBA and the
rest of the keys listed below.

It runs with **no API key at all** by proxying the keyless PuntersEdge demo endpoints,
so you can start it and see real Australian race prices before deciding whether to
register.

## Run it without an API key

```bash
git clone https://github.com/Propertyscout001/racing-data-fastapi
cd racing-data-fastapi
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Then, in another terminal:

```bash
curl -s 'localhost:8000/races/next?country=AU&limit=2'
```

No key, no signup, no config file. Open <http://127.0.0.1:8000/> for a small HTML
board rendered from the same service, and <http://127.0.0.1:8000/docs> for this
service's own OpenAPI documentation.

Docker, if you prefer:

```bash
docker compose up --build
```

## Real output

Captured 2026-09-14 23:29 UTC on macOS, Python 3.9.6, against the live API. The full
session — keyless mode, keyed mode, and every error path — is in
[`docs/output.txt`](docs/output.txt).

Verbatim, with `[... elided ...]` marking where lines were cut for length:

```
$ curl -s 'http://127.0.0.1:8000/races/next?country=AU&limit=2'
{
    "source": {
        "mode": "demo",
        "cached": false,
        "cache_age_seconds": 0.0,
        "upstream_path": "/demo/racing/next-to-go",
        "credits_charged": 0,
        "note": "Free sandbox sample (truncated). Get a free API key for full data: https://puntersedge.online/api?utm_source=demo_api&utm_medium=sandbox"
    },
    "count": 2,
    "races": [
        {
            "race_id": "e79e50b1-bc25-4d1d-976c-6b34d0b6426c",
            "venue": "Angle Park",
            "race_number": 1,
            "category": "greyhound",
            "country": "AU",
            "start_time": "2026-09-15T01:38:00Z",
            "runners": [
                {
                    "name": "Alia Rose",
                    "number": 1,
                    "best_price": 14.0,
                    "best_bookmaker": "tab",
                    "prices": [
                        {
                            "bookmaker": "tab",
                            "win_price": 14.0,
                            "place_price": null,
                            "age_seconds": null,
                            "source_url": "https://www.tab.com.au/racing/2026-09-15/ANGLE-PARK/ANG/G/1"
                        },
                        {
                            "bookmaker": "pointsbetau",
                            "win_price": 14.0,
                            "place_price": null,
                            "age_seconds": null,
                            "source_url": "https://pointsbet.com.au/racing/Greyhound/AUS/Angle-Park/race/115173313"
                        },
[... elided: the third price, four more runners, and the second race ...]
```

`place_price` and `age_seconds` are null here because the keyless demo sample omits
them. With a key they are populated.

Six requests to this service during that capture produced three calls upstream:

```
--- what the upstream actually saw for those 6 proxy requests ---
HTTP Request: GET https://api.puntersedge.online/v1/demo/racing/next-to-go "HTTP/1.1 200 OK"
HTTP Request: GET https://api.puntersedge.online/v1/demo/racing/next-to-go "HTTP/1.1 200 OK"
HTTP Request: GET https://api.puntersedge.online/v1/demo/best-odds?sport=afl "HTTP/1.1 200 OK"
```

## With a free key

Set `PE_API_KEY` and the same routes serve the full feed instead of the truncated
sample. In the capture above, one greyhound race went from 5 runners with 3
bookmakers each in demo mode to 6 runners with 10 bookmakers each in keyed mode, and
`place_price` and `age_seconds` stopped being null.

```bash
export PE_API_KEY=your_key_here
uvicorn app.main:app --port 8000
```

A free key is 1,500 credits a month with no credit card:
<https://puntersedge.online/api?utm_source=racing-data-fastapi&utm_medium=readme>

What each route costs upstream, per uncached call:

| Route | Upstream endpoint | Credits | Keyless? |
|---|---|---|---|
| `GET /health` | `/v1/demo/racing/next-to-go` | 0 | yes |
| `GET /races/next` | `/v1/racing/next-to-go` | 2 | yes, via `/v1/demo/racing/next-to-go` |
| `GET /races/{race_id}/prices` | *(reads the `/races/next` snapshot)* | 0 | yes |
| `GET /best-odds/{sport}` | `/v1/best-odds/{sport_key}` | 3 | yes, via `/v1/demo/best-odds` |
| `GET /races/{race_id}/history` | `/v1/racing/price-history` | 5 | no, needs a key |

`source.credits_charged` on every response tells you what that specific request cost,
which is `0` on a cache hit.

### Why the cache is the point

`/v1/racing/next-to-go` costs 2 credits. Proxy it one-to-one and 750 page loads spends
a free month — about 25 a day. Put a 20 second TTL in front of it and upstream cost
stops tracking your traffic and starts tracking the clock: 3 calls a minute, whether
one person is watching or ten thousand.

The TTL alone is not enough, because on a cold key every concurrent request checks the
cache before any of them has finished filling it. One `asyncio.Lock` per key collapses
that stampede. Measured against the live API with a real key:

```
$ python3 tools/demo_single_flight.py 25
url                 /races/next?country=AU&limit=34
concurrent requests 25
wall time           556 ms
200 responses       25
credits charged     2  (sum of source.credits_charged across all 25)

misses                1 -> 2    (+1)
coalesced             0 -> 24   (+24)
hits                  0 -> 0    (+0)
upstream_calls        1 -> 2    (+1)
credits_spent         2 -> 4    (+2)
```

Twenty-five simultaneous requests, one upstream call, two credits.

## How it works

**Two upstream shapes, one contract.** The keyless demo endpoints return an envelope,
`{"demo": true, "note": ..., "races": [...]}`. The keyed endpoints return a **bare
array**. Anything supporting both has to handle both, and `app/normalise.py` does it
once so no route and no browser ever has to.

**`country=AU` is not the default upstream.** `/v1/racing/next-to-go` serves every
country it has. Omit the filter and Japanese and Hong Kong races arrive mixed into
whatever your UI is labelling "Australian racing". This service defaults
`DEFAULT_COUNTRY=AU` and you have to pass `country=any` to turn it off.

**One `httpx.AsyncClient` for the process lifetime.** Built in the lifespan handler,
never per request. Measured from this machine on 2026-09-15 with
`tools/measure_connection_reuse.py`, over 5 cold and 8 warm calls:

```
cold (new client per call, n=5): median  187.9 ms  min  173.2  max  234.6
warm (reused connection,  n=8): median   47.8 ms  min   41.6  max   52.5
```

Your absolute numbers depend on where you are relative to the origin; the ratio is the
part that transfers. `Accept-Encoding: gzip` is set on the same client — the demo
next-to-go payload measured 7,324 bytes uncompressed against 1,531 bytes on the wire.

**The key never leaves the server.** It is read from the environment in
`app/config.py`, attached to outbound requests in `app/upstream.py`, and appears in no
response body, no log line and no OpenAPI document. Callers of this service
authenticate to it however you like, or not at all — they have no PuntersEdge key and
need none. The bundled HTML page at `/` exists to make that visible: open the network
tab and the only host it talks to is your own.

**402 and 429 are not the same failure.** Both mean the upstream said no, and
collapsing them into a generic 502 destroys the only information a caller can act on.

| Upstream | This service returns | `Retry-After` | `retryable` |
|---|---|---|---|
| 402 credits exhausted | `402` | not sent | `false` |
| 429 rate limited | `429` | forwarded from upstream | `true` |
| 401 / 403 | `502` | not sent | `false` |
| 5xx | `502` | not sent | `true` |
| timeout | `504` | not sent | `true` |

401 becomes 502 deliberately: the caller has no key to fix, so returning 401 to them
would be a lie about whose problem it is. Reproduce the whole table with no upstream
account using the bundled stub, which is stdlib-only:

```bash
python3 tools/stub_upstream.py 402 &
PE_API_KEY=not-a-real-key PE_UPSTREAM_BASE=http://127.0.0.1:8099/v1 \
  uvicorn app.main:app --port 8000
curl -i 'localhost:8000/races/next?limit=2'
```

**Nothing is retried automatically.** A silent retry on a price endpoint can return a
quote recorded before a move. Failures surface; the caller decides.

**Failed fetches are not cached**, so one bad minute upstream does not become twenty
seconds of stored error for every caller. `/health` reports `upstream_calls` and
`upstream_errors` separately, and `misses == upstream_calls + upstream_errors`.

**Typos are refused locally.** There is no `horse-racing` sport key — racing lives
under `/races/*`. `/best-odds/horse-racing` returns 400 with the valid keys listed,
without spending a credit to be told no upstream.

## Repository layout

```
app/
  config.py      env settings, the only place the key is read
  upstream.py    the single AsyncClient, and the status mapping table
  cache.py       TTL + single-flight, and the credit arithmetic
  normalise.py   demo envelope vs bare array, folded into one shape
  models.py      response models -- narrower than upstream on purpose
  routers/       races, best_odds, health
  static/        the HTML board served at /
tools/
  stub_upstream.py            a failing upstream, for the error paths
  demo_single_flight.py       proves N concurrent = 1 upstream call
  measure_connection_reuse.py reproduces the latency numbers above
  capture_output.sh           regenerates docs/output.txt
docs/
  output.txt      the real captured session
  openapi.json    this service's own spec
  demo-page.html  a static render of the HTML board
```

## Limitations

- **Not a general proxy.** Five routes over four upstream endpoints. PuntersEdge
  publishes 60 paths; results, movers, acceptances, jockey and trainer stats,
  arbitrage and the CSV exports are all absent. Add them by copying a router.
- **In-process cache only.** A `dict` in one process. Run two replicas and you get two
  caches and twice the upstream calls. For a real deployment put Redis behind the
  `TTLCache` interface — the single-flight lock would need to become a distributed
  lock too.
- **No authentication on this service.** Anyone who can reach it can spend your
  credits. Put it behind your own auth, or on a private network, before exposing it.
- **No cache-key bound on memory.** Distinct query combinations create distinct keys
  and nothing evicts early. Fine for a fixed set of routes, wrong if you let callers
  pass arbitrary parameters.
- **Demo mode ignores your filters upstream.** `/v1/demo/racing/next-to-go` takes no
  parameters and returns a fixed 3-race Australian sample. `country`, `limit` and
  `category` are applied locally to whatever that sample contained, so a demo-mode
  request for harness races can legitimately return nothing.
- **`credits_remaining` is usually `null`.** `/v1/usage` returned HTTP 500 for the key
  used during this build while every data endpoint answered normally, so the field is
  best-effort and cannot turn the health check red. It may populate with other keys.
- **Price history needs a key and a finished race.** `/v1/racing/price-history` returns
  404 for a race that has not jumped — history is written once the market closes.
  Verified 2026-09-15 across greyhound, harness and thoroughbred races.
- **`/races/{race_id}/prices` only covers currently quoted races**, because it reads
  the same snapshot `/races/next` fetched. Once a race jumps it leaves that window;
  use `/races/{race_id}/history`.
- **No tests.** The build machine had no pytest. `tools/` holds runnable checks
  instead, which is not the same thing.
- **The Docker path is unverified.** The `Dockerfile` and `docker-compose.yml` were
  written but never built — no Docker daemon was running on the build machine. Every
  captured result in `docs/output.txt` comes from uvicorn run directly. Treat the
  container as untested.
- **Bookmaker coverage is whatever the upstream serves**, and it changes. This README
  quotes no coverage count; the live list is at
  <https://puntersedge.online/coverage-report?utm_source=racing-data-fastapi&utm_medium=readme>.
  Betfair and Pinnacle are not included.

## Related

PuntersEdge guides:

- [Getting started](https://puntersedge.online/developers/getting-started?utm_source=racing-data-fastapi&utm_medium=docs)
- [API reference](https://puntersedge.online/developers/api-reference?utm_source=racing-data-fastapi&utm_medium=docs)
- [Australian odds API Python guide](https://puntersedge.online/blog/australian-odds-api-python-guide?utm_source=racing-data-fastapi&utm_medium=docs)
- [Betting model data pipeline in Python](https://puntersedge.online/blog/betting-model-data-pipeline-python?utm_source=racing-data-fastapi&utm_medium=docs)

Sibling repositories:

- [puntersedge-python](https://github.com/Propertyscout001/puntersedge-python) — the official Python SDK
- [puntersedge-node](https://github.com/Propertyscout001/puntersedge-node) — the Node SDK
- [puntersedge-mcp](https://github.com/Propertyscout001/puntersedge-mcp) — MCP server for LLM tooling
- [puntersedge-examples](https://github.com/Propertyscout001/puntersedge-examples) — standalone single-file Python scripts

## Sport keys

`afl` `aflw` `nrl` `nrlw` `nba` `wnba` `nfl` `ncaaf` `mlb` `nhl` `mma` `tennis_atp`
`tennis_wta` `cricket_test` `cricket_other` `rugby_union` `super_league` `soccer_epl`
`soccer_other` `basketball_other`

Racing is not a sport key. It lives under `/races/*`, covering Australian
thoroughbred, harness and greyhound racing, and New Zealand thoroughbred and harness.

## Licence

MIT. See [LICENSE](LICENSE).

---
18+ only. Gambling can be addictive — please gamble responsibly.
Gambling Help: 1800 858 858 · https://www.gambleaware.nsw.gov.au
This repository is a developer example for reading an odds data feed. It is not betting
advice, it places no bets and it holds no bookmaker credentials.
