# techno_scan

A personal tool that fetches upcoming electronic music events from [Resident Advisor](https://ra.co) for multiple cities, enriches artist data from SoundCloud, Discogs, and Bandcamp, filters for configurable genres or followed artists, ranks events by a scoring algorithm, and outputs an interactive HTML report.

100% Selenium-free. Available as a **desktop GUI** or **CLI**.

---

## What it does

1. **Fetches events** from RA.co's GraphQL API + club websites directly
2. **Enriches artists** from three sources (no browser needed):
   - **SoundCloud** — genre tags, follower count (via SC API with auto-extracted `client_id`)
   - **Discogs** — release styles, have/want counts, average ratings, labels (REST API with token auth)
   - **Bandcamp** — tags, supporter counts, latest release date (page scraping, ~3 req/sec)
3. **Filters** — keeps events matching configurable genres (default: Techno, Drum & Bass) or featuring followed artists
4. **Scores & ranks** — weighted formula combining SC followers, Discogs popularity, Bandcamp supporters, genre relevance, discovery signals (rising, similarity, shared labels, rarity, recency), and a large bonus for followed artists
5. **Discovers** — flags rising artists, similar artists, shared labels, and lineup strength
6. **Retries** — exponential backoff with jitter on all API calls (RA, SC, Discogs, Bandcamp)
6. **Outputs** an interactive HTML report per city with search, sort, filter, and .ics calendar export

---

## Features

### Interactive HTML Report (Vue.js)
- **Vue 3 SPA** — reactive rendering via Composition API, no build step, Vue runtime inlined for 100% offline use
- Modern dark theme with CSS variables, glassmorphism toolbar with backdrop blur
- Full-text search across artists, venues, promoters
- "Followed only" toggle
- **Genre multi-select dropdown** — filter events by one or more genre tags
- **Table / Card view toggle** — switch between table and card layouts
- Click any column header to sort (time, attenders, strength, etc.)
- **Expandable artist tags** — click to reveal SC/Discogs/Bandcamp tags per artist
- **Keyboard shortcuts** — `/` to focus search, `f` to toggle followed-only, `v` to switch view mode
- Followed artists highlighted with tinted blue rows
- Genre pills — colored badges by genre family (techno, D&B, house, ambient, industrial)
- Lineup formatting — per-artist dividers with styled floor labels, compact stats with middot separators
- Zebra striping for easier row scanning
- Ticket prices with currency-aware display (EUR, GBP, GEL, JPY, ARS, PLN)
- Smooth hover transitions on rows, links, images, and buttons
- Custom styled scrollbar matching the dark theme
- **Mobile responsive** — card layout under 768px with full-width flyers, labeled sections, and hidden low-priority columns

### Desktop GUI (3-tab layout)
- Professional dark theme (neutral grays + teal accent)
- **Scan tab** — city selection, date range, run/cancel, progress bar, live log
- **Results tab** — completed city cards with event count, followed count, "Open Report" / "Open All" buttons; auto-switches here after scan completes
- **Settings tab** — genre filter editor, scoring weight spinboxes, cache TTL spinboxes, notable thresholds, Save button that writes to `config.toml`
- **Structured progress callbacks** — progress bar driven by pipeline phase callbacks, not log-message regex
- **Elapsed timer** updating every second
- **City progress** label ("City 2/5: Berlin")
- **Cancel button** — stops after current city
- Select All / None for cities
- Date presets: 1/2/3 weekends, 1 month
- Live log output with Consolas monospace font
- SoundCloud profile save + sync following

### Discovery & Intelligence
- **Lineup Strength** — N/M notable artists shown with a gradient bar per event
- **Rising Detection** — fire badge on artists whose SC followers or Discogs want-count grew significantly since last enrichment
- **Artist Similarity** — purple "~ FollowedName" hint when a non-followed artist shares genre tags with someone you follow (Jaccard similarity >= 30%)
- **Label Discovery** — green label badges when a non-followed artist shares Discogs labels with your followed artists

### Calendar Export
- Per-event .ics download (calendar icon next to each title)
- Bulk "Export .ics" button for all visible/filtered events
- Floor-grouped lineup in the event description

### Club Scrapers
Direct website scraping with per-room/floor lineup separation:

| City | Club | Rooms |
|---|---|---|
| Berlin | Berghain | Berghain, Panorama Bar, Saule, Kantine |
| Berlin | Tresor | Tresor, Globus, Aurora Bar |
| Tbilisi | Bassiani | MainRoom, SecondRoom |
| Tbilisi | Khidi | — |
| Wuppertal | Openground | FREIFELD, ANNEX |

Club events are deduplicated against RA (substring venue matching), with flyers and attending counts inherited from RA where available.

### Performance
- **Parallel city scans** — `--parallel N` flag runs multiple cities concurrently via thread pool; shared rate limiters (Discogs, SC, BC) naturally serialize; 30-40% wall-clock reduction on multi-city scans
- **Incremental scans** — after the first scan, only enriches artists from new or changed events (lineup hash comparison via SQLite snapshot); second run of the same city typically skips 60-70% of enrichment work
- **Retry resilience** — all API calls wrapped with exponential backoff + jitter decorator (`http_utils.py`); respects `Retry-After` header on 429s
- **Phased enrichment pipeline** — SC (3 workers), Discogs (3 workers, rate-limited), Bandcamp (5 workers, 3 concurrent requests)
- **True Bandcamp parallelism** — semaphore-based rate limiting at ~3 req/sec (not single-threaded)
- **RA Bandcamp URL passthrough** — captures `bandcamp` URL from RA GraphQL, eliminating ~50% of Bandcamp name-search requests
- **Progress logging** — Discogs and Bandcamp report progress every 10 artists
- **Enrichment decay** — stale cache entries (> 14 days) are automatically re-enriched when the artist appears in a scan
- **Early Discogs termination** — stops paginating releases once enough masters are found
- **Smart Discogs resolution** — skips doomed slug API calls for +-encoded URLs
- **Fuzzy artist name matching** — strips parenthetical suffixes, Levenshtein fallback for merging RA and club data
- **Event deduplication** — RA API results deduped by event ID before processing

### Architecture
- **Modular design** — pipeline split into focused modules: `enrichment.py` (cache + 3-source pipeline), `scoring.py` (filter/sort/scoring), `discovery.py` (rising/similarity/labels), `fuzzy_match.py` (name matching)
- **ScanContext** — frozen dataclass replaces mutable globals, enabling safe parallel city execution
- **Thread-safe** — SQLite WAL mode with thread-local connections, per-source rate limiters with locks and semaphores

---

## Supported cities (16)

amsterdam, athens, barcelona, berlin, birmingham, bristol, bsas (Buenos Aires), lisbon, london, madrid, osaka, paris, tbilisi, tokyo, warsaw, wuppertal

Configure in `config.toml` under `[cities]`.

---

## Project structure

```
techno_scan/
├── config.toml                # All configurable values (cities, genres, scoring, cache TTLs)
├── pyproject.toml             # pytest, ruff, mypy configuration
├── lib/parser/
│   ├── event_fetcher.py       # CLI entry point — pipeline orchestration, ScanContext, parallel runner
│   ├── enrichment.py          # Artist enrichment pipeline (cache, SC/Discogs/BC, batch phased)
│   ├── scoring.py             # Filtering, scoring formula, notable detection
│   ├── discovery.py           # Rising detection, artist similarity, label affinity
│   ├── fuzzy_match.py         # Name normalization, Levenshtein distance, RA↔club merging
│   ├── http_utils.py          # Retry decorator with exponential backoff + jitter
│   ├── tag_utils.py           # Shared JSON tag parsing (eliminates redundant json.loads)
│   ├── stats.py               # ScanStats dataclass — pipeline metrics + HTML footer
│   ├── gui.py                 # Desktop GUI (CustomTkinter, 3-tab layout)
│   ├── html_creator.py        # Vue.js-powered interactive HTML report generator
│   ├── db.py                  # SQLite storage (WAL mode, thread-safe)
│   ├── config.py              # Typed config accessors from config.toml
│   ├── sc.py                  # SoundCloud API (followers, tags)
│   ├── discogs.py             # Discogs REST API (styles, have/want, ratings, labels)
│   ├── bandcamp.py            # Bandcamp scraping (tags, supporters, semaphore rate-limited)
│   ├── club_scrapers.py       # Club website scrapers with @register_club decorator
│   ├── following.py           # Followed SC artists set + event recording
│   ├── fetch_following.py     # Utility: sync following list from SC profile
│   ├── generic.py             # Global constants (BASE_PATH, RA URL, OUTPUT_PATH)
│   ├── payloads.py            # GraphQL query builders for RA
│   ├── flyers.py              # Flyer image URL extraction
│   ├── vendor/
│   │   └── vue.global.prod.js # Vue 3 production build (inlined into reports)
│   ├── tests/                 # 159 tests across 16 files
│   │   ├── conftest.py        # Shared fixtures (mock_config, tmp_db, sample data)
│   │   ├── test_config.py     # Config accessor tests
│   │   ├── test_following.py  # is_following tests
│   │   ├── test_parse_events.py # Event parsing tests
│   │   ├── test_html_functions.py # HTML helper function tests
│   │   ├── test_artist_tags.py # Artist tag extraction tests
│   │   ├── test_scoring.py    # Scoring algorithm + discovery signal tests
│   │   ├── test_filter.py     # Genre filter tests
│   │   ├── test_fuzzy_match.py # Fuzzy name matching tests
│   │   ├── test_db.py         # SQLite storage tests (incl. incremental snapshots)
│   │   ├── test_http_utils.py # Retry logic, backoff, Retry-After header tests
│   │   ├── test_sc.py         # SoundCloud API mock tests
│   │   ├── test_discogs.py    # Discogs API mock tests
│   │   ├── test_bandcamp.py   # Bandcamp scraping mock tests
│   │   ├── test_club_scrapers.py # Club scraper helper + HTML/JSON parsing tests
│   │   └── test_enrichment.py # Enrichment pipeline mock tests
│   └── cache/
│       └── techno_scan.db     # SQLite database (auto-created)
├── external_libs/
│   └── resident-advisor-events-scraper-main/
│       └── graphql_query_template.json
├── output/                    # Generated HTML reports
├── requirements.txt
├── launch_gui.bat             # Double-click launcher for the GUI
├── create_shortcut.ps1        # Creates a Desktop shortcut
├── playlist_parser.py         # Standalone: parse DJ tracklist
└── cue_encoding.py            # Standalone: convert .cue files to UTF-8
```

---

## Setup

### Requirements

- Python 3.11+ (tested on 3.13)
- No browser needed — all HTTP via `requests`

### Install

```bash
git clone <repo-url>
cd techno_scan

python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

### Discogs API token (recommended)

Create a token at https://www.discogs.com/settings/developers and save it:

```bash
echo "YOUR_TOKEN" > lib/parser/.discogs_token
```

Without a token: 25 req/min. With token: 60 req/min.

---

## Usage

### GUI

Double-click `launch_gui.bat` or create a Desktop shortcut:

```powershell
powershell -ExecutionPolicy Bypass -File create_shortcut.ps1
```

| Section | What it does |
|---|---|
| **SoundCloud Profile** | Paste your SC URL, click **Save** to persist, click **Sync Following** to fetch your full following list |
| **Cities** | Tick one or more cities (use All/None buttons) |
| **Date Range** | Pick start date, set days, or use presets (1/2/3 weekends, 1 month) |
| **Run Scan** | Runs the pipeline with live progress bar, phase labels, and elapsed timer |
| **Cancel** | Stops after the current city finishes |
| **Log** | Live log output with phase progress (SC, Discogs, Bandcamp) |

### CLI

```bash
cd lib/parser

# Default: Amsterdam, today, 7 days
python event_fetcher.py

# Specific city and date range
python event_fetcher.py --cities berlin --start 2026-03-20 --days 14

# Multiple cities
python event_fetcher.py --cities amsterdam berlin london --days 9

# Multiple cities in parallel (3 at a time)
python event_fetcher.py --cities amsterdam berlin london lisbon --parallel 3

# Force full re-scan (ignore incremental cache)
python event_fetcher.py --cities lisbon --full
```

---

## Configuration

All configurable values live in `config.toml`:

```toml
[general]
days_ahead = 7
ra_request_delay = 0.1
max_workers = 3
incremental = true             # skip enrichment for unchanged events on repeat scans

[cache]
ttl_days = 30              # cache TTL for regular artists
ttl_following_days = 7     # cache TTL for followed artists
stale_days = 14            # re-enrich stale artists appearing in current scan

[scoring]
sc_weight = 10             # divisor for SC follower count
dc_weight = 5              # divisor for Discogs have count
bc_weight = 8              # divisor for Bandcamp supporter count
ra_genre_bonus = 5000      # bonus per RA techno/DnB genre tag
followed_bonus = 1000000   # score bonus for followed artists
lineup_sc_threshold = 1000 # SC followers above this = "notable" artist
lineup_dc_threshold = 50   # DC have count above this = "notable"
lineup_bc_threshold = 30   # BC supporters above this = "notable"
rising_bonus = 3000        # flat bonus for rising artists
similarity_weight = 30     # multiplier for similarity score (0-100)
shared_label_bonus = 1500  # bonus per shared label with followed artists
dc_ratio_weight = 80       # multiplier for Discogs want/have ratio
recency_bonus = 800        # max bonus for recent Bandcamp release (decays over 12mo)

[genres]
filter = ["Techno", "Drum & Bass", "Drum n Bass"]

[discovery]
rising_sc_pct = 20         # % SC follower growth to flag as rising
rising_dc_pct = 30         # % DC want growth to flag as rising

[discogs]
max_masters = 15

[bandcamp]
max_albums = 5
```

---

## Scoring algorithm

Each event is scored by summing across all artists:

```
event_score = sum(artist_scores) + ra_genre_bonus * techno_genre_count

artist_score = (sc_followers * genre_match / sc_weight)
             + (dc_have * genre_match / dc_weight)
             + (bc_supporters * genre_match / bc_weight)
             + followed_bonus (if followed)
             + rising_bonus (if rising)
             + similarity_score * similarity_weight (if similar to followed)
             + shared_label_count * shared_label_bonus
             + dc_ratio * dc_ratio_weight (rarity signal)
             + recency_bonus * decay (linear decay over 12 months from bc_latest_release)
```

Where `genre_match` = count of filter genres in that artist's tags. Discovery signals add 1,000-5,000 range — enough to break ties and lift discovery candidates without overpowering the core formula.

---

## Data storage

SQLite database at `cache/techno_scan.db` (WAL mode, thread-safe):

| Table | Purpose | TTL |
|---|---|---|
| `artist_urls` | RA artist ID → SC/Discogs/BC URLs | Permanent |
| `artist_cache` | Full enrichment data per artist | 30 days (7 for followed) |
| `found_events` | Log of events featuring followed artists | Permanent |
| `artist_metrics_history` | SC followers + DC want baselines for rising detection | Permanent |
| `scan_events` | Per-city event lineup snapshots for incremental scans | Overwritten each scan |

Auto-migrates from legacy JSON/CSV files on first run.

---

## Syncing your following list

**GUI:** paste your SC profile URL and click **Sync Following**.

**CLI:**
```bash
cd lib/parser
python fetch_following.py https://soundcloud.com/your-username
```

---

## Testing

159 tests across 16 test files covering config, following, event parsing, HTML helpers, artist tags, scoring (including discovery signals), filtering, fuzzy matching, SQLite storage, and mocked API tests for all external services (SoundCloud, Discogs, Bandcamp, club scrapers, HTTP retry logic, enrichment pipeline).

```bash
# Run all tests from the project root
pytest

# With coverage
pytest --cov=lib/parser lib/parser/tests/

# Run a specific test file
pytest lib/parser/tests/test_scoring.py -v
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests` | All HTTP (RA GraphQL, SC API, Discogs API, club scrapers, Bandcamp) |
| `pandas` | Event data as DataFrame |
| `numpy` | Genre counting, aggregation |
| `loguru` | Structured logging |
| `beautifulsoup4` + `lxml` | HTML parsing for club scrapers and Bandcamp |
| `wakepy` | Prevent system sleep during long runs |
| `customtkinter` | Modern dark-theme GUI framework |
| `tkcalendar` | Calendar date picker widget |
| `pytest` + `pytest-cov` | Test framework and coverage reporting |
