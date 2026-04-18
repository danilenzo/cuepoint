"""Tests for parse_events_list() in event_fetcher.py."""

import pandas as pd

from techno_scan.event_fetcher import parse_events_list


def test_single_event(sample_ra_event):
    """Parsing a single event produces a 1-row DataFrame."""
    df = parse_events_list([sample_ra_event])
    assert len(df) == 1
    assert df.iloc[0]["title"] == "Test Event at Berghain"
    assert df.iloc[0]["event_id"] == "evt-100"


def test_empty_list():
    """Empty event list returns an empty DataFrame with correct columns."""
    df = parse_events_list([])
    assert df.empty
    assert "event_id" in df.columns
    assert "title" in df.columns


def test_multiple_events(sample_ra_event):
    """Multiple events produce correct row count."""
    ev2 = {**sample_ra_event, "id": "lst-002"}
    ev2 = dict(sample_ra_event)
    ev2["id"] = "lst-002"
    ev2["event"] = {**sample_ra_event["event"], "id": "evt-200", "title": "Second Event"}
    df = parse_events_list([sample_ra_event, ev2])
    assert len(df) == 2


def test_datetime_types(sample_ra_event):
    """Datetime columns should be parsed as Timestamp."""
    df = parse_events_list([sample_ra_event])
    assert isinstance(df.iloc[0]["start_time"], pd.Timestamp)
    assert isinstance(df.iloc[0]["end_time"], pd.Timestamp)
