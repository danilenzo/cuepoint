from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import re
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from loguru import logger
from wakepy import keep

from . import config as cfg
from . import db as store
from .club_scrapers import scrape_city_clubs
from .discovery import (
    compute_label_affinity as _compute_label_affinity,
)
from .discovery import (
    compute_similarity as _compute_similarity,
)
from .enrichment import (
    CACHE_STALE_DAYS,
    cleanup_cache,
    enrich_batch_phased,
    enrich_club_batch_phased,
    get_cached_artist,
    is_cache_stale,
)
from .flyers import embed_flyers, get_flyer
from .fuzzy_match import _merge_ra_into_stub, _norm_artist_name, _normalize_alnum
from .generic import OUTPUT_PATH, RA, URL
from .html_creator import create_html
from .http_utils import async_retry_on_failure
from .payloads import get_artist_payload_by_id, get_event_listings_payload
from .scoring import filter_df, sort_df
from .scoring import find_and_record as _find_and_record
from .stats import ScanStats
from .types import ArtistInfo

DELAY = cfg.ra_request_delay()

# Shared async client for RA GraphQL requests
_ra_client: httpx.AsyncClient | None = None
_RA_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:106.0) Gecko/20100101 Firefox/106.0",
}

CITIES = cfg.cities()


_ra_client_lock = asyncio.Lock()


async def _get_ra_client() -> httpx.AsyncClient:
    global _ra_client
    async with _ra_client_lock:
        if _ra_client is None or _ra_client.is_closed:
            _ra_client = httpx.AsyncClient(
                headers=_RA_HEADERS,
                timeout=15.0,
                follow_redirects=True,
            )
        return _ra_client


async def close_clients() -> None:
    """Close all async HTTP clients. Call on shutdown."""
    global _ra_client
    if _ra_client is not None:
        await _ra_client.aclose()
        _ra_client = None

    from . import bandcamp, club_scrapers, discogs, sc

    await sc.close_client()
    await discogs.close_client()
    await bandcamp.close_client()
    await club_scrapers.close_client()


@dataclasses.dataclass(frozen=True)
class ScanContext:
    """Immutable scan parameters — replaces mutable module globals."""

    area: int
    city_name: str
    city_slug: str
    start_date: datetime
    days_ahead: int

    @property
    def end_date(self) -> datetime:
        return self.start_date + timedelta(days=self.days_ahead)


@dataclasses.dataclass
class IncrementalPlan:
    """Result of incremental scan analysis — which artists need enrichment."""

    unique_artist_ids: list[str | int]
    all_artist_ids: list[str | int]
    unchanged_eids: set[str]
    event_lineup_map: dict[str, dict[str, Any]]


async def get_artist_urls(artist_id: str | int) -> dict[str, Any] | None:
    """Return {id, name, soundcloud, discogs, bandcamp, ra_followers} from SQLite cache or RA GraphQL."""
    key = str(artist_id)
    cached = store.get_artist_url(key)
    if cached is not None:
        return dict(cached)

    data = await _fetch_ra_artist(artist_id)
    if data is None:
        return None
    entry = {
        "id": data.get("id"),
        "name": data.get("name"),
        "soundcloud": data.get("soundcloud"),
        "discogs": data.get("discogs"),
        "bandcamp": data.get("bandcamp"),
        "contentUrl": data.get("contentUrl"),
        "country": data.get("country"),
        "ra_followers": data.get("followerCount"),
    }

    store.save_artist_url(key, entry)
    return entry


@async_retry_on_failure(max_retries=2, base_delay=1.0)
async def _fetch_ra_artist(artist_id: str | int) -> dict[str, Any] | None:
    """Fetch single artist from RA GraphQL with retry."""
    client = await _get_ra_client()
    r = await client.post(URL, headers={"Referer": RA}, json=get_artist_payload_by_id(artist_id))
    r.raise_for_status()
    result: dict[str, Any] | None = r.json()["data"]["artist"]
    return result


