"""Tests for sc.py — auth flow, circuit breaker, credentials loading."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from cuepoint.sc import _load_credentials, is_oauth, reset_circuit_breaker


class TestLoadCredentials:
    def test_from_env_vars(self, monkeypatch):
        monkeypatch.setenv("SC_CLIENT_ID", "test_id")
        monkeypatch.setenv("SC_CLIENT_SECRET", "test_secret")
        with patch("cuepoint.sc._CREDS_FILE") as mock_path:
            mock_path.exists.return_value = False
            cid, secret = _load_credentials()
        assert cid == "test_id"
        assert secret == "test_secret"

    def test_from_file(self, tmp_path, monkeypatch):
        creds_file = tmp_path / ".sc_credentials"
        creds_file.write_text("file_id\nfile_secret\n", encoding="utf-8")
        with patch("cuepoint.sc._CREDS_FILE", creds_file):
            monkeypatch.delenv("SC_CLIENT_ID", raising=False)
            monkeypatch.delenv("SC_CLIENT_SECRET", raising=False)
            cid, secret = _load_credentials()
        assert cid == "file_id"
        assert secret == "file_secret"

    def test_no_credentials(self, monkeypatch):
        monkeypatch.delenv("SC_CLIENT_ID", raising=False)
        monkeypatch.delenv("SC_CLIENT_SECRET", raising=False)
        with patch("cuepoint.sc._CREDS_FILE") as mock_path:
            mock_path.exists.return_value = False
            cid, secret = _load_credentials()
        assert cid is None
        assert secret is None

    def test_file_takes_precedence(self, tmp_path, monkeypatch):
        creds_file = tmp_path / ".sc_credentials"
        creds_file.write_text("file_id\nfile_secret\n", encoding="utf-8")
        monkeypatch.setenv("SC_CLIENT_ID", "env_id")
        monkeypatch.setenv("SC_CLIENT_SECRET", "env_secret")
        with patch("cuepoint.sc._CREDS_FILE", creds_file):
            cid, _secret = _load_credentials()
        assert cid == "file_id"


class TestResetCircuitBreaker:
    def test_resets_state(self):
        import cuepoint.sc as sc_mod

        sc_mod._breaker._total_403s = 99
        sc_mod._breaker._total_requests = 500
        sc_mod._breaker._is_open = True
        sc_mod._limiter._consecutive_fails = 10

        asyncio.run(reset_circuit_breaker())

        assert sc_mod._breaker._total_403s == 0
        assert sc_mod._breaker._total_requests == 0
        assert sc_mod._breaker._is_open is False
        assert sc_mod._limiter._consecutive_fails == 0


class TestIsOauth:
    def test_false_by_default(self):
        import cuepoint.sc as sc_mod

        sc_mod._use_oauth = False
        assert is_oauth() is False

    def test_true_when_set(self):
        import cuepoint.sc as sc_mod

        sc_mod._use_oauth = True
        assert is_oauth() is True

    def test_none_means_false(self):
        import cuepoint.sc as sc_mod

        sc_mod._use_oauth = None
        assert is_oauth() is False
