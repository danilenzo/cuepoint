"""Template assertions for the Refined Glass redesign."""

import pandas as pd

from cuepoint.html_creator import create_html
from tests.conftest import _make_event_row


def _render(sample_artist_info):
    df = pd.DataFrame([_make_event_row("evt-1", [sample_artist_info])])
    return create_html(df)


class TestGlassTokens:
    def test_new_tokens_present(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        for token in ("--glass:", "--glass-strong:", "--glass-border:",
                      "--grad-score:", "--radius-pill:", "--radius-card:", "--blur:"):
            assert token in html, f"missing token {token}"

    def test_base_palette(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "#0b0d14" in html          # new page base
        assert "#a855f7" in html          # new purple
        assert "rgba(88,60,200,0.22)" in html  # violet ambient glow

    def test_substitution_markers_survive(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert '"__EVENTS_DATA__"' not in html
        assert "__CSP_CONNECT_SRC__" not in html
        assert "/* __VUE_RUNTIME__ */" not in html


class TestIOSMeta:
    def test_home_screen_meta_tags(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in html
        assert 'apple-mobile-web-app-status-bar-style" content="black-translucent"' in html
        assert '<meta name="theme-color" content="#0b0d14">' in html


class TestRankedArtists:
    def test_ranked_artists_function_present(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "function rankedArtists(" in html
        assert "cardArtists" in html

    def test_card_lineup_uses_ranked_top3(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "cardArtists(ev)" in html
        assert "lineup-more" in html  # passive "+N more" label class
