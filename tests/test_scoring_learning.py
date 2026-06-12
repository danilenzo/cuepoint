"""End-to-end: feedback in DB changes sort_df ranking; disabled = baseline."""

import pandas as pd

from cuepoint import config as cfg
from cuepoint import db as store
from cuepoint.scoring import sort_df
from tests.conftest import _make_event_row


def _learning_cfg(base_cfg, enabled=True):
    return {
        **base_cfg,
        "learning": {
            "enabled": enabled,
            "min_feedback": 2,
            "min_per_class": 1,
            "genre_boost_unit": 500,
            "genre_boost_cap": 3,
            "artist_boost": 2000,
        },
    }


def _two_event_df(sample_artist_info):
    a1 = {**sample_artist_info, "id": "a-1", "name": "DJ One", "soundcloud": "/dj-one"}
    a2 = {**sample_artist_info, "id": "a-2", "name": "DJ Two", "soundcloud": "/dj-two"}
    return pd.DataFrame(
        [
            _make_event_row("evt-1", [a1], ["Techno"], title="Event One"),
            _make_event_row("evt-2", [a2], ["Techno"], title="Event Two"),
        ]
    )


class TestScoringWithLearning:
    def test_disabled_matches_baseline(self, tmp_db, mock_config, sample_artist_info, monkeypatch):
        df = _two_event_df(sample_artist_info)
        monkeypatch.setattr(cfg, "_cfg", _learning_cfg(mock_config, enabled=False))
        store.save_feedback("evt-x", "Berlin", "went", artist_ids=["a-2"], genres=["Techno"])
        baseline = sort_df(df.copy())
        # identical artists -> identical scores regardless of feedback
        assert baseline.iloc[0]["_score"] == baseline.iloc[1]["_score"]

    def test_artist_boost_reranks(self, tmp_db, mock_config, sample_artist_info, monkeypatch):
        df = _two_event_df(sample_artist_info)
        monkeypatch.setattr(cfg, "_cfg", _learning_cfg(mock_config))
        store.save_feedback("evt-x", "Berlin", "went", artist_ids=["a-2"])
        result = sort_df(df.copy())
        assert result.iloc[0]["title"] == "Event Two"
        bd = result.iloc[0]["_score_breakdown"]
        assert bd["artist_affinity"] == 2000.0

    def test_genre_boost_in_breakdown(self, tmp_db, mock_config, sample_artist_info, monkeypatch):
        df = _two_event_df(sample_artist_info)
        monkeypatch.setattr(cfg, "_cfg", _learning_cfg(mock_config))
        store.save_feedback("evt-x", "Berlin", "went", genres=["Techno"])
        result = sort_df(df.copy())
        # both events are Techno -> both get the boost
        for _, row in result.iterrows():
            assert row["_score_breakdown"]["genre_affinity"] == 500.0

    def test_multiplier_scales_contribution(self, tmp_db, mock_config, sample_artist_info, monkeypatch):
        df = _two_event_df(sample_artist_info)
        monkeypatch.setattr(cfg, "_cfg", _learning_cfg(mock_config))
        baseline = sort_df(df.copy())
        base_sc = baseline.iloc[0]["_score_breakdown"]["sc_followers"]
        # went dominated by sc_followers, skipped dominated by rising
        store.save_feedback("f-1", "Berlin", "went", breakdown={"sc_followers": 90.0, "rising": 10.0})
        store.save_feedback("f-2", "Berlin", "skipped", breakdown={"sc_followers": 10.0, "rising": 90.0})
        boosted = sort_df(df.copy())
        new_sc = boosted.iloc[0]["_score_breakdown"]["sc_followers"]
        assert new_sc == base_sc * 2.0  # clamped at multiplier_max
