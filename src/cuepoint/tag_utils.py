"""
Shared tag/genre parsing for artist info dicts.

Centralizes JSON deserialization of sc_tags, dc_styles, bc_tags.
Call materialize_tags() once after enrichment to cache parsed results
on the dict as _parsed_tags / _parsed_tag_set / _parsed_labels.
"""

from __future__ import annotations

import json

from .types import ArtistInfo

_TAG_KEYS = ("sc_tags", "dc_styles", "bc_tags")


def materialize_tags(artist_info: ArtistInfo) -> None:
    """Parse JSON tag fields once and cache on the dict. Idempotent."""
    if "_parsed_tags" in artist_info:
        return
    tags: list[str] = []
    tag_set: set[str] = set()
    for key in _TAG_KEYS:
        raw = artist_info.get(key)
        if raw:
            try:
                parsed = json.loads(str(raw))
                tags.extend(parsed)
                tag_set.update(t.lower() for t in parsed if t)
            except (json.JSONDecodeError, TypeError):
                pass
    artist_info["_parsed_tags"] = tags
    artist_info["_parsed_tag_set"] = tag_set
    raw_labels = artist_info.get("dc_labels")
    if raw_labels:
        try:
            artist_info["_parsed_labels"] = set(json.loads(str(raw_labels)))
        except (json.JSONDecodeError, TypeError):
            artist_info["_parsed_labels"] = set()
    else:
        artist_info["_parsed_labels"] = set()


def parse_artist_tags(artist_info: ArtistInfo) -> list[str]:
    """Return all genre tags (with duplicates). Uses cached result if available."""
    if "_parsed_tags" in artist_info:
        return artist_info["_parsed_tags"]
    tags: list[str] = []
    for key in _TAG_KEYS:
        raw = artist_info.get(key)
        if raw:
            try:
                tags.extend(json.loads(str(raw)))
            except (json.JSONDecodeError, TypeError):
                pass
    return tags


def parse_artist_tag_set(artist_info: ArtistInfo) -> set[str]:
    """Return lowercased deduplicated tag set. Uses cached result if available."""
    if "_parsed_tag_set" in artist_info:
        return set(artist_info["_parsed_tag_set"])
    tags: set[str] = set()
    for key in _TAG_KEYS:
        raw = artist_info.get(key)
        if raw:
            try:
                tags.update(t.lower() for t in json.loads(str(raw)) if t)
            except (json.JSONDecodeError, TypeError):
                pass
    return tags


def count_genre_matches(artist_info: ArtistInfo, genre_set: set[str]) -> int:
    """Count how many of the artist's tags match the configured genre filter."""
    if "_parsed_tags" in artist_info:
        best = 0
        for key in _TAG_KEYS:
            raw = artist_info.get(key)
            if raw:
                try:
                    hits: int = sum(int(g in genre_set) for g in json.loads(str(raw)))
                    best = max(best, hits)
                except (json.JSONDecodeError, TypeError):
                    pass
        return best
    best = 0
    for key in _TAG_KEYS:
        raw = artist_info.get(key)
        if raw:
            try:
                hits = sum(int(g in genre_set) for g in json.loads(str(raw)))
                best = max(best, hits)
            except (json.JSONDecodeError, TypeError):
                pass
    return best
