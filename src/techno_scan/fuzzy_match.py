"""
Fuzzy artist name matching: normalization, Levenshtein distance, RA data merging.

Extracted from event_fetcher.py for maintainability.
"""

from __future__ import annotations

import re
from typing import Any


def _norm_artist_name(name: str | None) -> str:
    """Normalize artist name: strip parentheticals like '(2)', lowercase, keep only alnum."""
    s = re.sub(r"\s*\(\d+\)\s*$", "", (name or ""))  # "Shed (2)" -> "Shed"
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if not s2:
        return len(s1)
    prev: list[int] = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


def _find_ra_match(name: str, ra_by_name: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Find best RA artist match: exact normalized first, then Levenshtein fallback."""
    norm = _norm_artist_name(name)
    if not norm:
        return None
    exact = ra_by_name.get(norm)
    if exact:
        return exact
    # Fuzzy: allow up to 2 edits for names >= 5 chars, 1 edit for shorter
    best_dist = float("inf")
    best_ra = None
    max_dist = 2 if len(norm) >= 5 else 1
    for ra_name, ra_info in ra_by_name.items():
        if abs(len(ra_name) - len(norm)) > max_dist:
            continue
        d = _levenshtein(norm, ra_name)
        if d < best_dist and d <= max_dist:
            best_dist = d
            best_ra = ra_info
    return best_ra


def _merge_ra_into_stub(stub: dict[str, Any], ra_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Copy SC/Discogs enrichment from RA data into a club artist stub where names match."""
    ra = _find_ra_match(stub.get("name", ""), ra_by_name)
    if not ra:
        return stub
    merged = {**stub}
    for key in (
        "soundcloud",
        "sc_followers",
        "sc_following",
        "sc_tags",
        "discogs",
        "dc_have",
        "dc_want",
        "dc_ratio",
        "dc_rating",
        "dc_styles",
        "bandcamp",
        "bc_tags",
        "bc_supporters",
        "bc_latest_release",
        "contentUrl",
    ):
        if ra.get(key) is not None:
            merged[key] = ra[key]
    # floor and country from the fresh stub take priority
    if stub.get("floor"):
        merged["floor"] = stub["floor"]
    if stub.get("country"):
        merged["country"] = stub["country"]
    elif ra.get("country"):
        merged["country"] = ra["country"]
    return merged
