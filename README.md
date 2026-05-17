# cuepoint

![CI](https://github.com/danilvorobjov/cuepoint/actions/workflows/ci.yml/badge.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)
![Tests](https://img.shields.io/badge/tests-587_passing-green)
![Coverage](https://img.shields.io/badge/coverage-75%25+-brightgreen)
![mypy strict](https://img.shields.io/badge/mypy-strict-blue)
![Docker](https://img.shields.io/badge/docker-ready-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Multi-source ETL pipeline that scrapes electronic music events from listing platforms and venue websites, enriches artist data from three external APIs (SoundCloud, Discogs, Bandcamp), scores and ranks events using a configurable algorithm, and serves results via a FastAPI REST API or interactive HTML report.

Built to solve a real problem: finding good electronic music events across 16 cities without manually checking every listing.

---

## Tech Stack

**Core:** Python 3.12, FastAPI, httpx (async), SQLite (WAL mode)
**Data:** pandas, BeautifulSoup, lxml
**Infra:** Docker, Docker Compose, Make
**Testing:** pytest (587 tests across 40 files), mypy strict, ruff
**APIs:** GraphQL consumption, REST (Discogs, SoundCloud), web scraping (Bandcamp)
**Patterns:** ETL pipeline, registry pattern, retry with exponential backoff, incremental processing, thread-safe concurrency

---

## Engineering Highlights

- **587 tests** across 40 files — unit, integration, API endpoint, security (XSS, SSRF), concurrency, and end-to-end pipeline coverage
- **Strict type checking** — `mypy --strict` across the entire codebase with full type annotations
- **Zero Selenium** — all HTTP via async `httpx` with retry/backoff/jitter
- **Incremental processing** — SHA-256 lineup hashing skips 60-70% of enrichment work on repeat scans
- **Thread-safe concurrency** — SQLite WAL + thread-local connections + semaphore rate limiters + `ThreadPoolExecutor`
- **Production-grade API** — rate limiting, pagination, health checks, CSV export, background task execution
- **Clean architecture** — frozen dataclasses, decorator-based registry pattern, configurable scoring weights via TOML
- **Retry resilience** — all external calls wrapped with exponential backoff, jitter, and `Retry-After` header support
- **CI pipeline** — lint, typecheck, test, and security audit on every push

---

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/danilvorobjov/cuepoint.git && cd cuepoint
docker compose up --build
# API at http://localhost:8000
# Swagger UI at http://localhost:8000/docs
```

### Local

```bash
pip install -e ".[dev]"

# CLI - scan Berlin events for the next 7 days
python -m cuepoint.event_fetcher --cities berlin --days 7

# API
uvicorn cuepoint.api:app --reload --port 8000
```

### Makefile

```bash
make install    # install package + dev tools
make run        # start API server
make test       # run test suite
make lint       # check linting
```

---

## REST API

Interactive docs at `http://localhost:8000/docs` (Swagger UI).

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/scan` | Start a background scan for one or more cities |
| `GET` | `/status` | List all scans with their status |
| `GET` | `/status/{scan_id}` | Get status of a specific scan |
| `GET` | `/results/{city}` | Latest results for a city (paginated) |
| `GET` | `/results/{city}/export` | Download results as CSV |
| `GET` | `/health` | Readiness check (DB status, version) |
| `GET` | `/cities` | List available city keys |

**Features:** pagination, rate limiting (5 scans/60s per IP), persistent SQLite storage, CSV export, health checks for container orchestration.

<details>
<summary>Example requests</summary>

```bash
# start a scan
curl -X POST http://localhost:8000/scan \
  -H "Content-Type: application/json" \
  -d '{"cities": ["berlin", "london"], "days": 7}'

# check status
curl http://localhost:8000/status/abc123def456

# get results (page 1, 50 per page)
curl http://localhost:8000/results/berlin?page=1&page_size=50

# export as CSV
curl -O http://localhost:8000/results/berlin/export
```

</details>

<details>
<summary>Example response</summary>

```json
{
  "city": "Berlin",
  "event_count": 42,
  "page": 1,
  "page_size": 50,
  "total_pages": 1,
  "events": [
    {
      "title": "Klubnacht",
      "event_date": "2026-04-18",
      "venue_name": "Berghain",
      "score": 48500.0,
      "lineup_notable": 4,
      "lineup_total": 8,
      "genres": ["Techno"],
      "artists": [
        {
          "name": "Surgeon",
          "sc_followers": 52000,
          "tags": ["techno", "industrial"],
          "rising": false
        }
      ]
    }
  ]
}
```

</details>

---

## Architecture

```
src/cuepoint/
  event_fetcher.py   -- CLI entry point, pipeline orchestration, parallel runner
  api.py             -- FastAPI REST API with background scan execution
  enrichment.py      -- SC -> Discogs -> Bandcamp -> rising -> save pipeline
  scoring.py         -- filter, sort, scoring formula with configurable weights
  discovery.py       -- rising detection, artist similarity, label affinity
  db.py              -- SQLite storage (WAL mode, thread-safe, indexed)
  sc.py              -- SoundCloud API client (OAuth + fallback)
  discogs.py         -- Discogs REST API client (token auth, 60 req/min)
  bandcamp.py        -- Bandcamp scraper with semaphore rate limiting
  club_scrapers.py   -- @register_club decorator, per-room lineup parsing
  http_utils.py      -- @retry_on_failure with exponential backoff + jitter
  html_creator.py    -- Vue 3 interactive report generator
  gui.py             -- CustomTkinter desktop GUI
  following.py       -- followed artist set, URL matching
  fuzzy_match.py     -- name normalization, Levenshtein distance
  config.py          -- typed accessors from config.toml
tests/               -- 587 tests across 40 files
```

### Pipeline Flow

```
Resident Advisor (GraphQL)  ──┐
Club websites (HTTP scrape)  ──┼──► Parse lineups
                               │
           ┌───────────────────┘
           ▼
    Enrich each artist (async, cached)
    ├── SoundCloud  → followers, tags
    ├── Discogs     → have/want, styles, labels
    └── Bandcamp    → supporters, tags, releases
           │
           ▼
    Score & rank events
    ├── Platform metrics weighted sum
    ├── Genre match multipliers
    ├── Followed artist bonus
    ├── Rising detection (growth vs baseline)
    ├── Artist similarity (Jaccard overlap)
    └── Shared label + recency bonuses
           │
           ▼
    Output: HTML report / REST API / GUI
```

### Key Design Decisions

- **Thread-safe concurrency** — SQLite WAL mode with thread-local connections, per-source rate limiters with locks and semaphores, `ThreadPoolExecutor` for parallel city scans
- **Incremental scans** — lineup hash comparison (SHA-256) via SQLite snapshots skips 60-70% of enrichment work on repeat runs
- **Tiered cache TTLs** — 7 days for followed artists, 30 days for others, 14-day soft stale threshold triggers re-enrichment
- **Retry resilience** — all external API calls wrapped with exponential backoff + jitter, respects `Retry-After` on 429s
- **Registry pattern** — club scrapers use `@register_club("city")` decorator for clean extensibility
- **Frozen dataclass** — `ScanContext` replaces mutable module globals for safe parallel execution

---

## Data Storage

SQLite at `cache/cuepoint.db` (WAL mode, thread-safe):

| Table | Purpose | TTL |
|-------|---------|-----|
| `artist_urls` | Artist ID -> SC/Discogs/BC URLs | permanent |
| `artist_cache` | Full enrichment data per artist | 30d (7d for followed) |
| `found_events` | Events featuring followed artists | permanent |
| `artist_metrics_history` | Baselines for rising detection | permanent |
| `scan_events` | Lineup snapshots for incremental scans | overwritten each scan |
| `api_results` | Latest scan results per city for the API | overwritten each scan |

---

<details>
<summary>Scoring Formula</summary>

```
event_score = sum(artist_scores) + ra_genre_bonus * genre_count

artist_score = (sc_followers * genre_match / sc_weight)
             + (dc_have * genre_match / dc_weight)
             + (bc_supporters * genre_match / bc_weight)
             + followed_bonus
             + rising_bonus
             + similarity_score * similarity_weight
             + shared_label_count * shared_label_bonus
             + dc_ratio * dc_ratio_weight
             + recency_bonus * decay_factor
```

All weights configurable in `config.toml`.

</details>

<details>
<summary>Configuration</summary>

All settings in `config.toml` (defaults in `config.toml.example`):

```toml
[general]
days_ahead = 7
max_workers = 3
incremental = true

[cache]
ttl_days = 30
ttl_following_days = 7
stale_days = 14

[scoring]
sc_weight = 10
dc_weight = 5
bc_weight = 8
followed_bonus = 1000000

[genres]
filter = ["Techno", "Drum & Bass", "Drum n Bass"]
```

</details>

<details>
<summary>Supported Cities</summary>

amsterdam, athens, barcelona, berlin, birmingham, bristol, buenos aires, lisbon, london, madrid, osaka, paris, tbilisi, tokyo, warsaw, wuppertal

</details>

<details>
<summary>Club Scrapers</summary>

| City | Club | Rooms |
|------|------|-------|
| Berlin | Berghain | Berghain, Panorama Bar, Saule, Kantine |
| Berlin | Tresor | Tresor, Globus, Aurora Bar |
| Tbilisi | Bassiani | MainRoom, SecondRoom |
| Tbilisi | Khidi | -- |
| Wuppertal | Openground | FREIFELD, ANNEX |

Club events are deduplicated against the listing platform by venue + date matching.

</details>

---

## Testing

```bash
make test                           # run all 587 tests
pytest tests/ --cov=src/cuepoint    # with coverage
```

40 test files covering: config validation, SQLite storage (CRUD + batch ops + migrations), HTTP retry logic (sync + async), SoundCloud auth/circuit breaker, Discogs/Bandcamp API mocking, club scraper parsing, enrichment pipeline, scoring with discovery signals, genre filtering, fuzzy matching, event fetching/parsing, HTML helpers, following detection, payload builders, pipeline stats, FastAPI endpoints (health, pagination, rate limiting, export), security (XSS injection, SSRF prevention), concurrency under load, lazy initialization, and full end-to-end pipeline tests.

---

## Deploy

The project includes a `Dockerfile` and `Procfile` for one-click deploy on any container platform. The `$PORT` env var is respected automatically.

```bash
# Railway
railway up

# Fly.io
fly launch
```

---

## License

MIT
