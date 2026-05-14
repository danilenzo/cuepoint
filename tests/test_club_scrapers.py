"""Tests for club_scrapers.py — club website scrapers (mocked HTML/API responses)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from bs4 import BeautifulSoup

from cuepoint.club_scrapers import (
    _event_dict,
    _make_ticket,
    _openground_artists_from_anchor,
    _openground_parse_detail_page,
    _parse_lineup,
    _stub_artist,
    get_registered_cities,
    scrape_bassiani,
    scrape_berghain,
    scrape_city_clubs,
    scrape_khidi,
    scrape_openground,
    scrape_tresor,
)

_run = asyncio.run

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
    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_parses_api_response(self, mock_get_client):
        mock_client = AsyncMock()
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
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        start = datetime(2026, 4, 15)
        end = datetime(2026, 4, 20)
        events = _run(scrape_bassiani(start, end))
        assert len(events) == 1
        assert events[0]["venue_name"] == "Bassiani"
        assert len(events[0]["_prefilled_artists_info"]) == 3
        assert events[0]["_prefilled_artists_info"][0]["floor"] == "Main Hall"
        assert events[0]["tickets"][0]["priceRetail"] == 40.0

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_out_of_range_skipped(self, mock_get_client):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "posts": [
                    {"id": 1, "event_start": "2025-01-01 23:00", "title": "Old", "line_up": "[]", "sub_title": ""}
                ]
            }
        }
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client
        events = _run(scrape_bassiani(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 0

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_api_error_returns_empty(self, mock_get_client):
        import httpx

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("API down"))
        mock_get_client.return_value = mock_client
        events = _run(scrape_bassiani(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert events == []


# ---------------------------------------------------------------------------
# Berghain (HTML scraping)
# ---------------------------------------------------------------------------


class TestScrapeBerghain:
    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_parses_program_page(self, mock_get_client):
        mock_client = AsyncMock()
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
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        events = _run(scrape_berghain(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        assert len(artists) == 2
        assert artists[0]["floor"] == "Berghain"
        assert artists[1]["floor"] == "Panorama Bar"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_out_of_range_skipped(self, mock_get_client):
        mock_client = MagicMock()
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
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client
        events = _run(scrape_berghain(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 0


# ---------------------------------------------------------------------------
# get_registered_cities()
# ---------------------------------------------------------------------------


class TestGetRegisteredCities:
    def test_returns_known_cities(self):
        cities = get_registered_cities()
        assert "Wuppertal" in cities
        assert "Tbilisi" in cities
        assert "Berlin" in cities

    def test_returns_list(self):
        cities = get_registered_cities()
        assert isinstance(cities, list)
        assert len(cities) >= 3


# ---------------------------------------------------------------------------
# scrape_city_clubs()
# ---------------------------------------------------------------------------


class TestScrapeCityClubs:
    @patch("cuepoint.club_scrapers.store.record_scraper_health")
    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_unregistered_city_returns_empty(self, mock_get_client, mock_health):
        events = _run(scrape_city_clubs("Nonexistent City", datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert events == []
        mock_health.assert_not_called()

    @patch("cuepoint.club_scrapers.store.record_scraper_health")
    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_successful_run(self, mock_get_client, mock_health):
        """Run city clubs for Tbilisi; mock both Khidi and Bassiani to return events."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Khidi listing page
        khidi_listing_resp = MagicMock()
        khidi_listing_resp.raise_for_status = MagicMock()
        khidi_listing_resp.text = """
        <html><body>
        <a href="https://khidi.ge/event/180426/">Event</a>
        </body></html>
        """
        # Khidi detail page
        khidi_detail_resp = MagicMock()
        khidi_detail_resp.raise_for_status = MagicMock()
        khidi_detail_resp.text = """
        <html><body>
        <div class="elementor-widget-text-editor">DJ Alpha | DJ Beta</div>
        </body></html>
        """
        # Bassiani API response
        bassiani_resp = MagicMock()
        bassiani_resp.raise_for_status = MagicMock()
        bassiani_resp.json.return_value = {
            "data": {
                "posts": [
                    {
                        "id": 1,
                        "event_start": "2026-04-18 23:00",
                        "title": "Bassiani Night",
                        "url": "/nights/1",
                        "main_image_path": None,
                        "line_up": json.dumps([{"name": "Hall", "data": [{"name": "Artist X"}]}]),
                        "sub_title": "",
                        "price": "0",
                        "selling": 0,
                    }
                ]
            }
        }

        mock_client.get.side_effect = [
            khidi_listing_resp,
            khidi_detail_resp,
            bassiani_resp,
        ]

        start = datetime(2026, 4, 15)
        end = datetime(2026, 4, 20)
        events = _run(scrape_city_clubs("Tbilisi", start, end))

        assert len(events) >= 2
        assert mock_health.call_count == 2

    @patch("cuepoint.club_scrapers.store.record_scraper_health")
    def test_one_scraper_fails_others_continue(self, mock_health):
        """If one scraper raises at the scrape_city_clubs level, others still run."""
        import httpx

        from cuepoint.club_scrapers import _REGISTRY

        # Create two fake scrapers: one that raises, one that returns an event
        async def failing_scraper(start, end):
            raise httpx.HTTPError("timeout")

        failing_scraper.__name__ = "failing_scraper"

        async def working_scraper(start, end):
            return [
                _event_dict(
                    "test",
                    "Test Venue",
                    "http://test.com",
                    datetime(2026, 4, 18, 22, 0),
                    datetime(2026, 4, 19, 6, 0),
                    "Test Night",
                    "http://test.com/1",
                    [_stub_artist("test", "Artist Z")],
                )
            ]

        working_scraper.__name__ = "working_scraper"

        # Temporarily register fake scrapers for a test city
        _REGISTRY["TestCity"] = [failing_scraper, working_scraper]
        try:
            start = datetime(2026, 4, 15)
            end = datetime(2026, 4, 20)
            events = _run(scrape_city_clubs("TestCity", start, end))

            # Working scraper event should still be present
            assert len(events) == 1
            assert events[0]["venue_name"] == "Test Venue"
            # Both scrapers should have health recorded
            assert mock_health.call_count == 2
            health_calls = mock_health.call_args_list
            statuses = [c.kwargs.get("status") for c in health_calls]
            assert "error" in statuses
            assert "ok" in statuses
        finally:
            del _REGISTRY["TestCity"]


