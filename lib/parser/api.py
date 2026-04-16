"""
FastAPI layer for techno_scan.

Exposes the scan pipeline as a REST API:
    POST /scan           — start a scan for one or more cities (runs in background)
    GET  /status         — list all scans and their status
    GET  /status/{id}    — get status of a specific scan
    GET  /results/{city} — latest scan results for a city as JSON

Run with:
    cd lib/parser && uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

import db as store
from enrichment import cleanup_cache
from event_fetcher import CITIES

app = FastAPI(
    title="techno_scan",
    description="Electronic music event scanner — fetches RA.co events, enriches artists via SoundCloud/Discogs/Bandcamp, filters and ranks by genre.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# In-memory scan tracking
# ---------------------------------------------------------------------------

_scans: dict[str, dict[str, Any]] = {}
_scans_lock = threading.Lock()
# Latest results per city: {city_name: [event_dicts]}
_results: dict[str, list[dict[str, Any]]] = {}
_results_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    cities: list[str] = Field(
        ...,
        min_length=1,
        description="City keys to scan (e.g. ['berlin', 'london'])",
        json_schema_extra={"example": ["berlin", "amsterdam"]},
    )
    days: int = Field(default=7, ge=1, le=90, description="Number of days ahead to scan")
    parallel: int = Field(default=1, ge=1, le=8, description="Parallel worker count for multi-city scans")
    full: bool = Field(default=False, description="Force full re-scan, ignoring incremental cache")


class ScanResponse(BaseModel):
    scan_id: str
    status: str
    cities: list[str]
    message: str


class ScanStatusResponse(BaseModel):
    scan_id: str
    status: str  # pending | running | completed | failed
    cities: list[str]
    started_at: str | None = None
    finished_at: str | None = None
    results: list[dict[str, Any]] | None = None
    error: str | None = None


class EventResult(BaseModel):
    event_id: str | None = None
    title: str | None = None
    event_date: str | None = None
    start_time: str | None = None
    venue_name: str | None = None
    event_url: str | None = None
    attending: int | None = None
    score: float = 0.0
    lineup_notable: int = 0
    lineup_total: int = 0
    genres: list[str] = []
    artists: list[dict[str, Any]] = []
    flyer: str | None = None
    city: str | None = None


class CityResultsResponse(BaseModel):
    city: str
    event_count: int
    events: list[EventResult]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_artist(info: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the fields worth exposing from an enriched artist dict."""
    if info is None:
        return {}
    return {
        "name": info.get("name"),
        "soundcloud": info.get("soundcloud"),
        "sc_followers": info.get("sc_followers"),
        "discogs": info.get("discogs"),
        "dc_have": info.get("dc_have"),
        "dc_want": info.get("dc_want"),
        "bandcamp": info.get("bandcamp"),
        "bc_supporters": info.get("bc_supporters"),
        "country": info.get("country"),
        "tags": info.get("sc_tags") or info.get("dc_tags") or info.get("bc_tags") or [],
        "rising": bool(info.get("_rising")),
        "similarity_score": info.get("_similarity_score", 0),
        "floor": info.get("floor"),
    }


