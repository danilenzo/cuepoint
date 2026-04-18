"""
Club website scrapers (no RA dependency, requests-only):
  - Openground  (Wuppertal — static HTML + Schema.org JSON-LD)
  - Khidi       (Tbilisi — static HTML, pipe-separated lineups)
  - Bassiani    (Tbilisi — JSON API)

Each scraper returns a list of event dicts ready to be turned into a DataFrame.
The dicts include a '_prefilled_artists_info' key so the main enrichment
pipeline skips RA/SC/Discogs lookups for these events.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup
from loguru import logger

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

# Shared session for club scrapers — reuses TCP connections per host
_session = requests.Session()
_session.headers.update(HEADERS)

MONTH_MAP = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}

TECHNO_GENRE = [{"id": "1", "name": "Techno", "slug": "techno"}]

# ---------------------------------------------------------------------------
# Club scraper registry
# ---------------------------------------------------------------------------

_ClubScraper = Callable[[datetime, datetime], list[dict[str, Any]]]
_REGISTRY: dict[str, list[_ClubScraper]] = {}


def register_club(city: str) -> Callable[[_ClubScraper], _ClubScraper]:
    """Decorator to register a club scraper for a city.

    Usage:
        @register_club("Berlin")
        def scrape_berghain(start_date, end_date): ...
    """

    def decorator(fn: _ClubScraper) -> _ClubScraper:
        _REGISTRY.setdefault(city, []).append(fn)
        return fn

    return decorator


def get_registered_cities() -> list[str]:
    """Return city names that have registered club scrapers."""
    return list(_REGISTRY.keys())


def scrape_city_clubs(city: str, start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """Run all registered scrapers for a city and combine results."""
    scrapers = _REGISTRY.get(city, [])
    all_events = []
    for scraper in scrapers:
        try:
            all_events.extend(scraper(start_date, end_date))
        except Exception as e:
            logger.warning(f"{scraper.__name__} failed: {e}")
    return all_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_artist(venue_id: str, name: str) -> dict[str, Any]:
    """Minimal artist dict — no RA/SC/Discogs data."""
    name = name.strip()
    slug = re.sub(r"\W+", "_", name.lower()).strip("_")
    return {
        "id": f"{venue_id}_{slug}",
        "name": name,
        "soundcloud": None,
        "discogs": None,
        "contentUrl": None,
        "country": None,
    }


def _parse_lineup(venue_id: str, text: str) -> list[dict[str, Any]]:
    """
    Split a free-text lineup string into artist stub dicts.
    Handles: 'A B2B B | C | D', floor prefixes like 'G2:', live suffixes.
    """
    artists = []
    for segment in re.split(r"\|", text):
        segment = re.sub(r"^[A-Z0-9]+\s*:\s*", "", segment.strip())  # strip floor prefix
        for name in re.split(r"\s+[Bb]2[Bb]\s+", segment):
            name = re.sub(r"\s*\(live\)\s*", "", name, flags=re.IGNORECASE).strip()
            if len(name) > 1:
                artists.append(_stub_artist(venue_id, name))
    return artists


def _make_ticket(
    price: float | str, currency_code: str, title: str = "General Admission", valid_type: str = "VALID"
) -> dict[str, Any]:
    """Build a ticket dict matching the RA ticket structure."""
    return {
        "title": title,
        "priceRetail": float(price),
        "validType": valid_type,
        "currency": {"code": currency_code},
    }


def _event_dict(
    venue_id: str,
    venue_name: str,
    venue_url: str,
    event_dt: datetime,
    end_dt: datetime,
    title: str,
    url: str,
    artists: list[dict[str, Any]],
    flyer: str | None = None,
    tickets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Use URL-derived slug for event_id to keep same-day events unique
    url_slug = re.sub(r"[^a-z0-9]+", "_", url.lower()).strip("_")
    event_id = f"{venue_id}_{url_slug}"
    return {
        "listing_id": event_id,
        "listing_date": event_dt,
        "event_id": event_id,
        "event_date": event_dt,
        "start_time": event_dt,
        "end_time": end_dt,
        "title": title,
        "content_url": url,
        "event_url": url,
        "is_ticketed": True,
        "attending": 0,
        "venue_id": venue_id,
        "venue_name": venue_name,
        "venue_url": venue_url,
        "images": [{"filename": flyer}] if flyer else [],
        "artists": [{"id": a["id"], "name": a["name"]} for a in artists],
        "promoters": [],
        "tickets": tickets or [],
        "genres": TECHNO_GENRE,
        "_prefilled_artists_info": artists,
    }


# ---------------------------------------------------------------------------
# Openground
# ---------------------------------------------------------------------------

_OPENGROUND_BASE = "https://www.openground.club"
_SKIP_LINES = re.compile(
    r"^\w{3}\.\d{2}\.\d{2}\.\d{2}$"  # date: Sat.21.03.26
    r"|^\d{2}:\d{2}[–\-]\d{2}:\d{2}$"  # time: 22:00–07:00
    r"|^(Clubnight|Concert|Special Event|Open Lobby|Extended Clubnight|Workshop)$",
    re.IGNORECASE,
)
_VENUE_LINE = re.compile(r"^[A-Z][A-Z\s]{2,}$")  # all-caps venue names: FREIFELD, ANNEX


def _openground_parse_detail_page(dsoup: BeautifulSoup) -> list[dict[str, Any]]:
    """
    Parse floor-separated artists from the Openground detail page HTML.
    Each artist gets a 'floor' key (e.g. 'FREIFELD', 'ANNEX') and
    a 'soundcloud' URL extracted from the artist's bio links if present.
    """
    artists = []
    for section in dsoup.find_all("div", class_="event-info"):
        floor_div = section.find("div", class_="event-info__floor__label")
        floor_name = floor_div.get_text(strip=True) if floor_div else None

        for item in section.find_all("div", class_="event-info__item"):
            name_div = item.find("div", class_="event-item__accordion-top-name")
            if not name_div:
                continue
            name = name_div.get_text(strip=True)
            if not name or len(name) < 2:
                continue

            stub = _stub_artist("openground", name)

            # SoundCloud URL from the artist bio links on the detail page
            links_div = item.find("div", class_="event-item__accordion-content-links")
            if links_div:
                for a in links_div.find_all("a", href=True):
                    if "soundcloud.com" in a["href"]:
                        stub["soundcloud"] = a["href"].replace("www.soundcloud.com", "soundcloud.com")
                        break

            city_div = item.find("div", class_="event-item__accordion-top-city")
            if city_div:
                city = city_div.get_text(strip=True)
                if city:
                    stub["country"] = {"name": city}

            if floor_name:
                stub["floor"] = floor_name

            artists.append(stub)
    return artists


def _openground_artists_from_anchor(anchor: BeautifulSoup) -> list[dict[str, Any]]:
    """Fallback: extract artist names from the homepage anchor (no floor/SC info)."""
    artists = []
    for p in anchor.find_all("p"):
        text = p.get_text(separator="\n", strip=True)
        for line in text.splitlines():
            line = line.strip()
            if not line or _SKIP_LINES.match(line) or _VENUE_LINE.match(line):
                continue
            for name in re.split(r"\s+b2b\s+", line, flags=re.IGNORECASE):
                name = re.sub(r"\s*\(live\)\s*", "", name, flags=re.IGNORECASE).strip()
                if name and len(name) > 1:
                    artists.append(_stub_artist("openground", name))
    return artists


@register_club("Wuppertal")
def scrape_openground(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """
    1. Fetch the homepage to find all event links in the date range.
    2. For each event, fetch the detail page and parse performers from JSON-LD.
    3. Fall back to parsing artist names directly from the homepage anchor if no JSON-LD.
    """
    events = []
    try:
        r = _session.get(f"{_OPENGROUND_BASE}/en/", timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Collect all schedule links whose URL date is in range
        all_links = soup.find_all("a", href=re.compile(r"/en/schedule/\d{4}-\d{2}-\d{2}"))
        logger.info(f"Openground: found {len(all_links)} total schedule links on homepage")
        for a in all_links:
            logger.debug(f"  Openground link: {a['href']}")

        in_range = []
        seen_hrefs = set()
        for anchor in all_links:
            href = anchor["href"]
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            m = re.search(r"/en/schedule/(\d{4})-(\d{2})-(\d{2})", href)
            if not m:
                continue
            try:
                event_dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 22, 0)
            except ValueError:
                continue
            if start_date <= event_dt <= end_date + timedelta(days=1):
                in_range.append((event_dt, href, anchor))

        logger.info(f"Openground: {len(in_range)} links match date range {start_date.date()} – {end_date.date()}")

        for event_dt, href, anchor in in_range:
            url = _OPENGROUND_BASE + href
            artists = []
            end_dt = event_dt + timedelta(hours=8)
            title = "Clubnight @ Openground"
            tickets = []

            # Extract ticket price from the parent container (e.g. "Tickets 20€")
            # The price text is outside the <a> tag, in the parent div.newhome-block-box
            box = anchor.find_parent("div", class_="newhome-block-box")
            box_text = box.get_text(" ", strip=True) if box else anchor.get_text(" ", strip=True)
            price_match = re.search(r"Tickets\s+(\d+)", box_text)
            if price_match:
                tickets = [_make_ticket(price_match.group(1), "EUR")]
            elif re.search(r"Free\s+Entry", box_text, re.IGNORECASE):
                tickets = [_make_ticket(0, "EUR", title="Free Entry")]
            elif re.search(r"Sold\s+out", box_text, re.IGNORECASE):
                tickets = [_make_ticket(0, "EUR", title="Sold Out", valid_type="SOLDOUT")]

            # Primary: JSON-LD on the detail page
            flyer = None
            try:
                detail = _session.get(url, timeout=15)
                detail.raise_for_status()
                dsoup = BeautifulSoup(detail.text, "html.parser")

                # Extract og:image as flyer
                og_img = dsoup.find("meta", property="og:image")
                if og_img and og_img.get("content"):  # type: ignore[union-attr]
                    flyer = str(og_img["content"])  # type: ignore[index]

                # Get title and end time from JSON-LD
                for script in dsoup.find_all("script", type="application/ld+json"):
                    try:
                        data = json.loads(script.string or "")
                        items = data if isinstance(data, list) else [data]
                        for item in items:
                            if item.get("@type") != "Event":
                                continue
                            title = item.get("name") or title
                            end_str = item.get("endDate", "")
                            if end_str:
                                try:
                                    end_dt = datetime.fromisoformat(end_str[:19])
                                except ValueError:
                                    pass
                            break
                    except Exception:
                        continue

                # Fallback: extract ticket price from detail page if homepage didn't have it
                if not tickets:
                    detail_text = dsoup.get_text(" ", strip=True)
                    dp = re.search(r"(?:Admission|Tickets)\s+(\d+)\s*", detail_text)
                    if dp:
                        tickets = [_make_ticket(dp.group(1), "EUR")]

                # Get floor-separated artists with SC URLs from HTML
                artists = _openground_parse_detail_page(dsoup)
            except Exception as e:
                logger.warning(f"Openground detail page failed ({url}): {e}")

            # Fallback: parse from homepage anchor HTML
            if not artists:
                logger.debug(f"Openground: no JSON-LD performers for {href}, trying anchor fallback")
                artists = _openground_artists_from_anchor(anchor)

            logger.info(f"Openground event {href}: '{title}' — {len(artists)} artists: {[a['name'] for a in artists]}")

            events.append(
                _event_dict(
                    "openground",
                    "Openground",
                    _OPENGROUND_BASE,
                    event_dt,
                    end_dt,
                    title,
                    url,
                    artists,
                    flyer=flyer,
                    tickets=tickets,
                )
            )

    except Exception as e:
        logger.warning(f"Openground scrape failed: {e}")

    logger.info(f"Openground: {len(events)} events in date range")
    return events


# ---------------------------------------------------------------------------
# Khidi
# ---------------------------------------------------------------------------


@register_club("Tbilisi")
def scrape_khidi(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """
    1. Fetch program page, find event links (khidi.ge/event/DDMMYY/).
    2. Extract date from URL slug.
    3. Fetch each in-range event detail page for lineup and ticket prices.
    """
    events = []
    try:
        r = _session.get("https://khidi.ge/program/", timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Collect unique event URLs from program page
        seen = set()
        event_links = []
        for a in soup.find_all("a", href=re.compile(r"khidi\.ge/event/\d+/")):
            href = a["href"]
            if href in seen:
                continue
            seen.add(href)
            event_links.append(href)

        logger.info(f"Khidi: found {len(event_links)} event links on homepage")

        for event_url in event_links:
            # Date is encoded in the URL slug as DDMMYY
            m = re.search(r"/event/(\d{2})(\d{2})(\d{2})/?$", event_url)
            if not m:
                continue
            day, month, year_2d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                event_dt = datetime(2000 + year_2d, month, day, 22, 0)
            except ValueError:
                continue

            if not (start_date <= event_dt <= end_date + timedelta(days=1)):
                continue

            # Fetch detail page for lineup, ticket prices, and flyer
            artists = []
            tickets = []
            flyer = None
            try:
                detail = _session.get(event_url, timeout=15)
                detail.raise_for_status()
                dsoup = BeautifulSoup(detail.text, "html.parser")

                # Extract flyer from og:image
                og_img = dsoup.find("meta", property="og:image")
                if og_img and og_img.get("content"):  # type: ignore[union-attr]
                    flyer = str(og_img["content"])  # type: ignore[index]

                for editor in dsoup.find_all("div", class_="elementor-widget-text-editor"):
                    text = editor.get_text(separator=" ", strip=True)
                    if len(text) > 2:
                        artists = _parse_lineup("khidi", text)
                        if artists:
                            break

                # Extract pre-sale tiers: "I PRE-SALE: 40 GEL [SOLD OUT]"
                page_text = dsoup.get_text(" ", strip=True)
                for tier_match in re.finditer(
                    r"(I{1,4}V?\s+PRE-SALE)\s*:\s*(\d+)\s*GEL\s*(\[SOLD\s*OUT\])?",
                    page_text,
                ):
                    tier_name = tier_match.group(1)
                    price = tier_match.group(2)
                    sold = bool(tier_match.group(3))
                    tickets.append(
                        _make_ticket(price, "GEL", title=tier_name, valid_type="SOLDOUT" if sold else "VALID")
                    )

            except Exception as e:
                logger.warning(f"Khidi detail page failed ({event_url}): {e}")

            title = f"Khidi — {event_dt.strftime('%d %b %Y')}"
            events.append(
                _event_dict(
                    "khidi",
                    "Khidi",
                    "https://khidi.ge",
                    event_dt,
                    event_dt + timedelta(hours=10),
                    title,
                    event_url,
                    artists,
                    flyer=flyer,
                    tickets=tickets,
                )
            )
            logger.info(f"Khidi {event_url}: {len(artists)} artists: {[a['name'] for a in artists]}")

    except Exception as e:
        logger.warning(f"Khidi scrape failed: {e}")

    logger.info(f"Khidi: {len(events)} events in date range")
    return events


# ---------------------------------------------------------------------------
# Bassiani  (JSON API)
# ---------------------------------------------------------------------------

_BASSIANI_API = "https://bassiani.com/api/"
_BASSIANI_HEADERS = {
    **HEADERS,
    "Referer": "https://bassiani.com/nights",
}


@register_club("Tbilisi")
def scrape_bassiani(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """Fetch Bassiani events from their JSON API and parse room-separated lineups."""
    events = []
    try:
        r = _session.get(
            _BASSIANI_API,
            params={"app": "WebNight", "resource": "list", "page": "1"},
            headers={"Referer": "https://bassiani.com/nights"},
            timeout=15,
        )
        r.raise_for_status()
        posts = r.json().get("data", {}).get("posts", [])
        logger.info(f"Bassiani API: {len(posts)} total nights fetched")

        for post in posts:
            raw_start = post.get("event_start", "")
            try:
                event_dt = datetime.strptime(raw_start[:16], "%Y-%m-%d %H:%M")
                event_dt = event_dt.replace(hour=23, minute=59)
            except ValueError:
                continue

            if not (start_date <= event_dt <= end_date + timedelta(days=1)):
                continue

            post_id = post.get("id")
            title = post.get("title") or f"Bassiani — {event_dt.strftime('%d %b %Y')}"
            url = f"https://bassiani.com{post.get('url', f'/light/nights/{post_id}')}"
            flyer = None
            img = post.get("main_image_path") or post.get("share_image_path")
            if img:
                flyer = f"https://bassiani.com{img}"

            # Parse room-separated lineup from the line_up JSON field
            artists = []
            raw_lineup = post.get("line_up") or ""
            if raw_lineup:
                try:
                    rooms = json.loads(raw_lineup)
                    for room in rooms:
                        room_name = room.get("name", "").strip()
                        for artist in room.get("data", []):
                            name = artist.get("name", "").strip()
                            if not name or len(name) < 2:
                                continue
                            for part in re.split(r"\s+[Bb]2[Bb]\s+", name):
                                part = re.sub(r"\s*\(live\)\s*", "", part, flags=re.IGNORECASE).strip()
                                if part and len(part) > 1:
                                    stub = _stub_artist("bassiani", part)
                                    if room_name:
                                        stub["floor"] = room_name
                                    artists.append(stub)
                except (json.JSONDecodeError, TypeError, AttributeError) as e:
                    logger.warning(f"Bassiani lineup parse failed for id={post_id}: {e}")

            # Fallback to sub_title if line_up is absent/empty
            if not artists and post.get("sub_title"):
                artists = _parse_lineup("bassiani", post["sub_title"])

            # Extract ticket price from API response
            tickets = []
            raw_price = post.get("price")
            if raw_price and float(raw_price) > 0:
                selling = post.get("selling", 0)
                tickets = [_make_ticket(raw_price, "GEL", valid_type="VALID" if selling else "SOLDOUT")]

            logger.info(f"Bassiani {event_dt.date()} '{title}': {len(artists)} artists: {[a['name'] for a in artists]}")
            events.append(
                _event_dict(
                    "bassiani",
                    "Bassiani",
                    "https://bassiani.com",
                    event_dt,
                    event_dt + timedelta(hours=10),
                    title,
                    url,
                    artists,
                    flyer=flyer,
                    tickets=tickets,
                )
            )

    except Exception as e:
        logger.warning(f"Bassiani scrape failed: {e}")

    logger.info(f"Bassiani: {len(events)} events in date range")
    return events


# ---------------------------------------------------------------------------
# Berghain
# ---------------------------------------------------------------------------

_BERGHAIN_BASE = "https://www.berghain.berlin"


@register_club("Berlin")
def scrape_berghain(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """
    Fetch the Berghain program page and parse floor-separated lineups.
    Artist names from listing page; detail page for running order times.
    """
    events = []
    try:
        r = _session.get(f"{_BERGHAIN_BASE}/en/program/", timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for anchor in soup.select("a.upcoming-event"):
            href = str(anchor.get("href", ""))
            if not href:
                continue

            # Extract date from listing: "21.03.2026" in span.font-bold
            date_span = anchor.select_one("p > span.font-bold")
            if not date_span:
                continue
            date_text = date_span.get_text(strip=True)
            m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", date_text)
            if not m:
                continue
            try:
                event_dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), 23, 59)
            except ValueError:
                continue

            if not (start_date <= event_dt <= end_date + timedelta(days=1)):
                continue

            # Title from h2
            h2 = anchor.select_one("h2")
            title = h2.get_text(strip=True) if h2 else "Berghain"

            # Parse floor-separated artists from h3 (floor) + h4 (artists) pairs
            artists = []
            current_floor = None
            for child in anchor.find_all(["h3", "h4"]):
                if child.name == "h3":
                    current_floor = child.get_text(strip=True)
                elif child.name == "h4":
                    for span in child.select("span.font-bold > span"):
                        # Skip "Live" indicator spans
                        if span.select_one("span.uppercase"):
                            continue
                        name = span.get_text(strip=True)
                        # Clean up live suffix that may remain
                        name = re.sub(r"\s*Live\s*$", "", name).strip()
                        if not name or len(name) < 2:
                            continue
                        stub = _stub_artist("berghain", name)
                        if current_floor:
                            stub["floor"] = current_floor
                        artists.append(stub)

            if not artists:
                continue

            url = _BERGHAIN_BASE + href if href.startswith("/") else href
            logger.info(f"Berghain {date_text}: '{title}' — {len(artists)} artists: {[a['name'] for a in artists]}")

            events.append(
                _event_dict(
                    "berghain",
                    "Berghain",
                    _BERGHAIN_BASE,
                    event_dt,
                    event_dt + timedelta(hours=12),
                    title,
                    url,
                    artists,
                )
            )

    except Exception as e:
        logger.warning(f"Berghain scrape failed: {e}")

    logger.info(f"Berghain: {len(events)} events in date range")
    return events


# ---------------------------------------------------------------------------
# Tresor
# ---------------------------------------------------------------------------

_TRESOR_BASE = "https://tresorberlin.com"


@register_club("Berlin")
def scrape_tresor(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """
    1. Fetch events listing page, extract event links with dates from URL slugs.
    2. For each in-range event, fetch detail page for floor-separated lineup,
       artist SC/RA links, flyer image, and ticket links.
    """
    events = []
    try:
        r = _session.get(f"{_TRESOR_BASE}/events", timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Collect event URLs — date is in the URL: /event/YYYYMMDD-slug/
        seen = set()
        event_links = []
        for article in soup.select("article.event-item"):
            a = article.select_one("div.event-date a.plus-link")
            if not a:
                continue
            href = str(a.get("href", ""))
            if href in seen:
                continue
            seen.add(href)

            m = re.search(r"/event/(\d{4})(\d{2})(\d{2})-", href)
            if not m:
                continue
            try:
                event_dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 23, 59)
            except ValueError:
                continue

            if not (start_date <= event_dt <= end_date + timedelta(days=1)):
                continue

            # Title from listing
            title_el = article.select_one("a.event-title span span")
            title = title_el.get_text(strip=True) if title_el else "Tresor"

            # Lineup from listing page (fallback if detail page fails)
            listing_artists = []
            for floor_div in article.select("div.event-floor[data-floor]"):
                floor_name_el = floor_div.select_one("div.floor-name")
                floor_name = floor_name_el.get_text(strip=True) if floor_name_el else None
                for artist_div in floor_div.select("div.floor-artist > span"):
                    name = artist_div.get_text(strip=True)
                    name = re.sub(r"\s*\[?LIVE\]?\s*", "", name, flags=re.IGNORECASE).strip()
                    if not name or len(name) < 2:
                        continue
                    if re.match(r"^all\s+night\s+long$", name, re.IGNORECASE):
                        continue
                    for part in re.split(r"\s+[Bb]2[Bb]\s+", name):
                        part = part.strip()
                        if part and len(part) > 1:
                            stub = _stub_artist("tresor", part)
                            if floor_name:
                                stub["floor"] = floor_name
                            listing_artists.append(stub)

            full_url = href if href.startswith("http") else _TRESOR_BASE + href
            event_links.append((event_dt, title, full_url, listing_artists))

        logger.info(f"Tresor: {len(event_links)} events in date range")

        for event_dt, title, event_url, listing_artists in event_links:
            artists: list[dict[str, Any]] = []
            flyer: str | None = None
            tickets: list[dict[str, Any]] = []

            # Fetch detail page for richer data
            try:
                detail = _session.get(event_url, timeout=15)
                detail.raise_for_status()
                dsoup = BeautifulSoup(detail.text, "html.parser")

                # Flyer from hero image
                hero_img = dsoup.select_one("aside.hero-outer picture img[src]")
                if hero_img and hero_img.get("src"):
                    flyer = str(hero_img["src"])

                # Floor-separated lineup with SC/RA links
                for floor_div in dsoup.select("div.lineup > div.floor[data-floor]"):
                    floor_name_el = floor_div.select_one("div.floor-name")
                    floor_name = floor_name_el.get_text(strip=True) if floor_name_el else None

                    for item in floor_div.select("a.lineup-item"):
                        name_el = item.select_one("div.lineup-name")
                        if not name_el:
                            continue
                        raw_name = name_el.get_text(strip=True)
                        raw_name = re.sub(r"\s*\[?LIVE\]?\s*", "", raw_name, flags=re.IGNORECASE).strip()
                        if not raw_name or len(raw_name) < 2:
                            continue
                        # Skip non-artist entries
                        if re.match(r"^all\s+night\s+long$", raw_name, re.IGNORECASE):
                            continue

                        # Extract SC URL from artist link
                        link_href = str(item.get("href", ""))
                        sc_url = None
                        if "soundcloud.com" in link_href:
                            sc_url = link_href.replace("www.soundcloud.com", "soundcloud.com")

                        # Split B2B artists
                        for part in re.split(r"\s+[Bb]2[Bb]\s+", raw_name):
                            part = part.strip()
                            if not part or len(part) < 2:
                                continue
                            stub = _stub_artist("tresor", part)
                            if floor_name:
                                stub["floor"] = floor_name
                            if sc_url:
                                stub["soundcloud"] = sc_url
                                sc_url = None  # only assign to first artist in B2B
                            artists.append(stub)

                # Ticket link (usually RA link)
                for a in dsoup.select("article.main-text a[href]"):
                    ticket_href = str(a.get("href", ""))
                    if "ra.co/events" in ticket_href:
                        # We don't extract price from RA, just note it's ticketed
                        break

            except Exception as e:
                logger.warning(f"Tresor detail page failed ({event_url}): {e}")

            # Fall back to listing page lineup if detail page had no artists
            if not artists:
                artists = listing_artists

            if not artists:
                continue

            logger.info(f"Tresor {event_dt.date()}: '{title}' — {len(artists)} artists: {[a['name'] for a in artists]}")

            events.append(
                _event_dict(
                    "tresor",
                    "Tresor",
                    _TRESOR_BASE,
                    event_dt,
                    event_dt + timedelta(hours=10),
                    title,
                    event_url,
                    artists,
                    flyer=flyer,
                    tickets=tickets,
                )
            )

    except Exception as e:
        logger.warning(f"Tresor scrape failed: {e}")

    logger.info(f"Tresor: {len(events)} events in date range")
    return events
