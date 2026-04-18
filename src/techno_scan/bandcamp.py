"""
Bandcamp artist enrichment via page scraping.

No official API — scrapes search results, /music page, and album pages.
Extracts: tags/genres, supporter counts, latest release date.

Rate-limited to ~1 req/sec to be respectful.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup
from loguru import logger

from . import config as cfg
from .http_utils import retry_on_failure

# ---------------------------------------------------------------------------
# Session & rate limiter
# ---------------------------------------------------------------------------

_session: requests.Session | None = None
_session_lock = threading.Lock()
_rate_semaphore = threading.Semaphore(3)  # max 3 concurrent requests
_rate_lock = threading.Lock()
_last_request_time = 0.0
_MIN_INTERVAL = 0.35  # ~3 req/sec global (respectful but not single-threaded)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is not None:
            return _session
        _session = requests.Session()
        _session.headers.update(_HEADERS)
        return _session


@retry_on_failure(max_retries=2, base_delay=1.0)
def _fetch(url: str, params: dict[str, str] | None = None) -> requests.Response:
    """GET with rate limiting (~3 req/sec global, max 3 concurrent)."""
    global _last_request_time

    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_request_time = time.time()

    with _rate_semaphore:
        session = _get_session()
        r = session.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r


# ---------------------------------------------------------------------------
# Search: find Bandcamp URL by artist name
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def search_bandcamp_url(name: str) -> str | None:
    """Search Bandcamp for an artist by name. Returns subdomain URL on match, else None."""
    try:
        r = _fetch("https://bandcamp.com/search", params={"q": name, "item_type": "b"})
        soup = BeautifulSoup(r.text, "html.parser")
        norm_name = _normalize(name)

        for result in soup.select("li.searchresult"):
            heading = result.select_one("div.heading a")
            if not heading:
                continue
            result_name = heading.get_text(strip=True)
            # Check for close name match
            if _normalize(result_name) == norm_name:
                # Get clean URL from itemurl div
                itemurl = result.select_one("div.itemurl a")
                if itemurl:
                    url = itemurl.get_text(strip=True).rstrip("/")
                    if not url.startswith("http"):
                        url = "https://" + url
                    return url
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"Bandcamp search failed for '{name}': {e}")
    return None


# ---------------------------------------------------------------------------
# Fetch album list from /music page
# ---------------------------------------------------------------------------


def _get_album_urls(artist_url: str) -> list[str]:
    """Fetch the /music page and return a list of album URLs."""
    try:
        r = _fetch(f"{artist_url}/music")
        soup = BeautifulSoup(r.text, "html.parser")

        albums = []
        for li in soup.select("li.music-grid-item a[href]"):
            href = str(li["href"])
            if "/album/" in href:
                if href.startswith("/"):
                    href = artist_url.rstrip("/") + href
                albums.append(href)
        return albums
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"Bandcamp /music page failed for '{artist_url}': {e}")
        return []


# ---------------------------------------------------------------------------
# Parse individual album page for tags, supporters, release date
# ---------------------------------------------------------------------------


def _parse_album(url: str) -> dict[str, Any]:
    """
    Fetch an album page and extract data from JSON-LD.
    Returns dict with keys: tags, supporters, release_date (or empty dict on failure).
    """
    try:
        r = _fetch(url)
        soup = BeautifulSoup(r.text, "html.parser")

        result: dict[str, Any] = {"tags": [], "supporters": 0, "release_date": None}

        # Try JSON-LD first (richest source)
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

        # Fallback: tags from HTML elements
        if not result["tags"]:
            for tag_el in soup.select("a.tag"):
                tag = tag_el.get_text(strip=True)
                if tag:
                    result["tags"].append(tag)

        return result
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"Bandcamp album page failed ({url}): {e}")
        return {}


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------


def populate_bandcamp_info(artist_info: dict[str, Any]) -> dict[str, Any]:
    """
    Enrich artist_info with Bandcamp data.

    Reads: artist_info.get('bandcamp') — URL string (or None to trigger name search)
    Adds:  bc_tags (JSON string), bc_supporters (int), bc_latest_release (date string)
    """
    bc_url = artist_info.get("bandcamp")

    # If no Bandcamp URL, try searching by name
    if not bc_url:
        name = artist_info.get("name")
        if not name:
            return artist_info
        bc_url = search_bandcamp_url(name)
        if bc_url:
            artist_info["bandcamp"] = bc_url
        else:
            return artist_info

    try:
        # Get album URLs
        album_urls = _get_album_urls(bc_url)
        if not album_urls:
            logger.trace(f"Bandcamp: no albums found for '{bc_url}'")
            return artist_info

        # Fetch top N albums (most recent are first in grid)
        max_albums = cfg.bandcamp_max_albums()
        all_tags = []
        total_supporters = 0
        latest_release = None

        for album_url in album_urls[:max_albums]:
            data = _parse_album(album_url)
            if not data:
                continue
            all_tags.extend(data.get("tags", []))
            total_supporters += data.get("supporters", 0)
            rd = data.get("release_date")
            if rd and (latest_release is None or rd > latest_release):
                latest_release = rd

        # Deduplicate tags, preserve order
        seen = set()
        unique_tags = []
        for t in all_tags:
            tl = t.lower()
            if tl not in seen:
                seen.add(tl)
                unique_tags.append(t)

        artist_info["bc_tags"] = json.dumps(unique_tags)
        artist_info["bc_supporters"] = total_supporters
        if latest_release:
            artist_info["bc_latest_release"] = latest_release

    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Bandcamp enrichment failed for '{artist_info.get('name')}': {e}")

    return artist_info
