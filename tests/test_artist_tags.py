"""Tests for _artist_tags() in event_fetcher.py."""

import json

from techno_scan.discovery import _artist_tags


def test_all_sources():
    """Tags from all three sources are combined."""
    info = {
        "sc_tags": json.dumps(["Techno", "Dark Techno"]),
        "dc_styles": json.dumps(["Minimal", "Dub Techno"]),
        "bc_tags": json.dumps(["techno", "ambient"]),
    }
    tags = _artist_tags(info)
    assert "techno" in tags
    assert "dark techno" in tags
    assert "minimal" in tags
    assert "ambient" in tags


def test_json_error():
    """Malformed JSON in a tag field should not crash."""
    info = {
        "sc_tags": "NOT VALID JSON",
        "dc_styles": json.dumps(["Techno"]),
    }
    tags = _artist_tags(info)
    assert "techno" in tags


def test_missing_keys():
    """Missing tag keys should return empty set."""
    info = {"name": "No Tags Artist"}
    tags = _artist_tags(info)
    assert tags == set()


def test_lowercase():
    """All tags should be lowercased."""
    info = {"sc_tags": json.dumps(["TECHNO", "Hard Techno"])}
    tags = _artist_tags(info)
    assert "techno" in tags
    assert "hard techno" in tags
    assert "TECHNO" not in tags
