"""
SoundCloud artist enrichment via the official OAuth 2.1 API.

Reads credentials from:
  1. .sc_credentials file (next to this script) — two lines: client_id, client_secret
  2. SC_CLIENT_ID + SC_CLIENT_SECRET environment variables

Falls back to the unofficial client_id scraping method if no credentials are configured.

Register an app at https://soundcloud.com/you/apps to get credentials.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from typing import Any

import requests
from loguru import logger

from following import is_following
from generic import BASE_PATH
from http_utils import retry_on_failure

# ---------------------------------------------------------------------------
# Session & auth
# ---------------------------------------------------------------------------

_CREDS_FILE = BASE_PATH / "lib/parser/.sc_credentials"
_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": "techno_scan/1.0",
    }
)

_auth_lock = threading.Lock()
_access_token = None
_token_expires_at = 0.0
_use_oauth = None  # None = not yet determined, True/False after init

# Fallback: unofficial client_id
_client_id = None


def _load_credentials() -> tuple[str | None, str | None]:
    """Try .sc_credentials file first, then env vars."""
    if _CREDS_FILE.exists():
        lines = _CREDS_FILE.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) >= 2:
            return lines[0].strip(), lines[1].strip()
    cid = os.environ.get("SC_CLIENT_ID")
    secret = os.environ.get("SC_CLIENT_SECRET")
    if cid and secret:
        return cid, secret
    return None, None


def _obtain_token(client_id: str, client_secret: str) -> tuple[str, float]:
    """Get an access token via Client Credentials flow."""
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        "https://secure.soundcloud.com/oauth/token",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json; charset=utf-8",
        },
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return data["access_token"], time.monotonic() + data.get("expires_in", 3600) - 60


def _ensure_auth() -> str:
    """Ensure we have a valid auth method. Returns 'oauth' or 'client_id'."""
    global _use_oauth, _access_token, _token_expires_at, _client_id

    with _auth_lock:
        # Already initialized and token still valid
        if _use_oauth is True and time.monotonic() < _token_expires_at:
            return "oauth"
        if _use_oauth is False and _client_id:
            return "client_id"

        # Try OAuth first
        client_id, client_secret = _load_credentials()
        if client_id and client_secret:
            try:
                _access_token, _token_expires_at = _obtain_token(client_id, client_secret)
                _use_oauth = True
                logger.info("SoundCloud: using OAuth (no rate limit)")
                return "oauth"
            except Exception as e:
                logger.warning(f"SoundCloud OAuth failed, falling back to client_id scraping: {e}")

        # Fallback: scrape client_id from JS bundles
        _use_oauth = False
        _client_id = _scrape_client_id()
        logger.info("SoundCloud: using scraped client_id (rate-limited)")
        return "client_id"


def _refresh_token_if_needed() -> None:
    """Refresh OAuth token if expired."""
    global _access_token, _token_expires_at
    with _auth_lock:
        if time.monotonic() >= _token_expires_at:
            client_id, client_secret = _load_credentials()
            if client_id and client_secret:
                _access_token, _token_expires_at = _obtain_token(client_id, client_secret)


def _scrape_client_id() -> str:
    """Extract client_id from SoundCloud JS bundles (fallback method)."""
    r = _SESSION.get("https://soundcloud.com", timeout=15)
    r.raise_for_status()
    js_urls = list(dict.fromkeys(re.findall(r'https://[^"]+\.js', r.text)))
    for js_url in js_urls[-5:]:
        try:
            js_r = _SESSION.get(js_url, timeout=10)
            match = re.search(r'client_id\s*:\s*"([a-zA-Z0-9]{32})"', js_r.text)
            if match:
                return match.group(1)
        except requests.RequestException:
            continue
    raise RuntimeError("Could not extract client_id from SoundCloud JS bundles.")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

# Rate limiting only used for fallback client_id mode
_rate_lock = threading.Lock()
_last_request = 0.0
_MIN_INTERVAL = 0.35
_backoff_until = 0.0


@retry_on_failure(max_retries=2, base_delay=2.0)
def _api_get(url: str, params: dict[str, Any] | None = None, timeout: int = 15) -> requests.Response:
    """Unified GET — uses OAuth header or client_id param depending on auth mode."""
    global _last_request, _backoff_until, _client_id

    auth_mode = _ensure_auth()

    if auth_mode == "oauth":
        _refresh_token_if_needed()
        headers = {"Authorization": f"OAuth {_access_token}"}
        r = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 401:
            # Token expired mid-request, refresh and retry
            _refresh_token_if_needed()
            headers = {"Authorization": f"OAuth {_access_token}"}
            r = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r
    else:
        # Fallback: client_id with rate limiting
        if params is None:
            params = {}
        params["client_id"] = _client_id

        with _rate_lock:
            now = time.monotonic()
            if now < _backoff_until:
                time.sleep(_backoff_until - now)
            now = time.monotonic()
            wait = _MIN_INTERVAL - (now - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.monotonic()

        r = _SESSION.get(url, params=params, timeout=timeout)
        if r.status_code == 403:
            logger.debug("SC 403 — global backoff 10s, refreshing client_id")
            try:
                _client_id = _scrape_client_id()
                params["client_id"] = _client_id
            except Exception:
                pass
            with _rate_lock:
                _backoff_until = time.monotonic() + 10
                _last_request = _backoff_until
            time.sleep(10)
            r = _SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _is_valid_sc_url(url: str) -> bool:
    """Check that the URL is a clean SoundCloud profile link."""
    return bool(re.match(r"https?://soundcloud\.com/[\w-]+/?$", url))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_API_BASE = "https://api.soundcloud.com"


def search_sc_by_name(name: str) -> str | None:
    """Search SoundCloud for a user by name. Returns permalink URL on a close match, else None."""
    try:
        r = _api_get(f"{_API_BASE}/users", params={"q": name, "limit": 5}, timeout=10)
        norm_name = _normalize(name)
        for user in r.json().get("collection", []):
            if (
                _normalize(user.get("username", "")) == norm_name
                or _normalize(user.get("permalink", "")) == norm_name
                or _normalize(user.get("full_name", "")) == norm_name
            ):
                return str(user.get("permalink_url")) if user.get("permalink_url") else None
    except Exception as e:
        logger.warning(f"SC name search failed for '{name}': {e}")
    return None


def populate_sc_info(artist_info: dict[str, Any]) -> dict[str, Any]:
    sc_url = artist_info.get("soundcloud")
    if not sc_url:
        return artist_info
    sc_url = sc_url.replace("www.soundcloud.com", "soundcloud.com")

    if not _is_valid_sc_url(sc_url):
        logger.debug(f"Skipping invalid SC URL for '{artist_info.get('name')}': {sc_url}")
        artist_info.setdefault("sc_tags", json.dumps([]))
        artist_info.setdefault("sc_followers", None)
        artist_info.setdefault("sc_following", None)
        return artist_info

    if is_following(sc_url):
        logger.info(f"Followed artist: {sc_url}")

    try:
        # Resolve user profile
        r = _api_get(f"{_API_BASE}/resolve", params={"url": sc_url})
        user_data = r.json()
        user_id = user_data.get("id")

        artist_info["sc_followers"] = user_data.get("followers_count")
        artist_info["sc_following"] = user_data.get("followings_count")

        # Fetch recent tracks for genre tags
        tracks_r = _api_get(
            f"{_API_BASE}/users/{user_id}/tracks",
            params={"limit": 10},
        )
        tracks = tracks_r.json().get("collection", [])

        tags = []
        seen = set()
        for track in tracks:
            for raw in [track.get("genre", ""), track.get("tag_list", "")]:
                for m in re.finditer(r'"([^"]+)"|(\S+)', raw or ""):
                    tag = m.group(1) or m.group(2)
                    if tag and tag not in seen:
                        seen.add(tag)
                        tags.append(tag)

        artist_info["sc_tags"] = json.dumps(tags)

    except Exception as e:
        logger.warning(f"SC API failed for '{artist_info.get('name')}': {e}")
        artist_info.setdefault("sc_tags", json.dumps([]))
        artist_info.setdefault("sc_followers", None)
        artist_info.setdefault("sc_following", None)

    return artist_info
