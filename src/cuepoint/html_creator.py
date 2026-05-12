from __future__ import annotations

import html
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .following import is_following
from .generic import RA
from .tag_utils import parse_artist_tags

_VUE_PATH = Path(__file__).parent / "vendor" / "vue.global.prod.js"
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "report.html"

_SAFE_URL_SCHEMES = ("http://", "https://", "/")


def _safe_href(url: str) -> str:
    """Escape a URL for use in an href attribute, blocking javascript: and data: schemes."""
    stripped = url.strip()
    if not any(stripped.startswith(s) for s in _SAFE_URL_SCHEMES):
        return "#"
    return html.escape(stripped)


def df_to_time(row: Any) -> str:
    date = row["start_time"].strftime("%b %d, %Y").replace(" 0", " ")
    time = row["start_time"].strftime("%H:%M") + " - " + row["end_time"].strftime("%H:%M")
    return f"<b>{date}</b><br>{time}"


def df_to_venue(row: Any) -> str:
    venue_url = row["venue_url"]
    if not venue_url or not isinstance(venue_url, str):
        return "NO_VENUE"
    # RA events store a relative path; club events store a full URL
    link = _safe_href(venue_url if venue_url.startswith("http") else RA + venue_url)
    title = html.escape(row["venue_name"])
    return f"""<a href="{link}">{title}</a>"""


_GENRE_BLACKLIST = {
    "electronic",
    "music",
    "dance",
    "club",
    "other",
    "experimental",
    "alternative",
    "indie",
    "pop",
    "rock",
    "hip-hop",
    "hip hop",
}

_GENRE_ALIASES = {
    "drum n bass": "Drum & Bass",
    "drum and bass": "Drum & Bass",
    "dnb": "Drum & Bass",
    "d&b": "Drum & Bass",
    "deep techno": "Techno",
    "hard techno": "Hard Techno",
    "detroit techno": "Detroit Techno",
}

_MAX_GENRES = 5


def _normalize_genre(name: str) -> str | None:
    """Lowercase, apply alias map, filter filler tags and non-genre strings."""
    stripped = name.strip()
    if not stripped or len(stripped) > 30:
        return None
    # Drop tags with no Latin letters (Japanese, Chinese, Arabic, etc.)
    if not re.search(r"[a-zA-Z]", stripped):
        return None
    low = stripped.lower()
    if low in _GENRE_BLACKLIST:
        return None
    canonical = _GENRE_ALIASES.get(low)
    if canonical:
        return canonical
    return stripped.title()


def _categorize_genre(name: str) -> str:
    low = name.lower()
    if "techno" in low:
        return "techno"
    if "bass" in low or "dnb" in low or "drum" in low:
        return "dnb"
    if "house" in low:
        return "house"
    if "ambient" in low or "dub" in low:
        return "ambient"
    if "industrial" in low or "ebm" in low or "noise" in low:
        return "industrial"
    return "default"


def _collect_genre_counts(row: Any) -> tuple[list[tuple[str, int, str]], int]:
    """Extract normalized, counted, categorized genres from an event row.

    Returns (top_genres, extra_count) where each genre is (name, count, category).
    """
    raw: list[str] = []
    for artist in row.get("artists_info") or []:
        if artist is not None:
            raw.extend(parse_artist_tags(artist))
    for genre in row.get("genres") or []:
        raw.append(genre["name"])

    counts: Counter[str] = Counter()
    for g in raw:
        norm = _normalize_genre(g)
        if norm:
            counts[norm] += 1

    filtered = {g: c for g, c in counts.items() if c >= 2}
    if not filtered:
        filtered = dict(counts)
    top = sorted(filtered.items(), key=lambda x: x[1], reverse=True)[:_MAX_GENRES]
    extra = len(filtered) - len(top)

    return [(g, c, _categorize_genre(g)) for g, c in top], extra


