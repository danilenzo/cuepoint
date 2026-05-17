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
import os
from pathlib import Path
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

from . import db as store
from .enrichment import cleanup_cache
from .event_fetcher import CITIES, close_clients

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("cuepoint")
except Exception:
    _VERSION = "0.0.0-dev"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Snapshot the set before iterating — done_callbacks run discard() on
    # the live set, which would mutate it while we loop over it.
    pending = list(_background_tasks)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    _background_tasks.clear()
    await close_clients()
    store.close_db()


app = FastAPI(
    title="cuepoint",
    description="Electronic music event scanner — fetches RA.co events, enriches artists via SoundCloud/Discogs/Bandcamp, filters and ranks by genre.",
    version=_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory scan tracking
# ---------------------------------------------------------------------------

_MAX_SCANS = 200
_SCAN_TTL = 3600  # 1 hour
_scans: dict[str, dict[str, Any]] = {}
_scans_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task[None]] = set()


def _evict_stale_scans() -> None:
    """Remove completed scans older than _SCAN_TTL, cap at _MAX_SCANS.

    Must be called while the caller already holds _scans_lock.
    Kept as a plain function (not async) so the caller controls locking
    granularity — call it only when the dict is already locked.
    """
    now = time.monotonic()
    stale = [
        sid
        for sid, s in _scans.items()
        if s.get("status") in ("done", "error", "completed", "failed")
        and now - s.get("_mono", now) > _SCAN_TTL
    ]
    for sid in stale:
        del _scans[sid]
    if len(_scans) > _MAX_SCANS:
        by_age = sorted(_scans, key=lambda sid: _scans[sid].get("_mono", 0))
        for sid in by_age[: len(_scans) - _MAX_SCANS]:
            del _scans[sid]


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
        timestamps = [t for t in _rate_log.get(client_ip, []) if now - t < _RATE_LIMIT_WINDOW]
        if len(timestamps) >= _RATE_LIMIT_MAX:
            _rate_log[client_ip] = timestamps
            return False
        timestamps.append(now)
        if timestamps:
            _rate_log[client_ip] = timestamps
        else:
            # No recent activity — remove the entry entirely to avoid unbounded growth
            _rate_log.pop(client_ip, None)
        return True


# ---------------------------------------------------------------------------
# API key authentication (optional — open access when CUEPOINT_API_KEY unset)
# ---------------------------------------------------------------------------

_API_KEY: str | None = os.environ.get("CUEPOINT_API_KEY")


async def _check_api_key(authorization: str | None = Header(default=None)) -> None:
    if _API_KEY is None:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    if authorization[7:] != _API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


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
    start_date: str | None = Field(default=None, description="Start date (YYYY-MM-DD). Defaults to today.")
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
    match_pct: int = 0
    briefing: list[str] = []
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
    for row in df.to_dict("records"):
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
                "match_pct": int(row.get("_match_pct", 0)),
                "briefing": list(row.get("_briefing", [])),
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

        from .event_fetcher import run_for_city

        if req.start_date:
            try:
                start_date = datetime.strptime(req.start_date, "%Y-%m-%d")
            except ValueError:
                start_date = datetime.now()
        else:
            start_date = datetime.now()
        raw_results = []

        for city_key in req.cities:
            city_name = CITIES[city_key][1]

            def _save_api_results(sorted_df: pd.DataFrame, _city: str = city_name) -> None:
                events_json = _df_to_events(sorted_df)
                store.save_api_results(_city.lower(), events_json)

            result = await run_for_city(
                city_key,
                start_date,
                req.days,
                full=req.full,
                on_sorted_df=_save_api_results,
            )
            if result.get("events", 0) == 0 and "error" not in result:
                store.save_api_results(city_name.lower(), [])
            raw_results.append(result)

        await _update_scan(scan_id, status="completed", finished_at=datetime.now().isoformat(), results=raw_results)

    except Exception as e:
        logger.error(f"Scan {scan_id} failed: {e}")
        await _update_scan(scan_id, status="failed", finished_at=datetime.now().isoformat(), error="Scan failed. Check server logs for details.")


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


