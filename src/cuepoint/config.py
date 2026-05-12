"""
Loads config.toml from the project root.
Provides typed accessors with defaults matching the old hardcoded values.
"""

from __future__ import annotations

import tomllib
from typing import Any, TypeVar

from loguru import logger

from .generic import BASE_PATH

_CONFIG_PATH = BASE_PATH / "config.toml"
_CONFIG_EXAMPLE_PATH = BASE_PATH / "config.toml.example"
_cfg: dict[str, Any] | None = None

_T = TypeVar("_T")


def _load() -> None:
    global _cfg
    if _cfg is not None:
        return
    if _CONFIG_PATH.exists():
        path = _CONFIG_PATH
    elif _CONFIG_EXAMPLE_PATH.exists():
        path = _CONFIG_EXAMPLE_PATH
        logger.warning(f"No config.toml found, using example config: {_CONFIG_EXAMPLE_PATH}")
    else:
        logger.warning("No config.toml or config.toml.example found, using built-in defaults")
        _cfg = {}
        return
    with open(path, "rb") as f:
        _cfg = tomllib.load(f)
    logger.info(f"Config loaded from {path}")


def reload() -> None:
    """Force re-read of config.toml from disk."""
    global _cfg
    _cfg = None
    _load()
    _validate()


def _validate() -> None:
    """Validate critical config values at load time."""
    errors: list[str] = []
    if days_ahead() < 1:
        errors.append("general.days_ahead must be >= 1")
    if ra_request_delay() < 0:
        errors.append("general.ra_request_delay must be >= 0")
    if max_workers() < 1:
        errors.append("general.max_workers must be >= 1")
    if cache_ttl_days() < 1:
        errors.append("cache.ttl_days must be >= 1")
    if cache_ttl_following_days() < 1:
        errors.append("cache.ttl_following_days must be >= 1")
    if sc_weight() < 0 or dc_weight() < 0 or bc_weight() < 0:
        errors.append("scoring weights must be >= 0")
    if similarity_threshold() < 0 or similarity_threshold() > 1:
        errors.append("discovery.similarity_threshold must be between 0 and 1")
    if errors:
        for err in errors:
            logger.warning(f"Config validation: {err}")


def _get(section: str, key: str, default: _T) -> _T:
    _load()
    return (_cfg or {}).get(section, {}).get(key, default)  # type: ignore[no-any-return]


# -- General --
def days_ahead() -> int:
    return _get("general", "days_ahead", 7)


def ra_request_delay() -> float:
    return _get("general", "ra_request_delay", 0.1)


def max_workers() -> int:
    return _get("general", "max_workers", 3)


def incremental() -> bool:
    return _get("general", "incremental", True)


# -- Cache --
def cache_ttl_days() -> int:
    return _get("cache", "ttl_days", 30)


def cache_ttl_following_days() -> int:
    return _get("cache", "ttl_following_days", 7)


def cache_stale_days() -> int:
    return _get("cache", "stale_days", 14)


# -- Scoring --
def sc_weight() -> int:
    return _get("scoring", "sc_weight", 10)


def dc_weight() -> int:
    return _get("scoring", "dc_weight", 5)


def ra_genre_bonus() -> int:
    return _get("scoring", "ra_genre_bonus", 5000)


def followed_bonus() -> int:
    return _get("scoring", "followed_bonus", 1_000_000)


# -- Genres --
def genre_filter() -> list[str]:
    return _get("genres", "filter", ["Techno", "Drum & Bass", "Drum n Bass"])


# -- Cities --
def cities() -> dict[str, tuple[int, str, str]]:
    """Return {key: (area_code, display_name, slug)} for each configured city."""
    _load()
    raw: dict[str, Any] = (_cfg or {}).get("cities", {})
    result: dict[str, tuple[int, str, str]] = {}
    for key, val in raw.items():
        if isinstance(val, dict) and "area" in val and "name" in val:
            slug = val.get("slug", f"xx/{key}")
            result[key] = (val["area"], val["name"], slug)
    # Fallback if config is missing or empty
    if not result:
        result = {
            "amsterdam": (29, "Amsterdam", "nl/amsterdam"),
            "berlin": (34, "Berlin", "de/berlin"),
            "london": (13, "London", "uk/london"),
            "tbilisi": (188, "Tbilisi", "ge/tbilisi"),
            "warsaw": (454, "Warsaw", "pl/warsaw"),
            "osaka": (664, "Osaka", "jp/osaka"),
            "bsas": (395, "Buenos Aires", "ar/buenosaires"),
            "bristol": (446, "Bristol", "uk/bristol"),
            "birmingham": (516, "Birmingham", "uk/birmingham"),
            "wuppertal": (353, "Wuppertal", "de/northrhinewestphalia"),
            "tokyo": (27, "Tokyo", "jp/tokyo"),
            "madrid": (41, "Madrid", "es/madrid"),
            "barcelona": (20, "Barcelona", "es/barcelona"),
            "athens": (549, "Athens", "gr/athens"),
            "paris": (44, "Paris", "fr/paris"),
            "lisbon": (53, "Lisbon", "pt/lisbon"),
        }
    return result


# -- Discogs --
def discogs_max_masters() -> int:
    return _get("discogs", "max_masters", 15)


# -- Bandcamp --
def bandcamp_max_albums() -> int:
    return _get("bandcamp", "max_albums", 5)


def bc_weight() -> int:
    return _get("scoring", "bc_weight", 8)


def lineup_sc_threshold() -> int:
    return _get("scoring", "lineup_sc_threshold", 1000)


def lineup_dc_threshold() -> int:
    return _get("scoring", "lineup_dc_threshold", 50)


def lineup_bc_threshold() -> int:
    return _get("scoring", "lineup_bc_threshold", 30)


# -- Discovery --
def rising_sc_pct() -> int:
    return _get("discovery", "rising_sc_pct", 20)


def rising_dc_pct() -> int:
    return _get("discovery", "rising_dc_pct", 30)


def similarity_threshold() -> float:
    return _get("discovery", "similarity_threshold", 0.5)


def similarity_min_overlap() -> int:
    return _get("discovery", "similarity_min_overlap", 3)


# -- Scoring: discovery signals --
def rising_bonus() -> int:
    return _get("scoring", "rising_bonus", 3000)


def similarity_weight() -> int:
    return _get("scoring", "similarity_weight", 30)


def shared_label_bonus() -> int:
    return _get("scoring", "shared_label_bonus", 1500)


def dc_ratio_weight() -> int:
    return _get("scoring", "dc_ratio_weight", 80)


def recency_bonus() -> int:
    return _get("scoring", "recency_bonus", 800)
