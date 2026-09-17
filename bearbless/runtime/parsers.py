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
    display_matches = re.findall(r"(?:displayId|mCurTokenDisplayId|targetDisplayId)\s*[=:]\s*(\d+)", text)
    token_match = re.search(r"(?:mCurToken|focusedWindow)\s*[=:]\s*([^\s]+)", text)
    return {
        "target_display_id": int(display_matches[-1]) if display_matches else None,
        "window_token": token_match.group(1) if token_match else None,
        "observable": bool(display_matches or token_match),
    }


def parse_packages(text: str) -> list[str]:
    return sorted({line.removeprefix("package:").strip() for line in text.splitlines() if line.startswith("package:")})
