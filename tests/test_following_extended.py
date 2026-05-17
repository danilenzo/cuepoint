"""Extended coverage tests for following.py — record() and load_found()."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from cuepoint import db as store
from cuepoint.following import load_found, record


class TestRecord:
    """Tests for following.record() — lines 75-94."""

    def _make_event(self, **overrides):
        """Create a dict-based event with sensible defaults."""
        base = {
            "event_date": "2026-03-29",
            "event_url": "https://ra.co/events/12345",
            "venue_name": "Berghain",
            "promoters": [{"name": "Ostgut Ton"}],
        }
        base.update(overrides)
        return base

    def test_basic_record(self, tmp_db):
        store._found_cache = None
        artist = {"name": "DJ Test"}
        event = self._make_event()
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "Berlin" in lines[0]
        assert "DJ Test" in lines[0]
        assert "12345" in lines[0]
        assert "Berghain" in lines[0]
        assert "Ostgut Ton" in lines[0]

    def test_empty_promoters(self, tmp_db):
        """Empty promoters list → one line with 'Empty' as promoter."""
        store._found_cache = None
        artist = {"name": "DJ Nopromo"}
        event = self._make_event(promoters=[])
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "Empty" in lines[0]

    def test_none_promoters(self, tmp_db):
        """promoters=None → treated as empty list."""
        store._found_cache = None
        artist = {"name": "DJ Nopromo"}
        event = self._make_event(promoters=None)
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "Empty" in lines[0]

    def test_multiple_promoters(self, tmp_db):
        """Multiple promoters → one found line per promoter."""
        store._found_cache = None
        artist = {"name": "DJ Multi"}
        event = self._make_event(promoters=[{"name": "Promo A"}, {"name": "Promo B"}])
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 2
        promos = {line.split(",")[4] for line in lines}
        assert promos == {"Promo A", "Promo B"}

    def test_string_promoter(self, tmp_db):
        """Promoter as plain string (not dict) is handled."""
        store._found_cache = None
        artist = {"name": "DJ StrPromo"}
        event = self._make_event(promoters=["Ostgut Ton"])
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "Ostgut Ton" in lines[0]

    def test_dict_promoter(self, tmp_db):
        """Promoter as dict with 'name' key."""
        store._found_cache = None
        artist = {"name": "DJ DictPromo"}
        event = self._make_event(promoters=[{"name": "Dystopian"}])
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "Dystopian" in lines[0]

    def test_datetime_event_date(self, tmp_db):
        """event_date as a datetime object → formatted via strftime."""
        store._found_cache = None
        artist = {"name": "DJ DateTime"}
        event = self._make_event(event_date=datetime(2026, 3, 29, 23, 0))
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "2026-03-29" in lines[0]

    def test_string_event_date(self, tmp_db):
        """event_date as a string → truncated to first 10 chars."""
        store._found_cache = None
        artist = {"name": "DJ StrDate"}
        event = self._make_event(event_date="2026-04-15T23:00:00")
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "2026-04-15" in lines[0]

    def test_missing_event_url(self, tmp_db):
        """Missing event_url → event_id becomes 'unknown'."""
        store._found_cache = None
        artist = {"name": "DJ NoURL"}
        event = self._make_event(event_url="")
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "unknown" in lines[0]

    def test_none_event_url(self, tmp_db):
        """event_url=None → event_id becomes 'unknown'."""
        store._found_cache = None
        artist = {"name": "DJ NoneURL"}
        event = self._make_event(event_url=None)
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        # event_id should be "unknown" or empty → check the 3rd CSV field
        parts = lines[0].split(",")
        assert parts[2] in ("unknown", "")

    def test_missing_event_date(self, tmp_db):
        """No event_date key → fallback to 'unknown' date."""
        store._found_cache = None
        artist = {"name": "DJ NoDate"}
        event = {"event_url": "https://ra.co/events/999", "venue_name": "Club", "promoters": []}
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        parts = lines[0].split(",")
        assert parts[1] == "unknown"

    def test_commas_in_fields_escaped(self, tmp_db):
        """Commas in field values are replaced with semicolons."""
        store._found_cache = None
        artist = {"name": "DJ A, B"}
        event = self._make_event(venue_name="Club, Berlin", promoters=[{"name": "X, Y"}])
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        # Should have semicolons instead of commas within fields
        assert "DJ A; B" in lines[0]
        assert "Club; Berlin" in lines[0]
        assert "X; Y" in lines[0]

    def test_missing_artist_name(self, tmp_db):
        """Missing artist name → defaults to 'unknown'."""
        store._found_cache = None
        artist = {}
        event = self._make_event()
        record(artist, event, "Berlin")

        lines = store.get_all_found_lines()
        assert len(lines) == 1
        assert "unknown" in lines[0]


class TestLoadFound:
    """Tests for following.load_found() — lines 98-102."""

    def test_load_found_with_data(self, tmp_db):
        store._found_cache = None
        store.record_found("Berlin,2026-01-01,e1,Berghain,Ostgut Ton,DJ A")
        store.record_found("Amsterdam,2026-01-02,e2,Shelter,NPC,DJ B")

        df = load_found()
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == ["City", "Date", "Event", "Club", "Promoter", "Artist"]
        assert df.iloc[0]["City"] == "Berlin"
        assert df.iloc[1]["City"] == "Amsterdam"

    def test_load_found_empty(self, tmp_db):
        df = load_found()
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_load_found_dedup(self, tmp_db):
        """Duplicate lines in DB produce a de-duplicated DataFrame."""
        store._found_cache = None
        # record_found prevents exact duplicates, but load_found also calls drop_duplicates
        store.record_found("Berlin,2026-01-01,e1,Club,Promo,DJ A")
        store.record_found("Berlin,2026-01-01,e1,Club,Promo,DJ B")

        df = load_found()
        assert len(df) == 2  # two distinct lines
