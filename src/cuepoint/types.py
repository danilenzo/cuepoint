"""Shared type definitions for cuepoint."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict


class _ArtistIdentity(TypedDict):
    """Required artist fields — always present after creation."""

    id: str | int
    name: str


class ArtistInfo(_ArtistIdentity, total=False):
    """Artist enrichment data passed through the pipeline."""

    # Platform URLs
    soundcloud: str | None
    discogs: str | None
    bandcamp: str | None

    # SoundCloud metrics
    sc_followers: int | None
    sc_following: int | None
    sc_tags: str  # JSON-encoded list

    # Discogs metrics
    dc_have: int | None
    dc_want: int | None
    dc_ratio: float | None
    dc_rating: float | None
    dc_styles: str  # JSON-encoded list
    dc_labels: str  # JSON-encoded list

    # Bandcamp metrics
    bc_supporters: int | None
    bc_tags: str  # JSON-encoded list
    bc_latest_release: str | None

    # RA metadata
    ra_followers: int | None
    country: dict[str, Any] | None
    floor: str | None

    # Cached parsed tags (materialized by tag_utils.materialize_tags)
    _parsed_tags: list[str]
    _parsed_tag_set: set[str]
    _parsed_labels: set[str]

    # Discovery (computed, underscore-prefixed)
    _rising: bool
    _similar_to: str
    _similarity_score: int
    _shared_labels: list[str]


class EventDict(TypedDict, total=False):
    """Event data as produced by event_fetcher / club_scrapers and consumed by the pipeline."""

    # RA listing metadata
    listing_id: str | int
    listing_date: str | datetime

    # Event identity
    event_id: str | int
    event_date: str | datetime
    start_time: str | datetime
    end_time: str | datetime
    title: str
    content_url: str
    event_url: str

    # Venue
    venue_id: str | int
    venue_name: str
    venue_url: str

    # Event details
    is_ticketed: bool
    attending: int
    images: list[dict[str, Any]]
    artists: list[dict[str, Any]]
    promoters: list[dict[str, Any]]
    tickets: list[dict[str, Any]]
    genres: list[dict[str, str]]

    # Enrichment (added by pipeline)
    artists_info: list[ArtistInfo | None]
    flyer: str | None
    city_name: str

    # Scoring (added by scoring.py)
    _score: float
    _score_breakdown: dict[str, float]
    _match_pct: int
    _briefing: list[str]
    _lineup_notable: int
    _lineup_total: int

    # Club scraper specific
    _prefilled_artists_info: list[dict[str, Any]]
