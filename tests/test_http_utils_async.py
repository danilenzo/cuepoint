"""Tests for http_utils async retry decorator and async delay calculation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from cuepoint.http_utils import _calc_delay, async_retry_on_failure

_run = asyncio.run


class TestCalcDelayAsync:
    def test_exponential_backoff(self):
        d0 = _calc_delay(None, 0, base_delay=1.0, max_delay=30.0)
        assert 1.0 <= d0 < 2.0

        d1 = _calc_delay(None, 1, base_delay=1.0, max_delay=30.0)
        assert 2.0 <= d1 < 3.0

    def test_respects_max_delay(self):
        d = _calc_delay(None, 10, base_delay=1.0, max_delay=5.0)
        assert d <= 5.0

    def test_retry_after_header_429(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        resp.headers = {"Retry-After": "7"}
        d = _calc_delay(resp, 0, base_delay=1.0, max_delay=30.0)
        assert d == 7.0

    def test_retry_after_capped(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        resp.headers = {"Retry-After": "120"}
        d = _calc_delay(resp, 0, base_delay=1.0, max_delay=30.0)
        assert d == 30.0

    def test_retry_after_invalid_falls_back(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        resp.headers = {"Retry-After": "not-a-number"}
        d = _calc_delay(resp, 0, base_delay=1.0, max_delay=30.0)
        assert 1.0 <= d < 2.0

    def test_non_429_ignores_retry_after(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 503
        resp.headers = {"Retry-After": "5"}
        d = _calc_delay(resp, 0, base_delay=1.0, max_delay=30.0)
        assert 1.0 <= d < 2.0


class TestAsyncRetryOnFailure:
    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_no_retry_on_success(self, mock_sleep):
        @async_retry_on_failure(max_retries=3)
        async def ok():
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            return resp

        result = _run(ok())
        assert result.status_code == 200
        mock_sleep.assert_not_awaited()

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_retries_on_retryable_status(self, mock_sleep):
        call_count = [0]

        @async_retry_on_failure(max_retries=2, base_delay=0.01)
        async def flaky():
            call_count[0] += 1
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200 if call_count[0] > 1 else 429
            resp.headers = {}
            return resp

        result = _run(flaky())
        assert result.status_code == 200
        assert call_count[0] == 2

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_retries_on_http_status_error(self, mock_sleep):
        call_count = [0]

        @async_retry_on_failure(max_retries=2, base_delay=0.01)
        async def err():
            call_count[0] += 1
            if call_count[0] <= 1:
                resp = MagicMock(spec=httpx.Response)
                resp.status_code = 502
                resp.headers = {}
                req = MagicMock(spec=httpx.Request)
                raise httpx.HTTPStatusError("bad", request=req, response=resp)
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            return resp

        result = _run(err())
        assert result.status_code == 200

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_retries_on_connect_error(self, mock_sleep):
        call_count = [0]

        @async_retry_on_failure(max_retries=2, base_delay=0.01)
        async def conn_err():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise httpx.ConnectError("down")
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            return resp

        result = _run(conn_err())
        assert result.status_code == 200
        assert call_count[0] == 3

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_non_retryable_raises_immediately(self, mock_sleep):
        @async_retry_on_failure(max_retries=3)
        async def bad():
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            _run(bad())
        mock_sleep.assert_not_awaited()

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_non_retryable_status_error_raises(self, mock_sleep):
        @async_retry_on_failure(max_retries=3)
        async def not_found():
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 404
            resp.headers = {}
            req = MagicMock(spec=httpx.Request)
            raise httpx.HTTPStatusError("not found", request=req, response=resp)

        with pytest.raises(httpx.HTTPStatusError):
            _run(not_found())
        mock_sleep.assert_not_awaited()

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_exhausted_retries_raises(self, mock_sleep):
        @async_retry_on_failure(max_retries=1, base_delay=0.01)
        async def always_fail():
            raise httpx.ConnectError("down")

        with pytest.raises(httpx.ConnectError):
            _run(always_fail())
