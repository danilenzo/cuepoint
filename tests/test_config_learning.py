"""Tests for [learning] config accessors."""

from cuepoint import config as cfg


class TestLearningDefaults:
    def test_defaults_without_section(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {})
        assert cfg.learning_enabled() is True
        assert cfg.learning_api_base() == "http://localhost:8000"
        assert cfg.learning_min_feedback() == 10
        assert cfg.learning_min_per_class() == 3
        assert cfg.learning_multiplier_min() == 0.5
        assert cfg.learning_multiplier_max() == 2.0
        assert cfg.learning_genre_boost_unit() == 500
        assert cfg.learning_genre_boost_cap() == 3
        assert cfg.learning_artist_boost() == 2000

    def test_reads_configured_values(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"learning": {"enabled": False, "artist_boost": 99}})
        assert cfg.learning_enabled() is False
        assert cfg.learning_artist_boost() == 99
