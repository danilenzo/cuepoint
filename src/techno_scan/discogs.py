"""
Discogs artist enrichment via the official REST API.

Reads an optional personal access token from:
  1. .discogs_token file (next to this script)
  2. DISCOGS_TOKEN environment variable

With a token: 60 requests/min.  Without: 25 requests/min.
Generate a token at https://www.discogs.com/settings/developers
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

import requests
from loguru import logger

from . import config as cfg
from .generic import BASE_PATH
from .http_utils import retry_on_failure

# ---------------------------------------------------------------------------
# Session & auth
# ---------------------------------------------------------------------------

_TOKEN_FILE = BASE_PATH / ".discogs_token"
_session: requests.Session | None = None
_session_lock = threading.Lock()


def _load_token() -> str | None:
    """Try .discogs_token file first, then env var."""
    if _TOKEN_FILE.exists():
        token = _TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    return os.environ.get("DISCOGS_TOKEN")


def _get_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is not None:
            return _session
        _session = requests.Session()
        token = _load_token()
        headers = {
            "User-Agent": "techno_scan/1.0 +https://github.com/techno_scan",
        }
        if token:
            headers["Authorization"] = f"Discogs token={token}"
            logger.info("Discogs: using authenticated session (60 req/min)")
        else:
            logger.warning(
                "Discogs: no token found — unauthenticated (25 req/min). "
                "Create .discogs_token or set DISCOGS_TOKEN env var."
            )
        _session.headers.update(headers)
        return _session


# ---------------------------------------------------------------------------
# Rate limiter — Discogs returns X-Discogs-Ratelimit-Remaining header
# ---------------------------------------------------------------------------

_rate_lock = threading.Lock()
_last_request_time = 0.0


_rate_remaining = 60  # optimistic start; updated from response headers


@retry_on_failure(max_retries=2, base_delay=2.0, retryable_statuses=(500, 502, 503, 504))
def _api_get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET with dynamic rate-limit awareness using Discogs headers."""
    global _last_request_time, _rate_remaining

    with _rate_lock:
        now = time.time()
        # Slow down only when running low on quota
        if _rate_remaining <= 5:
            min_interval = 2.0
        elif _rate_remaining <= 15:
            min_interval = 1.05
        else:
            min_interval = 0.4  # burst when we have headroom
        elapsed = now - _last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_request_time = time.time()

    session = _get_session()
    r = session.get(url, params=params, timeout=15)

    # Update remaining quota from response headers (thread-safe)
    remaining = r.headers.get("X-Discogs-Ratelimit-Remaining")
    if remaining is not None:
        with _rate_lock:
            _rate_remaining = int(remaining)

    # If we hit rate limit, wait and retry once
    if r.status_code == 429:
        retry_after = int(r.headers.get("Retry-After", 30))
        logger.warning(f"Discogs rate limited — waiting {retry_after}s")
        time.sleep(retry_after)
        with _rate_lock:
            _rate_remaining = 0
        r = session.get(url, params=params, timeout=15)

    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Artist ID extraction
# ---------------------------------------------------------------------------


def _resolve_artist_id(discogs_url: str) -> int | None:
    """
    Get numeric artist ID from a Discogs URL.

    Handles RA-provided formats including malformed/double URLs:
      1. https://www.discogs.com/artist/12345-Some-Name  → extract 12345
      2. https://www.discogs.com/artist/Some+Name        → resolve via search
      3. .../artist/https://de/246764-Cio-Dor            → extract 246764
    """
    from urllib.parse import unquote

    # URL-decode first so %28/%29 etc. become readable
    discogs_url = unquote(discogs_url)

    # Try numeric ID anywhere in the URL (covers malformed double-URLs too)
    m = re.search(r"(\d{2,})", discogs_url.split("/artist/")[-1])
    if m:
        return int(m.group(1))

    # Slug-only URL — resolve via search API.
    # Note: the Discogs API /artists/{id} endpoint only accepts numeric IDs,
    # not name slugs (the website redirects slugs, but the API does not).
    slug = discogs_url.rstrip("/").rsplit("/artist/", 1)[-1]
    if not slug:
        return None

    # Search by name — strip Discogs disambiguation suffix like "(5)"
    name = re.sub(r"\s*\(\d+\)\s*$", "", slug.replace("+", " ").replace("-", " ")).strip()
    try:
        data = _api_get(
            "https://api.discogs.com/database/search",
            params={"q": name, "type": "artist", "per_page": 5},
        )
        results = data.get("results", [])
        if results:
            return int(results[0]["id"])
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.debug(f"Discogs name search failed for '{name}': {e}")

    return None


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------


def populate_discogs_info(artist_info: dict[str, Any]) -> dict[str, Any]:
    url = artist_info.get("discogs")
    if not url:
        return artist_info

    artist_id = _resolve_artist_id(url)
    if not artist_id:
        logger.warning(f"Discogs: could not resolve artist ID from '{url}'")
        return artist_info

    try:
        # Fetch releases (paginated), filter to masters client-side.
        # Stop early once we have enough masters to avoid wasting API calls.
        _max_masters = cfg.discogs_max_masters()
        masters = []
        labels = set()
        page = 1
        while True:
            data = _api_get(
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
            # Stop if we have enough masters or no more pages
            if len(masters) >= _max_masters or page >= pages:
                break
            page += 1

        if not masters:
            return artist_info

        # Have/want are available directly from the releases list
        haves = []
        wants = []
        for m in masters:
            stats = m.get("stats", {}).get("community", {})
            haves.append(stats.get("in_collection", 0))
            wants.append(stats.get("in_wantlist", 0))

        # Fetch individual masters for styles.
        # Limit to top N by popularity, and stop early once we have enough
        # unique styles (diminishing returns from fetching more).
        sorted_masters = sorted(
            masters, key=lambda m: m.get("stats", {}).get("community", {}).get("in_collection", 0), reverse=True
        )[: cfg.discogs_max_masters()]

        styles = set()
        for m in sorted_masters:
            master_id = m["id"]
            try:
                master_data = _api_get(f"https://api.discogs.com/masters/{master_id}")
                styles.update(master_data.get("styles", []))
                # Once we have 4+ unique styles, further fetches rarely add new ones
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
