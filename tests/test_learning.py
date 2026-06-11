"""Tests for feedback-driven learning adjustments."""

from cuepoint import config as cfg
from cuepoint import db as store
from cuepoint.learning import (
    TUNABLE_SIGNALS,
    LearnedAdjustments,
    compute_adjustments,
)

LEARNING_CFG = {
    "learning": {
        "enabled": True,
        "min_feedback": 4,
        "min_per_class": 2,
        "multiplier_min": 0.5,
        "multiplier_max": 2.0,
        "genre_boost_unit": 500,
        "genre_boost_cap": 3,
        "artist_boost": 2000,
    }
}


def _seed(verdict, breakdown, genres=None, artist_ids=None, eid=None):
    _seed.n = getattr(_seed, "n", 0) + 1
    store.save_feedback(
        eid or f"evt-{_seed.n}",
        "Berlin",
        verdict,
        breakdown=breakdown,
        genres=genres or [],
        artist_ids=artist_ids or [],
    )


class TestMultipliers:
    def test_neutral_below_min_feedback(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {"rising": 100.0})
        adj = compute_adjustments()
        assert adj.multipliers == {}
        assert adj.multiplier("rising") == 1.0

    def test_neutral_below_min_per_class(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        for _ in range(4):
            _seed("went", {"rising": 100.0})
        assert compute_adjustments().multipliers == {}

    def test_signal_predicting_attendance_gets_boosted(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        # went events dominated by rising; skipped dominated by sc_followers
        _seed("went", {"rising": 80.0, "sc_followers": 20.0})
        _seed("went", {"rising": 80.0, "sc_followers": 20.0})
        _seed("skipped", {"rising": 20.0, "sc_followers": 80.0})
        _seed("skipped", {"rising": 20.0, "sc_followers": 80.0})
        adj = compute_adjustments()
        # rising: shares went=0.8 skipped=0.2 -> (0.81)/(0.21) = 3.86 -> clamp 2.0
        assert adj.multiplier("rising") == 2.0
        # sc_followers: (0.21)/(0.81) = 0.259 -> clamp 0.5
        assert adj.multiplier("sc_followers") == 0.5

    def test_absent_signal_is_neutral(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {"rising": 80.0, "sc_followers": 20.0})
        _seed("went", {"rising": 80.0, "sc_followers": 20.0})
        _seed("skipped", {"rising": 20.0, "sc_followers": 80.0})
        _seed("skipped", {"rising": 20.0, "sc_followers": 80.0})
        adj = compute_adjustments()
        assert adj.multiplier("recency") == 1.0  # epsilon/epsilon

    def test_followed_never_tuned(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        assert "followed" not in TUNABLE_SIGNALS
        _seed("went", {"followed": 1_000_000.0, "rising": 50.0})
        _seed("went", {"followed": 1_000_000.0, "rising": 50.0})
        _seed("skipped", {"rising": 50.0})
        _seed("skipped", {"rising": 50.0})
        adj = compute_adjustments()
        assert adj.multiplier("followed") == 1.0

    def test_zero_total_rows_skipped(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {})  # zero total — must not crash or count
        _seed("went", {"rising": 50.0})
        _seed("went", {"rising": 50.0})
        _seed("skipped", {"sc_followers": 50.0})
        _seed("skipped", {"sc_followers": 50.0})
        adj = compute_adjustments()
        assert adj.multiplier("rising") == 2.0


class TestGenreBoosts:
    def test_net_counts_with_cap(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        for _ in range(5):
            _seed("went", {"rising": 1.0}, genres=["Techno"])
        _seed("skipped", {"rising": 1.0}, genres=["House"])
        adj = compute_adjustments()
        assert adj.genre_boosts["Techno"] == 3 * 500  # capped at 3
        assert adj.genre_boosts["House"] == -500

    def test_genres_normalized(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {"rising": 1.0}, genres=["dnb"])
        adj = compute_adjustments()
        assert adj.genre_boosts == {"Drum & Bass": 500}


class TestArtistBoosts:
    def test_went_artists_boosted_skipped_not_penalized(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)
        _seed("went", {"rising": 1.0}, artist_ids=["a-1"])
        _seed("skipped", {"rising": 1.0}, artist_ids=["a-2"])
        adj = compute_adjustments()
        assert adj.artist_boosts == {"a-1": 2000.0}


class TestSafety:
    def test_disabled_returns_neutral(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"learning": {"enabled": False}})
        _seed("went", {"rising": 1.0}, genres=["Techno"], artist_ids=["a-1"])
        adj = compute_adjustments()
        assert adj == LearnedAdjustments()

    def test_db_error_returns_neutral(self, tmp_db, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", LEARNING_CFG)

        def boom():
            raise RuntimeError("db gone")

        monkeypatch.setattr(store, "get_all_feedback", boom)
        assert compute_adjustments() == LearnedAdjustments()
