"""Tests for filter_df() in scoring.py."""
from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd
import pytest

from cuepoint.scoring import filter_df


def _make_event(
    event_id="ev1",
    genres=None,
    artists_info=None,
    artists_list_info_past=None,
):
    """Build a minimal event row dict for filter_df testing."""
    return {
        "event_id": event_id,
        "genres": genres or [],
        "artists_info": artists_info or [],
        "artists_list_info_past": artists_list_info_past or [],
    }


class TestFilterDf:
    def test_empty_df(self):
        df = pd.DataFrame(columns=["event_id", "genres", "artists_info", "artists_list_info_past"])
        result = filter_df(df)
        assert len(result) == 0

    @patch("cuepoint.scoring.cfg.genre_filter", return_value=["Techno"])
    @patch("cuepoint.scoring.is_following", return_value=False)
    def test_event_with_no_ra_genres_passes(self, mock_follow, mock_genres):
        """Events with no RA genre tags pass unconditionally."""
        row = _make_event(genres=[])
        df = pd.DataFrame([row])
        result = filter_df(df)
        assert len(result) == 1

    @patch("cuepoint.scoring.cfg.genre_filter", return_value=["Techno"])
    @patch("cuepoint.scoring.is_following", return_value=False)
    def test_matching_genre_passes(self, mock_follow, mock_genres):
        row = _make_event(
            genres=[{"name": "Techno"}],
            artists_info=[{"id": "1", "name": "A", "sc_tags": json.dumps(["Techno"])}],
        )
        df = pd.DataFrame([row])
        result = filter_df(df)
        assert len(result) == 1

    @patch("cuepoint.scoring.cfg.genre_filter", return_value=["Techno"])
    @patch("cuepoint.scoring.is_following", return_value=False)
    def test_non_matching_genre_filtered(self, mock_follow, mock_genres):
        row = _make_event(
            genres=[{"name": "Hip-Hop"}],
            artists_info=[{"id": "1", "name": "A", "sc_tags": json.dumps(["House"])}],
        )
        df = pd.DataFrame([row])
        result = filter_df(df)
        assert len(result) == 0

    @patch("cuepoint.scoring.cfg.genre_filter", return_value=["Techno"])
    @patch("cuepoint.scoring.is_following", return_value=True)
    def test_followed_artist_passes_regardless(self, mock_follow, mock_genres):
        """Events with followed artists pass even without genre match."""
        row = _make_event(
            genres=[{"name": "Jazz"}],
            artists_info=[{"id": "1", "name": "A", "soundcloud": "/artist-a"}],
        )
        df = pd.DataFrame([row])
        result = filter_df(df)
        assert len(result) == 1

    @patch("cuepoint.scoring.cfg.genre_filter", return_value=["Techno"])
    @patch("cuepoint.scoring.is_following", return_value=False)
    def test_deduplicates_by_event_id(self, mock_follow, mock_genres):
        rows = [
            _make_event(event_id="ev1", genres=[]),
            _make_event(event_id="ev1", genres=[]),
        ]
        df = pd.DataFrame(rows)
        result = filter_df(df)
        assert len(result) == 1

    @patch("cuepoint.scoring.cfg.genre_filter", return_value=["Techno"])
    @patch("cuepoint.scoring.is_following", return_value=False)
    def test_ra_genre_match_passes(self, mock_follow, mock_genres):
        """RA genre tags (row['genres']) matching is sufficient."""
        row = _make_event(
            genres=[{"name": "Techno"}],
            artists_info=[{"id": "1", "name": "A"}],
        )
        df = pd.DataFrame([row])
        result = filter_df(df)
        assert len(result) == 1

    @patch("cuepoint.scoring.cfg.genre_filter", return_value=["Techno"])
    @patch("cuepoint.scoring.is_following", return_value=False)
    def test_multiple_events_mixed(self, mock_follow, mock_genres):
        rows = [
            _make_event(event_id="pass1", genres=[]),  # passes: no RA genres
            _make_event(event_id="pass2", genres=[{"name": "Techno"}]),  # passes: genre match
            _make_event(event_id="fail1", genres=[{"name": "Pop"}]),  # fails: no match
        ]
        df = pd.DataFrame(rows)
        result = filter_df(df)
        assert set(result["event_id"]) == {"pass1", "pass2"}
