"""Tests for payloads.py — GraphQL payload builders."""

from __future__ import annotations

from cuepoint.payloads import (
    get_artist_payload,
    get_artist_payload_by_id,
    get_event_detail_payload_by_id,
    get_event_listings_payload,
    get_promoter_events_archive_payload_by_id,
)


class TestEventListingsPayload:
    def test_structure(self):
        p = get_event_listings_payload(34, "2026-01-01", "2026-01-07")
        assert p["operationName"] == "GET_EVENT_LISTINGS"
        assert p["variables"]["filters"]["areas"]["eq"] == 34
        assert p["variables"]["filters"]["listingDate"]["gte"] == "2026-01-01"
        assert p["variables"]["filters"]["listingDate"]["lte"] == "2026-01-07"
        assert "query" in p

    def test_deepcopy_isolation(self):
        p1 = get_event_listings_payload(34, "2026-01-01", "2026-01-07")
        p2 = get_event_listings_payload(29, "2026-02-01", "2026-02-07")
        assert p1["variables"]["filters"]["areas"]["eq"] == 34
        assert p2["variables"]["filters"]["areas"]["eq"] == 29


class TestArtistPayload:
    def test_by_slug(self):
        p = get_artist_payload("test-artist")
        assert p["operationName"] == "GET_ARTIST_BY_SLUG"
        assert p["variables"]["slug"] == "test-artist"

    def test_by_id(self):
        p = get_artist_payload_by_id(12345)
        assert p["operationName"] == "GET_ARTIST_BY_ID"
        assert p["variables"]["id"] == 12345


class TestPromoterPayload:
    def test_structure(self):
        p = get_promoter_events_archive_payload_by_id(99)
        assert p["operationName"] == "GET_PROMOTER_EVENTS_ARCHIVE"
        assert p["variables"]["id"] == 99


class TestEventDetailPayload:
    def test_structure(self):
        p = get_event_detail_payload_by_id(42)
        assert p["operationName"] == "GET_EVENT_DETAIL"
        assert p["variables"]["id"] == 42
