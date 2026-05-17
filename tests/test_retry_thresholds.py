"""Tests for retry/circuit-breaker thresholds — edge cases not covered by test_http_utils."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import requests

from cuepoint.http_utils import async_retry_on_failure, retry_on_failure

_run = asyncio.run


class TestSyncRetryThresholds:
    @patch("cuepoint.http_utils.time.sleep")
    def test_zero_retries_no_retry(self, mock_sleep):
        call_count = [0]

        @retry_on_failure(max_retries=0, base_delay=0.01)
        def always_fail():
            call_count[0] += 1
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 503
            resp.headers = {}
            return resp

        result = always_fail()
        assert result.status_code == 503
        assert call_count[0] == 1
        mock_sleep.assert_not_called()

    @patch("cuepoint.http_utils.time.sleep")
    def test_custom_retryable_statuses(self, mock_sleep):
        call_count = [0]

        @retry_on_failure(max_retries=2, base_delay=0.01, retryable_statuses=(418,))
        def teapot():
            call_count[0] += 1
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 418
            resp.headers = {}
            return resp

        result = teapot()
        assert result.status_code == 418
        assert call_count[0] == 3  # 1 original + 2 retries

    @patch("cuepoint.http_utils.time.sleep")
    def test_503_not_retried_when_excluded(self, mock_sleep):
        @retry_on_failure(max_retries=3, base_delay=0.01, retryable_statuses=(429,))
        def server_error():
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 503
            resp.headers = {}
            return resp

        result = server_error()
        assert result.status_code == 503
        mock_sleep.assert_not_called()

    @patch("cuepoint.http_utils.time.sleep")
    def test_timeout_exception_retried(self, mock_sleep):
        call_count = [0]

        @retry_on_failure(max_retries=1, base_delay=0.01)
        def timeout():
            call_count[0] += 1
            if call_count[0] <= 1:
                raise requests.Timeout("timed out")
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200
            return resp

        result = timeout()
        assert result.status_code == 200
        assert call_count[0] == 2

    @patch("cuepoint.http_utils.time.sleep")
    def test_connection_error_exhausted_raises(self, mock_sleep):
        @retry_on_failure(max_retries=2, base_delay=0.01)
        def always_down():
            raise requests.ConnectionError("refused")

        with pytest.raises(requests.ConnectionError, match="refused"):
            always_down()
        assert mock_sleep.call_count == 2

    @patch("cuepoint.http_utils.time.sleep")
    def test_backoff_increases_between_retries(self, mock_sleep):
        @retry_on_failure(max_retries=3, base_delay=1.0)
        def fails():
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 500
            resp.headers = {}
            return resp

        fails()
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert len(delays) == 3
        # Last delay should be significantly larger than first (jitter adds noise)
        assert delays[-1] > delays[0]


class TestAsyncRetryThresholds:
    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_zero_retries_no_retry(self, mock_sleep):
        call_count = [0]

        @async_retry_on_failure(max_retries=0)
        async def always_fail():
            call_count[0] += 1
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 503
            resp.headers = {}
            return resp

        result = _run(always_fail())
        assert result.status_code == 503
        assert call_count[0] == 1
        mock_sleep.assert_not_awaited()

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_custom_retryable_statuses(self, mock_sleep):
        call_count = [0]

        @async_retry_on_failure(max_retries=2, base_delay=0.01, retryable_statuses=(418,))
        async def teapot():
            call_count[0] += 1
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 418
            resp.headers = {}
            return resp

        result = _run(teapot())
        assert result.status_code == 418
        assert call_count[0] == 3

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_timeout_exception_retried(self, mock_sleep):
        call_count = [0]

        @async_retry_on_failure(max_retries=1, base_delay=0.01)
        async def timeout():
            call_count[0] += 1
            if call_count[0] <= 1:
                raise httpx.TimeoutException("timed out")
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            return resp

        result = _run(timeout())
        assert result.status_code == 200
        assert call_count[0] == 2

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_pool_timeout_retried(self, mock_sleep):
        call_count = [0]

        @async_retry_on_failure(max_retries=1, base_delay=0.01)
        async def pool():
            call_count[0] += 1
            if call_count[0] <= 1:
                raise httpx.PoolTimeout("pool full")
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            return resp

        result = _run(pool())
        assert result.status_code == 200
        assert call_count[0] == 2

    @patch("cuepoint.http_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_backoff_increases_between_retries(self, mock_sleep):
        @async_retry_on_failure(max_retries=3, base_delay=1.0)
        async def fails():
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 500
            resp.headers = {}
            return resp

        result = _run(fails())
        assert result.status_code == 500
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert len(delays) == 3
        # Last delay should be significantly larger than first (jitter adds noise)
        assert delays[-1] > delays[0]
