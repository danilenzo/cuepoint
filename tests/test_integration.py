"""Integration test: parse → filter → sort → HTML end-to-end."""

import json

import pandas as pd

from cuepoint.html_creator import create_html
from cuepoint.scoring import filter_df, sort_df
from tests.conftest import _make_event_row


def test_end_to_end_pipeline(mock_config):
    """Build a DataFrame from raw data, filter, sort, and render to HTML."""
    techno_artist = {
        "id": "a-1",
        "name": "Techno DJ",
        "soundcloud": "/techno-dj",
        "discogs": "/artist/1-Techno-DJ",
        "contentUrl": "/dj/technodj",
        "country": {"name": "DE"},
        "sc_followers": 8000,
        "sc_following": 100,
        "sc_tags": json.dumps(["Techno", "Industrial Techno"]),
        "dc_have": 200,
        "dc_want": 150,
        "dc_ratio": 1.33,
        "dc_rating": 4.5,
        "dc_styles": json.dumps(["Techno", "Industrial"]),
        "dc_labels": json.dumps(["Mord", "Perc Trax"]),
        "bandcamp": "https://technodj.bandcamp.com",
        "bc_tags": json.dumps(["techno", "industrial"]),
        "bc_supporters": 300,
        "bc_latest_release": "2026-01-15",
    }
    ambient_artist = {
        "id": "a-2",
        "name": "Ambient Artist",
        "soundcloud": "/ambient-artist",
        "discogs": None,
        "contentUrl": "/dj/ambientartist",
        "country": {"name": "JP"},
        "sc_followers": 500,
        "sc_following": 50,
        "sc_tags": json.dumps(["Ambient", "Drone"]),
        "dc_have": None,
        "dc_want": None,
        "dc_ratio": None,
        "dc_rating": None,
        "dc_styles": json.dumps([]),
        "dc_labels": json.dumps([]),
        "bandcamp": None,
        "bc_tags": json.dumps([]),
        "bc_supporters": None,
        "bc_latest_release": None,
    }

    rows = [
        _make_event_row("evt-1", [techno_artist], ["Techno"], attending=500, title="Warehouse Rave"),
        _make_event_row("evt-2", [ambient_artist], ["Ambient"], attending=40, title="Drone Session"),
    ]
    df = pd.DataFrame(rows)

    # Filter: Techno event should survive, Ambient should be dropped by genre filter
    filtered = filter_df(df)
    assert len(filtered) >= 1
    titles = filtered["title"].tolist()
    assert "Warehouse Rave" in titles
    assert "Drone Session" not in titles

    # Sort: should not crash and should return a DataFrame
    sorted_df = sort_df(filtered)
    assert isinstance(sorted_df, pd.DataFrame)
    assert len(sorted_df) == len(filtered)

    # HTML render: should produce valid HTML containing the event title
    html = create_html(sorted_df)
    assert "<!DOCTYPE html>" in html or "<html" in html
    assert "Warehouse Rave" in html
    assert "Techno DJ" in html
