"""Tests for discogs.py — Discogs enrichment (mocked API calls)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import httpx

from cuepoint.discogs import _resolve_artist_id, populate_discogs_info

_run = asyncio.run

# ---------------------------------------------------------------------------
# _resolve_artist_id
# ---------------------------------------------------------------------------


class TestResolveArtistId:
    def test_numeric_id_from_url(self):
        assert _run(_resolve_artist_id("https://www.discogs.com/artist/12345-Some-Name")) == 12345

    def test_numeric_id_malformed_double_url(self):
        """Handles RA's double-URL format: .../artist/https://de/246764-Cio-Dor"""
        assert _run(_resolve_artist_id("https://www.discogs.com/artist/https://de/246764-Cio-Dor")) == 246764

    @patch("cuepoint.discogs._api_get")
    def test_slug_only_resolves_via_search(self, mock_get):
        mock_get.return_value = {"results": [{"id": 99999, "title": "Some Artist"}]}
        assert _run(_resolve_artist_id("https://www.discogs.com/artist/Some+Artist")) == 99999

    @patch("cuepoint.discogs._api_get")
    def test_slug_search_no_results(self, mock_get):
        mock_get.return_value = {"results": []}
        assert _run(_resolve_artist_id("https://www.discogs.com/artist/Nobody+Here")) is None

    @patch("cuepoint.discogs._api_get")
    def test_slug_search_api_error_returns_none(self, mock_get):
        mock_get.side_effect = httpx.HTTPError("API down")
        assert _run(_resolve_artist_id("https://www.discogs.com/artist/Unknown+Person")) is None


# ---------------------------------------------------------------------------
# populate_discogs_info
# ---------------------------------------------------------------------------


class TestPopulateDiscogsInfo:
    def test_no_discogs_url_returns_unchanged(self):
        info = {"name": "Test", "discogs": None}
        result = _run(populate_discogs_info(info))
        assert "dc_have" not in result

    @patch("cuepoint.discogs._api_get")
    def test_successful_enrichment(self, mock_get):
        releases_response = {
            "releases": [
                {
                    "id": 100,
                    "type": "master",
                    "label": "Mord Records",
                    "stats": {"community": {"in_collection": 50, "in_wantlist": 30}},
                },
                {
                    "id": 101,
                    "type": "master",
                    "label": "PoleGroup",
                    "stats": {"community": {"in_collection": 20, "in_wantlist": 10}},
                },
                {
                    "id": 200,
                    "type": "release",
                    "label": "Other",
                    "stats": {"community": {"in_collection": 5, "in_wantlist": 2}},
                },
            ],
            "pagination": {"pages": 1},
        }
        master_100 = {"styles": ["Techno", "Industrial"]}
        master_101 = {"styles": ["Minimal"]}

        mock_get.side_effect = [releases_response, master_100, master_101]

        info = {"name": "Test DJ", "discogs": "https://www.discogs.com/artist/12345-Test"}
        result = _run(populate_discogs_info(info))

        assert result["dc_have"] == 70
        assert result["dc_want"] == 40
        styles = json.loads(result["dc_styles"])
        assert "Techno" in styles
        assert "Mord Records" in json.loads(result["dc_labels"])

    @patch("cuepoint.discogs._api_get")
    def test_no_masters_returns_unchanged(self, mock_get):
        mock_get.return_value = {
            "releases": [
                {
                    "id": 1,
                    "type": "release",
                    "label": "X",
                    "stats": {"community": {"in_collection": 1, "in_wantlist": 0}},
                }
            ],
            "pagination": {"pages": 1},
        }
        info = {"name": "Test", "discogs": "https://www.discogs.com/artist/111-Test"}
        result = _run(populate_discogs_info(info))
        assert "dc_have" not in result

    @patch("cuepoint.discogs._resolve_artist_id")
    def test_unresolvable_id_returns_unchanged(self, mock_resolve):
        mock_resolve.return_value = None
        info = {"name": "Test", "discogs": "https://www.discogs.com/artist/Bad+Url"}
        result = _run(populate_discogs_info(info))
        assert "dc_have" not in result

    @patch("cuepoint.discogs._api_get")
    def test_api_error_handled_gracefully(self, mock_get):
        mock_get.side_effect = httpx.HTTPError("Discogs down")
        info = {"name": "Test", "discogs": "https://www.discogs.com/artist/12345-Test"}
        result = _run(populate_discogs_info(info))
        assert "dc_have" not in result

    @patch("cuepoint.discogs._api_get")
    def test_dc_ratio_zero_haves(self, mock_get):
        releases_response = {
            "releases": [
                {
                    "id": 100,
                    "type": "master",
                    "label": "L",
                    "stats": {"community": {"in_collection": 0, "in_wantlist": 5}},
                },
            ],
            "pagination": {"pages": 1},
        }
        master_100 = {"styles": ["Techno"]}
        mock_get.side_effect = [releases_response, master_100]

        info = {"name": "Test", "discogs": "https://www.discogs.com/artist/99-Test"}
        result = _run(populate_discogs_info(info))
        assert result["dc_ratio"] == 0