# ---------------------------------------------------------------------------
# _openground_parse_detail_page()
# ---------------------------------------------------------------------------


class TestOpengroundParseDetailPage:
    def test_parses_floors_and_artists(self):
        html = """
        <html><body>
        <div class="event-info">
            <div class="event-info__floor__label">FREIFELD</div>
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">DJ Alpha</div>
                <div class="event-item__accordion-top-city">Berlin</div>
                <div class="event-item__accordion-content-links">
                    <a href="https://soundcloud.com/dj-alpha">SC</a>
                </div>
            </div>
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">DJ Beta</div>
            </div>
        </div>
        <div class="event-info">
            <div class="event-info__floor__label">ANNEX</div>
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">DJ Gamma</div>
                <div class="event-item__accordion-content-links">
                    <a href="https://www.soundcloud.com/dj-gamma">SC</a>
                </div>
            </div>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        artists = _openground_parse_detail_page(soup)

        assert len(artists) == 3
        assert artists[0]["name"] == "DJ Alpha"
        assert artists[0]["floor"] == "FREIFELD"
        assert artists[0]["soundcloud"] == "https://soundcloud.com/dj-alpha"
        assert artists[0]["country"] == {"name": "Berlin"}

        assert artists[1]["name"] == "DJ Beta"
        assert artists[1]["floor"] == "FREIFELD"
        assert artists[1]["soundcloud"] is None

        assert artists[2]["name"] == "DJ Gamma"
        assert artists[2]["floor"] == "ANNEX"
        # www.soundcloud.com should be normalized
        assert artists[2]["soundcloud"] == "https://soundcloud.com/dj-gamma"

    def test_no_floor_label(self):
        html = """
        <html><body>
        <div class="event-info">
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">Solo Artist</div>
            </div>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        artists = _openground_parse_detail_page(soup)
        assert len(artists) == 1
        assert artists[0]["name"] == "Solo Artist"
        assert "floor" not in artists[0]

    def test_short_names_filtered(self):
        html = """
        <html><body>
        <div class="event-info">
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">X</div>
            </div>
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">Valid Name</div>
            </div>
        </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        artists = _openground_parse_detail_page(soup)
        assert len(artists) == 1
        assert artists[0]["name"] == "Valid Name"

    def test_empty_html(self):
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        artists = _openground_parse_detail_page(soup)
        assert artists == []


# ---------------------------------------------------------------------------
# _openground_artists_from_anchor()
# ---------------------------------------------------------------------------


class TestOpengroundArtistsFromAnchor:
    def test_extracts_artist_names(self):
        html = """
        <a href="/en/schedule/2026-04-18">
            <p>DJ Alpha</p>
            <p>DJ Beta</p>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        anchor = soup.find("a")
        artists = _openground_artists_from_anchor(anchor)
        assert len(artists) == 2
        assert artists[0]["name"] == "DJ Alpha"
        assert artists[1]["name"] == "DJ Beta"

    def test_filters_date_lines(self):
        html = """
        <a href="/en/schedule/2026-04-18">
            <p>Sat.21.03.26</p>
            <p>22:00-07:00</p>
            <p>DJ Alpha</p>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        anchor = soup.find("a")
        artists = _openground_artists_from_anchor(anchor)
        assert len(artists) == 1
        assert artists[0]["name"] == "DJ Alpha"

    def test_filters_category_lines(self):
        html = """
        <a href="/en/schedule/2026-04-18">
            <p>Clubnight</p>
            <p>Special Event</p>
            <p>DJ Alpha</p>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        anchor = soup.find("a")
        artists = _openground_artists_from_anchor(anchor)
        assert len(artists) == 1
        assert artists[0]["name"] == "DJ Alpha"

    def test_filters_venue_lines(self):
        html = """
        <a href="/en/schedule/2026-04-18">
            <p>FREIFELD</p>
            <p>ANNEX</p>
            <p>DJ Alpha</p>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        anchor = soup.find("a")
        artists = _openground_artists_from_anchor(anchor)
        assert len(artists) == 1
        assert artists[0]["name"] == "DJ Alpha"

    def test_handles_b2b(self):
        html = """
        <a href="/en/schedule/2026-04-18">
            <p>DJ Alpha b2b DJ Beta</p>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        anchor = soup.find("a")
        artists = _openground_artists_from_anchor(anchor)
        assert len(artists) == 2
        assert artists[0]["name"] == "DJ Alpha"
        assert artists[1]["name"] == "DJ Beta"

    def test_strips_live_suffix(self):
        html = """
        <a href="/en/schedule/2026-04-18">
            <p>DJ Alpha (live)</p>
        </a>
        """
        soup = BeautifulSoup(html, "html.parser")
        anchor = soup.find("a")
        artists = _openground_artists_from_anchor(anchor)
        assert len(artists) == 1
        assert artists[0]["name"] == "DJ Alpha"

    def test_empty_anchor(self):
        html = '<a href="/en/schedule/2026-04-18"></a>'
        soup = BeautifulSoup(html, "html.parser")
        anchor = soup.find("a")
        artists = _openground_artists_from_anchor(anchor)
        assert artists == []


