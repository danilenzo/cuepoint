# Scoring Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Went/Skipped feedback buttons in the HTML report that automatically tune scoring — signal weight multipliers learned from score breakdowns, plus genre/artist boosts.

**Architecture:** New `learning.py` computes a frozen `LearnedAdjustments` from feedback rows in SQLite; `sort_df()` calls it once per sort and applies multipliers/boosts. Report JS queues feedback in localStorage and syncs to `POST /feedback` when the API is reachable.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Vue 3 (embedded report), pytest.

**Spec:** `docs/superpowers/specs/2026-06-11-feedback-loop-design.md`

**Three deliberate deviations from spec (verified against code):**
1. *No migration 4.* `db.init_db()` runs `CREATE TABLE IF NOT EXISTS` for all tables on every process start (`_get_conn` lazy init). Adding `event_feedback` to `init_db` covers fresh and existing DBs. The numbered migration system is only for altering existing tables.
2. *Adjustments computed inside `sort_df()`,* not at a "shared scan entry" with module caching. `sort_df` is called by both CLI and API paths; computation is O(feedback rows) — trivial at personal volume. No cache, no invalidation, always fresh, thread-safe (each call gets its own frozen instance).
3. *No explicit 16KB byte cap on POST /feedback.* Pydantic field constraints (string max_lengths, list max_lengths, batch cap 100) bound the payload tighter than a byte check. YAGNI.

**Conventions (from existing code/tests):**
- Tests use fixtures from `tests/conftest.py`: `tmp_db` (temp SQLite), `mock_config` (monkeypatched `cfg._cfg`), `sample_df`, `_make_event_row`.
- Run tests: `python -m pytest tests/<file> -q` from repo root.
- Lint after each task: `ruff check src/` and `ruff format src/`. mypy strict is CI-enforced: annotate everything.
- Commit messages: conventional commits (`feat:`, `refactor:`, `test:`).

---

### Task 1: Move genre normalization from html_creator to tag_utils

Learning and scoring need genre normalization; it currently lives in the presentation module (`html_creator.py:45-87` — `_GENRE_BLACKLIST`, `_GENRE_ALIASES`, `_normalize_genre`).

**Files:**
- Modify: `src/cuepoint/tag_utils.py` (add `GENRE_BLACKLIST`, `GENRE_ALIASES`, `normalize_genre`)
- Modify: `src/cuepoint/html_creator.py:45-87` (delete the three definitions, import from tag_utils)
- Test: `tests/test_tag_utils_genres.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tag_utils_genres.py`:

```python
"""Tests for genre normalization in tag_utils (moved from html_creator)."""

from cuepoint.tag_utils import normalize_genre


class TestNormalizeGenre:
    def test_alias_maps_to_canonical(self):
        assert normalize_genre("drum n bass") == "Drum & Bass"
        assert normalize_genre("dnb") == "Drum & Bass"
        assert normalize_genre("deep techno") == "Techno"

    def test_blacklisted_returns_none(self):
        assert normalize_genre("electronic") is None
        assert normalize_genre("Music") is None

    def test_title_cases_unknown(self):
        assert normalize_genre("hard trance") == "Hard Trance"

    def test_rejects_empty_long_and_non_latin(self):
        assert normalize_genre("") is None
        assert normalize_genre("x" * 31) is None
        assert normalize_genre("テクノ") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tag_utils_genres.py -q`
Expected: FAIL with `ImportError: cannot import name 'normalize_genre'`

- [ ] **Step 3: Move the code**

In `src/cuepoint/tag_utils.py`, add at module level (after existing imports; add `import re` if not present):

```python
GENRE_BLACKLIST = {
    "electronic",
    "music",
    "dance",
    "club",
    "other",
    "experimental",
    "alternative",
    "indie",
    "pop",
    "rock",
    "hip-hop",
    "hip hop",
}

GENRE_ALIASES = {
    "drum n bass": "Drum & Bass",
    "drum and bass": "Drum & Bass",
    "dnb": "Drum & Bass",
    "d&b": "Drum & Bass",
    "deep techno": "Techno",
    "hard techno": "Hard Techno",
    "detroit techno": "Detroit Techno",
}


def normalize_genre(name: str) -> str | None:
    """Lowercase, apply alias map, filter filler tags and non-genre strings."""
    stripped = name.strip()
    if not stripped or len(stripped) > 30:
        return None
    # Drop tags with no Latin letters (Japanese, Chinese, Arabic, etc.)
    if not re.search(r"[a-zA-Z]", stripped):
        return None
    low = stripped.lower()
    if low in GENRE_BLACKLIST:
        return None
    canonical = GENRE_ALIASES.get(low)
    if canonical:
        return canonical
    return stripped.title()
```

In `src/cuepoint/html_creator.py`: delete `_GENRE_BLACKLIST` (lines 45-58), `_GENRE_ALIASES` (lines 60-68), and `_normalize_genre` (lines 73-87). Change the import on line 13:

```python
from .tag_utils import normalize_genre, parse_artist_tags
```

Then rename the one remaining call site inside `_collect_genre_counts` from `_normalize_genre(g)` to `normalize_genre(g)`.

- [ ] **Step 4: Run tests to verify pass + no regression**

Run: `python -m pytest tests/test_tag_utils_genres.py tests/ -q`
Expected: all PASS (existing html_creator tests confirm behavior preserved)

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/tag_utils.py src/cuepoint/html_creator.py tests/test_tag_utils_genres.py
git commit -m "refactor: move genre normalization to tag_utils for shared use"
```

---

### Task 2: Config accessors for [learning]

**Files:**
- Modify: `src/cuepoint/config.py` (append accessors)
- Modify: `config.toml.example` (append section)
- Test: `tests/test_config_learning.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_learning.py`:

```python
"""Tests for [learning] config accessors."""

from cuepoint import config as cfg


