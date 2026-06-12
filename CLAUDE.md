# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A personal tool that fetches upcoming electronic music events from Resident Advisor (RA.co) for multiple cities, enriches each event's artist data from SoundCloud, Discogs, and Bandcamp, filters for configurable genres or followed artists, ranks them by a scoring algorithm, and writes an interactive HTML report to `output/`.

## Running

```bash
# CLI
python -m cuepoint.event_fetcher --cities berlin amsterdam --days 7
python -m cuepoint.event_fetcher --cities berlin --days 7 --parallel 3

# API
uvicorn cuepoint.api:app --reload --port 8000

# Docker
docker compose up --build
```

There is also a GUI (`python -m cuepoint.gui`) for selecting cities and date ranges.

Output HTML is written to the path defined in `generic.py` (`OUTPUT_PATH`).

## Testing

```bash
python -m pytest tests/ -q
```

Tests across 26 test files. Linting via `ruff check src/` and formatting via `ruff format src/`.

## Architecture

All core logic lives in `src/cuepoint/`:

| File | Role |
|---|---|
| `event_fetcher.py` | Entry point + CLI. Orchestrates fetch → enrich → filter → sort → render. `ScanContext` frozen dataclass, `--parallel N` flag. |
| `api.py` | FastAPI REST API. POST `/scan`, GET `/status`, `/results/{city}`, `/cities`. Background thread execution. |
| `enrichment.py` | `_run_enrichment_phases()` shared SC→Discogs→Bandcamp→rising→save pipeline. Batch + club enrichment entry points. |
| `scoring.py` | `filter_df()`, `sort_df()`, `_is_notable()`. Scoring formula with configurable weights. |
| `learning.py` | `compute_adjustments()` — feedback-driven signal multipliers + genre/artist boosts from `event_feedback` table. Never raises; neutral on failure. |
| `discovery.py` | `check_rising()`, `compute_similarity()`, `compute_label_affinity()`. |
| `fuzzy_match.py` | `_norm_artist_name()`, `_levenshtein()`, `_find_ra_match()`, `_merge_ra_into_stub()`. |
| `http_utils.py` | `@retry_on_failure` decorator with exponential backoff + jitter + Retry-After header support. |
| `tag_utils.py` | `parse_artist_tags()`, `parse_artist_tag_set()`, `count_genre_matches()`. Shared JSON tag parsing. |
| `stats.py` | `ScanStats` dataclass — thread-safe pipeline counters, timing, `to_html_footer()`. |
| `generic.py` | Global constants: `RA`, `URL`, `CITY_URL`, `OUTPUT_PATH`, `BASE_PATH`. |
| `config.py` | Loads `config.toml` from project root. Typed accessors for all configurable values. |
| `db.py` | Unified SQLite storage (WAL mode, thread-safe). Tables: `artist_urls`, `artist_cache`, `found_events`, `scan_events`, `artist_metrics`. |
| `payloads.py` | GraphQL query builders for RA's `/graphql` endpoint. |
| `sc.py` | SoundCloud API — extracts `client_id` from JS bundles, fetches follower counts and genre tags. |
| `discogs.py` | Discogs REST API — token auth (60 req/min), fetches styles, have/want, ratings from masters/releases. |
| `bandcamp.py` | Bandcamp scraper — searches by artist name, scrapes tags/supporters from album pages. |
| `club_scrapers.py` | `@register_club("city")` decorator pattern. Openground (Wuppertal), Khidi + Bassiani (Tbilisi), Berghain + Tresor (Berlin). |
| `following.py` | Followed SoundCloud URL slugs. `is_following(url)`, `record()`/`load_found()` via SQLite. |
| `fetch_following.py` | Standalone script to refresh `FOLLOWING` set from a SoundCloud profile's following list. |
| `html_creator.py` | Vue 3 interactive HTML report: dark theme, search/filter/sort, genre pills, .ics export, stats footer. |
| `gui.py` | CustomTkinter 3-tab GUI (Scan, Results, Settings) with progress callbacks. |
| `flyers.py` | Extracts flyer image URL from event dict. |

Configuration lives in `config.toml` at the project root (cities, genres, scoring weights, cache TTLs, worker count).

## Key design notes

- **100% Selenium-free** — async HTTP via `httpx.AsyncClient`, sync `requests` only in `fetch_following.py`.
- **Modular pipeline**: `event_fetcher.py` orchestrates, delegates to `enrichment.py` (data), `scoring.py` (ranking), `discovery.py` (signals).
- **City selection**: Via CLI `--cities` arg, GUI checkboxes, or `config.toml` `[cities]` section.
- **Parallel city scans**: `--parallel N` CLI flag, `asyncio.gather` + `Semaphore`.
- **Followed artists**: Maintained as a Python set in `following.py`. Update with `fetch_following.py <profile_url>`.
- **Scoring**: `sort_df()` uses SC followers, DC have/want/ratio, BC supporters, genre-match multipliers, plus bonuses for followed artists, rising detection, similarity, shared labels, and release recency.
- **Genre filter**: Configurable via `config.toml [genres].filter`. Events with no RA genre tags pass unconditionally.
- **Cache**: SQLite at `cache/cuepoint.db`. Tiered TTL: 7 days for followed artists, 30 days for others (configurable). SQL-based cleanup.
- **Incremental scans**: Lineup hash comparison via `scan_events` table — only re-enriches changed lineups.
- **Retry resilience**: `@retry_on_failure` decorator on all external API calls (RA, SC, Discogs, Bandcamp, club sites).
- **Club scrapers**: `@register_club("city")` decorator + registry. Produce event dicts with `_prefilled_artists_info` key.
- **Discogs auth**: Token from `.discogs_token` file or `DISCOGS_TOKEN` env var. Without token: 25 req/min.
- **Feedback loop**: Went/Skipped buttons in HTML report → localStorage queue → `POST /feedback`. `learning.py` computes clamped weight multipliers (needs ≥10 rows, ≥3/verdict) + genre/artist boosts, applied in `sort_df()`. `--learning-stats`, `--reset-learning` CLI flags. `[learning]` config section.
