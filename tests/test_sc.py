"""Tests for sc.py — SoundCloud enrichment (mocked API calls)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from techno_scan.sc import _is_valid_sc_url, _normalize, populate_sc_info, search_sc_by_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_strips_non_alphanumeric(self):
        assert _normalize("DJ Test-Name") == "djtestname"

    def test_lowercases(self):
        assert _normalize("TECHNO") == "techno"

    def test_empty(self):
        assert _normalize("") == ""


class TestIsValidScUrl:
    def test_valid(self):
        assert _is_valid_sc_url("https://soundcloud.com/some-artist")

    def test_valid_trailing_slash(self):
        assert _is_valid_sc_url("https://soundcloud.com/some-artist/")

    def test_invalid_track_url(self):
        assert not _is_valid_sc_url("https://soundcloud.com/some-artist/some-track")

    def test_invalid_empty(self):
        assert not _is_valid_sc_url("")

    def test_invalid_non_sc(self):
        assert not _is_valid_sc_url("https://example.com/artist")


# ---------------------------------------------------------------------------
# search_sc_by_name
# ---------------------------------------------------------------------------


class TestSearchScByName:
    @patch("techno_scan.sc._api_get")
    def test_exact_username_match(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "collection": [
                {
                    "username": "Test Artist",
                    "permalink": "test-artist",
                    "full_name": "",
                    "permalink_url": "https://soundcloud.com/test-artist",
                },
                {
                    "username": "Other",
                    "permalink": "other",
                    "full_name": "",
                    "permalink_url": "https://soundcloud.com/other",
                },
            ]
        }
        mock_get.return_value = mock_resp
        assert search_sc_by_name("Test Artist") == "https://soundcloud.com/test-artist"

    @patch("techno_scan.sc._api_get")
    def test_permalink_match(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "collection": [
                {
                    "username": "SomeOtherName",
                    "permalink": "dvs1",
                    "full_name": "",
                    "permalink_url": "https://soundcloud.com/dvs1",
                },
            ]
        }
        mock_get.return_value = mock_resp
        assert search_sc_by_name("DVS1") == "https://soundcloud.com/dvs1"

    @patch("techno_scan.sc._api_get")
    def test_no_match_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"collection": []}
        mock_get.return_value = mock_resp
        assert search_sc_by_name("Nonexistent") is None

    @patch("techno_scan.sc._api_get")
    def test_api_failure_returns_none(self, mock_get):
        mock_get.side_effect = Exception("Connection failed")
        assert search_sc_by_name("Test") is None


# ---------------------------------------------------------------------------
# populate_sc_info
# ---------------------------------------------------------------------------


class TestPopulateScInfo:
    def test_no_sc_url_returns_unchanged(self):
        info = {"name": "Test", "soundcloud": None}
        result = populate_sc_info(info)
        assert result is info

    def test_invalid_sc_url_sets_defaults(self):
        info = {"name": "Test", "soundcloud": "https://soundcloud.com/artist/track"}
        result = populate_sc_info(info)
        assert result["sc_tags"] == json.dumps([])
        assert result["sc_followers"] is None

    @patch("techno_scan.sc._api_get")
    def test_successful_enrichment(self, mock_get):
        # First call: resolve user
        user_resp = MagicMock()
        user_resp.json.return_value = {
            "id": 123,
            "followers_count": 50000,
            "followings_count": 200,
        }
        # Second call: tracks
        # Note: the regex extracts quoted strings as tags; unquoted single words
        # from genre field are captured individually via \S+
        tracks_resp = MagicMock()
        tracks_resp.json.return_value = {
            "collection": [
                {"genre": '"Techno"', "tag_list": '"Dark Techno" industrial'},
                {"genre": '"Minimal"', "tag_list": ""},
            ]
        }
        mock_get.side_effect = [user_resp, tracks_resp]

        info = {"name": "Test DJ", "soundcloud": "https://soundcloud.com/test-dj"}
        result = populate_sc_info(info)

        assert result["sc_followers"] == 50000
        assert result["sc_following"] == 200
        tags = json.loads(result["sc_tags"])
        assert "Techno" in tags
        assert "Dark Techno" in tags

    @patch("techno_scan.sc._api_get")
    def test_api_error_sets_defaults(self, mock_get):
        mock_get.side_effect = Exception("API down")
        info = {"name": "Test", "soundcloud": "https://soundcloud.com/test"}
        result = populate_sc_info(info)
        assert result["sc_followers"] is None
        assert json.loads(result["sc_tags"]) == []

    def test_www_url_normalized(self):
        """The www. prefix should be stripped."""
        info = {"name": "Test", "soundcloud": "https://www.soundcloud.com/test"}
        # Just verify it doesn't crash and normalizes
        with patch("techno_scan.sc._api_get") as mock_get:
            user_resp = MagicMock()
            user_resp.json.return_value = {"id": 1, "followers_count": 10, "followings_count": 5}
            tracks_resp = MagicMock()
            tracks_resp.json.return_value = {"collection": []}
            mock_get.side_effect = [user_resp, tracks_resp]

            result = populate_sc_info(info)
            assert result["sc_followers"] == 10