def df_to_genre(row: Any) -> str:
    top, extra = _collect_genre_counts(row)
    parts = [f'<span class="genre-pill {cat}">{html.escape(g)} {c}</span>' for g, c, cat in top]
    if extra > 0:
        parts.append(f'<span class="genre-more">+{extra}</span>')
    return " ".join(parts)


def df_to_lineup(row: Any) -> str:
    artists_list_info = row["artists_info"]

    def artist_dict_to_str(artists_list_info: list[dict[str, Any] | None]) -> str:
        artist_dict_to_str_res = ""

        def artist_info_to_str(artist_info: dict[str, Any] | None) -> str:
            res = ""
            if artist_info is None:
                return res
            name = html.escape(artist_info.get("name", ""))
            sc_url = artist_info.get("soundcloud")
            if sc_url is not None:
                sc_url_esc = _safe_href(sc_url)
                if is_following(sc_url):
                    res = res + f'<a href="{sc_url_esc}"><b>{name}</b></a> '
                else:
                    res = res + f'<a href="{sc_url_esc}">{name}</a> '
            else:
                res = res + f"{name} "

            stats = []
            if "sc_followers" in artist_info:
                followers = html.escape(str(artist_info["sc_followers"]))
                if is_following(artist_info.get("soundcloud")):
                    stats.append(f'<font color="green"><b>SC</b></font>: <font color="red">{followers}</font>')
                else:
                    stats.append(f'SC: <font color="red">{followers}</font>')
            if "dc_have" in artist_info:
                dc_have = html.escape(str(artist_info["dc_have"]))
                dc_ratio = html.escape(str(artist_info["dc_ratio"]))
                dc_rating = html.escape(str(artist_info["dc_rating"]))
                stats.append(f"DC: {dc_have}#{dc_ratio}#{dc_rating}")
            if artist_info.get("bc_supporters"):
                bc_sup = html.escape(str(artist_info["bc_supporters"]))
                if artist_info.get("bandcamp"):
                    bc_url = _safe_href(artist_info["bandcamp"])
                    stats.append(f'<a href="{bc_url}">BC</a>: <font color="red">{bc_sup}</font>')
                else:
                    stats.append(f'BC: <font color="red">{bc_sup}</font>')

            if stats:
                res += f'<span class="artist-stats">{" &middot; ".join(stats)}</span> '

            if artist_info.get("country"):
                country_name = html.escape(artist_info["country"].get("name", ""))
                res = res + f"<i>({country_name})</i>"

            if artist_info.get("_rising"):
                res = res + ' <span title="Rising artist" style="color:#e06c75;">&#128293;</span>'

            if artist_info.get("_similar_to"):
                sim_name = html.escape(artist_info["_similar_to"])
                sim_pct = artist_info.get("_similarity_score", 0)
                res = res + f' <span title="{sim_pct}% similar" style="color:#c678dd;">~ {sim_name}</span>'

            if artist_info.get("_shared_labels"):
                label_text = html.escape(", ".join(artist_info["_shared_labels"]))
                res = res + f' <span style="color:#98c379;">[{label_text}]</span>'

            return f'<div class="artist-row">{res}</div>'

        current_floor = None
        for artist_info in artists_list_info:
            floor = artist_info.get("floor") if artist_info else None
            if floor and floor != current_floor:
                current_floor = floor
                artist_dict_to_str_res += f'<div class="floor-label">{html.escape(floor)}</div>'
            artist_dict_to_str_res += artist_info_to_str(artist_info)

        return artist_dict_to_str_res

    return artist_dict_to_str(artists_list_info)


def _plain_lineup(row: Any) -> str:
    """Build a plain-text lineup string for .ics DESCRIPTION (floor-grouped).

    Uses literal two-char '\\n' as line separator — that is what RFC 5545
    DESCRIPTION fields expect.
    """
    parts = []
    current_floor = None
    for a in row.get("artists_info", []):
        if a is None:
            continue
        floor = a.get("floor")
        if floor and floor != current_floor:
            current_floor = floor
            parts.append(f"{floor}:")
        parts.append(f"  {a.get('name', '')}")
    return "\\n".join(parts)


