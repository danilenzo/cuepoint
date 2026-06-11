# Scoring Feedback Loop — Design

Date: 2026-06-11
Status: implemented

## Problem

Scoring weights are static config (`config.toml [scoring]`). No mechanism learns from which
recommended events were actually attended. README lists this as a known gap.

## Goal

One-click "Went / Skipped" feedback on each event card in the HTML report. Feedback adjusts
future scoring automatically — signal weights tune toward what predicts attendance, and
genres/artists of attended events accumulate boosts. Base `config.toml` is never modified;
the learned layer is separate, clamped, and resettable.

## Decisions (settled during brainstorming)

| Question | Decision |
|---|---|
| Report viewing mode | Both `file://` and via running API — capture must work offline |
| Feedback signal | Two buttons: **Went** / **Skipped** (one verdict per event, re-click overwrites) |
| Learning mechanism | Layered: statistical weight multipliers on `_score_breakdown` + count-based genre/artist boosts |
| Apply mode | Auto with guardrails: clamps, minimum data thresholds, separate resettable layer |
| ML library | None — pure-stats update. sklearn upgrade path stays open (feedback data accumulates regardless) |

## Architecture

New module `src/cuepoint/learning.py` owns all feedback math. Touch points:

| File | Change |
|---|---|
| `learning.py` (new) | `compute_adjustments() -> LearnedAdjustments`, feedback row parsing, multiplier/boost math |
| `db.py` | Migration 4: `event_feedback` table; accessors `save_feedback()`, `get_all_feedback()`, `clear_feedback()` |
| `templates/report.html` | Went/Skipped buttons per card; localStorage queue; sync JS (POST + retry on load) |
| `html_creator.py` | Add `"id"` to `_artist_to_dict` output (one line). Embed API base URL constant. Import genre normalization from `tag_utils` |
| `tag_utils.py` | Receives `_normalize_genre` + `_GENRE_ALIASES` + `_GENRE_BLACKLIST` moved from `html_creator.py` (learning and scoring need them; presentation module is the wrong owner) |
| `api.py` | `POST /feedback` (single or batch upsert), `GET /feedback/stats`; add `"null"` to CORS `allow_origins` |
| `scoring.py` | Apply multipliers inside `_add` + on the direct `ra_genre` add; add `genre_affinity` / `artist_affinity` breakdown entries |
| `event_fetcher.py` | Compute adjustments at shared scan entry (so both CLI and API paths get them); `--learning-stats`, `--reset-learning` CLI flags |
| `config.py` / `config.toml.example` | `[learning]` section with typed accessors |

## Data model

Migration 4 via the existing numbered migration system in `db.py`:

```sql
CREATE TABLE event_feedback (
    event_id    TEXT NOT NULL,
    city        TEXT NOT NULL,
    verdict     TEXT NOT NULL CHECK (verdict IN ('went', 'skipped')),
    event_title TEXT NOT NULL DEFAULT '',
    breakdown   TEXT NOT NULL,   -- {signal_key: value} JSON, reconstructed from report's scoreBreakdown
    genres      TEXT NOT NULL,   -- normalized genre name list, JSON
    artist_ids  TEXT NOT NULL,   -- RA artist id list, JSON
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (event_id)
);
```

Rows are self-contained snapshots — they survive event expiry, cache cleanup, and re-scans.
No `learned_weights` table: adjustments are recomputed from feedback rows at scan start
(cheap at personal-tool volume, always consistent, reset = delete rows).

`PRIMARY KEY (event_id)`: RA event ids are globally unique; club scraper ids are synthetic.
Implementation must verify club event id format doesn't collide with RA numeric ids.

## Capture flow

```
card click (Went/Skipped)
  → localStorage["cuepoint_feedback"][event_id] = {verdict, breakdown, genres, artistIds, city, title, ts, synced: false}
  → attempt POST {API_BASE}/feedback
      → 2xx: mark synced (entry kept, not deleted — verdict survives report regeneration)
      → failure (file:// offline, API down): stays queued
report load
  → retry POST for all unsynced entries (batch)
  → restore button states from localStorage
```

- `breakdown` is reconstructed client-side from the already-embedded `scoreBreakdown`
  (`[{key, value}]` → `{key: value}`). Rounding to 0.1 and dropped zero entries are
  irrelevant for learning. No new raw embed needed.
- API base URL embedded at report generation from `[learning] api_base` config
  (default `http://localhost:8000`).
- CORS: report opened via `file://` sends `Origin: null` — `api.py` must add `"null"` to
  `allow_origins`. Trade-off: any local file may call the API. Accepted for a
  localhost-only personal tool; noted here deliberately.
- JS kept minimal — all learning logic is server-side Python.

## Learning math

`LearnedAdjustments` — frozen dataclass: `multipliers: dict[str, float]`,
`genre_boosts: dict[str, float]`, `artist_boosts: dict[str, float]`.

### Signal weight multipliers

- Features = breakdown keys: `sc_followers`, `dc_have`, `bc_supporters`, `rising`,
  `similarity`, `shared_labels`, `dc_ratio`, `recency`, `ra_genre`.
