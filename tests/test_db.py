"""Tests for db.py — SQLite storage layer."""

import json
from datetime import datetime, timedelta

from techno_scan import db as store


def test_save_and_get_url(tmp_db):
    """Saved URL should be retrievable."""
    entry = {"soundcloud": "/sc-url", "discogs": "/dc-url", "bandcamp": "https://bc.bandcamp.com"}
    store.save_artist_url("artist-1", entry)
    result = store.get_artist_url("artist-1")
    assert result is not None
    assert result["soundcloud"] == "/sc-url"
    assert result["discogs"] == "/dc-url"
    assert result["bandcamp"] == "https://bc.bandcamp.com"


def test_save_and_get_cache(tmp_db):
    """Cached artist info should be retrievable."""
    info = {"name": "Test", "sc_followers": 5000, "sc_tags": json.dumps(["Techno"])}
    store.save_cached_artist("artist-1", info)
    result = store.get_cached_artist("artist-1")
    assert result is not None
    data, _cached_at = result
    assert data["sc_followers"] == 5000


def test_missing_returns_none(tmp_db):
    """Non-existent artist should return None."""
    assert store.get_artist_url("nonexistent") is None
    assert store.get_cached_artist("nonexistent") is None


def test_stale_cache_cleanup(tmp_db):
    """Cache entries older than TTL should be removed by cleanup_cache."""
    info = {"name": "Old", "sc_followers": 100}
    store.save_cached_artist("artist-old", info)

    # Backdate to 31 days ago
    conn = store._get_conn()
    old_date = (datetime.now() - timedelta(days=31)).isoformat()
    conn.execute(
        "UPDATE artist_cache SET cached_at = ? WHERE artist_id = ?",
        (old_date, "artist-old"),
    )
    conn.commit()

    store.cleanup_cache(ttl_days=30, ttl_following_days=7, is_following_fn=lambda x: False)
    assert store.get_cached_artist("artist-old") is None


def test_cleanup_keeps_fresh(tmp_db):
    """Fresh cache entries should survive cleanup."""
    info = {"name": "Fresh", "sc_followers": 200}
    store.save_cached_artist("artist-fresh", info)
    store.cleanup_cache(ttl_days=30, ttl_following_days=7, is_following_fn=lambda x: False)
    assert store.get_cached_artist("artist-fresh") is not None


def test_followed_ttl(tmp_db):
    """Followed artists have shorter TTL."""
    info = {"name": "Followed DJ", "sc_followers": 300, "soundcloud": "/followed-dj"}
    store.save_cached_artist("artist-followed", info)

    # Backdate to 8 days ago
    conn = store._get_conn()
    old_date = (datetime.now() - timedelta(days=8)).isoformat()
    conn.execute(
        "UPDATE artist_cache SET cached_at = ? WHERE artist_id = ?",
        (old_date, "artist-followed"),
    )
    conn.commit()

    # 7-day TTL for followed -> should be removed
    store.cleanup_cache(ttl_days=30, ttl_following_days=7, is_following_fn=lambda x: True)
    assert store.get_cached_artist("artist-followed") is None


def test_record_found_dedup(tmp_db):
    """record_found should deduplicate identical lines."""
    # Reset _found_cache so it loads fresh from DB
    store._found_cache = None
    line = "Berlin,2026-03-29,Test Event,Berghain,Followed DJ"
    store.record_found(line)
    store.record_found(line)  # duplicate

    found = store.get_all_found_lines()
    matching = [f for f in found if "Followed DJ" in f]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# Scan snapshot (incremental scans)
# ---------------------------------------------------------------------------


def test_scan_snapshot_save_and_get(tmp_db):
    """Saved scan snapshot should be retrievable."""
    events = [
        {"event_id": "evt-1", "artist_ids": ["a1", "a2"], "lineup_hash": "hash1"},
        {"event_id": "evt-2", "artist_ids": ["a3"], "lineup_hash": "hash2"},
    ]
    store.save_scan_snapshot("Berlin", events)
    snapshot = store.get_scan_snapshot("Berlin")
    assert snapshot == {"evt-1": "hash1", "evt-2": "hash2"}


def test_scan_snapshot_upsert(tmp_db):
    """Re-saving updates existing rows and removes stale ones."""
    store.save_scan_snapshot(
        "Berlin",
        [
            {"event_id": "evt-1", "artist_ids": ["a1"], "lineup_hash": "old"},
            {"event_id": "evt-2", "artist_ids": ["a2"], "lineup_hash": "keep"},
        ],
    )
    # evt-1 lineup changed, evt-2 gone, evt-3 new
    store.save_scan_snapshot(
        "Berlin",
        [
            {"event_id": "evt-1", "artist_ids": ["a1", "a9"], "lineup_hash": "new"},
            {"event_id": "evt-3", "artist_ids": ["a5"], "lineup_hash": "fresh"},
        ],
    )
    snapshot = store.get_scan_snapshot("Berlin")
    assert snapshot == {"evt-1": "new", "evt-3": "fresh"}
    assert "evt-2" not in snapshot


def test_scan_event_artist_ids(tmp_db):
    """get_scan_event_artist_ids returns stored artist list."""
    store.save_scan_snapshot(
        "Berlin",
        [
            {"event_id": "evt-1", "artist_ids": ["a1", "a2"], "lineup_hash": "h"},
        ],
    )
    ids = store.get_scan_event_artist_ids("Berlin", "evt-1")
    assert ids == ["a1", "a2"]
    assert store.get_scan_event_artist_ids("Berlin", "evt-99") is None


def test_clear_scan_snapshot(tmp_db):
    """clear_scan_snapshot removes all data for a city."""
    store.save_scan_snapshot(
        "Berlin",
        [
            {"event_id": "evt-1", "artist_ids": ["a1"], "lineup_hash": "h"},
        ],
    )
    store.clear_scan_snapshot("Berlin")
    assert store.get_scan_snapshot("Berlin") == {}


def test_scan_snapshot_city_isolation(tmp_db):
    """Snapshots for different cities are independent."""
    store.save_scan_snapshot(
        "Berlin",
        [
            {"event_id": "evt-1", "artist_ids": ["a1"], "lineup_hash": "h1"},
        ],
    )
    store.save_scan_snapshot(
        "London",
        [
            {"event_id": "evt-2", "artist_ids": ["a2"], "lineup_hash": "h2"},
        ],
    )
    assert "evt-1" in store.get_scan_snapshot("Berlin")
    assert "evt-1" not in store.get_scan_snapshot("London")
    assert "evt-2" in store.get_scan_snapshot("London")
