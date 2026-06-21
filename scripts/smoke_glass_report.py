"""Headless smoke test for the Refined Glass report.

Manual verification (Playwright required):
    pip install playwright && playwright install chromium
    python scripts/smoke_glass_report.py
"""

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from tests.conftest import _make_event_row

from cuepoint.html_creator import create_html


def make_artist(name, followers, rising=False):
    return {
        "id": name,
        "name": name,
        "soundcloud": None,
        "sc_followers": followers,
        "sc_tags": json.dumps(["Techno"]),
        "_rising": rising,
    }


def main():
    # nobody followed -> riser (tier 1) must outrank both tier-2 artists,
    # including the one with 90k followers
    artists = [
        make_artist("low-nobody", 100),
        make_artist("big-nobody", 90000),
        make_artist("riser", 500, rising=True),
    ]
    row = _make_event_row("evt-1", artists, score=120, notable=2, total=3)
    row["_match_pct"] = 87
    row["_briefing"] = ["1 rising artist on the lineup", "Strong techno match"]
    row["_score_breakdown"] = {"sc_followers": 80.0, "rising": 25.0, "ra_genre": 15.0}
    html = create_html(pd.DataFrame([row]))

    out = Path(tempfile.mkdtemp()) / "report.html"
    out.write_text(html, encoding="utf-8")
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # --- mobile ---
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(out.as_uri())
        page.wait_for_selector(".event-card")
        if not page.is_visible(".bottom-bar"):
            failures.append("mobile: bottom bar not visible")
        if page.is_visible(".table-wrap table"):
            failures.append("mobile: table visible")
        names = page.eval_on_selector_all(
            ".event-card .card-lineup .artist-row", "els => els.map(e => e.textContent.trim())"
        )
        if not names or "riser" not in names[0]:
            failures.append(f"mobile: ranked order wrong, first row = {names[:1]}")
        page.click(".event-card .card-body")
        page.wait_for_selector(".detail-panel")
        if "Why this matches you" not in page.inner_text(".detail-panel"):
            failures.append("mobile: why-panel missing")
        if not page.is_visible(".detail-actions .fb-btn.went"):
            failures.append("mobile: detail actions missing")
        page.keyboard.press("Escape")
        page.wait_for_selector(".detail-panel", state="detached")
        page.close()

        # --- desktop ---
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(out.as_uri())
        page.wait_for_selector(".toolbar")
        if not page.is_visible("#app .table-wrap table"):
            failures.append("desktop: table view not default")
        # click the time cell — row center may land on a link/button (guard ignores those)
        page.click("#app .table-wrap tbody tr td:first-child")
        page.wait_for_selector(".detail-panel")
        page.click(".detail-close")
        page.wait_for_selector(".detail-panel", state="detached")
        page.click(".view-toggle")
        page.wait_for_selector(".event-card")
        page.close()
        browser.close()

    if failures:
        print("FAIL:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("PASS: glass report smoke test")


if __name__ == "__main__":
    main()
