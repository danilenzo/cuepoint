"""
FastAPI layer for cuepoint.

Exposes the scan pipeline as a REST API:
    POST /scan           — start a scan for one or more cities (runs as background task)
    GET  /status         — list all scans and their status
    GET  /status/{id}    — get status of a specific scan
    GET  /results/{city} — latest scan results for a city as JSON
    GET  /results/{city}/export — export results as CSV
    GET  /health         — readiness check
    GET  /cities         — list available city keys

Run with:
    uvicorn cuepoint.api:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from . import db as store
from .enrichment import cleanup_cache
from .event_fetcher import CITIES, close_clients


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await close_clients()
    store.close_db()


app = FastAPI(
    title="cuepoint",
    description="Electronic music event scanner — fetches RA.co events, enriches artists via SoundCloud/Discogs/Bandcamp, filters and ranks by genre.",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# In-memory scan tracking
# ---------------------------------------------------------------------------

_scans: dict[str, dict[str, Any]] = {}
_scans_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task[None]] = set()

# ---------------------------------------------------------------------------
# Rate limiting (simple sliding window per IP)
# ---------------------------------------------------------------------------

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 5  # max POST /scan per window per IP
_rate_log: dict[str, list[float]] = {}
_rate_lock = asyncio.Lock()


async def _check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is allowed."""
    now = time.monotonic()
    async with _rate_lock:
        timestamps = _rate_log.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
        if len(timestamps) >= _RATE_LIMIT_MAX:
            _rate_log[client_ip] = timestamps
            return False
        timestamps.append(now)
        _rate_log[client_ip] = timestamps
        return True


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
    page: int
    page_size: int
    total_pages: int
    events: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    version: str
    db_ok: bool
    cities_loaded: int


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


async def _update_scan(scan_id: str, **fields: Any) -> None:
    """Thread-safe update of a scan record."""
    async with _scans_lock:
        if scan_id in _scans:
            _scans[scan_id].update(fields)


async def _run_scan(scan_id: str, req: ScanRequest) -> None:
    """Run scan pipeline as an async background task."""
    await _update_scan(scan_id, status="running", started_at=datetime.now().isoformat())

    try:
        store.migrate_if_needed()
        cleanup_cache()

        start_date = datetime.now()
        raw_results = []

        from .event_fetcher import ScanContext, get_data
        from .generic import OUTPUT_PATH
        from .html_creator import create_html
        from .scoring import filter_df, sort_df
        from .scoring import find_and_record as _find_and_record
        from .stats import ScanStats

        for city_key in req.cities:
            area, city_name, city_slug = CITIES[city_key]
            ctx = ScanContext(
                area=area,
                city_name=city_name,
                city_slug=city_slug,
                start_date=start_date,
                days_ahead=req.days,
            )

            if req.full:
                store.clear_scan_snapshot(city_name)

            stats = ScanStats(city=city_name)
            stats.start()

            try:
                df = await get_data(ctx)
                stats.ra_events_fetched = len(df)

                if not df.empty:
                    _find_and_record(df, city_name)
                    filtered = filter_df(df)
                    sorted_df = sort_df(filtered)
                    sorted_df["city_name"] = city_name
                    stats.events_after_filter = len(sorted_df)

                    events_json = _df_to_events(sorted_df)
                    store.save_api_results(city_name.lower(), events_json)

                    stats.finish()
                    html_res = create_html(sorted_df, stats_html=stats.to_html_footer())
                    file_path = (
                        OUTPUT_PATH + city_name + " " + start_date.strftime("%Y-%m-%d") + " " + str(req.days) + ".html"
                    )
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(html_res)
                    logger.info(f"Report saved: {file_path}")

                    raw_results.append(
                        {
                            "city": city_name,
                            "events": len(sorted_df),
                            "followed": int(sorted_df.get("_score", pd.Series()).gt(500_000).sum()),
                            "file_path": file_path,
                        }
                    )
                else:
                    stats.finish()
                    store.save_api_results(city_name.lower(), [])
                    raw_results.append({"city": city_name, "events": 0, "followed": 0, "file_path": None})

            except Exception as city_err:
                stats.record_error(str(city_err))
                stats.finish()
                logger.error(f"Scan failed for {city_name}: {city_err}")
                raw_results.append(
                    {
                        "city": city_name,
                        "events": 0,
                        "followed": 0,
                        "file_path": None,
                        "error": str(city_err),
                    }
                )

        await _update_scan(scan_id, status="completed", finished_at=datetime.now().isoformat(), results=raw_results)

    except Exception as e:
        logger.error(f"Scan {scan_id} failed: {e}")
        await _update_scan(scan_id, status="failed", finished_at=datetime.now().isoformat(), error=str(e))


