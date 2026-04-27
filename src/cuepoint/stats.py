"""
Pipeline statistics and error tracking for cuepoint.

ScanStats collects metrics during the fetch → enrich → filter → render pipeline
and renders a summary footer for the HTML report.
"""

from __future__ import annotations

import html
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ScanStats:
    """Accumulates pipeline metrics across a single city scan."""

    city: str = ""

    # Timing
    started_at: float = 0.0
    finished_at: float = 0.0

    # Event counts
    ra_events_fetched: int = 0
    club_events_fetched: int = 0
    events_after_filter: int = 0

    # Artist enrichment
    artists_total: int = 0
    artists_cached: int = 0
    artists_enriched: int = 0

    # Enrichment source outcomes
    sc_ok: int = 0
    sc_fail: int = 0
    dc_ok: int = 0
    dc_fail: int = 0
    bc_ok: int = 0
    bc_fail: int = 0

    # Errors
    errors: list[str] = field(default_factory=list)

    # Thread safety for increments from enrichment workers
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start(self) -> None:
        self.started_at = time.monotonic()

    def finish(self) -> None:
        self.finished_at = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        if self.finished_at and self.started_at:
            return self.finished_at - self.started_at
        return 0.0

    def record_error(self, msg: str) -> None:
        with self._lock:
            self.errors.append(msg)

    def increment(self, **kwargs: int) -> None:
        """Thread-safe increment of any integer field(s)."""
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, getattr(self, key) + value)

    def to_html_footer(self) -> str:
        """Render a compact HTML summary suitable for the report footer."""
        elapsed = self.elapsed_seconds
        mins, secs = divmod(int(elapsed), 60)
        time_str = f"{mins}m {secs}s" if mins else f"{secs}s"

        total_events = self.ra_events_fetched + self.club_events_fetched
        cache_pct = round(self.artists_cached / self.artists_total * 100) if self.artists_total else 0

        parts = [
            f"<b>{html.escape(self.city)}</b>",
            f"{total_events} fetched",
            f"{self.events_after_filter} after filter",
            f"{self.artists_total} artists ({cache_pct}% cached)",
        ]

        src_parts = []
        if self.sc_ok or self.sc_fail:
            src_parts.append(f"SC {self.sc_ok}/{self.sc_ok + self.sc_fail}")
        if self.dc_ok or self.dc_fail:
            src_parts.append(f"DC {self.dc_ok}/{self.dc_ok + self.dc_fail}")
        if self.bc_ok or self.bc_fail:
            src_parts.append(f"BC {self.bc_ok}/{self.bc_ok + self.bc_fail}")
        if src_parts:
            parts.append("enrich: " + ", ".join(src_parts))

        if self.errors:
            parts.append(f'<span style="color:#ff6b6b">{len(self.errors)} error(s)</span>')

        parts.append(time_str)

        return " · ".join(parts)