class EventFetcher:
    """Fetch event details from RA.co (async)."""

    def __init__(self, referer: str, areas: int, listing_date_gte: str, listing_date_lte: str) -> None:
        self.referer = referer
        self.payload = self.generate_payload(areas, listing_date_gte, listing_date_lte)

    @staticmethod
    def generate_payload(areas: int, listing_date_gte: str, listing_date_lte: str) -> dict[str, Any]:
        return get_event_listings_payload(areas, listing_date_gte, listing_date_lte)

    async def get_events(self, page_number: int) -> list[dict[str, Any]]:
        self.payload["variables"]["page"] = page_number
        try:
            data = await self._fetch_page()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"RA API error: {e}")
            return []

        if "data" not in data:
            logger.warning(f"RA API unexpected response: {data}")
            return []

        events: list[dict[str, Any]] = data["data"]["eventListings"]["data"]
        return events

    @async_retry_on_failure(max_retries=2, base_delay=1.0)
    async def _fetch_page(self) -> dict[str, Any]:
        """POST to RA GraphQL with retry on transient failures."""
        client = await _get_ra_client()
        r = await client.post(URL, headers={"Referer": self.referer}, json=self.payload)
        r.raise_for_status()
        resp: dict[str, Any] = r.json()
        return resp

    async def fetch_all_events(self) -> list[dict[str, Any]]:
        all_events: list[dict[str, Any]] = []
        page_number = 1

        while True:
            events = await self.get_events(page_number)

            if not events:
                break

            all_events.extend(events)
            page_number += 1
            await asyncio.sleep(DELAY)

        return all_events


def parse_events_list(events_list: list[dict[str, Any]]) -> pd.DataFrame:
    flattened_data = []

    for event in events_list:
        flat_event = {
            "listing_id": event["id"],
            "listing_date": event["listingDate"],
            "event_id": event["event"]["id"],
            "event_date": event["event"]["date"],
            "start_time": event["event"]["startTime"],
            "end_time": event["event"]["endTime"],
            "title": event["event"]["title"],
            "content_url": event["event"]["contentUrl"],
            "event_url": RA + event["event"]["contentUrl"],
            "is_ticketed": event["event"]["isTicketed"],
            "attending": event["event"]["attending"],
            "venue_id": event["event"]["venue"]["id"],
            "venue_name": event["event"]["venue"]["name"],
            "venue_url": event["event"]["venue"]["contentUrl"],
            "images": event["event"]["images"],
            "artists": event["event"]["artists"],
            "promoters": event["event"]["promoters"],
            "tickets": event["event"]["tickets"],
            "genres": event["event"]["genres"],
        }

        flattened_data.append(flat_event)

    if not flattened_data:
        return pd.DataFrame(
            columns=[
                "listing_id",
                "listing_date",
                "event_id",
                "event_date",
                "start_time",
                "end_time",
                "title",
                "content_url",
                "event_url",
                "is_ticketed",
                "attending",
                "venue_id",
                "venue_name",
                "venue_url",
                "images",
                "artists",
                "promoters",
                "tickets",
                "genres",
            ]
        )

    df = pd.DataFrame(flattened_data)
    datetime_columns = ["listing_date", "event_date", "start_time", "end_time"]
    for col in datetime_columns:
        df[col] = pd.to_datetime(df[col])

    return df


async def _fetch_and_dedup_events(
    ctx: ScanContext,
    _cb: Callable[[str, str, float], None],
) -> pd.DataFrame:
    """Fetch RA events, deduplicate by event ID, and parse into a DataFrame."""
    start_date = ctx.start_date.strftime("%Y-%m-%d")
    end_date = ctx.end_date.strftime("%Y-%m-%d")

    areas = ctx.area

    listing_date_gte = f"{start_date}T00:00:00.000Z"
    listing_date_lte = f"{end_date}T23:59:59.999Z"

    referer = RA + "/events/" + ctx.city_slug
    event_fetcher = EventFetcher(referer, areas, listing_date_gte, listing_date_lte)
    all_events = await event_fetcher.fetch_all_events()

    # Deduplicate by event ID
    seen_event_ids: set[str] = set()
    unique_events = []
    for ev in all_events:
        eid = ev.get("event", {}).get("id")
        if eid and eid not in seen_event_ids:
            seen_event_ids.add(eid)
            unique_events.append(ev)
    if len(unique_events) < len(all_events):
        logger.info(f"Deduped {len(all_events) - len(unique_events)} duplicate RA events")
    all_events = unique_events

    df = parse_events_list(all_events)
    return df


