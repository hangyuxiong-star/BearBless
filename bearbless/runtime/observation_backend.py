from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from bearbless.runtime.adb import AdbClient
from bearbless.runtime.shadow_display import ShadowDisplay


class ObservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Frame:
    display_id: int
    captured_at: str
    png: bytes


@dataclass
class AdbScreencapBackend:
    adb: AdbClient
    display: ShadowDisplay

    def capture(self, display_id: int) -> Frame:
        live_id = self.display.resolve_live_id()
        if display_id != live_id:
            raise ObservationError(f"requested display {display_id}, current shadow display is {live_id}")
        result = self.adb.shell("screencap", "-d", str(live_id), "-p", binary=True)
        if not result.ok or not isinstance(result.stdout, bytes) or not result.stdout.startswith(b"\x89PNG"):
            raise ObservationError("display-specific screencap did not return PNG data")
        return Frame(live_id, datetime.now(timezone.utc).isoformat(), result.stdout)
