"""Tests for sort_df() and _is_notable() in event_fetcher.py."""

import json

import pandas as pd

from cuepoint.scoring import _is_notable, sort_df
from tests.conftest import _make_event_row


def test_followed_bonus_top(mock_config):
    """An event with a followed artist should score higher than one without."""
    followed = {
        "id": "a-1",
        "name": "Followed",
        "soundcloud": "/dj-q-mono",
        "sc_followers": 100,
        "sc_tags": json.dumps(["Ambient"]),
        "dc_styles": json.dumps([]),
        "bc_tags": json.dumps([]),
    }
    big_artist = {
        "id": "a-2",
        "name": "Big DJ",
        "soundcloud": "/big-dj-not-followed",
        "sc_followers": 50000,
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps(["Techno"]),
        "bc_tags": json.dumps(["techno"]),
    }
    rows = [
        _make_event_row("evt-1", [followed], ["Ambient"], title="Followed Event"),
        _make_event_row("evt-2", [big_artist], ["Techno"], title="Big Event"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    assert result.iloc[0]["title"] == "Followed Event"


def test_sc_genre_weighting(mock_config):
    """Higher SC followers with genre match should score higher."""
    high = {
        "id": "a-1",
        "name": "High SC",
        "soundcloud": "/high-sc",
        "sc_followers": 10000,
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps([]),
        "bc_tags": json.dumps([]),
    }
    low = {
        "id": "a-2",
        "name": "Low SC",
        "soundcloud": "/low-sc",
        "sc_followers": 100,
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps([]),
        "bc_tags": json.dumps([]),
    }
    rows = [
        _make_event_row("evt-1", [low], ["Techno"], title="Low Event"),
        _make_event_row("evt-2", [high], ["Techno"], title="High Event"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    assert result.iloc[0]["title"] == "High Event"


def test_empty_df(mock_config):
    """sort_df on empty DataFrame should return empty."""
    df = pd.DataFrame()
    result = sort_df(df)
    assert result.empty


def test_descending_order(mock_config):
    """Results should be sorted descending by score."""
    a1 = {
        "id": "a-1",
        "name": "DJ A",
        "soundcloud": "/dj-a",
        "sc_followers": 5000,
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps([]),
        "bc_tags": json.dumps([]),
    }
    a2 = {
        "id": "a-2",
        "name": "DJ B",
        "soundcloud": "/dj-b",
        "sc_followers": 1000,
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps([]),
        "bc_tags": json.dumps([]),
    }
    rows = [
        _make_event_row("evt-1", [a2], ["Techno"], title="Small"),
        _make_event_row("evt-2", [a1], ["Techno"], title="Big"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    scores = list(result["_score"])
    assert scores == sorted(scores, reverse=True)


def test_is_notable_sc(mock_config):
    assert _is_notable({"sc_followers": 1500}) is True
    assert _is_notable({"sc_followers": 500}) is False


def test_is_notable_dc(mock_config):
    assert _is_notable({"dc_have": 100}) is True
    assert _is_notable({"dc_have": 10}) is False


def test_is_notable_bc(mock_config):
    assert _is_notable({"bc_supporters": 50}) is True
    assert _is_notable({"bc_supporters": 5}) is False


def test_is_notable_none():
    assert _is_notable(None) is False


# ---------------------------------------------------------------------------
# WS3: Discovery signal scoring tests
# ---------------------------------------------------------------------------


def _base_artist(**overrides):
    """Minimal artist dict with zero-score defaults."""
    a = {
        "id": "a-test",
        "name": "Test DJ",
        "soundcloud": "/test-dj",
        "sc_followers": 0,
        "sc_tags": json.dumps([]),
        "dc_styles": json.dumps([]),
        "bc_tags": json.dumps([]),
    }
    a.update(overrides)
    return a


def test_rising_bonus(mock_config):
    """Rising artist should score higher than identical non-rising artist."""
    rising = _base_artist(id="a-1", name="Rising DJ", _rising=True)
    normal = _base_artist(id="a-2", name="Normal DJ")
    rows = [
        _make_event_row("evt-1", [normal], ["Techno"], title="Normal"),
        _make_event_row("evt-2", [rising], ["Techno"], title="Rising"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    assert result.iloc[0]["title"] == "Rising"


def test_similarity_score(mock_config):
    """Artist similar to a followed artist should get a score boost."""
    similar = _base_artist(id="a-1", name="Similar DJ", _similarity_score=80)
    plain = _base_artist(id="a-2", name="Plain DJ")
    rows = [
        _make_event_row("evt-1", [plain], ["Techno"], title="Plain"),
        _make_event_row("evt-2", [similar], ["Techno"], title="Similar"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    assert result.iloc[0]["title"] == "Similar"


def test_shared_labels(mock_config):
    """Artist sharing labels with followed artists should rank higher."""
    shared = _base_artist(id="a-1", name="Label DJ", _shared_labels=["Mord", "Perc Trax"])
    plain = _base_artist(id="a-2", name="Plain DJ")
    rows = [
        _make_event_row("evt-1", [plain], ["Techno"], title="Plain"),
        _make_event_row("evt-2", [shared], ["Techno"], title="Shared Labels"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    assert result.iloc[0]["title"] == "Shared Labels"


def test_dc_ratio(mock_config):
    """Artist with higher DC want/have ratio should rank higher."""
    rare = _base_artist(id="a-1", name="Rare DJ", dc_ratio=3.5)
    common = _base_artist(id="a-2", name="Common DJ", dc_ratio=0.5)
    rows = [
        _make_event_row("evt-1", [common], ["Techno"], title="Common"),
        _make_event_row("evt-2", [rare], ["Techno"], title="Rare"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    assert result.iloc[0]["title"] == "Rare"


def test_recency_bonus(mock_config):
    """Artist with a recent release should rank higher than one without."""
    from datetime import datetime, timedelta

    recent_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    recent = _base_artist(id="a-1", name="Recent DJ", bc_latest_release=recent_date)
    stale = _base_artist(id="a-2", name="Stale DJ")
    rows = [
        _make_event_row("evt-1", [stale], ["Techno"], title="Stale"),
        _make_event_row("evt-2", [recent], ["Techno"], title="Recent"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    assert result.iloc[0]["title"] == "Recent"


def test_recency_old_release_no_bonus(mock_config):
    """Release older than 365 days should get no recency bonus."""
    old_date = "2020-01-01"
    old = _base_artist(id="a-1", name="Old DJ", bc_latest_release=old_date)
    plain = _base_artist(id="a-2", name="Plain DJ")
    rows = [
        _make_event_row("evt-1", [old], ["Techno"], title="Old"),
        _make_event_row("evt-2", [plain], ["Techno"], title="Plain"),
    ]
    df = pd.DataFrame(rows)
    result = sort_df(df)
    # Both should have the same score (RA genre bonus only) — order doesn't matter
    scores = list(result["_score"])
    assert scores[0] == scores[1]