def _build_incremental_plan(df: pd.DataFrame, ctx: ScanContext) -> IncrementalPlan:
    """Log events, build incremental snapshot, and collect artist IDs to enrich."""
    incremental_enabled = cfg.incremental()
    prev_snapshot: dict[str, str] = {}
    event_lineup_map: dict[str, dict[str, Any]] = {}
    if incremental_enabled and not df.empty:
        prev_snapshot = store.get_scan_snapshot(ctx.city_name)

    seen_ids: set[str | int] = set()
    unique_artist_ids: list[str | int] = []
    all_artist_ids: list[str | int] = []
    unchanged_eids: set[str] = set()

    for _, row in df.iterrows():
        logger.info(f"{row['event_date']} {row['title']} {RA}{row['content_url']}")

        eid = str(row["event_id"])

        if incremental_enabled and not df.empty:
            artist_ids_sorted = sorted(str(a["id"]) for a in row["artists"])
            lineup_hash = hashlib.sha256(json.dumps(artist_ids_sorted).encode()).hexdigest()
            event_lineup_map[eid] = {
                "artist_ids": artist_ids_sorted,
                "lineup_hash": lineup_hash,
            }
            if prev_snapshot and eid in prev_snapshot and prev_snapshot[eid] == lineup_hash:
                unchanged_eids.add(eid)

        for artist in row["artists"]:
            aid = artist["id"]
            if aid not in seen_ids:
                seen_ids.add(aid)
                all_artist_ids.append(aid)
                if eid not in unchanged_eids or not store.has_cached_artist(str(aid)):
                    unique_artist_ids.append(aid)

    if unchanged_eids:
        total_artists = len(all_artist_ids)
        skipped = total_artists - len(unique_artist_ids)
        logger.info(
            f"Incremental: {len(unchanged_eids)} unchanged events, "
            f"{len(event_lineup_map) - len(unchanged_eids)} new/changed — "
            f"enriching {len(unique_artist_ids)} of {total_artists} artists (skipping {skipped})"
        )
    else:
        if incremental_enabled and prev_snapshot:
            logger.info(f"Incremental: all {len(event_lineup_map)} events are new or changed")
        elif incremental_enabled:
            logger.info("Incremental: first scan for this city, full enrichment")

    return IncrementalPlan(
        unique_artist_ids=unique_artist_ids,
        all_artist_ids=all_artist_ids,
        unchanged_eids=unchanged_eids,
        event_lineup_map=event_lineup_map,
    )


async def _merge_club_events(
    df: pd.DataFrame,
    ctx: ScanContext,
    artist_lookup: dict[str | int, ArtistInfo],
    _cb: Callable[[str, str, float], None],
    stats: ScanStats | None = None,
) -> pd.DataFrame:
    """Scrape club websites, enrich stubs, merge with RA events, and deduplicate."""
    _ra_by_name = {_norm_artist_name(info.get("name")): info for info in artist_lookup.values()}

    _cb("clubs", "Scraping club websites...", 0.70)
    club_events = await scrape_city_clubs(ctx.city_name, ctx.start_date, ctx.end_date)
    if not club_events:
        return df

    seen_stub_ids: set[str] = set()
    unique_stubs: list[ArtistInfo] = []
    for ev in club_events:
        for a in ev.get("_prefilled_artists_info", []):
            if a["id"] not in seen_stub_ids:
                seen_stub_ids.add(a["id"])
                unique_stubs.append(a)

    logger.info(f"Enriching {len(unique_stubs)} club artists (phased pipeline)...")
    stub_lookup = await enrich_club_batch_phased(unique_stubs, stats=stats)

    for ev in club_events:
        ev["_prefilled_artists_info"] = [
            _merge_ra_into_stub(
                {
                    **stub_lookup.get(a["id"], a),
                    "floor": a.get("floor"),
                    **({} if not a.get("country") else {"country": a["country"]}),
                },
                _ra_by_name,
            )
            for a in ev["_prefilled_artists_info"]
        ]

    club_df = pd.DataFrame(club_events)
    artists_info_col = club_df.pop("_prefilled_artists_info")
    club_df["artists_info"] = list(artists_info_col)
    club_df["artists_list_info_past"] = [[] for _ in range(len(club_df))]
    club_flyer_urls = [get_flyer(row.to_dict()) for _, row in club_df.iterrows()]
    club_df["flyer"] = await embed_flyers(club_flyer_urls)
    for col in ["listing_date", "event_date", "start_time", "end_time"]:
        club_df[col] = pd.to_datetime(club_df[col])

    club_names = {str(ev["venue_name"]).lower() for ev in club_events}
    club_dates = {pd.Timestamp(ev["event_date"]).date() for ev in club_events}

    def _is_club_duplicate(ra_row: Any) -> bool:
        ra_date = pd.Timestamp(ra_row["event_date"]).date()
        if ra_date not in club_dates:
            return False
        ra_venue = str(ra_row["venue_name"]).lower()
        return any(cn in ra_venue for cn in club_names)

    for _idx, ra_row in df.iterrows():
        if not _is_club_duplicate(ra_row):
            continue
        ra_date = pd.Timestamp(ra_row["event_date"]).date()
        ra_flyer = ra_row.get("flyer")
        ra_attending = ra_row.get("attending", 0)
        ra_title = _normalize_alnum(str(ra_row.get("title", "")))

        best_ci = None
        for ci in club_df.index:
            c_date = pd.Timestamp(club_df.at[ci, "event_date"]).date()
            if c_date != ra_date:
                continue
            c_title = _normalize_alnum(str(club_df.at[ci, "title"]))
            if ra_title and c_title and (ra_title in c_title or c_title in ra_title):
                best_ci = ci
                break
            if best_ci is None:
                best_ci = ci

        if best_ci is not None:
            cur_flyer = club_df.at[best_ci, "flyer"]
            cur_attending = club_df.at[best_ci, "attending"]
            flyer_empty = cur_flyer is None or pd.isna(cur_flyer)
            if ra_flyer and flyer_empty:
                club_df.at[best_ci, "flyer"] = ra_flyer
            if ra_attending and (not cur_attending or cur_attending == 0):
                club_df.at[best_ci, "attending"] = ra_attending

    before = len(df)
    df = df[~df.apply(_is_club_duplicate, axis=1)]
    dropped = before - len(df)
    if dropped:
        logger.info(f"Dropped {dropped} RA duplicate(s) superseded by club-scraper events")

    df = pd.concat([df, club_df], ignore_index=True)
    logger.info(f"Added {len(club_df)} club-website events to {ctx.city_name} results")

    return df


