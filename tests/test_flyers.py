"""Tests for flyers.py — flyer extraction and image processing."""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, patch

from PIL import Image

from cuepoint.flyers import _to_data_uri, embed_flyers, get_flyer

_run = asyncio.run


class TestGetFlyer:
    def test_extracts_filename(self):
        event = {"images": [{"filename": "https://example.com/flyer.jpg"}]}
        assert get_flyer(event) == "https://example.com/flyer.jpg"

    def test_returns_none_on_missing_images(self):
        assert get_flyer({}) is None

    def test_returns_none_on_empty_images(self):
        assert get_flyer({"images": []}) is None

    def test_returns_none_on_none_filename(self):
        assert get_flyer({"images": [{"filename": None}]}) is None

    def test_returns_none_on_bad_structure(self):
        assert get_flyer({"images": "not a list"}) is None


class TestToDataUri:
    def _make_image(self, width=200, height=200, mode="RGB"):
        img = Image.new(mode, (width, height), color="red")
        buf = io.BytesIO()
        fmt = "PNG" if mode in ("RGBA", "P") else "JPEG"
        if mode == "P":
            img = img.convert("P")
        img.save(buf, format=fmt)
        return buf.getvalue()

    def test_small_image_no_resize(self):
        raw = self._make_image(200, 200)
        result = _to_data_uri(raw)
        assert result.startswith("data:image/jpeg;base64,")

    def test_large_image_resized(self):
        raw = self._make_image(800, 600)
        result = _to_data_uri(raw)
        assert result.startswith("data:image/jpeg;base64,")

    def test_rgba_converted(self):
        raw = self._make_image(100, 100, mode="RGBA")
        result = _to_data_uri(raw)
        assert result.startswith("data:image/jpeg;base64,")


class TestEmbedFlyers:
    def test_none_urls_pass_through(self):
        result = _run(embed_flyers([None, None]))
        assert result == [None, None]

    def test_empty_list(self):
        result = _run(embed_flyers([]))
        assert result == []

    @patch("cuepoint.flyers.httpx.AsyncClient")
    def test_download_failure_keeps_original(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))
        mock_client_cls.return_value = mock_client

        result = _run(embed_flyers(["https://example.com/flyer.jpg"]))
        assert result == ["https://example.com/flyer.jpg"]
