"""
Unified SQLite storage for cuepoint.

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
from typing import Any, cast

from loguru import logger

from .generic import BASE_PATH
from .types import ArtistInfo

DB_PATH = BASE_PATH / "cache/cuepoint.db"

_local = threading.local()
_db_initialized = False
_db_init_lock = threading.Lock()


def _ensure_conn() -> sqlite3.Connection:
    """Get or create the per-thread connection (no schema init)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return cast(sqlite3.Connection, _local.conn)


def _get_conn() -> sqlite3.Connection:
    """One connection per thread, WAL mode for concurrent reads. Lazy schema init."""
    conn = _ensure_conn()
    global _db_initialized
    if not _db_initialized:
        with _db_init_lock:
            if not _db_initialized:
                _db_initialized = True
                init_db()
                _run_migrations(_ensure_conn())
    return conn


def close_db() -> None:
    """Close the current thread's connection (if any)."""
    global _db_initialized
    if hasattr(_local, "conn") and _local.conn is not None:
        _local.conn.close()
        _local.conn = None
    _db_initialized = False


def check_db() -> bool:
    """Return True if the database is reachable."""
    try:
        _get_conn().execute("SELECT 1").fetchone()
        return True
    except (sqlite3.Error, OSError):
        return False


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _ensure_conn()
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
            artist_id    TEXT NOT NULL,
            sc_followers INTEGER,
            dc_want      INTEGER,
            recorded_at  TEXT NOT NULL,
            PRIMARY KEY (artist_id, recorded_at)
        );

        CREATE TABLE IF NOT EXISTS scan_events (
            city        TEXT NOT NULL,
            event_id    TEXT NOT NULL,
            artist_ids  TEXT NOT NULL,
            lineup_hash TEXT NOT NULL,
            scanned_at  TEXT NOT NULL,
            PRIMARY KEY (city, event_id)
        );

        CREATE TABLE IF NOT EXISTS api_results (
            city       TEXT PRIMARY KEY,
            data       TEXT NOT NULL,
            scanned_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scraper_health (
            source           TEXT NOT NULL,
            city             TEXT NOT NULL DEFAULT '',
            status           TEXT NOT NULL,
            events_found     INTEGER NOT NULL DEFAULT 0,
            error_msg        TEXT NOT NULL DEFAULT '',
            recorded_at      TEXT NOT NULL,
            last_nonempty_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (source, city)
        );

        CREATE TABLE IF NOT EXISTS event_feedback (
            event_id    TEXT NOT NULL,
            city        TEXT NOT NULL DEFAULT '',
            verdict     TEXT NOT NULL CHECK (verdict IN ('went', 'skipped')),
            event_title TEXT NOT NULL DEFAULT '',
            breakdown   TEXT NOT NULL DEFAULT '{}',
            genres      TEXT NOT NULL DEFAULT '[]',
            artist_ids  TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (event_id)
        );

        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_artist_cache_cached_at ON artist_cache(cached_at);
        CREATE INDEX IF NOT EXISTS idx_scan_events_city ON scan_events(city);
        CREATE INDEX IF NOT EXISTS idx_api_results_city ON api_results(city);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Artist URL cache (permanent — SC/Discogs URLs never change)
# ---------------------------------------------------------------------------


def get_artist_url(artist_id: str) -> dict[str, Any] | None:
    conn = _get_conn()
    row = conn.execute("SELECT data FROM artist_urls WHERE artist_id = ?", (str(artist_id),)).fetchone()
    if row:
        result: dict[str, Any] = json.loads(row["data"])
        return result
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


def get_cached_artist(artist_id: str) -> tuple[ArtistInfo, str] | None:
    """Return (data_dict, cached_at_iso) or None if not found."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT data, cached_at FROM artist_cache WHERE artist_id = ?",
        (str(artist_id),),
    ).fetchone()
    if row:
        return json.loads(row["data"]), row["cached_at"]
    return None


def save_cached_artist(artist_id: str, data: ArtistInfo) -> None:
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
    """Return oldest (sc_followers, dc_want, recorded_at) as baseline, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT sc_followers, dc_want, recorded_at FROM artist_metrics_history "
        "WHERE artist_id = ? ORDER BY recorded_at ASC LIMIT 1",
        (str(artist_id),),
    ).fetchone()
    if row:
        return row["sc_followers"], row["dc_want"], row["recorded_at"]
    return None


