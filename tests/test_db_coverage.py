"""Extra coverage tests for db.py — targets uncovered lines."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import numpy as np
import pytest

from cuepoint import db as store

# ---- helpers ---------------------------------------------------------------


def _insert_cached_artist(db_path, artist_id, data, cached_at_iso, sc_url=None):
    """Insert a cached artist row directly with a specific cached_at timestamp."""
    if sc_url is not None:
        data = {**data, "soundcloud": sc_url}
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO artist_cache (artist_id, data, cached_at) VALUES (?, ?, ?)",
        (artist_id, json.dumps(data, ensure_ascii=False), cached_at_iso),
    )
    conn.commit()
    conn.close()


# ---- cleanup_cache: expire_ids path (lines 228-234) -----------------------


class TestCleanupCacheExpirePath:
    def test_non_followed_between_ttls_deleted(self, tmp_db):
        """Artists between short/long TTL that are NOT followed get deleted."""
        now = datetime.now()
        # 15 days old — between short (7) and long (30) TTL
        old_date = (now - timedelta(days=15)).isoformat()
        _insert_cached_artist(tmp_db, "artist-mid", {"name": "MidAge"}, old_date, sc_url="/not-followed")

        assert store.get_cached_artist("artist-mid") is not None

        store.cleanup_cache(
            ttl_days=7,
            ttl_following_days=30,
            is_following_fn=lambda url: False,
        )

        assert store.get_cached_artist("artist-mid") is None

    def test_followed_between_ttls_kept(self, tmp_db):
        """Artists between short/long TTL that ARE followed survive."""
        now = datetime.now()
        old_date = (now - timedelta(days=15)).isoformat()
        _insert_cached_artist(tmp_db, "artist-fol", {"name": "Followed"}, old_date, sc_url="/my-dj")

        store.cleanup_cache(
            ttl_days=7,
            ttl_following_days=30,
            is_following_fn=lambda url: url == "/my-dj",
        )

        assert store.get_cached_artist("artist-fol") is not None

    def test_older_than_long_ttl_deleted(self, tmp_db):
        """Artists older than long TTL are deleted even if followed."""
        now = datetime.now()
        very_old = (now - timedelta(days=60)).isoformat()
        _insert_cached_artist(tmp_db, "artist-old", {"name": "VeryOld"}, very_old, sc_url="/my-dj")

        store.cleanup_cache(
            ttl_days=7,
            ttl_following_days=30,
            is_following_fn=lambda url: True,
        )

        assert store.get_cached_artist("artist-old") is None

    def test_fresh_entries_untouched(self, tmp_db):
        """Entries newer than short TTL are never touched."""
        now = datetime.now()
        fresh = (now - timedelta(days=1)).isoformat()
        _insert_cached_artist(tmp_db, "artist-new", {"name": "Fresh"}, fresh)

        store.cleanup_cache(
            ttl_days=7,
            ttl_following_days=30,
            is_following_fn=lambda url: False,
        )

        assert store.get_cached_artist("artist-new") is not None

    def test_no_sc_url_between_ttls_deleted(self, tmp_db):
        """Artist with no soundcloud URL between TTLs is deleted (sc_url=None)."""
        now = datetime.now()
        mid = (now - timedelta(days=15)).isoformat()
        _insert_cached_artist(tmp_db, "no-sc", {"name": "NoSC"}, mid)

        store.cleanup_cache(
            ttl_days=7,
            ttl_following_days=30,
            is_following_fn=lambda url: False,
        )

        assert store.get_cached_artist("no-sc") is None


# ---- record_found: sqlite error handling (lines 322-324) ------------------


class TestRecordFoundErrorPath:
    def test_sqlite_error_reverts_cache(self, tmp_db):
        """When the DB write fails, the in-memory cache entry is discarded."""
        from unittest.mock import MagicMock

        store._found_cache = set()
        new_line = "Berlin,2026-02-02,evt2,Club2,Promo2,Artist2"

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("disk full")

        with patch.object(store, "_get_conn", return_value=mock_conn):
            store.record_found(new_line)
            assert new_line not in store._found_cache

        store._found_cache = None

    def test_duplicate_line_skipped(self, tmp_db):
        """record_found skips lines already in the in-memory cache."""
        store._found_cache = None
        line = "Berlin,2026-01-01,evt1,Club,Promo,Artist"
        store.record_found(line)
        # Calling again should be a no-op (early return)
        store.record_found(line)
        lines = store.get_all_found_lines()
        assert lines.count(line) == 1


# ---- save_scan_snapshot: empty current_ids path (line 378) -----------------


class TestScanSnapshotEmptyEvents:
    def test_empty_events_deletes_all(self, tmp_db):
        """Passing empty event list deletes all scan events for that city."""
        events = [
            {"event_id": "e1", "artist_ids": ["a1"], "lineup_hash": "h1"},
            {"event_id": "e2", "artist_ids": ["a2"], "lineup_hash": "h2"},
        ]
        store.save_scan_snapshot("berlin", events)
        snapshot = store.get_scan_snapshot("berlin")
        assert len(snapshot) == 2

        # Pass empty list → should delete all
        store.save_scan_snapshot("berlin", [])
        snapshot = store.get_scan_snapshot("berlin")
        assert len(snapshot) == 0


# ---- save_scan_snapshot: upsert + removal of stale events ------------------


class TestScanSnapshotUpsertAndStale:
    def test_upsert_updates_existing(self, tmp_db):
        events1 = [
            {"event_id": "e1", "artist_ids": ["a1"], "lineup_hash": "h1"},
        ]
        store.save_scan_snapshot("berlin", events1)
        assert store.get_scan_snapshot("berlin")["e1"] == "h1"

        events2 = [
            {"event_id": "e1", "artist_ids": ["a1", "a2"], "lineup_hash": "h1-updated"},
        ]
        store.save_scan_snapshot("berlin", events2)
        assert store.get_scan_snapshot("berlin")["e1"] == "h1-updated"

    def test_stale_events_removed(self, tmp_db):
        events1 = [
            {"event_id": "e1", "artist_ids": ["a1"], "lineup_hash": "h1"},
            {"event_id": "e2", "artist_ids": ["a2"], "lineup_hash": "h2"},
        ]
        store.save_scan_snapshot("berlin", events1)

        # Second scan only has e2 → e1 should be removed
        events2 = [
            {"event_id": "e2", "artist_ids": ["a2"], "lineup_hash": "h2"},
        ]
        store.save_scan_snapshot("berlin", events2)
        snapshot = store.get_scan_snapshot("berlin")
        assert "e1" not in snapshot
        assert "e2" in snapshot


# ---- get_scan_snapshot, get_scan_event_artist_ids (hit and miss) -----------


class TestScanSnapshotGetters:
    def test_get_scan_snapshot_empty(self, tmp_db):
        assert store.get_scan_snapshot("nowhere") == {}

    def test_get_scan_event_artist_ids_hit(self, tmp_db):
        events = [
            {"event_id": "e1", "artist_ids": ["a1", "a2"], "lineup_hash": "h1"},
        ]
        store.save_scan_snapshot("berlin", events)
        ids = store.get_scan_event_artist_ids("berlin", "e1")
        assert ids == ["a1", "a2"]

    def test_get_scan_event_artist_ids_miss(self, tmp_db):
        assert store.get_scan_event_artist_ids("berlin", "no-such-event") is None


# ---- clear_scan_snapshot ---------------------------------------------------


class TestClearScanSnapshot:
    def test_clear_removes_city_data(self, tmp_db):
        events = [
            {"event_id": "e1", "artist_ids": ["a1"], "lineup_hash": "h1"},
        ]
        store.save_scan_snapshot("berlin", events)
        store.save_scan_snapshot("amsterdam", events)

        store.clear_scan_snapshot("berlin")
        assert store.get_scan_snapshot("berlin") == {}
        # Amsterdam untouched
        assert len(store.get_scan_snapshot("amsterdam")) == 1


# ---- scraper_health CRUD ---------------------------------------------------


class TestScraperHealth:
    def test_record_and_get(self, tmp_db):
        store.record_scraper_health("ra", city="berlin", status="ok", events_found=5)
        store.record_scraper_health("tresor", city="berlin", status="error", error_msg="timeout")

        rows = store.get_all_scraper_health()
        assert len(rows) == 2
        sources = {r["source"] for r in rows}
        assert sources == {"ra", "tresor"}

    def test_upsert_same_source_city(self, tmp_db):
        store.record_scraper_health("ra", city="berlin", status="ok", events_found=5)
        store.record_scraper_health("ra", city="berlin", status="error", events_found=0, error_msg="503")

        rows = store.get_all_scraper_health()
        assert len(rows) == 1
        assert rows[0]["status"] == "error"

    def test_empty_health(self, tmp_db):
        assert store.get_all_scraper_health() == []


# ---- record_found / get_all_found_lines CRUD + dedup ----------------------


class TestFoundCRUD:
    def test_record_and_retrieve(self, tmp_db):
        store._found_cache = None
        store.record_found("Berlin,2026-01-01,e1,Berghain,Ostgut,DJ A")
        store.record_found("Amsterdam,2026-01-02,e2,Shelter,NPC,DJ B")
        lines = store.get_all_found_lines()
        assert len(lines) == 2

    def test_dedup(self, tmp_db):
        store._found_cache = None
        line = "Berlin,2026-01-01,e1,Club,Promo,DJ"
        store.record_found(line)
        store.record_found(line)
        store.record_found(line)
        assert store.get_all_found_lines().count(line) == 1

    def test_empty(self, tmp_db):
        assert store.get_all_found_lines() == []


# ---- check_db: success and failure paths -----------------------------------


class TestCheckDb:
    def test_success(self, tmp_db):
        assert store.check_db() is True

    def test_failure(self, tmp_db, monkeypatch):
        def _broken_conn():
            raise sqlite3.OperationalError("no such db")

        monkeypatch.setattr(store, "_get_conn", _broken_conn)
        assert store.check_db() is False


# ---- migrate_if_needed: migration from old files (lines 468-538) -----------


class TestMigrateIfNeeded:
    def test_skip_if_already_populated(self, tmp_db):
        """Migration is a no-op if artist_urls already has data."""
        store.save_artist_url("existing", {"name": "Present"})
        # Should return early, no error
        store.migrate_if_needed()
        assert store.get_artist_url("existing")["name"] == "Present"

    def test_migrate_url_cache(self, tmp_db, tmp_path, monkeypatch):
        """Migrate _artist_urls.json → artist_urls table."""
        url_file = tmp_path / "_artist_urls.json"
        url_data = {
            "id1": {"name": "Artist 1", "soundcloud": "/a1"},
            "id2": {"name": "Artist 2", "soundcloud": "/a2"},
        }
        url_file.write_text(json.dumps(url_data), encoding="utf-8")
        monkeypatch.setattr(store, "_OLD_URL_CACHE", url_file)
        monkeypatch.setattr(store, "_OLD_FOUND_DB", tmp_path / "nonexistent")
        monkeypatch.setattr(store, "_CACHE_DIR", tmp_path / "empty_cache_dir")

        store.migrate_if_needed()
        assert store.get_artist_url("id1")["name"] == "Artist 1"
        assert store.get_artist_url("id2")["name"] == "Artist 2"

    def test_migrate_per_artist_cache_files(self, tmp_db, tmp_path, monkeypatch):
        """Migrate per-artist JSON cache files → artist_cache table."""
        cache_dir = tmp_path / "cache_files"
        cache_dir.mkdir()

        entry1 = {"data": {"name": "Cached1", "sc_followers": 100}, "cached_at": "2025-01-01T00:00:00"}
        (cache_dir / "12345.json").write_text(json.dumps(entry1), encoding="utf-8")

        entry2 = {"data": {"name": "Cached2"}}  # missing cached_at → uses datetime.now()
        (cache_dir / "67890.json").write_text(json.dumps(entry2), encoding="utf-8")

        # A bad file that should be skipped
        (cache_dir / "99999.json").write_text("NOT VALID JSON", encoding="utf-8")

        monkeypatch.setattr(store, "_CACHE_DIR", cache_dir)
        monkeypatch.setattr(store, "_OLD_URL_CACHE", tmp_path / "nonexistent")
        monkeypatch.setattr(store, "_OLD_FOUND_DB", tmp_path / "nonexistent")

        store.migrate_if_needed()

        c1 = store.get_cached_artist("12345")
        assert c1 is not None
        assert c1[0]["name"] == "Cached1"

        c2 = store.get_cached_artist("67890")
        assert c2 is not None
        assert c2[0]["name"] == "Cached2"

        # Bad file should have been skipped — no entry for 99999
        assert store.get_cached_artist("99999") is None

    def test_migrate_found_db(self, tmp_db, tmp_path, monkeypatch):
        """Migrate db/Found CSV → found_events table."""
        found_file = tmp_path / "Found"
        content = (
            "City,Date,Event,Club,Promoter,Artist\n"
            "Berlin,2025-01-01,e1,Berghain,Ostgut,DJ A\n"
            "Amsterdam,2025-01-02,e2,Shelter,NPC,DJ B\n"
        )
        found_file.write_text(content, encoding="utf-8")

        monkeypatch.setattr(store, "_OLD_FOUND_DB", found_file)
        monkeypatch.setattr(store, "_OLD_URL_CACHE", tmp_path / "nonexistent")
        monkeypatch.setattr(store, "_CACHE_DIR", tmp_path / "empty_cache_dir")

        store.migrate_if_needed()
        lines = store.get_all_found_lines()
        assert len(lines) == 2
        assert "Berlin,2025-01-01,e1,Berghain,Ostgut,DJ A" in lines

    def test_migrate_found_db_no_header(self, tmp_db, tmp_path, monkeypatch):
        """Migrate db/Found CSV without header line."""
        found_file = tmp_path / "Found"
        content = "Berlin,2025-01-01,e1,Berghain,Ostgut,DJ A\n"
        found_file.write_text(content, encoding="utf-8")

        monkeypatch.setattr(store, "_OLD_FOUND_DB", found_file)
        monkeypatch.setattr(store, "_OLD_URL_CACHE", tmp_path / "nonexistent")
        monkeypatch.setattr(store, "_CACHE_DIR", tmp_path / "empty_cache_dir")

        store.migrate_if_needed()
        lines = store.get_all_found_lines()
        assert len(lines) == 1

    def test_migrate_corrupt_url_json(self, tmp_db, tmp_path, monkeypatch):
        """Gracefully handle corrupt _artist_urls.json."""
        url_file = tmp_path / "_artist_urls.json"
        url_file.write_text("NOT VALID JSON!!!", encoding="utf-8")

        monkeypatch.setattr(store, "_OLD_URL_CACHE", url_file)
        monkeypatch.setattr(store, "_OLD_FOUND_DB", tmp_path / "nonexistent")
        monkeypatch.setattr(store, "_CACHE_DIR", tmp_path / "empty_cache_dir")

        # Should not raise — failure is logged as warning
        store.migrate_if_needed()
        assert store.get_all_artist_urls() == {}


# ---- _json_default: numpy branches (lines 548-559) ------------------------


class TestJsonDefault:
    def test_numpy_integer(self):
        val = np.int64(42)
        result = store._json_default(val)
        assert result == 42
        assert isinstance(result, int)

    def test_numpy_floating(self):
        val = np.float64(3.14)
        result = store._json_default(val)
        assert result == pytest.approx(3.14)
        assert isinstance(result, float)

    def test_numpy_ndarray(self):
        val = np.array([1, 2, 3])
        result = store._json_default(val)
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_non_serializable_raises(self):
        with pytest.raises(TypeError, match="not JSON serializable"):
            store._json_default(object())

    def test_set_raises(self):
        with pytest.raises(TypeError, match="not JSON serializable"):
            store._json_default({1, 2, 3})


# ---- Numbered migration system -------


class TestMigrateMetricsSchema:
    def test_already_correct_schema_no_op(self, tmp_db):
        """When the schema already has compound PK, migration is a no-op."""
        # init_db already creates the correct schema
        conn = store._ensure_conn()
        store._migrate_001(conn)
        # Save and retrieve metrics to verify table is functional
        store.save_artist_metrics("a1", 500, 30)
        m = store.get_artist_metrics("a1")
        assert m is not None
        assert m[0] == 500

    def test_old_schema_gets_migrated(self, tmp_db):
        """Table with single-column PK gets migrated to compound PK."""
        conn = sqlite3.connect(str(tmp_db))
        # Drop current table and recreate with old schema (single PK on artist_id only)
        conn.executescript("""
            DROP TABLE IF EXISTS artist_metrics_history;
            CREATE TABLE artist_metrics_history (
                artist_id    TEXT PRIMARY KEY,
                sc_followers INTEGER,
                dc_want      INTEGER,
                recorded_at  TEXT NOT NULL
            );
            INSERT INTO artist_metrics_history VALUES ('a1', 100, 10, '2025-01-01T00:00:00');
            INSERT INTO artist_metrics_history VALUES ('a2', 200, 20, '2025-01-02T00:00:00');
        """)
        conn.commit()
        conn.close()

        conn = store._ensure_conn()
        store._migrate_001(conn)

        # Verify data survived migration
        m = store.get_artist_metrics("a1")
        assert m is not None
        assert m[0] == 100

        m2 = store.get_artist_metrics("a2")
        assert m2 is not None
        assert m2[0] == 200

    def test_schema_without_artist_id_returns_early(self, tmp_db):
        """If 'artist_id' column is missing entirely, migration returns early."""
        conn = sqlite3.connect(str(tmp_db))
        conn.executescript("""
            DROP TABLE IF EXISTS artist_metrics_history;
            CREATE TABLE artist_metrics_history (
                some_id      TEXT PRIMARY KEY,
                sc_followers INTEGER,
                dc_want      INTEGER,
                recorded_at  TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()

        # Should return early without error
        conn = store._ensure_conn()
        store._migrate_001(conn)

    def test_run_migrations_tracks_versions(self, tmp_db):
        """_run_migrations records applied versions in schema_version table."""
        conn = store._ensure_conn()
        store._run_migrations(conn)
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert 1 in applied
        assert 2 in applied

    def test_run_migrations_skips_already_applied(self, tmp_db):
        """_run_migrations skips migrations already in schema_version."""
        conn = store._ensure_conn()
        # Pre-mark migration 1 as applied
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (1,))
        conn.commit()
        store._run_migrations(conn)
        # Both should be recorded now
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_version").fetchall()}
        assert 1 in applied
        assert 2 in applied
