"""Tests for club_scrapers.py — club website scrapers (mocked HTML/API responses)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from club_scrapers import (
    _event_dict,
    _make_ticket,
    _parse_lineup,
    _stub_artist,
    scrape_bassiani,
    scrape_berghain,
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestStubArtist:
    def test_basic(self):
        stub = _stub_artist("venue1", "DJ Test")
        assert stub["id"] == "venue1_dj_test"
        assert stub["name"] == "DJ Test"
        assert stub["soundcloud"] is None

    def test_strips_whitespace(self):
        stub = _stub_artist("v", "  Whitespace  ")
        assert stub["name"] == "Whitespace"

    def test_slug_special_chars(self):
        stub = _stub_artist("v", "Dj (live)")
        assert "live" in stub["id"]


class TestParseLineup:
    def test_pipe_separated(self):
        artists = _parse_lineup("v", "Artist A | Artist B | Artist C")
        assert len(artists) == 3
        assert artists[0]["name"] == "Artist A"

    def test_b2b(self):
        artists = _parse_lineup("v", "DJ One b2b DJ Two")
        assert len(artists) == 2

    def test_floor_prefix_stripped(self):
        artists = _parse_lineup("v", "G2: Floor Artist")
        assert artists[0]["name"] == "Floor Artist"

    def test_live_suffix_stripped(self):
        artists = _parse_lineup("v", "Test Artist (live)")
        assert artists[0]["name"] == "Test Artist"

    def test_short_names_filtered(self):
        artists = _parse_lineup("v", "A | Real Name")
        assert len(artists) == 1


class TestMakeTicket:
    def test_basic(self):
        t = _make_ticket(20, "EUR")
        assert t["priceRetail"] == 20.0
        assert t["currency"]["code"] == "EUR"
        assert t["validType"] == "VALID"

    def test_sold_out(self):
        t = _make_ticket(0, "GEL", title="Sold Out", valid_type="SOLDOUT")
        assert t["validType"] == "SOLDOUT"


class TestEventDict:
    def test_structure(self):
        dt = datetime(2026, 4, 15, 22, 0)
        artists = [_stub_artist("v", "Test")]
        ev = _event_dict(
            "v", "Venue", "http://v.com", dt, dt + timedelta(hours=8), "Night", "http://v.com/event/1", artists
        )
        assert ev["venue_name"] == "Venue"
        assert ev["title"] == "Night"
        assert len(ev["artists"]) == 1
        assert ev["_prefilled_artists_info"] == artists


# ---------------------------------------------------------------------------
# Bassiani (JSON API)
# ---------------------------------------------------------------------------


class TestScrapeBassiani:
    @patch("club_scrapers._session.get")
    def test_parses_api_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "posts": [
                    {
                        "id": 1,
                        "event_start": "2026-04-16 23:00",
                        "title": "Bassiani Night",
                        "url": "/nights/1",
                        "main_image_path": "/img/flyer.jpg",
                        "line_up": json.dumps(
                            [
                                {"name": "Main Hall", "data": [{"name": "Artist A"}, {"name": "Artist B"}]},
                                {"name": "Horoom", "data": [{"name": "Artist C"}]},
                            ]
                        ),
                        "sub_title": "",
                        "price": "40",
                        "selling": 1,
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp

        start = datetime(2026, 4, 15)
        end = datetime(2026, 4, 20)
        events = scrape_bassiani(start, end)
        assert len(events) == 1
        assert events[0]["venue_name"] == "Bassiani"
        # 3 artists across 2 rooms
        assert len(events[0]["_prefilled_artists_info"]) == 3
        assert events[0]["_prefilled_artists_info"][0]["floor"] == "Main Hall"
        assert events[0]["tickets"][0]["priceRetail"] == 40.0

    @patch("club_scrapers._session.get")
    def test_out_of_range_skipped(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "posts": [
                    {"id": 1, "event_start": "2025-01-01 23:00", "title": "Old", "line_up": "[]", "sub_title": ""}
                ]
            }
        }
        mock_get.return_value = mock_resp
        events = scrape_bassiani(datetime(2026, 4, 15), datetime(2026, 4, 20))
        assert len(events) == 0

    @patch("club_scrapers._session.get")
    def test_api_error_returns_empty(self, mock_get):
        mock_get.side_effect = Exception("API down")
        events = scrape_bassiani(datetime(2026, 4, 15), datetime(2026, 4, 20))
        assert events == []


# ---------------------------------------------------------------------------
# Berghain (HTML scraping)
# ---------------------------------------------------------------------------


class TestScrapeBerghain:
    @patch("club_scrapers._session.get")
    def test_parses_program_page(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = """
        <html><body>
        <a class="upcoming-event" href="/en/event/12345">
            <p><span class="font-bold">18.04.2026</span></p>
            <h2>Klubnacht</h2>
            <h3>Berghain</h3>
            <h4><span class="font-bold"><span>DJ Alpha</span></span></h4>
            <h3>Panorama Bar</h3>
            <h4><span class="font-bold"><span>DJ Beta</span></span></h4>
        </a>
        </body></html>
        """
        mock_get.return_value = mock_resp

        events = scrape_berghain(datetime(2026, 4, 15), datetime(2026, 4, 20))
        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        assert len(artists) == 2
        assert artists[0]["floor"] == "Berghain"
        assert artists[1]["floor"] == "Panorama Bar"

    @patch("club_scrapers._session.get")
    def test_out_of_range_skipped(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = """
        <html><body>
        <a class="upcoming-event" href="/en/event/1">
            <p><span class="font-bold">01.01.2025</span></p>
            <h2>Old Event</h2>
            <h3>Berghain</h3>
            <h4><span class="font-bold"><span>Old DJ</span></span></h4>
        </a>
        </body></html>
        """
        mock_get.return_value = mock_resp
        events = scrape_berghain(datetime(2026, 4, 15), datetime(2026, 4, 20))
        assert len(events) == 0