def save_artist_metrics(artist_id: str, sc_followers: int | None, dc_want: int | None) -> None:
    conn = _get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO artist_metrics_history (artist_id, sc_followers, dc_want, recorded_at) VALUES (?, ?, ?, ?)",
        (str(artist_id), sc_followers, dc_want, now),
    )
    conn.execute(
        "DELETE FROM artist_metrics_history WHERE artist_id = ? AND recorded_at NOT IN "
        "(SELECT recorded_at FROM artist_metrics_history WHERE artist_id = ? ORDER BY recorded_at ASC LIMIT 5)",
        (str(artist_id), str(artist_id)),
    )
    conn.commit()


def _prune_metrics_history(conn: sqlite3.Connection, artist_ids: list[str], keep: int = 5) -> None:
    """Keep only the `keep` oldest rows per artist — run once after a batch insert."""
    for aid in artist_ids:
        conn.execute(
            "DELETE FROM artist_metrics_history WHERE artist_id = ? AND recorded_at NOT IN "
            "(SELECT recorded_at FROM artist_metrics_history WHERE artist_id = ? ORDER BY recorded_at ASC LIMIT ?)",
            (aid, aid, keep),
        )


def batch_save_enriched(items: list[tuple[str, ArtistInfo, int | None, int | None]]) -> None:
    """Batch-save enriched artists: cache data + metrics in a single transaction.

    Each item is (artist_id, data_dict, sc_followers, dc_want).
    Metrics history is pruned to 5 rows per artist inside the same transaction.
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
        "INSERT OR IGNORE INTO artist_metrics_history (artist_id, sc_followers, dc_want, recorded_at) VALUES (?, ?, ?, ?)",
        [(str(aid), sc, dc, now) for aid, _, sc, dc in items],
    )
    # Prune history within the same transaction — one commit for everything
    _prune_metrics_history(conn, [str(aid) for aid, *_ in items])
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
        assert _found_cache is not None
        if line in _found_cache:
            return
        _found_cache.add(line)
        conn = _get_conn()
        try:
            conn.execute("INSERT OR IGNORE INTO found_events (line) VALUES (?)", (line,))
            conn.commit()
        except sqlite3.Error as e:
            _found_cache.discard(line)
            logger.debug(f"record_found failed for line: {e}")


def get_all_found_lines() -> list[str]:
    conn = _get_conn()
    rows = conn.execute("SELECT line FROM found_events ORDER BY id").fetchall()
    return [row["line"] for row in rows]


# ---------------------------------------------------------------------------
# Event feedback (scoring feedback loop)
# ---------------------------------------------------------------------------


def save_feedback(
    event_id: str,
    city: str,
    verdict: str,
    *,
    event_title: str = "",
    breakdown: dict[str, float] | None = None,
    genres: list[str] | None = None,
    artist_ids: list[str] | None = None,
) -> None:
    """Upsert one feedback verdict. Verdict is enforced by a CHECK constraint."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO event_feedback "
        "(event_id, city, verdict, event_title, breakdown, genres, artist_ids, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(event_id),
            city,
            verdict,
            event_title,
            json.dumps(breakdown or {}, ensure_ascii=False),
            json.dumps(genres or [], ensure_ascii=False),
            json.dumps(artist_ids or [], ensure_ascii=False),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def get_all_feedback() -> list[dict[str, Any]]:
    """Return all feedback rows with JSON fields parsed. Malformed rows are skipped."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT event_id, city, verdict, event_title, breakdown, genres, artist_ids, recorded_at "
        "FROM event_feedback"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append(
                {
                    "event_id": row["event_id"],
                    "city": row["city"],
                    "verdict": row["verdict"],
                    "event_title": row["event_title"],
                    "breakdown": json.loads(row["breakdown"]),
                    "genres": json.loads(row["genres"]),
                    "artist_ids": json.loads(row["artist_ids"]),
                    "recorded_at": row["recorded_at"],
                }
            )
        except (json.JSONDecodeError, TypeError) as e:
            logger.debug(f"Skipping malformed feedback row {row['event_id']}: {e}")
    return out


def count_feedback() -> dict[str, int]:
    """Return {verdict: count}."""
    conn = _get_conn()
    rows = conn.execute("SELECT verdict, COUNT(*) AS c FROM event_feedback GROUP BY verdict").fetchall()
    return {row["verdict"]: row["c"] for row in rows}


def clear_feedback() -> int:
    """Delete all feedback rows. Returns number deleted."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM event_feedback")
    conn.commit()
    return cur.rowcount


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
        ids: list[str] = json.loads(row["artist_ids"])
        return ids
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
# API results (persisted scan results for the REST API)
# ---------------------------------------------------------------------------