class TestLearningDefaults:
    def test_defaults_without_section(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {})
        assert cfg.learning_enabled() is True
        assert cfg.learning_api_base() == "http://localhost:8000"
        assert cfg.learning_min_feedback() == 10
        assert cfg.learning_min_per_class() == 3
        assert cfg.learning_multiplier_min() == 0.5
        assert cfg.learning_multiplier_max() == 2.0
        assert cfg.learning_genre_boost_unit() == 500
        assert cfg.learning_genre_boost_cap() == 3
        assert cfg.learning_artist_boost() == 2000

    def test_reads_configured_values(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"learning": {"enabled": False, "artist_boost": 99}})
        assert cfg.learning_enabled() is False
        assert cfg.learning_artist_boost() == 99
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_learning.py -q`
Expected: FAIL with `AttributeError: module 'cuepoint.config' has no attribute 'learning_enabled'`

- [ ] **Step 3: Implement accessors**

Append to `src/cuepoint/config.py`:

```python
# -- Learning (feedback loop) --
def learning_enabled() -> bool:
    return _get("learning", "enabled", True)


def learning_api_base() -> str:
    return _get("learning", "api_base", "http://localhost:8000")


def learning_min_feedback() -> int:
    return _get("learning", "min_feedback", 10)


def learning_min_per_class() -> int:
    return _get("learning", "min_per_class", 3)


def learning_multiplier_min() -> float:
    return _get("learning", "multiplier_min", 0.5)


def learning_multiplier_max() -> float:
    return _get("learning", "multiplier_max", 2.0)


def learning_genre_boost_unit() -> int:
    return _get("learning", "genre_boost_unit", 500)


def learning_genre_boost_cap() -> int:
    return _get("learning", "genre_boost_cap", 3)


def learning_artist_boost() -> int:
    return _get("learning", "artist_boost", 2000)
```

Append to `config.toml.example`:

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

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_learning.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/config.py config.toml.example tests/test_config_learning.py
git commit -m "feat: [learning] config section with typed accessors"
```

---

### Task 3: event_feedback table + db accessors

**Files:**
- Modify: `src/cuepoint/db.py` (table in `init_db` script, new accessor section)
- Test: `tests/test_db_feedback.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_feedback.py`:

```python
"""Tests for event_feedback storage."""

from cuepoint import db as store


class TestFeedbackStorage:
    def test_save_and_get(self, tmp_db):
        store.save_feedback(
            "evt-1",
            "Berlin",
            "went",
            event_title="Techno Night",
            breakdown={"sc_followers": 1200.5, "ra_genre": 5000},
            genres=["Techno", "Hard Techno"],
            artist_ids=["a-1", "a-2"],
        )
        rows = store.get_all_feedback()
        assert len(rows) == 1
        row = rows[0]
        assert row["event_id"] == "evt-1"
        assert row["verdict"] == "went"
        assert row["breakdown"]["sc_followers"] == 1200.5
        assert row["genres"] == ["Techno", "Hard Techno"]
        assert row["artist_ids"] == ["a-1", "a-2"]

    def test_upsert_overwrites_verdict(self, tmp_db):
        store.save_feedback("evt-1", "Berlin", "went")
        store.save_feedback("evt-1", "Berlin", "skipped")
        rows = store.get_all_feedback()
        assert len(rows) == 1
        assert rows[0]["verdict"] == "skipped"

    def test_invalid_verdict_rejected(self, tmp_db):
        import sqlite3

        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            store.save_feedback("evt-1", "Berlin", "maybe")

    def test_malformed_json_row_skipped(self, tmp_db):
        store.save_feedback("evt-ok", "Berlin", "went")
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO event_feedback (event_id, city, verdict, breakdown, genres, artist_ids, recorded_at) "
            "VALUES ('evt-bad', 'Berlin', 'went', '{not json', '[]', '[]', '2026-01-01')"
        )
        conn.commit()
        rows = store.get_all_feedback()
        assert [r["event_id"] for r in rows] == ["evt-ok"]

    def test_count_and_clear(self, tmp_db):
        store.save_feedback("evt-1", "Berlin", "went")
        store.save_feedback("evt-2", "Berlin", "skipped")
        store.save_feedback("evt-3", "Berlin", "went")
        assert store.count_feedback() == {"went": 2, "skipped": 1}
        assert store.clear_feedback() == 3
        assert store.get_all_feedback() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_feedback.py -q`
Expected: FAIL with `AttributeError: module 'cuepoint.db' has no attribute 'save_feedback'`

- [ ] **Step 3: Implement table + accessors**

In `src/cuepoint/db.py`, add to the `init_db()` executescript (before the `schema_version` table):

```sql
        CREATE TABLE IF NOT EXISTS event_feedback (
            event_id    TEXT NOT NULL,
            city        TEXT NOT NULL DEFAULT '',
            verdict     TEXT NOT NULL CHECK (verdict IN ('went', 'skipped')),
            event_title TEXT NOT NULL DEFAULT '',
            breakdown   TEXT NOT NULL DEFAULT '{}',
            genres      TEXT NOT NULL DEFAULT '[]',
            artist_ids  TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (event_id)
        );
```

Add a new section after the "Found events" section:

```python
# ---------------------------------------------------------------------------
# Event feedback (scoring feedback loop)
# ---------------------------------------------------------------------------


def save_feedback(
    event_id: str,
    city: str,
    verdict: str,
    *,
    event_title: str = "",
    breakdown: dict[str, float] | None = None,
    genres: list[str] | None = None,
    artist_ids: list[str] | None = None,
) -> None:
    """Upsert one feedback verdict. Verdict is enforced by a CHECK constraint."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO event_feedback "
        "(event_id, city, verdict, event_title, breakdown, genres, artist_ids, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(event_id),
            city,
            verdict,
            event_title,
            json.dumps(breakdown or {}, ensure_ascii=False),
            json.dumps(genres or [], ensure_ascii=False),
            json.dumps(artist_ids or [], ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def get_all_feedback() -> list[dict[str, Any]]:
    """Return all feedback rows with JSON fields parsed. Malformed rows are skipped."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT event_id, city, verdict, event_title, breakdown, genres, artist_ids, recorded_at "
        "FROM event_feedback"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append(
                {
                    "event_id": row["event_id"],
                    "city": row["city"],
                    "verdict": row["verdict"],
                    "event_title": row["event_title"],
                    "breakdown": json.loads(row["breakdown"]),
                    "genres": json.loads(row["genres"]),
                    "artist_ids": json.loads(row["artist_ids"]),
                    "recorded_at": row["recorded_at"],
                }
            )
        except (json.JSONDecodeError, TypeError) as e:
            logger.debug(f"Skipping malformed feedback row {row['event_id']}: {e}")
    return out


def count_feedback() -> dict[str, int]:
    """Return {verdict: count}."""
    conn = _get_conn()
    rows = conn.execute("SELECT verdict, COUNT(*) AS c FROM event_feedback GROUP BY verdict").fetchall()
    return {row["verdict"]: row["c"] for row in rows}


def clear_feedback() -> int:
    """Delete all feedback rows. Returns number deleted."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM event_feedback")
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db_feedback.py tests/ -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/db.py tests/test_db_feedback.py
git commit -m "feat: event_feedback table and accessors"
```

