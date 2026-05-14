"""
Fetches your SoundCloud following list and saves to following.txt.
Uses SoundCloud's internal API — no browser/Selenium required.

Usage:
    python -m cuepoint.fetch_following https://soundcloud.com/your-username

On first run the profile URL is saved to .sc_profile. Subsequent runs with a
different URL are rejected unless --force is passed — this prevents accidentally
overwriting your following list with someone else's.
"""

import re
import sys

import requests

from .generic import BASE_PATH

_PROFILE_FILE = BASE_PATH / ".sc_profile"
_FOLLOWING_FILE = BASE_PATH / "following.txt"


def _check_profile_lock(profile_url: str, *, force: bool) -> None:
    """Ensure we're syncing the same profile as last time."""
    normalised = profile_url.rstrip("/").lower()

    if _PROFILE_FILE.exists():
        saved = _PROFILE_FILE.read_text(encoding="utf-8").strip()
        if saved != normalised:
            if force:
                print(f"--force: switching profile from {saved} to {normalised}")
            else:
                print(
                    f"ERROR: saved profile is {saved}, "
                    f"but you passed {normalised}.\n"
                    f"Pass --force to overwrite with the new profile's followings."
                )
                sys.exit(1)

    _PROFILE_FILE.write_text(normalised, encoding="utf-8")


_CLIENT_ID_PATTERNS = [
    re.compile(r'client_id\s*:\s*"([a-zA-Z0-9]{32})"'),
    re.compile(r"client_id\s*:\s*'([a-zA-Z0-9]{32})'"),
    re.compile(r'clientId\s*[:=]\s*"([a-zA-Z0-9]{32})"'),
]


def get_client_id(session: requests.Session) -> str:
    """Extract the client_id embedded in SoundCloud's JS bundles."""
    for _attempt in range(3):
        r = session.get("https://soundcloud.com", timeout=15)
        r.raise_for_status()
        js_urls = list(dict.fromkeys(re.findall(r'https://[^"\']+\.js', r.text)))
        sndcdn = [u for u in js_urls if "sndcdn.com" in u]
        if sndcdn:
            break
    else:
        raise RuntimeError("Could not load SoundCloud JS bundles after 3 attempts.")

    for js_url in sndcdn:
        try:
            js_r = session.get(js_url, timeout=10)
            for pat in _CLIENT_ID_PATTERNS:
                match = pat.search(js_r.text)
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


def update_following(slugs: list[str]) -> None:
    """Write slugs to following.txt and reload in-memory set."""
    _FOLLOWING_FILE.write_text("\n".join(sorted(slugs)) + "\n", encoding="utf-8")

    from . import following as _fmod

    _fmod.reload_following()

    print(f"following.txt updated with {len(slugs)} artists.")


def show_following() -> None:
    """Print the current FOLLOWING set."""
    from .following import FOLLOWING

    if not FOLLOWING:
        print("FOLLOWING is empty. Run: python -m cuepoint.fetch_following <profile_url>")
        return

    print(f"Currently following {len(FOLLOWING)} artists:\n")
    for slug in sorted(FOLLOWING):
        print(f"  soundcloud.com{slug}")


if __name__ == "__main__":
    if "--show" in sys.argv:
        show_following()
        sys.exit(0)

    force = "--force" in sys.argv
    args = [a for a in sys.argv[1:] if a not in ("--force", "--show")]

    if len(args) < 1:
        print(
            "Usage:\n"
            "  python -m cuepoint.fetch_following <profile_url> [--force]\n"
            "  python -m cuepoint.fetch_following --show"
        )
        sys.exit(1)

    profile_url = args[0]

    _check_profile_lock(profile_url, force=force)

    slugs = fetch_following_slugs(profile_url)

    if not slugs:
        print("No followings found. Is the profile URL correct and public?")
        sys.exit(1)

    print(f"Total: {len(slugs)} followings fetched.")
    update_following(slugs)
    print("Done.")
