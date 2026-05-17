"""Extended tests for discogs.py — covers _load_token, _get_client, close_client,
_api_get rate limiting, and populate_discogs_info pagination/edge cases."""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import cuepoint.discogs as discogs_mod
from cuepoint.discogs import (
    _get_client,
    _load_token,
    close_client,
    populate_discogs_info,
)

_run = asyncio.run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_client():
    """Reset the module-level _client to None between tests."""
    discogs_mod._client = None


def _reset_rate_state():
    """Reset rate-limit state between tests."""
    discogs_mod._last_request_time = 0.0
    discogs_mod._rate_remaining = 60


def _make_response(
    status_code: int = 200,
    json_data: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    """Build a minimal httpx.Response for mocking."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data or {},
        headers=headers or {},
        request=httpx.Request("GET", "https://api.discogs.com/test"),
    )
    return resp


# ====================================================================
# _load_token
# ====================================================================


class TestLoadToken:
    def test_env_var_set_returns_immediately(self, monkeypatch):
        monkeypatch.setenv("DISCOGS_TOKEN", "env-token-123")
        assert _load_token() == "env-token-123"

    def test_env_var_not_set_file_exists_valid_token(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        token_file = tmp_path / ".discogs_token"
        token_file.write_text("file-token-456\n", encoding="utf-8")
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", token_file)
        assert _load_token() == "file-token-456"

    def test_env_var_not_set_file_exists_empty(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        token_file = tmp_path / ".discogs_token"
        token_file.write_text("   \n", encoding="utf-8")
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", token_file)
        assert _load_token() is None

    def test_env_var_not_set_file_does_not_exist(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        token_file = tmp_path / ".discogs_token_nonexistent"
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", token_file)
        assert _load_token() is None

    def test_file_world_readable_logs_warning(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        token_file = tmp_path / ".discogs_token"
        token_file.write_text("world-readable-token", encoding="utf-8")

        fake_stat = MagicMock()
        fake_stat.st_mode = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", token_file)

        with patch.object(Path, "stat", return_value=fake_stat), patch("cuepoint.discogs.logger") as mock_logger:
            result = _load_token()
            mock_logger.warning.assert_called_once()
            assert "readable by group/others" in mock_logger.warning.call_args[0][0]
            assert result == "world-readable-token"

    def test_file_stat_raises_os_error(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        token_file = tmp_path / ".discogs_token"
        token_file.write_text("valid-token", encoding="utf-8")

        mock_file = MagicMock(spec=Path)
        mock_file.exists.return_value = True
        mock_file.stat.side_effect = OSError("permission denied")
        mock_file.read_text.return_value = "valid-token"
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", mock_file)

        result = _load_token()
        assert result == "valid-token"

    def test_env_var_takes_precedence_over_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISCOGS_TOKEN", "env-wins")
        token_file = tmp_path / ".discogs_token"
        token_file.write_text("file-loses", encoding="utf-8")
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", token_file)
        assert _load_token() == "env-wins"


# ====================================================================
# _get_client
# ====================================================================


class TestGetClient:
    def setup_method(self):
        _reset_client()

    def teardown_method(self):
        if discogs_mod._client is not None and not discogs_mod._client.is_closed:
            _run(discogs_mod._client.aclose())
        _reset_client()

    def test_creates_client_with_token(self, monkeypatch):
        monkeypatch.setenv("DISCOGS_TOKEN", "my-token")
        client = _run(_get_client())
        assert client is not None
        auth_header = client.headers.get("Authorization")
        assert auth_header is not None
        assert "Discogs token=my-token" in auth_header

    def test_creates_client_without_token(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", tmp_path / "nonexistent")
        client = _run(_get_client())
        assert client is not None
        assert "Authorization" not in client.headers

    def test_returns_existing_client_if_not_closed(self, monkeypatch):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", Path("/nonexistent"))
        client1 = _run(_get_client())
        client2 = _run(_get_client())
        assert client1 is client2

    def test_creates_new_client_if_previous_closed(self, monkeypatch):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", Path("/nonexistent"))
        client1 = _run(_get_client())
        _run(client1.aclose())
        client2 = _run(_get_client())
        assert client1 is not client2
        assert not client2.is_closed


# ====================================================================
# close_client
# ====================================================================


class TestCloseClient:
    def setup_method(self):
        _reset_client()

    def teardown_method(self):
        _reset_client()

    def test_closes_and_nullifies_client(self, monkeypatch):
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        monkeypatch.setattr(discogs_mod, "_TOKEN_FILE", Path("/nonexistent"))
        _run(_get_client())
        assert discogs_mod._client is not None
        _run(close_client())
        assert discogs_mod._client is None

    def test_handles_none_client(self):
        discogs_mod._client = None
        _run(close_client())
        assert discogs_mod._client is None


# ====================================================================
# _api_get — rate limiting
# ====================================================================


class TestApiGetRateLimiting:
    """Test _api_get rate-limit logic by mocking _get_client and asyncio.sleep.

    We avoid patching time.monotonic because asyncio's event loop uses it
    internally and exhausting the mock's side_effect breaks the loop.
    Instead, set _last_request_time far in the past so no sleep is needed,
    or set it to time.monotonic() to force sleep.
    """

    def setup_method(self):
        _reset_client()
        _reset_rate_state()

    def teardown_method(self):
        _reset_rate_state()

    @patch("cuepoint.discogs._get_client")
    @patch("cuepoint.discogs.asyncio.sleep", new_callable=AsyncMock)
    def test_rate_remaining_low_triggers_sleep(self, mock_sleep, mock_get_client):
        """rate_remaining <= 5 -> min_interval = 2.0s, so sleep is called."""
        import time as _time

        discogs_mod._rate_remaining = 3
        discogs_mod._last_request_time = _time.monotonic()

        mock_response = _make_response(200, {"ok": True}, {"X-Discogs-Ratelimit-Remaining": "3"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = _run(discogs_mod._api_get("https://api.discogs.com/test"))
        assert result == {"ok": True}
        assert mock_sleep.call_count >= 1
        sleep_val = mock_sleep.call_args_list[0][0][0]
        assert sleep_val > 0

    @patch("cuepoint.discogs._get_client")
    @patch("cuepoint.discogs.asyncio.sleep", new_callable=AsyncMock)
    def test_no_sleep_when_enough_time_elapsed(self, mock_sleep, mock_get_client):
        """If enough time has passed, no rate-limit sleep needed."""
        discogs_mod._rate_remaining = 60
        discogs_mod._last_request_time = 0.0

        mock_response = _make_response(200, {"data": 1}, {"X-Discogs-Ratelimit-Remaining": "59"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = _run(discogs_mod._api_get("https://api.discogs.com/test"))
        assert result == {"data": 1}
        mock_sleep.assert_not_called()

    @patch("cuepoint.discogs._get_client")
    @patch("cuepoint.discogs.asyncio.sleep", new_callable=AsyncMock)
    def test_reads_ratelimit_remaining_header(self, mock_sleep, mock_get_client):
        """Verify _rate_remaining is updated from response header."""
        discogs_mod._rate_remaining = 60
        discogs_mod._last_request_time = 0.0

        mock_response = _make_response(200, {"ok": True}, {"X-Discogs-Ratelimit-Remaining": "42"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        _run(discogs_mod._api_get("https://api.discogs.com/test"))
        assert discogs_mod._rate_remaining == 42

    @patch("cuepoint.discogs._get_client")
    @patch("cuepoint.discogs.asyncio.sleep", new_callable=AsyncMock)
    def test_429_reads_retry_after_and_retries(self, mock_sleep, mock_get_client):
        """429 response -> sleep for Retry-After seconds, then retry."""
        discogs_mod._rate_remaining = 60
        discogs_mod._last_request_time = 0.0

        resp_429 = _make_response(429, {}, {"Retry-After": "5", "X-Discogs-Ratelimit-Remaining": "0"})
        resp_200 = _make_response(200, {"retried": True}, {"X-Discogs-Ratelimit-Remaining": "58"})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[resp_429, resp_200])
        mock_get_client.return_value = mock_client

        result = _run(discogs_mod._api_get("https://api.discogs.com/test"))
        assert result == {"retried": True}
        mock_sleep.assert_any_call(5)

    @patch("cuepoint.discogs._get_client")
    @patch("cuepoint.discogs.asyncio.sleep", new_callable=AsyncMock)
    def test_429_default_retry_after(self, mock_sleep, mock_get_client):
        """429 without Retry-After header defaults to 30s."""
        discogs_mod._rate_remaining = 60
        discogs_mod._last_request_time = 0.0

        resp_429 = _make_response(429, {}, {"X-Discogs-Ratelimit-Remaining": "0"})
        resp_200 = _make_response(200, {"ok": True}, {"X-Discogs-Ratelimit-Remaining": "58"})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[resp_429, resp_200])
        mock_get_client.return_value = mock_client

        result = _run(discogs_mod._api_get("https://api.discogs.com/test"))
        assert result == {"ok": True}
        mock_sleep.assert_any_call(30)

    @patch("cuepoint.discogs._get_client")
    @patch("cuepoint.discogs.asyncio.sleep", new_callable=AsyncMock)
    def test_successful_response_returns_json(self, mock_sleep, mock_get_client):
        """Normal 200 response returns parsed JSON."""
        discogs_mod._rate_remaining = 60
        discogs_mod._last_request_time = 0.0

        payload = {"artists": [{"id": 1, "name": "Test"}]}
        mock_response = _make_response(200, payload, {"X-Discogs-Ratelimit-Remaining": "59"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = _run(discogs_mod._api_get("https://api.discogs.com/artists/1"))
        assert result == payload


# ====================================================================
# populate_discogs_info — pagination and edge cases
# ====================================================================


class TestPopulateDiscogsInfoExtended:
    """Additional tests for multi-page pagination, master detail failures,
    label collection, and dc_ratio calculation."""

    @patch("cuepoint.discogs._api_get")
    def test_multi_page_release_pagination(self, mock_get):
        """Releases spanning multiple pages are all fetched."""
        page1 = {
            "releases": [
                {
                    "id": 1,
                    "type": "master",
                    "label": "Label-A",
                    "stats": {"community": {"in_collection": 10, "in_wantlist": 5}},
                },
            ],
            "pagination": {"pages": 2},
        }
        page2 = {
            "releases": [
                {
                    "id": 2,
                    "type": "master",
                    "label": "Label-B",
                    "stats": {"community": {"in_collection": 20, "in_wantlist": 15}},
                },
            ],
            "pagination": {"pages": 2},
        }
        master_1 = {"styles": ["Techno"]}
        master_2 = {"styles": ["House"]}

        mock_get.side_effect = [page1, page2, master_1, master_2]

        info = {"name": "Multi-Page", "discogs": "https://www.discogs.com/artist/555-Multi"}
        result = _run(populate_discogs_info(info))

        assert result["dc_have"] == 30
        assert result["dc_want"] == 20
        styles = json.loads(result["dc_styles"])
        assert "Techno" in styles
        assert "House" in styles
        labels = json.loads(result["dc_labels"])
        assert "Label-A" in labels
        assert "Label-B" in labels

    @patch("cuepoint.discogs._api_get")
    def test_master_detail_fetch_failure_continues(self, mock_get):
        """If a master detail fetch raises HTTPError, enrichment continues with other masters."""
        releases = {
            "releases": [
                {
                    "id": 10,
                    "type": "master",
                    "label": "Good-Label",
                    "stats": {"community": {"in_collection": 50, "in_wantlist": 25}},
                },
                {
                    "id": 11,
                    "type": "master",
                    "label": "Bad-Label",
                    "stats": {"community": {"in_collection": 30, "in_wantlist": 10}},
                },
            ],
            "pagination": {"pages": 1},
        }
        # Master 10 (top by haves=50) succeeds, master 11 (haves=30) fails
        master_10 = {"styles": ["Ambient"]}

        mock_get.side_effect = [
            releases,
            master_10,
            httpx.HTTPError("master detail failed"),
        ]

        info = {"name": "Partial", "discogs": "https://www.discogs.com/artist/777-Partial"}
        result = _run(populate_discogs_info(info))

        assert result["dc_have"] == 80
        assert result["dc_want"] == 35
        styles = json.loads(result["dc_styles"])
        assert "Ambient" in styles

    @patch("cuepoint.discogs._api_get")
    def test_labels_collected_across_all_releases(self, mock_get):
        """Labels from both master and non-master releases are collected."""
        releases = {
            "releases": [
                {
                    "id": 100,
                    "type": "master",
                    "label": "Master-Label",
                    "stats": {"community": {"in_collection": 10, "in_wantlist": 5}},
                },
                {
                    "id": 200,
                    "type": "release",
                    "label": "Release-Label",
                    "stats": {"community": {"in_collection": 5, "in_wantlist": 2}},
                },
                {
                    "id": 300,
                    "type": "release",
                    "label": None,
                    "stats": {"community": {"in_collection": 1, "in_wantlist": 0}},
                },
            ],
            "pagination": {"pages": 1},
        }
        master_100 = {"styles": ["Dub Techno"]}
        mock_get.side_effect = [releases, master_100]

        info = {"name": "Labels", "discogs": "https://www.discogs.com/artist/888-Labels"}
        result = _run(populate_discogs_info(info))

        labels = json.loads(result["dc_labels"])
        assert "Master-Label" in labels
        assert "Release-Label" in labels
        assert len(labels) == 2  # None label should not be included

    @patch("cuepoint.discogs._api_get")
    def test_dc_ratio_calculated_correctly(self, mock_get):
        """dc_ratio = round(wants / haves, 1) when haves > 0."""
        releases = {
            "releases": [
                {
                    "id": 1,
                    "type": "master",
                    "label": "L",
                    "stats": {"community": {"in_collection": 40, "in_wantlist": 28}},
                },
            ],
            "pagination": {"pages": 1},
        }
        master_1 = {"styles": ["Electro"]}
        mock_get.side_effect = [releases, master_1]

        info = {"name": "Ratio", "discogs": "https://www.discogs.com/artist/999-Ratio"}
        result = _run(populate_discogs_info(info))

        assert result["dc_have"] == 40
        assert result["dc_want"] == 28
        assert result["dc_ratio"] == round(28 / 40, 1)  # 0.7

    @patch("cuepoint.discogs._api_get")
    def test_dc_rating_always_zero(self, mock_get):
        """dc_rating is always set to 0 in current implementation."""
        releases = {
            "releases": [
                {
                    "id": 1,
                    "type": "master",
                    "label": "L",
                    "stats": {"community": {"in_collection": 10, "in_wantlist": 5}},
                },
            ],
            "pagination": {"pages": 1},
        }
        master_1 = {"styles": ["Techno"]}
        mock_get.side_effect = [releases, master_1]

        info = {"name": "Rating", "discogs": "https://www.discogs.com/artist/111-Rating"}
        result = _run(populate_discogs_info(info))
        assert result["dc_rating"] == 0

    @patch("cuepoint.discogs._api_get")
    def test_no_labels_key_when_labels_empty(self, mock_get):
        """When no releases have labels, dc_labels is not set."""
        releases = {
            "releases": [
                {
                    "id": 1,
                    "type": "master",
                    "label": None,
                    "stats": {"community": {"in_collection": 5, "in_wantlist": 3}},
                },
            ],
            "pagination": {"pages": 1},
        }
        master_1 = {"styles": ["Minimal"]}
        mock_get.side_effect = [releases, master_1]

        info = {"name": "No Labels", "discogs": "https://www.discogs.com/artist/222-NoLabel"}
        result = _run(populate_discogs_info(info))
        assert "dc_labels" not in result

    @patch("cuepoint.discogs._api_get")
    def test_styles_capped_at_four(self, mock_get):
        """Style collection stops once 4 or more styles are gathered."""
        releases = {
            "releases": [
                {
                    "id": i,
                    "type": "master",
                    "label": f"L{i}",
                    "stats": {"community": {"in_collection": 100 - i, "in_wantlist": 10}},
                }
                for i in range(1, 6)
            ],
            "pagination": {"pages": 1},
        }
        # Master with highest haves first (sorted by in_collection desc)
        master_1 = {"styles": ["Techno", "Industrial"]}
        master_2 = {"styles": ["EBM", "Dark Ambient"]}
        # Should stop after master_2 since we have 4 styles >= 4

        mock_get.side_effect = [releases, master_1, master_2]

        info = {"name": "Styles", "discogs": "https://www.discogs.com/artist/333-Styles"}
        result = _run(populate_discogs_info(info))

        styles = json.loads(result["dc_styles"])
        assert len(styles) >= 4

    @patch("cuepoint.discogs._api_get")
    def test_empty_discogs_url_returns_unchanged(self, mock_get):
        """Empty string discogs URL is treated as falsy, returns unchanged."""
        info = {"name": "No URL", "discogs": ""}
        result = _run(populate_discogs_info(info))
        assert "dc_have" not in result
        mock_get.assert_not_called()

    @patch("cuepoint.discogs._api_get")
    def test_masters_without_stats_default_to_zero(self, mock_get):
        """Masters missing stats still contribute 0 to haves/wants."""
        releases = {
            "releases": [
                {
                    "id": 1,
                    "type": "master",
                    "label": "L",
                    "stats": {},
                },
                {
                    "id": 2,
                    "type": "master",
                    "label": "L",
                    "stats": {"community": {"in_collection": 10, "in_wantlist": 7}},
                },
            ],
            "pagination": {"pages": 1},
        }
        master_1 = {"styles": ["Techno"]}
        master_2 = {"styles": ["House"]}
        mock_get.side_effect = [releases, master_2, master_1]

        info = {"name": "No Stats", "discogs": "https://www.discogs.com/artist/444-NoStats"}
        result = _run(populate_discogs_info(info))

        assert result["dc_have"] == 10
        assert result["dc_want"] == 7

    @patch("cuepoint.discogs._api_get")
    @patch("cuepoint.discogs.cfg.discogs_max_masters", return_value=1)
    def test_pagination_stops_at_max_masters(self, mock_cfg, mock_get):
        """Pagination stops once enough masters have been collected."""
        page1 = {
            "releases": [
                {
                    "id": 1,
                    "type": "master",
                    "label": "L1",
                    "stats": {"community": {"in_collection": 10, "in_wantlist": 5}},
                },
            ],
            "pagination": {"pages": 3},
        }
        master_1 = {"styles": ["Techno"]}
        mock_get.side_effect = [page1, master_1]

        info = {"name": "MaxMasters", "discogs": "https://www.discogs.com/artist/600-Max"}
        result = _run(populate_discogs_info(info))

        assert result["dc_have"] == 10
        # Only 2 _api_get calls: releases page 1 + master detail
        assert mock_get.call_count == 2

    @patch("cuepoint.discogs._api_get")
    def test_all_master_details_fail(self, mock_get):
        """If all master detail fetches fail, styles is empty but haves/wants still set."""
        releases = {
            "releases": [
                {
                    "id": 1,
                    "type": "master",
                    "label": "L",
                    "stats": {"community": {"in_collection": 20, "in_wantlist": 10}},
                },
            ],
            "pagination": {"pages": 1},
        }
        mock_get.side_effect = [releases, httpx.HTTPError("fail")]

        info = {"name": "AllFail", "discogs": "https://www.discogs.com/artist/700-Fail"}
        result = _run(populate_discogs_info(info))

        assert result["dc_have"] == 20
        assert result["dc_want"] == 10
        styles = json.loads(result["dc_styles"])
        assert styles == []