async def get_data(
    ctx: ScanContext,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    stats: ScanStats | None = None,
) -> pd.DataFrame:
    def _cb(phase: str, detail: str, pct: float) -> None:
        if progress_cb:
            progress_cb({"phase": phase, "detail": detail, "pct": pct})

    df = await _fetch_and_dedup_events(ctx, _cb)

    if df.empty:
        logger.warning(f"No RA events found for {ctx.city_name} in the requested date range.")

    plan = _build_incremental_plan(df, ctx)

    logger.info(f"Enriching {len(plan.unique_artist_ids)} unique artists (phased pipeline)...")
    _cb("enrich", f"Enriching {len(plan.unique_artist_ids)} artists...", 0.10)

    artist_lookup = await enrich_batch_phased(
        plan.unique_artist_ids, get_artist_urls, progress_cb=progress_cb, pct_base=0.10, pct_range=0.55, stats=stats
    )

    # Load cached artists that were skipped by incremental mode
    skipped_ids = [aid for aid in plan.all_artist_ids if aid not in artist_lookup]
    if skipped_ids:
        loaded = 0
        for aid in skipped_ids:
            cached = get_cached_artist(aid)
            if cached is not None:
                artist_lookup[aid] = cached
                loaded += 1
        logger.info(f"Loaded {loaded} cached artists from unchanged events")

    # Re-enrich stale artists
    stale_ids = [aid for aid in plan.all_artist_ids if is_cache_stale(aid)]
    if stale_ids:
        logger.info(f"Re-enriching {len(stale_ids)} stale artists (cache > {CACHE_STALE_DAYS}d)...")
        for aid in stale_ids:
            store.delete_cached_artist(str(aid))
        refreshed = await enrich_batch_phased(stale_ids, get_artist_urls, stats=stats)
        artist_lookup.update(refreshed)

    flyer_urls = df.apply(lambda row: get_flyer(row.to_dict()), axis=1).tolist()
    flyer_task = asyncio.create_task(embed_flyers(flyer_urls))

    _compute_similarity(artist_lookup)
    _compute_label_affinity(artist_lookup)

    df["artists_info"] = df["artists"].apply(
        lambda artists: [artist_lookup[a["id"]] for a in artists if a["id"] in artist_lookup]
    )
    df["artists_list_info_past"] = [[] for _ in range(len(df))]
    df["flyer"] = await flyer_task

    # Save scan snapshot for incremental mode
    if cfg.incremental() and not df.empty:
        snapshot_rows = [
            {"event_id": eid, "artist_ids": info["artist_ids"], "lineup_hash": info["lineup_hash"]}
            for eid, info in plan.event_lineup_map.items()
        ]
        store.save_scan_snapshot(ctx.city_name, snapshot_rows)
        logger.debug(f"Saved scan snapshot: {len(snapshot_rows)} events for {ctx.city_name}")

    df = await _merge_club_events(df, ctx, artist_lookup, _cb, stats)

    df["city_name"] = ctx.city_name
    return df