---

### Task 4: learning.py — adjustments computation

**Files:**
- Create: `src/cuepoint/learning.py`
- Test: `tests/test_learning.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_learning.py`:

```python
"""Tests for feedback-driven learning adjustments."""

from cuepoint import config as cfg
from cuepoint import db as store
from cuepoint.learning import (
    TUNABLE_SIGNALS,
    LearnedAdjustments,
    compute_adjustments,
)

LEARNING_CFG = {
    "learning": {
        "enabled": True,
        "min_feedback": 4,
        "min_per_class": 2,
        "multiplier_min": 0.5,
        "multiplier_max": 2.0,
        "genre_boost_unit": 500,
        "genre_boost_cap": 3,
        "artist_boost": 2000,
    }
}


def _seed(verdict, breakdown, genres=None, artist_ids=None, eid=None):
    _seed.n = getattr(_seed, "n", 0) + 1
    store.save_feedback(
        eid or f"evt-{_seed.n}",
        "Berlin",
        verdict,
        breakdown=breakdown,
        genres=genres or [],
        artist_ids=artist_ids or [],
    )


class TestMultipliers:
    def test_neutral_below_min_feedback(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {"rising": 100.0})
        adj = compute_adjustments()
        assert adj.multipliers == {}
        assert adj.multiplier("rising") == 1.0

    def test_neutral_below_min_per_class(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        for _ in range(4):
            _seed("went", {"rising": 100.0})
        assert compute_adjustments().multipliers == {}

    def test_signal_predicting_attendance_gets_boosted(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        # went events dominated by rising; skipped dominated by sc_followers
        _seed("went", {"rising": 80.0, "sc_followers": 20.0})
        _seed("went", {"rising": 80.0, "sc_followers": 20.0})
        _seed("skipped", {"rising": 20.0, "sc_followers": 80.0})
        _seed("skipped", {"rising": 20.0, "sc_followers": 80.0})
        adj = compute_adjustments()
        # rising: shares went=0.8 skipped=0.2 -> (0.81)/(0.21) = 3.86 -> clamp 2.0
        assert adj.multiplier("rising") == 2.0
        # sc_followers: (0.21)/(0.81) = 0.259 -> clamp 0.5
        assert adj.multiplier("sc_followers") == 0.5

    def test_absent_signal_is_neutral(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {"rising": 80.0, "sc_followers": 20.0})
        _seed("went", {"rising": 80.0, "sc_followers": 20.0})
        _seed("skipped", {"rising": 20.0, "sc_followers": 80.0})
        _seed("skipped", {"rising": 20.0, "sc_followers": 80.0})
        adj = compute_adjustments()
        assert adj.multiplier("recency") == 1.0  # epsilon/epsilon

    def test_followed_never_tuned(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        assert "followed" not in TUNABLE_SIGNALS
        _seed("went", {"followed": 1_000_000.0, "rising": 50.0})
        _seed("went", {"followed": 1_000_000.0, "rising": 50.0})
        _seed("skipped", {"rising": 50.0})
        _seed("skipped", {"rising": 50.0})
        adj = compute_adjustments()
        assert adj.multiplier("followed") == 1.0

    def test_zero_total_rows_skipped(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {})  # zero total — must not crash or count
        _seed("went", {"rising": 50.0})
        _seed("went", {"rising": 50.0})
        _seed("skipped", {"sc_followers": 50.0})
        _seed("skipped", {"sc_followers": 50.0})
        adj = compute_adjustments()
        assert adj.multiplier("rising") == 2.0


class TestGenreBoosts:
    def test_net_counts_with_cap(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        for _ in range(5):
            _seed("went", {"rising": 1.0}, genres=["Techno"])
        _seed("skipped", {"rising": 1.0}, genres=["House"])
        adj = compute_adjustments()
        assert adj.genre_boosts["Techno"] == 3 * 500  # capped at 3
        assert adj.genre_boosts["House"] == -500

    def test_genres_normalized(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {"rising": 1.0}, genres=["dnb"])
        adj = compute_adjustments()
        assert adj.genre_boosts == {"Drum & Bass": 500}


class TestArtistBoosts:
    def test_went_artists_boosted_skipped_not_penalized(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {"rising": 1.0}, artist_ids=["a-1"])
        _seed("skipped", {"rising": 1.0}, artist_ids=["a-2"])
        adj = compute_adjustments()
        assert adj.artist_boosts == {"a-1": 2000.0}


class TestSafety:
    def test_disabled_returns_neutral(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"learning": {"enabled": False}})
        _seed("went", {"rising": 1.0}, genres=["Techno"], artist_ids=["a-1"])
        adj = compute_adjustments()
        assert adj == LearnedAdjustments()

    def test_db_error_returns_neutral(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)

        def boom():
            raise RuntimeError("db gone")

        monkeypatch.setattr(store, "get_all_feedback", boom)
        assert compute_adjustments() == LearnedAdjustments()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_learning.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cuepoint.learning'`

- [ ] **Step 3: Implement learning.py**

Create `src/cuepoint/learning.py`:

