"""Tests for stats.py — ScanStats pipeline counters and HTML footer."""

from __future__ import annotations

import threading
import time

from cuepoint.stats import ScanStats


class TestScanStats:
    def test_default_values(self):
        s = ScanStats()
        assert s.city == ""
        assert s.ra_events_fetched == 0
        assert s.errors == []

    def test_start_finish_elapsed(self):
        s = ScanStats()
        s.start()
        time.sleep(0.05)
        s.finish()
        assert s.elapsed_seconds >= 0.04

    def test_elapsed_zero_when_not_started(self):
        s = ScanStats()
        assert s.elapsed_seconds == 0.0

    def test_record_error(self):
        s = ScanStats()
        s.record_error("boom")
        s.record_error("crash")
        assert len(s.errors) == 2
        assert "boom" in s.errors

    def test_increment_single(self):
        s = ScanStats()
        s.increment(sc_ok=3)
        assert s.sc_ok == 3

    def test_increment_multiple(self):
        s = ScanStats()
        s.increment(sc_ok=1, dc_ok=2, bc_ok=3)
        assert s.sc_ok == 1
        assert s.dc_ok == 2
        assert s.bc_ok == 3

    def test_increment_thread_safe(self):
        s = ScanStats()

        def bump():
            for _ in range(100):
                s.increment(artists_enriched=1)

        threads = [threading.Thread(target=bump) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert s.artists_enriched == 1000


class TestToHtmlFooter:
    def test_basic_footer(self):
        s = ScanStats(
            city="Berlin",
            ra_events_fetched=50,
            club_events_fetched=5,
            events_after_filter=30,
            artists_total=100,
            artists_cached=60,
        )
        s.started_at = 1000.0
        s.finished_at = 1045.0
        html = s.to_html_footer()
        assert "Berlin" in html
        assert "55 fetched" in html
        assert "30 after filter" in html
        assert "100 artists" in html
        assert "60% cached" in html
        assert "45s" in html

    def test_footer_with_enrichment(self):
        s = ScanStats(sc_ok=10, sc_fail=2, dc_ok=8, dc_fail=0, bc_ok=5, bc_fail=1)
        s.started_at = 1000.0
        s.finished_at = 1120.0
        html = s.to_html_footer()
        assert "SC 10/12" in html
        assert "DC 8/8" in html
        assert "BC 5/6" in html
        assert "2m 0s" in html

    def test_footer_with_errors(self):
        s = ScanStats()
        s.started_at = 1000.0
        s.finished_at = 1001.0
        s.record_error("test error")
        html = s.to_html_footer()
        assert "1 error(s)" in html
        assert "color:#ff6b6b" in html

    def test_footer_no_artists(self):
        s = ScanStats(artists_total=0)
        s.started_at = 1.0
        s.finished_at = 2.0
        html = s.to_html_footer()
        assert "0% cached" in html

    def test_footer_html_escapes_city(self):
        s = ScanStats(city="<script>alert(1)</script>")
        s.started_at = 1.0
        s.finished_at = 2.0
        html = s.to_html_footer()
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
