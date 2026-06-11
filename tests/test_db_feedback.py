"""Tests for event_feedback storage."""

from cuepoint import db as store


class TestFeedbackStorage:
    def test_save_and_get(self, tmp_db):
        store.save_feedback(
            "evt-1",
            "Berlin",
            "went",
            event_title="Techno Night",
            breakdown={"sc_followers": 1200.5, "ra_genre": 5000},
            genres=["Techno", "Hard Techno"],
            artist_ids=["a-1", "a-2"],
        )
        rows = store.get_all_feedback()
        assert len(rows) == 1
        row = rows[0]
        assert row["event_id"] == "evt-1"
        assert row["verdict"] == "went"
        assert row["breakdown"]["sc_followers"] == 1200.5
        assert row["genres"] == ["Techno", "Hard Techno"]
        assert row["artist_ids"] == ["a-1", "a-2"]

    def test_upsert_overwrites_verdict(self, tmp_db):
        store.save_feedback("evt-1", "Berlin", "went")
        store.save_feedback("evt-1", "Berlin", "skipped")
        rows = store.get_all_feedback()
        assert len(rows) == 1
        assert rows[0]["verdict"] == "skipped"

    def test_invalid_verdict_rejected(self, tmp_db):
        import sqlite3

        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            store.save_feedback("evt-1", "Berlin", "maybe")

    def test_malformed_json_row_skipped(self, tmp_db):
        store.save_feedback("evt-ok", "Berlin", "went")
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO event_feedback (event_id, city, verdict, breakdown, genres, artist_ids, recorded_at) "
            "VALUES ('evt-bad', 'Berlin', 'went', '{not json', '[]', '[]', '2026-01-01')"
        )
        conn.commit()
        rows = store.get_all_feedback()
        assert [r["event_id"] for r in rows] == ["evt-ok"]

    def test_count_and_clear(self, tmp_db):
        store.save_feedback("evt-1", "Berlin", "went")
        store.save_feedback("evt-2", "Berlin", "skipped")
        store.save_feedback("evt-3", "Berlin", "went")
        assert store.count_feedback() == {"went": 2, "skipped": 1}
        assert store.clear_feedback() == 3
        assert store.get_all_feedback() == []
