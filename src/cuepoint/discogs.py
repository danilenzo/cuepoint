"""
Discogs artist enrichment via the official REST API.

Reads an optional personal access token from:
  1. .discogs_token file (next to this script)
  2. DISCOGS_TOKEN environment variable

With a token: 60 requests/min.  Without: 25 requests/min.
Generate a token at https://www.discogs.com/settings/developers
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

import httpx
from loguru import logger

from . import config as cfg
from .generic import BASE_PATH
from .http_utils import async_retry_on_failure

# ---------------------------------------------------------------------------
# Client & auth
# ---------------------------------------------------------------------------

_TOKEN_FILE = BASE_PATH / ".discogs_token"
_client: httpx.AsyncClient | None = None
_client_init_lock = asyncio.Lock()


def _load_token() -> str | None:
    """Try .discogs_token file first, then env var."""
    if _TOKEN_FILE.exists():
        token = _TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    return os.environ.get("DISCOGS_TOKEN")


async def _get_client() -> httpx.AsyncClient:
    global _client
    async with _client_init_lock:
        if _client is not None and not _client.is_closed:
            return _client
        token = _load_token()
        headers: dict[str, str] = {
            "User-Agent": "cuepoint/1.0 +https://github.com/cuepoint",
        }
        if token:
            headers["Authorization"] = f"Discogs token={token}"
            logger.info("Discogs: using authenticated session (60 req/min)")
        else:
            logger.warning(
                "Discogs: no token found — unauthenticated (25 req/min). "
                "Create .discogs_token or set DISCOGS_TOKEN env var."
            )
        _client = httpx.AsyncClient(
            headers=headers,
            timeout=15.0,
            follow_redirects=True,
        )
        return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Rate limiter — Discogs returns X-Discogs-Ratelimit-Remaining header
# ---------------------------------------------------------------------------

_rate_lock = asyncio.Lock()
_last_request_time = 0.0
_rate_remaining = 60


@async_retry_on_failure(max_retries=2, base_delay=2.0, retryable_statuses=(500, 502, 503, 504))
async def _api_get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET with dynamic rate-limit awareness using Discogs headers."""
    global _last_request_time, _rate_remaining

    async with _rate_lock:
        now = time.time()
        if _rate_remaining <= 5:
            min_interval = 2.0
        elif _rate_remaining <= 15:
            min_interval = 1.05
        else:
            min_interval = 0.4
        elapsed = now - _last_request_time
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        _last_request_time = time.time()

    client = await _get_client()
    r = await client.get(url, params=params)

    remaining = r.headers.get("X-Discogs-Ratelimit-Remaining")
    if remaining is not None:
        async with _rate_lock:
            _rate_remaining = int(remaining)

    if r.status_code == 429:
        retry_after = int(r.headers.get("Retry-After", 30))
        logger.warning(f"Discogs rate limited — waiting {retry_after}s")
        await asyncio.sleep(retry_after)
        async with _rate_lock:
            _rate_remaining = 0
        r = await client.get(url, params=params)

    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Artist ID extraction
# ---------------------------------------------------------------------------


async def _resolve_artist_id(discogs_url: str) -> int | None:
    """
    Get numeric artist ID from a Discogs URL.

    Handles RA-provided formats including malformed/double URLs:
      1. https://www.discogs.com/artist/12345-Some-Name  → extract 12345
      2. https://www.discogs.com/artist/Some+Name        → resolve via search
      3. .../artist/https://de/246764-Cio-Dor            → extract 246764
    """
    from urllib.parse import unquote

    discogs_url = unquote(discogs_url)

    m = re.search(r"(\d{2,})", discogs_url.split("/artist/")[-1])
    if m:
        return int(m.group(1))

    slug = discogs_url.rstrip("/").rsplit("/artist/", 1)[-1]
    if not slug:
        return None

    name = re.sub(r"\s*\(\d+\)\s*$", "", slug.replace("+", " ").replace("-", " ")).strip()
    try:
        data = await _api_get(
            "https://api.discogs.com/database/search",
            params={"q": name, "type": "artist", "per_page": 5},
        )
        results = data.get("results", [])
        if results:
            return int(results[0]["id"])
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.debug(f"Discogs name search failed for '{name}': {e}")

    return None


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------


async def populate_discogs_info(artist_info: dict[str, Any]) -> dict[str, Any]:
    url = artist_info.get("discogs")
    if not url:
        return artist_info

    artist_id = await _resolve_artist_id(url)
    if not artist_id:
        logger.warning(f"Discogs: could not resolve artist ID from '{url}'")
        return artist_info

    try:
        _max_masters = cfg.discogs_max_masters()
        masters = []
        labels: set[str] = set()
        page = 1
        while True:
            data = await _api_get(
                f"https://api.discogs.com/artists/{artist_id}/releases",
                params={
                    "per_page": 100,
                    "page": page,
                    "sort": "year",
                    "sort_order": "desc",
                },
            )
            for rel in data.get("releases", []):
                lbl = rel.get("label")
                if lbl:
                    labels.add(lbl)
                if rel.get("type") == "master":
                    masters.append(rel)
            pages = data.get("pagination", {}).get("pages", 1)
            if len(masters) >= _max_masters or page >= pages:
                break
            page += 1

        if not masters:
            return artist_info

        haves = []
        wants = []
        for m in masters:
            stats = m.get("stats", {}).get("community", {})
            haves.append(stats.get("in_collection", 0))
            wants.append(stats.get("in_wantlist", 0))

        sorted_masters = sorted(
            masters, key=lambda m: m.get("stats", {}).get("community", {}).get("in_collection", 0), reverse=True
        )[: cfg.discogs_max_masters()]

        styles: set[str] = set()
        for m in sorted_masters:
            master_id = m["id"]
            try:
                master_data = await _api_get(f"https://api.discogs.com/masters/{master_id}")
                styles.update(master_data.get("styles", []))
                if len(styles) >= 4:
                    break
            except Exception as e:
                logger.warning(f"Discogs master {master_id} failed: {e}")

        total_haves = sum(haves)
        total_wants = sum(wants)

        artist_info["dc_styles"] = json.dumps(list(styles))
        artist_info["dc_have"] = total_haves
        artist_info["dc_want"] = total_wants
        artist_info["dc_ratio"] = round(total_wants / total_haves, 1) if total_haves > 0 else 0
        artist_info["dc_rating"] = 0
        if labels:
            artist_info["dc_labels"] = json.dumps(list(labels))

    except Exception as e:
        logger.warning(f"Discogs enrichment failed for '{artist_info.get('name')}': {e}")

    return artist_info
