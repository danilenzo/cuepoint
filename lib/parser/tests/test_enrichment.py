"""Tests for enrichment.py — enrichment pipeline (mocked sources)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from enrichment import (
    _run_enrichment_phases,
    enrich_batch_phased,
    enrich_club_batch_phased,
    get_artist_info_by_ra_id,
    get_cached_artist,
    is_cache_stale,
)

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


class TestGetCachedArtist:
    @patch("enrichment.store")
    def test_returns_none_when_not_cached(self, mock_store):
        mock_store.get_cached_artist.return_value = None
        assert get_cached_artist("123") is None

    @patch("enrichment.is_following", return_value=False)
    @patch("enrichment.store")
    def test_returns_data_when_fresh(self, mock_store, _):
        data = {"name": "Test", "soundcloud": "/test"}
        mock_store.get_cached_artist.return_value = (data, datetime.now().isoformat())
        result = get_cached_artist("123")
        assert result == data

    @patch("enrichment.CACHE_TTL_DAYS", 30)
    @patch("enrichment.is_following", return_value=False)
    @patch("enrichment.store")
    def test_returns_none_when_expired(self, mock_store, _):
        data = {"name": "Test", "soundcloud": "/test"}
        old_date = (datetime.now() - timedelta(days=31)).isoformat()
        mock_store.get_cached_artist.return_value = (data, old_date)
        assert get_cached_artist("123") is None

    @patch("enrichment.CACHE_TTL_FOLLOWING_DAYS", 7)
    @patch("enrichment.is_following", return_value=True)
    @patch("enrichment.store")
    def test_followed_artist_uses_shorter_ttl(self, mock_store, _):
        data = {"name": "Test", "soundcloud": "/followed-artist"}
        old_date = (datetime.now() - timedelta(days=8)).isoformat()
        mock_store.get_cached_artist.return_value = (data, old_date)
        assert get_cached_artist("123") is None


class TestIsCacheStale:
    @patch("enrichment.store")
    def test_not_cached_returns_false(self, mock_store):
        mock_store.get_cached_artist.return_value = None
        assert is_cache_stale("123") is False

    @patch("enrichment.CACHE_STALE_DAYS", 14)
    @patch("enrichment.store")
    def test_stale_returns_true(self, mock_store):
        old_date = (datetime.now() - timedelta(days=15)).isoformat()
        mock_store.get_cached_artist.return_value = ({}, old_date)
        assert is_cache_stale("123") is True

    @patch("enrichment.CACHE_STALE_DAYS", 14)
    @patch("enrichment.store")
    def test_fresh_returns_false(self, mock_store):
        fresh_date = datetime.now().isoformat()
        mock_store.get_cached_artist.return_value = ({}, fresh_date)
        assert is_cache_stale("123") is False


# ---------------------------------------------------------------------------
# Single-artist enrichment
# ---------------------------------------------------------------------------


class TestGetArtistInfoByRaId:
    @patch("enrichment.save_cached_artist")
    @patch("enrichment.check_rising")
    @patch("enrichment.populate_bandcamp_info", side_effect=lambda x: x)
    @patch("enrichment.populate_discogs_info", side_effect=lambda x: x)
    @patch("enrichment.populate_sc_info", side_effect=lambda x: {**x, "sc_followers": 100})
    @patch("enrichment.get_cached_artist", return_value=None)
    def test_full_pipeline(self, mock_cache, mock_sc, mock_dc, mock_bc, mock_rising, mock_save):
        get_urls = MagicMock(return_value={"id": "1", "name": "DJ Test"})
        result = get_artist_info_by_ra_id("1", get_urls)
        assert result is not None
        assert result["sc_followers"] == 100
        mock_save.assert_called_once()
        mock_rising.assert_called_once()

    @patch("enrichment.get_cached_artist")
    def test_cache_hit_skips_enrichment(self, mock_cache):
        cached = {"id": "1", "name": "Cached", "sc_followers": 999}
        mock_cache.return_value = cached
        get_urls = MagicMock()
        result = get_artist_info_by_ra_id("1", get_urls)
        assert result == cached
        get_urls.assert_not_called()

    @patch("enrichment.get_cached_artist", return_value=None)
    def test_ra_returns_none(self, mock_cache):
        get_urls = MagicMock(return_value=None)
        result = get_artist_info_by_ra_id("1", get_urls)
        assert result is None

    @patch("enrichment.save_cached_artist")
    @patch("enrichment.check_rising")
    @patch("enrichment.populate_bandcamp_info", side_effect=lambda x: x)
    @patch("enrichment.populate_discogs_info", side_effect=lambda x: x)
    @patch("enrichment.populate_sc_info", side_effect=Exception("SC down"))
    @patch("enrichment.get_cached_artist", return_value=None)
    def test_sc_failure_continues(self, mock_cache, mock_sc, mock_dc, mock_bc, mock_rising, mock_save):
        get_urls = MagicMock(return_value={"id": "1", "name": "Test"})
        result = get_artist_info_by_ra_id("1", get_urls)
        assert result is not None
        mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# _run_enrichment_phases (shared pipeline)
# ---------------------------------------------------------------------------


class TestRunEnrichmentPhases:
    @patch("enrichment.store.batch_save_enriched")
    @patch("enrichment.check_rising")
    @patch("enrichment.populate_bandcamp_info", side_effect=lambda x: {**x, "bc_tags": "[]"})
    @patch("enrichment.populate_discogs_info", side_effect=lambda x: {**x, "dc_have": 10})
    @patch("enrichment.populate_sc_info", side_effect=lambda x: {**x, "sc_followers": 500})
    def test_runs_all_phases(self, mock_sc, mock_dc, mock_bc, mock_rising, mock_batch):
        to_enrich = [("a1", {"name": "DJ1"}), ("a2", {"name": "DJ2"})]
        results = _run_enrichment_phases(to_enrich)
        assert len(results) == 2
        assert results["a1"]["sc_followers"] == 500
        assert results["a1"]["dc_have"] == 10
        mock_batch.assert_called_once()
        assert len(mock_batch.call_args[0][0]) == 2  # batch of 2 items
        assert mock_rising.call_count == 2

    @patch("enrichment.store.batch_save_enriched")
    @patch("enrichment.check_rising")
    @patch("enrichment.populate_bandcamp_info", side_effect=lambda x: x)
    @patch("enrichment.populate_discogs_info", side_effect=lambda x: x)
    @patch("enrichment.populate_sc_info", side_effect=lambda x: x)
    def test_progress_callback(self, mock_sc, mock_dc, mock_bc, mock_rising, mock_batch):
        cb_calls = []

        def cb(phase, detail, frac):
            cb_calls.append((phase, frac))

        _run_enrichment_phases([("a1", {"name": "DJ1"})], progress_cb=cb)
        phases = [c[0] for c in cb_calls]
        assert "enrich_sc" in phases
        assert "enrich_discogs" in phases
        assert "enrich_bandcamp" in phases
        assert "saving" in phases


# ---------------------------------------------------------------------------
# Batch enrichment
# ---------------------------------------------------------------------------


class TestEnrichBatchPhased:
    @patch("enrichment._run_enrichment_phases")
    @patch("enrichment.get_cached_artist")
    def test_cache_hits_skip_pipeline(self, mock_cache, mock_pipeline):
        mock_cache.return_value = {"name": "Cached", "sc_followers": 100}
        mock_pipeline.return_value = {}

        get_urls = MagicMock()
        results = enrich_batch_phased(["a1", "a2"], get_urls)
        assert len(results) == 2
        get_urls.assert_not_called()
        mock_pipeline.assert_not_called()  # all cache hits, no pipeline needed

    @patch("enrichment._run_enrichment_phases", return_value={"a1": {"name": "Enriched"}})
    @patch("enrichment.get_cached_artist", return_value=None)
    def test_cache_miss_goes_through_pipeline(self, mock_cache, mock_pipeline):
        get_urls = MagicMock(return_value={"id": "a1", "name": "New"})
        results = enrich_batch_phased(["a1"], get_urls)
        assert "a1" in results
        mock_pipeline.assert_called_once()


# ---------------------------------------------------------------------------
# Club batch enrichment
# ---------------------------------------------------------------------------


class TestEnrichClubBatchPhased:
    @patch("enrichment._run_enrichment_phases", return_value={"club_1": {"name": "Club Artist"}})
    @patch("enrichment.search_sc_by_name", return_value="https://soundcloud.com/club-artist")
    @patch("enrichment.get_cached_artist", return_value=None)
    def test_sc_name_search_then_pipeline(self, mock_cache, mock_search, mock_pipeline):
        stubs = [{"id": "club_1", "name": "Club Artist", "soundcloud": None}]
        results = enrich_club_batch_phased(stubs)
        assert "club_1" in results
        mock_search.assert_called_once()
        mock_pipeline.assert_called_once()

    @patch("enrichment.get_cached_artist")
    def test_all_cached(self, mock_cache):
        mock_cache.return_value = {"name": "Already Cached"}
        stubs = [{"id": "c1"}, {"id": "c2"}]
        results = enrich_club_batch_phased(stubs)
        assert len(results) == 2
