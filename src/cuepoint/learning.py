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
