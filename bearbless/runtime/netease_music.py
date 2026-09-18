from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bearbless.runtime.media_session import requested_track


NETEASE_SEARCH_ENDPOINT = "https://music.163.com/api/search/get"


def resolve_netease_song_uri(
    goal: str,
    *,
    fetch: Callable[[Request, float], bytes] | None = None,
    timeout: float = 5.0,
) -> str | None:
    """Resolve an exact song title to a NetEase deep link.

    This is the preferred media skill path because NetEase focuses its search
    box automatically on a secondary display, causing Android's singleton IME
    to appear on Display 0.  A semantic lookup plus app-owned deep link avoids
    all text focus while keeping playback inside the official app.
    """
    target = requested_track(goal)
    if not target or "播放" not in goal or not any(name in goal for name in ("网易云", "音乐")):
        return None
    query = urlencode({"s": target, "type": 1, "offset": 0, "total": "true", "limit": 10})
    request = Request(
        f"{NETEASE_SEARCH_ENDPOINT}?{query}",
        headers={
            "Referer": "https://music.163.com/",
            "User-Agent": "Mozilla/5.0 BearBless/1.0",
        },
    )
    try:
        if fetch is None:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS host
                payload = response.read()
        else:
            payload = fetch(request, timeout)
        data = json.loads(payload.decode("utf-8"))
        songs = data.get("result", {}).get("songs", [])
    except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None
    normalized_target = target.replace(" ", "").casefold()
    for song in songs if isinstance(songs, list) else []:
        if not isinstance(song, dict):
            continue
        name = str(song.get("name") or "").replace(" ", "").casefold()
        song_id = song.get("id")
        if name == normalized_target and isinstance(song_id, int) and song_id > 0:
            return f"orpheus://song/{song_id}"
    return None