- `followed` is **excluded** — the 1M bonus encodes intent ("always surface followed
  artists") and would swamp share normalization. Its multiplier is fixed at 1.0.
- Per feedback row: normalize breakdown to shares — `share_k = value_k / sum(non-followed values)`.
  Rows whose non-followed sum is 0 are skipped (zero-total guard).
- Per signal: `w_k` = mean share across *went* rows, `s_k` = mean share across *skipped* rows.
- `m_k = clamp((w_k + ε) / (s_k + ε), multiplier_min, multiplier_max)` with ε = 0.01.
  Signal absent everywhere → ε/ε = 1.0, neutral.
- Cold start: multipliers stay 1.0 until ≥ `min_feedback` total rows AND ≥ `min_per_class`
  rows per verdict.

### Genre boosts (active immediately, no threshold)

- Per normalized genre: `net = went_count − skipped_count`, clamped to ±`genre_boost_cap`.
- Event bonus: `net × genre_boost_unit` per matching genre in the event's normalized genre
  list, added once per event per genre. Normalization shared via `tag_utils` so feedback
  genres always match scan-time genres.

### Artist boosts

- Any artist appearing in a *went* lineup → flat `artist_boost` on future appearances,
  keyed by RA artist id. Positive-only: no skip penalty (skipping rarely means
  "dislike this artist" — too noisy).

## Scoring integration

- `compute_adjustments()` runs once at the shared scan entry point, before the
  `ThreadPoolExecutor` starts (frozen dataclass → thread-safe under `--parallel`).
  Result module-cached; `scoring.py` reads it.
- Multipliers applied to *contributions*, not config values — `_add(key, base_val * m_k)`.
  This matters: `sc_weight` etc. are **divisors** in the scoring formula; multiplying
  config values would invert semantics.
- Two application points: inside `_add` (8 signals) and the direct event-level `ra_genre`
  add in `_score_row` (bypasses `_add`).
- New breakdown entries `genre_affinity` and `artist_affinity` flow into the report's
  score-breakdown display — ranking stays explainable.
- Import direction: `scoring → learning → {db, tag_utils, config}`. No cycle; `learning`
  never imports `scoring` (breakdown key list duplicated as a constant or shared via `types.py`).

## Configuration

```toml
[learning]
enabled = true
api_base = "http://localhost:8000"
min_feedback = 10
min_per_class = 3
multiplier_min = 0.5
multiplier_max = 2.0
genre_boost_unit = 500
genre_boost_cap = 3
artist_boost = 2000
```

`enabled = false` → all adjustments neutral, byte-identical scoring to today.

## API

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/feedback` | Single object or batch list. Upsert by event_id. Validation: verdict whitelist, non-empty event_id, numeric breakdown values, ≤16KB payload, ≤100 items per batch → 422 otherwise. Rate limit 60/min (reuse existing limiter pattern) |
| `GET` | `/feedback/stats` | Counts per verdict, current multipliers with explanation, top genre/artist boosts |

## CLI

- `--learning-stats` — print multipliers, boosts, feedback counts, and why (active/cold-start).
- `--reset-learning` — delete all `event_feedback` rows after confirmation prompt.

## Error handling

- `compute_adjustments()` can never break a scan: any exception → log warning, return
  neutral adjustments. Malformed feedback rows (bad JSON) skipped per-row.
- Failed POSTs keep localStorage queue entries; retried on every report load.
- Synced entries retained in localStorage so button states persist across report regenerations.

## Testing

Mocked pytest, matching existing patterns:

- `test_learning.py` — multiplier math against hand-computed expectations, clamps,
  cold-start thresholds, zero-total guard, genre net/cap, artist boosts, neutral-when-disabled,
  malformed-row skip.
- `test_db_feedback.py` — migration 4, upsert semantics, accessors.
- `test_api_feedback.py` — POST single/batch, validation 422s, stats endpoint, CORS
  `null`-origin response headers.
- `test_scoring_learning.py` — end-to-end: feedback rows in temp DB → `sort_df` ranking
  shifts as expected; `enabled = false` → output identical to baseline.
- Report JS is untestable in pytest — kept minimal by design; logic lives server-side.

## Out of scope (explicit)

- No sklearn / real ML model (upgrade path open later — data accumulates regardless).
- No feedback decay/windowing in v1.
- No skip-penalty for artists.
- No GUI (CustomTkinter) integration in v1 — report + API + CLI only.

## Implementation deviations

1. No migration 4 — `db.init_db()` runs `CREATE TABLE IF NOT EXISTS` on every process
   start, which covers new tables for fresh and existing DBs alike; the numbered
   migration system is only needed for altering existing tables.
2. Adjustments are computed inside `sort_df()` (shared by CLI and API paths) instead of
   at a cached "scan entry" hook — O(feedback rows) per sort is trivial at personal
   volume and removes cache-invalidation complexity.
3. No explicit 16KB byte cap on `POST /feedback` — Pydantic field constraints plus the
   100-item batch cap bound the payload tighter than a byte check.
