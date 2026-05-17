"""Tests for SSRF protection in flyers.py — _is_safe_url validation."""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from cuepoint.flyers import _is_safe_url, embed_flyers

_run = asyncio.run


class TestIsSafeUrl:
    def test_valid_https_url(self):
        assert _is_safe_url("https://example.com/flyer.jpg") is True

    def test_valid_https_cdn(self):
        assert _is_safe_url("https://cdn.ra.co/images/flyer.jpg") is True

    def test_blocks_http(self):
        assert _is_safe_url("http://example.com/flyer.jpg") is False

    def test_blocks_ftp(self):
        assert _is_safe_url("ftp://example.com/flyer.jpg") is False

    def test_blocks_file(self):
        assert _is_safe_url("file:///etc/passwd") is False

    def test_blocks_javascript(self):
        assert _is_safe_url("javascript:alert(1)") is False

    def test_blocks_data(self):
        assert _is_safe_url("data:text/html,<script>") is False

    def test_blocks_localhost(self):
        assert _is_safe_url("https://localhost/image.jpg") is False

    def test_blocks_localhost_ip(self):
        assert _is_safe_url("https://127.0.0.1/image.jpg") is False

    def test_blocks_loopback_alt(self):
        assert _is_safe_url("https://127.0.0.2/image.jpg") is False

    def test_blocks_private_10(self):
        assert _is_safe_url("https://10.0.0.1/image.jpg") is False

    def test_blocks_private_172(self):
        assert _is_safe_url("https://172.16.0.1/image.jpg") is False

    def test_blocks_private_192(self):
        assert _is_safe_url("https://192.168.1.1/image.jpg") is False

    def test_blocks_dot_local(self):
        assert _is_safe_url("https://server.local/image.jpg") is False

    def test_blocks_dot_internal(self):
        assert _is_safe_url("https://api.internal/image.jpg") is False

    def test_blocks_link_local(self):
        assert _is_safe_url("https://169.254.169.254/metadata") is False

    def test_blocks_empty_hostname(self):
        assert _is_safe_url("https:///path") is False

    def test_blocks_no_scheme(self):
        assert _is_safe_url("//example.com/image.jpg") is False

    def test_allows_public_ip(self):
        assert _is_safe_url("https://93.184.216.34/image.jpg") is True


class TestEmbedFlyersSSRF:
    def test_unsafe_url_blocked(self):
        result = _run(embed_flyers(["https://127.0.0.1/evil.jpg"]))
        assert result == ["https://127.0.0.1/evil.jpg"]

    @patch("cuepoint.flyers.httpx.AsyncClient")
    def test_oversized_response_rejected(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b"x" * (11 * 1024 * 1024)  # > 10MB
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = _run(embed_flyers(["https://example.com/huge.jpg"]))
        assert result == ["https://example.com/huge.jpg"]

    @patch("cuepoint.flyers.httpx.AsyncClient")
    def test_valid_url_embedded(self, mock_client_cls):
        img = Image.new("RGB", (100, 100), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        raw = buf.getvalue()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = raw
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = _run(embed_flyers(["https://cdn.ra.co/flyer.jpg"]))
        assert result[0].startswith("data:image/jpeg;base64,")