def _record_enrichment_health(stats: ScanStats) -> None:
    """Bridge ScanStats counters into scraper_health table."""
    city = stats.city
    for source, ok, fail in [
        ("soundcloud", stats.sc_ok, stats.sc_fail),
        ("discogs", stats.dc_ok, stats.dc_fail),
        ("bandcamp", stats.bc_ok, stats.bc_fail),
    ]:
        total = ok + fail
        if total:
            status = "ok" if fail == 0 else ("degraded" if ok > 0 else "error")
            store.record_scraper_health(
                source, city=city, status=status, events_found=ok, error_msg=f"{fail} failures" if fail else ""
            )
    store.record_scraper_health("ra", city=city, status="ok", events_found=stats.ra_events_fetched)


async def run_for_city(
    city_key: str,
    start_date: datetime,
    days_ahead: int,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    *,
    full: bool = False,
    on_sorted_df: Callable[[pd.DataFrame], None] | None = None,
) -> dict[str, Any]:
    """Run the full pipeline for one city (async).

    Args:
        full: Clear scan snapshot before fetching (forces full re-enrichment).
        on_sorted_df: Called with the scored DataFrame before HTML generation.
    """
    area, city_name, city_slug = CITIES[city_key]
    ctx = ScanContext(
        area=area,
        city_name=city_name,
        city_slug=city_slug,
        start_date=start_date,
        days_ahead=days_ahead,
    )

    if full:
        store.clear_scan_snapshot(city_name)

    def _cb(phase: str, detail: str = "", pct: float = 0.0) -> None:
        if progress_cb:
            progress_cb({"phase": phase, "detail": detail, "pct": pct})

    logger.info(
        f"--- Fetching {ctx.city_name} from {ctx.start_date.strftime('%Y-%m-%d')} for {ctx.days_ahead} days ---"
    )

    stats = ScanStats(city=ctx.city_name)
    stats.start()

    try:
        _cb("fetch_ra", "Fetching RA events...", 0.05)
        df = await get_data(ctx, progress_cb=progress_cb, stats=stats)

        stats.ra_events_fetched = len(df)

        _cb("filter", "Filtering & scoring...", 0.85)
        _find_and_record(df, ctx.city_name)
        filtered_data = filter_df(df)
        sorted_data = sort_df(filtered_data)
        sorted_data["city_name"] = city_name
        stats.events_after_filter = len(sorted_data)

        if on_sorted_df:
            on_sorted_df(sorted_data)

        _cb("report", "Generating HTML report...", 0.92)
        stats.finish()
        _record_enrichment_health(stats)

        stale = store.get_stale_scrapers()
        for s in stale:
            logger.warning(
                f"Scraper '{s['source']}' ({s['city']}) has not returned events "
                f"in {s['days_since']} days — may be broken"
            )
        html_res = create_html(
            sorted_data, stats_html=stats.to_html_footer(), scraper_health=store.get_all_scraper_health()
        )

        safe_city = re.sub(r"[^a-zA-Z0-9_-]", "_", ctx.city_name)
        out_dir = Path(OUTPUT_PATH)
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / f"{safe_city}_{ctx.start_date:%Y-%m-%d}_{ctx.days_ahead}d.html"
        file_path.write_text(html_res, encoding="utf-8")
        logger.info(f"Report saved: {file_path}")

        _cb("done", f"{len(sorted_data)} events found", 1.0)
        return {
            "city": ctx.city_name,
            "events": len(sorted_data),
            "followed": int(sorted_data.get("_score", pd.Series()).gt(500_000).sum()),
            "file_path": str(file_path),
        }
    except Exception as e:
        stats.record_error(str(e))
        stats.finish()
        logger.error(f"run_for_city failed for {ctx.city_name}:\n{traceback.format_exc()}")
        return {"city": ctx.city_name, "events": 0, "followed": 0, "file_path": None, "error": str(e)}