def df_to_title(row: Any) -> str:
    link = _safe_href(row["event_url"])
    title = html.escape(row["title"])
    return f'<button class="cal-btn" title="Download .ics">&#128197;</button> <a href="{link}">{title}</a>'


def df_to_promoters(row: Any) -> str:
    promoters = row["promoters"]

    promoters_str = ""
    for promoter in promoters:
        link = _safe_href(RA + promoter["contentUrl"])
        title = html.escape(promoter["name"])
        promoters_str = promoters_str + f"""<a href="{link}">{title}</a>"""
        promoters_str = promoters_str + "<br></br>"

    return promoters_str


def df_to_flyer(row: Any) -> str:
    pic = row["flyer"]
    if not pic or (isinstance(pic, float) and pic != pic):
        return ""

    return f"""<img src="{html.escape(pic)}" alt="Image" style="width:100%; height:auto;">"""


def df_to_attenders(row: Any) -> int:
    return _safe_int(row["attending"])


def df_to_strength(row: Any) -> str:
    notable = row.get("_lineup_notable", 0)
    total = row.get("_lineup_total", 0)
    if not total:
        return ""
    if not notable:
        return f"0/{total}"
    pct = notable / total
    fill_w = max(1, int(pct * 60))
    return f'{notable}/{total} <span class="str-bar str-fill" style="width:{fill_w}px;"></span>'


_CURRENCY_SYMBOLS = {
    "GBP": "£",
    "EUR": "€",
    "USD": "$",
    "GEL": "₾",
    "JPY": "¥",
    "ARS": "AR$",
    "PLN": "zł",
}

_CITY_CURRENCY = {
    "Amsterdam": "EUR",
    "Berlin": "EUR",
    "Bristol": "GBP",
    "Birmingham": "GBP",
    "London": "GBP",
    "Tbilisi": "GEL",
    "Wuppertal": "EUR",
    "Osaka": "JPY",
    "Tokyo": "JPY",
    "Buenos Aires": "ARS",
    "Warsaw": "PLN",
    "Madrid": "EUR",
    "Barcelona": "EUR",
    "Athens": "EUR",
    "Paris": "EUR",
    "Lisbon": "EUR",
}


def df_to_tickets(row: Any) -> str:
    tickets = row["tickets"]
    if not tickets:
        return ""

    city_fallback = _CITY_CURRENCY.get(row.get("city_name", ""), "")

    available = [t for t in tickets if t["validType"] != "SOLDOUT"]
    if not available:
        return '<span style="color:var(--red);">SOLD OUT</span>'

    html_output = ""
    for ticket in available:
        title = ticket["title"]
        currency_code = ticket.get("currency", {}).get("code", "") or city_fallback
        symbol = _CURRENCY_SYMBOLS.get(currency_code, currency_code + " ")
        price = f"{symbol}{ticket['priceRetail']:.2f}"
        html_output += f'<div class="ticket-line"><span class="tk-name">{html.escape(title)}</span><span class="tk-price">{price}</span></div>'

    return html_output


column_functions = {
    "time": df_to_time,
    "tickets": df_to_tickets,
    "title": df_to_title,
    "attenders": df_to_attenders,
    "strength": df_to_strength,
    "genre": df_to_genre,
    "lineup": df_to_lineup,
    "venue": df_to_venue,
    "promoters": df_to_promoters,
    "flyer": df_to_flyer,
}


def _has_followed(row: Any) -> bool:
    """Check if any artist in the lineup is followed."""
    for artist in row.get("artists_info", []):
        if artist and is_following(artist.get("soundcloud")):
            return True
    return False