_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

# Read index.html once at import time so GET / never touches the filesystem again.
_INDEX_HTML: str | None = None
_index_path = _STATIC_DIR / "index.html"
if _index_path.exists():
    _INDEX_HTML = _index_path.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def landing_page() -> HTMLResponse:
    if _INDEX_HTML is not None:
        return HTMLResponse(_INDEX_HTML)
    available_cities = list(CITIES.keys())
    return HTMLResponse(
        f"<pre>cuepoint API {_VERSION}\nCities: {available_cities}\nDocs: /docs</pre>"
    )


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    db_ok = store.check_db()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=_VERSION,
        db_ok=db_ok,
        cities_loaded=len(CITIES),
    )


@app.get("/cities")
async def list_cities() -> dict[str, list[str]]:
    return {"cities": list(CITIES.keys())}


@app.post("/reset-breaker", dependencies=[Depends(_check_api_key)])
async def reset_breaker() -> dict[str, str]:
    """Reset the SoundCloud circuit breaker without restarting the server."""
    from .sc import reset_circuit_breaker
    await reset_circuit_breaker()
    return {"status": "ok", "detail": "SC circuit breaker and rate limiter reset"}


@app.post("/scan", response_model=ScanResponse, dependencies=[Depends(_check_api_key)])
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
        _evict_stale_scans()
        _scans[scan_id] = {
            "scan_id": scan_id,
            "status": "pending",
            "cities": req.cities,
            "started_at": None,
            "finished_at": None,
            "results": None,
            "error": None,
            "_mono": time.monotonic(),
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
    if not re.fullmatch(r"[a-f0-9]{12}", scan_id):
        raise HTTPException(status_code=400, detail="Invalid scan ID format")
    async with _scans_lock:
        scan = _scans.get(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")
    return scan


@app.get("/results/{city}", response_model=CityResultsResponse, dependencies=[Depends(_check_api_key)])
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


@app.get("/results/{city}/export", dependencies=[Depends(_check_api_key)])
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
    safe_name = re.sub(r"[^a-z0-9_]", "_", city_display.lower())
    filename = f"{safe_name}_events.csv"
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


@app.get("/results/{city}/report", dependencies=[Depends(_check_api_key)])
async def export_html_report(city: str) -> HTMLResponse:
    """Serve the latest generated HTML report for a city."""
    resolved = _resolve_city(city)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown city '{city}'. Available: {list(CITIES.keys())}",
        )
    city_lower, city_display = resolved

    if not _OUTPUT_DIR.exists():
        raise HTTPException(status_code=404, detail="No reports generated yet. Run a scan first.")

    safe_city = re.sub(r"[^a-zA-Z0-9_-]", "_", city_display)
    matches = sorted(_OUTPUT_DIR.glob(f"{safe_city}_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"No HTML report for '{city_display}'. Run a scan first via POST /scan.",
        )

    report_html = matches[0].read_text(encoding="utf-8")
    filename = matches[0].name
    return HTMLResponse(
        content=report_html,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class SyncFollowingRequest(BaseModel):
    profile_url: str = Field(..., description="SoundCloud profile URL, e.g. https://soundcloud.com/username")


@app.post("/sync-following")
async def sync_following(req: SyncFollowingRequest) -> dict[str, Any]:
    """Sync followed artists from a SoundCloud profile."""
    url = req.profile_url.strip()
    if not re.match(r"https?://(www\.)?soundcloud\.com/[a-zA-Z0-9_-]+/?$", url):
        raise HTTPException(status_code=400, detail="Invalid SoundCloud profile URL.")

    try:
        from .fetch_following import fetch_following_slugs, update_following

        def _sync_all() -> int:
            slugs = fetch_following_slugs(url)
            update_following(slugs)
            return len(slugs)

        artists_synced = await asyncio.to_thread(_sync_all)
        return {"status": "ok", "artists_synced": artists_synced}
    except Exception as e:
        logger.error(f"Following sync failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to sync following list. SoundCloud may be rate-limiting.")
