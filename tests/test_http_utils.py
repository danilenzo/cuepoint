"""Tests for http_utils.retry_on_failure decorator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from cuepoint.http_utils import _calc_delay, retry_on_failure

# ---------------------------------------------------------------------------
# _calc_delay
# ---------------------------------------------------------------------------


class TestCalcDelay:
    def test_exponential_backoff(self):
        """Without Retry-After, delay doubles each attempt (plus jitter < 1)."""
        d0 = _calc_delay(None, 0, base_delay=1.0, max_delay=30.0)
        assert 1.0 <= d0 < 2.0  # 1*2^0 + jitter

        d1 = _calc_delay(None, 1, base_delay=1.0, max_delay=30.0)
        assert 2.0 <= d1 < 3.0  # 1*2^1 + jitter

    def test_respects_max_delay(self):
        d = _calc_delay(None, 10, base_delay=1.0, max_delay=5.0)
        assert d <= 5.0

    def test_retry_after_header(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 429
        resp.headers = {"Retry-After": "7"}
        d = _calc_delay(resp, 0, base_delay=1.0, max_delay=30.0)
        assert d == 7.0

    def test_retry_after_capped_by_max_delay(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 429
        resp.headers = {"Retry-After": "120"}
        d = _calc_delay(resp, 0, base_delay=1.0, max_delay=30.0)
        assert d == 30.0

    def test_retry_after_non_429_ignored(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 503
        resp.headers = {"Retry-After": "5"}
        d = _calc_delay(resp, 0, base_delay=1.0, max_delay=30.0)
        # Should use exponential, not Retry-After
        assert 1.0 <= d < 2.0


# ---------------------------------------------------------------------------
# retry_on_failure decorator
# ---------------------------------------------------------------------------


class TestRetryOnFailure:
    @patch("cuepoint.http_utils.time.sleep")
    def test_no_retry_on_success(self, mock_sleep):
        @retry_on_failure(max_retries=3)
        def ok():
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200
            return resp

        result = ok()
        assert result.status_code == 200
        mock_sleep.assert_not_called()

    @patch("cuepoint.http_utils.time.sleep")
    def test_retries_on_429_then_succeeds(self, mock_sleep):
        call_count = [0]

        @retry_on_failure(max_retries=2, base_delay=0.01)
        def flaky():
            call_count[0] += 1
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200 if call_count[0] > 1 else 429
            resp.headers = {}
            return resp

        result = flaky()
        assert result.status_code == 200
        assert call_count[0] == 2
        assert mock_sleep.call_count == 1

    @patch("cuepoint.http_utils.time.sleep")
    def test_retries_exhausted_returns_last_response(self, mock_sleep):
        @retry_on_failure(max_retries=2, base_delay=0.01)
        def always_503():
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 503
            resp.headers = {}
            return resp

        result = always_503()
        assert result.status_code == 503  # returns last bad response
        assert mock_sleep.call_count == 2

    @patch("cuepoint.http_utils.time.sleep")
    def test_retries_on_connection_error(self, mock_sleep):
        call_count = [0]

        @retry_on_failure(max_retries=2, base_delay=0.01)
        def conn_err():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise requests.ConnectionError("Network down")
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200
            return resp

        result = conn_err()
        assert result.status_code == 200
        assert call_count[0] == 3

    @patch("cuepoint.http_utils.time.sleep")
    def test_non_retryable_exception_raises_immediately(self, mock_sleep):
        @retry_on_failure(max_retries=3, base_delay=0.01)
        def bad():
            raise ValueError("Not retryable")

        with pytest.raises(ValueError, match="Not retryable"):
            bad()
        mock_sleep.assert_not_called()

    @patch("cuepoint.http_utils.time.sleep")
    def test_non_retryable_status_returns_immediately(self, mock_sleep):
        @retry_on_failure(max_retries=3, base_delay=0.01)
        def not_found():
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 404
            return resp

        result = not_found()
        assert result.status_code == 404
        mock_sleep.assert_not_called()

    @patch("cuepoint.http_utils.time.sleep")
    def test_retries_on_http_error_with_retryable_status(self, mock_sleep):
        call_count = [0]

        @retry_on_failure(max_retries=2, base_delay=0.01)
        def raises_http():
            call_count[0] += 1
            if call_count[0] <= 1:
                resp = MagicMock(spec=requests.Response)
                resp.status_code = 502
                resp.headers = {}
                raise requests.HTTPError(response=resp)
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200
            return resp

        result = raises_http()
        assert result.status_code == 200
        assert call_count[0] == 2
