"""Tests for discovery.py �� rising detection, similarity, label affinity."""

from __future__ import annotations

import json
from unittest.mock import patch

from cuepoint.discovery import check_rising, compute_label_affinity, compute_similarity


class TestCheckRising:
    def test_no_previous_metrics(self, tmp_db, mock_config):
        info = {"sc_followers": 1000, "dc_want": 50}
        check_rising("a1", info)
        assert "_rising" not in info

    def test_rising_sc(self, tmp_db, mock_config):
        from cuepoint import db as store

        store.save_artist_metrics("a1", 100, 10)
        info = {"sc_followers": 130, "dc_want": 10}
        check_rising("a1", info)
        assert info.get("_rising") is True

    def test_rising_dc(self, tmp_db, mock_config):
        from cuepoint import db as store

        store.save_artist_metrics("a1", 100, 100)
        info = {"sc_followers": 100, "dc_want": 140}
        check_rising("a1", info)
        assert info.get("_rising") is True

    def test_not_rising_below_threshold(self, tmp_db, mock_config):
        from cuepoint import db as store

        store.save_artist_metrics("a1", 1000, 100)
        info = {"sc_followers": 1010, "dc_want": 102}
        check_rising("a1", info)
        assert "_rising" not in info

    def test_save_false_skips_db_write(self, tmp_db, mock_config):
        from cuepoint import db as store

        info = {"sc_followers": 500, "dc_want": 20}
        check_rising("a1", info, save=False)
        assert store.get_artist_metrics("a1") is None

    def test_none_metrics(self, tmp_db, mock_config):
        info = {"sc_followers": None, "dc_want": None}
        check_rising("a1", info)
        assert "_rising" not in info

    def test_zero_baseline_no_division_error(self, tmp_db, mock_config):
        from cuepoint import db as store

        store.save_artist_metrics("a1", 0, 0)
        info = {"sc_followers": 100, "dc_want": 50}
        check_rising("a1", info)
        assert "_rising" not in info


class TestComputeSimilarity:
    @patch("cuepoint.discovery.is_following")
    def test_similarity_above_threshold(self, mock_follow, mock_config):
        mock_follow.side_effect = lambda url: url == "/followed"
        lookup = {
            "a1": {
                "name": "Followed DJ",
                "soundcloud": "/followed",
                "sc_tags": json.dumps(["Techno", "Industrial", "Ambient"]),
                "dc_styles": json.dumps([]),
                "bc_tags": json.dumps([]),
            },
            "a2": {
                "name": "Similar DJ",
                "soundcloud": "/other",
                "sc_tags": json.dumps(["Techno", "Industrial", "Ambient", "Dark"]),
                "dc_styles": json.dumps([]),
                "bc_tags": json.dumps([]),
            },
        }
        compute_similarity(lookup)
        assert lookup["a2"].get("_similar_to") == "Followed DJ"
        assert lookup["a2"].get("_similarity_score") is not None

    @patch("cuepoint.discovery.is_following")
    def test_no_followed_artists_skips(self, mock_follow, mock_config):
        mock_follow.return_value = False
        lookup = {
            "a1": {
                "name": "DJ A",
                "soundcloud": "/a",
                "sc_tags": json.dumps(["Techno"]),
                "dc_styles": json.dumps([]),
                "bc_tags": json.dumps([]),
            },
        }
        compute_similarity(lookup)
        assert "_similar_to" not in lookup["a1"]

    @patch("cuepoint.discovery.is_following")
    def test_no_tags_skips(self, mock_follow, mock_config):
        mock_follow.return_value = True
        lookup = {
            "a1": {
                "name": "DJ A",
                "soundcloud": "/followed",
                "sc_tags": json.dumps([]),
                "dc_styles": json.dumps([]),
                "bc_tags": json.dumps([]),
            },
        }
        compute_similarity(lookup)
        assert "_similar_to" not in lookup["a1"]

    @patch("cuepoint.discovery.is_following")
    def test_below_threshold_no_flag(self, mock_follow, mock_config):
        mock_follow.side_effect = lambda url: url == "/followed"
        lookup = {
            "a1": {
                "name": "Followed DJ",
                "soundcloud": "/followed",
                "sc_tags": json.dumps(["Techno", "Industrial"]),
                "dc_styles": json.dumps([]),
                "bc_tags": json.dumps([]),
            },
            "a2": {
                "name": "Unrelated DJ",
                "soundcloud": "/other",
                "sc_tags": json.dumps(["Jazz", "Soul", "Funk", "Disco"]),
                "dc_styles": json.dumps([]),
                "bc_tags": json.dumps([]),
            },
        }
        compute_similarity(lookup)
        assert "_similar_to" not in lookup["a2"]


class TestComputeLabelAffinity:
    @patch("cuepoint.discovery.is_following")
    def test_shared_labels_flagged(self, mock_follow):
        mock_follow.side_effect = lambda url: url == "/followed"
        lookup = {
            "a1": {
                "soundcloud": "/followed",
                "dc_labels": json.dumps(["Mord", "Semantica"]),
            },
            "a2": {
                "soundcloud": "/other",
                "dc_labels": json.dumps(["Mord", "Tresor"]),
            },
        }
        compute_label_affinity(lookup)
        assert lookup["a2"]["_shared_labels"] == ["Mord"]

    @patch("cuepoint.discovery.is_following")
    def test_no_shared_labels(self, mock_follow):
        mock_follow.side_effect = lambda url: url == "/followed"
        lookup = {
            "a1": {
                "soundcloud": "/followed",
                "dc_labels": json.dumps(["Mord"]),
            },
            "a2": {
                "soundcloud": "/other",
                "dc_labels": json.dumps(["Tresor"]),
            },
        }
        compute_label_affinity(lookup)
        assert "_shared_labels" not in lookup["a2"]

    @patch("cuepoint.discovery.is_following")
    def test_no_followed_labels_skips(self, mock_follow):
        mock_follow.return_value = False
        lookup = {
            "a1": {
                "soundcloud": "/a",
                "dc_labels": json.dumps(["Mord"]),
            },
        }
        compute_label_affinity(lookup)
        assert "_shared_labels" not in lookup["a1"]

    @patch("cuepoint.discovery.is_following")
    def test_malformed_json_skipped(self, mock_follow):
        mock_follow.side_effect = lambda url: url == "/followed"
        lookup = {
            "a1": {
                "soundcloud": "/followed",
                "dc_labels": json.dumps(["Mord"]),
            },
            "a2": {
                "soundcloud": "/other",
                "dc_labels": "not-json{{",
            },
        }
        compute_label_affinity(lookup)
        assert "_shared_labels" not in lookup["a2"]

    @patch("cuepoint.discovery.is_following")
    def test_max_three_labels(self, mock_follow):
        mock_follow.side_effect = lambda url: url == "/followed"
        labels = ["A", "B", "C", "D", "E"]
        lookup = {
            "a1": {
                "soundcloud": "/followed",
                "dc_labels": json.dumps(labels),
            },
            "a2": {
                "soundcloud": "/other",
                "dc_labels": json.dumps(labels),
            },
        }
        compute_label_affinity(lookup)
        assert len(lookup["a2"]["_shared_labels"]) == 3
