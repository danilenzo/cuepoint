"""Tests for XSS payload injection — verify html_creator escapes untrusted data.

html.escape() converts <, >, &, ", ' to HTML entities — preventing tag injection.
_safe_href() blocks javascript:/data: protocols in href attributes.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from cuepoint.html_creator import (
    _safe_href,
    df_to_flyer,
    df_to_lineup,
    df_to_promoters,
    df_to_tickets,
    df_to_title,
    df_to_venue,
)

SCRIPT_PAYLOADS = [
    '<script>alert("xss")</script>',
    "<svg onload=alert(1)>",
    '<iframe src="javascript:alert(1)">',
]

ATTR_INJECTION_PAYLOADS = [
    '"><img src=x onerror=alert(1)>',
    "' onclick='alert(1)",
]

PROTOCOL_PAYLOADS = [
    "javascript:alert(document.cookie)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:MsgBox(1)",
]


def _make_row(**overrides):
    base = {
        "start_time": datetime(2025, 6, 1, 22, 0),
        "end_time": datetime(2025, 6, 2, 6, 0),
        "title": "Safe Event",
        "event_url": "/events/123",
        "venue_name": "Safe Venue",
        "venue_url": "/clubs/456",
        "attending": 100,
        "artists_info": [],
        "genres": [],
        "promoters": [],
        "tickets": [],
        "flyer": None,
        "city_name": "Berlin",
    }
    base.update(overrides)
    return base


class TestSafeHref:
    def test_allows_https(self):
        assert _safe_href("https://example.com") == "https://example.com"

    def test_allows_http(self):
        assert _safe_href("http://example.com") == "http://example.com"

    def test_allows_relative_path(self):
        assert _safe_href("/events/123") == "/events/123"

    def test_blocks_javascript_protocol(self):
        assert _safe_href("javascript:alert(1)") == "#"

    def test_blocks_data_protocol(self):
        assert _safe_href("data:text/html,<script>") == "#"

    def test_blocks_vbscript(self):
        assert _safe_href("vbscript:MsgBox(1)") == "#"

    def test_escapes_html_in_url(self):
        result = _safe_href('https://example.com/x?a=1&b="2"')
        assert "&amp;" in result
        assert "&quot;" in result


class TestXSSInTitle:
    def test_script_tags_escaped_in_title(self):
        for payload in SCRIPT_PAYLOADS:
            row = _make_row(title=payload, event_url="/events/safe")
            result = df_to_title(row)
            assert "<script>" not in result
            assert "<svg" not in result
            assert "<iframe" not in result

    def test_attr_injection_escaped_in_title(self):
        for payload in ATTR_INJECTION_PAYLOADS:
            row = _make_row(title=payload, event_url="/events/safe")
            result = df_to_title(row)
            assert '"><img' not in result

    def test_protocol_injection_blocked_in_event_url(self):
        for payload in PROTOCOL_PAYLOADS:
            row = _make_row(event_url=payload)
            result = df_to_title(row)
            assert 'href="#"' in result


class TestXSSInVenue:
    def test_script_tags_escaped_in_venue_name(self):
        for payload in SCRIPT_PAYLOADS:
            row = _make_row(venue_name=payload, venue_url="/clubs/safe")
            result = df_to_venue(row)
            assert "<script>" not in result
            assert "<svg" not in result

    def test_protocol_injection_neutralized_in_venue_url(self):
        for payload in PROTOCOL_PAYLOADS:
            row = _make_row(venue_url=payload)
            result = df_to_venue(row)
            # Non-http URLs get RA prefix, making them https:// — safe
            assert 'href="https://' in result or 'href="#"' in result


class TestXSSInLineup:
    @patch("cuepoint.html_creator.is_following", return_value=False)
    def test_script_tags_escaped_in_artist_name(self, _mock):
        for payload in SCRIPT_PAYLOADS:
            artist = {"name": payload, "soundcloud": None}
            row = _make_row(artists_info=[artist])
            result = df_to_lineup(row)
            assert "<script>" not in result
            assert "<svg" not in result

    @patch("cuepoint.html_creator.is_following", return_value=False)
    def test_protocol_injection_blocked_in_soundcloud_url(self, _mock):
        for payload in PROTOCOL_PAYLOADS:
            artist = {"name": "Safe", "soundcloud": payload}
            row = _make_row(artists_info=[artist])
            result = df_to_lineup(row)
            assert 'href="#"' in result

    @patch("cuepoint.html_creator.is_following", return_value=False)
    def test_script_tags_escaped_in_country(self, _mock):
        for payload in SCRIPT_PAYLOADS:
            artist = {"name": "Safe", "soundcloud": None, "country": {"name": payload}}
            row = _make_row(artists_info=[artist])
            result = df_to_lineup(row)
            assert "<script>" not in result

    @patch("cuepoint.html_creator.is_following", return_value=False)
    def test_protocol_injection_blocked_in_bandcamp_url(self, _mock):
        for payload in PROTOCOL_PAYLOADS:
            artist = {
                "name": "Safe",
                "soundcloud": None,
                "bc_supporters": 100,
                "bandcamp": payload,
            }
            row = _make_row(artists_info=[artist])
            result = df_to_lineup(row)
            assert 'href="#"' in result

    @patch("cuepoint.html_creator.is_following", return_value=False)
    def test_script_tags_escaped_in_similar_to(self, _mock):
        for payload in SCRIPT_PAYLOADS:
            artist = {"name": "Safe", "soundcloud": None, "_similar_to": payload, "_similarity_score": 80}
            row = _make_row(artists_info=[artist])
            result = df_to_lineup(row)
            assert "<script>" not in result

    @patch("cuepoint.html_creator.is_following", return_value=False)
    def test_script_tags_escaped_in_shared_labels(self, _mock):
        for payload in SCRIPT_PAYLOADS:
            artist = {"name": "Safe", "soundcloud": None, "_shared_labels": [payload]}
            row = _make_row(artists_info=[artist])
            result = df_to_lineup(row)
            assert "<script>" not in result

    @patch("cuepoint.html_creator.is_following", return_value=False)
    def test_script_tags_escaped_in_floor_label(self, _mock):
        for payload in SCRIPT_PAYLOADS:
            artist = {"name": "Safe", "soundcloud": None, "floor": payload}
            row = _make_row(artists_info=[artist])
            result = df_to_lineup(row)
            assert "<script>" not in result


class TestXSSInPromoters:
    def test_script_tags_escaped_in_promoter_name(self):
        for payload in SCRIPT_PAYLOADS:
            row = _make_row(promoters=[{"name": payload, "contentUrl": "/promoters/safe"}])
            result = df_to_promoters(row)
            assert "<script>" not in result
            assert "<svg" not in result

    def test_protocol_injection_blocked_in_promoter_url(self):
        for payload in PROTOCOL_PAYLOADS:
            row = _make_row(promoters=[{"name": "Safe", "contentUrl": payload}])
            result = df_to_promoters(row)
            # Promoter URLs get RA prefix, so they become https://ra.co + payload — safe
            assert "javascript:" not in result or "https://ra.co" in result


class TestXSSInTickets:
    def test_script_tags_escaped_in_ticket_title(self):
        for payload in SCRIPT_PAYLOADS:
            row = _make_row(
                tickets=[{"title": payload, "priceRetail": 10.0, "currency": {"code": "EUR"}, "validType": "VALID"}],
                city_name="Berlin",
            )
            result = df_to_tickets(row)
            assert "<script>" not in result
            assert "<svg" not in result


class TestXSSInFlyer:
    def test_script_tags_escaped_in_flyer_url(self):
        for payload in SCRIPT_PAYLOADS:
            row = _make_row(flyer=payload)
            result = df_to_flyer(row)
            assert "<script>" not in result
            assert "<svg" not in result

    def test_attr_injection_escaped_in_flyer_url(self):
        row = _make_row(flyer='"><img src=x onerror=alert(1)>')
        result = df_to_flyer(row)
        assert '"><img' not in result
