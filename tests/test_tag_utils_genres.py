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