def save_api_results(city: str, events: list[dict[str, Any]]) -> None:
    """Replace stored API results for a city with fresh data (atomic upsert)."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO api_results (city, data, scanned_at) VALUES (?, ?, ?)",
        (city, json.dumps(events, ensure_ascii=False, default=_json_default), datetime.now().isoformat()),
    )
    conn.commit()


def get_api_results(city: str) -> list[dict[str, Any]] | None:
    """Return stored API results for a city, or None if never scanned."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT data FROM api_results WHERE city = ?",
        (city,),
    ).fetchone()
    if row:
        results: list[dict[str, Any]] = json.loads(row["data"])
        return results
    return None


# ---------------------------------------------------------------------------
# Scraper health tracking
# ---------------------------------------------------------------------------


def record_scraper_health(
    source: str,
    *,
    city: str = "",
    status: str = "ok",
    events_found: int = 0,
    error_msg: str = "",
) -> None:
    conn = _get_conn()
    now = datetime.now().isoformat()
    prev = conn.execute(
        "SELECT last_nonempty_at FROM scraper_health WHERE source = ? AND city = ?",
        (source, city),
    ).fetchone()
    prev_nonempty = prev["last_nonempty_at"] if prev else ""
    last_nonempty = now if events_found > 0 else prev_nonempty
    conn.execute(
        "INSERT OR REPLACE INTO scraper_health "
        "(source, city, status, events_found, error_msg, recorded_at, last_nonempty_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, city, status, events_found, error_msg, now, last_nonempty),
    )
    conn.commit()


def get_stale_scrapers(days: int = 14) -> list[dict[str, Any]]:
    """Return scrapers that haven't returned events in `days` days."""
    conn = _get_conn()
    now_iso = datetime.now().isoformat()
    rows = conn.execute(
        "SELECT source, city, last_nonempty_at, recorded_at FROM scraper_health "
        "WHERE last_nonempty_at != '' "
        "AND julianday(?) - julianday(last_nonempty_at) >= ?",
        (now_iso, days),
    ).fetchall()
    return [
        {
            "source": row["source"],
            "city": row["city"],
            "last_nonempty_at": row["last_nonempty_at"],
            "days_since": int((datetime.fromisoformat(now_iso) - datetime.fromisoformat(row["last_nonempty_at"])).days),
        }
        for row in rows
    ]


