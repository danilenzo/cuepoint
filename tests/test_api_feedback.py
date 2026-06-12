"""Tests for /feedback endpoints."""

import pytest
from fastapi.testclient import TestClient

from cuepoint import db as store
from cuepoint.api import app


@pytest.fixture
def client(tmp_db):
    with TestClient(app) as c:
        yield c


class TestPostFeedback:
    def test_single_item(self, client):
        r = client.post(
            "/feedback",
            json={
                "event_id": "evt-1",
                "verdict": "went",
                "city": "Berlin",
                "title": "Night",
                "breakdown": {"sc_followers": 100.5},
                "genres": ["Techno"],
                "artist_ids": ["a-1"],
            },
        )
        assert r.status_code == 200
        assert r.json() == {"saved": 1}
        rows = store.get_all_feedback()
        assert rows[0]["verdict"] == "went"

    def test_batch(self, client):
        items = [{"event_id": f"evt-{i}", "verdict": "went" if i % 2 else "skipped"} for i in range(5)]
        r = client.post("/feedback", json=items)
        assert r.status_code == 200
        assert r.json() == {"saved": 5}

    def test_invalid_verdict_422(self, client):
        r = client.post("/feedback", json={"event_id": "e", "verdict": "maybe"})
        assert r.status_code == 422

    def test_empty_event_id_422(self, client):
        r = client.post("/feedback", json={"event_id": "", "verdict": "went"})
        assert r.status_code == 422

    def test_non_numeric_breakdown_422(self, client):
        r = client.post("/feedback", json={"event_id": "e", "verdict": "went", "breakdown": {"k": "high"}})
        assert r.status_code == 422

    def test_oversized_batch_413(self, client):
        items = [{"event_id": f"e-{i}", "verdict": "went"} for i in range(101)]
        r = client.post("/feedback", json=items)
        assert r.status_code == 413

    def test_upsert(self, client):
        client.post("/feedback", json={"event_id": "e1", "verdict": "went"})
        client.post("/feedback", json={"event_id": "e1", "verdict": "skipped"})
        rows = store.get_all_feedback()
        assert len(rows) == 1
        assert rows[0]["verdict"] == "skipped"


class TestFeedbackStats:
    def test_stats_shape(self, client):
        client.post("/feedback", json={"event_id": "e1", "verdict": "went", "genres": ["Techno"]})
        r = client.get("/feedback/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["counts"] == {"went": 1}
        assert "multipliers" in body
        assert "genre_boosts" in body


class TestCorsNullOrigin:
    def test_null_origin_allowed(self, client):
        r = client.post(
            "/feedback",
            json={"event_id": "e1", "verdict": "went"},
            headers={"Origin": "null"},
        )
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "null"
