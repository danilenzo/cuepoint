from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
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

# Extracted modules
from .enrichment import (
    CACHE_STALE_DAYS,
    cleanup_cache,
    enrich_batch_phased,
    enrich_club_batch_phased,
    is_cache_stale,
)
from .flyers import get_flyer
from .fuzzy_match import _merge_ra_into_stub, _norm_artist_name
from .generic import BASE_PATH, OUTPUT_PATH, RA, URL
from .html_creator import create_html
from .http_utils import retry_on_failure
from .payloads import get_artist_payload_by_id
from .scoring import filter_df, sort_df
from .scoring import find_and_record as _find_and_record
from .stats import ScanStats

QUERY_TEMPLATE_PATH = str(BASE_PATH / "external_libs/resident-advisor-events-scraper-main/graphql_query_template.json")
DELAY = cfg.ra_request_delay()

# Cache the GraphQL template once at import time — avoids repeated disk reads
with open(QUERY_TEMPLATE_PATH) as _f:
    _QUERY_TEMPLATE = json.load(_f)

# Shared session for RA GraphQL requests — reuses TCP connections
_ra_session = requests.Session()
_ra_session.headers.update(
    {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:106.0) Gecko/20100101 Firefox/106.0",
    }
)

CITIES = cfg.cities()


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


def get_artist_urls(artist_id: str | int) -> dict[str, Any] | None:
    """Return {id, name, soundcloud, discogs, bandcamp, ra_followers} from SQLite cache or RA GraphQL."""
    key = str(artist_id)
    cached = store.get_artist_url(key)
    if cached is not None:
        return dict(cached)

    # Not cached — fetch from RA
    data = _fetch_ra_artist(artist_id)
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


@retry_on_failure(max_retries=2, base_delay=1.0)
def _fetch_ra_artist(artist_id: str | int) -> dict[str, Any] | None:
    """Fetch single artist from RA GraphQL with retry."""
    r = _ra_session.post(URL, headers={"Referer": RA}, json=get_artist_payload_by_id(artist_id), timeout=15)
    r.raise_for_status()
    return r.json()["data"]["artist"]  # type: ignore[no-any-return]


class EventFetcher:
    """
    A class to fetch and print event details from RA.co
    """

    def __init__(self, referer: str, areas: int, listing_date_gte: str, listing_date_lte: str) -> None:
        self.referer = referer
        self.payload = self.generate_payload(areas, listing_date_gte, listing_date_lte)

    @staticmethod
    def generate_payload(areas: int, listing_date_gte: str, listing_date_lte: str) -> dict[str, Any]:
        """
        Generate the payload for the GraphQL request.

        :param areas: The area code to filter events.
        :param listing_date_gte: The start date for event listings (inclusive).
        :param listing_date_lte: The end date for event listings (inclusive).
        :return: The generated payload.
        """
        import copy

        payload: dict[str, Any] = copy.deepcopy(_QUERY_TEMPLATE)

        payload["variables"]["filters"]["areas"]["eq"] = areas
        payload["variables"]["filters"]["listingDate"]["gte"] = listing_date_gte
        payload["variables"]["filters"]["listingDate"]["lte"] = listing_date_lte

        return payload

    def get_events(self, page_number: int) -> list[dict[str, Any]]:
        """
        Fetch events for the given page number.

        :param page_number: The page number for event listings.
        :return: A list of events.
        """
        self.payload["variables"]["page"] = page_number
        try:
            data = self._fetch_page()
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"RA API error: {e}")
            return []

        if "data" not in data:
            logger.warning(f"RA API unexpected response: {data}")
            return []

        return data["data"]["eventListings"]["data"]  # type: ignore[no-any-return]

    @retry_on_failure(max_retries=2, base_delay=1.0)
    def _fetch_page(self) -> dict[str, Any]:
        """POST to RA GraphQL with retry on transient failures."""
        r = _ra_session.post(URL, headers={"Referer": self.referer}, json=self.payload, timeout=15)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    def fetch_all_events(self) -> list[dict[str, Any]]:
        """
        Fetch all events and return them as a list.

        :return: A list of all events.
        """
        all_events: list[dict[str, Any]] = []
        page_number = 1

        while True:
            events = self.get_events(page_number)

            if not events:
                break

            all_events.extend(events)
            page_number += 1
            time.sleep(DELAY)

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

    # Convert datetime strings to datetime objects
    datetime_columns = ["listing_date", "event_date", "start_time", "end_time"]
    for col in datetime_columns:
        df[col] = pd.to_datetime(df[col])

    return df


