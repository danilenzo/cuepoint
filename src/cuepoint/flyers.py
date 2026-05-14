from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import urllib.parse
from typing import Any

import httpx
from loguru import logger
from PIL import Image

Image.MAX_IMAGE_PIXELS = 50_000_000

_MAX_WIDTH = 400
_JPEG_QUALITY = 70
_DOWNLOAD_CONCURRENCY = 8
_DOWNLOAD_TIMEOUT = 10.0
_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
_ALLOWED_SCHEMES = {"https"}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:106.0) Gecko/20100101 Firefox/106.0",
    "Referer": "https://ra.co/",
}


def _is_safe_url(url: str) -> bool:
    """Return False for private/internal targets to prevent SSRF."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    if hostname.endswith((".local", ".internal")) or hostname == "localhost":
        return False
    try:
        addr = ipaddress.ip_address(hostname)
        return not addr.is_private and not addr.is_loopback and not addr.is_link_local
    except ValueError:
        return True


def get_flyer(event_dict: dict[str, Any]) -> str | None:
    """Extract flyer URL from event dict (no download)."""
    try:
        val = event_dict["images"][0]["filename"]
        return str(val) if val else None
    except (KeyError, IndexError, TypeError):
        return None


async def embed_flyers(urls: list[str | None]) -> list[str | None]:
    """Download and embed a batch of flyer URLs as base64 data URIs concurrently.

    Returns a list the same length as *urls*. Each entry is either a
    ``data:`` URI, the original URL (on download failure), or ``None``.
    """
    work = [(i, url) for i, url in enumerate(urls) if url]
    results: list[str | None] = list(urls)

    if not work:
        return results

    logger.info(f"Downloading {len(work)} flyer images ({_DOWNLOAD_CONCURRENCY} concurrent)...")
    sem = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

    async with httpx.AsyncClient(headers=_HEADERS, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:

        async def _fetch(idx: int, url: str) -> None:
            async with sem:
                try:
                    if not _is_safe_url(url):
                        logger.warning(f"Blocked unsafe flyer URL: {url}")
                        return
                    r = await client.get(url)
                    r.raise_for_status()
                    if len(r.content) > _MAX_DOWNLOAD_BYTES:
                        logger.warning(f"Flyer too large ({len(r.content)} bytes): {url}")
                        return
                    results[idx] = _to_data_uri(r.content)
                except Exception as e:
                    logger.debug(f"Failed to download flyer {url}: {type(e).__name__}: {e}")

        await asyncio.gather(*[_fetch(i, url) for i, url in work])

    embedded = sum(1 for i, _ in work if isinstance(results[i], str) and str(results[i]).startswith("data:"))
    logger.info(f"Embedded {embedded}/{len(work)} flyer images")
    return results


def _to_data_uri(raw: bytes) -> str:
    """Resize image and encode as a JPEG base64 data URI."""
    img: Image.Image = Image.open(io.BytesIO(raw))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    if img.width > _MAX_WIDTH:
        ratio = _MAX_WIDTH / img.width
        img = img.resize((_MAX_WIDTH, int(img.height * ratio)), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"
