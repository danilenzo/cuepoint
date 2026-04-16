from __future__ import annotations

import html
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from following import is_following
from generic import RA
from tag_utils import parse_artist_tags

_VUE_PATH = Path(__file__).parent / "vendor" / "vue.global.prod.js"


def df_to_time(row: Any) -> str:
    date = row["start_time"].strftime("%b %d, %Y").replace(" 0", " ")
    time = row["start_time"].strftime("%H:%M") + " - " + row["end_time"].strftime("%H:%M")
    return f"<b>{date}</b><br>{time}"


def df_to_venue(row: Any) -> str:
    venue_url = row["venue_url"]
    if not venue_url or not isinstance(venue_url, str):
        return "NO_VENUE"
    # RA events store a relative path; club events store a full URL
    link = html.escape(venue_url if venue_url.startswith("http") else RA + venue_url)
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


def df_to_genre(row: Any) -> str:
    raw = []
    for artist in row["artists_info"]:
        if artist is not None:
            raw.extend(parse_artist_tags(artist))

    for genre in row["genres"]:
        raw.append(genre["name"])

    # Normalize, deduplicate, count
    counts: Counter[str] = Counter()
    for g in raw:
        norm = _normalize_genre(g)
        if norm:
            counts[norm] += 1

    # Drop singletons, sort by count, take top N
    filtered = {g: c for g, c in counts.items() if c >= 2}
    if not filtered:
        filtered = dict(counts)  # fallback: show all if everything is a singleton
    top = sorted(filtered.items(), key=lambda x: x[1], reverse=True)[:_MAX_GENRES]

    extra = len(filtered) - len(top)
    parts = []
    for g, c in top:
        low = g.lower()
        if "techno" in low:
            cls = "techno"
        elif "bass" in low or "dnb" in low or "drum" in low:
            cls = "dnb"
        elif "house" in low:
            cls = "house"
        elif "ambient" in low or "dub" in low:
            cls = "ambient"
        elif "industrial" in low or "ebm" in low or "noise" in low:
            cls = "industrial"
        else:
            cls = "default"
        parts.append(f'<span class="genre-pill {cls}">{html.escape(g)} {c}</span>')
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
                sc_url_esc = html.escape(sc_url)
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
                    bc_url = html.escape(artist_info["bandcamp"])
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
    link = html.escape(row["event_url"])
    title = html.escape(row["title"])
    return f'<button class="cal-btn" title="Download .ics">&#128197;</button> <a href="{link}">{title}</a>'


def df_to_promoters(row: Any) -> str:
    promoters = row["promoters"]

    promoters_str = ""
    for promoter in promoters:
        link = html.escape(RA + promoter["contentUrl"])
        title = html.escape(promoter["name"])
        promoters_str = promoters_str + f"""<a href="{link}">{title}</a>"""
        promoters_str = promoters_str + "<br></br>"

    return promoters_str


def df_to_flyer(row: Any) -> str:
    pic = row["flyer"]
    if not pic or (isinstance(pic, float) and pic != pic):
        return ""

    return f"""<img src="{pic}" alt="Image" style="width:200px; height:auto;">"""


def df_to_attenders(row: Any) -> int:
    return int(row["attending"])


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
    "GBP": "&#163;",  # £
    "EUR": "&#8364;",  # €
    "USD": "&#36;",  # $
    "GEL": "&#8382;",  # ₾
    "JPY": "&#165;",  # ¥
    "ARS": "AR&#36;",  # AR$
    "PLN": "z&#322;",  # zł
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
        html_output += f"ticket-{html.escape(title)} ({price})<br />"

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


