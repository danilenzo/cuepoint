"""Tests for _norm_artist_name, _levenshtein, _find_ra_match, _merge_ra_into_stub."""

import json

from fuzzy_match import (
    _find_ra_match,
    _levenshtein,
    _merge_ra_into_stub,
    _norm_artist_name,
)

# --- _norm_artist_name ---


def test_norm_strips_parenthetical():
    assert _norm_artist_name("Shed (2)") == "shed"


def test_norm_lowercase_alnum():
    assert _norm_artist_name("DJ Rush") == "djrush"


def test_norm_none():
    assert _norm_artist_name(None) == ""


# --- _levenshtein ---


def test_levenshtein_exact():
    assert _levenshtein("abc", "abc") == 0


def test_levenshtein_one_edit():
    assert _levenshtein("abc", "abd") == 1


def test_levenshtein_empty():
    assert _levenshtein("abc", "") == 3


# --- _find_ra_match ---


def test_find_exact_match():
    ra_by_name = {"shed": {"name": "Shed", "sc_followers": 5000}}
    result = _find_ra_match("Shed", ra_by_name)
    assert result is not None
    assert result["name"] == "Shed"


def test_find_fuzzy_1_edit():
    ra_by_name = {"shed": {"name": "Shed", "sc_followers": 5000}}
    # "Shd" -> "shed" (norm) vs "shed" -> distance 1, len 3 < 5 so max_dist=1
    result = _find_ra_match("Sheed", ra_by_name)
    # "sheed" vs "shed" -> distance 1, len("sheed")=5 so max_dist=2
    assert result is not None


def test_find_fuzzy_2_edit():
    ra_by_name = {"berghain": {"name": "Berghain DJ", "sc_followers": 3000}}
    # "berghan" vs "berghain" -> distance 1
    result = _find_ra_match("Berghan", ra_by_name)
    assert result is not None


def test_find_no_match():
    ra_by_name = {"shed": {"name": "Shed"}}
    result = _find_ra_match("Completely Different", ra_by_name)
    assert result is None


# --- _merge_ra_into_stub ---


def test_merge_copies_enrichment():
    ra_by_name = {
        "shed": {
            "name": "Shed",
            "sc_followers": 5000,
            "sc_tags": json.dumps(["Techno"]),
            "discogs": "/artist/shed",
            "dc_have": 100,
        }
    }
    stub = {"name": "Shed", "floor": "Panorama Bar"}
    merged = _merge_ra_into_stub(stub, ra_by_name)
    assert merged["sc_followers"] == 5000
    assert merged["dc_have"] == 100


def test_merge_preserves_floor():
    ra_by_name = {"shed": {"name": "Shed", "sc_followers": 5000, "floor": "Main"}}
    stub = {"name": "Shed", "floor": "Panorama Bar"}
    merged = _merge_ra_into_stub(stub, ra_by_name)
    assert merged["floor"] == "Panorama Bar"
