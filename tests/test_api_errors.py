"""Tests for API error paths, validation, and scan lifecycle."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    import cuepoint.db as store_mod

    monkeypatch.setattr(store_mod, "init_db", lambda: None)


@pytest.fixture(autouse=True)
def _clear_api_state() -> None:
    import cuepoint.api as api_mod

    api_mod._scans.clear()
    api_mod._rate_log.clear()


@pytest.fixture()
def client() -> TestClient:
    from cuepoint.api import app

    with TestClient(app) as c:
        yield c


class TestScanIdValidation:
    def test_invalid_scan_id_format(self, client: TestClient):
        resp = client.get("/status/INVALID!")
        assert resp.status_code == 400
        assert "Invalid scan ID" in resp.json()["detail"]

    def test_scan_id_too_short(self, client: TestClient):
        resp = client.get("/status/abc")
        assert resp.status_code == 400

    def test_scan_id_too_long(self, client: TestClient):
        resp = client.get("/status/abcdef123456789")
        assert resp.status_code == 400

    def test_valid_format_not_found(self, client: TestClient):
        resp = client.get("/status/abcdef123456")
        assert resp.status_code == 404


class TestScanEviction:
    def test_stale_scans_evicted(self, client: TestClient):
        import cuepoint.api as api_mod

        for i in range(5):
            sid = f"{i:012x}"
            api_mod._scans[sid] = {
                "scan_id": sid,
                "status": "done",
                "cities": ["berlin"],
                "_mono": time.monotonic() - 7200,
            }

        api_mod._evict_stale_scans()
        assert len(api_mod._scans) == 0

    def test_max_scans_cap(self, client: TestClient):
        import cuepoint.api as api_mod

        for i in range(250):
            sid = f"{i:012x}"
            api_mod._scans[sid] = {
                "scan_id": sid,
                "status": "pending",
                "cities": ["berlin"],
                "_mono": time.monotonic() - i,
            }

        api_mod._evict_stale_scans()
        assert len(api_mod._scans) <= api_mod._MAX_SCANS

    def test_running_scans_not_evicted(self, client: TestClient):
        import cuepoint.api as api_mod

        sid = "aabbccdd0011"
        api_mod._scans[sid] = {
            "scan_id": sid,
            "status": "running",
            "cities": ["berlin"],
            "_mono": time.monotonic() - 7200,
        }

        api_mod._evict_stale_scans()
        assert sid in api_mod._scans


class TestPaginationEdgeCases:
    @patch("cuepoint.api.store.get_api_results")
    def test_page_beyond_range(self, mock_get: MagicMock, client: TestClient):
        mock_get.return_value = [{"event_id": "1"}]
        resp = client.get("/results/berlin?page=999")
        assert resp.status_code == 200
        assert resp.json()["events"] == []

    @patch("cuepoint.api.store.get_api_results")
    def test_max_page_size(self, mock_get: MagicMock, client: TestClient):
        mock_get.return_value = [{"event_id": str(i)} for i in range(300)]
        resp = client.get("/results/berlin?page_size=200")
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == 200

    def test_page_size_over_max_rejected(self, client: TestClient):
        resp = client.get("/results/berlin?page_size=201")
        assert resp.status_code == 422

    def test_page_zero_rejected(self, client: TestClient):
        resp = client.get("/results/berlin?page=0")
        assert resp.status_code == 422

    def test_negative_page_rejected(self, client: TestClient):
        resp = client.get("/results/berlin?page=-1")
        assert resp.status_code == 422


class TestCsvExportSafety:
    @patch("cuepoint.api.store.get_api_results")
    def test_filename_sanitized(self, mock_get: MagicMock, client: TestClient):
        mock_get.return_value = []
        resp = client.get("/results/berlin/export")
        assert resp.status_code == 200
        disposition = resp.headers["content-disposition"]
        assert "berlin_events.csv" in disposition
        assert '"' in disposition

    @patch("cuepoint.api.store.get_api_results")
    def test_special_chars_in_event_data(self, mock_get: MagicMock, client: TestClient):
        mock_get.return_value = [
            {
                "event_id": "1",
                "title": 'Event "with" quotes, and commas',
                "event_date": "2026-05-01",
                "venue_name": "Test Venue",
                "attending": 100,
                "score": 50.0,
                "genres": ["Techno"],
                "event_url": "https://ra.co/events/1",
            }
        ]
        resp = client.get("/results/berlin/export")
        assert resp.status_code == 200
        assert "with" in resp.text


class TestRateLimitDecay:
    @patch("cuepoint.api._run_scan")
    def test_rate_limit_window_decays(self, mock_run: MagicMock, client: TestClient):
        import cuepoint.api as api_mod

        mock_run.side_effect = lambda *a, **kw: None

        old_timestamps = [time.monotonic() - 120] * api_mod._RATE_LIMIT_MAX
        api_mod._rate_log["testclient"] = old_timestamps

        resp = client.post("/scan", json={"cities": ["berlin"]})
        assert resp.status_code == 200


class TestScanRequestValidation:
    def test_days_too_high(self, client: TestClient):
        resp = client.post("/scan", json={"cities": ["berlin"], "days": 91})
        assert resp.status_code == 422

    def test_parallel_too_high(self, client: TestClient):
        resp = client.post("/scan", json={"cities": ["berlin"], "parallel": 9})
        assert resp.status_code == 422

    def test_days_zero(self, client: TestClient):
        resp = client.post("/scan", json={"cities": ["berlin"], "days": 0})
        assert resp.status_code == 422
