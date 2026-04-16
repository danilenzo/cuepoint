"""
Artist enrichment pipeline: URL resolution, SC/Discogs/Bandcamp enrichment, caching.

Extracted from event_fetcher.py for maintainability.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from loguru import logger

import config as cfg
import db as store
from bandcamp import populate_bandcamp_info
from discogs import populate_discogs_info
from discovery import check_rising
from following import is_following
from sc import populate_sc_info, search_sc_by_name

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

CACHE_TTL_DAYS = cfg.cache_ttl_days()
CACHE_TTL_FOLLOWING_DAYS = cfg.cache_ttl_following_days()
CACHE_STALE_DAYS = cfg.cache_stale_days()


def get_cached_artist(artist_id: str | int) -> dict[str, Any] | None:
    result = store.get_cached_artist(str(artist_id))
    if result is None:
        return None
    data, cached_at_str = result
    cached_at = datetime.fromisoformat(cached_at_str)
    sc_url = data.get("soundcloud")
    ttl = CACHE_TTL_FOLLOWING_DAYS if (sc_url and is_following(sc_url)) else CACHE_TTL_DAYS
    if (datetime.now() - cached_at).days >= ttl:
        return None
    return data


def is_cache_stale(artist_id: str | int) -> bool:
    """Check if cached data exists but is older than stale_days (soft TTL)."""
    result = store.get_cached_artist(str(artist_id))
    if result is None:
        return False
    _, cached_at_str = result
    cached_at = datetime.fromisoformat(cached_at_str)
    return (datetime.now() - cached_at).days >= CACHE_STALE_DAYS


def save_cached_artist(artist_id: str | int, artist_info: dict[str, Any]) -> None:
    store.save_cached_artist(str(artist_id), artist_info)


def cleanup_cache() -> None:
    store.cleanup_cache(CACHE_TTL_DAYS, CACHE_TTL_FOLLOWING_DAYS, is_following)


# ---------------------------------------------------------------------------
# Shared enrichment phases (SC → Discogs → Bandcamp → rising → save)
# ---------------------------------------------------------------------------


def _run_enrichment_phases(
    to_enrich: list[tuple[str, dict[str, Any]]],
    progress_cb: Callable[[str, str, float], None] | None = None,
    label: str = "",
) -> dict[str, dict[str, Any]]:
    """Run the SC → Discogs → Bandcamp → rising → save pipeline on a list of (aid, info) pairs.

    Args:
        to_enrich: list of (artist_id, info_dict) to enrich.
        progress_cb: optional callback(phase, detail, fraction) where fraction is 0.0–1.0.
        label: prefix for log messages (e.g. "" or "club ").

    Returns:
        dict mapping artist_id → enriched info.
    """

    def _cb(phase: str, detail: str, frac: float) -> None:
        if progress_cb:
            progress_cb(phase, detail, frac)

    total = len(to_enrich)

    # Phase: SoundCloud (3 workers)
    logger.info(f"  SC enrichment for {total} {label}artists (3 workers)...")
    _cb("enrich_sc", f"SoundCloud: 0/{total}", 0.0)
    _sc_lock = threading.Lock()
    _sc_done = [0]

    def _sc(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        aid, info = item
        try:
            info = populate_sc_info(info)
        except Exception as e:
            logger.warning(f"SC failed for '{info.get('name')}': {e}")
        with _sc_lock:
            _sc_done[0] += 1
            done = _sc_done[0]
        _cb("enrich_sc", f"SoundCloud: {done}/{total}", done / total * 0.25)
        return aid, info

    with ThreadPoolExecutor(max_workers=3) as pool:
        to_enrich = list(pool.map(_sc, to_enrich))

    # Phase: Discogs (3 workers, rate limited)
    dc_total = len(to_enrich)
    logger.info(f"  Discogs enrichment for {dc_total} {label}artists (rate-limited)...")
    _cb("enrich_discogs", f"Discogs: 0/{dc_total}", 0.25)
    _dc_lock = threading.Lock()
    _dc_done = [0]

    def _dc(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        aid, info = item
        try:
            info = populate_discogs_info(info)
        except Exception as e:
            logger.warning(f"Discogs failed for '{info.get('name')}': {e}")
        with _dc_lock:
            _dc_done[0] += 1
            done = _dc_done[0]
        if done % 10 == 0 or done == dc_total:
            logger.info(f"  Discogs {label}progress: {done}/{dc_total}")
        _cb("enrich_discogs", f"Discogs: {done}/{dc_total}", 0.25 + done / dc_total * 0.4)
        return aid, info

    with ThreadPoolExecutor(max_workers=3) as pool:
        to_enrich = list(pool.map(_dc, to_enrich))

    # Phase: Bandcamp (5 workers)
    bc_total = len(to_enrich)
    logger.info(f"  Bandcamp enrichment for {bc_total} {label}artists (5 workers)...")
    _cb("enrich_bandcamp", f"Bandcamp: 0/{bc_total}", 0.65)
    _bc_lock = threading.Lock()
    _bc_done = [0]

    def _bc(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        aid, info = item
        try:
            info = populate_bandcamp_info(info)
        except Exception as e:
            logger.warning(f"Bandcamp failed for '{info.get('name')}': {e}")
        with _bc_lock:
            _bc_done[0] += 1
            done = _bc_done[0]
        if done % 10 == 0 or done == bc_total:
            logger.info(f"  Bandcamp {label}progress: {done}/{bc_total}")
        _cb("enrich_bandcamp", f"Bandcamp: {done}/{bc_total}", 0.65 + done / bc_total * 0.30)
        return aid, info

    with ThreadPoolExecutor(max_workers=5) as pool:
        to_enrich = list(pool.map(_bc, to_enrich))

    # Phase: rising check + batch cache save
    _cb("saving", "Saving to cache...", 0.95)
    results: dict[str, dict[str, Any]] = {}
    batch_items: list[tuple[str, dict[str, Any], int | None, int | None]] = []
    for aid, info in to_enrich:
        check_rising(aid, info, save=False)
        sc_val = int(info["sc_followers"]) if info.get("sc_followers") is not None else None
        dc_val = int(info["dc_want"]) if info.get("dc_want") is not None else None
        batch_items.append((str(aid), info, sc_val, dc_val))
        results[aid] = info

    store.batch_save_enriched(batch_items)

    return results


# ---------------------------------------------------------------------------
# Single-artist enrichment
# ---------------------------------------------------------------------------


def get_artist_info_by_ra_id(
    artist_id: str | int, get_artist_urls_fn: Callable[[str | int], dict[str, Any] | None]
) -> dict[str, Any] | None:
    """Single-artist enrichment (used by stale refresh and club fallback)."""
    cached = get_cached_artist(artist_id)
    if cached is not None:
        logger.info(f"Cache hit for artist {artist_id}")
        return cached

    artist_info = get_artist_urls_fn(artist_id)
    if artist_info is None:
        logger.warning(f"RA returned no data for artist {artist_id}, skipping enrichment")
        return None

    try:
        artist_info = populate_sc_info(artist_info)
    except Exception as e:
        logger.warning(f"SC failed for '{artist_info.get('name')}': {e}")

    try:
        artist_info = populate_discogs_info(artist_info)
    except Exception as e:
        logger.warning(f"Discogs failed for '{artist_info.get('name')}': {e}")

    try:
        artist_info = populate_bandcamp_info(artist_info)
    except Exception as e:
        logger.warning(f"Bandcamp failed for '{artist_info.get('name')}': {e}")

    check_rising(artist_id, artist_info)
    save_cached_artist(artist_id, artist_info)
    return artist_info


# ---------------------------------------------------------------------------
# Batch enrichment (RA artists)
# ---------------------------------------------------------------------------


def enrich_batch_phased(
    artist_ids: list[str | int],
    get_artist_urls_fn: Callable[[str | int], dict[str, Any] | None],
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    pct_base: float = 0.1,
    pct_range: float = 0.6,
) -> dict[str | int, dict[str, Any]]:
    """Enrich a batch of artist IDs using source-specific thread pools.

    Phase 1: URL resolution + cache check (parallel)
    Phase 2-4: SC → Discogs → Bandcamp (shared pipeline)
    Phase 5: Rising check + cache save

    progress_cb: optional callback({"phase", "detail", "pct"})
    pct_base/pct_range: progress percentage range this batch occupies
    """

    # Phase 1: resolve URLs, filter out cache hits
    def _resolve(aid: str | int) -> tuple[str | int, dict[str, Any] | None, bool]:
        cached = get_cached_artist(aid)
        if cached is not None:
            logger.info(f"Cache hit for artist {aid}")
            return aid, cached, True
        info = get_artist_urls_fn(aid)
        return aid, info, False

    with ThreadPoolExecutor(max_workers=cfg.max_workers()) as pool:
        resolved = list(pool.map(_resolve, artist_ids))

    results: dict[str | int, dict[str, Any]] = {}
    to_enrich: list[tuple[str, dict[str, Any]]] = []
    for aid, info, was_cached in resolved:
        if was_cached and info is not None:
            results[aid] = info
        elif info is None:
            logger.warning(f"RA returned no data for artist {aid}, skipping")
        else:
            to_enrich.append((str(aid), info))

    if not to_enrich:
        return results

    # Adapter: translate (phase, detail, frac) → {"phase", "detail", "pct"}
    def _wrapped_cb(phase: str, detail: str, frac: float) -> None:
        if progress_cb:
            progress_cb({"phase": phase, "detail": detail, "pct": pct_base + frac * pct_range})

    enriched = _run_enrichment_phases(to_enrich, progress_cb=_wrapped_cb)
    results.update(enriched)
    return results


# ---------------------------------------------------------------------------
# Club artist enrichment
# ---------------------------------------------------------------------------


def get_club_artist_info(
    artist: dict[str, Any], get_artist_urls_fn: Callable[..., Any] | None = None
) -> dict[str, Any]:
    """Single club artist enrichment (fallback for individual calls)."""
    cached = get_cached_artist(artist["id"])
    if cached is not None:
        return cached

    enriched = dict(artist)

    if not enriched.get("soundcloud"):
        sc_url = search_sc_by_name(artist["name"])
        if sc_url:
            enriched["soundcloud"] = sc_url

    try:
        enriched = populate_sc_info(enriched)
    except Exception as e:
        logger.warning(f"SC populate failed for '{artist['name']}': {e}")
    try:
        enriched = populate_discogs_info(enriched)
    except Exception as e:
        logger.warning(f"Discogs failed for club artist '{artist['name']}': {e}")
    try:
        enriched = populate_bandcamp_info(enriched)
    except Exception as e:
        logger.warning(f"Bandcamp failed for club artist '{artist['name']}': {e}")

    check_rising(artist["id"], enriched)
    save_cached_artist(artist["id"], enriched)
    return enriched


def enrich_club_batch_phased(stubs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Phased enrichment for club artist stubs (SC name search + 3-source pipeline)."""
    # Phase 0: cache check
    results: dict[str, dict[str, Any]] = {}
    to_enrich: list[tuple[str, dict[str, Any]]] = []
    for stub in stubs:
        cached = get_cached_artist(stub["id"])
        if cached is not None:
            results[stub["id"]] = cached
        else:
            to_enrich.append((stub["id"], dict(stub)))

    if not to_enrich:
        return results

    # Phase 1: SC name search for stubs missing SC URL (3 workers)
    def _sc_resolve(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        aid, info = item
        if not info.get("soundcloud"):
            sc_url = search_sc_by_name(info["name"])
            if sc_url:
                info["soundcloud"] = sc_url
        return aid, info

    logger.info(f"  SC name search for {len(to_enrich)} club artists (3 workers)...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        to_enrich = list(pool.map(_sc_resolve, to_enrich))

    # Phases 2-5: shared SC → Discogs → Bandcamp → rising → save pipeline
    enriched = _run_enrichment_phases(to_enrich, label="club ")
    results.update(enriched)
    return results
