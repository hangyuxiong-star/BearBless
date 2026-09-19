from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO

from PIL import Image

from bearbless.runtime.adb import AdbClient
from bearbless.errors import IsolationViolation
from bearbless.runtime.shadow_display import ShadowDisplay


class ObservationError(RuntimeError):
    pass


def _pixel_fingerprint(png: bytes) -> str:
    """Hash decoded pixels so PNG metadata cannot hide identical surfaces."""
    with Image.open(BytesIO(png)) as image:
        normalized = image.convert("RGB").resize((64, 64))
        return hashlib.sha256(normalized.tobytes()).hexdigest()


@dataclass(frozen=True)
class Frame:
    display_id: int
    captured_at: str
    png: bytes


@dataclass
class AdbScreencapBackend:
    adb: AdbClient
    display: ShadowDisplay

    def _capture_png(self, display_id: int) -> bytes:
        result = self.adb.shell("screencap", "-d", str(display_id), "-p", binary=True)
        if not result.ok or not isinstance(result.stdout, bytes) or not result.stdout.startswith(b"\x89PNG"):
            raise ObservationError(f"display-specific screencap failed for display {display_id}")
        return result.stdout

    def assert_isolated(self, display_id: int) -> None:
        """Fail closed when Android aliases the shadow capture to Display 0.

        Some Huawei reconnects keep a nominal virtual-display id while
        ``screencap -d`` silently returns the primary surface.  Checking both
        surfaces before every mutating action prevents a click from being sent
        under that false identity.
        """
        live_id = self.display.resolve_live_id()
        if display_id != live_id:
            raise ObservationError(f"requested display {display_id}, current shadow display is {live_id}")
        shadow_png = self._capture_png(live_id)
        primary_png = self._capture_png(0)
        if _pixel_fingerprint(shadow_png) == _pixel_fingerprint(primary_png):
            raise IsolationViolation(
                f"shadow display {live_id} capture is indistinguishable from Display 0"
            )

    def capture(self, display_id: int) -> Frame:
        live_id = self.display.resolve_live_id()
        if display_id != live_id:
            raise ObservationError(f"requested display {display_id}, current shadow display is {live_id}")
        png = self._capture_png(live_id)
        primary_png = self._capture_png(0)
        if _pixel_fingerprint(png) == _pixel_fingerprint(primary_png):
            raise IsolationViolation(
                f"shadow display {live_id} capture is indistinguishable from Display 0"
            )
        return Frame(live_id, datetime.now(timezone.utc).isoformat(), png)
