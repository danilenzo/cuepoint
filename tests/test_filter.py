"""Tests for filter_df() in event_fetcher.py."""

import json

import pandas as pd

from techno_scan.scoring import filter_df
from tests.conftest import _make_event_row


def test_genre_match_kept(mock_config):
    """Events with a matching genre artist should pass the filter."""
    artist = {
        "id": "a-1",
        "name": "Techno DJ",
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps(["Techno"]),
        "bc_tags": json.dumps([]),
    }
    df = pd.DataFrame([_make_event_row("evt-1", [artist], ["Techno"])])
    result = filter_df(df)
    assert len(result) == 1


def test_non_genre_dropped(mock_config):
    """Events with no matching genre should be dropped."""
    artist = {
        "id": "a-1",
        "name": "Jazz DJ",
        "sc_tags": json.dumps(["Jazz"]),
        "dc_styles": json.dumps(["Jazz"]),
        "bc_tags": json.dumps(["jazz"]),
    }
    df = pd.DataFrame([_make_event_row("evt-1", [artist], ["Jazz"])])
    result = filter_df(df)
    assert len(result) == 0


def test_followed_passes(mock_config):
    """Events with a followed artist should pass regardless of genre."""
    artist = {
        "id": "a-1",
        "name": "Followed",
        "soundcloud": "/dj-q-mono",
        "sc_tags": json.dumps(["Ambient"]),
        "dc_styles": json.dumps(["Ambient"]),
        "bc_tags": json.dumps(["ambient"]),
    }
    df = pd.DataFrame([_make_event_row("evt-1", [artist], ["Ambient"])])
    result = filter_df(df)
    assert len(result) == 1


def test_empty_ra_genres_pass(mock_config):
    """Events with no RA genre tags should still be checked by artist tags."""
    artist = {
        "id": "a-1",
        "name": "Techno DJ",
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps([]),
        "bc_tags": json.dumps([]),
    }
    df = pd.DataFrame([_make_event_row("evt-1", [artist], [])])
    result = filter_df(df)
    assert len(result) == 1


def test_dedup_by_event_id(mock_config):
    """Duplicate event_id rows should be deduplicated."""
    artist = {
        "id": "a-1",
        "name": "Techno DJ",
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps([]),
        "bc_tags": json.dumps([]),
    }
    row = _make_event_row("evt-1", [artist], ["Techno"])
    df = pd.DataFrame([row, row])
    result = filter_df(df)
    assert len(result) == 1


def test_empty_df(mock_config):
    """filter_df on empty DataFrame with correct columns should return empty."""
    cols = ["event_id", "artists_info", "artists_list_info_past", "genres"]
    df = pd.DataFrame(columns=cols)
    result = filter_df(df)
    assert result.empty
