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


class TestDetailView:
    def test_detail_markup_present(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "detail-backdrop" in html
        assert "detail-panel" in html
        assert "Why this matches you" in html
        assert "openDetail" in html
        assert "closeDetail" in html

    def test_detail_action_row(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "detail-actions" in html
        # feedback contract intact
        assert "setFeedback" in html
        assert "fb-btn" in html

    def test_card_accordion_removed(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "card-expand-hint" not in html
        assert "cardExpanded" not in html


class TestGlassRestyle:
    def test_gradient_match_badge(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "var(--grad-score)" in html

    def test_zebra_striping_removed(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "tbody tr:nth-child(even)" not in html


class TestMobileBottomBar:
    def test_bottom_bar_markup(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "bottom-bar" in html
        assert "mobileSearchOpen" in html
        assert "mobileFilterOpen" in html

    def test_mobile_table_css_removed(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        # the old table-to-block transform must be gone
        assert "table, thead, tbody, tr, th, td { display: block" not in html
        assert "attr(data-label)" not in html


class TestMotion:
    def test_entrance_animation_present(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "@keyframes card-in" in html
        assert "animationDelay" in html

    def test_reduced_motion_guard_kept(self, sample_artist_info, mock_config):
        html = _render(sample_artist_info)
        assert "prefers-reduced-motion" in html