def _artist_to_dict(a: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert an artist info dict to a JSON-serializable dict for Vue."""
    if a is None:
        return None
    sc_url = a.get("soundcloud")
    tags: set[str] = set()
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
        "bcUrl": a.get("bandcamp"),
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
    """Extract normalized genre counts for an event row."""
    raw = []
    for artist in row.get("artists_info", []):
        if artist is not None:
            for key in ("sc_tags", "dc_styles", "bc_tags"):
                if key in artist:
                    try:
                        raw.extend(json.loads(artist[key]))
                    except (json.JSONDecodeError, TypeError):
                        pass

    for g in row.get("genres", []):
        raw.append(g["name"])

    counts: Counter[str] = Counter()
    for g in raw:
        norm = _normalize_genre(g)
        if norm:
            counts[norm] += 1

    filtered = {g: c for g, c in counts.items() if c >= 2}
    if not filtered:
        filtered = dict(counts)
    top = sorted(filtered.items(), key=lambda x: x[1], reverse=True)[:_MAX_GENRES]

    result = []
    for g, c in top:
        low = g.lower()
        if "techno" in low:
            cat = "techno"
        elif "bass" in low or "dnb" in low or "drum" in low:
            cat = "dnb"
        elif "house" in low:
            cat = "house"
        elif "ambient" in low or "dub" in low:
            cat = "ambient"
        elif "industrial" in low or "ebm" in low or "noise" in low:
            cat = "industrial"
        else:
            cat = "default"
        result.append({"name": g, "count": c, "category": cat})
    return result


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


def create_html(df: Any, stats_html: str = "") -> str:
    """Render DataFrame into a self-contained Vue 3 HTML report.

    Args:
        df: event DataFrame to render.
        stats_html: optional HTML string for the pipeline stats footer.
    """
    events_json = json.dumps(_df_to_json(df), ensure_ascii=True, default=str)

    # Read Vue runtime
    try:
        vue_js = _VUE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        vue_js = "/* Vue not found — download vue.global.prod.js to vendor/ */"

    static_table = _build_static_fallback(df)

    return (
        _VUE_HTML_TEMPLATE.replace("/* __VUE_RUNTIME__ */", vue_js)
        .replace('"__EVENTS_DATA__"', events_json)
        .replace("<!-- __STATIC_FALLBACK__ -->", static_table)
        .replace("<!-- __STATS_FOOTER__ -->", stats_html)
    )


_VUE_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>techno_scan</title>
<style>
    :root {
        --bg-primary: #0f1114;
        --bg-secondary: #161920;
        --bg-card: #1a1e27;
        --bg-elevated: #1f2430;
        --bg-hover: #252a36;
        --border: rgba(255,255,255,0.06);
        --border-hover: rgba(255,255,255,0.12);
        --text-primary: #e2e4e9;
        --text-secondary: #9399a6;
        --text-muted: #5c6370;
        --accent: #4a9eff;
        --accent-dim: rgba(74,158,255,0.15);
        --green: #7ec87e;
        --green-dim: rgba(126,200,126,0.12);
        --red: #e06c75;
        --purple: #c678dd;
        --radius: 10px;
        --radius-sm: 6px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'Inter', -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        background: var(--bg-primary); color: var(--text-primary);
        padding: 0; min-height: 100vh; min-height: 100dvh;
        padding-left: env(safe-area-inset-left);
        padding-right: env(safe-area-inset-right);
        padding-bottom: env(safe-area-inset-bottom);
        -webkit-text-size-adjust: 100%;
    }
    /* --- toolbar --- */
    .toolbar {
        position: sticky; top: 0; z-index: 10;
        background: rgba(15,17,20,0.82);
        backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
        border-bottom: 1px solid var(--border);
        padding: 14px 24px;
        display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
    }
    .toolbar input[type="text"] {
        background: var(--bg-elevated); color: var(--text-primary);
        border: 1px solid var(--border-hover);
        border-radius: var(--radius); padding: 9px 14px; font-size: 13px;
        width: 260px; transition: border-color 0.2s, box-shadow 0.2s;
        outline: none;
    }
    .toolbar input[type="text"]:focus {
        border-color: var(--accent);
        box-shadow: 0 0 0 3px var(--accent-dim);
    }
    .toolbar input[type="text"]::placeholder { color: var(--text-muted); }
    .toolbar label, .toolbar .tb-btn {
        font-size: 13px; cursor: pointer; user-select: none;
        color: var(--text-secondary); transition: color 0.15s;
        display: flex; align-items: center; gap: 6px;
    }
    .toolbar label:hover, .toolbar .tb-btn:hover { color: var(--text-primary); }
    .toolbar input[type="checkbox"] {
        accent-color: var(--accent); width: 15px; height: 15px;
    }
    .count {
        font-size: 12px; color: var(--text-muted);
        margin-left: auto; font-variant-numeric: tabular-nums;
    }
    .toolbar .ics-export, .toolbar .view-toggle {
        background: var(--bg-elevated); color: var(--text-secondary);
        border: 1px solid var(--border-hover);
        border-radius: var(--radius); padding: 9px 16px; font-size: 13px;
        cursor: pointer; transition: all 0.2s;
    }
    .toolbar .ics-export:hover, .toolbar .view-toggle:hover {
        background: var(--bg-hover); color: var(--text-primary);
        border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim);
    }
    .toolbar .view-toggle.active {
        background: var(--accent-dim); color: var(--accent);
        border-color: var(--accent);
    }
    /* genre dropdown */
    .genre-dropdown { position: relative; }
    .genre-dropdown-btn {
        background: var(--bg-elevated); color: var(--text-secondary);
        border: 1px solid var(--border-hover);
        border-radius: var(--radius); padding: 9px 14px; font-size: 13px;
        cursor: pointer; transition: all 0.2s; white-space: nowrap;
    }
    .genre-dropdown-btn:hover {
        background: var(--bg-hover); color: var(--text-primary);
    }
    .genre-panel {
        position: absolute; top: 100%; left: 0; z-index: 20;
        background: var(--bg-card); border: 1px solid var(--border-hover);
        border-radius: var(--radius); padding: 8px; margin-top: 4px;
        min-width: 200px; max-height: 300px; overflow-y: auto;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    }
    .genre-panel label {
        display: flex; align-items: center; gap: 8px;
        padding: 5px 8px; border-radius: var(--radius-sm);
        font-size: 12px; cursor: pointer; transition: background 0.15s;
    }
    .genre-panel label:hover { background: var(--bg-hover); }
    /* --- container --- */
    .table-wrap { padding: 16px 24px 32px; }
    /* --- table --- */
    table {
        border-collapse: separate; border-spacing: 0;
        width: 100%; table-layout: auto;
    }
    thead { position: sticky; top: 52px; z-index: 5; }
    th {
        background: var(--bg-secondary);
        color: var(--text-muted); border: none;
        border-bottom: 2px solid var(--border);
        padding: 11px 14px; text-align: left; font-size: 11px;
        text-transform: uppercase; letter-spacing: 0.8px; font-weight: 600;
        cursor: pointer; user-select: none; white-space: nowrap;
        transition: color 0.15s;
    }
    th:first-child { border-radius: var(--radius) 0 0 0; }
    th:last-child { border-radius: 0 var(--radius) 0 0; }
    th:hover { color: var(--accent); }
    th .arrow { font-size: 10px; margin-left: 4px; color: var(--accent); }
    td {
        border: none; border-bottom: 1px solid var(--border);
        padding: 12px 14px; text-align: left; vertical-align: top;
        word-wrap: break-word; white-space: normal; font-size: 13px;
        line-height: 1.5; transition: background 0.15s;
    }
    tr { transition: background 0.15s; }
    tr:hover td { background: var(--bg-hover); }
    tr.followed td {
        background: var(--accent-dim);
        border-bottom-color: rgba(74,158,255,0.1);
    }
    tr.followed td:first-child {
        box-shadow: inset 3px 0 0 var(--accent);
    }
    tr.followed:hover td { background: rgba(74,158,255,0.2); }
    td:first-child {
        width: 10em; min-width: 10em; max-width: 10em;
        white-space: nowrap; font-variant-numeric: tabular-nums;
    }
    a { color: var(--accent); text-decoration: none; transition: color 0.15s; }
    a:hover { color: #7cbfff; text-decoration: underline; }
    b { color: var(--text-primary); font-weight: 600; }
    img {
        border-radius: var(--radius); max-width: 200px; height: auto;
        transition: transform 0.2s, box-shadow 0.2s;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    }
    @media (hover: hover) {
        img:hover { transform: scale(1.03); box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
    }
    .hidden { display: none; }
    /* zebra */
    tbody tr:nth-child(even) td { background: var(--bg-secondary); }
    tbody tr:nth-child(even):hover td { background: var(--bg-hover); }
    tbody tr.followed:nth-child(even) td { background: rgba(74,158,255,0.1); }
    tbody tr.followed:nth-child(even):hover td { background: rgba(74,158,255,0.2); }
    /* genre pills */
    .genre-pill {
        display: inline-block; padding: 3px 9px; margin: 2px 3px 2px 0;
        border-radius: 12px; font-size: 11px; font-weight: 500;
        line-height: 1.4; white-space: nowrap;
    }
    .genre-pill.techno { background: rgba(224,108,117,0.18); color: #e06c75; }
    .genre-pill.dnb { background: rgba(198,120,221,0.18); color: #c678dd; }
    .genre-pill.house { background: rgba(97,175,239,0.18); color: #61afef; }
    .genre-pill.ambient { background: rgba(86,182,194,0.18); color: #56b6c2; }
    .genre-pill.industrial { background: rgba(190,146,100,0.18); color: #be9264; }
    .genre-pill.default { background: rgba(255,255,255,0.07); color: var(--text-secondary); }
    .genre-more { font-size: 11px; color: var(--text-muted); margin-left: 2px; }
    /* lineup */
    .artist-row {
        padding: 4px 0; border-bottom: 1px solid var(--border);
        line-height: 1.5;
    }
    .artist-row:last-child { border-bottom: none; }
    .floor-label {
        display: inline-block; font-weight: 700; color: var(--text-primary);
        padding: 6px 0 2px; margin-top: 4px; font-size: 12px;
        text-transform: uppercase; letter-spacing: 0.5px;
        border-bottom: 1px solid var(--accent-dim);
    }
    .artist-stats { color: var(--text-muted); font-size: 12px; }
    /* strength bar */
    .str-bar {
        display: inline-block; height: 8px;
        border-radius: 4px; vertical-align: middle;
    }
    .str-fill { background: linear-gradient(90deg, var(--green), #5dba5d); }
    /* calendar icon */
    .cal-btn {
        cursor: pointer; opacity: 0.4; font-size: 14px;
        background: none; border: none; color: var(--accent); padding: 0 4px;
        vertical-align: middle; transition: opacity 0.15s;
    }
    .cal-btn:hover { opacity: 1; }
    /* artist expand */
    .artist-expand-btn {
        cursor: pointer; font-size: 11px; color: var(--text-muted);
        background: none; border: none; padding: 2px 6px;
        transition: color 0.15s;
    }
    .artist-expand-btn:hover { color: var(--accent); }
    .artist-detail {
        font-size: 11px; color: var(--text-muted);
        padding: 4px 0 4px 16px; line-height: 1.6;
    }
    .artist-detail .tag-list {
        display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px;
    }
    .artist-detail .tag-chip {
        background: rgba(255,255,255,0.06); padding: 1px 7px;
        border-radius: 8px; font-size: 10px;
    }
    /* card view */
    .card-grid {
        display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
        gap: 16px; padding: 16px 24px 32px;
    }
    .event-card {
        background: var(--bg-card); border: 1px solid var(--border);
        border-radius: var(--radius); overflow: hidden;
        transition: border-color 0.2s, box-shadow 0.2s;
    }
    .event-card:hover {
        border-color: var(--border-hover);
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }
    .event-card.followed { border-left: 3px solid var(--accent); background: var(--accent-dim); }
    .event-card .card-flyer img {
        width: 100%; max-width: 100%; border-radius: 0;
    }
    .event-card .card-body { padding: 14px; }
    .event-card .card-title { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
    .event-card .card-meta { font-size: 12px; color: var(--text-secondary); margin-bottom: 8px; }
    .event-card .card-meta span { margin-right: 12px; }
    .event-card .card-genres { margin-bottom: 8px; }
    .event-card .card-lineup { border-top: 1px solid var(--border); padding-top: 8px; }
    /* scrollbar */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: var(--bg-primary); }
    ::-webkit-scrollbar-thumb { background: var(--bg-elevated); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--bg-hover); }
    /* static fallback */
    .static-fallback { padding: 16px 24px 32px; }
    /* transitions */
    .row-move { transition: transform 0.3s ease; }
    /* responsive */
    @media (max-width: 900px) {
        .toolbar { padding: 12px 14px; }
        .toolbar input[type="text"] { width: 100%; }
        .table-wrap, .card-grid { padding: 8px 8px 24px; }
        td, th { padding: 8px 8px; font-size: 12px; }
        img { max-width: 140px; }
    }
    @media (max-width: 768px) {
        .table-wrap { padding: 8px; }
        table, thead, tbody, tr, th, td { display: block; width: 100%; }
        thead { position: static; display: none; }
        tbody tr {
            background: var(--bg-card); border: 1px solid var(--border);
            border-radius: var(--radius); margin-bottom: 12px; padding: 0; overflow: hidden;
        }
        tbody tr:hover { border-color: var(--border-hover); }
        tbody tr.followed { border-left: 3px solid var(--accent); background: var(--accent-dim); }
        tbody tr:nth-child(even) td,
        tbody tr:nth-child(even):hover td,
        tbody tr.followed:nth-child(even) td { background: transparent; }
        tbody tr td:first-child { box-shadow: none; }
        td {
            padding: 8px 14px; border-bottom: 1px solid var(--border);
            position: relative; min-height: 28px; width: auto; min-width: 0; max-width: none; white-space: normal;
        }
        td:last-child { border-bottom: none; }
        td::before {
            content: attr(data-label); display: block; font-size: 10px; font-weight: 600;
            text-transform: uppercase; letter-spacing: 0.6px; color: var(--text-muted); margin-bottom: 4px;
        }
        td[data-label="flyer"] img { max-width: 100%; width: 100%; border-radius: var(--radius) var(--radius) 0 0; }
        td[data-label="flyer"] { padding: 0; order: -1; }
        td[data-label="flyer"]::before { display: none; }
        td[data-label="time"] { background: var(--bg-elevated); font-size: 13px; }
        td[data-label="promoters"], td[data-label="strength"], td[data-label="attenders"] { display: none; }
        td[data-label="genre"] .genre-pill { font-size: 10px; padding: 2px 7px; }
        td[data-label="lineup"] .artist-row { font-size: 12px; }
        td[data-label="lineup"] .artist-stats { font-size: 11px; }
        .card-grid { grid-template-columns: 1fr; padding: 8px; }
    }
</style>
</head>
<body>
<!-- Static fallback: shown immediately, hidden once Vue mounts -->
<div id="static-view">
<!-- __STATIC_FALLBACK__ -->
</div>
<div id="app" style="display:none;">
    <!-- Toolbar -->
    <div class="toolbar">
        <input type="text" v-model="searchQuery" placeholder="Search artists, venues, promoters..."
               @keydown.escape="searchQuery = ''">
        <label><input type="checkbox" v-model="followedOnly"> Followed only</label>

        <!-- Genre dropdown -->
        <div class="genre-dropdown" ref="genreDropdown">
            <button class="genre-dropdown-btn" @click="genreDropdownOpen = !genreDropdownOpen">
                Genres {{ selectedGenres.length ? '(' + selectedGenres.length + ')' : '' }} &#9662;
            </button>
            <div class="genre-panel" v-if="genreDropdownOpen" ref="genrePanel">
                <label v-for="g in allGenres" :key="g">
                    <input type="checkbox" :value="g" v-model="selectedGenres">
                    {{ g }}
                </label>
                <div style="border-top:1px solid var(--border); margin-top:6px; padding-top:6px;">
                    <label @click.prevent="selectedGenres = []" style="color:var(--accent); cursor:pointer;">
                        Clear all
                    </label>
                </div>
            </div>
        </div>

        <button class="view-toggle" :class="{active: viewMode === 'card'}" @click="toggleView">
            {{ viewMode === 'table' ? '&#9638; Cards' : '&#9776; Table' }}
        </button>
        <button class="ics-export" @click="exportICS">Export .ics</button>
        <span class="count">{{ visibleCount === totalCount ? totalCount + ' events' : visibleCount + ' / ' + totalCount + ' events' }}</span>
    </div>

    <!-- Table view -->
    <div class="table-wrap" v-if="viewMode === 'table'">
        <table>
            <thead>
                <tr>
                    <th v-for="col in columns" :key="col.key" @click="sortBy(col.key)">
                        {{ col.label }}<span class="arrow" v-if="sortColumn === col.key">{{ sortAsc ? '\u25B2' : '\u25BC' }}</span>
                    </th>
                </tr>
            </thead>
            <tbody>
                <tr v-for="ev in filteredEvents" :key="ev.id"
                    :class="{followed: ev.hasFollowed}">
                    <td data-label="time" :data-sort="ev.startTime">
                        <b>{{ formatDate(ev.startTime) }}</b><br>
                        {{ formatTimeRange(ev.startTime, ev.endTime) }}
                    </td>
                    <td data-label="tickets" v-html="formatTickets(ev)"></td>
                    <td data-label="title">
                        <button class="cal-btn" @click="downloadSingleICS(ev)" title="Download .ics">&#128197;</button>
                        <a :href="ev.eventUrl">{{ ev.title }}</a>
                    </td>
                    <td data-label="attenders" :data-sort="ev.attending">{{ ev.attending }}</td>
                    <td data-label="strength" :data-sort="ev.notableCount" v-html="formatStrength(ev)"></td>
                    <td data-label="genre">
                        <span v-for="g in ev.genres" :key="g.name" :class="'genre-pill ' + g.category">
                            {{ g.name }} {{ g.count }}
                        </span>
                    </td>
                    <td data-label="lineup">
                        <template v-for="(a, idx) in ev.artists" :key="idx">
                            <div class="floor-label" v-if="a.floor && (idx === 0 || a.floor !== ev.artists[idx-1].floor)">{{ a.floor }}</div>
                            <div class="artist-row">
                                <template v-if="a.scUrl">
                                    <a :href="a.scUrl"><b v-if="a.isFollowed">{{ a.name }}</b><span v-else>{{ a.name }}</span></a>
                                </template>
                                <span v-else>{{ a.name }}</span>
                                <span class="artist-stats" v-if="a.scFollowers || a.dcHave || a.bcSupporters || a.raFollowers">
                                    <template v-if="a.scFollowers">
                                        <span v-if="a.isFollowed" style="color:var(--green)"><b>SC</b></span>
                                        <span v-else>SC</span>: <span style="color:var(--red)">{{ a.scFollowers }}</span>
                                    </template>
                                    <template v-if="a.dcHave"> &middot; DC: {{ a.dcHave }}#{{ a.dcRatio }}#{{ a.dcRating }}</template>
                                    <template v-if="a.bcSupporters">
                                        &middot; <a v-if="a.bcUrl" :href="a.bcUrl">BC</a><span v-else>BC</span>: <span style="color:var(--red)">{{ a.bcSupporters }}</span>
                                        <span v-if="a.bcLatestRelease" style="color:var(--muted);font-size:0.85em;"> ({{ a.bcLatestRelease }})</span>
                                    </template>
                                    <template v-if="a.raFollowers"> &middot; <span style="color:#d19a66;">RA: {{ a.raFollowers }}</span></template>
                                </span>
                                <i v-if="a.country"> ({{ a.country }})</i>
                                <span v-if="a.rising" title="Rising artist" style="color:#e06c75;">&#128293;</span>
                                <span v-if="a.similarTo" :title="a.similarityScore + '% similar'" style="color:#c678dd;">~ {{ a.similarTo }}</span>
                                <span v-if="a.sharedLabels.length" style="color:#98c379;">[{{ a.sharedLabels.join(', ') }}]</span>
                                <button class="artist-expand-btn" v-if="a.tags.length" @click="a.expanded = !a.expanded">
                                    {{ a.expanded ? '&#9660;' : '&#9654;' }} tags
                                </button>
                                <div class="artist-detail" v-if="a.expanded">
                                    <div class="tag-list">
                                        <span class="tag-chip" v-for="t in a.tags" :key="t">{{ t }}</span>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </td>
                    <td data-label="venue">
                        <a :href="ev.venueUrl">{{ ev.venueName }}</a>
                    </td>
                    <td data-label="promoters">
                        <template v-for="(p, i) in ev.promoters" :key="i">
                            <a :href="'https://ra.co' + p.url">{{ p.name }}</a><br v-if="i < ev.promoters.length - 1">
                        </template>
                    </td>
                    <td data-label="flyer">
                        <img v-if="ev.flyer" :src="ev.flyer" alt="flyer" style="width:200px; height:auto;">
                    </td>
                </tr>
            </tbody>
        </table>
    </div>

    <!-- Card view -->
    <div class="card-grid" v-if="viewMode === 'card'">
        <div class="event-card" v-for="ev in filteredEvents" :key="ev.id"
             :class="{followed: ev.hasFollowed}">
            <div class="card-flyer" v-if="ev.flyer">
                <img :src="ev.flyer" alt="flyer">
            </div>
            <div class="card-body">
                <div class="card-title">
                    <button class="cal-btn" @click="downloadSingleICS(ev)" title="Download .ics">&#128197;</button>
                    <a :href="ev.eventUrl">{{ ev.title }}</a>
                </div>
                <div class="card-meta">
                    <span>{{ formatDate(ev.startTime) }} {{ formatTimeRange(ev.startTime, ev.endTime) }}</span>
                    <span><a :href="ev.venueUrl">{{ ev.venueName }}</a></span>
                    <span v-if="ev.attending">{{ ev.attending }} attending</span>
                </div>
                <div class="card-genres">
                    <span v-for="g in ev.genres" :key="g.name" :class="'genre-pill ' + g.category">
                        {{ g.name }} {{ g.count }}
                    </span>
                </div>
                <div class="card-lineup">
                    <template v-for="(a, idx) in ev.artists" :key="idx">
                        <div class="floor-label" v-if="a.floor && (idx === 0 || a.floor !== ev.artists[idx-1].floor)">{{ a.floor }}</div>
                        <div class="artist-row">
                            <template v-if="a.scUrl">
                                <a :href="a.scUrl"><b v-if="a.isFollowed">{{ a.name }}</b><span v-else>{{ a.name }}</span></a>
                            </template>
                            <span v-else>{{ a.name }}</span>
                            <span class="artist-stats" v-if="a.scFollowers">SC: <span style="color:var(--red)">{{ a.scFollowers }}</span></span>
                            <span v-if="a.rising" style="color:#e06c75;">&#128293;</span>
                        </div>
                    </template>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
/* __VUE_RUNTIME__ */
</script>
<script>
const { createApp, ref, computed, onMounted, onUnmounted } = Vue;

const eventsData = "__EVENTS_DATA__";

// Add reactive _expanded to each artist
eventsData.forEach(ev => {
    ev.artists.forEach(a => { a.expanded = false; });
});

createApp({
    setup() {
        const searchQuery = ref('');
        const followedOnly = ref(false);
        const sortColumn = ref('');
        const sortAsc = ref(true);
        const viewMode = ref('table');
        const genreDropdownOpen = ref(false);
        const selectedGenres = ref([]);
        const genreDropdown = ref(null);

        const columns = [
            { key: 'time', label: 'Time' },
            { key: 'tickets', label: 'Tickets' },
            { key: 'title', label: 'Title' },
            { key: 'attenders', label: 'Attenders' },
            { key: 'strength', label: 'Strength' },
            { key: 'genre', label: 'Genre' },
            { key: 'lineup', label: 'Lineup' },
            { key: 'venue', label: 'Venue' },
            { key: 'promoters', label: 'Promoters' },
            { key: 'flyer', label: 'Flyer' },
        ];

        const allGenres = computed(() => {
            const s = new Set();
            eventsData.forEach(ev => ev.genres.forEach(g => s.add(g.name)));
            return Array.from(s).sort();
        });

        const filteredEvents = computed(() => {
            let result = eventsData;

            // Search filter
            const q = searchQuery.value.toLowerCase();
            if (q) {
                result = result.filter(ev => {
                    const text = [
                        ev.title, ev.venueName, ev.city,
                        ...ev.artists.map(a => a.name),
                        ...ev.promoters.map(p => p.name),
                    ].join(' ').toLowerCase();
                    return text.includes(q);
                });
            }

            // Followed filter
            if (followedOnly.value) {
                result = result.filter(ev => ev.hasFollowed);
            }

            // Genre filter
            if (selectedGenres.value.length > 0) {
                const sg = new Set(selectedGenres.value);
                result = result.filter(ev => ev.genres.some(g => sg.has(g.name)));
            }

            // Sort
            const col = sortColumn.value;
            if (col) {
                const asc = sortAsc.value;
                result = [...result].sort((a, b) => {
                    let va, vb;
                    switch(col) {
                        case 'time': va = a.startTime; vb = b.startTime; break;
                        case 'attenders': va = a.attending; vb = b.attending; break;
                        case 'strength': va = a.notableCount; vb = b.notableCount; break;
                        case 'title': va = a.title.toLowerCase(); vb = b.title.toLowerCase(); break;
                        case 'venue': va = a.venueName.toLowerCase(); vb = b.venueName.toLowerCase(); break;
                        default: return 0;
                    }
                    if (va < vb) return asc ? -1 : 1;
                    if (va > vb) return asc ? 1 : -1;
                    return 0;
                });
            }

            return result;
        });

        const visibleCount = computed(() => filteredEvents.value.length);
        const totalCount = eventsData.length;

        function sortBy(key) {
            if (sortColumn.value === key) {
                sortAsc.value = !sortAsc.value;
            } else {
                sortColumn.value = key;
                sortAsc.value = true;
            }
        }

        function toggleView() {
            viewMode.value = viewMode.value === 'table' ? 'card' : 'table';
        }

        function formatDate(iso) {
            const d = new Date(iso);
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            return months[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear();
        }

        function formatTimeRange(start, end) {
            const pad = n => String(n).padStart(2, '0');
            const s = new Date(start);
            const e = new Date(end);
            return pad(s.getHours()) + ':' + pad(s.getMinutes()) + ' - ' + pad(e.getHours()) + ':' + pad(e.getMinutes());
        }

        function formatStrength(ev) {
            if (!ev.totalArtists) return '';
            if (!ev.notableCount) return '0/' + ev.totalArtists;
            const pct = ev.notableCount / ev.totalArtists;
            const w = Math.max(1, Math.round(pct * 60));
            return ev.notableCount + '/' + ev.totalArtists + ' <span class="str-bar str-fill" style="width:' + w + 'px;"></span>';
        }

        function formatTickets(ev) {
            if (!ev.tickets || !ev.tickets.length) return '';
            var available = ev.tickets.filter(t => !t.soldOut);
            if (!available.length) return '<span style="color:var(--red);">SOLD OUT</span>';
            return available.map(t => {
                return 'ticket-' + t.title + ' (' + t.symbol + t.price.toFixed(2) + ')';
            }).join('<br>');
        }

        // ICS helpers
        function icsEscape(s) {
            return (s || '').replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n');
        }
        function toICSDate(iso) {
            return iso.replace(/[-:]/g, '').replace(/\.\d+/, '').substring(0, 15);
        }
        function buildVEvent(ev) {
            const lineup = ev.artists.map(a => '  ' + a.name).join('\n');
            const now = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '').substring(0, 15) + 'Z';
            return [
                'BEGIN:VEVENT',
                'UID:' + ev.id + '@techno_scan',
                'DTSTAMP:' + now,
                'DTSTART:' + toICSDate(ev.startTime),
                'DTEND:' + toICSDate(ev.endTime),
                'SUMMARY:' + icsEscape(ev.title + ' @ ' + ev.venueName),
                'LOCATION:' + icsEscape(ev.venueName),
                'URL:' + (ev.eventUrl || ''),
                'DESCRIPTION:' + icsEscape(lineup),
                'END:VEVENT'
            ].join('\r\n');
        }
        function downloadBlob(filename, content) {
            // iOS Safari doesn't support Blob downloads from file:// — use data URI
            const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
            const a = document.createElement('a');
            if (isIOS) {
                a.href = 'data:text/calendar;charset=utf-8,' + encodeURIComponent(content);
            } else {
                const blob = new Blob([content], {type:'text/calendar;charset=utf-8'});
                a.href = URL.createObjectURL(blob);
            }
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            if (!isIOS && a.href.startsWith('blob:')) URL.revokeObjectURL(a.href);
        }
        function downloadSingleICS(ev) {
            const ics = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//techno_scan//EN\r\nCALSCALE:GREGORIAN\r\nMETHOD:PUBLISH\r\n' + buildVEvent(ev) + '\r\nEND:VCALENDAR';
            const name = (ev.title || 'event').replace(/[^a-zA-Z0-9_@ -]/g, '').substring(0, 60) + '.ics';
            downloadBlob(name, ics);
        }
        function exportICS() {
            const events = filteredEvents.value;
            if (!events.length) return;
            const vevents = events.map(buildVEvent).join('\r\n');
            const ics = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//techno_scan//EN\r\nCALSCALE:GREGORIAN\r\nMETHOD:PUBLISH\r\n' + vevents + '\r\nEND:VCALENDAR';
            downloadBlob('techno_scan_events.ics', ics);
        }

        // Close genre dropdown on outside click/tap (fixes iOS touch)
        function onDocClick(e) {
            if (genreDropdownOpen.value && genreDropdown.value && !genreDropdown.value.contains(e.target)) {
                genreDropdownOpen.value = false;
            }
        }
        onMounted(() => document.addEventListener('click', onDocClick, true));
        onUnmounted(() => document.removeEventListener('click', onDocClick, true));

        // Keyboard shortcuts
        document.addEventListener('keydown', e => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            if (e.key === '/') { e.preventDefault(); document.querySelector('.toolbar input[type="text"]').focus(); }
            if (e.key === 'f') { followedOnly.value = !followedOnly.value; }
            if (e.key === 'v') { toggleView(); }
        });

        return {
            searchQuery, followedOnly, sortColumn, sortAsc, viewMode,
            genreDropdownOpen, selectedGenres, genreDropdown,
            columns, allGenres, filteredEvents, visibleCount, totalCount,
            sortBy, toggleView, formatDate, formatTimeRange, formatStrength, formatTickets,
            downloadSingleICS, exportICS,
        };
    }
}).mount('#app');
// Vue mounted successfully — show interactive app, hide static fallback
document.getElementById('app').style.display = '';
var sf = document.getElementById('static-view');
if (sf) sf.style.display = 'none';
</script>
<footer style="text-align:center;padding:1rem 0;color:var(--text-muted);font-size:0.75rem;border-top:1px solid var(--border)"><!-- __STATS_FOOTER__ --></footer>
</body>
</html>
"""
