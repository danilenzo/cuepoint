"""Tests for html_creator.py rendering functions."""

import json

import pandas as pd

from html_creator import (
    _normalize_genre,
    df_to_genre,
    df_to_strength,
    df_to_time,
    df_to_venue,
)

# --- _normalize_genre ---


def test_normalize_genre_alias():
    assert _normalize_genre("drum n bass") == "Drum & Bass"
    assert _normalize_genre("dnb") == "Drum & Bass"


def test_normalize_genre_blacklist():
    assert _normalize_genre("electronic") is None
    assert _normalize_genre("music") is None


def test_normalize_genre_too_long():
    assert _normalize_genre("a" * 31) is None


def test_normalize_genre_no_latin():
    """Tags with no Latin letters (e.g. Japanese) should be filtered."""
    assert _normalize_genre("テクノ") is None


def test_normalize_genre_normal():
    assert _normalize_genre("techno") == "Techno"
    assert _normalize_genre("  Ambient  ") == "Ambient"


# --- df_to_genre ---


def test_df_to_genre_pills():
    """Genre pills should contain genre-pill class and genre name."""
    row = {
        "artists_info": [
            {
                "sc_tags": json.dumps(["Techno", "Techno"]),
                "dc_styles": json.dumps(["Techno"]),
                "bc_tags": json.dumps(["techno"]),
            },
        ],
        "genres": [{"name": "Techno"}],
    }
    result = df_to_genre(row)
    assert "genre-pill" in result
    assert "Techno" in result


# --- df_to_time ---


def test_df_to_time_format():
    row = {
        "start_time": pd.Timestamp("2026-03-29 23:00"),
        "end_time": pd.Timestamp("2026-03-30 08:00"),
    }
    result = df_to_time(row)
    assert "Mar 29, 2026" in result
    assert "23:00" in result
    assert "08:00" in result


def test_df_to_time_no_leading_zero():
    """Day should not have leading zero."""
    row = {
        "start_time": pd.Timestamp("2026-03-01 22:00"),
        "end_time": pd.Timestamp("2026-03-02 06:00"),
    }
    result = df_to_time(row)
    assert "Mar 1, 2026" in result


# --- df_to_strength ---


def test_df_to_strength_with_notable():
    row = {"_lineup_notable": 3, "_lineup_total": 5}
    result = df_to_strength(row)
    assert "3/5" in result
    assert "str-fill" in result


def test_df_to_strength_zero_notable():
    row = {"_lineup_notable": 0, "_lineup_total": 4}
    result = df_to_strength(row)
    assert result == "0/4"
    assert "str-fill" not in result


def test_df_to_strength_empty():
    row = {"_lineup_notable": 0, "_lineup_total": 0}
    result = df_to_strength(row)
    assert result == ""


# --- df_to_venue ---


def test_df_to_venue_ra_link():
    row = {"venue_url": "/club/berghain", "venue_name": "Berghain"}
    result = df_to_venue(row)
    assert "Berghain" in result
    assert 'href="https://ra.co/club/berghain"' in result


def test_df_to_venue_full_url():
    row = {"venue_url": "https://bassiani.com", "venue_name": "Bassiani"}
    result = df_to_venue(row)
    assert 'href="https://bassiani.com"' in result
