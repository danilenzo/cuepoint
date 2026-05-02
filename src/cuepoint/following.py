from __future__ import annotations

import io
from typing import Any

import pandas as pd
from loguru import logger

from . import db as store
from .generic import BASE_PATH

_FOLLOWING_FILE = BASE_PATH / "following.txt"


def _load_from_file() -> set[str]:
    """Load SoundCloud slugs from following.txt (one per line)."""
    if not _FOLLOWING_FILE.exists():
        logger.warning(
            f"following.txt not found at {_FOLLOWING_FILE}. Run: python -m cuepoint.fetch_following <profile_url>"
        )
        return set()
    lines = _FOLLOWING_FILE.read_text(encoding="utf-8").strip().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


FOLLOWING: set[str] = _load_from_file()


def _build_expanded(slugs: set[str]) -> set[str]:
    """Build lookup set with slug + full URL variants for O(1) matching."""
    expanded = set(slugs)
    for slug in slugs:
        expanded.add(f"https://soundcloud.com{slug}")
        expanded.add(f"https://www.soundcloud.com{slug}")
    return expanded


_FOLLOWING_EXPANDED: set[str] | None = None


def _get_expanded() -> set[str]:
    """Return the expanded set, building it once from FOLLOWING on first call."""
    global _FOLLOWING_EXPANDED
    if _FOLLOWING_EXPANDED is None:
        _FOLLOWING_EXPANDED = _build_expanded(FOLLOWING)
    return _FOLLOWING_EXPANDED


def reload_following() -> None:
    """Reload from file and rebuild the expanded set."""
    global _FOLLOWING_EXPANDED
    FOLLOWING.clear()
    FOLLOWING.update(_load_from_file())
    _FOLLOWING_EXPANDED = _build_expanded(FOLLOWING)


def is_following(sc_url: str | None) -> bool:
    if sc_url is None:
        return False
    expanded = _get_expanded()
    return sc_url in expanded or sc_url.rstrip("/") in expanded


def record(artist: dict[str, Any], event: Any, city: str) -> None:
    try:
        event_date = event.get("event_date") or event.event_date
        date = event_date.strftime("%Y-%m-%d") if hasattr(event_date, "strftime") else str(event_date)[:10]
    except Exception:
        date = "unknown"
    event_url = str(event.get("event_url", "") or "")
    event_id = event_url.replace("https://ra.co/events/", "") or "unknown"
    venue = str(event.get("venue_name", "") or "unknown")
    promoters = event.get("promoters") or []
    artist_name = str(artist.get("name", "unknown"))

    def _safe_join(*parts: str) -> str:
        return ",".join(p.replace(",", ";") for p in parts)

    if len(promoters) == 0:
        store.record_found(_safe_join(city, date, event_id, venue, "Empty", artist_name))

    for promoter in promoters:
        promo_name = promoter["name"] if isinstance(promoter, dict) else str(promoter)
        store.record_found(_safe_join(city, date, event_id, venue, promo_name, artist_name))


def load_found() -> pd.DataFrame:
    lines = store.get_all_found_lines()
    csv_text = "City,Date,Event,Club,Promoter,Artist\n" + "\n".join(lines)
    fd = pd.read_csv(io.StringIO(csv_text)).drop_duplicates()
    fd.reset_index(inplace=True, drop=True)
    return fd
