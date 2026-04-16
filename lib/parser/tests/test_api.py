"""Tests for the FastAPI layer (api.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# Patch heavy imports before importing api module
@pytest.fixture(autouse=True)
def _patch_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent api.py from triggering real DB init or config loading."""
    # db.init_db() runs on import — patch it
    import db as store

    monkeypatch.setattr(store, "init_db", lambda: None)


@pytest.fixture()
def client() -> TestClient:
    from api import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_root(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "techno_scan API"
    assert "cities" in body
    assert isinstance(body["cities"], list)
    assert len(body["cities"]) > 0


# ---------------------------------------------------------------------------
# GET /cities
# ---------------------------------------------------------------------------


def test_list_cities(client: TestClient) -> None:
    resp = client.get("/cities")
    assert resp.status_code == 200
    cities = resp.json()["cities"]
    assert "berlin" in cities
    assert "london" in cities


# ---------------------------------------------------------------------------
# POST /scan — validation
# ---------------------------------------------------------------------------


def test_scan_invalid_city(client: TestClient) -> None:
    resp = client.post("/scan", json={"cities": ["narnia"]})
    assert resp.status_code == 400
    assert "narnia" in resp.json()["detail"]


def test_scan_empty_cities(client: TestClient) -> None:
    resp = client.post("/scan", json={"cities": []})
    assert resp.status_code == 422  # min_length=1 validation


@patch("api._run_scan_with_capture")
def test_scan_starts_background(mock_run: MagicMock, client: TestClient) -> None:
    """POST /scan should return immediately with a scan_id."""
    # Make the mock block briefly to simulate a running scan
    mock_run.side_effect = lambda *a, **kw: None

    resp = client.post("/scan", json={"cities": ["berlin"], "days": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert "scan_id" in body
    assert body["status"] == "pending"
    assert body["cities"] == ["berlin"]
    assert len(body["scan_id"]) == 12


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


def test_status_empty(client: TestClient) -> None:
    """Before any scans, /status returns an empty list."""
    # Clear scan state
    import api

    with api._scans_lock:
        api._scans.clear()

    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json() == []


@patch("api._run_scan_with_capture")
def test_status_after_scan(mock_run: MagicMock, client: TestClient) -> None:
    mock_run.side_effect = lambda *a, **kw: None
    import api

    with api._scans_lock:
        api._scans.clear()

    client.post("/scan", json={"cities": ["berlin"]})
    resp = client.get("/status")
    assert resp.status_code == 200
    scans = resp.json()
    assert len(scans) == 1
    assert scans[0]["cities"] == ["berlin"]


# ---------------------------------------------------------------------------
# GET /status/{scan_id}
# ---------------------------------------------------------------------------


def test_status_not_found(client: TestClient) -> None:
    resp = client.get("/status/nonexistent123")
    assert resp.status_code == 404


@patch("api._run_scan_with_capture")
def test_status_by_id(mock_run: MagicMock, client: TestClient) -> None:
    mock_run.side_effect = lambda *a, **kw: None
    import api

    with api._scans_lock:
        api._scans.clear()

    scan_resp = client.post("/scan", json={"cities": ["amsterdam"]})
    scan_id = scan_resp.json()["scan_id"]

    resp = client.get(f"/status/{scan_id}")
    assert resp.status_code == 200
    assert resp.json()["scan_id"] == scan_id
    assert resp.json()["cities"] == ["amsterdam"]


# ---------------------------------------------------------------------------
# GET /results/{city}
# ---------------------------------------------------------------------------


def test_results_unknown_city(client: TestClient) -> None:
    resp = client.get("/results/narnia")
    assert resp.status_code == 404
    assert "Unknown city" in resp.json()["detail"]


def test_results_no_scan_yet(client: TestClient) -> None:
    import api

    with api._results_lock:
        api._results.pop("berlin", None)

    resp = client.get("/results/berlin")
    assert resp.status_code == 404
    assert "No results" in resp.json()["detail"]


def test_results_with_data(client: TestClient) -> None:
    import api

    sample_events: list[dict[str, Any]] = [
        {
            "event_id": "123",
            "title": "Techno Night",
            "event_date": "2026-04-20",
            "start_time": "2026-04-20T23:00",
            "venue_name": "Berghain",
            "event_url": "https://ra.co/events/123",
            "attending": 500,
            "score": 42000.5,
            "lineup_notable": 3,
            "lineup_total": 5,
            "genres": ["Techno"],
            "artists": [{"name": "DJ Test", "sc_followers": 10000}],
            "flyer": None,
            "city": "Berlin",
        }
    ]

    with api._results_lock:
        api._results["berlin"] = sample_events

    resp = client.get("/results/berlin")
    assert resp.status_code == 200
    body = resp.json()
    assert body["city"] == "Berlin"
    assert body["event_count"] == 1
    assert body["events"][0]["title"] == "Techno Night"
    assert body["events"][0]["venue_name"] == "Berghain"
    assert body["events"][0]["score"] == 42000.5


def test_results_case_insensitive(client: TestClient) -> None:
    import api

    with api._results_lock:
        api._results["london"] = [
            {
                "event_id": "456",
                "title": "DnB Rave",
                "event_date": "2026-04-21",
                "start_time": "2026-04-21T22:00",
                "venue_name": "Fabric",
                "event_url": "https://ra.co/events/456",
                "attending": 200,
                "score": 15000.0,
                "lineup_notable": 1,
                "lineup_total": 4,
                "genres": ["Drum & Bass"],
                "artists": [],
                "flyer": None,
                "city": "London",
            }
        ]

    # Query with different casing
    resp = client.get("/results/London")
    assert resp.status_code == 200
    assert resp.json()["city"] == "London"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def test_serialize_artist() -> None:
    from api import _serialize_artist

    info: dict[str, Any] = {
        "name": "Test DJ",
        "soundcloud": "https://soundcloud.com/testdj",
        "sc_followers": 5000,
        "discogs": "https://discogs.com/artist/123",
        "dc_have": 100,
        "dc_want": 50,
        "bandcamp": None,
        "bc_supporters": None,
        "country": "DE",
        "sc_tags": ["techno", "minimal"],
        "_rising": True,
        "_similarity_score": 0.85,
    }

    result = _serialize_artist(info)
    assert result["name"] == "Test DJ"
    assert result["sc_followers"] == 5000
    assert result["rising"] is True
    assert result["tags"] == ["techno", "minimal"]


def test_serialize_artist_none() -> None:
    from api import _serialize_artist

    assert _serialize_artist(None) == {}
