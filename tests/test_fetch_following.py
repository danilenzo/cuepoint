"""Tests for fetch_following.py — profile lock and slug helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from cuepoint.fetch_following import (
    _check_profile_lock,
    fetch_following_slugs,
    get_client_id,
    resolve_user_id,
    show_following,
    update_following,
)


class TestCheckProfileLock:
    def test_first_run_writes_profile(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        _check_profile_lock("https://soundcloud.com/testuser", force=False)
        assert profile_file.read_text(encoding="utf-8") == "https://soundcloud.com/testuser"

    def test_same_profile_ok(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        profile_file.write_text("https://soundcloud.com/testuser", encoding="utf-8")
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        _check_profile_lock("https://soundcloud.com/testuser", force=False)

    def test_different_profile_exits(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        profile_file.write_text("https://soundcloud.com/user1", encoding="utf-8")
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        with pytest.raises(SystemExit):
            _check_profile_lock("https://soundcloud.com/user2", force=False)

    def test_different_profile_force_ok(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        profile_file.write_text("https://soundcloud.com/user1", encoding="utf-8")
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        _check_profile_lock("https://soundcloud.com/user2", force=True)
        assert "user2" in profile_file.read_text(encoding="utf-8")

    def test_normalizes_trailing_slash(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod

        profile_file = tmp_path / ".sc_profile"
        monkeypatch.setattr(mod, "_PROFILE_FILE", profile_file)

        _check_profile_lock("https://soundcloud.com/TestUser/", force=False)
        assert profile_file.read_text(encoding="utf-8") == "https://soundcloud.com/testuser"


class TestUpdateFollowing:
    def test_writes_file_and_reloads(self, tmp_path, monkeypatch):
        import cuepoint.fetch_following as mod
        import cuepoint.following as following_mod

        following_file = tmp_path / "following.txt"
        monkeypatch.setattr(mod, "_FOLLOWING_FILE", following_file)
        monkeypatch.setattr(following_mod, "_FOLLOWING_FILE", following_file)

        update_following(["/artist-a", "/artist-b"])

        content = following_file.read_text(encoding="utf-8")
        assert "/artist-a" in content
        assert "/artist-b" in content


class TestShowFollowing:
    def test_empty_following(self, monkeypatch, capsys):
        import cuepoint.following as following_mod

        monkeypatch.setattr(following_mod, "FOLLOWING", set())
        show_following()
        out = capsys.readouterr().out
        assert "empty" in out.lower()

    def test_with_following(self, monkeypatch, capsys):
        import cuepoint.following as following_mod

        monkeypatch.setattr(following_mod, "FOLLOWING", {"/dj-a", "/dj-b"})
        show_following()
        out = capsys.readouterr().out
        assert "2 artists" in out


class TestGetClientId:
    """Tests for get_client_id() — scrapes client_id from SoundCloud JS bundles."""

    def test_successful_extraction(self):
        session = MagicMock(spec=requests.Session)

        html_response = MagicMock()
        html_response.text = (
            '<script src="https://a-v2.sndcdn.com/assets/app-1234.js"></script>'
            '<script src="https://a-v2.sndcdn.com/assets/vendor-5678.js"></script>'
        )
        html_response.raise_for_status = MagicMock()

        js_response = MagicMock()
        js_response.text = 'var o={client_id:"abc12345678901234567890123456789"}'
        js_response.raise_for_status = MagicMock()

        session.get.side_effect = [html_response, js_response]

        result = get_client_id(session)
        assert result == "abc12345678901234567890123456789"

    def test_no_sndcdn_urls_raises_after_3_attempts(self):
        session = MagicMock(spec=requests.Session)

        html_response = MagicMock()
        html_response.text = "<html><body>No JS links here</body></html>"
        html_response.raise_for_status = MagicMock()

        session.get.return_value = html_response

        with pytest.raises(RuntimeError, match="Could not load SoundCloud JS bundles"):
            get_client_id(session)

        assert session.get.call_count == 3

    def test_no_client_id_in_js_raises(self):
        session = MagicMock(spec=requests.Session)

        html_response = MagicMock()
        html_response.text = '<script src="https://a-v2.sndcdn.com/assets/app-1234.js"></script>'
        html_response.raise_for_status = MagicMock()

        js_response = MagicMock()
        js_response.text = "var config = {someOtherKey: 42};"

        session.get.side_effect = [html_response, js_response]

        with pytest.raises(RuntimeError, match="Could not extract client_id"):
            get_client_id(session)


class TestResolveUserId:
    """Tests for resolve_user_id() — resolves SC username to numeric ID."""

    def test_successful_resolution(self):
        session = MagicMock(spec=requests.Session)

        response = MagicMock()
        response.json.return_value = {"id": 12345}
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        result = resolve_user_id("testuser", "fake_client_id", session)
        assert result == 12345

        session.get.assert_called_once_with(
            "https://api-v2.soundcloud.com/resolve",
            params={
                "url": "https://soundcloud.com/testuser",
                "client_id": "fake_client_id",
            },
            timeout=15,
        )

    def test_api_error_propagates(self):
        session = MagicMock(spec=requests.Session)

        response = MagicMock()
        response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        session.get.return_value = response

        with pytest.raises(requests.HTTPError, match="404 Not Found"):
            resolve_user_id("nonexistent", "fake_client_id", session)


class TestFetchFollowingSlugs:
    """Tests for fetch_following_slugs() — full flow with pagination."""

    @patch("cuepoint.fetch_following.resolve_user_id", return_value=12345)
    @patch("cuepoint.fetch_following.get_client_id", return_value="test_client_id")
    @patch("cuepoint.fetch_following.requests.Session")
    def test_single_page(self, MockSession, mock_cid, mock_uid):
        mock_session = MagicMock()
        MockSession.return_value = mock_session

        page_response = MagicMock()
        page_response.json.return_value = {
            "collection": [
                {"permalink": "artist-a"},
                {"permalink": "artist-b"},
                {"permalink": "artist-c"},
            ],
            "next_href": None,
        }
        page_response.raise_for_status = MagicMock()
        mock_session.get.return_value = page_response

        result = fetch_following_slugs("https://soundcloud.com/myuser")

        assert result == ["/artist-a", "/artist-b", "/artist-c"]
        mock_cid.assert_called_once_with(mock_session)
        mock_uid.assert_called_once_with("myuser", "test_client_id", mock_session)

    @patch("cuepoint.fetch_following.resolve_user_id", return_value=12345)
    @patch("cuepoint.fetch_following.get_client_id", return_value="test_client_id")
    @patch("cuepoint.fetch_following.requests.Session")
    def test_multi_page_pagination(self, MockSession, mock_cid, mock_uid):
        mock_session = MagicMock()
        MockSession.return_value = mock_session

        page1_response = MagicMock()
        page1_response.json.return_value = {
            "collection": [
                {"permalink": "artist-a"},
                {"permalink": "artist-b"},
            ],
            "next_href": "https://api-v2.soundcloud.com/users/12345/followings?offset=200",
        }
        page1_response.raise_for_status = MagicMock()

        page2_response = MagicMock()
        page2_response.json.return_value = {
            "collection": [
                {"permalink": "artist-c"},
            ],
            "next_href": None,
        }
        page2_response.raise_for_status = MagicMock()

        mock_session.get.side_effect = [page1_response, page2_response]

        result = fetch_following_slugs("https://soundcloud.com/myuser/")

        assert result == ["/artist-a", "/artist-b", "/artist-c"]
        assert mock_session.get.call_count == 2

    @patch("cuepoint.fetch_following.resolve_user_id", return_value=12345)
    @patch("cuepoint.fetch_following.get_client_id", return_value="test_client_id")
    @patch("cuepoint.fetch_following.requests.Session")
    def test_empty_results(self, MockSession, mock_cid, mock_uid):
        mock_session = MagicMock()
        MockSession.return_value = mock_session

        empty_response = MagicMock()
        empty_response.json.return_value = {
            "collection": [],
            "next_href": None,
        }
        empty_response.raise_for_status = MagicMock()
        mock_session.get.return_value = empty_response

        result = fetch_following_slugs("https://soundcloud.com/myuser")

        assert result == []
