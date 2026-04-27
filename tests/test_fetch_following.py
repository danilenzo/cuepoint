"""Tests for fetch_following.py — profile lock and slug helpers."""

from __future__ import annotations

import pytest

from cuepoint.fetch_following import _check_profile_lock, show_following, update_following


class TestCheckProfileLock:
    def test_first_run_writes_profile(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        _check_profile_lock("https://soundcloud.com/testuser", force=False)
        assert profile_file.read_text(encoding="utf-8") == "https://soundcloud.com/testuser"

    def test_same_profile_ok(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        profile_file.write_text("https://soundcloud.com/testuser", encoding="utf-8")
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        _check_profile_lock("https://soundcloud.com/testuser", force=False)

    def test_different_profile_exits(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        profile_file.write_text("https://soundcloud.com/user1", encoding="utf-8")
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        with pytest.raises(SystemExit):
            _check_profile_lock("https://soundcloud.com/user2", force=False)

    def test_different_profile_force_ok(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        profile_file.write_text("https://soundcloud.com/user1", encoding="utf-8")
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        _check_profile_lock("https://soundcloud.com/user2", force=True)
        assert "user2" in profile_file.read_text(encoding="utf-8")

    def test_normalizes_trailing_slash(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        _check_profile_lock("https://soundcloud.com/TestUser/", force=False)
        assert profile_file.read_text(encoding="utf-8") == "https://soundcloud.com/testuser"


class TestUpdateFollowing:
    def test_writes_file_and_reloads(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod
        import cuepoint.following as following_mod

        following_file = tmp_path / "following.txt"
        monkeypatch.setattr(mod, "_FOLLOWING_FILE", following_file)
        monkeypatch.setattr(following_mod, "_FOLLOWING_FILE", following_file)

        update_following(["/artist-a", "/artist-b"])

        content = following_file.read_text(encoding="utf-8")
        assert "/artist-a" in content
        assert "/artist-b" in content


class TestShowFollowing:
    def test_empty_following(self, monkeypatch, capsys):
        import cuepoint.following as following_mod

        monkeypatch.setattr(following_mod, "FOLLOWING", set())
        show_following()
        out = capsys.readouterr().out
        assert "empty" in out.lower()

    def test_with_following(self, monkeypatch, capsys):
        import cuepoint.following as following_mod

        monkeypatch.setattr(following_mod, "FOLLOWING", {"/dj-a", "/dj-b"})
        show_following()
        out = capsys.readouterr().out
        assert "2 artists" in out
