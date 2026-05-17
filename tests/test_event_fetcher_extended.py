"""Extended tests for event_fetcher.py — covers get_data dedup, incremental scan,
club event merging, run_for_city, _record_enrichment_health, and run_cities_parallel."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from cuepoint.event_fetcher import (
    ScanContext,
    _record_enrichment_health,
    get_data,
    run_cities_parallel,
    run_for_city,
)
from cuepoint.stats import ScanStats

_run = asyncio.run


def _make_ra_event(
    event_id: str = "evt-1",
    title: str = "Test Event",
    venue_name: str = "Club",
    venue_id: str = "v-1",
    event_date: str = "2026-04-01",
    attending: int = 200,
    artists: list[dict[str, Any]] | None = None,
    flyer: str | None = "flyer.jpg",
) -> dict[str, Any]:
    if artists is None:
        artists = [{"id": "a-1", "name": "DJ Test"}]
    images = [{"filename": flyer}] if flyer else []
    return {
        "id": f"lst-{event_id}",
        "listingDate": event_date,
        "event": {
            "id": event_id,
            "date": event_date,
            "startTime": f"{event_date}T23:00:00.000",
            "endTime": f"{event_date}T08:00:00.000",
            "title": title,
            "contentUrl": f"/events/{event_id}",
            "isTicketed": True,
            "attending": attending,
            "venue": {"id": venue_id, "name": venue_name, "contentUrl": f"/club/{venue_name.lower()}"},
            "images": images,
            "artists": artists,
            "promoters": [{"name": "Promo"}],
            "tickets": [],
            "genres": [{"name": "Techno"}],
        },
    }


def _make_ctx(city_name: str = "TestCity", area: int = 99, slug: str = "xx/testcity") -> ScanContext:
    return ScanContext(
        area=area,
        city_name=city_name,
        city_slug=slug,
        start_date=datetime(2026, 4, 1),
        days_ahead=7,
    )


def _stub_artist(aid: str, name: str = "Artist") -> dict[str, Any]:
    return {
        "id": aid,
        "name": name,
        "soundcloud": f"/{name.lower().replace(' ', '-')}",
        "discogs": None,
        "bandcamp": None,
        "contentUrl": f"/dj/{name.lower()}",
        "country": "DE",
        "sc_followers": 1000,
        "sc_tags": "[]",
        "dc_styles": "[]",
        "dc_labels": "[]",
        "bc_tags": "[]",
    }


# ---------------------------------------------------------------------------
# Shared patches for get_data tests
# ---------------------------------------------------------------------------

_GET_DATA_PATCHES = {
    "cuepoint.event_fetcher.enrich_batch_phased": "mock_enrich",
    "cuepoint.event_fetcher.enrich_club_batch_phased": "mock_club_enrich",
    "cuepoint.event_fetcher.scrape_city_clubs": "mock_clubs",
    "cuepoint.event_fetcher.embed_flyers": "mock_embed",
    "cuepoint.event_fetcher.get_flyer": "mock_get_flyer",
    "cuepoint.event_fetcher.get_artist_urls": "mock_get_urls",
    "cuepoint.event_fetcher._compute_similarity": "mock_sim",
    "cuepoint.event_fetcher._compute_label_affinity": "mock_label",
    "cuepoint.event_fetcher.get_cached_artist": "mock_cached",
    "cuepoint.event_fetcher.is_cache_stale": "mock_stale",
}


def _apply_get_data_defaults(mocks: dict[str, MagicMock]) -> None:
    """Set sensible defaults on all mock objects for a get_data call."""
    mocks["mock_clubs"].return_value = []
    mocks["mock_get_flyer"].return_value = None
    mocks["mock_embed"].return_value = [None]
    mocks["mock_sim"].return_value = None
    mocks["mock_label"].return_value = None
    mocks["mock_cached"].return_value = None
    mocks["mock_stale"].return_value = False


# ═══════════════════════════════════════════════════════════════════════════
# get_data() — event dedup (lines 270-281)
# ═══════════════════════════════════════════════════════════════════════════


class TestGetDataDedup:
    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock, return_value=[])
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_duplicate_events_deduped(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """Two events with same event_id -> only one survives dedup."""
        evt = _make_ra_event("dup-1", "Same Event")
        mock_fetch.return_value = [evt, evt]

        lookup = {"a-1": _stub_artist("a-1", "DJ Test")}
        mock_enrich.return_value = lookup
        mock_embed.return_value = [None]
        mock_cfg.incremental.return_value = False

        ctx = _make_ctx()
        df = _run(get_data(ctx))
        assert len(df) == 1
        assert df.iloc[0]["event_id"] == "dup-1"

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock, return_value=[])
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_unique_events_no_dedup(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """All unique events -> no dedup, all present."""
        e1 = _make_ra_event("evt-1", "Event One")
        e2 = _make_ra_event("evt-2", "Event Two", artists=[{"id": "a-2", "name": "DJ Two"}])
        mock_fetch.return_value = [e1, e2]

        lookup = {
            "a-1": _stub_artist("a-1", "DJ Test"),
            "a-2": _stub_artist("a-2", "DJ Two"),
        }
        mock_enrich.return_value = lookup
        mock_embed.return_value = [None, None]
        mock_cfg.incremental.return_value = False

        ctx = _make_ctx()
        df = _run(get_data(ctx))
        assert len(df) == 2


# ═══════════════════════════════════════════════════════════════════════════
# get_data() — incremental scan logic (lines 289-362)
# ═══════════════════════════════════════════════════════════════════════════


class TestGetDataIncremental:
    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock, return_value=[])
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_incremental_unchanged_skips_enrichment(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """Incremental enabled + unchanged lineup hash -> artist skipped from unique_artist_ids."""
        import hashlib
        import json

        evt = _make_ra_event("evt-1", "Party", artists=[{"id": "a-1", "name": "DJ Test"}])
        mock_fetch.return_value = [evt]

        # Compute the expected lineup hash
        lineup_hash = hashlib.sha256(json.dumps(["a-1"]).encode()).hexdigest()

        mock_cfg.incremental.return_value = True
        mock_store.get_scan_snapshot.return_value = {"evt-1": lineup_hash}
        mock_store.has_cached_artist.return_value = True

        # enrich_batch_phased should receive an empty list (skipped)
        mock_enrich.return_value = {}
        mock_embed.return_value = [None]
        # The skipped artist should be loaded from cache
        mock_cached.return_value = _stub_artist("a-1", "DJ Test")

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        # enrich_batch_phased called with empty list since lineup is unchanged and cached
        called_ids = mock_enrich.call_args[0][0]
        assert len(called_ids) == 0
        assert len(df) == 1

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock, return_value=[])
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_incremental_changed_lineup_re_enriches(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """Incremental enabled + different lineup hash -> artist sent for enrichment."""
        evt = _make_ra_event("evt-1", "Party", artists=[{"id": "a-1", "name": "DJ Test"}])
        mock_fetch.return_value = [evt]

        mock_cfg.incremental.return_value = True
        mock_store.get_scan_snapshot.return_value = {"evt-1": "old-different-hash"}
        mock_store.has_cached_artist.return_value = False

        lookup = {"a-1": _stub_artist("a-1", "DJ Test")}
        mock_enrich.return_value = lookup
        mock_embed.return_value = [None]

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        called_ids = mock_enrich.call_args[0][0]
        assert "a-1" in called_ids
        assert len(df) == 1

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock, return_value=[])
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_incremental_first_scan_full_enrichment(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """Incremental enabled but no previous snapshot -> full enrichment."""
        evt = _make_ra_event("evt-1", "Party", artists=[{"id": "a-1", "name": "DJ Test"}])
        mock_fetch.return_value = [evt]

        mock_cfg.incremental.return_value = True
        mock_store.get_scan_snapshot.return_value = {}  # No prev snapshot

        lookup = {"a-1": _stub_artist("a-1", "DJ Test")}
        mock_enrich.return_value = lookup
        mock_embed.return_value = [None]

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        called_ids = mock_enrich.call_args[0][0]
        assert "a-1" in called_ids
        assert len(df) == 1
        # Verify snapshot is saved
        mock_store.save_scan_snapshot.assert_called_once()

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist")
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock, return_value=[])
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_skipped_artists_loaded_from_cache(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """Artists not in artist_lookup after enrichment are loaded from cache (lines 345-353)."""
        import hashlib
        import json

        evt = _make_ra_event(
            "evt-1",
            "Party",
            artists=[{"id": "a-1", "name": "DJ Test"}, {"id": "a-2", "name": "DJ Two"}],
        )
        mock_fetch.return_value = [evt]

        lineup_hash = hashlib.sha256(json.dumps(["a-1", "a-2"]).encode()).hexdigest()
        mock_cfg.incremental.return_value = True
        mock_store.get_scan_snapshot.return_value = {"evt-1": lineup_hash}
        mock_store.has_cached_artist.return_value = True

        # enrich_batch_phased returns empty (all skipped)
        mock_enrich.return_value = {}
        mock_embed.return_value = [None]

        # get_cached_artist returns data for both
        cached_a1 = _stub_artist("a-1", "DJ Test")
        cached_a2 = _stub_artist("a-2", "DJ Two")
        mock_cached.side_effect = lambda aid: cached_a1 if str(aid) == "a-1" else cached_a2

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        assert len(df) == 1
        # Both artists should appear in artists_info (loaded from cache)
        info = df.iloc[0]["artists_info"]
        assert len(info) == 2

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale")
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock, return_value=[])
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_stale_artists_re_enriched(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_stale,
        mock_cached,
        mock_store,
        mock_cfg,
    ):
        """Stale artists trigger delete + re-enrichment (lines 356-362)."""
        evt = _make_ra_event("evt-1", "Party", artists=[{"id": "a-1", "name": "DJ Test"}])
        mock_fetch.return_value = [evt]
        mock_cfg.incremental.return_value = False

        first_lookup = {"a-1": _stub_artist("a-1", "DJ Test")}
        refreshed_lookup = {"a-1": {**_stub_artist("a-1", "DJ Test"), "sc_followers": 9999}}

        # First call returns the initial lookup, second (re-enrich) returns refreshed
        mock_enrich.side_effect = [first_lookup, refreshed_lookup]
        mock_embed.return_value = [None]

        # Mark artist as stale
        mock_stale.return_value = True

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        # enrich_batch_phased called twice (initial + stale re-enrichment)
        assert mock_enrich.call_count == 2
        # delete_cached_artist should have been called
        mock_store.delete_cached_artist.assert_called_with("a-1")
        assert len(df) == 1


# ═══════════════════════════════════════════════════════════════════════════
# get_data() — club event merging (lines 388-470)
# ═══════════════════════════════════════════════════════════════════════════


class TestGetDataClubMerging:
    def _make_club_event(
        self,
        event_id: str = "club-1",
        title: str = "Club Night",
        venue_name: str = "Berghain",
        event_date: str = "2026-04-01",
        attending: int = 0,
        flyer: str | None = None,
        artists_info: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if artists_info is None:
            artists_info = [
                {
                    "id": "stub-1",
                    "name": "Club DJ",
                    "soundcloud": None,
                    "discogs": None,
                    "bandcamp": None,
                }
            ]
        return {
            "listing_id": f"lst-{event_id}",
            "listing_date": event_date,
            "event_id": event_id,
            "event_date": event_date,
            "start_time": f"{event_date}T23:00:00.000",
            "end_time": f"{event_date}T08:00:00.000",
            "title": title,
            "content_url": f"/events/{event_id}",
            "event_url": f"https://club.example.com/events/{event_id}",
            "is_ticketed": True,
            "attending": attending,
            "venue_id": "cv-1",
            "venue_name": venue_name,
            "venue_url": f"/club/{venue_name.lower()}",
            "images": [{"filename": flyer}] if flyer else [],
            "artists": [{"id": a["id"], "name": a["name"]} for a in artists_info],
            "promoters": [],
            "tickets": [],
            "genres": [{"name": "Techno"}],
            "_prefilled_artists_info": artists_info,
        }

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.enrich_club_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_club_events_appended(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_club_enrich,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """Club events are appended to RA results when no duplicates."""
        # One RA event at a different venue
        ra_evt = _make_ra_event("ra-1", "RA Event", venue_name="OtherVenue", event_date="2026-04-02")
        mock_fetch.return_value = [ra_evt]
        mock_cfg.incremental.return_value = False

        lookup = {"a-1": _stub_artist("a-1", "DJ Test")}
        mock_enrich.return_value = lookup

        # Club event at a different venue/date
        club_evt = self._make_club_event("club-1", "Club Night", "Berghain", "2026-04-01")
        mock_clubs.return_value = [club_evt]
        mock_club_enrich.return_value = {"stub-1": _stub_artist("stub-1", "Club DJ")}
        mock_embed.return_value = [None]

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        # Should have 2 events (1 RA + 1 club)
        assert len(df) == 2
        titles = set(df["title"])
        assert "RA Event" in titles
        assert "Club Night" in titles

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.enrich_club_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_ra_duplicate_removed_when_club_has_same_venue_and_date(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_club_enrich,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """RA event with same venue + date as club event -> RA event dropped."""
        ra_evt = _make_ra_event("ra-1", "Berghain Night", venue_name="Berghain", event_date="2026-04-01")
        mock_fetch.return_value = [ra_evt]
        mock_cfg.incremental.return_value = False

        lookup = {"a-1": _stub_artist("a-1", "DJ Test")}
        mock_enrich.return_value = lookup

        club_evt = self._make_club_event("club-1", "Berghain Night (club)", "Berghain", "2026-04-01")
        mock_clubs.return_value = [club_evt]
        mock_club_enrich.return_value = {"stub-1": _stub_artist("stub-1", "Club DJ")}
        mock_embed.return_value = [None]

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        # RA event should be dropped, club event kept
        assert len(df) == 1
        assert df.iloc[0]["title"] == "Berghain Night (club)"

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer")
    @patch("cuepoint.event_fetcher.enrich_club_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_ra_flyer_and_attending_merged_into_club_event(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_club_enrich,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """RA flyer/attending merged into club event when club event missing them."""
        ra_evt = _make_ra_event(
            "ra-1",
            "Berghain Night",
            venue_name="Berghain",
            event_date="2026-04-01",
            attending=500,
            flyer="ra_flyer.jpg",
        )
        mock_fetch.return_value = [ra_evt]
        mock_cfg.incremental.return_value = False

        lookup = {"a-1": _stub_artist("a-1", "DJ Test")}
        mock_enrich.return_value = lookup

        club_evt = self._make_club_event("club-1", "Berghain Night", "Berghain", "2026-04-01", attending=0, flyer=None)
        mock_clubs.return_value = [club_evt]
        mock_club_enrich.return_value = {"stub-1": _stub_artist("stub-1", "Club DJ")}

        # get_flyer: return a "ra_flyer_url" for the RA event, None for club
        def _get_flyer_side(event_dict: dict) -> str | None:
            if "ra_flyer.jpg" in str(event_dict.get("images", [])):
                return "https://example.com/ra_flyer.jpg"
            return None

        mock_get_flyer.side_effect = _get_flyer_side
        # embed_flyers: first call for RA events, second for club events
        mock_embed.side_effect = [
            ["https://example.com/ra_flyer_embedded.jpg"],
            [None],
        ]

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        # Club event should have the RA attending merged
        club_row = df[df["title"] == "Berghain Night"]
        assert len(club_row) == 1
        assert club_row.iloc[0]["attending"] == 500

    @patch("cuepoint.event_fetcher.cfg")
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.is_cache_stale", return_value=False)
    @patch("cuepoint.event_fetcher.get_cached_artist", return_value=None)
    @patch("cuepoint.event_fetcher._compute_label_affinity")
    @patch("cuepoint.event_fetcher._compute_similarity")
    @patch("cuepoint.event_fetcher.get_artist_urls", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.embed_flyers", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.get_flyer", return_value=None)
    @patch("cuepoint.event_fetcher.enrich_club_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.scrape_city_clubs", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.enrich_batch_phased", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.EventFetcher.fetch_all_events", new_callable=AsyncMock)
    def test_club_title_containment_match(
        self,
        mock_fetch,
        mock_enrich,
        mock_clubs,
        mock_club_enrich,
        mock_get_flyer,
        mock_embed,
        mock_get_urls,
        mock_sim,
        mock_label,
        mock_cached,
        mock_stale,
        mock_store,
        mock_cfg,
    ):
        """Title containment check: RA title contained in club title -> best match."""
        ra_evt = _make_ra_event("ra-1", "Klubnacht", venue_name="Berghain", event_date="2026-04-01", attending=300)
        mock_fetch.return_value = [ra_evt]
        mock_cfg.incremental.return_value = False

        lookup = {"a-1": _stub_artist("a-1", "DJ Test")}
        mock_enrich.return_value = lookup

        # Club event title contains the RA title
        club_evt = self._make_club_event("club-1", "Klubnacht — April Edition", "Berghain", "2026-04-01", attending=0)
        mock_clubs.return_value = [club_evt]
        mock_club_enrich.return_value = {"stub-1": _stub_artist("stub-1", "Club DJ")}
        mock_embed.return_value = [None]

        ctx = _make_ctx()
        df = _run(get_data(ctx))

        # RA event dropped, club event kept with attending merged
        assert len(df) == 1
        assert "Klubnacht" in df.iloc[0]["title"]
        assert df.iloc[0]["attending"] == 300


# ═══════════════════════════════════════════════════════════════════════════
# _record_enrichment_health (lines 476-490)
# ═══════════════════════════════════════════════════════════════════════════


class TestRecordEnrichmentHealth:
    @patch("cuepoint.event_fetcher.store")
    def test_all_ok_status(self, mock_store):
        """All sources ok, zero failures -> status 'ok'."""
        stats = ScanStats(city="Berlin")
        stats.sc_ok = 10
        stats.sc_fail = 0
        stats.dc_ok = 8
        stats.dc_fail = 0
        stats.bc_ok = 5
        stats.bc_fail = 0
        stats.ra_events_fetched = 20

        _record_enrichment_health(stats)

        calls = mock_store.record_scraper_health.call_args_list
        # 4 calls: SC, DC, BC, RA
        assert len(calls) == 4
        for call in calls[:3]:  # SC, DC, BC
            assert call.kwargs["status"] == "ok" or call[1]["status"] == "ok"

    @patch("cuepoint.event_fetcher.store")
    def test_degraded_status(self, mock_store):
        """Some failures but some ok -> status 'degraded'."""
        stats = ScanStats(city="Berlin")
        stats.sc_ok = 5
        stats.sc_fail = 3
        stats.dc_ok = 0
        stats.dc_fail = 0
        stats.bc_ok = 0
        stats.bc_fail = 0
        stats.ra_events_fetched = 10

        _record_enrichment_health(stats)

        calls = mock_store.record_scraper_health.call_args_list
        # SC has ok>0 and fail>0 -> degraded
        sc_call = calls[0]
        assert sc_call.kwargs.get("status") == "degraded" or sc_call[1].get("status") == "degraded"

    @patch("cuepoint.event_fetcher.store")
    def test_error_status(self, mock_store):
        """All failures, zero ok -> status 'error'."""
        stats = ScanStats(city="Berlin")
        stats.sc_ok = 0
        stats.sc_fail = 10
        stats.dc_ok = 0
        stats.dc_fail = 5
        stats.bc_ok = 0
        stats.bc_fail = 3
        stats.ra_events_fetched = 10

        _record_enrichment_health(stats)

        calls = mock_store.record_scraper_health.call_args_list
        # SC, DC, BC all error
        for call in calls[:3]:
            kwargs = call.kwargs if call.kwargs else {}
            assert kwargs.get("status") == "error"

    @patch("cuepoint.event_fetcher.store")
    def test_skips_sources_with_zero_total(self, mock_store):
        """Sources with zero ok + zero fail are skipped entirely."""
        stats = ScanStats(city="Berlin")
        stats.sc_ok = 5
        stats.sc_fail = 0
        stats.dc_ok = 0
        stats.dc_fail = 0
        stats.bc_ok = 0
        stats.bc_fail = 0
        stats.ra_events_fetched = 10

        _record_enrichment_health(stats)

        calls = mock_store.record_scraper_health.call_args_list
        # Only SC and RA (DC and BC skipped because total is 0)
        assert len(calls) == 2
        sources = [c.args[0] if c.args else c.kwargs.get("source") for c in calls]
        assert "soundcloud" in sources
        assert "ra" in sources


# ═══════════════════════════════════════════════════════════════════════════
# run_for_city (lines 493-578)
# ═══════════════════════════════════════════════════════════════════════════


class TestRunForCity:
    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.create_html", return_value="<html></html>")
    @patch("cuepoint.event_fetcher._find_and_record")
    @patch("cuepoint.event_fetcher.sort_df")
    @patch("cuepoint.event_fetcher.filter_df")
    @patch("cuepoint.event_fetcher.get_data", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.CITIES", {"testcity": (99, "TestCity", "xx/testcity")})
    def test_successful_run_returns_result(
        self, mock_get_data, mock_filter, mock_sort, mock_find, mock_html, mock_store, tmp_path
    ):
        """Successful run returns city, events count, file_path."""
        rows = [
            {
                "listing_id": "lst-1",
                "listing_date": pd.Timestamp("2026-04-01"),
                "event_id": "evt-1",
                "event_date": pd.Timestamp("2026-04-01"),
                "start_time": pd.Timestamp("2026-04-01 23:00"),
                "end_time": pd.Timestamp("2026-04-02 08:00"),
                "title": "Test Event",
                "content_url": "/events/evt-1",
                "event_url": "https://ra.co/events/evt-1",
                "is_ticketed": True,
                "attending": 200,
                "venue_id": "v-1",
                "venue_name": "Club",
                "venue_url": "/club/club",
                "images": [],
                "artists": [{"id": "a-1", "name": "DJ"}],
                "promoters": [],
                "tickets": [],
                "genres": [{"name": "Techno"}],
                "artists_info": [_stub_artist("a-1", "DJ")],
                "artists_list_info_past": [],
                "flyer": None,
                "city_name": "TestCity",
            }
        ]
        df = pd.DataFrame(rows)
        mock_get_data.return_value = df
        mock_filter.return_value = df.copy()
        sorted_df = df.copy()
        sorted_df["_score"] = 100
        mock_sort.return_value = sorted_df

        with patch("cuepoint.event_fetcher.OUTPUT_PATH", str(tmp_path) + "/"), patch("builtins.open", MagicMock()):
            result = _run(run_for_city("testcity", datetime(2026, 4, 1), 7))

        assert result["city"] == "TestCity"
        assert result["events"] == 1
        assert result["file_path"] is not None

    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.create_html", return_value="<html></html>")
    @patch("cuepoint.event_fetcher._find_and_record")
    @patch("cuepoint.event_fetcher.sort_df")
    @patch("cuepoint.event_fetcher.filter_df")
    @patch("cuepoint.event_fetcher.get_data", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.CITIES", {"testcity": (99, "TestCity", "xx/testcity")})
    def test_full_clears_snapshot(
        self, mock_get_data, mock_filter, mock_sort, mock_find, mock_html, mock_store, tmp_path
    ):
        """full=True -> store.clear_scan_snapshot called."""
        df = pd.DataFrame(
            columns=[
                "listing_id",
                "listing_date",
                "event_id",
                "event_date",
                "start_time",
                "end_time",
                "title",
                "content_url",
                "event_url",
                "is_ticketed",
                "attending",
                "venue_id",
                "venue_name",
                "venue_url",
                "images",
                "artists",
                "promoters",
                "tickets",
                "genres",
                "artists_info",
                "artists_list_info_past",
                "flyer",
                "city_name",
            ]
        )
        mock_get_data.return_value = df
        mock_filter.return_value = df
        sorted_df = df.copy()
        sorted_df["_score"] = pd.Series(dtype=float)
        mock_sort.return_value = sorted_df

        with patch("cuepoint.event_fetcher.OUTPUT_PATH", str(tmp_path) + "/"), patch("builtins.open", MagicMock()):
            _run(run_for_city("testcity", datetime(2026, 4, 1), 7, full=True))

        mock_store.clear_scan_snapshot.assert_called_once_with("TestCity")

    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.get_data", new_callable=AsyncMock, side_effect=RuntimeError("boom"))
    @patch("cuepoint.event_fetcher.CITIES", {"testcity": (99, "TestCity", "xx/testcity")})
    def test_exception_returns_error_dict(self, mock_get_data, mock_store):
        """Exception in pipeline -> returns error dict."""
        result = _run(run_for_city("testcity", datetime(2026, 4, 1), 7))

        assert result["city"] == "TestCity"
        assert result["events"] == 0
        assert result["error"] == "boom"
        assert result["file_path"] is None

    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.create_html", return_value="<html></html>")
    @patch("cuepoint.event_fetcher._find_and_record")
    @patch("cuepoint.event_fetcher.sort_df")
    @patch("cuepoint.event_fetcher.filter_df")
    @patch("cuepoint.event_fetcher.get_data", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.CITIES", {"testcity": (99, "TestCity", "xx/testcity")})
    def test_on_sorted_df_callback_invoked(
        self, mock_get_data, mock_filter, mock_sort, mock_find, mock_html, mock_store, tmp_path
    ):
        """on_sorted_df callback is invoked with the sorted DataFrame."""
        rows = [
            {
                "listing_id": "lst-1",
                "listing_date": pd.Timestamp("2026-04-01"),
                "event_id": "evt-1",
                "event_date": pd.Timestamp("2026-04-01"),
                "start_time": pd.Timestamp("2026-04-01 23:00"),
                "end_time": pd.Timestamp("2026-04-02 08:00"),
                "title": "Test",
                "content_url": "/events/evt-1",
                "event_url": "https://ra.co/events/evt-1",
                "is_ticketed": True,
                "attending": 200,
                "venue_id": "v-1",
                "venue_name": "Club",
                "venue_url": "/club/club",
                "images": [],
                "artists": [{"id": "a-1", "name": "DJ"}],
                "promoters": [],
                "tickets": [],
                "genres": [{"name": "Techno"}],
                "artists_info": [_stub_artist("a-1", "DJ")],
                "artists_list_info_past": [],
                "flyer": None,
                "city_name": "TestCity",
            }
        ]
        df = pd.DataFrame(rows)
        mock_get_data.return_value = df
        mock_filter.return_value = df.copy()
        sorted_df = df.copy()
        sorted_df["_score"] = 100
        mock_sort.return_value = sorted_df

        callback = MagicMock()
        with patch("cuepoint.event_fetcher.OUTPUT_PATH", str(tmp_path) + "/"), patch("builtins.open", MagicMock()):
            _run(run_for_city("testcity", datetime(2026, 4, 1), 7, on_sorted_df=callback))

        callback.assert_called_once()
        passed_df = callback.call_args[0][0]
        assert isinstance(passed_df, pd.DataFrame)
        assert len(passed_df) == 1

    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.create_html", return_value="<html></html>")
    @patch("cuepoint.event_fetcher._find_and_record")
    @patch("cuepoint.event_fetcher.sort_df")
    @patch("cuepoint.event_fetcher.filter_df")
    @patch("cuepoint.event_fetcher.get_data", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher._record_enrichment_health")
    @patch("cuepoint.event_fetcher.CITIES", {"testcity": (99, "TestCity", "xx/testcity")})
    def test_record_enrichment_health_called(
        self, mock_rec, mock_get_data, mock_filter, mock_sort, mock_find, mock_html, mock_store, tmp_path
    ):
        """_record_enrichment_health is called with ScanStats."""
        df = pd.DataFrame(
            columns=[
                "listing_id",
                "listing_date",
                "event_id",
                "event_date",
                "start_time",
                "end_time",
                "title",
                "content_url",
                "event_url",
                "is_ticketed",
                "attending",
                "venue_id",
                "venue_name",
                "venue_url",
                "images",
                "artists",
                "promoters",
                "tickets",
                "genres",
                "artists_info",
                "artists_list_info_past",
                "flyer",
                "city_name",
            ]
        )
        mock_get_data.return_value = df
        mock_filter.return_value = df
        sorted_df = df.copy()
        sorted_df["_score"] = pd.Series(dtype=float)
        mock_sort.return_value = sorted_df

        with patch("cuepoint.event_fetcher.OUTPUT_PATH", str(tmp_path) + "/"), patch("builtins.open", MagicMock()):
            _run(run_for_city("testcity", datetime(2026, 4, 1), 7))

        mock_rec.assert_called_once()
        passed_stats = mock_rec.call_args[0][0]
        assert isinstance(passed_stats, ScanStats)
        assert passed_stats.city == "TestCity"

    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.create_html", return_value="<html></html>")
    @patch("cuepoint.event_fetcher._find_and_record")
    @patch("cuepoint.event_fetcher.sort_df")
    @patch("cuepoint.event_fetcher.filter_df")
    @patch("cuepoint.event_fetcher.get_data", new_callable=AsyncMock)
    @patch("cuepoint.event_fetcher.CITIES", {"testcity": (99, "TestCity", "xx/testcity")})
    def test_progress_callback_invoked(
        self, mock_get_data, mock_filter, mock_sort, mock_find, mock_html, mock_store, tmp_path
    ):
        """Progress callback receives phase messages."""
        df = pd.DataFrame(
            columns=[
                "listing_id",
                "listing_date",
                "event_id",
                "event_date",
                "start_time",
                "end_time",
                "title",
                "content_url",
                "event_url",
                "is_ticketed",
                "attending",
                "venue_id",
                "venue_name",
                "venue_url",
                "images",
                "artists",
                "promoters",
                "tickets",
                "genres",
                "artists_info",
                "artists_list_info_past",
                "flyer",
                "city_name",
            ]
        )
        mock_get_data.return_value = df
        mock_filter.return_value = df
        sorted_df = df.copy()
        sorted_df["_score"] = pd.Series(dtype=float)
        mock_sort.return_value = sorted_df

        cb = MagicMock()
        with patch("cuepoint.event_fetcher.OUTPUT_PATH", str(tmp_path) + "/"), patch("builtins.open", MagicMock()):
            _run(run_for_city("testcity", datetime(2026, 4, 1), 7, progress_cb=cb))

        assert cb.call_count >= 1
        phases = {call.args[0]["phase"] for call in cb.call_args_list}
        assert "fetch_ra" in phases


# ═══════════════════════════════════════════════════════════════════════════
# run_cities_parallel (lines 581-620)
# ═══════════════════════════════════════════════════════════════════════════


class TestRunCitiesParallel:
    @patch("cuepoint.event_fetcher.store")
    @patch(
        "cuepoint.event_fetcher.run_for_city",
        new_callable=AsyncMock,
        return_value={"city": "TestCity", "events": 5, "followed": 0, "file_path": "out.html"},
    )
    @patch(
        "cuepoint.event_fetcher.CITIES",
        {
            "city_a": (1, "CityA", "xx/citya"),
            "city_b": (2, "CityB", "xx/cityb"),
        },
    )
    def test_multiple_cities_run_concurrently(self, mock_run, mock_store):
        """Multiple cities run concurrently with semaphore."""
        results = _run(run_cities_parallel(["city_a", "city_b"], datetime(2026, 4, 1), 7, max_workers=2))
        assert len(results) == 2
        assert mock_run.call_count == 2

    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.run_for_city", new_callable=AsyncMock)
    @patch(
        "cuepoint.event_fetcher.CITIES",
        {
            "city_a": (1, "CityA", "xx/citya"),
            "city_b": (2, "CityB", "xx/cityb"),
        },
    )
    def test_progress_callback_includes_city_info(self, mock_run, mock_store):
        """Progress callback includes city name and index."""
        mock_run.return_value = {"city": "CityA", "events": 3, "followed": 0, "file_path": "x.html"}

        progress_messages: list[dict] = []

        def _capture(msg: dict) -> None:
            progress_messages.append(msg.copy())

        # Make run_for_city call its progress_cb
        async def _side_effect(key, start, days, progress_cb=None, **kwargs):
            if progress_cb:
                progress_cb({"phase": "test", "detail": "", "pct": 0.5})
            return {"city": key, "events": 3, "followed": 0, "file_path": "x.html"}

        mock_run.side_effect = _side_effect

        _run(
            run_cities_parallel(
                ["city_a", "city_b"],
                datetime(2026, 4, 1),
                7,
                max_workers=2,
                progress_cb=_capture,
            )
        )

        assert len(progress_messages) >= 2
        for msg in progress_messages:
            assert "city" in msg
            assert "city_idx" in msg
            assert "city_total" in msg
            assert msg["city_total"] == 2

    @patch("cuepoint.event_fetcher.store")
    @patch("cuepoint.event_fetcher.run_for_city", new_callable=AsyncMock)
    @patch(
        "cuepoint.event_fetcher.CITIES",
        {
            "city_a": (1, "CityA", "xx/citya"),
            "city_b": (2, "CityB", "xx/cityb"),
        },
    )
    def test_full_clears_snapshots_for_all_cities(self, mock_run, mock_store):
        """full=True clears scan snapshot for every city before running."""
        mock_run.return_value = {"city": "CityA", "events": 0, "followed": 0, "file_path": None}

        _run(
            run_cities_parallel(
                ["city_a", "city_b"],
                datetime(2026, 4, 1),
                7,
                max_workers=2,
                full=True,
            )
        )

        cleared = [c.args[0] for c in mock_store.clear_scan_snapshot.call_args_list]
        assert "CityA" in cleared
        assert "CityB" in cleared
