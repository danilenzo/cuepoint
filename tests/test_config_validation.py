"""Tests for config.py — validation, reload, edge cases."""

from __future__ import annotations

from unittest.mock import patch

from cuepoint import config as cfg


class TestConfigValidation:
    def test_valid_config_no_warnings(self, monkeypatch):
        monkeypatch.setattr(
            cfg,
            "_cfg",
            {
                "general": {"days_ahead": 7, "ra_request_delay": 0.1, "max_workers": 3},
                "cache": {"ttl_days": 30, "ttl_following_days": 7},
                "scoring": {"sc_weight": 10, "dc_weight": 5, "bc_weight": 8},
                "discovery": {"similarity_threshold": 0.5},
            },
        )
        with patch("cuepoint.config.logger") as mock_logger:
            cfg._validate()
            mock_logger.warning.assert_not_called()

    def test_invalid_days_ahead(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"general": {"days_ahead": 0}})
        with patch("cuepoint.config.logger") as mock_logger:
            cfg._validate()
            warnings = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("days_ahead" in w for w in warnings)

    def test_negative_weight(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"scoring": {"sc_weight": -1}})
        with patch("cuepoint.config.logger") as mock_logger:
            cfg._validate()
            warnings = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("weight" in w for w in warnings)

    def test_similarity_threshold_out_of_range(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"discovery": {"similarity_threshold": 1.5}})
        with patch("cuepoint.config.logger") as mock_logger:
            cfg._validate()
            warnings = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("similarity_threshold" in w for w in warnings)

    def test_negative_cache_ttl(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"cache": {"ttl_days": 0}})
        with patch("cuepoint.config.logger") as mock_logger:
            cfg._validate()
            warnings = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("ttl_days" in w for w in warnings)

    def test_negative_request_delay(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"general": {"ra_request_delay": -0.5}})
        with patch("cuepoint.config.logger") as mock_logger:
            cfg._validate()
            warnings = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("ra_request_delay" in w for w in warnings)

    def test_zero_max_workers(self, monkeypatch):
        monkeypatch.setattr(cfg, "_cfg", {"general": {"max_workers": 0}})
        with patch("cuepoint.config.logger") as mock_logger:
            cfg._validate()
            warnings = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("max_workers" in w for w in warnings)


class TestConfigReload:
    def test_reload_resets_cfg(self, monkeypatch, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text("[general]\ndays_ahead = 21\n")
        monkeypatch.setattr(cfg, "_CONFIG_PATH", toml_file)
        monkeypatch.setattr(cfg, "_cfg", None)
        cfg.reload()
        assert cfg.days_ahead() == 21

    def test_reload_missing_file_uses_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cfg, "_CONFIG_PATH", tmp_path / "missing.toml")
        monkeypatch.setattr(cfg, "_CONFIG_EXAMPLE_PATH", tmp_path / "also_missing.toml")
        monkeypatch.setattr(cfg, "_cfg", None)
        cfg.reload()
        assert cfg.days_ahead() == 7
