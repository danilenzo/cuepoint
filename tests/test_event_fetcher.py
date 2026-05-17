"""Tests for event_fetcher.py — ScanContext, EventFetcher, parse_events_list."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from cuepoint.event_fetcher import EventFetcher, ScanContext, close_clients, parse_events_list

_run = asyncio.run


def _make_ra_event(event_id: str = "evt-1", title: str = "Test Event") -> dict[str, Any]:
    return {
        "id": f"lst-{event_id}",
        "listingDate": "2026-04-01",
        "event": {
            "id": event_id,
            "date": "2026-04-01",
            "startTime": "2026-04-01T23:00:00.000",
            "endTime": "2026-04-02T08:00:00.000",
            "title": title,
            "contentUrl": f"/events/{event_id}",
            "isTicketed": True,
            "attending": 200,
            "venue": {"id": "v-1", "name": "Club", "contentUrl": "/club/club"},
            "images": [{"filename": "flyer.jpg"}],
            "artists": [{"id": "a-1", "name": "DJ Test"}],
            "promoters": [{"name": "Promo"}],
            "tickets": [],
            "genres": [{"name": "Techno"}],
        },
    }


class TestScanContext:
    def test_frozen(self):
        ctx = ScanContext(
            area=34, city_name="Berlin", city_slug="de/berlin", start_date=datetime(2026, 4, 1), days_ahead=7
        )
        with pytest.raises(AttributeError):
            ctx.area = 99  # type: ignore[misc]

    def test_end_date(self):
        ctx = ScanContext(
            area=34, city_name="Berlin", city_slug="de/berlin", start_date=datetime(2026, 4, 1), days_ahead=7
        )
        assert ctx.end_date == datetime(2026, 4, 8)

    def test_end_date_zero_days(self):
        ctx = ScanContext(
            area=34, city_name="Berlin", city_slug="de/berlin", start_date=datetime(2026, 4, 1), days_ahead=0
        )
        assert ctx.end_date == ctx.start_date


class TestParseEventsList:
    def test_single_event(self):
        df = parse_events_list([_make_ra_event()])
        assert len(df) == 1
        assert df.iloc[0]["title"] == "Test Event"
        assert df.iloc[0]["venue_name"] == "Club"

    def test_empty_list(self):
        df = parse_events_list([])
        assert df.empty
        assert "title" in df.columns

    def test_multiple_events(self):
        events = [_make_ra_event("e1", "Event A"), _make_ra_event("e2", "Event B")]
        df = parse_events_list(events)
        assert len(df) == 2

    def test_datetime_columns_parsed(self):
        df = parse_events_list([_make_ra_event()])
        assert pd.api.types.is_datetime64_any_dtype(df["event_date"])
        assert pd.api.types.is_datetime64_any_dtype(df["start_time"])

    def test_event_url_includes_ra(self):
        df = parse_events_list([_make_ra_event()])
        assert df.iloc[0]["event_url"].startswith("https://ra.co")


class TestEventFetcher:
    def test_generate_payload(self):
        p = EventFetcher.generate_payload(34, "2026-01-01", "2026-01-07")
        assert p["operationName"] == "GET_EVENT_LISTINGS"
        assert p["variables"]["filters"]["areas"]["eq"] == 34

    @patch("cuepoint.event_fetcher._get_ra_client")
    def test_get_events_success(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"eventListings": {"data": [_make_ra_event()]}}}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        ef = EventFetcher("https://ra.co/events/de/berlin", 34, "2026-01-01", "2026-01-07")
        events = _run(ef.get_events(1))
        assert len(events) == 1

    @patch("cuepoint.event_fetcher._get_ra_client")
    def test_get_events_api_error_returns_empty(self, mock_client_fn):
        import httpx

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("down")
        mock_client_fn.return_value = mock_client

        ef = EventFetcher("https://ra.co/events/de/berlin", 34, "2026-01-01", "2026-01-07")
        events = _run(ef.get_events(1))
        assert events == []

    @patch("cuepoint.event_fetcher._get_ra_client")
    def test_get_events_no_data_key(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errors": ["bad"]}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client_fn.return_value = mock_client

        ef = EventFetcher("https://ra.co/events/de/berlin", 34, "2026-01-01", "2026-01-07")
        events = _run(ef.get_events(1))
        assert events == []

    @patch("cuepoint.event_fetcher._get_ra_client")
    @patch("cuepoint.event_fetcher.DELAY", 0)
    def test_fetch_all_events_pagination(self, mock_client_fn):
        call_count = [0]
        mock_client = AsyncMock()

        def _post(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count[0] == 1:
                resp.json.return_value = {"data": {"eventListings": {"data": [_make_ra_event("e1")]}}}
            else:
                resp.json.return_value = {"data": {"eventListings": {"data": []}}}
            return resp

        mock_client.post = AsyncMock(side_effect=_post)
        mock_client_fn.return_value = mock_client

        ef = EventFetcher("https://ra.co/events/de/berlin", 34, "2026-01-01", "2026-01-07")
        events = _run(ef.fetch_all_events())
        assert len(events) == 1
        assert call_count[0] == 2


class TestGetArtistUrls:
    @patch("cuepoint.event_fetcher._fetch_ra_artist")
    def test_cache_hit(self, mock_fetch, tmp_db):
        from cuepoint import db as store
        from cuepoint.event_fetcher import get_artist_urls

        store.save_artist_url("123", {"id": "123", "name": "Cached"})
        result = _run(get_artist_urls("123"))
        assert result["name"] == "Cached"
        mock_fetch.assert_not_called()

    @patch("cuepoint.event_fetcher._fetch_ra_artist")
    def test_cache_miss_fetches(self, mock_fetch, tmp_db):
        from cuepoint.event_fetcher import get_artist_urls

        mock_fetch.return_value = {
            "id": "456",
            "name": "New Artist",
            "soundcloud": "/new-artist",
            "discogs": None,
            "bandcamp": None,
            "contentUrl": "/dj/new",
            "country": "DE",
            "followerCount": 5000,
        }
        result = _run(get_artist_urls("456"))
        assert result["name"] == "New Artist"
        assert result["ra_followers"] == 5000

    @patch("cuepoint.event_fetcher._fetch_ra_artist")
    def test_fetch_returns_none(self, mock_fetch, tmp_db):
        from cuepoint.event_fetcher import get_artist_urls

        mock_fetch.return_value = None
        result = _run(get_artist_urls("789"))
        assert result is None


class TestCloseClients:
    @patch("cuepoint.sc.close_client", new_callable=AsyncMock)
    @patch("cuepoint.discogs.close_client", new_callable=AsyncMock)
    @patch("cuepoint.bandcamp.close_client", new_callable=AsyncMock)
    @patch("cuepoint.club_scrapers.close_client", new_callable=AsyncMock)
    def test_close_all(self, mock_clubs, mock_bc, mock_dc, mock_sc):
        import cuepoint.event_fetcher as mod

        mod._ra_client = AsyncMock()

        _run(close_clients())

        mock_sc.assert_awaited_once()
        mock_dc.assert_awaited_once()
        mock_bc.assert_awaited_once()
        mock_clubs.assert_awaited_once()
        assert mod._ra_client is None
