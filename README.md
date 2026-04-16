# techno_scan

Multi-source ETL pipeline that scrapes electronic music events from RA.co and club websites, enriches artist data from three external APIs (SoundCloud, Discogs, Bandcamp), scores and ranks events using a configurable algorithm, and serves results via a FastAPI REST API or interactive HTML report.

175 tests. GitHub Actions CI. Zero Selenium — all HTTP via `requests`.

<!-- TODO: add screenshot of HTML report here -->
<!-- ![HTML Report](docs/screenshot.png) -->

---

## Quick start

```bash
git clone <repo-url> && cd techno_scan
pip install -r requirements.txt

# CLI - scan Berlin events for the next 7 days
cd lib/parser
python event_fetcher.py --cities berlin --days 7

# API - start the server
uvicorn api:app --port 8000
# then visit http://localhost:8000/docs for Swagger UI
```

---

## What it does

1. **Extracts** events from RA.co's GraphQL API + 5 club websites (Berghain, Tresor, Bassiani, Khidi, Openground)
2. **Enriches** each artist from three sources:
   - **SoundCloud** — genre tags, follower count (API with auto-extracted `client_id`)
   - **Discogs** — release styles, have/want counts, ratings, labels (REST API)
   - **Bandcamp** — tags, supporter counts, latest release date (scraping)
3. **Filters** by configurable genres (default: Techno, Drum & Bass) or followed artists
4. **Scores** using a weighted formula: SC followers + Discogs popularity + Bandcamp supporters + genre match multipliers + discovery bonuses (rising artists, similarity, shared labels, release recency)
5. **Serves** results via REST API or generates interactive HTML reports per city

---

## REST API

Start the server:

```bash
cd lib/parser && uvicorn api:app --reload --port 8000
```

Interactive docs at `http://localhost:8000/docs`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/scan` | Start a background scan for one or more cities |
| `GET` | `/status` | List all scans with their status |
| `GET` | `/status/{scan_id}` | Get status of a specific scan |
| `GET` | `/results/{city}` | Latest results for a city as JSON |
| `GET` | `/cities` | List available city keys |

### Examples

```bash
# start a scan
curl -X POST http://localhost:8000/scan \
  -H "Content-Type: application/json" \
  -d '{"cities": ["berlin", "london"], "days": 7}'

# check status
curl http://localhost:8000/status/abc123def456

# get results
curl http://localhost:8000/results/berlin
```

Response from `/results/berlin`:
```json
{
  "city": "Berlin",
  "event_count": 42,
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

---

## CLI

```bash
cd lib/parser

# single city
python event_fetcher.py --cities berlin --days 7

# multiple cities in parallel
python event_fetcher.py --cities amsterdam berlin london --parallel 3

# force full re-scan (ignore incremental cache)
python event_fetcher.py --cities lisbon --full
```

Output: interactive HTML report saved to `output/`.

---

## Desktop GUI

Double-click `launch_gui.bat` or run `python lib/parser/gui.py`.

3-tab layout: **Scan** (city selection, date range, progress bar, live log), **Results** (per-city cards with report links), **Settings** (genre filters, scoring weights, cache TTLs — saves to `config.toml`).

---

## Architecture

```
lib/parser/
  event_fetcher.py   — CLI entry point, pipeline orchestration, parallel runner
  api.py             — FastAPI REST API with background scan execution
  enrichment.py      — SC -> Discogs -> Bandcamp -> rising -> save pipeline
  scoring.py         — filter, sort, scoring formula with configurable weights
  discovery.py       — rising detection, artist similarity, label affinity
  db.py              — SQLite storage (WAL mode, thread-safe, indexed)
  sc.py              — SoundCloud API client
  discogs.py         — Discogs REST API client
  bandcamp.py        — Bandcamp scraper with semaphore rate limiting
  club_scrapers.py   — @register_club decorator, per-room lineup parsing
  http_utils.py      — @retry_on_failure with exponential backoff + jitter
  html_creator.py    — Vue 3 interactive report generator
  gui.py             — CustomTkinter desktop GUI
  fuzzy_match.py     — name normalization, Levenshtein distance
  tag_utils.py       — shared JSON tag parsing
  stats.py           — pipeline metrics dataclass
  config.py          — typed accessors from config.toml
  tests/             — 175 tests across 17 files
```

### Key design decisions

- **Thread-safe concurrency** — SQLite WAL mode with thread-local connections, per-source rate limiters with locks and semaphores, `ThreadPoolExecutor` for parallel city scans
- **Incremental scans** — lineup hash comparison (SHA-256) via SQLite snapshots skips 60-70% of enrichment work on repeat runs
- **Tiered cache TTLs** — 7 days for followed artists, 30 days for others, 14-day soft stale threshold triggers re-enrichment
- **Retry resilience** — all external API calls wrapped with exponential backoff + jitter, respects `Retry-After` on 429s
- **Registry pattern** — club scrapers use `@register_club("city")` decorator for clean extensibility
- **Frozen dataclass** — `ScanContext` replaces mutable module globals for safe parallel execution

---

## Data storage

SQLite at `cache/techno_scan.db` (WAL mode, thread-safe):

| Table | Purpose | TTL |
|-------|---------|-----|
| `artist_urls` | RA artist ID -> SC/Discogs/BC URLs | permanent |
| `artist_cache` | full enrichment data per artist | 30d (7d for followed) |
| `found_events` | events featuring followed artists | permanent |
| `artist_metrics_history` | SC/DC baselines for rising detection | permanent |
| `scan_events` | lineup snapshots for incremental scans | overwritten each scan |

---

## Scoring formula

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

---

## Configuration

All settings in `config.toml`:

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

---

## Supported cities

amsterdam, athens, barcelona, berlin, birmingham, bristol, buenos aires, lisbon, london, madrid, osaka, paris, tbilisi, tokyo, warsaw, wuppertal

---

## Club scrapers

| City | Club | Rooms |
|------|------|-------|
| Berlin | Berghain | Berghain, Panorama Bar, Saule, Kantine |
| Berlin | Tresor | Tresor, Globus, Aurora Bar |
| Tbilisi | Bassiani | MainRoom, SecondRoom |
| Tbilisi | Khidi | -- |
| Wuppertal | Openground | FREIFELD, ANNEX |

Club events are deduplicated against RA by venue + date matching.

---

## Testing

```bash
pytest                                          # run all 175 tests
pytest --cov=lib/parser lib/parser/tests/       # with coverage
pytest lib/parser/tests/test_scoring.py -v      # specific file
```

17 test files covering: config, SQLite storage, HTTP retry logic, SoundCloud/Discogs/Bandcamp API mocking, club scraper parsing, enrichment pipeline, scoring with discovery signals, genre filtering, fuzzy matching, event parsing, HTML helpers, following detection, FastAPI endpoints.

---

## Setup

**Requirements:** Python 3.11+ (tested on 3.13)

```bash
pip install -r requirements.txt
```

**Discogs API token** (optional, increases rate limit from 25 to 60 req/min):

```bash
echo "YOUR_TOKEN" > lib/parser/.discogs_token
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `requests` | all HTTP (RA, SC, Discogs, Bandcamp, club scrapers) |
| `fastapi` + `uvicorn` | REST API |
| `pandas` | event data processing |
| `beautifulsoup4` + `lxml` | HTML parsing |
| `loguru` | structured logging |
| `pytest` | test framework |

---

## License

MIT
