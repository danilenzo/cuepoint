"""
Discovery computations: rising detection, artist similarity, label affinity.

Extracted from event_fetcher.py for maintainability.
"""

from __future__ import annotations

import json
from typing import Any

from . import config as cfg
from . import db as store
from .following import is_following
from .tag_utils import parse_artist_tag_set


def _artist_tags(info: dict[str, Any]) -> set[str]:
    """Extract a lowercased set of all genre tags for an artist."""
    return parse_artist_tag_set(info)


def check_rising(artist_id: str | int, artist_info: dict[str, Any], *, save: bool = True) -> None:
    """Compare current metrics to stored baseline; flag as rising if growth exceeds threshold.

    Args:
        save: If True (default), persist updated metrics to DB immediately.
              Set False when caller will batch-save metrics separately.
    """
    sc = artist_info.get("sc_followers")
    dc = artist_info.get("dc_want")
    sc_val = int(sc) if sc is not None else None
    dc_val = int(dc) if dc is not None else None

    prev = store.get_artist_metrics(str(artist_id))
    if prev is not None:
        old_sc, old_dc, _ = prev
        rising = False
        if old_sc and sc_val and old_sc > 0:
            growth = (sc_val - old_sc) / old_sc * 100
            if growth >= cfg.rising_sc_pct():
                rising = True
        if old_dc and dc_val and old_dc > 0:
            growth = (dc_val - old_dc) / old_dc * 100
            if growth >= cfg.rising_dc_pct():
                rising = True
        if rising:
            artist_info["_rising"] = True

    if save:
        store.save_artist_metrics(str(artist_id), sc_val, dc_val)


def compute_similarity(artist_lookup: dict[str | int, dict[str, Any]]) -> None:
    """For each non-followed artist, find the best Jaccard match among followed artists."""
    followed = {}
    non_followed = {}
    for aid, info in artist_lookup.items():
        sc_url = info.get("soundcloud")
        tags = _artist_tags(info)
        if not tags:
            continue
        if sc_url and is_following(sc_url):
            followed[aid] = (info.get("name", ""), tags)
        else:
            non_followed[aid] = tags

    if not followed:
        return

    for aid, tags in non_followed.items():
        best_score = 0.0
        best_name = None
        for _fid, (fname, ftags) in followed.items():
            intersection = len(tags & ftags)
            union = len(tags | ftags)
            if union == 0:
                continue
            score = intersection / union
            if score > best_score:
                best_score = score
                best_name = fname
        if best_score >= cfg.similarity_threshold() and best_name:
            artist_lookup[aid]["_similar_to"] = best_name
            artist_lookup[aid]["_similarity_score"] = round(best_score * 100)


def compute_label_affinity(artist_lookup: dict[str | int, dict[str, Any]]) -> None:
    """Flag non-followed artists that share Discogs labels with followed artists."""
    followed_labels = set()
    followed_ids = set()
    for aid, info in artist_lookup.items():
        sc_url = info.get("soundcloud")
        if sc_url and is_following(sc_url):
            followed_ids.add(aid)
            raw = info.get("dc_labels")
            if raw:
                try:
                    followed_labels.update(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    pass

    if not followed_labels:
        return

    for aid, info in artist_lookup.items():
        if aid in followed_ids:
            continue
        raw = info.get("dc_labels")
        if not raw:
            continue
        try:
            artist_labels = set(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
        shared = artist_labels & followed_labels
        if shared:
            info["_shared_labels"] = sorted(shared)[:3]