```python
"""
Feedback-driven scoring adjustments.

Computes a frozen LearnedAdjustments from event_feedback rows:
  - multipliers: per-signal weight multipliers learned from score-breakdown
    shares in went vs skipped events (clamped, cold-start gated)
  - genre_boosts: net went-minus-skipped counts per normalized genre
  - artist_boosts: flat bonus for artists seen in attended lineups

Never raises — any failure returns neutral adjustments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from . import config as cfg
from . import db as store
from .tag_utils import normalize_genre

EPSILON = 0.01

# Breakdown keys eligible for weight tuning. "followed" is deliberately
# excluded: its bonus encodes intent and would swamp share normalization.
TUNABLE_SIGNALS = (
    "sc_followers",
    "dc_have",
    "bc_supporters",
    "rising",
    "similarity",
    "shared_labels",
    "dc_ratio",
    "recency",
    "ra_genre",
)


@dataclass(frozen=True)
class LearnedAdjustments:
    multipliers: dict[str, float] = field(default_factory=dict)
    genre_boosts: dict[str, float] = field(default_factory=dict)
    artist_boosts: dict[str, float] = field(default_factory=dict)

    def multiplier(self, key: str) -> float:
        return self.multipliers.get(key, 1.0)


def compute_adjustments() -> LearnedAdjustments:
    """Compute adjustments from stored feedback. Returns neutral on any failure."""
    if not cfg.learning_enabled():
        return LearnedAdjustments()
    try:
        rows = store.get_all_feedback()
        return LearnedAdjustments(
            multipliers=_compute_multipliers(rows),
            genre_boosts=_compute_genre_boosts(rows),
            artist_boosts=_compute_artist_boosts(rows),
        )
    except Exception as e:
        logger.warning(f"learning: compute_adjustments failed, using neutral adjustments: {e}")
        return LearnedAdjustments()


def _mean_shares(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    """Mean per-signal share of total tunable contribution. None if no usable rows."""
    sums = dict.fromkeys(TUNABLE_SIGNALS, 0.0)
    n = 0
    for row in rows:
        bd_raw = row.get("breakdown")
        if not isinstance(bd_raw, dict):
            continue
        bd = {k: float(v) for k, v in bd_raw.items() if k in TUNABLE_SIGNALS}
        total = sum(bd.values())
        if total <= 0:
            continue  # zero-total guard
        n += 1
        for k, v in bd.items():
            sums[k] += v / total
    if n == 0:
        return None
    return {k: s / n for k, s in sums.items()}


def _compute_multipliers(rows: list[dict[str, Any]]) -> dict[str, float]:
    went = [r for r in rows if r["verdict"] == "went"]
    skipped = [r for r in rows if r["verdict"] == "skipped"]
    if len(rows) < cfg.learning_min_feedback():
        return {}
    if min(len(went), len(skipped)) < cfg.learning_min_per_class():
        return {}
    w = _mean_shares(went)
    s = _mean_shares(skipped)
    if w is None or s is None:
        return {}
    lo, hi = cfg.learning_multiplier_min(), cfg.learning_multiplier_max()
    return {k: max(lo, min(hi, (w[k] + EPSILON) / (s[k] + EPSILON))) for k in TUNABLE_SIGNALS}


def _compute_genre_boosts(rows: list[dict[str, Any]]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for row in rows:
        delta = 1 if row["verdict"] == "went" else -1
        genres = row.get("genres")
        if not isinstance(genres, list):
            continue
        for g in genres:
            norm = normalize_genre(str(g))
            if norm:
                counts[norm] = counts.get(norm, 0) + delta
    cap = cfg.learning_genre_boost_cap()
    unit = cfg.learning_genre_boost_unit()
    return {g: float(max(-cap, min(cap, c)) * unit) for g, c in counts.items() if c != 0}


def _compute_artist_boosts(rows: list[dict[str, Any]]) -> dict[str, float]:
    boost = float(cfg.learning_artist_boost())
    ids: set[str] = set()
    for row in rows:
        if row["verdict"] != "went":
            continue
        artist_ids = row.get("artist_ids")
        if isinstance(artist_ids, list):
            ids.update(str(aid) for aid in artist_ids)
    return dict.fromkeys(ids, boost)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_learning.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/learning.py tests/test_learning.py
git commit -m "feat: learning module — multipliers and boosts from feedback"
```

---

### Task 5: Apply adjustments in scoring

**Files:**
- Modify: `src/cuepoint/scoring.py` (`sort_df`, `_score_artist`, `_score_row`)
- Modify: `src/cuepoint/html_creator.py` (`_BREAKDOWN_LABELS` — two new entries)
- Test: `tests/test_scoring_learning.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scoring_learning.py`:

```python
"""End-to-end: feedback in DB changes sort_df ranking; disabled = baseline."""

import pandas as pd

from cuepoint import config as cfg
from cuepoint import db as store
from cuepoint.scoring import sort_df
from tests.conftest import _make_event_row


def _learning_cfg(base_cfg, enabled=True):
    return {
        **base_cfg,
        "learning": {
            "enabled": enabled,
            "min_feedback": 2,
            "min_per_class": 1,
            "genre_boost_unit": 500,
            "genre_boost_cap": 3,
            "artist_boost": 2000,
        },
    }


def _two_event_df(sample_artist_info):
    a1 = {**sample_artist_info, "id": "a-1", "name": "DJ One", "soundcloud": "/dj-one"}
    a2 = {**sample_artist_info, "id": "a-2", "name": "DJ Two", "soundcloud": "/dj-two"}
    return pd.DataFrame(
        [
            _make_event_row("evt-1", [a1], ["Techno"], title="Event One"),
            _make_event_row("evt-2", [a2], ["Techno"], title="Event Two"),
        ]
    )


class TestScoringWithLearning:
    def test_disabled_matches_baseline(self, tmp_db, mock_config, sample_artist_info, monkeypatch):
        df = _two_event_df(sample_artist_info)
        monkeypatch.setattr(cfg, "_cfg", _learning_cfg(mock_config, enabled=False))
        store.save_feedback("evt-x", "Berlin", "went", artist_ids=["a-2"], genres=["Techno"])
        baseline = sort_df(df.copy())
        # identical artists -> identical scores regardless of feedback
        assert baseline.iloc[0]["_score"] == baseline.iloc[1]["_score"]

    def test_artist_boost_reranks(self, tmp_db, mock_config, sample_artist_info, monkeypatch):
        df = _two_event_df(sample_artist_info)
        monkeypatch.setattr(cfg, "_cfg", _learning_cfg(mock_config))
        store.save_feedback("evt-x", "Berlin", "went", artist_ids=["a-2"])
        result = sort_df(df.copy())
        assert result.iloc[0]["title"] == "Event Two"
        bd = result.iloc[0]["_score_breakdown"]
        assert bd["artist_affinity"] == 2000.0

    def test_genre_boost_in_breakdown(self, tmp_db, mock_config, sample_artist_info, monkeypatch):
        df = _two_event_df(sample_artist_info)
        monkeypatch.setattr(cfg, "_cfg", _learning_cfg(mock_config))
        store.save_feedback("evt-x", "Berlin", "went", genres=["Techno"])
        result = sort_df(df.copy())
        # both events are Techno -> both get the boost
        for _, row in result.iterrows():
            assert row["_score_breakdown"]["genre_affinity"] == 500.0

    def test_multiplier_scales_contribution(self, tmp_db, mock_config, sample_artist_info, monkeypatch):
        df = _two_event_df(sample_artist_info)
        monkeypatch.setattr(cfg, "_cfg", _learning_cfg(mock_config))
        baseline = sort_df(df.copy())
        base_sc = baseline.iloc[0]["_score_breakdown"]["sc_followers"]
        # went dominated by sc_followers, skipped dominated by rising
        store.save_feedback("f-1", "Berlin", "went", breakdown={"sc_followers": 90.0, "rising": 10.0})
        store.save_feedback("f-2", "Berlin", "skipped", breakdown={"sc_followers": 10.0, "rising": 90.0})
        boosted = sort_df(df.copy())
        new_sc = boosted.iloc[0]["_score_breakdown"]["sc_followers"]
        assert new_sc == base_sc * 2.0  # clamped at multiplier_max
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scoring_learning.py -q`
Expected: FAIL — `KeyError: 'artist_affinity'` (and rank assertions fail)

