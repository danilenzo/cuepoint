"""Server-side report changes for the feedback loop."""

import pandas as pd

from cuepoint.html_creator import _artist_to_dict, _csp_connect_src, create_html
from tests.conftest import _make_event_row


class TestArtistId:
    def test_artist_dict_includes_id(self, sample_artist_info):
        d = _artist_to_dict(sample_artist_info)
        assert d["id"] == "12345"


class TestApiBaseEmbed:
    def test_api_base_substituted(self, sample_artist_info, mock_config):
        df = pd.DataFrame([_make_event_row("evt-1", [sample_artist_info])])
        html = create_html(df)
        assert '"__API_BASE__"' not in html
        assert "http://localhost:8000" in html

    def test_feedback_buttons_present(self, sample_artist_info, mock_config):
        df = pd.DataFrame([_make_event_row("evt-1", [sample_artist_info])])
        html = create_html(df)
        assert "setFeedback" in html
        assert "cuepoint_feedback" in html

    def test_csp_allows_feedback_endpoint(self, sample_artist_info, mock_config):
        df = pd.DataFrame([_make_event_row("evt-1", [sample_artist_info])])
        html = create_html(df)
        assert "__CSP_CONNECT_SRC__" not in html
        assert "connect-src http://localhost:8000;" in html


class TestCspConnectSrc:
    def test_valid_origins_pass(self):
        assert _csp_connect_src("http://localhost:8000") == "http://localhost:8000"
        assert _csp_connect_src("https://api.example.com") == "https://api.example.com"

    def test_invalid_values_fall_back_to_none(self):
        for bad in ("", "localhost:8000", "http://host:8000/path", 'http://x">', "http://a b"):
            assert _csp_connect_src(bad) == "'none'"