def _resolve_city(city: str) -> tuple[str, str] | None:
    """Resolve a city key or name to (city_lower_key, display_name). Returns None if unknown."""
    city_lower = city.lower()
    for key, (_, name, _) in CITIES.items():
        if key == city_lower or name.lower() == city_lower:
            return name.lower(), name
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/")
async def root() -> dict[str, Any]:
    available_cities = list(CITIES.keys())
    return {
        "name": "cuepoint API",
        "version": "1.0.0",
        "endpoints": {
            "POST /scan": "Start a scan for one or more cities",
            "GET /status": "List all scans",
            "GET /status/{scan_id}": "Get scan status by ID",
            "GET /results/{city}": "Get latest results for a city",
            "GET /results/{city}/export": "Export results as CSV",
            "GET /health": "Readiness check",
            "GET /cities": "List available cities",
        },
        "cities": available_cities,
    }


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    db_ok = False
    try:
        store._get_conn().execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        pass
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version="1.0.0",
        db_ok=db_ok,
        cities_loaded=len(CITIES),
    )


@app.get("/cities")
async def list_cities() -> dict[str, list[str]]:
    return {"cities": list(CITIES.keys())}


@app.post("/scan", response_model=ScanResponse)
async def start_scan(req: ScanRequest, request: Request) -> ScanResponse:
    client_ip = request.client.host if request.client else "unknown"
    if not await _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {_RATE_LIMIT_MAX} scans per {_RATE_LIMIT_WINDOW}s.",
        )

    invalid = [c for c in req.cities if c not in CITIES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown cities: {invalid}. Available: {list(CITIES.keys())}",
        )

    scan_id = uuid.uuid4().hex[:12]
    async with _scans_lock:
        _scans[scan_id] = {
            "scan_id": scan_id,
            "status": "pending",
            "cities": req.cities,
            "started_at": None,
            "finished_at": None,
            "results": None,
            "error": None,
        }

    _background_tasks.add(task := asyncio.create_task(_run_scan(scan_id, req)))
    task.add_done_callback(_background_tasks.discard)

    return ScanResponse(
        scan_id=scan_id,
        status="pending",
        cities=req.cities,
        message=f"Scan started for {len(req.cities)} city/cities. Poll GET /status/{scan_id} for progress.",
    )


@app.get("/status", response_model=list[ScanStatusResponse])
async def list_scans() -> list[dict[str, Any]]:
    async with _scans_lock:
        return list(_scans.values())


@app.get("/status/{scan_id}", response_model=ScanStatusResponse)
async def get_scan_status(scan_id: str) -> dict[str, Any]:
    async with _scans_lock:
        scan = _scans.get(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")
    return scan


@app.get("/results/{city}", response_model=CityResultsResponse)
async def get_results(
    city: str,
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=50, ge=1, le=200, description="Events per page"),
) -> CityResultsResponse:
    resolved = _resolve_city(city)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown city '{city}'. Available: {list(CITIES.keys())}",
        )
    city_lower, city_display = resolved

    events = store.get_api_results(city_lower)
    if events is None:
        raise HTTPException(
            status_code=404,
            detail=f"No results for '{city_display}'. Run a scan first via POST /scan.",
        )

    total = len(events)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_events = events[start : start + page_size]

    return CityResultsResponse(
        city=city_display,
        event_count=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        events=page_events,
    )


@app.get("/results/{city}/export")
async def export_results(city: str) -> StreamingResponse:
    resolved = _resolve_city(city)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown city '{city}'. Available: {list(CITIES.keys())}",
        )
    city_lower, city_display = resolved

    events = store.get_api_results(city_lower)
    if events is None:
        raise HTTPException(
            status_code=404,
            detail=f"No results for '{city_display}'. Run a scan first via POST /scan.",
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["event_id", "title", "date", "venue", "attending", "score", "genres", "url"])
    for ev in events:
        writer.writerow(
            [
                ev.get("event_id", ""),
                ev.get("title", ""),
                ev.get("event_date", ""),
                ev.get("venue_name", ""),
                ev.get("attending", 0),
                ev.get("score", 0),
                "; ".join(ev.get("genres", [])),
                ev.get("event_url", ""),
            ]
        )

    output.seek(0)
    filename = f"{city_display.lower().replace(' ', '_')}_events.csv"
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