- [ ] **Step 3: Integrate into scoring.py**

In `src/cuepoint/scoring.py`:

Add import after the existing `from . import config as cfg`:

```python
from .learning import compute_adjustments
```

Update the tag_utils import line:

```python
from .tag_utils import count_genre_matches, normalize_genre, parse_artist_tags
```

In `sort_df`, right after `_genre_set = set(cfg.genre_filter())` add:

```python
    _adj = compute_adjustments()
```

In `_score_artist`, replace the existing inner `_add` so multipliers apply at the single choke point:

```python
        def _add(key: str, val: float) -> None:
            nonlocal total
            val *= _adj.multiplier(key)
            total += val
            if breakdown is not None and val:
                breakdown[key] = breakdown.get(key, 0) + val
```

(`followed`, `genre_affinity`, `artist_affinity` are never in `_adj.multipliers`, so `multiplier()` returns 1.0 for them — no special-casing needed.)

Still in `_score_artist`, add artist affinity right after the `is_following` block:

```python
        _aid = str(artist_info.get("id", ""))
        if _aid and _aid in _adj.artist_boosts:
            _add("artist_affinity", _adj.artist_boosts[_aid] / divisor)
```

In `_score_row`, the `ra_genre` direct add (bypasses `_add`) becomes:

```python
        ra_genre_val = count_techno_in_list(ra_genres) * cfg.ra_genre_bonus() * _adj.multiplier("ra_genre")
```

Also in `_score_row`, add genre affinity after the `ra_genre` block (before `return total`):

```python
        if _adj.genre_boosts:
            names: set[str] = set()
            for a in row["artists_info"]:
                if a is not None:
                    for t in parse_artist_tags(a):
                        norm = normalize_genre(t)
                        if norm:
                            names.add(norm)
            for g in row["genres"]:
                if isinstance(g, dict):
                    norm = normalize_genre(g["name"])
                    if norm:
                        names.add(norm)
            affinity = sum(_adj.genre_boosts.get(n, 0.0) for n in names)
            if affinity:
                total += affinity
                if breakdown is not None:
                    breakdown["genre_affinity"] = affinity
```

In `src/cuepoint/html_creator.py`, add to `_BREAKDOWN_LABELS` (dict ending at line 396):

```python
    "genre_affinity": "Genre Affinity (learned)",
    "artist_affinity": "Artist Affinity (learned)",
```

- [ ] **Step 4: Run tests to verify pass + no regression**

Run: `python -m pytest tests/test_scoring_learning.py tests/ -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/scoring.py src/cuepoint/html_creator.py tests/test_scoring_learning.py
git commit -m "feat: apply learned adjustments in scoring"
```

---

### Task 6: API — POST /feedback, GET /feedback/stats, CORS null origin

**Files:**
- Modify: `src/cuepoint/api.py`
- Test: `tests/test_api_feedback.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_feedback.py` (check `tests/test_api.py` first and reuse its TestClient fixture pattern if one exists; otherwise use the fixture below):