def _enrich_batch_phased(
    artist_ids: list[str | int],
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    pct_base: float = 0.1,
    pct_range: float = 0.6,
) -> dict[str | int, dict[str, Any]]:
    """Delegate to enrichment module."""
    return enrich_batch_phased(artist_ids, get_artist_urls, progress_cb, pct_base, pct_range)


def _enrich_club_batch_phased(stubs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Delegate to enrichment module."""
    return enrich_club_batch_phased(stubs)


def get_data(ctx: ScanContext, progress_cb: Callable[[dict[str, Any]], None] | None = None) -> pd.DataFrame:
    def _cb(phase: str, detail: str, pct: float) -> None:
        if progress_cb:
            progress_cb({"phase": phase, "detail": detail, "pct": pct})

    start_date = ctx.start_date.strftime("%Y-%m-%d")
    end_date = ctx.end_date.strftime("%Y-%m-%d")

    areas = ctx.area

    listing_date_gte = f"{start_date}T00:00:00.000Z"
    listing_date_lte = f"{end_date}T23:59:59.999Z"

    referer = RA + "/events/" + ctx.city_slug
    event_fetcher = EventFetcher(referer, areas, listing_date_gte, listing_date_lte)
    all_events = event_fetcher.fetch_all_events()

    # Deduplicate by event ID (RA may return duplicates across pages)
    seen_event_ids = set()
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

    if df.empty:
        logger.warning(f"No RA events found for {ctx.city_name} in the requested date range.")

    # --- Single pass: log events, build incremental snapshot, collect artist IDs ---
    incremental_enabled = cfg.incremental()
    prev_snapshot = {}
    event_lineup_map = {}  # {event_id: sorted artist_ids list}
    if incremental_enabled and not df.empty:
        prev_snapshot = store.get_scan_snapshot(ctx.city_name)

    seen_ids: set[str | int] = set()
    unique_artist_ids = []
    all_artist_ids = []  # for stale-refresh later
    unchanged_eids = set()

    for _, row in df.iterrows():
        # Log event
        logger.info(f"{row['event_date']} {row['title']} {RA}{row['content_url']}")

        eid = str(row["event_id"])

        # Build lineup hash for incremental mode
        if incremental_enabled and not df.empty:
            artist_ids_sorted = sorted(str(a["id"]) for a in row["artists"])
            lineup_hash = hashlib.sha256(json.dumps(artist_ids_sorted).encode()).hexdigest()
            event_lineup_map[eid] = {
                "artist_ids": artist_ids_sorted,
                "lineup_hash": lineup_hash,
            }
            if prev_snapshot and eid in prev_snapshot and prev_snapshot[eid] == lineup_hash:
                unchanged_eids.add(eid)

        # Collect artist IDs
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

    logger.info(f"Enriching {len(unique_artist_ids)} unique artists (phased pipeline)...")
    _cb("enrich", f"Enriching {len(unique_artist_ids)} artists...", 0.10)

    artist_lookup = _enrich_batch_phased(unique_artist_ids, progress_cb=progress_cb, pct_base=0.10, pct_range=0.55)

    # Re-enrich stale artists that appeared in this scan (soft TTL refresh)
    stale_ids = [aid for aid in all_artist_ids if is_cache_stale(aid)]
    if stale_ids:
        logger.info(f"Re-enriching {len(stale_ids)} stale artists (cache > {CACHE_STALE_DAYS}d)...")
        for aid in stale_ids:
            store.delete_cached_artist(str(aid))
        refreshed = _enrich_batch_phased(stale_ids)
        artist_lookup.update(refreshed)

    _compute_similarity(artist_lookup)
    _compute_label_affinity(artist_lookup)

    df["artists_info"] = [
        [artist_lookup[a["id"]] for a in row["artists"] if a["id"] in artist_lookup] for _, row in df.iterrows()
    ]
    df["artists_list_info_past"] = [[] for _ in range(len(df))]
    df["flyer"] = [get_flyer(row.to_dict()) for _, row in df.iterrows()]

    # Save scan snapshot for incremental mode
    if incremental_enabled and not df.empty:
        snapshot_rows = [
            {"event_id": eid, "artist_ids": info["artist_ids"], "lineup_hash": info["lineup_hash"]}
            for eid, info in event_lineup_map.items()
        ]
        store.save_scan_snapshot(ctx.city_name, snapshot_rows)
        logger.debug(f"Saved scan snapshot: {len(snapshot_rows)} events for {ctx.city_name}")

    _ra_by_name = {_norm_artist_name(info.get("name")): info for info in artist_lookup.values()}

    # Append events scraped directly from club websites (registry-based)
    _cb("clubs", "Scraping club websites...", 0.70)
    club_events = scrape_city_clubs(ctx.city_name, ctx.start_date, ctx.end_date)
    if club_events:
        # Collect unique stub artists across all club events
        seen_stub_ids: set[str] = set()
        unique_stubs: list[dict[str, Any]] = []
        for ev in club_events:
            for a in ev.get("_prefilled_artists_info", []):
                if a["id"] not in seen_stub_ids:
                    seen_stub_ids.add(a["id"])
                    unique_stubs.append(a)

        logger.info(f"Enriching {len(unique_stubs)} club artists (phased pipeline)...")
        stub_lookup = _enrich_club_batch_phased(unique_stubs)

        # Rebuild prefilled artists: enriched cache first, then merge RA data for
        # name-matched artists, then always restore floor/country from fresh stub.
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
        club_df["flyer"] = [get_flyer(row.to_dict()) for _, row in club_df.iterrows()]
        for col in ["listing_date", "event_date", "start_time", "end_time"]:
            club_df[col] = pd.to_datetime(club_df[col])

        # Drop RA rows for any venue+date already covered by the club scraper.
        # RA venue names often include multiple rooms (e.g. "Berghain | Panorama Bar | Säule")
        # so we check if any club venue name is *contained* in the RA venue name.
        club_names = {str(ev["venue_name"]).lower() for ev in club_events}
        club_dates = {pd.Timestamp(ev["event_date"]).date() for ev in club_events}

        def _is_club_duplicate(ra_row: Any) -> bool:
            ra_date = pd.Timestamp(ra_row["event_date"]).date()
            if ra_date not in club_dates:
                return False
            ra_venue = str(ra_row["venue_name"]).lower()
            return any(cn in ra_venue for cn in club_names)

        # Before dropping, steal flyer + attending from RA rows into club events.
        # Match by date + fuzzy title (RA title lowercased contained in club title or vice versa).
        def _normalize(s: Any) -> str:
            return re.sub(r"[^a-z0-9]", "", str(s).lower())

        for _idx, ra_row in df.iterrows():
            if not _is_club_duplicate(ra_row):
                continue
            ra_date = pd.Timestamp(ra_row["event_date"]).date()
            ra_flyer = ra_row.get("flyer")
            ra_attending = ra_row.get("attending", 0)
            ra_title = _normalize(ra_row.get("title", ""))

            best_ci = None
            for ci in club_df.index:
                c_date = pd.Timestamp(club_df.at[ci, "event_date"]).date()
                if c_date != ra_date:
                    continue
                c_title = _normalize(club_df.at[ci, "title"])
                # Prefer title match; fall back to first same-date event
                if ra_title and c_title and (ra_title in c_title or c_title in ra_title):
                    best_ci = ci
                    break
                if best_ci is None:
                    best_ci = ci

            if best_ci is not None:
                cur_flyer = club_df.at[best_ci, "flyer"]
                cur_attending = club_df.at[best_ci, "attending"]
                # NaN check: pandas stores None as NaN (float) in mixed columns
                flyer_empty = cur_flyer is None or (isinstance(cur_flyer, float) and cur_flyer != cur_flyer)
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

    df["city_name"] = ctx.city_name
    return df


def run_for_city(
    city_key: str, start_date: datetime, days_ahead: int, progress_cb: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Run the full pipeline for one city.

    progress_cb, if provided, is called with a dict:
        {"phase": str, "detail": str, "pct": float 0-1}
    """
    area, city_name, city_slug = CITIES[city_key]
    ctx = ScanContext(
        area=area,
        city_name=city_name,
        city_slug=city_slug,
        start_date=start_date,
        days_ahead=days_ahead,
    )

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
        with keep.running():
            df = get_data(ctx, progress_cb=progress_cb)

        stats.ra_events_fetched = len(df)

        _cb("filter", "Filtering & scoring...", 0.85)
        _find_and_record(df, ctx.city_name)
        filtered_data = filter_df(df)
        sorted_data = sort_df(filtered_data)
        stats.events_after_filter = len(sorted_data)

        _cb("report", "Generating HTML report...", 0.92)
        stats.finish()
        html_res = create_html(sorted_data, stats_html=stats.to_html_footer())

        file_path = (
            OUTPUT_PATH
            + ctx.city_name
            + " "
            + ctx.start_date.strftime("%Y-%m-%d")
            + " "
            + str(ctx.days_ahead)
            + ".html"
        )
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(html_res)
        logger.info(f"Report saved: {file_path}")

        _cb("done", f"{len(sorted_data)} events found", 1.0)
        return {
            "city": ctx.city_name,
            "events": len(sorted_data),
            "followed": int(sorted_data.get("_score", pd.Series()).gt(500_000).sum()),
            "file_path": file_path,
        }
    except Exception as e:
        stats.record_error(str(e))
        stats.finish()
        logger.error(f"run_for_city failed for {ctx.city_name}:\n{traceback.format_exc()}")
        return {"city": ctx.city_name, "events": 0, "followed": 0, "file_path": None, "error": str(e)}


def run_cities_parallel(
    city_keys: list[str],
    start_date: datetime,
    days_ahead: int,
    max_workers: int = 3,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    full: bool = False,
) -> list[dict[str, Any]]:
    """Run multiple cities in parallel using a thread pool.

    Shared rate-limited resources (Discogs lock, SC lock, BC semaphore)
    naturally serialize — no risk of exceeding API limits.
    SQLite WAL mode handles concurrent writes from different city threads.

    Returns list of result dicts from run_for_city().
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if full:
        for key in city_keys:
            store.clear_scan_snapshot(CITIES[key][1])

    results: list[dict[str, Any]] = []
    total = len(city_keys)

    def _run(idx: int, key: str) -> dict[str, Any]:
        def _city_cb(msg: dict[str, Any]) -> None:
            if progress_cb:
                msg["city"] = CITIES[key][1]
                msg["city_idx"] = idx
                msg["city_total"] = total
                progress_cb(msg)

        return run_for_city(key, start_date, days_ahead, progress_cb=_city_cb)

    effective_workers = min(max_workers, total)
    logger.info(f"Running {total} cities with {effective_workers} parallel workers")

    with ThreadPoolExecutor(max_workers=effective_workers) as pool:
        futures = {pool.submit(_run, i, key): key for i, key in enumerate(city_keys)}
        for future in as_completed(futures):
            key = futures[future]
            try:
                result = future.result()
                results.append(result)
                logger.info(f"Completed {result['city']}: {result['events']} events")
            except Exception as e:
                logger.error(f"City {key} failed: {e}")
                results.append({"city": CITIES[key][1], "events": 0, "followed": 0, "file_path": None, "error": str(e)})

    return results


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
    parser.add_argument("--parallel", type=int, default=1, help="Number of cities to scan in parallel (default: 1)")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d") if args.start else datetime.now()

    store.migrate_if_needed()
    cleanup_cache()

    if args.parallel > 1 and len(args.cities) > 1:
        results = run_cities_parallel(args.cities, start_date, args.days, max_workers=args.parallel, full=args.full)
        for r in results:
            status = f"{r['events']} events" if not r.get("error") else f"ERROR: {r['error']}"
            logger.info(f"  {r['city']}: {status}")
    else:
        for city in args.cities:
            if args.full:
                store.clear_scan_snapshot(CITIES[city][1])
            run_for_city(city, start_date, args.days)
