"""Tests for sc.py — SoundCloud enrichment (mocked API calls)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import httpx

from cuepoint.fuzzy_match import _normalize_alnum
from cuepoint.sc import _is_valid_sc_url, populate_sc_info, search_sc_by_name

_run = asyncio.run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_strips_non_alphanumeric(self):
        assert _normalize_alnum("DJ Test-Name") == "djtestname"

    def test_lowercases(self):
        assert _normalize_alnum("TECHNO") == "techno"

    def test_empty(self):
        assert _normalize_alnum("") == ""


class TestIsValidScUrl:
    def test_valid(self):
        assert _is_valid_sc_url("https://soundcloud.com/some-artist")

    def test_valid_trailing_slash(self):
        assert _is_valid_sc_url("https://soundcloud.com/some-artist/")

    def test_invalid_track_url(self):
        assert not _is_valid_sc_url("https://soundcloud.com/some-artist/some-track")

    def test_invalid_empty(self):
        assert not _is_valid_sc_url("")

    def test_invalid_non_sc(self):
        assert not _is_valid_sc_url("https://example.com/artist")


# ---------------------------------------------------------------------------
# search_sc_by_name
# ---------------------------------------------------------------------------


class TestSearchScByName:
    @patch("cuepoint.sc._api_get")
    def test_exact_username_match(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "collection": [
                {
                    "username": "Test Artist",
                    "permalink": "test-artist",
                    "full_name": "",
                    "permalink_url": "https://soundcloud.com/test-artist",
                },
                {
                    "username": "Other",
                    "permalink": "other",
                    "full_name": "",
                    "permalink_url": "https://soundcloud.com/other",
                },
            ]
        }
        mock_get.return_value = mock_resp
        assert _run(search_sc_by_name("Test Artist")) == "https://soundcloud.com/test-artist"

    @patch("cuepoint.sc._api_get")
    def test_permalink_match(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "collection": [
                {
                    "username": "SomeOtherName",
                    "permalink": "dvs1",
                    "full_name": "",
                    "permalink_url": "https://soundcloud.com/dvs1",
                },
            ]
        }
        mock_get.return_value = mock_resp
        assert _run(search_sc_by_name("DVS1")) == "https://soundcloud.com/dvs1"

    @patch("cuepoint.sc._api_get")
    def test_no_match_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"collection": []}
        mock_get.return_value = mock_resp
        assert _run(search_sc_by_name("Nonexistent")) is None

    @patch("cuepoint.sc._api_get")
    def test_api_failure_returns_none(self, mock_get):
        mock_get.side_effect = httpx.HTTPError("Connection failed")
        assert _run(search_sc_by_name("Test")) is None


# ---------------------------------------------------------------------------
# populate_sc_info
# ---------------------------------------------------------------------------


class TestPopulateScInfo:
    def test_no_sc_url_returns_unchanged(self):
        info = {"name": "Test", "soundcloud": None}
        result = _run(populate_sc_info(info))
        assert result is info

    def test_invalid_sc_url_sets_defaults(self):
        info = {"name": "Test", "soundcloud": "https://soundcloud.com/artist/track"}
        result = _run(populate_sc_info(info))
        assert result["sc_tags"] == json.dumps([])
        assert result["sc_followers"] is None

    @patch("cuepoint.sc._api_get")
    def test_successful_enrichment(self, mock_get):
        user_resp = MagicMock()
        user_resp.json.return_value = {
            "id": 123,
            "followers_count": 50000,
            "followings_count": 200,
        }
        tracks_resp = MagicMock()
        tracks_resp.json.return_value = {
            "collection": [
                {"genre": '"Techno"', "tag_list": '"Dark Techno" industrial'},
                {"genre": '"Minimal"', "tag_list": ""},
            ]
        }
        mock_get.side_effect = [user_resp, tracks_resp]

        info = {"name": "Test DJ", "soundcloud": "https://soundcloud.com/test-dj"}
        result = _run(populate_sc_info(info))

        assert result["sc_followers"] == 50000
        assert result["sc_following"] == 200
        tags = json.loads(result["sc_tags"])
        assert "Techno" in tags
        assert "Dark Techno" in tags

    @patch("cuepoint.sc._api_get")
    def test_api_error_sets_defaults(self, mock_get):
        mock_get.side_effect = httpx.HTTPError("API down")
        info = {"name": "Test", "soundcloud": "https://soundcloud.com/test"}
        result = _run(populate_sc_info(info))
        assert result["sc_followers"] is None
        assert json.loads(result["sc_tags"]) == []

    def test_www_url_normalized(self):
        """The www. prefix should be stripped."""
        info = {"name": "Test", "soundcloud": "https://www.soundcloud.com/test"}
        with patch("cuepoint.sc._api_get") as mock_get:
            user_resp = MagicMock()
            user_resp.json.return_value = {"id": 1, "followers_count": 10, "followings_count": 5}
            tracks_resp = MagicMock()
            tracks_resp.json.return_value = {"collection": []}
            mock_get.side_effect = [user_resp, tracks_resp]

            result = _run(populate_sc_info(info))
            assert result["sc_followers"] == 10


# ---------------------------------------------------------------------------
# _get_client / close_client
# ---------------------------------------------------------------------------

import cuepoint.sc as sc_mod
from cuepoint.sc import (
    SCCircuitOpen,
    _get_client,
    _scrape_client_id,
    close_client,
)


class TestGetClientAndClose:
    def test_get_client_returns_async_client(self):
        sc_mod._client = None
        client = _run(_get_client())
        assert client is not None
        assert not client.is_closed
        _run(close_client())

    def test_get_client_returns_same_instance(self):
        sc_mod._client = None
        c1 = _run(_get_client())
        c2 = _run(_get_client())
        assert c1 is c2
        _run(close_client())

    def test_close_client_sets_none(self):
        sc_mod._client = None
        _run(_get_client())
        assert sc_mod._client is not None
        _run(close_client())
        assert sc_mod._client is None

    def test_get_client_recreates_after_close(self):
        sc_mod._client = None
        c1 = _run(_get_client())
        _run(close_client())
        c2 = _run(_get_client())
        assert c1 is not c2
        _run(close_client())


# ---------------------------------------------------------------------------
# _scrape_client_id
# ---------------------------------------------------------------------------


class TestScrapeClientId:
    @patch("cuepoint.sc._get_client")
    def test_successful_extraction(self, mock_get_client):
        mock_client = MagicMock()

        # Simulate the SoundCloud homepage returning JS URLs
        homepage_resp = MagicMock()
        homepage_resp.text = (
            '<script src="https://a-v2.sndcdn.com/bundle-abc123.js"></script>'
            '<script src="https://a-v2.sndcdn.com/bundle-def456.js"></script>'
        )
        homepage_resp.raise_for_status = MagicMock()

        # Simulate the JS file containing a client_id
        js_resp = MagicMock()
        js_resp.text = 'var config={client_id:"abc12345678901234567890123456789"}'

        async def fake_get(url, **kwargs):
            if url == "https://soundcloud.com":
                return homepage_resp
            return js_resp

        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        result = _run(_scrape_client_id())
        assert result == "abc12345678901234567890123456789"

    @patch("cuepoint.sc._get_client")
    def test_no_sndcdn_urls_raises(self, mock_get_client):
        mock_client = MagicMock()

        homepage_resp = MagicMock()
        homepage_resp.text = '<script src="https://example.com/some.js"></script>'
        homepage_resp.raise_for_status = MagicMock()

        async def fake_get(url, **kwargs):
            return homepage_resp

        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        import pytest

        with pytest.raises(RuntimeError, match="Could not load SoundCloud JS bundles"):
            _run(_scrape_client_id())

    @patch("cuepoint.sc._get_client")
    def test_no_client_id_in_js_raises(self, mock_get_client):
        mock_client = MagicMock()

        homepage_resp = MagicMock()
        homepage_resp.text = '<script src="https://a-v2.sndcdn.com/bundle-abc123.js"></script>'
        homepage_resp.raise_for_status = MagicMock()

        js_resp = MagicMock()
        js_resp.text = "var x = 42; // no client_id here"

        async def fake_get(url, **kwargs):
            if url == "https://soundcloud.com":
                return homepage_resp
            return js_resp

        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        import pytest

        with pytest.raises(RuntimeError, match="Could not extract client_id"):
            _run(_scrape_client_id())


# ---------------------------------------------------------------------------
# _ensure_auth
# ---------------------------------------------------------------------------

from cuepoint.sc import _ensure_auth


class TestEnsureAuth:
    def _reset_auth_state(self):
        sc_mod._use_oauth = None
        sc_mod._access_token = None
        sc_mod._token_expires_at = 0.0
        sc_mod._client_id = None

    @patch("cuepoint.sc._scrape_client_id")
    @patch("cuepoint.sc._obtain_token")
    @patch("cuepoint.sc._load_credentials")
    def test_with_credentials_uses_oauth(self, mock_creds, mock_token, mock_scrape):
        self._reset_auth_state()
        mock_creds.return_value = ("my_id", "my_secret")
        mock_token.return_value = ("tok123", time.monotonic() + 3600)

        result = _run(_ensure_auth())
        assert result == "oauth"
        assert sc_mod._use_oauth is True
        mock_scrape.assert_not_called()

    @patch("cuepoint.sc._scrape_client_id")
    @patch("cuepoint.sc._obtain_token")
    @patch("cuepoint.sc._load_credentials")
    def test_oauth_failure_falls_back_to_client_id(self, mock_creds, mock_token, mock_scrape):
        self._reset_auth_state()
        mock_creds.return_value = ("my_id", "my_secret")
        mock_token.side_effect = httpx.HTTPError("OAuth failed")
        mock_scrape.return_value = "scraped_client_id_32charslong00"

        result = _run(_ensure_auth())
        assert result == "client_id"
        assert sc_mod._use_oauth is False
        assert sc_mod._client_id == "scraped_client_id_32charslong00"

    @patch("cuepoint.sc._scrape_client_id")
    @patch("cuepoint.sc._load_credentials")
    def test_no_credentials_uses_client_id(self, mock_creds, mock_scrape):
        self._reset_auth_state()
        mock_creds.return_value = (None, None)
        mock_scrape.return_value = "scraped_client_id_32charslong00"

        result = _run(_ensure_auth())
        assert result == "client_id"
        assert sc_mod._use_oauth is False


# ---------------------------------------------------------------------------
# _api_get circuit breaker
# ---------------------------------------------------------------------------

import time

import pytest

from cuepoint.sc import _api_get


class TestApiGetCircuitBreaker:
    def _reset_circuit_state(self):
        sc_mod._use_oauth = False
        sc_mod._client_id = "test_client_id"
        sc_mod._last_client_id_refresh = 0.0
        _run(sc_mod._breaker.reset())
        _run(sc_mod._limiter.reset())

    @patch("cuepoint.sc._ensure_auth")
    @patch("cuepoint.sc._get_client")
    @patch("cuepoint.sc._refresh_token_if_needed")
    def test_oauth_mode_adds_auth_header(self, mock_refresh, mock_get_client, mock_ensure):
        """OAuth mode should add Authorization header."""
        sc_mod._use_oauth = True
        sc_mod._access_token = "my_token"
        mock_ensure.return_value = "oauth"

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()

        async def fake_get(url, **kwargs):
            assert kwargs.get("headers", {}).get("Authorization") == "OAuth my_token"
            return mock_resp

        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        result = _run(_api_get("https://api.soundcloud.com/users"))
        assert result is mock_resp

    @patch("cuepoint.sc._ensure_auth")
    @patch("cuepoint.sc._get_client")
    @patch("cuepoint.sc._refresh_token_if_needed")
    def test_oauth_401_refreshes_token(self, mock_refresh, mock_get_client, mock_ensure):
        """On 401, should refresh token and retry."""
        sc_mod._use_oauth = True
        sc_mod._access_token = "expired_token"
        mock_ensure.return_value = "oauth"

        mock_401 = MagicMock(spec=httpx.Response)
        mock_401.status_code = 401

        mock_200 = MagicMock(spec=httpx.Response)
        mock_200.status_code = 200
        mock_200.raise_for_status = MagicMock()

        call_count = 0

        async def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_401
            return mock_200

        mock_client = MagicMock()
        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        result = _run(_api_get("https://api.soundcloud.com/users"))
        assert result is mock_200
        # _refresh_token_if_needed called at least twice (once before, once on 401)
        assert mock_refresh.call_count >= 2

    @patch("cuepoint.sc.asyncio.sleep", return_value=None)
    @patch("cuepoint.sc._ensure_auth")
    @patch("cuepoint.sc._get_client")
    def test_client_id_mode_adds_param(self, mock_get_client, mock_ensure, mock_sleep):
        """client_id mode should add client_id query param."""
        self._reset_circuit_state()
        mock_ensure.return_value = "client_id"

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        captured_params = {}

        async def fake_get(url, **kwargs):
            captured_params.update(kwargs.get("params", {}))
            return mock_resp

        mock_client = MagicMock()
        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        _run(_api_get("https://api.soundcloud.com/users", params={"q": "test"}))
        assert captured_params.get("client_id") == "test_client_id"
        assert captured_params.get("q") == "test"

    @patch("cuepoint.sc.random.uniform", return_value=0.0)
    @patch("cuepoint.sc.asyncio.sleep", return_value=None)
    @patch("cuepoint.sc._scrape_client_id")
    @patch("cuepoint.sc._ensure_auth")
    @patch("cuepoint.sc._get_client")
    def test_403_increments_counters(
        self,
        mock_get_client,
        mock_ensure,
        mock_scrape,
        mock_sleep,
        mock_random,
    ):
        """A 403 response should increment 403 counters and apply backoff."""
        self._reset_circuit_state()
        mock_ensure.return_value = "client_id"
        mock_scrape.return_value = "new_client_id_32chars_long00000"

        call_count = 0

        async def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock(spec=httpx.Response)
            if call_count == 1:
                resp.status_code = 403
                resp.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=resp)
                )
            else:
                # Retry after 403 returns 200
                resp.status_code = 200
                resp.raise_for_status = MagicMock()
            return resp

        mock_client = MagicMock()
        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        _run(_api_get("https://api.soundcloud.com/users"))
        assert sc_mod._breaker._total_403s >= 1

    @patch("cuepoint.sc.random.uniform", return_value=0.0)
    @patch("cuepoint.sc.asyncio.sleep", return_value=None)
    @patch("cuepoint.sc._scrape_client_id")
    @patch("cuepoint.sc._ensure_auth")
    @patch("cuepoint.sc._get_client")
    def test_circuit_breaker_trips(
        self,
        mock_get_client,
        mock_ensure,
        mock_scrape,
        mock_sleep,
        mock_random,
    ):
        """Circuit breaker should trip when ratio >= 0.6 and requests >= 8."""
        self._reset_circuit_state()
        mock_ensure.return_value = "client_id"
        mock_scrape.return_value = "refreshed_client_id_00000000000"

        # Pre-seed counters: 7 requests already done, 4 were 403s
        # Next request will be #8 total. If it 403s, that's 5/8 = 0.625 >= 0.6
        sc_mod._breaker._total_requests = 7
        sc_mod._breaker._total_403s = 4

        mock_403 = MagicMock(spec=httpx.Response)
        mock_403.status_code = 403
        mock_403.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=mock_403)
        )

        async def fake_get(url, **kwargs):
            return mock_403

        mock_client = MagicMock()
        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        with pytest.raises(SCCircuitOpen):
            _run(_api_get("https://api.soundcloud.com/users"))

        assert sc_mod._breaker._is_open is True

    @patch("cuepoint.sc.asyncio.sleep", return_value=None)
    @patch("cuepoint.sc._ensure_auth")
    @patch("cuepoint.sc._get_client")
    def test_circuit_open_raises_immediately(self, mock_get_client, mock_ensure, mock_sleep):
        """When circuit is already open, should raise immediately."""
        self._reset_circuit_state()
        sc_mod._breaker._is_open = True
        mock_ensure.return_value = "client_id"

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        with pytest.raises(SCCircuitOpen):
            _run(_api_get("https://api.soundcloud.com/users"))

    @patch("cuepoint.sc.random.uniform", return_value=0.0)
    @patch("cuepoint.sc.asyncio.sleep", return_value=None)
    @patch("cuepoint.sc._scrape_client_id")
    @patch("cuepoint.sc._ensure_auth")
    @patch("cuepoint.sc._get_client")
    def test_client_id_refresh_on_403_with_cooldown(
        self,
        mock_get_client,
        mock_ensure,
        mock_scrape,
        mock_sleep,
        mock_random,
    ):
        """On 403, client_id should be refreshed if cooldown has elapsed."""
        self._reset_circuit_state()
        sc_mod._last_client_id_refresh = 0.0  # long ago
        mock_ensure.return_value = "client_id"
        mock_scrape.return_value = "new_scraped_client_id_0000000000"

        call_count = 0

        async def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock(spec=httpx.Response)
            if call_count == 1:
                resp.status_code = 403
                resp.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=resp)
                )
            else:
                resp.status_code = 200
                resp.raise_for_status = MagicMock()
            return resp

        mock_client = MagicMock()
        mock_client.get = fake_get
        mock_get_client.return_value = mock_client

        _run(_api_get("https://api.soundcloud.com/users"))
        mock_scrape.assert_called_once()


# ---------------------------------------------------------------------------
# populate_sc_info with SCCircuitOpen
# ---------------------------------------------------------------------------


class TestPopulateScInfoCircuitOpen:
    @patch("cuepoint.sc._api_get")
    def test_circuit_open_sets_defaults(self, mock_get):
        """When circuit is open, populate_sc_info should catch SCCircuitOpen and set defaults."""
        mock_get.side_effect = SCCircuitOpen("circuit open")
        info = {"name": "Test DJ", "soundcloud": "https://soundcloud.com/test-dj"}
        result = _run(populate_sc_info(info))
        assert result["sc_followers"] is None
        assert result["sc_following"] is None
        assert json.loads(result["sc_tags"]) == []

    @patch("cuepoint.sc._api_get")
    def test_circuit_open_preserves_existing_values(self, mock_get):
        """If sc_tags/sc_followers were already set, SCCircuitOpen should not overwrite them."""
        mock_get.side_effect = SCCircuitOpen("circuit open")
        info = {
            "name": "Test DJ",
            "soundcloud": "https://soundcloud.com/test-dj",
            "sc_followers": 1000,
            "sc_tags": json.dumps(["Techno"]),
            "sc_following": 50,
        }
        result = _run(populate_sc_info(info))
        # setdefault won't overwrite existing values
        assert result["sc_followers"] == 1000
        assert json.loads(result["sc_tags"]) == ["Techno"]
        assert result["sc_following"] == 50
