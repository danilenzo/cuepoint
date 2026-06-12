"""Shared pytest fixtures for cuepoint tests."""

import json

import pandas as pd
import pytest


@pytest.fixture(autouse=True, scope="session")
def _close_db_at_exit():
    """Close any sqlite connections leaked by module-level init_db()."""
    yield
    from cuepoint import db as store

    store.close_db()


@pytest.fixture
def sample_artist_info():
    """A fully-enriched artist info dict."""
    return {
        "id": "12345",
        "name": "Test Artist",
        "soundcloud": "/test-artist",
        "discogs": "/artist/12345-Test-Artist",
        "contentUrl": "/dj/testart",
        "country": {"name": "DE"},
        "sc_followers": 5000,
        "sc_following": 200,
        "sc_tags": json.dumps(["Techno", "Dark Techno", "Industrial"]),
        "dc_have": 120,
        "dc_want": 80,
        "dc_ratio": 1.5,
        "dc_rating": 4.2,
        "dc_styles": json.dumps(["Techno", "Minimal"]),
        "dc_labels": json.dumps(["Mord", "Semantica"]),
        "bandcamp": "https://testartist.bandcamp.com",
        "bc_tags": json.dumps(["techno", "dark ambient", "industrial"]),
        "bc_supporters": 150,
        "bc_latest_release": "2025-11-01",
    }


@pytest.fixture
def sample_ra_event():
    """A dict matching RA GraphQL event structure."""
    return {
        "id": "lst-001",
        "listingDate": "2026-03-29",
        "event": {
            "id": "evt-100",
            "date": "2026-03-29",
            "startTime": "2026-03-29T23:00:00.000",
            "endTime": "2026-03-30T08:00:00.000",
            "title": "Test Event at Berghain",
            "contentUrl": "/events/100",
            "isTicketed": True,
            "attending": 420,
            "venue": {
                "id": "v-1",
                "name": "Berghain",
                "contentUrl": "/club/berghain",
            },
            "images": [{"filename": "flyer.jpg"}],
            "artists": [
                {"id": "a-1", "name": "Artist One"},
                {"id": "a-2", "name": "Artist Two"},
            ],
            "promoters": [{"name": "Ostgut Ton"}],
            "tickets": [
                {
                    "title": "Standard",
                    "priceRetail": 20.0,
                    "currency": {"code": "EUR"},
                    "onSale": True,
                }
            ],
            "genres": [{"name": "Techno"}],
        },
    }


def _make_event_row(
    event_id,
    artists_info,
    genres=None,
    attending=100,
    title="Test Event",
    venue="Club",
    score=0,
    notable=0,
    total=0,
    has_followed=False,
):
    """Helper to build a single-row dict for DataFrame construction."""
    return {
        "listing_id": f"lst-{event_id}",
        "listing_date": pd.Timestamp("2026-03-29"),
        "event_id": event_id,
        "event_date": pd.Timestamp("2026-03-29"),
        "start_time": pd.Timestamp("2026-03-29 23:00"),
        "end_time": pd.Timestamp("2026-03-30 08:00"),
        "title": title,
        "content_url": f"/events/{event_id}",
        "event_url": f"https://ra.co/events/{event_id}",
        "is_ticketed": True,
        "attending": attending,
        "venue_id": "v-1",
        "venue_name": venue,
        "venue_url": f"/club/{venue.lower().replace(' ', '-')}",
        "images": [],
        "artists": [{"id": f"a-{i}", "name": a.get("name", f"A{i}")} for i, a in enumerate(artists_info)],
        "promoters": [{"name": "Promoter"}],
        "tickets": [],
        "genres": [{"name": g} for g in (genres or ["Techno"])],
        "artists_info": artists_info,
        "artists_list_info_past": [],
        "flyer": None,
        "city_name": "Berlin",
        "_score": score,
        "_lineup_notable": notable,
        "_lineup_total": total,
    }


@pytest.fixture
def sample_df(sample_artist_info):
    """Pre-built DataFrame with 3 events for filter/sort tests."""
    techno_artist = {
        **sample_artist_info,
        "id": "a-1",
        "name": "Techno DJ",
        "sc_followers": 5000,
        "sc_tags": json.dumps(["Techno"]),
        "dc_styles": json.dumps(["Techno"]),
        "bc_tags": json.dumps(["techno"]),
    }
    house_artist = {
        **sample_artist_info,
        "id": "a-2",
        "name": "House DJ",
        "soundcloud": "/house-dj",
        "sc_followers": 3000,
        "sc_tags": json.dumps(["House", "Deep House"]),
        "dc_styles": json.dumps(["House"]),
        "bc_tags": json.dumps(["house"]),
    }
    followed_artist = {
        **sample_artist_info,
        "id": "a-3",
        "name": "Followed Artist",
        "soundcloud": "/dj-q-mono",  # in FOLLOWING set
        "sc_followers": 800,
        "sc_tags": json.dumps(["Ambient"]),
        "dc_styles": json.dumps(["Ambient"]),
        "bc_tags": json.dumps(["ambient"]),
    }

    rows = [
        _make_event_row("evt-1", [techno_artist], ["Techno"], attending=300, title="Techno Night", notable=1, total=1),
        _make_event_row("evt-2", [house_artist], ["House"], attending=200, title="House Party"),
        _make_event_row(
            "evt-3", [followed_artist], ["Ambient"], attending=50, title="Ambient Voyage", has_followed=True
        ),
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def mock_config(monkeypatch):
    """Monkeypatch config._cfg with known test values and FOLLOWING set."""
    from cuepoint import config as cfg
    from cuepoint import following

    monkeypatch.setattr(following, "FOLLOWING", {"/dj-q-mono"})
    monkeypatch.setattr(following, "_FOLLOWING_EXPANDED", following._build_expanded({"/dj-q-mono"}))
    test_cfg = {
        "general": {"days_ahead": 7, "ra_request_delay": 0.1, "max_workers": 3},
        "cache": {"ttl_days": 30, "ttl_following_days": 7, "stale_days": 14},
        "scoring": {
            "sc_weight": 10,
            "dc_weight": 5,
            "bc_weight": 8,
            "ra_genre_bonus": 5000,
            "followed_bonus": 1_000_000,
            "lineup_sc_threshold": 1000,
            "lineup_dc_threshold": 50,
            "lineup_bc_threshold": 30,
        },
        "genres": {"filter": ["Techno", "Drum & Bass", "Drum n Bass"]},
        "discovery": {"rising_sc_pct": 20, "rising_dc_pct": 30},
        "discogs": {"max_masters": 15},
        "bandcamp": {"max_albums": 5},
        # Keep tests hermetic: never read learned adjustments from the real DB.
        "learning": {"enabled": False},
    }
    monkeypatch.setattr(cfg, "_cfg", test_cfg)
    return test_cfg


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point db module at a temp SQLite file and init tables."""
    from cuepoint import db as store

    store.close_db()
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store._local = __import__("threading").local()
    store.init_db()
    yield db_file
    store.close_db()