```python
"""Tests for /feedback endpoints."""

import pytest
from fastapi.testclient import TestClient

from cuepoint import db as store
from cuepoint.api import app


@pytest.fixture
def client(tmp_db):
    with TestClient(app) as c:
        yield c


class TestPostFeedback:
    def test_single_item(self, client):
        r = client.post(
            "/feedback",
            json={
                "event_id": "evt-1",
                "verdict": "went",
                "city": "Berlin",
                "title": "Night",
                "breakdown": {"sc_followers": 100.5},
                "genres": ["Techno"],
                "artist_ids": ["a-1"],
            },
        )
        assert r.status_code == 200
        assert r.json() == {"saved": 1}
        rows = store.get_all_feedback()
        assert rows[0]["verdict"] == "went"

    def test_batch(self, client):
        items = [
            {"event_id": f"evt-{i}", "verdict": "went" if i % 2 else "skipped"}
            for i in range(5)
        ]
        r = client.post("/feedback", json=items)
        assert r.status_code == 200
        assert r.json() == {"saved": 5}

    def test_invalid_verdict_422(self, client):
        r = client.post("/feedback", json={"event_id": "e", "verdict": "maybe"})
        assert r.status_code == 422

    def test_empty_event_id_422(self, client):
        r = client.post("/feedback", json={"event_id": "", "verdict": "went"})
        assert r.status_code == 422

    def test_non_numeric_breakdown_422(self, client):
        r = client.post(
            "/feedback", json={"event_id": "e", "verdict": "went", "breakdown": {"k": "high"}}
        )
        assert r.status_code == 422

    def test_oversized_batch_413(self, client):
        items = [{"event_id": f"e-{i}", "verdict": "went"} for i in range(101)]
        r = client.post("/feedback", json=items)
        assert r.status_code == 413

    def test_upsert(self, client):
        client.post("/feedback", json={"event_id": "e1", "verdict": "went"})
        client.post("/feedback", json={"event_id": "e1", "verdict": "skipped"})
        rows = store.get_all_feedback()
        assert len(rows) == 1
        assert rows[0]["verdict"] == "skipped"


class TestFeedbackStats:
    def test_stats_shape(self, client):
        client.post("/feedback", json={"event_id": "e1", "verdict": "went", "genres": ["Techno"]})
        r = client.get("/feedback/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["counts"] == {"went": 1}
        assert "multipliers" in body
        assert "genre_boosts" in body


class TestCorsNullOrigin:
    def test_null_origin_allowed(self, client):
        r = client.post(
            "/feedback",
            json={"event_id": "e1", "verdict": "went"},
            headers={"Origin": "null"},
        )
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "null"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_feedback.py -q`
Expected: FAIL — 404 on `/feedback` (route doesn't exist)

- [ ] **Step 3: Implement endpoints**

In `src/cuepoint/api.py`:

CORS (line 74) — add `"null"`:

```python
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "null"],
```

(Security note, deliberate: `"null"` lets the report opened via `file://` POST feedback. Any local file gains the same access — accepted for a localhost-only personal tool; documented in the spec.)

Add a feedback rate-limit log next to the existing one (after line 118):

```python
_FEEDBACK_RATE_MAX = 60  # max POST /feedback per window per IP
_feedback_rate_log: dict[str, list[float]] = {}
```

Generalize `_check_rate_limit` (replace existing function; `/scan` call sites stay unchanged because of the defaults):

```python
async def _check_rate_limit(
    client_ip: str,
    *,
    log: dict[str, list[float]] | None = None,
    max_per_window: int = _RATE_LIMIT_MAX,
) -> bool:
    """Return True if the request is allowed."""
    rate_log = _rate_log if log is None else log
    now = time.monotonic()
    async with _rate_lock:
        timestamps = [t for t in rate_log.get(client_ip, []) if now - t < _RATE_LIMIT_WINDOW]
        if len(timestamps) >= max_per_window:
            rate_log[client_ip] = timestamps
            return False
        timestamps.append(now)
        rate_log[client_ip] = timestamps
        return True
```

Add the model near the other Pydantic models (add `Literal` to the `typing` import at the top of api.py):

```python
class FeedbackItem(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=64)
    verdict: Literal["went", "skipped"]
    city: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=256)
    breakdown: dict[str, float] = Field(default_factory=dict)
    genres: list[str] = Field(default_factory=list, max_length=50)
    artist_ids: list[str] = Field(default_factory=list, max_length=200)
```

Add endpoints (near the other routes):

```python
@app.post("/feedback")
async def post_feedback(
    items: FeedbackItem | list[FeedbackItem],
    request: Request,
    _: None = Depends(_check_api_key),
) -> dict[str, int]:
    """Record Went/Skipped verdicts from the HTML report (single item or batch)."""
    batch = items if isinstance(items, list) else [items]
    if len(batch) > 100:
        raise HTTPException(status_code=413, detail="Batch too large (max 100)")
    client_ip = request.client.host if request.client else "unknown"
    if not await _check_rate_limit(client_ip, log=_feedback_rate_log, max_per_window=_FEEDBACK_RATE_MAX):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    for item in batch:
        store.save_feedback(
            item.event_id,
            item.city,
            item.verdict,
            event_title=item.title,
            breakdown=item.breakdown,
            genres=item.genres,
            artist_ids=item.artist_ids,
        )
    return {"saved": len(batch)}


@app.get("/feedback/stats")
async def feedback_stats(_: None = Depends(_check_api_key)) -> dict[str, Any]:
    """Current feedback counts and learned adjustments."""
    from .learning import compute_adjustments

    counts = store.count_feedback()
    adj = compute_adjustments()
    top_genres = dict(sorted(adj.genre_boosts.items(), key=lambda x: -abs(x[1]))[:20])
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "multipliers": adj.multipliers,
        "multipliers_active": bool(adj.multipliers),
        "genre_boosts": top_genres,
        "artist_boost_count": len(adj.artist_boosts),
    }
```

Fallback note: if `FeedbackItem | list[FeedbackItem]` body parsing misbehaves in the installed FastAPI version (422 on valid lists), accept `list[FeedbackItem]` only and change the single-item test to send `[item]` — the Task 7 JS always sends an array anyway.

- [ ] **Step 4: Run tests to verify pass + no regression**

Run: `python -m pytest tests/test_api_feedback.py tests/ -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/api.py tests/test_api_feedback.py
git commit -m "feat: /feedback endpoints with rate limit and null-origin CORS"
```

---

### Task 7: Report — buttons, localStorage queue, sync JS

**Files:**
- Modify: `src/cuepoint/html_creator.py` (`_artist_to_dict` id field; `__API_BASE__` substitution in `create_html`)
- Modify: `src/cuepoint/templates/report.html` (buttons in card + table views, CSS, JS)
- Test: `tests/test_html_feedback.py` (new — server-side parts only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_html_feedback.py`:

```python
"""Server-side report changes for the feedback loop."""

import pandas as pd

from cuepoint.html_creator import _artist_to_dict, create_html
from tests.conftest import _make_event_row


class TestArtistId:
    def test_artist_dict_includes_id(self, sample_artist_info):
        d = _artist_to_dict(sample_artist_info)
        assert d["id"] == "12345"


class TestApiBaseEmbed:
    def test_api_base_substituted(self, sample_artist_info, mock_config):
        df = pd.DataFrame([_make_event_row("evt-1", [sample_artist_info])])
        html = create_html(df)
        assert '"__API_BASE__"' not in html
        assert "http://localhost:8000" in html

    def test_feedback_buttons_present(self, sample_artist_info, mock_config):
        df = pd.DataFrame([_make_event_row("evt-1", [sample_artist_info])])
        html = create_html(df)
        assert "setFeedback" in html
        assert "cuepoint_feedback" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_html_feedback.py -q`
Expected: FAIL — `KeyError: 'id'`

- [ ] **Step 3: Implement server-side changes**

In `src/cuepoint/html_creator.py`:

`_artist_to_dict` return dict — add as first entry (line 427):

```python
        "id": str(a.get("id", "")),
```

In `create_html`, add to the template `.replace()` chain (lines 580-583):

```python
        .replace('"__API_BASE__"', json.dumps(cfg.learning_api_base()))
```

Add the config import at the top of html_creator.py:

```python
from . import config as cfg
```

- [ ] **Step 4: Implement template changes**

In `src/cuepoint/templates/report.html`:

**CSS** — add inside the existing `<style>` block (near the `.score-breakdown` rules at ~line 429):

```css
    /* feedback buttons */
    .fb-btns { display: inline-flex; gap: 4px; margin-left: 6px; }
    .fb-btn {
        font-size: 0.72rem; padding: 2px 8px; border-radius: 10px;
        border: 1px solid var(--border); background: transparent;
        color: var(--text-muted); cursor: pointer;
    }
    .fb-btn.went.active { color: var(--green); border-color: var(--green); background: rgba(152,195,121,0.12); }
    .fb-btn.skipped.active { color: var(--red); border-color: var(--red); background: var(--red-dim); }
```

**Buttons — table view**: in the title `<td>` after the match-badge span (~line 692):

```html
                        <span class="fb-btns" @click.stop>
                            <button class="fb-btn went" :class="{active: feedback[ev.id] === 'went'}" @click="setFeedback(ev, 'went')">Went</button>
                            <button class="fb-btn skipped" :class="{active: feedback[ev.id] === 'skipped'}" @click="setFeedback(ev, 'skipped')">Skipped</button>
                        </span>
```

**Buttons — card view**: in `.card-overlay-title` after the match-badge (~line 799):

```html
                        <span class="fb-btns" @click.stop>
                            <button class="fb-btn went" :class="{active: feedback[ev.id] === 'went'}" @click="setFeedback(ev, 'went')">Went</button>
                            <button class="fb-btn skipped" :class="{active: feedback[ev.id] === 'skipped'}" @click="setFeedback(ev, 'skipped')">Skipped</button>
                        </span>
```

**JS** — inside `setup()` (after the existing refs, ~line 914):

```javascript
        // --- feedback loop ---
        const API_BASE = "__API_BASE__";
        const FB_KEY = 'cuepoint_feedback';
        function loadFbStore() {
            try { return JSON.parse(localStorage.getItem(FB_KEY)) || {}; } catch (e) { return {}; }
        }
        function saveFbStore(s) {
            try { localStorage.setItem(FB_KEY, JSON.stringify(s)); } catch (e) { /* storage unavailable */ }
        }
        const fbStore = loadFbStore();
        const feedback = reactive({});
        Object.keys(fbStore).forEach(k => { feedback[k] = fbStore[k].verdict; });

        async function syncFeedback() {
            const pending = Object.entries(fbStore).filter(([, v]) => !v.synced);
            if (!pending.length) return;
            const payload = pending.map(([event_id, v]) => ({
                event_id,
                verdict: v.verdict,
                city: v.city || '',
                title: (v.title || '').slice(0, 256),
                breakdown: v.breakdown || {},
                genres: v.genres || [],
                artist_ids: v.artist_ids || [],
            }));
            try {
                const r = await fetch(API_BASE + '/feedback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (r.ok) {
                    pending.forEach(([k]) => { fbStore[k].synced = true; });
                    saveFbStore(fbStore);
                }
            } catch (e) { /* API unreachable — retry on next report load */ }
        }

        function setFeedback(ev, verdict) {
            fbStore[ev.id] = {
                verdict,
                city: ev.city || '',
                title: ev.title || '',
                ts: new Date().toISOString(),
                synced: false,
                breakdown: Object.fromEntries(ev.scoreBreakdown.map(s => [s.key, s.value])),
                genres: ev.genres.map(g => g.name),
                artist_ids: ev.artists.map(a => a.id).filter(Boolean),
            };
            feedback[ev.id] = verdict;
            saveFbStore(fbStore);
            syncFeedback();
        }
```

Then: add `syncFeedback();` inside the existing `onMounted(...)` callback (near the end of `setup()`), and add `feedback` and `setFeedback` to the object returned by `setup()` — the template needs both.

- [ ] **Step 5: Run tests + lint**

Run: `python -m pytest tests/test_html_feedback.py tests/ -q && ruff check src/`
Expected: all PASS, no lint errors

- [ ] **Step 6: Manual smoke test (JS is not covered by pytest)**

```bash
python -c "
import pandas as pd, json
from tests.conftest import _make_event_row
from cuepoint.html_creator import create_html
a = {'id': 'a-1', 'name': 'DJ Test', 'soundcloud': '/dj-test', 'sc_followers': 5000, 'sc_tags': json.dumps(['Techno'])}
df = pd.DataFrame([_make_event_row('evt-1', [a]), _make_event_row('evt-2', [a])])
open('output/_fb_smoke.html', 'w', encoding='utf-8').write(create_html(df))
print('wrote output/_fb_smoke.html')
"
```

Open `output/_fb_smoke.html` in a browser. Verify: Went/Skipped buttons render in both views; clicking toggles active state; verdict persists across reload (localStorage); with `uvicorn cuepoint.api:app --port 8000` running, click a button then `GET http://localhost:8000/feedback/stats` shows the row. Delete `output/_fb_smoke.html` afterwards.

- [ ] **Step 7: Commit**

```bash
git add src/cuepoint/html_creator.py src/cuepoint/templates/report.html tests/test_html_feedback.py
git commit -m "feat: Went/Skipped feedback buttons with offline queue in report"
```

---

### Task 8: CLI — --learning-stats and --reset-learning

**Files:**
- Modify: `src/cuepoint/event_fetcher.py` (helper function + `__main__` block, lines 717-776)
- Test: `tests/test_cli_learning.py` (new)

- [ ] **Step 1: Write the failing test**

The `__main__` block isn't importable; test the helper function instead. Create `tests/test_cli_learning.py`:

```python
"""Tests for CLI learning helpers."""

from cuepoint import config as cfg
from cuepoint import db as store
from cuepoint.event_fetcher import format_learning_stats


class TestLearningStats:
    def test_cold_start_message(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"learning": {"enabled": True}})
        out = format_learning_stats()
        assert "0 total" in out

    def test_shows_counts_and_boosts(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"learning": {"enabled": True}})
        store.save_feedback("e1", "Berlin", "went", genres=["Techno"], artist_ids=["a-1"])
        out = format_learning_stats()
        assert "went: 1" in out
        assert "Techno" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_learning.py -q`
Expected: FAIL with `ImportError: cannot import name 'format_learning_stats'`

- [ ] **Step 3: Implement**

In `src/cuepoint/event_fetcher.py`, add a module-level function (before the `__main__` block):

```python
def format_learning_stats() -> str:
    """Human-readable summary of the learned scoring adjustments."""
    from .learning import compute_adjustments

    counts = store.count_feedback()
    total = sum(counts.values())
    parts = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    lines = [f"Feedback: {total} total" + (f" ({parts})" if parts else "")]
    if total == 0:
        lines.append("Record some Went/Skipped verdicts in the HTML report first.")
        return "\n".join(lines)
    adj = compute_adjustments()
    if adj.multipliers:
        lines.append("Signal multipliers (learned):")
        for key, m in sorted(adj.multipliers.items(), key=lambda x: -x[1]):
            lines.append(f"  {key:<16s} x{m:.2f}")
    else:
        lines.append(
            f"Signal multipliers: inactive (need >= {cfg.learning_min_feedback()} total "
            f"and >= {cfg.learning_min_per_class()} per verdict)"
        )
    if adj.genre_boosts:
        lines.append("Genre boosts:")
        for g, b in sorted(adj.genre_boosts.items(), key=lambda x: -abs(x[1]))[:10]:
            lines.append(f"  {g:<24s} {b:+.0f}")
    if adj.artist_boosts:
        lines.append(f"Artist boosts: {len(adj.artist_boosts)} attended artists at +{cfg.learning_artist_boost()}")
    return "\n".join(lines)
```

In the `__main__` block, add the flags to the parser (after `--verbose`, line 730):

```python
    parser.add_argument("--learning-stats", action="store_true", help="Print learned scoring adjustments and exit")
    parser.add_argument("--reset-learning", action="store_true", help="Delete all feedback and exit")
```

Handle them right after `args = parser.parse_args()` (before `store.migrate_if_needed()`):

```python
    if args.learning_stats:
        print(format_learning_stats())
        raise SystemExit(0)

    if args.reset_learning:
        n = sum(store.count_feedback().values())
        answer = input(f"Delete {n} feedback rows? [y/N] ").strip().lower()
        if answer == "y":
            print(f"Deleted {store.clear_feedback()} feedback rows.")
        else:
            print("Aborted.")
        raise SystemExit(0)
```

Note: `--cities` has a default, so `python -m cuepoint.event_fetcher --learning-stats` works without extra args.

- [ ] **Step 4: Run tests + verify CLI manually**

Run: `python -m pytest tests/test_cli_learning.py -q`
Expected: PASS

Run: `python -m cuepoint.event_fetcher --learning-stats`
Expected: prints feedback summary and exits without scanning

- [ ] **Step 5: Commit**

```bash
git add src/cuepoint/event_fetcher.py tests/test_cli_learning.py
git commit -m "feat: --learning-stats and --reset-learning CLI flags"
```

---

### Task 9: Docs, full suite, wrap-up

**Files:**
- Modify: `CLAUDE.md` (architecture table + key design notes)
- Modify: `README.md` (remove closed gap, add highlights + API rows)
- Modify: `docs/superpowers/specs/2026-06-11-feedback-loop-design.md` (status line)

- [ ] **Step 1: Update CLAUDE.md**

Architecture table — add row after `scoring.py`:

```markdown
| `learning.py` | `compute_adjustments()` — feedback-driven signal multipliers + genre/artist boosts from `event_feedback` table. Never raises; neutral on failure. |
```

Key design notes — add bullet:

```markdown
- **Feedback loop**: Went/Skipped buttons in HTML report → localStorage queue → `POST /feedback`. `learning.py` computes clamped weight multipliers (needs ≥10 rows, ≥3/verdict) + genre/artist boosts, applied in `sort_df()`. `--learning-stats`, `--reset-learning` CLI flags. `[learning]` config section.
```

Also fix the stale `ThreadPoolExecutor` claim in CLAUDE.md — parallel scans use `asyncio.gather` + `Semaphore` (see `run_cities_parallel`).

- [ ] **Step 2: Update README.md**

In `## Honest Gaps`, delete the line:

```markdown
- **No scoring feedback loop** — scoring weights are static config; no mechanism to learn from which recommended events were actually attended
```

Add to Engineering Highlights:

```markdown
- **Scoring feedback loop** — Went/Skipped verdicts from the report tune signal weights (clamped multipliers, cold-start gated) and accumulate genre/artist boosts; offline-capable via localStorage queue
```

Add `/feedback` rows to the REST API table:

```markdown
| `POST` | `/feedback` | Record Went/Skipped verdicts (single or batch) |
| `GET` | `/feedback/stats` | Feedback counts and learned adjustments |
```

- [ ] **Step 3: Update spec status**

In `docs/superpowers/specs/2026-06-11-feedback-loop-design.md` change `Status: approved for planning` to `Status: implemented`, and append a `## Implementation deviations` section listing the three deviations from this plan's header.

- [ ] **Step 4: Full verification**

```bash
python -m pytest tests/ -q
ruff check src/
ruff format --check src/
mypy src/cuepoint --strict
```

Expected: all tests pass, no lint/format/type errors. If mypy flags the `FeedbackItem | list[FeedbackItem]` union or learning row typing, fix annotations — do not loosen mypy config.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md docs/superpowers/specs/2026-06-11-feedback-loop-design.md
git commit -m "docs: feedback loop — close README gap, update CLAUDE.md"
```
