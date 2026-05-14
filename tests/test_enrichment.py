"""Tests for enrichment.py — enrichment pipeline (mocked sources)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx

from cuepoint.enrichment import (
    _run_enrichment_phases,
    enrich_batch_phased,
    enrich_club_batch_phased,
    get_artist_info_by_ra_id,
    get_cached_artist,
    is_cache_stale,
)

_run = asyncio.run

# ---------------------------------------------------------------------------
# Cache helpers (sync — no change needed)
# ---------------------------------------------------------------------------


class TestGetCachedArtist:
    @patch("cuepoint.enrichment.store")
    def test_returns_none_when_not_cached(self, mock_store):
        mock_store.get_cached_artist.return_value = None
        assert get_cached_artist("123") is None

    @patch("cuepoint.enrichment.is_following", return_value=False)
    @patch("cuepoint.enrichment.store")
    def test_returns_data_when_fresh(self, mock_store, _):
        data = {"name": "Test", "soundcloud": "/test", "sc_followers": 100}
        mock_store.get_cached_artist.return_value = (data, datetime.now().isoformat())
        result = get_cached_artist("123")
        assert result == data

    @patch("cuepoint.enrichment.is_following", return_value=False)
    @patch("cuepoint.enrichment.store")
    def test_sc_incomplete_fresh_returns_data(self, mock_store, _):
        data = {"name": "Test", "soundcloud": "/test", "sc_followers": None}
        mock_store.get_cached_artist.return_value = (data, datetime.now().isoformat())
        assert get_cached_artist("123") == data

    @patch("cuepoint.enrichment.is_following", return_value=False)
    @patch("cuepoint.enrichment.store")
    def test_sc_incomplete_stale_treated_as_miss(self, mock_store, _):
        data = {"name": "Test", "soundcloud": "/test", "sc_followers": None}
        old_date = (datetime.now() - timedelta(days=2)).isoformat()
        mock_store.get_cached_artist.return_value = (data, old_date)
        assert get_cached_artist("123") is None

    @patch("cuepoint.enrichment.CACHE_TTL_DAYS", 30)
    @patch("cuepoint.enrichment.is_following", return_value=False)
    @patch("cuepoint.enrichment.store")
    def test_returns_none_when_expired(self, mock_store, _):
        data = {"name": "Test", "soundcloud": "/test"}
        old_date = (datetime.now() - timedelta(days=31)).isoformat()
        mock_store.get_cached_artist.return_value = (data, old_date)
        assert get_cached_artist("123") is None

    @patch("cuepoint.enrichment.CACHE_TTL_FOLLOWING_DAYS", 7)
    @patch("cuepoint.enrichment.is_following", return_value=True)
    @patch("cuepoint.enrichment.store")
    def test_followed_artist_uses_shorter_ttl(self, mock_store, _):
        data = {"name": "Test", "soundcloud": "/followed-artist"}
        old_date = (datetime.now() - timedelta(days=8)).isoformat()
        mock_store.get_cached_artist.return_value = (data, old_date)
        assert get_cached_artist("123") is None


class TestIsCacheStale:
    @patch("cuepoint.enrichment.store")
    def test_not_cached_returns_false(self, mock_store):
        mock_store.get_cached_artist.return_value = None
        assert is_cache_stale("123") is False

    @patch("cuepoint.enrichment.CACHE_STALE_DAYS", 14)
    @patch("cuepoint.enrichment.store")
    def test_stale_returns_true(self, mock_store):
        old_date = (datetime.now() - timedelta(days=15)).isoformat()
        mock_store.get_cached_artist.return_value = ({}, old_date)
        assert is_cache_stale("123") is True

    @patch("cuepoint.enrichment.CACHE_STALE_DAYS", 14)
    @patch("cuepoint.enrichment.store")
    def test_fresh_returns_false(self, mock_store):
        fresh_date = datetime.now().isoformat()
        mock_store.get_cached_artist.return_value = ({}, fresh_date)
        assert is_cache_stale("123") is False


# ---------------------------------------------------------------------------
# Single-artist enrichment (async)
# ---------------------------------------------------------------------------


class TestGetArtistInfoByRaId:
    @patch("cuepoint.enrichment.save_cached_artist")
    @patch("cuepoint.enrichment.check_rising")
    @patch("cuepoint.enrichment.populate_bandcamp_info", side_effect=lambda x: x)
    @patch("cuepoint.enrichment.populate_discogs_info", side_effect=lambda x: x)
    @patch("cuepoint.enrichment.populate_sc_info", side_effect=lambda x: {**x, "sc_followers": 100})
    @patch("cuepoint.enrichment.get_cached_artist", return_value=None)
    def test_full_pipeline(self, mock_cache, mock_sc, mock_dc, mock_bc, mock_rising, mock_save):
        async def _get_urls(aid):
            return {"id": "1", "name": "DJ Test"}

        result = _run(get_artist_info_by_ra_id("1", _get_urls))
        assert result is not None
        assert result["sc_followers"] == 100
        mock_save.assert_called_once()
        mock_rising.assert_called_once()

    @patch("cuepoint.enrichment.get_cached_artist")
    def test_cache_hit_skips_enrichment(self, mock_cache):
        cached = {"id": "1", "name": "Cached", "sc_followers": 999}
        mock_cache.return_value = cached

        async def _get_urls(aid):
            raise AssertionError("should not be called")

        result = _run(get_artist_info_by_ra_id("1", _get_urls))
        assert result == cached

    @patch("cuepoint.enrichment.get_cached_artist", return_value=None)
    def test_ra_returns_none(self, mock_cache):
        async def _get_urls(aid):
            return None

        result = _run(get_artist_info_by_ra_id("1", _get_urls))
        assert result is None

    @patch("cuepoint.enrichment.save_cached_artist")
    @patch("cuepoint.enrichment.check_rising")
    @patch("cuepoint.enrichment.populate_bandcamp_info", side_effect=lambda x: x)
    @patch("cuepoint.enrichment.populate_discogs_info", side_effect=lambda x: x)
    @patch("cuepoint.enrichment.populate_sc_info", side_effect=httpx.ConnectError("SC down"))
    @patch("cuepoint.enrichment.get_cached_artist", return_value=None)
    def test_sc_failure_continues(self, mock_cache, mock_sc, mock_dc, mock_bc, mock_rising, mock_save):
        async def _get_urls(aid):
            return {"id": "1", "name": "Test"}

        result = _run(get_artist_info_by_ra_id("1", _get_urls))
        assert result is not None
        mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# _run_enrichment_phases (shared pipeline)
# ---------------------------------------------------------------------------


class TestRunEnrichmentPhases:
    @patch("cuepoint.enrichment.store.batch_save_enriched")
    @patch("cuepoint.enrichment.check_rising")
    @patch("cuepoint.enrichment.populate_bandcamp_info", side_effect=lambda x: {**x, "bc_tags": "[]"})
    @patch("cuepoint.enrichment.populate_discogs_info", side_effect=lambda x: {**x, "dc_have": 10})
    @patch("cuepoint.enrichment.populate_sc_info", side_effect=lambda x: {**x, "sc_followers": 500})
    def test_runs_all_phases(self, mock_sc, mock_dc, mock_bc, mock_rising, mock_batch):
        to_enrich = [("a1", {"name": "DJ1"}), ("a2", {"name": "DJ2"})]
        results = _run(_run_enrichment_phases(to_enrich))
        assert len(results) == 2
        assert results["a1"]["sc_followers"] == 500
        assert results["a1"]["dc_have"] == 10
        mock_batch.assert_called_once()
        assert len(mock_batch.call_args[0][0]) == 2
        assert mock_rising.call_count == 2

    @patch("cuepoint.enrichment.store.batch_save_enriched")
    @patch("cuepoint.enrichment.check_rising")
    @patch("cuepoint.enrichment.populate_bandcamp_info", side_effect=lambda x: x)
    @patch("cuepoint.enrichment.populate_discogs_info", side_effect=lambda x: x)
    @patch("cuepoint.enrichment.populate_sc_info", side_effect=lambda x: x)
    def test_progress_callback(self, mock_sc, mock_dc, mock_bc, mock_rising, mock_batch):
        cb_calls = []

        def cb(phase, detail, frac):
            cb_calls.append((phase, frac))

        _run(_run_enrichment_phases([("a1", {"name": "DJ1"})], progress_cb=cb))
        phases = [c[0] for c in cb_calls]
        assert "enrich_sc" in phases
        assert "enrich_discogs" in phases
        assert "enrich_bandcamp" in phases
        assert "saving" in phases


# ---------------------------------------------------------------------------
# Batch enrichment
# ---------------------------------------------------------------------------


class TestEnrichBatchPhased:
    @patch("cuepoint.enrichment._run_enrichment_phases")
    @patch("cuepoint.enrichment.get_cached_artist")
    def test_cache_hits_skip_pipeline(self, mock_cache, mock_pipeline):
        mock_cache.return_value = {"name": "Cached", "sc_followers": 100}
        mock_pipeline.return_value = {}

        async def _get_urls(aid):
            raise AssertionError("should not be called")

        results = _run(enrich_batch_phased(["a1", "a2"], _get_urls))
        assert len(results) == 2
        mock_pipeline.assert_not_called()

    @patch("cuepoint.enrichment._run_enrichment_phases", return_value={"a1": {"name": "Enriched"}})
    @patch("cuepoint.enrichment.get_cached_artist", return_value=None)
    def test_cache_miss_goes_through_pipeline(self, mock_cache, mock_pipeline):
        async def _get_urls(aid):
            return {"id": "a1", "name": "New"}

        results = _run(enrich_batch_phased(["a1"], _get_urls))
        assert "a1" in results
        mock_pipeline.assert_called_once()


# ---------------------------------------------------------------------------
# Club batch enrichment
# ---------------------------------------------------------------------------


class TestEnrichClubBatchPhased:
    @patch("cuepoint.enrichment._run_enrichment_phases", return_value={"club_1": {"name": "Club Artist"}})
    @patch("cuepoint.enrichment.search_sc_by_name", return_value="https://soundcloud.com/club-artist")
    @patch("cuepoint.enrichment.get_cached_artist", return_value=None)
    def test_sc_name_search_then_pipeline(self, mock_cache, mock_search, mock_pipeline):
        stubs = [{"id": "club_1", "name": "Club Artist", "soundcloud": None}]
        results = _run(enrich_club_batch_phased(stubs))
        assert "club_1" in results
        mock_search.assert_called_once()
        mock_pipeline.assert_called_once()

    @patch("cuepoint.enrichment.get_cached_artist")
    def test_all_cached(self, mock_cache):
        mock_cache.return_value = {"name": "Already Cached"}
        stubs = [{"id": "c1"}, {"id": "c2"}]
        results = _run(enrich_club_batch_phased(stubs))
        assert len(results) == 2
