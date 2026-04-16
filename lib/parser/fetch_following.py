"""
Fetches your SoundCloud following list and updates FOLLOWING in following.py.
Uses SoundCloud's internal API — no browser/Selenium required.

Usage (from lib/parser/):
    python fetch_following.py https://soundcloud.com/your-username
"""

import re
import sys

import requests

from generic import BASE_PATH


def get_client_id(session: requests.Session) -> str:
    """Extract the client_id embedded in SoundCloud's JS bundles."""
    r = session.get("https://soundcloud.com", timeout=15)
    r.raise_for_status()

    # Find JS bundle URLs in the page
    js_urls = re.findall(r'https://[^"]+\.js', r.text)
    # Deduplicate, prefer smaller numbered bundles (they tend to have auth config)
    js_urls = list(dict.fromkeys(js_urls))

    for js_url in js_urls[-5:]:  # check last few bundles
        try:
            js_r = session.get(js_url, timeout=10)
            match = re.search(r'client_id\s*:\s*"([a-zA-Z0-9]{32})"', js_r.text)
            if match:
                return match.group(1)
        except requests.RequestException:
            continue

    raise RuntimeError("Could not extract client_id from SoundCloud JS bundles.")


def resolve_user_id(username: str, client_id: str, session: requests.Session) -> int:
    """Resolve a SC username/profile-url to a numeric user ID."""
    url = "https://api-v2.soundcloud.com/resolve"
    r = session.get(url, params={"url": f"https://soundcloud.com/{username}", "client_id": client_id}, timeout=15)
    r.raise_for_status()
    return int(r.json()["id"])


def fetch_following_slugs(profile_url: str) -> list[str]:
    username = profile_url.rstrip("/").split("/")[-1]

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    )

    print("Getting client_id...")
    client_id = get_client_id(session)
    print(f"client_id: {client_id}")

    print(f"Resolving user ID for '{username}'...")
    user_id = resolve_user_id(username, client_id, session)
    print(f"User ID: {user_id}")

    slugs = []
    url = f"https://api-v2.soundcloud.com/users/{user_id}/followings"
    params: dict[str, str] = {"client_id": client_id, "limit": "200"}

    while url:
        r = session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        for user in data.get("collection", []):
            permalink = user.get("permalink")
            if permalink:
                slugs.append(f"/{permalink}")

        print(f"  {len(slugs)} followings fetched...")

        next_href = data.get("next_href")
        if next_href:
            url = next_href
            params = {"client_id": client_id}  # next_href has offset but not client_id
        else:
            break

    return slugs


def update_following_py(slugs: list[str]) -> None:
    following_path = BASE_PATH / "lib/parser/following.py"

    with open(following_path, encoding="utf-8") as f:
        content = f.read()

    items_str = ", ".join(repr(s) for s in slugs)
    new_list = f"FOLLOWING = set([{items_str}])"

    new_content = re.sub(
        r"FOLLOWING\s*=\s*(?:set\()?\[.*?\]\)?",
        new_list,
        content,
        flags=re.DOTALL,
    )

    with open(following_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"following.py updated with {len(slugs)} artists.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fetch_following.py https://soundcloud.com/your-username")
        sys.exit(1)

    profile_url = sys.argv[1]

    slugs = fetch_following_slugs(profile_url)

    if not slugs:
        print("No followings found. Is the profile URL correct and public?")
        sys.exit(1)

    print(f"Total: {len(slugs)} followings fetched.")
    update_following_py(slugs)
    print("Done.")
