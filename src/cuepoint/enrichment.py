"""
Artist enrichment pipeline: URL resolution, SC/Discogs/Bandcamp enrichment, caching.

Uses asyncio.gather + Semaphore for concurrent enrichment within each phase.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from loguru import logger

from . import config as cfg
from . import db as store
from .bandcamp import populate_bandcamp_info
from .discogs import populate_discogs_info
from .discovery import check_rising
from .following import is_following
from .sc import is_oauth, populate_sc_info, reset_circuit_breaker, search_sc_by_name
from .stats import ScanStats

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
    if sc_url and data.get("sc_followers") is None:
        if (datetime.now() - cached_at).days >= 1:
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


async def _run_enrichment_phases(
    to_enrich: list[tuple[str, dict[str, Any]]],
    progress_cb: Callable[[str, str, float], None] | None = None,
    label: str = "",
    stats: ScanStats | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the SC → Discogs → Bandcamp → rising → save pipeline on a list of (aid, info) pairs.

    Uses asyncio.Semaphore for concurrency control within each phase (replaces ThreadPoolExecutor).
    """

    def _cb(phase: str, detail: str, frac: float) -> None:
        if progress_cb:
            progress_cb(phase, detail, frac)

    total = len(to_enrich)
    t0 = time.monotonic()

    # Phase: SoundCloud
    reset_circuit_breaker()
    sc_concurrency = 3 if is_oauth() else 1
    sc_sem = asyncio.Semaphore(sc_concurrency)
    logger.info(f"  SC enrichment for {total} {label}artists ({sc_concurrency} concurrent)...")
    _cb("enrich_sc", f"SoundCloud: 0/{total}", 0.0)
    sc_done = 0

    async def _sc(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        nonlocal sc_done
        aid, info = item
        async with sc_sem:
            try:
                info = await populate_sc_info(info)
                if stats:
                    stats.increment(sc_ok=1)
            except Exception as e:
                logger.warning(f"SC failed for '{info.get('name')}': {e}")
                if stats:
                    stats.increment(sc_fail=1)
        sc_done += 1
        _cb("enrich_sc", f"SoundCloud: {sc_done}/{total}", sc_done / total * 0.25)
        return aid, info

    to_enrich = list(await asyncio.gather(*[_sc(item) for item in to_enrich]))
    t_sc = time.monotonic()

    # Phase: Discogs
    dc_total = len(to_enrich)
    dc_sem = asyncio.Semaphore(3)
    logger.info(f"  Discogs enrichment for {dc_total} {label}artists (rate-limited)...")
    _cb("enrich_discogs", f"Discogs: 0/{dc_total}", 0.25)
    dc_done = 0

    async def _dc(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        nonlocal dc_done
        aid, info = item
        async with dc_sem:
            try:
                info = await populate_discogs_info(info)
                if stats:
                    stats.increment(dc_ok=1)
            except Exception as e:
                logger.warning(f"Discogs failed for '{info.get('name')}': {e}")
                if stats:
                    stats.increment(dc_fail=1)
        dc_done += 1
        if dc_done % 10 == 0 or dc_done == dc_total:
            logger.info(f"  Discogs {label}progress: {dc_done}/{dc_total}")
        _cb("enrich_discogs", f"Discogs: {dc_done}/{dc_total}", 0.25 + dc_done / dc_total * 0.4)
        return aid, info

    to_enrich = list(await asyncio.gather(*[_dc(item) for item in to_enrich]))
    t_dc = time.monotonic()

    # Phase: Bandcamp
    bc_total = len(to_enrich)
    bc_sem = asyncio.Semaphore(5)
    logger.info(f"  Bandcamp enrichment for {bc_total} {label}artists (5 concurrent)...")
    _cb("enrich_bandcamp", f"Bandcamp: 0/{bc_total}", 0.65)
    bc_done = 0

    async def _bc(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        nonlocal bc_done
        aid, info = item
        async with bc_sem:
            try:
                info = await populate_bandcamp_info(info)
                if stats:
                    stats.increment(bc_ok=1)
            except Exception as e:
                logger.warning(f"Bandcamp failed for '{info.get('name')}': {e}")
                if stats:
                    stats.increment(bc_fail=1)
        bc_done += 1
        if bc_done % 10 == 0 or bc_done == bc_total:
            logger.info(f"  Bandcamp {label}progress: {bc_done}/{bc_total}")
        _cb("enrich_bandcamp", f"Bandcamp: {bc_done}/{bc_total}", 0.65 + bc_done / bc_total * 0.30)
        return aid, info

    to_enrich = list(await asyncio.gather(*[_bc(item) for item in to_enrich]))
    t_bc = time.monotonic()

    logger.info(
        f"  Enrichment timing for {total} {label}artists: "
        f"SC={t_sc - t0:.0f}s, Discogs={t_dc - t_sc:.0f}s, "
        f"Bandcamp={t_bc - t_dc:.0f}s, total={t_bc - t0:.0f}s"
    )

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


async def get_artist_info_by_ra_id(
    artist_id: str | int, get_artist_urls_fn: Callable[[str | int], Any]
) -> dict[str, Any] | None:
    """Single-artist enrichment (used by stale refresh and club fallback)."""
    cached = get_cached_artist(artist_id)
    if cached is not None:
        logger.info(f"Cache hit for artist {artist_id}")
        return cached

    artist_info: dict[str, Any] | None = await get_artist_urls_fn(artist_id)
    if artist_info is None:
        logger.warning(f"RA returned no data for artist {artist_id}, skipping enrichment")
        return None

    try:
        artist_info = await populate_sc_info(artist_info)
    except Exception as e:
        logger.warning(f"SC failed for '{artist_info.get('name')}': {e}")

    try:
        artist_info = await populate_discogs_info(artist_info)
    except Exception as e:
        logger.warning(f"Discogs failed for '{artist_info.get('name')}': {e}")

    try:
        artist_info = await populate_bandcamp_info(artist_info)
    except Exception as e:
        logger.warning(f"Bandcamp failed for '{artist_info.get('name')}': {e}")

    check_rising(artist_id, artist_info)
    save_cached_artist(artist_id, artist_info)
    return artist_info


# ---------------------------------------------------------------------------
# Batch enrichment (RA artists)
# ---------------------------------------------------------------------------


async def enrich_batch_phased(
    artist_ids: list[str | int],
    get_artist_urls_fn: Callable[[str | int], Any],
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    pct_base: float = 0.1,
    pct_range: float = 0.6,
    stats: ScanStats | None = None,
) -> dict[str | int, dict[str, Any]]:
    """Enrich a batch of artist IDs using async concurrency.

    Phase 1: URL resolution + cache check (concurrent)
    Phase 2-4: SC → Discogs → Bandcamp (shared pipeline)
    Phase 5: Rising check + cache save
    """
    t_resolve_start = time.monotonic()
    resolve_sem = asyncio.Semaphore(cfg.max_workers())

    async def _resolve(aid: str | int) -> tuple[str | int, dict[str, Any] | None, bool]:
        cached = get_cached_artist(aid)
        if cached is not None:
            return aid, cached, True
        async with resolve_sem:
            info = await get_artist_urls_fn(aid)
        return aid, info, False

    resolved = await asyncio.gather(*[_resolve(aid) for aid in artist_ids])

    logger.info(
        f"URL resolution + cache check: {time.monotonic() - t_resolve_start:.0f}s for {len(artist_ids)} artists"
    )

    results: dict[str | int, dict[str, Any]] = {}
    to_enrich: list[tuple[str, dict[str, Any]]] = []
    cache_hits = 0
    ra_misses = 0
    for aid, info, was_cached in resolved:
        if was_cached and info is not None:
            results[aid] = info
            cache_hits += 1
        elif info is None:
            logger.warning(f"RA returned no data for artist {aid}, skipping")
            ra_misses += 1
        else:
            to_enrich.append((str(aid), info))

    logger.info(
        f"Enrichment breakdown: {cache_hits} cache hits, "
        f"{len(to_enrich)} to enrich, {ra_misses} RA misses "
        f"(total {len(artist_ids)})"
    )

    if not to_enrich:
        return results

    def _wrapped_cb(phase: str, detail: str, frac: float) -> None:
        if progress_cb:
            progress_cb({"phase": phase, "detail": detail, "pct": pct_base + frac * pct_range})

    enriched = await _run_enrichment_phases(to_enrich, progress_cb=_wrapped_cb, stats=stats)
    results.update(enriched)
    return results


# ---------------------------------------------------------------------------
# Club artist enrichment
# ---------------------------------------------------------------------------


async def enrich_club_batch_phased(
    stubs: list[dict[str, Any]], stats: ScanStats | None = None
) -> dict[str, dict[str, Any]]:
    """Phased enrichment for club artist stubs (SC name search + 3-source pipeline)."""
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

    # Phase 1: SC name search for stubs missing SC URL
    sc_resolve_sem = asyncio.Semaphore(3)

    async def _sc_resolve(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        aid, info = item
        if not info.get("soundcloud"):
            async with sc_resolve_sem:
                sc_url = await search_sc_by_name(info["name"])
            if sc_url:
                info["soundcloud"] = sc_url
        return aid, info

    logger.info(f"  SC name search for {len(to_enrich)} club artists (3 concurrent)...")
    to_enrich = list(await asyncio.gather(*[_sc_resolve(item) for item in to_enrich]))

    # Phases 2-5: shared SC → Discogs → Bandcamp → rising → save pipeline
    enriched = await _run_enrichment_phases(to_enrich, label="club ", stats=stats)
    results.update(enriched)
    return results
