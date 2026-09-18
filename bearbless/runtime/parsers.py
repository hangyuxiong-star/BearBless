from __future__ import annotations

import re


_DISPLAY_PATTERNS = (
    re.compile(r"(?:display(?:Id| id)?|virtual display)\s*[=:]\s*(\d+)", re.I),
    re.compile(r"--display-id[= ](\d+)", re.I),
    re.compile(r"Display\s+(\d+):"),
)


def parse_display_ids(text: str) -> list[int]:
    ids: set[int] = set()
    for pattern in _DISPLAY_PATTERNS:
        ids.update(int(match) for match in pattern.findall(text))
    return sorted(ids)


def parse_adb_devices(text: str) -> list[str]:
    devices = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device":
            devices.append(fields[0])
    return devices


def supports_display_targeted_input(text: str) -> bool:
    return bool(re.search(r"(?:^|[\s\[])-d(?:[\s,\]]|$)|--display", text, re.MULTILINE))


def parse_wm_size(text: str) -> str | None:
    matches = re.findall(r"(?:Physical|Override) size:\s*(\d+x\d+)", text)
    return matches[-1] if matches else None


def parse_ime_state(text: str) -> dict[str, object]:
    # Only use the current InputMethodManager fields.  A dumpsys also contains
    # hundreds of historical/client ``displayId`` values; taking the last one
    # silently reports an arbitrary stale virtual display.
    display_match = re.search(r"^\s*mCurTokenDisplayId\s*[=:]\s*(\d+)", text, re.MULTILINE)
    token_match = re.search(r"^\s*mCurToken\s*[=:]\s*([^\s]+)", text, re.MULTILINE)
    shown_match = re.search(r"\bmInputShown\s*[=:]\s*(true|false)\b", text, re.I)
    if shown_match is None:
        shown_match = re.search(r"\bmIsInputViewShown\s*[=:]\s*(true|false)\b", text, re.I)
    return {
        "target_display_id": int(display_match.group(1)) if display_match else None,
        "window_token": token_match.group(1) if token_match else None,
        "visible": shown_match.group(1).lower() == "true" if shown_match else None,
        "observable": bool(display_match or token_match or shown_match),
    }


def parse_packages(text: str) -> list[str]:
    return sorted({line.removeprefix("package:").strip() for line in text.splitlines() if line.startswith("package:")})
