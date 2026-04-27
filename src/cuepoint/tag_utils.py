"""
Shared tag/genre parsing for artist info dicts.

Centralizes JSON deserialization of sc_tags, dc_styles, bc_tags
to avoid redundant json.loads() calls across scoring, discovery, and HTML modules.
"""

from __future__ import annotations

import json
from typing import Any

_TAG_KEYS = ("sc_tags", "dc_styles", "bc_tags")


def parse_artist_tags(artist_info: dict[str, Any]) -> list[str]:
    """Parse all genre tags from an artist's SC/DC/BC fields. Returns list with duplicates."""
    tags: list[str] = []
    for key in _TAG_KEYS:
        raw = artist_info.get(key)
        if raw:
            try:
                tags.extend(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
    return tags


def parse_artist_tag_set(artist_info: dict[str, Any]) -> set[str]:
    """Parse all genre tags as a lowercased deduplicated set (for similarity matching)."""
    tags: set[str] = set()
    for key in _TAG_KEYS:
        raw = artist_info.get(key)
        if raw:
            try:
                tags.update(t.lower() for t in json.loads(raw) if t)
            except (json.JSONDecodeError, TypeError):
                pass
    return tags


def count_genre_matches(artist_info: dict[str, Any], genre_set: set[str]) -> int:
    """Count how many of the artist's tags match the configured genre filter."""
    best = 0
    for key in _TAG_KEYS:
        raw = artist_info.get(key)
        if raw:
            try:
                hits = sum(1 for g in json.loads(raw) if g in genre_set)
                best = max(best, hits)
            except (json.JSONDecodeError, TypeError):
                pass
    return best