def _ensure_bc_url(url: Any) -> str | None:
    if not url or not isinstance(url, str):
        return None
    cleaned: str = url.strip().rstrip("/")
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    if ".bandcamp.com" in cleaned:
        return "https://" + cleaned
    return f"https://{cleaned}.bandcamp.com"


def _safe_int(v: Any, default: int = 0) -> int:
    """Safely convert to int, handling NaN/None."""
    if v is None:
        return default
    try:
        if isinstance(v, float) and math.isnan(v):
            return default
        return int(v)
    except (ValueError, TypeError):
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        if isinstance(v, float) and math.isnan(v):
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


_BREAKDOWN_LABELS = {
    "sc_followers": "SC Followers",
    "dc_have": "Discogs Have",
    "dc_ratio": "Discogs Ratio",
    "bc_supporters": "BC Supporters",
    "followed": "Followed Artist",
    "rising": "Rising",
    "similarity": "Similarity",
    "shared_labels": "Shared Labels",
    "recency": "Release Recency",
    "ra_genre": "RA Genre Match",
}


def _format_breakdown(breakdown: dict[str, float] | Any) -> list[dict[str, Any]]:
    if not breakdown or not isinstance(breakdown, dict):
        return []
    return [
        {"key": k, "label": _BREAKDOWN_LABELS.get(k, k), "value": round(v, 1)}
        for k, v in sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
        if v
    ]


