"""Tests for CLI learning helpers."""

from cuepoint import config as cfg
from cuepoint import db as store
from cuepoint.event_fetcher import format_learning_stats


class TestLearningStats:
    def test_cold_start_message(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"learning": {"enabled": True}})
        out = format_learning_stats()
        assert "0 total" in out

    def test_shows_counts_and_boosts(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"learning": {"enabled": True}})
        store.save_feedback("e1", "Berlin", "went", genres=["Techno"], artist_ids=["a-1"])
        out = format_learning_stats()
        assert "went: 1" in out
        assert "Techno" in out
