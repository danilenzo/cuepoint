"""Tests for bandcamp.py — Bandcamp enrichment (mocked HTTP calls)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import httpx

from cuepoint.bandcamp import _get_album_urls, _normalize, _parse_album, populate_bandcamp_info, search_bandcamp_url

_run = asyncio.run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_strips_non_alphanumeric(self):
        assert _normalize("Test Artist!") == "testartist"


# ---------------------------------------------------------------------------
# search_bandcamp_url
# ---------------------------------------------------------------------------


class TestSearchBandcampUrl:
    @patch("cuepoint.bandcamp._fetch")
    def test_exact_match(self, mock_fetch):
        mock_resp = MagicMock()
        mock_resp.text = """
        <ul>
            <li class="searchresult">
                <div class="heading"><a href="/artist">TestArtist</a></div>
                <div class="itemurl"><a href="testartist.bandcamp.com">testartist.bandcamp.com</a></div>
            </li>
        </ul>
        """
        mock_fetch.return_value = mock_resp
        assert _run(search_bandcamp_url("TestArtist")) == "https://testartist.bandcamp.com"

    @patch("cuepoint.bandcamp._fetch")
    def test_no_match_returns_none(self, mock_fetch):
        mock_resp = MagicMock()
        mock_resp.text = """
        <ul>
            <li class="searchresult">
                <div class="heading"><a href="/artist">Other Name</a></div>
                <div class="itemurl"><a href="other.bandcamp.com">other.bandcamp.com</a></div>
            </li>
        </ul>
        """
        mock_fetch.return_value = mock_resp
        assert _run(search_bandcamp_url("TestArtist")) is None

    @patch("cuepoint.bandcamp._fetch")
    def test_error_returns_none(self, mock_fetch):
        mock_fetch.side_effect = httpx.HTTPError("fail")
        assert _run(search_bandcamp_url("TestArtist")) is None


# ---------------------------------------------------------------------------
# _get_album_urls
# ---------------------------------------------------------------------------


class TestGetAlbumUrls:
    @patch("cuepoint.bandcamp._fetch")
    def test_extracts_album_links(self, mock_fetch):
        mock_resp = MagicMock()
        mock_resp.text = """
        <ol>
            <li class="music-grid-item"><a href="/album/first-album">First</a></li>
            <li class="music-grid-item"><a href="/album/second-album">Second</a></li>
            <li class="music-grid-item"><a href="/track/some-track">Track</a></li>
        </ol>
        """
        mock_fetch.return_value = mock_resp
        urls = _run(_get_album_urls("https://testartist.bandcamp.com"))
        assert len(urls) == 2
        assert "first-album" in urls[0]

    @patch("cuepoint.bandcamp._fetch")
    def test_error_returns_empty(self, mock_fetch):
        mock_fetch.side_effect = httpx.HTTPError("fail")
        assert _run(_get_album_urls("https://test.bandcamp.com")) == []


# ---------------------------------------------------------------------------
# _parse_album
# ---------------------------------------------------------------------------


class TestParseAlbum:
    @patch("cuepoint.bandcamp._fetch")
    def test_json_ld_extraction(self, mock_fetch):
        mock_resp = MagicMock()
        mock_resp.text = """
        <html><head>
        <script type="application/ld+json">
        {
            "keywords": ["techno", "industrial", "dark ambient"],
            "sponsor": [{"name": "Fan1"}, {"name": "Fan2"}],
            "datePublished": "15 Mar 2026 00:00:00 GMT"
        }
        </script>
        </head><body></body></html>
        """
        mock_fetch.return_value = mock_resp
        result = _run(_parse_album("https://test.bandcamp.com/album/test"))
        assert "techno" in result["tags"]
        assert result["supporters"] == 2
        assert result["release_date"] == "2026-03-15"

    @patch("cuepoint.bandcamp._fetch")
    def test_fallback_to_html_tags(self, mock_fetch):
        mock_resp = MagicMock()
        mock_resp.text = """
        <html><body>
        <a class="tag">techno</a>
        <a class="tag">minimal</a>
        </body></html>
        """
        mock_fetch.return_value = mock_resp
        result = _run(_parse_album("https://test.bandcamp.com/album/test"))
        assert "techno" in result["tags"]
        assert "minimal" in result["tags"]


# ---------------------------------------------------------------------------
# populate_bandcamp_info
# ---------------------------------------------------------------------------


class TestPopulateBandcampInfo:
    def test_no_bandcamp_no_name_returns_unchanged(self):
        info = {"soundcloud": "x"}
        result = _run(populate_bandcamp_info(info))
        assert "bc_tags" not in result

    @patch("cuepoint.bandcamp.search_bandcamp_url")
    def test_name_search_fallback(self, mock_search):
        mock_search.return_value = None
        info = {"name": "NoOneHere"}
        result = _run(populate_bandcamp_info(info))
        assert "bc_tags" not in result

    @patch("cuepoint.bandcamp._parse_album")
    @patch("cuepoint.bandcamp._get_album_urls")
    def test_successful_enrichment(self, mock_albums, mock_parse):
        mock_albums.return_value = ["https://test.bandcamp.com/album/a"]
        mock_parse.return_value = {
            "tags": ["techno", "dark techno"],
            "supporters": 50,
            "release_date": "2026-01-15",
        }
        info = {"name": "Test", "bandcamp": "https://test.bandcamp.com"}
        result = _run(populate_bandcamp_info(info))
        tags = json.loads(result["bc_tags"])
        assert "techno" in tags
        assert result["bc_supporters"] == 50
        assert result["bc_latest_release"] == "2026-01-15"

    @patch("cuepoint.bandcamp._get_album_urls")
    def test_no_albums_returns_unchanged(self, mock_albums):
        mock_albums.return_value = []
        info = {"name": "Test", "bandcamp": "https://test.bandcamp.com"}
        result = _run(populate_bandcamp_info(info))
        assert "bc_tags" not in result

    @patch("cuepoint.bandcamp._parse_album")
    @patch("cuepoint.bandcamp._get_album_urls")
    def test_deduplicates_tags(self, mock_albums, mock_parse):
        mock_albums.return_value = ["https://x.bandcamp.com/album/a", "https://x.bandcamp.com/album/b"]
        mock_parse.side_effect = [
            {"tags": ["techno", "industrial"], "supporters": 10, "release_date": "2025-06-01"},
            {"tags": ["Techno", "ambient"], "supporters": 5, "release_date": "2026-01-01"},
        ]
        info = {"name": "Test", "bandcamp": "https://x.bandcamp.com"}
        result = _run(populate_bandcamp_info(info))
        tags = json.loads(result["bc_tags"])
        techno_count = sum(1 for t in tags if t.lower() == "techno")
        assert techno_count == 1
        assert result["bc_supporters"] == 15
        assert result["bc_latest_release"] == "2026-01-01"
