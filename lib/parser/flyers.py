from typing import Any


def get_flyer(event_dict: dict[str, Any]) -> str | None:
    try:
        val = event_dict["images"][0]["filename"]
        return str(val) if val else None
    except (KeyError, IndexError, TypeError):
        return None
