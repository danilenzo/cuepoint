"""
SoundCloud artist enrichment via the official OAuth 2.1 API.

Reads credentials from:
  1. .sc_credentials file (next to this script) — two lines: client_id, client_secret
  2. SC_CLIENT_ID + SC_CLIENT_SECRET environment variables

Falls back to the unofficial client_id scraping method if no credentials are configured.

Register an app at https://soundcloud.com/you/apps to get credentials.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import time
from typing import Any

import httpx
from loguru import logger

from .following import is_following
from .fuzzy_match import _normalize_alnum
from .generic import BASE_PATH
from .http_utils import async_retry_on_failure

# ---------------------------------------------------------------------------
# Client & auth
# ---------------------------------------------------------------------------

_CREDS_FILE = BASE_PATH / ".sc_credentials"
_client: httpx.AsyncClient | None = None

_auth_lock = asyncio.Lock()
_access_token: str | None = None
_token_expires_at = 0.0
_use_oauth: bool | None = None  # None = not yet determined

# Fallback: unofficial client_id
_client_id: str | None = None


_client_init_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _client
    async with _client_init_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(
                headers={"User-Agent": "cuepoint/1.0"},
                timeout=15.0,
                follow_redirects=True,
            )
        return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


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


async def _obtain_token(cid: str, client_secret: str) -> tuple[str, float]:
    """Get an access token via Client Credentials flow."""
    creds = base64.b64encode(f"{cid}:{client_secret}".encode()).decode()
    client = await _get_client()
    r = await client.post(
        "https://secure.soundcloud.com/oauth/token",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json; charset=utf-8",
        },
        content="grant_type=client_credentials",
    )
    r.raise_for_status()
    data = r.json()
    return data["access_token"], time.monotonic() + data.get("expires_in", 3600) - 60


async def _ensure_auth() -> str:
    """Ensure we have a valid auth method. Returns 'oauth' or 'client_id'."""
    global _use_oauth, _access_token, _token_expires_at, _client_id

    async with _auth_lock:
        if _use_oauth is True and time.monotonic() < _token_expires_at:
            return "oauth"
        if _use_oauth is False and _client_id:
            return "client_id"

        cid, client_secret = _load_credentials()
        if cid and client_secret:
            try:
                _access_token, _token_expires_at = await _obtain_token(cid, client_secret)
                _use_oauth = True
                logger.info("SoundCloud: using OAuth (no rate limit)")
                return "oauth"
            except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as e:
                logger.warning(f"SoundCloud OAuth failed, falling back to client_id scraping: {e}")

        _use_oauth = False
        _client_id = await _scrape_client_id()
        logger.info("SoundCloud: using scraped client_id (rate-limited)")
        return "client_id"


async def _refresh_token_if_needed() -> None:
    """Refresh OAuth token if expired."""
    global _access_token, _token_expires_at
    async with _auth_lock:
        if time.monotonic() >= _token_expires_at:
            cid, client_secret = _load_credentials()
            if cid and client_secret:
                _access_token, _token_expires_at = await _obtain_token(cid, client_secret)


_CLIENT_ID_PATTERNS = [
    re.compile(r'client_id\s*:\s*"([a-zA-Z0-9]{32})"'),
    re.compile(r"client_id\s*:\s*'([a-zA-Z0-9]{32})'"),
    re.compile(r'clientId\s*[:=]\s*"([a-zA-Z0-9]{32})"'),
]


async def _scrape_client_id() -> str:
    """Extract client_id from SoundCloud JS bundles (fallback method)."""
    client = await _get_client()
    for _attempt in range(3):
        r = await client.get("https://soundcloud.com")
        r.raise_for_status()
        js_urls = list(dict.fromkeys(re.findall(r'https://[^"\']+\.js', r.text)))
        sndcdn = [u for u in js_urls if "sndcdn.com" in u]
        if sndcdn:
            break
    else:
        raise RuntimeError("Could not load SoundCloud JS bundles after 3 attempts.")

    for js_url in sndcdn:
        try:
            js_r = await client.get(js_url, timeout=10.0)
            for pat in _CLIENT_ID_PATTERNS:
                match = pat.search(js_r.text)
                if match:
                    return match.group(1)
        except httpx.HTTPError:
            continue
    raise RuntimeError("Could not extract client_id from SoundCloud JS bundles.")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

class SCCircuitOpen(Exception):
    """Raised when too many 403s trip the circuit breaker."""


class _CircuitBreaker:
    """Tracks 403 failure ratio and trips when threshold is exceeded."""

    def __init__(self, ratio: float = 0.6, min_requests: int = 8) -> None:
        self._lock = asyncio.Lock()
        self._ratio = ratio
        self._min_requests = min_requests
        self._total_403s = 0
        self._total_requests = 0
        self._is_open = False

    async def check(self) -> None:
        async with self._lock:
            if self._is_open:
                raise SCCircuitOpen("SC circuit breaker open — too many 403s")

    async def record_success(self) -> None:
        async with self._lock:
            self._total_requests += 1

    async def record_failure(self) -> None:
        async with self._lock:
            self._total_requests += 1
            self._total_403s += 1
            if self._total_requests >= self._min_requests and self._total_403s / self._total_requests >= self._ratio:
                self._is_open = True
                logger.warning(
                    f"SC circuit breaker tripped: {self._total_403s}/{self._total_requests} "
                    f"requests got 403 — skipping remaining SC enrichment"
                )
                raise SCCircuitOpen(
                    f"SC circuit breaker open: {self._total_403s}/{self._total_requests} requests failed"
                )

    async def reset(self) -> None:
        async with self._lock:
            self._total_403s = 0
            self._total_requests = 0
            self._is_open = False


class _RateLimiter:
    """Enforces minimum interval between requests with backoff support."""

    def __init__(self, min_interval: float = 0.5, base_backoff: float = 3.0, max_backoff: float = 30.0) -> None:
        self._lock = asyncio.Lock()
        self._min_interval = min_interval
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._last_request = 0.0
        self._consecutive_fails = 0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def backoff(self) -> None:
        async with self._lock:
            self._consecutive_fails += 1
            delay = min(self._base_backoff * (2 ** (self._consecutive_fails - 1)), self._max_backoff)
            delay += random.uniform(0, delay * 0.3)
        logger.debug(f"SC 403 — backoff {delay:.1f}s")
        await asyncio.sleep(delay)
        async with self._lock:
            self._last_request = time.monotonic()

    async def record_success(self) -> None:
        async with self._lock:
            self._consecutive_fails = 0

    async def reset(self) -> None:
        async with self._lock:
            self._last_request = 0.0
            self._consecutive_fails = 0


_breaker = _CircuitBreaker()
_limiter = _RateLimiter()
_CLIENT_ID_REFRESH_COOLDOWN = 60.0
_last_client_id_refresh = 0.0
_refresh_lock = asyncio.Lock()


@async_retry_on_failure(max_retries=2, base_delay=2.0)
async def _api_get(url: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> httpx.Response:
    """Unified GET — uses OAuth header or client_id param depending on auth mode."""
    global _client_id, _last_client_id_refresh

    auth_mode = await _ensure_auth()
    client = await _get_client()

    if auth_mode == "oauth":
        await _refresh_token_if_needed()
        headers = {"Authorization": f"OAuth {_access_token}"}
        r = await client.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 401:
            await _refresh_token_if_needed()
            headers = {"Authorization": f"OAuth {_access_token}"}
            r = await client.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r

    await _breaker.check()
    params = dict(params) if params else {}
    params["client_id"] = _client_id
    await _limiter.wait()

    r = await client.get(url, params=params, timeout=timeout)
    if r.status_code != 403:
        await _breaker.record_success()
        await _limiter.record_success()
        r.raise_for_status()
        return r

    # 403 — record failure, maybe refresh client_id, backoff, retry once
    await _breaker.record_failure()

    async with _refresh_lock:
        now = time.monotonic()
        if now - _last_client_id_refresh >= _CLIENT_ID_REFRESH_COOLDOWN:
            _last_client_id_refresh = now
            try:
                _client_id = await _scrape_client_id()
                params["client_id"] = _client_id
                logger.debug("SC 403 — refreshed client_id")
            except (httpx.HTTPError, ValueError, RuntimeError):
                pass

    await _limiter.backoff()
    r = await client.get(url, params=params, timeout=timeout)
    if r.status_code == 403:
        await _breaker.record_failure()
    else:
        await _limiter.record_success()
    r.raise_for_status()
    return r


async def reset_circuit_breaker() -> None:
    """Reset rate limiter and circuit breaker for a new scan batch."""
    await _breaker.reset()
    await _limiter.reset()


def is_oauth() -> bool:
    """Return True if using OAuth (no rate limit), False if using scraped client_id."""
    return _use_oauth is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid_sc_url(url: str) -> bool:
    """Check that the URL is a clean SoundCloud profile link."""
    return bool(re.match(r"https?://soundcloud\.com/[\w-]+/?$", url))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_API_BASE = "https://api.soundcloud.com"


async def search_sc_by_name(name: str) -> str | None:
    """Search SoundCloud for a user by name. Returns permalink URL on a close match, else None."""
    try:
        r = await _api_get(f"{_API_BASE}/users", params={"q": name, "limit": 5}, timeout=10.0)
        norm_name = _normalize_alnum(name)
        for user in r.json().get("collection", []):
            if (
                _normalize_alnum(user.get("username", "")) == norm_name
                or _normalize_alnum(user.get("permalink", "")) == norm_name
                or _normalize_alnum(user.get("full_name", "")) == norm_name
            ):
                return str(user.get("permalink_url")) if user.get("permalink_url") else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"SC name search failed for '{name}': {e}")
    return None


async def populate_sc_info(artist_info: dict[str, Any]) -> dict[str, Any]:
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
        r = await _api_get(f"{_API_BASE}/resolve", params={"url": sc_url})
        user_data = r.json()
        user_id = user_data.get("id")

        artist_info["sc_followers"] = user_data.get("followers_count")
        artist_info["sc_following"] = user_data.get("followings_count")

        tracks_r = await _api_get(
            f"{_API_BASE}/users/{user_id}/tracks",
            params={"limit": 10},
        )
        tracks = tracks_r.json().get("collection", [])

        tags: list[str] = []
        seen: set[str] = set()
        for track in tracks:
            for raw in [track.get("genre", ""), track.get("tag_list", "")]:
                for m in re.finditer(r'"([^"]+)"|(\S+)', raw or ""):
                    tag = m.group(1) or m.group(2)
                    if tag and tag not in seen:
                        seen.add(tag)
                        tags.append(tag)

        artist_info["sc_tags"] = json.dumps(tags)

    except SCCircuitOpen:
        logger.debug(f"SC skipped (circuit open) for '{artist_info.get('name')}'")
        artist_info.setdefault("sc_tags", json.dumps([]))
        artist_info.setdefault("sc_followers", None)
        artist_info.setdefault("sc_following", None)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"SC API failed for '{artist_info.get('name')}': {e}")
        artist_info.setdefault("sc_tags", json.dumps([]))
        artist_info.setdefault("sc_followers", None)
        artist_info.setdefault("sc_following", None)

    return artist_info