def get_all_scraper_health() -> list[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT source, city, status, events_found, error_msg, recorded_at, last_nonempty_at "
        "FROM scraper_health ORDER BY recorded_at DESC"
    ).fetchall()
    return [
        {
            "source": row["source"],
            "city": row["city"],
            "status": row["status"],
            "events_found": row["events_found"],
            "error_msg": row["error_msg"],
            "recorded_at": row["recorded_at"],
            "last_nonempty_at": row["last_nonempty_at"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Migration from old file-based storage
# ---------------------------------------------------------------------------

_CACHE_DIR = BASE_PATH / "cache"
_OLD_URL_CACHE = _CACHE_DIR / "_artist_urls.json"
_OLD_FOUND_DB = BASE_PATH / "db/Found"


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


# ---------------------------------------------------------------------------
# Numbered migration system
# ---------------------------------------------------------------------------

_MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = []


def _migration(
    version: int, description: str
) -> Callable[[Callable[[sqlite3.Connection], None]], Callable[[sqlite3.Connection], None]]:
    """Decorator to register a numbered migration."""

    def decorator(fn: Callable[[sqlite3.Connection], None]) -> Callable[[sqlite3.Connection], None]:
        _MIGRATIONS.append((version, description, fn))
        return fn

    return decorator


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any pending migrations in order."""
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version").fetchall()}
    for version, desc, fn in sorted(_MIGRATIONS):
        if version not in applied:
            logger.info(f"Running migration {version}: {desc}")
            fn(conn)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            conn.commit()


@_migration(1, "add artist_metrics compound primary key")
def _migrate_001(conn: sqlite3.Connection) -> None:
    """Migrate artist_metrics_history from single-row to multi-row schema."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(artist_metrics_history)").fetchall()]
    if "artist_id" not in cols:
        return
    pk_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='artist_metrics_history'"
    ).fetchone()
    if pk_sql and "artist_id, recorded_at" not in pk_sql["sql"]:
        conn.executescript("""
            ALTER TABLE artist_metrics_history RENAME TO _old_metrics;
            CREATE TABLE artist_metrics_history (
                artist_id    TEXT NOT NULL,
                sc_followers INTEGER,
                dc_want      INTEGER,
                recorded_at  TEXT NOT NULL,
                PRIMARY KEY (artist_id, recorded_at)
            );
            INSERT OR IGNORE INTO artist_metrics_history
                SELECT artist_id, sc_followers, dc_want, recorded_at FROM _old_metrics;
            DROP TABLE _old_metrics;
        """)
        logger.info("Migrated artist_metrics_history to compound primary key")


@_migration(3, "rebuild api_results with city PRIMARY KEY")
def _migrate_003(conn: sqlite3.Connection) -> None:
    """Deduplicate api_results and add a PRIMARY KEY on city."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "api_results" not in tables:
        return
    pk_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='api_results'").fetchone()
    if pk_sql and "PRIMARY KEY" in (pk_sql["sql"] or ""):
        return  # already migrated
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _api_results_new (
            city       TEXT PRIMARY KEY,
            data       TEXT NOT NULL,
            scanned_at TEXT NOT NULL
        );
        INSERT OR REPLACE INTO _api_results_new (city, data, scanned_at)
            SELECT city, data, scanned_at FROM api_results
            ORDER BY scanned_at DESC;
        DROP TABLE api_results;
        ALTER TABLE _api_results_new RENAME TO api_results;
        CREATE INDEX IF NOT EXISTS idx_api_results_city ON api_results(city);
    """)
    logger.info("Migration 3: rebuilt api_results with city PRIMARY KEY")


@_migration(2, "add scraper_health last_nonempty_at column")
def _migrate_002(conn: sqlite3.Connection) -> None:
    """Add last_nonempty_at column to scraper_health if missing."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(scraper_health)").fetchall()]
    if "last_nonempty_at" not in cols and "source" in cols:
        conn.execute("ALTER TABLE scraper_health ADD COLUMN last_nonempty_at TEXT NOT NULL DEFAULT ''")
        conn.commit()
        logger.info("Added last_nonempty_at column to scraper_health")
