"""
Unified SQLite storage for techno_scan.

Replaces:
  - cache/_artist_urls.json   → artist_urls table
  - cache/{id}.json           → artist_cache table
  - db/Found                  → found_events table
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from loguru import logger

from generic import BASE_PATH

DB_PATH = BASE_PATH / "lib/parser/cache/techno_scan.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """One connection per thread, WAL mode for concurrent reads."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn  # type: ignore[no-any-return]


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS artist_urls (
            artist_id TEXT PRIMARY KEY,
            data      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artist_cache (
            artist_id TEXT PRIMARY KEY,
            data      TEXT NOT NULL,
            cached_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS found_events (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            line TEXT    UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artist_metrics_history (
            artist_id    TEXT PRIMARY KEY,
            sc_followers INTEGER,
            dc_want      INTEGER,
            recorded_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_events (
            city        TEXT NOT NULL,
            event_id    TEXT NOT NULL,
            artist_ids  TEXT NOT NULL,
            lineup_hash TEXT NOT NULL,
            scanned_at  TEXT NOT NULL,
            PRIMARY KEY (city, event_id)
        );

        CREATE INDEX IF NOT EXISTS idx_artist_cache_cached_at ON artist_cache(cached_at);
        CREATE INDEX IF NOT EXISTS idx_scan_events_city ON scan_events(city);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Artist URL cache (permanent — SC/Discogs URLs never change)
# ---------------------------------------------------------------------------


def get_artist_url(artist_id: str) -> dict[str, Any] | None:
    conn = _get_conn()
    row = conn.execute("SELECT data FROM artist_urls WHERE artist_id = ?", (str(artist_id),)).fetchone()
    if row:
        return json.loads(row["data"])  # type: ignore[no-any-return]
    return None


def save_artist_url(artist_id: str, entry: dict[str, Any]) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO artist_urls (artist_id, data) VALUES (?, ?)",
        (str(artist_id), json.dumps(entry, ensure_ascii=False)),
    )
    conn.commit()


def get_all_artist_urls() -> dict[str, Any]:
    """Return full URL cache as {artist_id: data_dict}."""
    conn = _get_conn()
    rows = conn.execute("SELECT artist_id, data FROM artist_urls").fetchall()
    return {row["artist_id"]: json.loads(row["data"]) for row in rows}


# ---------------------------------------------------------------------------
# Artist enrichment cache (with TTL)
# ---------------------------------------------------------------------------


def has_cached_artist(artist_id: str) -> bool:
    """Fast existence check — no JSON parsing."""
    conn = _get_conn()
    row = conn.execute("SELECT 1 FROM artist_cache WHERE artist_id = ?", (str(artist_id),)).fetchone()
    return row is not None


def get_cached_artist(artist_id: str) -> tuple[dict[str, Any], str] | None:
    """Return (data_dict, cached_at_iso) or None if not found."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT data, cached_at FROM artist_cache WHERE artist_id = ?",
        (str(artist_id),),
    ).fetchone()
    if row:
        return json.loads(row["data"]), row["cached_at"]
    return None


def save_cached_artist(artist_id: str, data: dict[str, Any]) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO artist_cache (artist_id, data, cached_at) VALUES (?, ?, ?)",
        (str(artist_id), json.dumps(data, ensure_ascii=False, default=_json_default), datetime.now().isoformat()),
    )
    conn.commit()


def delete_cached_artist(artist_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM artist_cache WHERE artist_id = ?", (str(artist_id),))
    conn.commit()


def cleanup_cache(ttl_days: int, ttl_following_days: int, is_following_fn: Callable[[str | None], bool]) -> None:
    """Remove stale cache entries based on TTL.

    Uses SQL-based age filtering to avoid per-row queries:
    1. Delete everything older than the max TTL (ttl_following_days) in one pass.
    2. For entries between ttl_days and ttl_following_days old, keep only followed artists.
    """
    conn = _get_conn()
    now_iso = datetime.now().isoformat()

    # Pass 1: delete everything older than the longest TTL — nothing survives past this
    cur = conn.execute(
        "DELETE FROM artist_cache WHERE julianday(?) - julianday(cached_at) >= ?",
        (now_iso, ttl_following_days),
    )
    removed = cur.rowcount

    # Pass 2: entries between short and long TTL — only followed artists survive
    rows = conn.execute(
        "SELECT artist_id, json_extract(data, '$.soundcloud') AS sc_url "
        "FROM artist_cache "
        "WHERE julianday(?) - julianday(cached_at) >= ?",
        (now_iso, ttl_days),
    ).fetchall()

    expire_ids = [row["artist_id"] for row in rows if not (row["sc_url"] and is_following_fn(row["sc_url"]))]
    if expire_ids:
        placeholders = ",".join("?" for _ in expire_ids)
        conn.execute(
            f"DELETE FROM artist_cache WHERE artist_id IN ({placeholders})",
            expire_ids,
        )
        removed += len(expire_ids)

    conn.commit()
    if removed:
        logger.info(f"Cache cleanup: removed {removed} stale entries.")


# ---------------------------------------------------------------------------
# Artist metrics history (for rising detection)
# ---------------------------------------------------------------------------


def get_artist_metrics(artist_id: str) -> tuple[int, int, str] | None:
    """Return (sc_followers, dc_want, recorded_at) or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT sc_followers, dc_want, recorded_at FROM artist_metrics_history WHERE artist_id = ?",
        (str(artist_id),),
    ).fetchone()
    if row:
        return row["sc_followers"], row["dc_want"], row["recorded_at"]
    return None


def save_artist_metrics(artist_id: str, sc_followers: int | None, dc_want: int | None) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO artist_metrics_history (artist_id, sc_followers, dc_want, recorded_at) VALUES (?, ?, ?, ?)",
        (str(artist_id), sc_followers, dc_want, datetime.now().isoformat()),
    )
    conn.commit()


def batch_save_enriched(items: list[tuple[str, dict[str, Any], int | None, int | None]]) -> None:
    """Batch-save enriched artists: cache data + metrics in a single transaction.

    Each item is (artist_id, data_dict, sc_followers, dc_want).
    """
    if not items:
        return
    conn = _get_conn()
    now = datetime.now().isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO artist_cache (artist_id, data, cached_at) VALUES (?, ?, ?)",
        [(str(aid), json.dumps(data, ensure_ascii=False, default=_json_default), now) for aid, data, _, _ in items],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO artist_metrics_history (artist_id, sc_followers, dc_want, recorded_at) VALUES (?, ?, ?, ?)",
        [(str(aid), sc, dc, now) for aid, _, sc, dc in items],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Found events (followed artist sightings)
# ---------------------------------------------------------------------------

_found_cache: set[str] | None = None
_found_lock = threading.Lock()


def _load_found_cache() -> None:
    global _found_cache
    conn = _get_conn()
    rows = conn.execute("SELECT line FROM found_events").fetchall()
    _found_cache = {row["line"] for row in rows}


def record_found(line: str) -> None:
    global _found_cache
    with _found_lock:
        if _found_cache is None:
            _load_found_cache()
        assert _found_cache is not None  # _load_found_cache always sets this
        if line in _found_cache:
            return
        _found_cache.add(line)
    conn = _get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO found_events (line) VALUES (?)", (line,))
        conn.commit()
    except sqlite3.Error as e:
        logger.debug(f"record_found failed for line: {e}")


def get_all_found_lines() -> list[str]:
    conn = _get_conn()
    rows = conn.execute("SELECT line FROM found_events ORDER BY id").fetchall()
    return [row["line"] for row in rows]


# ---------------------------------------------------------------------------
# Scan snapshot (for incremental scans)
# ---------------------------------------------------------------------------


def get_scan_snapshot(city: str) -> dict[str, str]:
    """Return {event_id: lineup_hash} for a city's last scan."""
    conn = _get_conn()
    rows = conn.execute("SELECT event_id, lineup_hash FROM scan_events WHERE city = ?", (city,)).fetchall()
    return {row["event_id"]: row["lineup_hash"] for row in rows}


def get_scan_event_artist_ids(city: str, event_id: str) -> list[str] | None:
    """Return the stored artist_ids list for a specific event, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT artist_ids FROM scan_events WHERE city = ? AND event_id = ?",
        (city, event_id),
    ).fetchone()
    if row:
        return json.loads(row["artist_ids"])  # type: ignore[no-any-return]
    return None


def save_scan_snapshot(city: str, events: list[dict[str, Any]]) -> None:
    """Upsert event snapshots for a city. Removes events no longer present.

    Each dict in events must have: event_id, artist_ids (list[str]), lineup_hash.
    """
    conn = _get_conn()
    now = datetime.now().isoformat()
    current_ids = {ev["event_id"] for ev in events}
    conn.executemany(
        "INSERT OR REPLACE INTO scan_events (city, event_id, artist_ids, lineup_hash, scanned_at) VALUES (?, ?, ?, ?, ?)",
        [(city, ev["event_id"], json.dumps(ev["artist_ids"]), ev["lineup_hash"], now) for ev in events],
    )
    # Remove events that are no longer in the fetch
    if current_ids:
        placeholders = ",".join("?" for _ in current_ids)
        conn.execute(
            f"DELETE FROM scan_events WHERE city = ? AND event_id NOT IN ({placeholders})",
            (city, *current_ids),
        )
    else:
        conn.execute("DELETE FROM scan_events WHERE city = ?", (city,))
    conn.commit()


def clear_scan_snapshot(city: str) -> None:
    """Remove all scan snapshot data for a city (forces full re-scan)."""
    conn = _get_conn()
    conn.execute("DELETE FROM scan_events WHERE city = ?", (city,))
    conn.commit()


# ---------------------------------------------------------------------------
# Migration from old file-based storage
# ---------------------------------------------------------------------------

_CACHE_DIR = BASE_PATH / "lib/parser/cache"
_OLD_URL_CACHE = _CACHE_DIR / "_artist_urls.json"
_OLD_FOUND_DB = BASE_PATH / "lib/parser/db/Found"


def migrate_if_needed() -> None:
    """One-time migration from JSON files + Found CSV to SQLite."""
    conn = _get_conn()

    # Check if we've already migrated
    url_count = conn.execute("SELECT COUNT(*) as c FROM artist_urls").fetchone()["c"]
    if url_count > 0:
        return  # Already populated

    migrated = False

    # Migrate _artist_urls.json
    if _OLD_URL_CACHE.exists():
        try:
            with open(_OLD_URL_CACHE, encoding="utf-8") as f:
                url_data = json.load(f)
            conn.executemany(
                "INSERT OR IGNORE INTO artist_urls (artist_id, data) VALUES (?, ?)",
                [(aid, json.dumps(entry, ensure_ascii=False)) for aid, entry in url_data.items()],
            )
            conn.commit()
            logger.info(f"Migrated {len(url_data)} entries from _artist_urls.json")
            migrated = True
        except Exception as e:
            logger.warning(f"Failed to migrate _artist_urls.json: {e}")

    # Migrate per-artist cache files
    cache_files = list(_CACHE_DIR.glob("[0-9]*.json"))
    if cache_files:
        rows = []
        for cf in cache_files:
            try:
                with open(cf, encoding="utf-8") as f:
                    entry = json.load(f)
                rows.append(
                    (
                        cf.stem,
                        json.dumps(entry.get("data", {}), ensure_ascii=False, default=_json_default),
                        entry.get("cached_at", datetime.now().isoformat()),
                    )
                )
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.debug(f"Skipping cache file {cf.name}: {e}")
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO artist_cache (artist_id, data, cached_at) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
        logger.info(f"Migrated {len(rows)} artist cache files to SQLite")
        migrated = True

    # Migrate db/Found
    if _OLD_FOUND_DB.exists():
        try:
            with open(_OLD_FOUND_DB, encoding="utf-8-sig", errors="replace") as f:
                lines = [line.strip() for line in f if line.strip()]
            # Skip header if present
            if lines and lines[0].startswith("City,"):
                lines = lines[1:]
            conn.executemany(
                "INSERT OR IGNORE INTO found_events (line) VALUES (?)",
                [(line,) for line in lines],
            )
            conn.commit()
            logger.info(f"Migrated {len(lines)} found-event entries to SQLite")
            migrated = True
        except Exception as e:
            logger.warning(f"Failed to migrate Found DB: {e}")

    if migrated:
        logger.info(f"Migration complete. SQLite DB at: {DB_PATH}")
        logger.info("Old JSON/CSV files kept as backup. Delete them manually when ready.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_default(obj: object) -> Any:
    """Handle numpy types in JSON serialization."""
    try:
        import numpy as np

        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# Initialize on import
init_db()
