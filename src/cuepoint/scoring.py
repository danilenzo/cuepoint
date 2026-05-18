"""
Scoring, filtering, and recording logic for cuepoint events.

Extracted from event_fetcher.py for maintainability.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from . import config as cfg
from .following import is_following, record
from .tag_utils import count_genre_matches, parse_artist_tags
from .types import ArtistInfo


def filter_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.groupby("event_id").head(1).reset_index(drop=True)
    _filter_genres = cfg.genre_filter()

    def filter_row(row: Any) -> bool:
        genres = []
        following = False

        for artist in row["artists_info"]:
            if artist is not None:
                genres.extend(parse_artist_tags(artist))
                if is_following(artist.get("soundcloud")):
                    following = True

        try:
            for artist in row["artists_list_info_past"]:
                if artist is not None:
                    genres.extend(parse_artist_tags(artist))
                    if is_following(artist.get("soundcloud")):
                        following = True
        except (TypeError, KeyError, AttributeError) as e:
            logger.warning(f"filter_row past artists error: {e}")

        for genre in row["genres"]:
            genres.append(genre["name"])

        # If RA didn't tag the event with any genre, let it through — scoring handles ranking
        ra_has_genres = len(row["genres"]) > 0
        if not ra_has_genres:
            return True

        return following or any(g in genres for g in _filter_genres)

    return df[df.apply(filter_row, axis=1)]


def find_and_record(df: pd.DataFrame, city_name: str) -> None:
    def find_and_record_fun(row: Any) -> None:
        for artist in row["artists_info"]:
            if artist is not None:
                if "soundcloud" in artist:
                    if is_following(artist["soundcloud"]):
                        record(artist, row, city_name)

    df.apply(find_and_record_fun, axis=1)


def _is_notable(artist_info: ArtistInfo | None) -> bool:
    """Check if an artist exceeds any lineup-strength threshold."""
    if artist_info is None:
        return False
    sc = artist_info.get("sc_followers")
    if sc is not None and int(sc) >= cfg.lineup_sc_threshold():
        return True
    dc = artist_info.get("dc_have")
    if dc is not None and int(dc) >= cfg.lineup_dc_threshold():
        return True
    bc = artist_info.get("bc_supporters")
    if bc and int(bc) >= cfg.lineup_bc_threshold():
        return True
    return False


def sort_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()

    _genre_set = set(cfg.genre_filter())

    def count_techno_in_list(genres: list[str]) -> int:
        return sum(1 for g in genres if g in _genre_set)

    def _score_artist(
        artist_info: ArtistInfo | None, divisor: int = 1, breakdown: dict[str, float] | None = None
    ) -> float:
        if artist_info is None:
            return 0.0
        total = 0.0
        genre_hits = count_genre_matches(artist_info, _genre_set)

        def _add(key: str, val: float) -> None:
            nonlocal total
            total += val
            if breakdown is not None and val:
                breakdown[key] = breakdown.get(key, 0) + val

        if "sc_followers" in artist_info and artist_info["sc_followers"] is not None:
            _add("sc_followers", int(artist_info["sc_followers"]) * genre_hits / cfg.sc_weight() / divisor)

        if is_following(artist_info.get("soundcloud")):
            _add("followed", cfg.followed_bonus())

        if "dc_have" in artist_info and artist_info["dc_have"] is not None:
            _add("dc_have", int(artist_info["dc_have"]) * genre_hits / cfg.dc_weight() / divisor)

        bc_sup = artist_info.get("bc_supporters")
        if bc_sup:
            _add("bc_supporters", int(bc_sup) * genre_hits / cfg.bc_weight() / divisor)

        if artist_info.get("_rising"):
            _add("rising", cfg.rising_bonus() / divisor)

        sim_score = artist_info.get("_similarity_score", 0)
        if sim_score:
            _add("similarity", sim_score * cfg.similarity_weight() / divisor)

        shared = artist_info.get("_shared_labels")
        if shared:
            _add("shared_labels", len(shared) * cfg.shared_label_bonus() / divisor)

        dc_ratio = artist_info.get("dc_ratio", 0)
        if dc_ratio:
            _add("dc_ratio", dc_ratio * cfg.dc_ratio_weight() / divisor)

        bc_release = artist_info.get("bc_latest_release")
        if bc_release:
            try:
                release_dt = datetime.strptime(bc_release, "%Y-%m-%d")
                age_days = (datetime.now() - release_dt).days
                if 0 <= age_days <= 365:
                    recency_factor = 1.0 - (age_days / 365.0)
                    _add("recency", cfg.recency_bonus() * recency_factor / divisor)
            except (ValueError, TypeError):
                pass

        return total

    def _score_row(row: Any, breakdown: dict[str, float] | None = None) -> float:
        total = 0.0
        for artist_info in row["artists_info"]:
            total += _score_artist(artist_info, breakdown=breakdown)
        for artist_info in row["artists_list_info_past"]:
            total += _score_artist(artist_info, 5, breakdown=breakdown)

        ra_genres = [g["name"] for g in row["genres"] if isinstance(g, dict)]
        ra_genre_val = count_techno_in_list(ra_genres) * cfg.ra_genre_bonus()
        if ra_genre_val:
            total += ra_genre_val
            if breakdown is not None:
                breakdown["ra_genre"] = ra_genre_val

        return total

    def density_row(row: Any) -> tuple[int, int]:
        artists = row["artists_info"]
        total = len([a for a in artists if a is not None])
        notable = sum(1 for a in artists if _is_notable(a))
        return (notable, total)

    def _compute_score_and_breakdown(row: Any) -> tuple[float, dict[str, float]]:
        bd: dict[str, float] = {}
        score = _score_row(row, breakdown=bd)
        return score, bd

    computed = df.apply(_compute_score_and_breakdown, axis=1)
    df["_score"] = computed.apply(lambda x: x[0])
    df["_score_breakdown"] = computed.apply(lambda x: x[1])
    density = df.apply(density_row, axis=1)
    df["_lineup_notable"] = density.apply(lambda x: x[0])
    df["_lineup_total"] = density.apply(lambda x: x[1])

    max_score = df["_score"].max()
    if max_score > 0:
        df["_match_pct"] = (df["_score"].rank(pct=True) * 99).round().clip(0, 99).astype(int)
        df.loc[df["_score"] <= 0, "_match_pct"] = 0
    else:
        df["_match_pct"] = 0

    def _build_briefing(row: Any) -> list[str]:
        reasons: list[str] = []
        followed_names: list[str] = []
        rising_names: list[str] = []
        similar_pairs: list[tuple[str, str]] = []
        label_info: list[tuple[str, list[str]]] = []

        for a in row["artists_info"]:
            if a is None:
                continue
            name = a.get("name", "")
            if not name:
                continue
            if is_following(a.get("soundcloud")):
                followed_names.append(name)
            if a.get("_rising"):
                rising_names.append(name)
            if a.get("_similar_to"):
                similar_pairs.append((name, a["_similar_to"]))
            if a.get("_shared_labels"):
                label_info.append((name, a["_shared_labels"][:2]))

        if followed_names:
            reasons.append("You follow " + ", ".join(followed_names[:3]))
        for name, sim_to in similar_pairs[:2]:
            reasons.append(f"{name} sounds like {sim_to}")
        for name, labels in label_info[:2]:
            reasons.append(f"{name} shares labels ({', '.join(labels)})")
        if rising_names:
            if len(rising_names) == 1:
                reasons.append(f"{rising_names[0]} is rising")
            else:
                reasons.append(f"{len(rising_names)} rising artists")
        total_hits = sum(count_genre_matches(a, _genre_set) for a in row["artists_info"] if a)
        if total_hits >= 5 and not followed_names:
            reasons.append("Strong genre match across lineup")
        return reasons[:4]

    df["_briefing"] = df.apply(_build_briefing, axis=1)

    return df.sort_values("_score", ascending=False).reset_index(drop=True)