# ---------------------------------------------------------------------------
# scrape_openground()
# ---------------------------------------------------------------------------


class TestScrapeOpenground:
    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_parses_events_in_range(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        homepage_resp = MagicMock()
        homepage_resp.raise_for_status = MagicMock()
        homepage_resp.text = """
        <html><body>
        <a href="/en/schedule/2026-04-18">
            <p>DJ Fallback</p>
        </a>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><head>
        <meta property="og:image" content="https://openground.club/flyer.jpg"/>
        </head><body>
        <script type="application/ld+json">
        {"@type": "Event", "name": "Techno Night", "endDate": "2026-04-19T07:00:00"}
        </script>
        <div class="event-info">
            <div class="event-info__floor__label">FREIFELD</div>
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">DJ Alpha</div>
            </div>
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">DJ Beta</div>
                <div class="event-item__accordion-content-links">
                    <a href="https://soundcloud.com/dj-beta">SC</a>
                </div>
            </div>
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [homepage_resp, detail_resp]

        start = datetime(2026, 4, 15)
        end = datetime(2026, 4, 20)
        events = _run(scrape_openground(start, end))

        assert len(events) == 1
        ev = events[0]
        assert ev["venue_name"] == "Openground"
        assert ev["title"] == "Techno Night"
        assert ev["images"] == [{"filename": "https://openground.club/flyer.jpg"}]
        artists = ev["_prefilled_artists_info"]
        assert len(artists) == 2
        assert artists[0]["name"] == "DJ Alpha"
        assert artists[0]["floor"] == "FREIFELD"
        assert artists[1]["soundcloud"] == "https://soundcloud.com/dj-beta"
        assert ev["end_time"] == datetime(2026, 4, 19, 7, 0)

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_out_of_range_skipped(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        homepage_resp = MagicMock()
        homepage_resp.raise_for_status = MagicMock()
        homepage_resp.text = """
        <html><body>
        <a href="/en/schedule/2025-01-01">Old Event</a>
        </body></html>
        """
        mock_client.get.return_value = homepage_resp

        events = _run(scrape_openground(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 0

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_detail_page_failure_uses_anchor_fallback(self, mock_get_client):
        import httpx

        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        homepage_resp = MagicMock()
        homepage_resp.raise_for_status = MagicMock()
        homepage_resp.text = """
        <html><body>
        <a href="/en/schedule/2026-04-18">
            <p>DJ Fallback Artist</p>
        </a>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status.side_effect = httpx.HTTPError("404")

        mock_client.get.side_effect = [homepage_resp, detail_resp]

        start = datetime(2026, 4, 15)
        end = datetime(2026, 4, 20)
        events = _run(scrape_openground(start, end))

        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        assert len(artists) == 1
        assert artists[0]["name"] == "DJ Fallback Artist"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_http_error_returns_empty(self, mock_get_client):
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.HTTPError("connection failed")
        mock_get_client.return_value = mock_client

        events = _run(scrape_openground(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert events == []

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_deduplicates_same_href(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        homepage_resp = MagicMock()
        homepage_resp.raise_for_status = MagicMock()
        homepage_resp.text = """
        <html><body>
        <a href="/en/schedule/2026-04-18"><p>DJ One</p></a>
        <a href="/en/schedule/2026-04-18"><p>DJ One</p></a>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="event-info">
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">DJ One</div>
            </div>
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [homepage_resp, detail_resp]

        events = _run(scrape_openground(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_ticket_price_extraction(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        homepage_resp = MagicMock()
        homepage_resp.raise_for_status = MagicMock()
        homepage_resp.text = """
        <html><body>
        <div class="newhome-block-box">
            <a href="/en/schedule/2026-04-18">
                <p>DJ Test</p>
            </a>
            Tickets 15
        </div>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="event-info">
            <div class="event-info__item">
                <div class="event-item__accordion-top-name">DJ Test</div>
            </div>
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [homepage_resp, detail_resp]

        events = _run(scrape_openground(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        assert events[0]["tickets"][0]["priceRetail"] == 15.0
        assert events[0]["tickets"][0]["currency"]["code"] == "EUR"


# ---------------------------------------------------------------------------
# scrape_khidi()
# ---------------------------------------------------------------------------


class TestScrapeKhidi:
    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_parses_events_in_range(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <a href="https://khidi.ge/event/180426/">April Event</a>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><head>
        <meta property="og:image" content="https://khidi.ge/flyer.jpg"/>
        </head><body>
        <div class="elementor-widget-text-editor">
            DJ Alpha | DJ Beta | DJ Gamma
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        start = datetime(2026, 4, 15)
        end = datetime(2026, 4, 20)
        events = _run(scrape_khidi(start, end))

        assert len(events) == 1
        ev = events[0]
        assert ev["venue_name"] == "Khidi"
        assert "18 Apr 2026" in ev["title"]
        assert ev["images"] == [{"filename": "https://khidi.ge/flyer.jpg"}]
        artists = ev["_prefilled_artists_info"]
        assert len(artists) == 3
        assert artists[0]["name"] == "DJ Alpha"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_out_of_range_skipped(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <a href="https://khidi.ge/event/010125/">Old Event</a>
        </body></html>
        """
        mock_client.get.return_value = listing_resp

        events = _run(scrape_khidi(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 0

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_date_parsing_from_url(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        # 150426 = day 15, month 04, year 26
        listing_resp.text = """
        <html><body>
        <a href="https://khidi.ge/event/150426/">Event</a>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="elementor-widget-text-editor">DJ Test</div>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_khidi(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        assert events[0]["event_date"].day == 15
        assert events[0]["event_date"].month == 4
        assert events[0]["event_date"].year == 2026

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_detail_page_failure_still_creates_event(self, mock_get_client):
        import httpx

        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <a href="https://khidi.ge/event/180426/">Event</a>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status.side_effect = httpx.HTTPError("timeout")

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_khidi(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        # Event is still created even without detail page data (empty artists)
        assert len(events) == 1
        assert events[0]["_prefilled_artists_info"] == []

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_http_error_returns_empty(self, mock_get_client):
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.HTTPError("connection refused")
        mock_get_client.return_value = mock_client

        events = _run(scrape_khidi(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert events == []

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_deduplicates_event_links(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <a href="https://khidi.ge/event/180426/">Event</a>
        <a href="https://khidi.ge/event/180426/">Same Event</a>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="elementor-widget-text-editor">DJ Alpha</div>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_khidi(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_ticket_tier_parsing(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <a href="https://khidi.ge/event/180426/">Event</a>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="elementor-widget-text-editor">DJ Test</div>
        <p>I PRE-SALE: 30 GEL [SOLD OUT]</p>
        <p>II PRE-SALE: 40 GEL</p>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_khidi(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        tickets = events[0]["tickets"]
        assert len(tickets) == 2
        assert tickets[0]["priceRetail"] == 30.0
        assert tickets[0]["validType"] == "SOLDOUT"
        assert tickets[0]["currency"]["code"] == "GEL"
        assert tickets[1]["priceRetail"] == 40.0
        assert tickets[1]["validType"] == "VALID"


# ---------------------------------------------------------------------------
# scrape_tresor()
# ---------------------------------------------------------------------------


class TestScrapeTresor:
    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_parses_events_in_range(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-techno-night">18.04</a>
            </div>
            <a class="event-title"><span><span>Tresor Techno Night</span></span></a>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>DJ Alpha</span></div>
                <div class="floor-artist"><span>DJ Beta</span></div>
            </div>
            <div class="event-floor" data-floor="ohm">
                <div class="floor-name">OHM</div>
                <div class="floor-artist"><span>DJ Gamma</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <aside class="hero-outer">
            <picture><img src="https://tresorberlin.com/flyer.jpg"/></picture>
        </aside>
        <div class="lineup">
            <div class="floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <a class="lineup-item" href="https://soundcloud.com/dj-alpha">
                    <div class="lineup-name">DJ Alpha</div>
                </a>
                <a class="lineup-item" href="https://ra.co/dj/dj-beta">
                    <div class="lineup-name">DJ Beta</div>
                </a>
            </div>
            <div class="floor" data-floor="ohm">
                <div class="floor-name">OHM</div>
                <a class="lineup-item" href="https://soundcloud.com/dj-gamma">
                    <div class="lineup-name">DJ Gamma</div>
                </a>
            </div>
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        start = datetime(2026, 4, 15)
        end = datetime(2026, 4, 20)
        events = _run(scrape_tresor(start, end))

        assert len(events) == 1
        ev = events[0]
        assert ev["venue_name"] == "Tresor"
        assert ev["title"] == "Tresor Techno Night"
        assert ev["images"] == [{"filename": "https://tresorberlin.com/flyer.jpg"}]

        artists = ev["_prefilled_artists_info"]
        assert len(artists) == 3
        assert artists[0]["name"] == "DJ Alpha"
        assert artists[0]["floor"] == "Tresor"
        assert artists[0]["soundcloud"] == "https://soundcloud.com/dj-alpha"
        assert artists[1]["name"] == "DJ Beta"
        assert artists[1]["soundcloud"] is None  # ra.co link, not SC
        assert artists[2]["name"] == "DJ Gamma"
        assert artists[2]["floor"] == "OHM"
        assert artists[2]["soundcloud"] == "https://soundcloud.com/dj-gamma"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_out_of_range_skipped(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20250101-old-event">01.01</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>Old DJ</span></div>
            </div>
        </article>
        </body></html>
        """
        mock_client.get.return_value = listing_resp

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 0

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_date_parsing_from_url(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260419-some-event">19.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>DJ Test</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = "<html><body></body></html>"

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        assert events[0]["event_date"].year == 2026
        assert events[0]["event_date"].month == 4
        assert events[0]["event_date"].day == 19

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_detail_page_failure_falls_back_to_listing_artists(self, mock_get_client):
        import httpx

        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-night">18.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>Listing DJ</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status.side_effect = httpx.HTTPError("timeout")

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        assert len(artists) == 1
        assert artists[0]["name"] == "Listing DJ"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_http_error_returns_empty(self, mock_get_client):
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.HTTPError("connection refused")
        mock_get_client.return_value = mock_client

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert events == []

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_floor_separated_lineup_from_detail(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-multi-floor">18.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>Listing Only</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="lineup">
            <div class="floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <a class="lineup-item" href="#">
                    <div class="lineup-name">Detail DJ One</div>
                </a>
            </div>
            <div class="floor" data-floor="globus">
                <div class="floor-name">Globus</div>
                <a class="lineup-item" href="https://soundcloud.com/detail-dj-two">
                    <div class="lineup-name">Detail DJ Two</div>
                </a>
            </div>
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        # Detail page artists should be used instead of listing page
        assert len(artists) == 2
        assert artists[0]["name"] == "Detail DJ One"
        assert artists[0]["floor"] == "Tresor"
        assert artists[1]["name"] == "Detail DJ Two"
        assert artists[1]["floor"] == "Globus"
        assert artists[1]["soundcloud"] == "https://soundcloud.com/detail-dj-two"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_live_tag_stripped(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-live-night">18.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>Artist LIVE</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="lineup">
            <div class="floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <a class="lineup-item" href="#">
                    <div class="lineup-name">Artist [LIVE]</div>
                </a>
            </div>
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        assert len(artists) == 1
        assert artists[0]["name"] == "Artist"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_b2b_split_in_detail(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-b2b">18.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>DJ One b2b DJ Two</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="lineup">
            <div class="floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <a class="lineup-item" href="#">
                    <div class="lineup-name">DJ One b2b DJ Two</div>
                </a>
            </div>
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        assert len(artists) == 2
        assert artists[0]["name"] == "DJ One"
        assert artists[1]["name"] == "DJ Two"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_all_night_long_filtered(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-anl">18.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>DJ Real</span></div>
                <div class="floor-artist"><span>all night long</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = "<html><body></body></html>"

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        assert len(artists) == 1
        assert artists[0]["name"] == "DJ Real"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_sc_www_normalized(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-sc">18.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>SC Artist</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = """
        <html><body>
        <div class="lineup">
            <div class="floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <a class="lineup-item" href="https://www.soundcloud.com/sc-artist">
                    <div class="lineup-name">SC Artist</div>
                </a>
            </div>
        </div>
        </body></html>
        """

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
        artists = events[0]["_prefilled_artists_info"]
        assert artists[0]["soundcloud"] == "https://soundcloud.com/sc-artist"

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_no_artists_event_skipped(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-empty">18.04</a>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = "<html><body></body></html>"

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        # Events with no artists from either listing or detail are skipped
        assert len(events) == 0

    @patch("cuepoint.club_scrapers._get_client", new_callable=AsyncMock)
    def test_deduplicates_event_links(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        listing_resp = MagicMock()
        listing_resp.raise_for_status = MagicMock()
        listing_resp.text = """
        <html><body>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-night">18.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>DJ Alpha</span></div>
            </div>
        </article>
        <article class="event-item">
            <div class="event-date">
                <a class="plus-link" href="/event/20260418-night">18.04</a>
            </div>
            <div class="event-floor" data-floor="tresor">
                <div class="floor-name">Tresor</div>
                <div class="floor-artist"><span>DJ Alpha</span></div>
            </div>
        </article>
        </body></html>
        """

        detail_resp = MagicMock()
        detail_resp.raise_for_status = MagicMock()
        detail_resp.text = "<html><body></body></html>"

        mock_client.get.side_effect = [listing_resp, detail_resp]

        events = _run(scrape_tresor(datetime(2026, 4, 15), datetime(2026, 4, 20)))
        assert len(events) == 1
