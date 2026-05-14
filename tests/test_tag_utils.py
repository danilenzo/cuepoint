"""Tests for tag_utils.py — tag parsing, materialization, and genre matching."""

from __future__ import annotations

import json

from cuepoint.tag_utils import (
    count_genre_matches,
    materialize_tags,
    parse_artist_tag_set,
    parse_artist_tags,
)


class TestMaterializeTags:
    def test_basic_materialization(self):
        info = {"sc_tags": json.dumps(["Techno", "Minimal"])}
        materialize_tags(info)
        assert info["_parsed_tags"] == ["Techno", "Minimal"]
        assert info["_parsed_tag_set"] == {"techno", "minimal"}
        assert info["_parsed_labels"] == set()

    def test_idempotent(self):
        info = {"sc_tags": json.dumps(["Techno"])}
        materialize_tags(info)
        first_tags = info["_parsed_tags"]
        materialize_tags(info)
        assert info["_parsed_tags"] is first_tags

    def test_multiple_sources(self):
        info = {
            "sc_tags": json.dumps(["Techno"]),
            "dc_styles": json.dumps(["Minimal"]),
            "bc_tags": json.dumps(["Industrial"]),
        }
        materialize_tags(info)
        assert set(info["_parsed_tags"]) == {"Techno", "Minimal", "Industrial"}
        assert info["_parsed_tag_set"] == {"techno", "minimal", "industrial"}

    def test_malformed_json(self):
        info = {"sc_tags": "not valid json"}
        materialize_tags(info)
        assert info["_parsed_tags"] == []
        assert info["_parsed_tag_set"] == set()

    def test_no_tag_fields(self):
        info = {"name": "Test Artist"}
        materialize_tags(info)
        assert info["_parsed_tags"] == []
        assert info["_parsed_tag_set"] == set()

    def test_labels_parsed(self):
        info = {
            "sc_tags": json.dumps([]),
            "dc_labels": json.dumps(["Mord Records", "Perc Trax"]),
        }
        materialize_tags(info)
        assert info["_parsed_labels"] == {"Mord Records", "Perc Trax"}

    def test_labels_malformed_json(self):
        info = {"sc_tags": json.dumps([]), "dc_labels": "broken"}
        materialize_tags(info)
        assert info["_parsed_labels"] == set()

    def test_empty_tags_ignored(self):
        info = {"sc_tags": json.dumps(["", "Techno", ""])}
        materialize_tags(info)
        assert "techno" in info["_parsed_tag_set"]
        assert "" not in info["_parsed_tag_set"]

    def test_none_tag_field(self):
        info = {"sc_tags": None}
        materialize_tags(info)
        assert info["_parsed_tags"] == []


class TestParseArtistTags:
    def test_uses_cached(self):
        info = {"_parsed_tags": ["Cached"]}
        assert parse_artist_tags(info) == ["Cached"]

    def test_parses_without_cache(self):
        info = {"sc_tags": json.dumps(["Techno", "House"])}
        assert parse_artist_tags(info) == ["Techno", "House"]

    def test_empty(self):
        assert parse_artist_tags({}) == []


class TestParseArtistTagSet:
    def test_uses_cached(self):
        info = {"_parsed_tag_set": {"techno"}}
        assert parse_artist_tag_set(info) == {"techno"}

    def test_parses_without_cache(self):
        info = {"dc_styles": json.dumps(["Techno", "Minimal"])}
        assert parse_artist_tag_set(info) == {"techno", "minimal"}


class TestCountGenreMatches:
    def test_with_cached_tags(self):
        info = {"sc_tags": json.dumps(["Techno", "House", "Techno"])}
        materialize_tags(info)
        assert count_genre_matches(info, {"Techno"}) == 2

    def test_cached_multi_source_uses_max(self):
        info = {
            "sc_tags": json.dumps(["Techno", "Techno"]),
            "dc_styles": json.dumps(["Techno"]),
        }
        materialize_tags(info)
        assert count_genre_matches(info, {"Techno"}) == 2

    def test_without_cache(self):
        info = {"sc_tags": json.dumps(["Techno", "Minimal"])}
        assert count_genre_matches(info, {"Techno"}) == 1

    def test_no_match(self):
        info = {"sc_tags": json.dumps(["House", "Disco"])}
        materialize_tags(info)
        assert count_genre_matches(info, {"Techno"}) == 0

    def test_empty_artist(self):
        assert count_genre_matches({}, {"Techno"}) == 0
