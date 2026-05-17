"""Tests for lazy initialization patterns: DB, following, config."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide a fresh temporary database for each test."""
    import cuepoint.db as db_mod

    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setattr(db_mod, "_db_initialized", False)

    if hasattr(db_mod._local, "conn"):
        db_mod._local.conn = None

    yield db_file

    db_mod.close_db()


class TestDBLazyInit:
    def test_first_access_initializes(self, tmp_db):
        import cuepoint.db as db_mod

        assert db_mod._db_initialized is False
        db_mod._get_conn()
        assert db_mod._db_initialized is True

    def test_tables_created(self, tmp_db):
        import cuepoint.db as db_mod

        conn = db_mod._get_conn()
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "artist_urls" in tables
        assert "artist_cache" in tables
        assert "found_events" in tables
        assert "scan_events" in tables

    def test_close_resets_initialized(self, tmp_db):
        import cuepoint.db as db_mod

        db_mod._get_conn()
        assert db_mod._db_initialized is True
        db_mod.close_db()
        assert db_mod._db_initialized is False

    def test_check_db_works(self, tmp_db):
        import cuepoint.db as db_mod

        assert db_mod.check_db() is True

    def test_check_db_fails_gracefully(self, tmp_db):
        import cuepoint.db as db_mod

        db_mod._get_conn()
        with patch.object(db_mod, "_get_conn", side_effect=sqlite3.OperationalError("disk I/O error")):
            assert db_mod.check_db() is False

    def test_concurrent_reads_after_init(self, tmp_db):
        import cuepoint.db as db_mod

        db_mod._get_conn()
        assert db_mod._db_initialized is True

        results = []

        def _read_thread():
            try:
                conn = db_mod._ensure_conn()
                conn.execute("SELECT 1").fetchone()
                results.append(True)
            except Exception:
                results.append(False)

        threads = [threading.Thread(target=_read_thread) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results)


class TestFollowingLazyLoad:
    def test_lazy_load_on_access(self, tmp_path, monkeypatch):
        import cuepoint.following as fol_mod

        following_file = tmp_path / "following.txt"
        following_file.write_text("/test-artist\n/another-artist\n")

        monkeypatch.setattr(fol_mod, "_FOLLOWING_FILE", following_file)
        monkeypatch.setattr(fol_mod, "_following_loaded", False)
        monkeypatch.setattr(fol_mod, "_FOLLOWING_EXPANDED", None)
        fol_mod.FOLLOWING.clear()

        assert fol_mod._following_loaded is False
        result = fol_mod.is_following("https://soundcloud.com/test-artist")
        assert fol_mod._following_loaded is True
        assert result is True

    def test_reload_following(self, tmp_path, monkeypatch):
        import cuepoint.following as fol_mod

        following_file = tmp_path / "following.txt"
        following_file.write_text("/artist-a\n")

        monkeypatch.setattr(fol_mod, "_FOLLOWING_FILE", following_file)
        monkeypatch.setattr(fol_mod, "_following_loaded", False)
        monkeypatch.setattr(fol_mod, "_FOLLOWING_EXPANDED", None)
        fol_mod.FOLLOWING.clear()

        assert fol_mod.is_following("https://soundcloud.com/artist-a") is True
        assert fol_mod.is_following("https://soundcloud.com/artist-b") is False

        following_file.write_text("/artist-b\n")
        fol_mod.reload_following()

        assert fol_mod.is_following("https://soundcloud.com/artist-a") is False
        assert fol_mod.is_following("https://soundcloud.com/artist-b") is True

    def test_missing_file(self, tmp_path, monkeypatch):
        import cuepoint.following as fol_mod

        monkeypatch.setattr(fol_mod, "_FOLLOWING_FILE", tmp_path / "nonexistent.txt")
        monkeypatch.setattr(fol_mod, "_following_loaded", False)
        monkeypatch.setattr(fol_mod, "_FOLLOWING_EXPANDED", None)
        fol_mod.FOLLOWING.clear()

        assert fol_mod.is_following("https://soundcloud.com/test") is False

    def test_comments_and_blanks_ignored(self, tmp_path, monkeypatch):
        import cuepoint.following as fol_mod

        following_file = tmp_path / "following.txt"
        following_file.write_text("# comment\n\n/real-artist\n  \n# another comment\n")

        monkeypatch.setattr(fol_mod, "_FOLLOWING_FILE", following_file)
        monkeypatch.setattr(fol_mod, "_following_loaded", False)
        monkeypatch.setattr(fol_mod, "_FOLLOWING_EXPANDED", None)
        fol_mod.FOLLOWING.clear()

        fol_mod._ensure_loaded()
        assert "/real-artist" in fol_mod.FOLLOWING
        assert len(fol_mod.FOLLOWING) == 1