def _df_to_events(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a scored DataFrame to a list of JSON-serializable event dicts."""
    events: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        genres = []
        if isinstance(row.get("genres"), list):
            genres = [g["name"] if isinstance(g, dict) else str(g) for g in row["genres"]]

        artists = [_serialize_artist(a) for a in (row.get("artists_info") or []) if a]

        events.append(
            {
                "event_id": str(row.get("event_id", "")),
                "title": row.get("title"),
                "event_date": str(row.get("event_date", ""))[:10],
                "start_time": str(row.get("start_time", ""))[:16],
                "venue_name": row.get("venue_name"),
                "event_url": row.get("event_url"),
                "attending": int(row.get("attending", 0) or 0),
                "score": round(float(row.get("_score", 0)), 1),
                "lineup_notable": int(row.get("_lineup_notable", 0)),
                "lineup_total": int(row.get("_lineup_total", 0)),
                "genres": genres,
                "artists": artists,
                "flyer": row.get("flyer"),
                "city": row.get("city_name"),
            }
        )
    return events


def _update_scan(scan_id: str, **fields: Any) -> None:
    """Thread-safe update of a scan record. No-op if scan_id was removed."""
    with _scans_lock:
        if scan_id in _scans:
            _scans[scan_id].update(fields)


def _run_scan_with_capture(scan_id: str, req: ScanRequest) -> None:
    """Run scan pipeline in a background thread, capturing JSON results."""
    _update_scan(scan_id, status="running", started_at=datetime.now().isoformat())

    try:
        store.migrate_if_needed()
        cleanup_cache()

        start_date = datetime.now()
        raw_results = []

        from event_fetcher import ScanContext, get_data
        from generic import OUTPUT_PATH
        from html_creator import create_html
        from scoring import filter_df, sort_df
        from scoring import find_and_record as _find_and_record
        from stats import ScanStats

        for city_key in req.cities:
            area, city_name, city_slug = CITIES[city_key]
            ctx = ScanContext(
                area=area, city_name=city_name, city_slug=city_slug,
                start_date=start_date, days_ahead=req.days,
            )

            if req.full:
                store.clear_scan_snapshot(city_name)

            stats = ScanStats(city=city_name)
            stats.start()

            try:
                df = get_data(ctx)
                stats.ra_events_fetched = len(df)

                if not df.empty:
                    _find_and_record(df, city_name)
                    filtered = filter_df(df)
                    sorted_df = sort_df(filtered)
                    sorted_df["city_name"] = city_name
                    stats.events_after_filter = len(sorted_df)

                    # Capture JSON for API results
                    events_json = _df_to_events(sorted_df)
                    with _results_lock:
                        _results[city_name.lower()] = events_json

                    # Also generate the HTML report
                    stats.finish()
                    html_res = create_html(sorted_df, stats_html=stats.to_html_footer())
                    file_path = (
                        OUTPUT_PATH + city_name + " "
                        + start_date.strftime("%Y-%m-%d") + " "
                        + str(req.days) + ".html"
                    )
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(html_res)
                    logger.info(f"Report saved: {file_path}")

                    raw_results.append({
                        "city": city_name,
                        "events": len(sorted_df),
                        "followed": int(sorted_df.get("_score", pd.Series()).gt(500_000).sum()),
                        "file_path": file_path,
                    })
                else:
                    stats.finish()
                    with _results_lock:
                        _results[city_name.lower()] = []
                    raw_results.append({"city": city_name, "events": 0, "followed": 0, "file_path": None})

            except Exception as city_err:
                stats.record_error(str(city_err))
                stats.finish()
                logger.error(f"Scan failed for {city_name}: {city_err}")
                raw_results.append({
                    "city": city_name, "events": 0, "followed": 0,
                    "file_path": None, "error": str(city_err),
                })

        _update_scan(scan_id, status="completed", finished_at=datetime.now().isoformat(), results=raw_results)

    except Exception as e:
        logger.error(f"Scan {scan_id} failed: {e}")
        _update_scan(scan_id, status="failed", finished_at=datetime.now().isoformat(), error=str(e))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/")
def root() -> dict[str, Any]:
    available_cities = list(CITIES.keys())
    return {
        "name": "techno_scan API",
        "version": "1.0.0",
        "endpoints": {
            "POST /scan": "Start a scan for one or more cities",
            "GET /status": "List all scans",
            "GET /status/{scan_id}": "Get scan status by ID",
            "GET /results/{city}": "Get latest results for a city",
            "GET /cities": "List available cities",
        },
        "cities": available_cities,
    }


@app.get("/cities")
def list_cities() -> dict[str, list[str]]:
    return {"cities": list(CITIES.keys())}


@app.post("/scan", response_model=ScanResponse)
def start_scan(req: ScanRequest) -> ScanResponse:
    invalid = [c for c in req.cities if c not in CITIES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown cities: {invalid}. Available: {list(CITIES.keys())}",
        )

    scan_id = uuid.uuid4().hex[:12]
    with _scans_lock:
        _scans[scan_id] = {
            "scan_id": scan_id,
            "status": "pending",
            "cities": req.cities,
            "started_at": None,
            "finished_at": None,
            "results": None,
            "error": None,
        }

    thread = threading.Thread(target=_run_scan_with_capture, args=(scan_id, req), daemon=True)
    thread.start()

    return ScanResponse(
        scan_id=scan_id,
        status="pending",
        cities=req.cities,
        message=f"Scan started for {len(req.cities)} city/cities. Poll GET /status/{scan_id} for progress.",
    )


@app.get("/status", response_model=list[ScanStatusResponse])
def list_scans() -> list[dict[str, Any]]:
    with _scans_lock:
        return list(_scans.values())


@app.get("/status/{scan_id}", response_model=ScanStatusResponse)
def get_scan_status(scan_id: str) -> dict[str, Any]:
    with _scans_lock:
        scan = _scans.get(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")
    return scan


@app.get("/results/{city}", response_model=CityResultsResponse)
def get_results(city: str) -> CityResultsResponse:
    city_lower = city.lower()

    city_display = None
    for key, (_, name, _) in CITIES.items():
        if key == city_lower or name.lower() == city_lower:
            city_display = name
            city_lower = name.lower()
            break

    if city_display is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown city '{city}'. Available: {list(CITIES.keys())}",
        )

    with _results_lock:
        events = _results.get(city_lower)

    if events is None:
        raise HTTPException(
            status_code=404,
            detail=f"No results for '{city_display}'. Run a scan first via POST /scan.",
        )

    return CityResultsResponse(city=city_display, event_count=len(events), events=events)
