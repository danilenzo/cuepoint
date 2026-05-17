"""End-to-end pipeline tests with mocked HTTP responses.

Exercises the full fetch -> enrich -> filter -> sort -> HTML generation pipeline
with canned responses for RA GraphQL, SoundCloud, Discogs, and Bandcamp.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

import httpx

# ---------------------------------------------------------------------------
# Canned RA GraphQL response
# ---------------------------------------------------------------------------


def _ra_event_listings_response(events=None):
    """Build a canned RA GraphQL eventListings response."""
    if events is None:
        events = [
            {
                "id": "lst-001",
                "listingDate": "2026-04-01",
                "event": {
                    "id": "evt-100",
                    "date": "2026-04-01",
                    "startTime": "2026-04-01T23:00:00.000",
                    "endTime": "2026-04-02T08:00:00.000",
                    "title": "E2E Test Event",
                    "contentUrl": "/events/100",
                    "isTicketed": True,
                    "attending": 200,
                    "venue": {
                        "id": "v-1",
                        "name": "Test Club",
                        "contentUrl": "/club/test-club",
                        "live": True,
                    },
                    "images": [{"id": "img-1", "filename": "flyer.jpg", "alt": "", "type": "IMAGE", "crop": None}],
                    "artists": [
                        {"id": "a-1", "name": "Alpha Artist", "__typename": "Artist"},
                        {"id": "a-2", "name": "Beta Artist", "__typename": "Artist"},
                    ],
                    "promoters": [
                        {
                            "id": "p-1",
                            "name": "Test Promo",
                            "contentUrl": "/promoter/1",
                            "live": True,
                            "hasTicketAccess": False,
                            "__typename": "Promoter",
                        }
                    ],
                    "tickets": [],
                    "genres": [{"id": "g-1", "name": "Techno", "slug": "techno", "__typename": "Genre"}],
                    "pick": None,
                    "flyerFront": None,
                    "queueItEnabled": False,
                    "newEventForm": False,
                    "__typename": "Event",
                },
                "__typename": "EventListing",
            }
        ]
    return {
        "data": {
            "eventListings": {
                "data": events,
                "filterOptions": {"genre": [], "__typename": "FilterOptions"},
                "totalResults": len(events),
                "__typename": "EventListings",
            }
        }
    }


def _ra_artist_response(artist_id, name, sc_url=None):
    """Build a canned RA artist-by-id response."""
    return {
        "data": {
            "artist": {
                "id": artist_id,
                "name": name,
                "followerCount": 50,
                "contentUrl": f"/dj/{name.lower().replace(' ', '')}",
                "soundcloud": sc_url or f"/{name.lower().replace(' ', '-')}",
                "discogs": None,
                "bandcamp": None,
                "country": {"id": "c-1", "name": "DE", "urlCode": "de", "__typename": "Country"},
                "__typename": "Artist",
            }
        }
    }


# ---------------------------------------------------------------------------
# Fake httpx transport that routes requests to canned responses
# ---------------------------------------------------------------------------


class _FakeTransport(httpx.AsyncBaseTransport):
    """Return canned responses based on request content."""

    def __init__(self):
        self._event_pages_served = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        # RA GraphQL POST
        if "ra.co/graphql" in url and request.method == "POST":
            body = json.loads(request.content.decode())
            op = body.get("operationName", "")

            if op == "GET_EVENT_LISTINGS":
                # First call returns events, second returns empty to stop pagination
                if self._event_pages_served == 0:
                    self._event_pages_served += 1
                    payload = _ra_event_listings_response()
                else:
                    payload = _ra_event_listings_response(events=[])
                return httpx.Response(200, json=payload)

            if op in ("GET_ARTIST_BY_ID", "GET_ARTIST_BY_SLUG"):
                aid = body.get("variables", {}).get("id", "unknown")
                name_map = {"a-1": "Alpha Artist", "a-2": "Beta Artist"}
                name = name_map.get(str(aid), f"Artist-{aid}")
                return httpx.Response(200, json=_ra_artist_response(aid, name))

        # SoundCloud API (any GET to api-v2.soundcloud.com)
        if "soundcloud.com" in url:
            return httpx.Response(200, json={"followers_count": 1200, "genre": "Techno"})

        # Discogs API
        if "api.discogs.com" in url:
            return httpx.Response(200, json={"results": []})

        # Bandcamp
        if "bandcamp.com" in url:
            return httpx.Response(200, text="<html><body>No results</body></html>")

        # Flyer image download
        if "flyer" in url.lower() or "image" in url.lower():
            # Return a tiny valid JPEG (1x1 pixel)
            return httpx.Response(200, content=b"", headers={"content-type": "image/jpeg"})

        # Default
        return httpx.Response(200, json={})


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestPipelineE2E:
    """End-to-end pipeline test with mocked HTTP."""

    def _run_async(self, coro):
        """Run an async coroutine in a new event loop."""
        return asyncio.run(coro)

    def test_full_pipeline_with_mocked_responses(self, tmp_db, mock_config, tmp_path, monkeypatch):
        """Mock HTTP to return canned RA/SC/Discogs/Bandcamp responses,
        run the full pipeline, and verify it produces valid output."""
        from cuepoint import event_fetcher as ef

        # Ensure the output directory exists under tmp_path
        output_dir = str(tmp_path / "output") + "/"
        os.makedirs(output_dir, exist_ok=True)
        monkeypatch.setattr(ef, "OUTPUT_PATH", output_dir)

        # Disable incremental mode to simplify
        from cuepoint import config as cfg

        monkeypatch.setattr(cfg, "incremental", lambda: False)

        # Ensure CITIES has our test city
        monkeypatch.setattr(
            ef,
            "CITIES",
            {
                "testcity": (999, "TestCity", "xx/testcity"),
            },
        )

        # Set DELAY to 0 for speed
        monkeypatch.setattr(ef, "DELAY", 0)

        # Create a fake async client with our transport
        fake_transport = _FakeTransport()

        async def _fake_get_ra_client():
            return httpx.AsyncClient(transport=fake_transport, base_url="https://ra.co")

        monkeypatch.setattr(ef, "_get_ra_client", _fake_get_ra_client)

        # Mock enrichment to skip real SC/Discogs/Bandcamp calls
        async def _mock_enrich_batch(artist_ids, get_urls_fn, **kwargs):
            result = {}
            for aid in artist_ids:
                info = await get_urls_fn(aid)
                if info is not None:
                    info["sc_followers"] = 1500
                    info["sc_tags"] = json.dumps(["Techno"])
                    info["dc_have"] = 100
                    info["dc_want"] = 80
                    info["dc_ratio"] = 1.25
                    info["dc_rating"] = 4.0
                    info["dc_styles"] = json.dumps(["Techno"])
                    info["dc_labels"] = json.dumps(["Test Label"])
                    info["bc_tags"] = json.dumps(["techno"])
                    info["bc_supporters"] = 50
                    info["bc_latest_release"] = "2026-01-01"
                    result[aid] = info
            return result

        monkeypatch.setattr(ef, "enrich_batch_phased", _mock_enrich_batch)

        # Mock embed_flyers to avoid image downloads
        async def _mock_embed_flyers(urls):
            return [None] * len(urls)

        monkeypatch.setattr(ef, "embed_flyers", _mock_embed_flyers)

        # Mock club scrapers to return nothing
        async def _mock_scrape_clubs(city_name, start, end):
            return []

        monkeypatch.setattr(ef, "scrape_city_clubs", _mock_scrape_clubs)

        # Mock is_cache_stale to always return False
        monkeypatch.setattr(ef, "is_cache_stale", lambda aid: False)

        # Mock get_cached_artist to always return None (no cache)
        monkeypatch.setattr(ef, "get_cached_artist", lambda aid: None)

        # Mock discovery functions to be no-ops
        monkeypatch.setattr(ef, "_compute_similarity", lambda lookup: None)
        monkeypatch.setattr(ef, "_compute_label_affinity", lambda lookup: None)

        async def _run():
            try:
                result = await ef.run_for_city(
                    "testcity",
                    datetime(2026, 4, 1),
                    days_ahead=7,
                )
                return result
            finally:
                await ef.close_clients()

        result = self._run_async(_run())

        # Verify the result dict
        assert result is not None
        assert result["city"] == "TestCity"
        assert result["events"] >= 1, f"Expected at least 1 event, got {result['events']}"
        assert result["file_path"] is not None
        assert result["file_path"].endswith(".html")

        # Verify the HTML file was created
        assert os.path.exists(result["file_path"]), f"HTML file not created: {result['file_path']}"
        with open(result["file_path"], encoding="utf-8") as f:
            html_content = f.read()
        assert len(html_content) > 100
        assert "E2E Test Event" in html_content

    def test_empty_city_returns_zero_events(self, tmp_db, mock_config, tmp_path, monkeypatch):
        """When RA returns an empty event list, the pipeline should
        return a result dict with events=0 and no file."""
        from cuepoint import config as cfg
        from cuepoint import event_fetcher as ef

        output_dir = str(tmp_path / "output") + "/"
        os.makedirs(output_dir, exist_ok=True)
        monkeypatch.setattr(ef, "OUTPUT_PATH", output_dir)
        monkeypatch.setattr(cfg, "incremental", lambda: False)
        monkeypatch.setattr(
            ef,
            "CITIES",
            {
                "emptycity": (888, "EmptyCity", "xx/emptycity"),
            },
        )
        monkeypatch.setattr(ef, "DELAY", 0)

        # Transport that always returns empty listings
        class _EmptyTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json=_ra_event_listings_response(events=[]))

        async def _fake_get_ra_client():
            return httpx.AsyncClient(transport=_EmptyTransport(), base_url="https://ra.co")

        monkeypatch.setattr(ef, "_get_ra_client", _fake_get_ra_client)

        async def _mock_enrich_batch(artist_ids, get_urls_fn, **kwargs):
            return {}

        monkeypatch.setattr(ef, "enrich_batch_phased", _mock_enrich_batch)

        async def _mock_embed_flyers(urls):
            return [None] * len(urls)

        monkeypatch.setattr(ef, "embed_flyers", _mock_embed_flyers)

        async def _mock_scrape_clubs(city_name, start, end):
            return []

        monkeypatch.setattr(ef, "scrape_city_clubs", _mock_scrape_clubs)

        monkeypatch.setattr(ef, "is_cache_stale", lambda aid: False)
        monkeypatch.setattr(ef, "get_cached_artist", lambda aid: None)
        monkeypatch.setattr(ef, "_compute_similarity", lambda lookup: None)
        monkeypatch.setattr(ef, "_compute_label_affinity", lambda lookup: None)

        async def _run():
            try:
                result = await ef.run_for_city(
                    "emptycity",
                    datetime(2026, 4, 1),
                    days_ahead=7,
                )
                return result
            finally:
                await ef.close_clients()

        result = self._run_async(_run())

        assert result is not None
        assert result["city"] == "EmptyCity"
        assert result["events"] == 0
