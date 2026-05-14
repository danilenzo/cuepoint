"""
Bandcamp artist enrichment via page scraping.

No official API — scrapes search results, /music page, and album pages.
Extracts: tags/genres, supporter counts, latest release date.

Rate-limited to ~3 req/sec to be respectful.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from . import config as cfg
from .fuzzy_match import _normalize_alnum
from .http_utils import async_retry_on_failure
from .types import ArtistInfo

# ---------------------------------------------------------------------------
# Client & rate limiter
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None
_rate_semaphore = asyncio.Semaphore(3)  # max 3 concurrent requests
_rate_lock = asyncio.Lock()
_last_request_time = 0.0
_MIN_INTERVAL = 0.35  # ~3 req/sec global

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


_client_init_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _client
    async with _client_init_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(
                headers=_HEADERS,
                timeout=15.0,
                follow_redirects=True,
            )
        return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@async_retry_on_failure(max_retries=2, base_delay=1.0)
async def _fetch(url: str, params: dict[str, str] | None = None) -> httpx.Response:
    """GET with rate limiting (~3 req/sec global, max 3 concurrent)."""
    global _last_request_time

    async with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        _last_request_time = time.monotonic()

    async with _rate_semaphore:
        client = await _get_client()
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r


# ---------------------------------------------------------------------------
# Search: find Bandcamp URL by artist name
# ---------------------------------------------------------------------------


def _normalize_bc_url(url: str) -> str:
    """Ensure a bandcamp value from RA is a full absolute URL."""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if ".bandcamp.com" in url:
        return "https://" + url
    return f"https://{url}.bandcamp.com"


async def search_bandcamp_url(name: str) -> str | None:
    """Search Bandcamp for an artist by name. Returns subdomain URL on match, else None."""
    try:
        r = await _fetch("https://bandcamp.com/search", params={"q": name, "item_type": "b"})
        soup = BeautifulSoup(r.text, "html.parser")
        norm_name = _normalize_alnum(name)

        for result in soup.select("li.searchresult"):
            heading = result.select_one("div.heading a")
            if not heading:
                continue
            result_name = heading.get_text(strip=True)
            if _normalize_alnum(result_name) == norm_name:
                itemurl = result.select_one("div.itemurl a")
                if itemurl:
                    url = itemurl.get_text(strip=True).rstrip("/")
                    if not url.startswith("http"):
                        url = "https://" + url
                    return url
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(f"Bandcamp search failed for '{name}': {e}")
    return None


# ---------------------------------------------------------------------------
# Fetch album list from /music page
# ---------------------------------------------------------------------------


async def _get_album_urls(artist_url: str) -> list[str]:
    """Fetch the /music page and return a list of album URLs."""
    try:
        r = await _fetch(f"{artist_url}/music")
        soup = BeautifulSoup(r.text, "html.parser")

        albums = []
        for li in soup.select("li.music-grid-item a[href]"):
            href = str(li["href"])
            if "/album/" in href:
                if href.startswith("/"):
                    href = artist_url.rstrip("/") + href
                albums.append(href)
        return albums
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(f"Bandcamp /music page failed for '{artist_url}': {e}")
        return []


# ---------------------------------------------------------------------------
# Parse individual album page for tags, supporters, release date
# ---------------------------------------------------------------------------


async def _parse_album(url: str) -> dict[str, Any]:
    """
    Fetch an album page and extract data from JSON-LD.
    Returns dict with keys: tags, supporters, release_date (or empty dict on failure).
    """
    try:
        r = await _fetch(url)
        soup = BeautifulSoup(r.text, "html.parser")

        result: dict[str, Any] = {"tags": [], "supporters": 0, "release_date": None}

        ld_script = soup.find("script", type="application/ld+json")
        if ld_script and ld_script.string:  # type: ignore[union-attr]
            try:
                ld = json.loads(ld_script.string)  # type: ignore[union-attr]
                result["tags"] = ld.get("keywords", [])
                sponsors = ld.get("sponsor", [])
                result["supporters"] = len(sponsors) if isinstance(sponsors, list) else 0
                date_str = ld.get("datePublished", "")
                if date_str:
                    try:
                        result["release_date"] = datetime.strptime(date_str[:20].strip(), "%d %b %Y %H:%M:%S").strftime(
                            "%Y-%m-%d"
                        )
                    except ValueError:
                        pass
            except (json.JSONDecodeError, TypeError):
                pass

        if not result["tags"]:
            for tag_el in soup.select("a.tag"):
                tag = tag_el.get_text(strip=True)
                if tag:
                    result["tags"].append(tag)

        return result
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(f"Bandcamp album page failed ({url}): {e}")
        return {}


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------


async def populate_bandcamp_info(artist_info: ArtistInfo) -> ArtistInfo:
    """
    Enrich artist_info with Bandcamp data.

    Reads: artist_info.get('bandcamp') — URL string (or None to trigger name search)
    Adds:  bc_tags (JSON string), bc_supporters (int), bc_latest_release (date string)
    """
    bc_url = artist_info.get("bandcamp")

    if bc_url:
        bc_url = _normalize_bc_url(bc_url)
        artist_info["bandcamp"] = bc_url
    else:
        name = artist_info.get("name")
        if not name:
            return artist_info
        bc_url = await search_bandcamp_url(name)
        if bc_url:
            artist_info["bandcamp"] = bc_url
        else:
            return artist_info

    try:
        album_urls = await _get_album_urls(bc_url)
        if not album_urls:
            logger.trace(f"Bandcamp: no albums found for '{bc_url}'")
            return artist_info

        max_albums = cfg.bandcamp_max_albums()
        all_tags: list[str] = []
        total_supporters = 0
        latest_release = None

        album_results = await asyncio.gather(
            *[_parse_album(url) for url in album_urls[:max_albums]],
            return_exceptions=True,
        )

        for data in album_results:
            if isinstance(data, Exception) or not data:
                continue
            all_tags.extend(data.get("tags", []))
            total_supporters += data.get("supporters", 0)
            rd = data.get("release_date")
            if rd and (latest_release is None or rd > latest_release):
                latest_release = rd

        seen: set[str] = set()
        unique_tags: list[str] = []
        for t in all_tags:
            tl = t.lower()
            if tl not in seen:
                seen.add(tl)
                unique_tags.append(t)

        artist_info["bc_tags"] = json.dumps(unique_tags)
        artist_info["bc_supporters"] = total_supporters
        if latest_release:
            artist_info["bc_latest_release"] = latest_release

    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Bandcamp enrichment failed for '{artist_info.get('name')}': {e}")

    return artist_info