async def run_cities_parallel(
    city_keys: list[str],
    start_date: datetime,
    days_ahead: int,
    max_workers: int = 3,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    full: bool = False,
) -> list[dict[str, Any]]:
    """Run multiple cities concurrently using asyncio.gather + Semaphore.

    Shared rate-limited resources (Discogs lock, SC lock, BC semaphore)
    naturally serialize — no risk of exceeding API limits.
    SQLite WAL mode handles concurrent writes.
    """
    if full:
        for key in city_keys:
            store.clear_scan_snapshot(CITIES[key][1])

    total = len(city_keys)
    sem = asyncio.Semaphore(max_workers)

    async def _run(idx: int, key: str) -> dict[str, Any]:
        def _city_cb(msg: dict[str, Any]) -> None:
            if progress_cb:
                msg["city"] = CITIES[key][1]
                msg["city_idx"] = idx
                msg["city_total"] = total
                progress_cb(msg)

        async with sem:
            return await run_for_city(key, start_date, days_ahead, progress_cb=_city_cb)

    logger.info(f"Running {total} cities with {min(max_workers, total)} concurrent")

    results = await asyncio.gather(*[_run(i, key) for i, key in enumerate(city_keys)])

    for result in results:
        logger.info(f"Completed {result['city']}: {result['events']} events")

    return list(results)


# ---------------------------------------------------------------------------
# Sync wrappers for CLI / GUI
# ---------------------------------------------------------------------------


def run_for_city_sync(
    city_key: str,
    start_date: datetime,
    days_ahead: int,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    *,
    full: bool = False,
    on_sorted_df: Callable[[pd.DataFrame], None] | None = None,
) -> dict[str, Any]:
    """Sync wrapper for run_for_city — used by CLI and GUI."""

    async def _main() -> dict[str, Any]:
        try:
            return await run_for_city(
                city_key, start_date, days_ahead, progress_cb, full=full, on_sorted_df=on_sorted_df
            )
        finally:
            await close_clients()

    return asyncio.run(_main())


def run_cities_parallel_sync(
    city_keys: list[str],
    start_date: datetime,
    days_ahead: int,
    max_workers: int = 3,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    full: bool = False,
) -> list[dict[str, Any]]:
    """Sync wrapper for run_cities_parallel — used by CLI and GUI."""

    async def _main() -> list[dict[str, Any]]:
        try:
            return await run_cities_parallel(city_keys, start_date, days_ahead, max_workers, progress_cb, full)
        finally:
            await close_clients()

    return asyncio.run(_main())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch RA.co events for one or more cities")
    parser.add_argument(
        "--cities",
        nargs="+",
        default=["amsterdam"],
        choices=CITIES.keys(),
        help="One or more cities to fetch (default: amsterdam)",
    )
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=7, help="Number of days to fetch (default: 7)")
    parser.add_argument("--full", action="store_true", help="Force full re-scan, ignoring incremental cache")
    parser.add_argument("--parallel", type=int, default=1, help="Number of cities to scan concurrently (default: 1)")
    parser.add_argument("--verbose", action="store_true", help="Print top events with score breakdown after scan")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d") if args.start else datetime.now()

    store.migrate_if_needed()
    cleanup_cache()

    def _print_breakdown(df: pd.DataFrame) -> None:
        if not args.verbose or df.empty:
            return
        top = df.head(10)
        print(f"\n{'─' * 80}")
        print(f"  TOP {len(top)} EVENTS — SCORE BREAKDOWN")
        print(f"{'─' * 80}")
        for _, row in top.iterrows():
            score = row.get("_score", 0)
            pct = row.get("_match_pct", 0)
            title = row.get("title", "?")
            venue = row.get("venue_name", "?")
            date = row.get("event_date")
            date_str = date.strftime("%b %d") if hasattr(date, "strftime") else str(date)[:10]
            print(f"\n  {date_str}  {title}  @{venue}")
            print(f"  Score: {score:,.0f}  ({pct}% match)")
            bd = row.get("_score_breakdown", {})
            if bd:
                parts = sorted(bd.items(), key=lambda x: x[1], reverse=True)
                for key, val in parts:
                    if val:
                        print(f"    {key:<20s} {val:>10,.0f}")
            briefing = row.get("_briefing", [])
            if briefing:
                print(f"  Why: {' · '.join(briefing)}")
        print(f"{'─' * 80}\n")

    with keep.running():
        if args.parallel > 1 and len(args.cities) > 1:
            results = run_cities_parallel_sync(
                args.cities, start_date, args.days, max_workers=args.parallel, full=args.full
            )
            for r in results:
                status = f"{r['events']} events" if not r.get("error") else f"ERROR: {r['error']}"
                logger.info(f"  {r['city']}: {status}")
        else:
            for city in args.cities:
                run_for_city_sync(city, start_date, args.days, full=args.full, on_sorted_df=_print_breakdown)
