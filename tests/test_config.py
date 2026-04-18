"""Tests for config.py — typed accessors from config.toml."""

from techno_scan import config as cfg


def test_defaults_with_empty_config(monkeypatch):
    """When _cfg is empty dict, accessors return defaults."""
    monkeypatch.setattr(cfg, "_cfg", {})
    assert cfg.days_ahead() == 7
    assert cfg.sc_weight() == 10
    assert cfg.followed_bonus() == 1_000_000
    assert cfg.genre_filter() == ["Techno", "Drum & Bass", "Drum n Bass"]


def test_fallback_cities(monkeypatch):
    """When config has no [cities] section, fallback dict is used."""
    monkeypatch.setattr(cfg, "_cfg", {})
    cities = cfg.cities()
    assert "berlin" in cities
    assert "amsterdam" in cities
    assert cities["berlin"] == (34, "Berlin", "de/berlin")
    assert len(cities) == 16


def test_custom_values(monkeypatch):
    """Custom values in _cfg override defaults."""
    monkeypatch.setattr(
        cfg,
        "_cfg",
        {
            "general": {"days_ahead": 14},
            "scoring": {"sc_weight": 20, "followed_bonus": 500},
            "genres": {"filter": ["House"]},
        },
    )
    assert cfg.days_ahead() == 14
    assert cfg.sc_weight() == 20
    assert cfg.followed_bonus() == 500
    assert cfg.genre_filter() == ["House"]
    # Unset sections still return defaults
    assert cfg.cache_ttl_days() == 30
