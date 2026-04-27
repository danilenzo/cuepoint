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
    import cuepoint.db as store_mod

    monkeypatch.setattr(store_mod, "init_db", lambda: None)


@pytest.fixture(autouse=True)
def _clear_api_state() -> None:
    """Reset in-memory API state between tests."""
    import cuepoint.api as api_mod

    api_mod._scans.clear()
    api_mod._rate_log.clear()


@pytest.fixture()
def client() -> TestClient:
    from cuepoint.api import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_root(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "cuepoint API"
    assert "cities" in body
    assert isinstance(body["cities"], list)
    assert len(body["cities"]) > 0


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "version" in body
    assert "db_ok" in body
    assert "cities_loaded" in body
    assert body["cities_loaded"] > 0


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


@patch("cuepoint.api._run_scan")
def test_scan_starts_background(mock_run: MagicMock, client: TestClient) -> None:
    """POST /scan should return immediately with a scan_id."""
    mock_run.side_effect = lambda *a, **kw: None

    resp = client.post("/scan", json={"cities": ["berlin"], "days": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert "scan_id" in body
    assert body["status"] == "pending"
    assert body["cities"] == ["berlin"]
    assert len(body["scan_id"]) == 12


# ---------------------------------------------------------------------------
# POST /scan — rate limiting
# ---------------------------------------------------------------------------


@patch("cuepoint.api._run_scan")
def test_scan_rate_limit(mock_run: MagicMock, client: TestClient) -> None:
    """Exceeding rate limit returns 429."""
    mock_run.side_effect = lambda *a, **kw: None

    import cuepoint.api as api_mod

    # Fire up to the limit
    for _ in range(api_mod._RATE_LIMIT_MAX):
        resp = client.post("/scan", json={"cities": ["berlin"]})
        assert resp.status_code == 200

    # Next one should be rate limited
    resp = client.post("/scan", json={"cities": ["berlin"]})
    assert resp.status_code == 429
    assert "Rate limit" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


def test_status_empty(client: TestClient) -> None:
    """Before any scans, /status returns an empty list."""
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json() == []


@patch("cuepoint.api._run_scan")
def test_status_after_scan(mock_run: MagicMock, client: TestClient) -> None:
    mock_run.side_effect = lambda *a, **kw: None

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


@patch("cuepoint.api._run_scan")
def test_status_by_id(mock_run: MagicMock, client: TestClient) -> None:
    mock_run.side_effect = lambda *a, **kw: None

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


@patch("cuepoint.api.store.get_api_results", return_value=None)
def test_results_no_scan_yet(mock_get: MagicMock, client: TestClient) -> None:
    resp = client.get("/results/berlin")
    assert resp.status_code == 404
    assert "No results" in resp.json()["detail"]


@patch("cuepoint.api.store.get_api_results")
def test_results_with_data(mock_get: MagicMock, client: TestClient) -> None:
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
    mock_get.return_value = sample_events

    resp = client.get("/results/berlin")
    assert resp.status_code == 200
    body = resp.json()
    assert body["city"] == "Berlin"
    assert body["event_count"] == 1
    assert body["page"] == 1
    assert body["total_pages"] == 1
    assert body["events"][0]["title"] == "Techno Night"
    assert body["events"][0]["venue_name"] == "Berghain"


@patch("cuepoint.api.store.get_api_results")
def test_results_pagination(mock_get: MagicMock, client: TestClient) -> None:
    events = [{"event_id": str(i), "title": f"Event {i}"} for i in range(75)]
    mock_get.return_value = events

    resp = client.get("/results/berlin?page=1&page_size=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_count"] == 75
    assert len(body["events"]) == 50
    assert body["page"] == 1
    assert body["total_pages"] == 2

    resp2 = client.get("/results/berlin?page=2&page_size=50")
    body2 = resp2.json()
    assert len(body2["events"]) == 25
    assert body2["page"] == 2


# ---------------------------------------------------------------------------
# GET /results/{city}/export
# ---------------------------------------------------------------------------


@patch("cuepoint.api.store.get_api_results")
def test_export_csv(mock_get: MagicMock, client: TestClient) -> None:
    mock_get.return_value = [
        {
            "event_id": "123",
            "title": "Techno Night",
            "event_date": "2026-04-20",
            "venue_name": "Berghain",
            "attending": 500,
            "score": 42000.5,
            "genres": ["Techno", "Industrial"],
            "event_url": "https://ra.co/events/123",
        }
    ]

    resp = client.get("/results/berlin/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().split("\n")
    assert len(lines) == 2  # header + 1 row
    assert "Techno Night" in lines[1]


def test_export_unknown_city(client: TestClient) -> None:
    resp = client.get("/results/narnia/export")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def test_serialize_artist() -> None:
    from cuepoint.api import _serialize_artist

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
    from cuepoint.api import _serialize_artist

    assert _serialize_artist(None) == {}
