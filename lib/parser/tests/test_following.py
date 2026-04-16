"""Tests for following.py — is_following checks."""

import following
from following import _build_expanded, is_following


def _patch_following(monkeypatch, slugs):
    """Patch both FOLLOWING and the expanded lookup set."""
    monkeypatch.setattr(following, "FOLLOWING", slugs)
    monkeypatch.setattr(following, "_FOLLOWING_EXPANDED", _build_expanded(slugs))


def test_is_following_match(monkeypatch):
    """Known slug from FOLLOWING set should match."""
    _patch_following(monkeypatch, {"/test-dj"})
    assert is_following("/test-dj") is True


def test_is_following_none():
    """None input returns False."""
    assert is_following(None) is False


def test_is_following_full_url(monkeypatch):
    """Full SC URL should be stripped and matched."""
    _patch_following(monkeypatch, {"/test-dj"})
    assert is_following("https://www.soundcloud.com/test-dj") is True


def test_is_following_no_www_url(monkeypatch):
    """Full SC URL without www should be stripped and matched."""
    _patch_following(monkeypatch, {"/test-dj"})
    assert is_following("https://soundcloud.com/test-dj") is True


def test_is_following_miss(monkeypatch):
    """Unknown slug should not match."""
    _patch_following(monkeypatch, {"/test-dj"})
    assert is_following("/definitely-not-a-real-dj") is False