def _artist_to_dict(a: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert an artist info dict to a JSON-serializable dict for Vue."""
    if a is None:
        return None
    sc_url = a.get("soundcloud")
    if "_parsed_tag_set" in a:
        tags = a["_parsed_tag_set"]
    else:
        tags = set()
        for key in ("sc_tags", "dc_styles", "bc_tags"):
            raw = a.get(key)
            if raw:
                try:
                    tags.update(t for t in json.loads(raw) if t)
                except (json.JSONDecodeError, TypeError):
                    pass

    return {
        "name": a.get("name", ""),
        "scUrl": sc_url,
        "scFollowers": _safe_int(a.get("sc_followers")),
        "dcHave": _safe_int(a.get("dc_have")),
        "dcRatio": _safe_float(a.get("dc_ratio")),
        "dcRating": _safe_float(a.get("dc_rating")),
        "bcUrl": _ensure_bc_url(a.get("bandcamp")),
        "bcSupporters": _safe_int(a.get("bc_supporters")),
        "bcLatestRelease": a.get("bc_latest_release", ""),
        "raFollowers": _safe_int(a.get("ra_followers")),
        "contentUrl": a.get("contentUrl", ""),
        "country": (a.get("country") or {}).get("name", ""),
        "floor": a.get("floor"),
        "isFollowed": bool(sc_url and is_following(sc_url)),
        "rising": bool(a.get("_rising")),
        "similarTo": a.get("_similar_to", ""),
        "similarityScore": _safe_int(a.get("_similarity_score")),
        "sharedLabels": a.get("_shared_labels", []),
        "tags": sorted(tags),
    }


def _genre_counts(row: Any) -> list[dict[str, Any]]:
    """Extract normalized genre counts for an event row (Vue JSON format)."""
    top, _ = _collect_genre_counts(row)
    return [{"name": g, "count": c, "category": cat} for g, c, cat in top]


def _df_to_json(df: Any) -> list[dict[str, Any]]:
    """Convert DataFrame to a JSON-serializable list for Vue."""
    events = []
    seen = set()
    for _, row in df.iterrows():
        eid = row.get("event_id", "")
        if eid in seen:
            continue
        seen.add(eid)

        artists = [_artist_to_dict(a) for a in row.get("artists_info", []) if a is not None]
        has_followed = any(a["isFollowed"] for a in artists if a)

        tickets = []
        city_name = row.get("city_name", "")
        city_fallback = _CITY_CURRENCY.get(city_name, "")
        for t in row.get("tickets") or []:
            cur_code = (t.get("currency") or {}).get("code", "") or city_fallback
            tickets.append(
                {
                    "title": t.get("title", ""),
                    "price": t.get("priceRetail", 0),
                    "currency": cur_code,
                    "symbol": _CURRENCY_SYMBOLS.get(cur_code, cur_code + " "),
                    "soldOut": t.get("validType") == "SOLDOUT",
                }
            )

        promoters = []
        for p in row.get("promoters") or []:
            promoters.append(
                {
                    "name": p.get("name", ""),
                    "url": p.get("contentUrl", ""),
                }
            )

        flyer = row.get("flyer")
        if isinstance(flyer, float) and flyer != flyer:
            flyer = None

        venue_url = row.get("venue_url", "")
        if not venue_url or not isinstance(venue_url, str):
            venue_url = ""
        elif not venue_url.startswith("http"):
            venue_url = RA + venue_url

        events.append(
            {
                "id": eid,
                "title": row.get("title", ""),
                "eventUrl": row.get("event_url", ""),
                "startTime": row["start_time"].isoformat()
                if hasattr(row["start_time"], "isoformat")
                else str(row["start_time"]),
                "endTime": row["end_time"].isoformat()
                if hasattr(row["end_time"], "isoformat")
                else str(row["end_time"]),
                "venueName": row.get("venue_name", ""),
                "venueUrl": venue_url,
                "attending": _safe_int(row.get("attending")),
                "score": _safe_int(row.get("_score")),
                "scoreBreakdown": _format_breakdown(row.get("_score_breakdown", {})),
                "matchPct": _safe_int(row.get("_match_pct")),
                "briefing": list(row.get("_briefing", [])),
                "notableCount": _safe_int(row.get("_lineup_notable")),
                "totalArtists": _safe_int(row.get("_lineup_total")),
                "flyer": flyer,
                "city": city_name,
                "hasFollowed": has_followed,
                "artists": artists,
                "genres": _genre_counts(row),
                "tickets": tickets,
                "promoters": promoters,
            }
        )

    return events


def _build_static_fallback(df: Any) -> str:
    """Build a static HTML table as fallback when Vue can't run (iOS previews, no-JS)."""
    rows = []
    seen = set()
    for _, row in df.iterrows():
        eid = row.get("event_id", "")
        if eid in seen:
            continue
        seen.add(eid)
        rows.append(
            "<tr>"
            f"<td>{df_to_time(row)}</td>"
            f"<td>{df_to_title(row)}</td>"
            f"<td>{df_to_genre(row)}</td>"
            f"<td>{df_to_lineup(row)}</td>"
            f"<td>{df_to_venue(row)}</td>"
            f"<td>{df_to_flyer(row)}</td>"
            "</tr>"
        )
    header = "<tr><th>Time</th><th>Title</th><th>Genre</th><th>Lineup</th><th>Venue</th><th>Flyer</th></tr>"
    return (
        '<div class="table-wrap static-fallback">'
        f"<table><thead>{header}</thead><tbody>" + "\n".join(rows) + "</tbody></table></div>"
    )


def create_html(df: Any, stats_html: str = "", scraper_health: list[dict[str, Any]] | None = None) -> str:
    """Render DataFrame into a self-contained Vue 3 HTML report.

    Args:
        df: event DataFrame to render.
        stats_html: optional HTML string for the pipeline stats footer.
        scraper_health: kept for API compatibility but no longer rendered in reports.
    """
    events_json = json.dumps(_df_to_json(df), ensure_ascii=True, default=str)

    try:
        vue_js = _VUE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        vue_js = "/* Vue not found — download vue.global.prod.js to vendor/ */"

    static_table = _build_static_fallback(df)
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")

    return (
        template.replace("/* __VUE_RUNTIME__ */", vue_js)
        .replace('"__EVENTS_DATA__"', events_json)
        .replace("<!-- __STATIC_FALLBACK__ -->", static_table)
        .replace("<!-- __STATS_FOOTER__ -->", stats_html)
    )
