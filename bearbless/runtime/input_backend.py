from __future__ import annotations

import base64
from dataclasses import dataclass

from bearbless.runtime.adb import AdbClient
from bearbless.runtime.shadow_display import ShadowDisplay


class InputBackendError(RuntimeError):
    pass


@dataclass
class AccessibilityTextBridge:
    """Display-scoped Unicode text entry through the BearBless Android bridge."""

    adb: AdbClient
    authority: str = "com.bearbless.bridge.control"

    def available(self) -> bool:
        result = self.adb.shell(
            "content", "call", "--uri", f"content://{self.authority}",
            "--method", "health",
        )
        output = self._output(result)
        return result.ok and "enabled=true" in output

    def set_text(self, display_id: int, text: str) -> None:
        encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")
        result = self.adb.shell(
            "content", "call", "--uri", f"content://{self.authority}",
            "--method", "set_text", "--arg", f"{display_id}:{encoded}",
        )
        output = self._output(result)
        if not result.ok or "ok=true" not in output:
            reason = output.strip() or "BearBless accessibility bridge rejected text entry"
            raise InputBackendError(reason)

    @staticmethod
    def _output(result: object) -> str:
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return f"{stdout}\n{stderr}"


@dataclass
class AdbDisplayInputBackend:
    adb: AdbClient
    display: ShadowDisplay
    text_bridge: AccessibilityTextBridge | None = None

    def _dispatch(self, expected_display_id: int, *args: str) -> None:
        live_id = self.display.resolve_live_id()
        if expected_display_id != live_id:
            raise InputBackendError(
                f"action targeted display {expected_display_id}, current shadow display is {live_id}"
            )
        result = self.adb.shell("input", "-d", str(live_id), *args)
        if not result.ok:
            error = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
            raise InputBackendError(error.strip() or "display-targeted input failed")

    def tap(self, display_id: int, x: int, y: int) -> None:
        self._dispatch(display_id, "tap", str(x), str(y))

    def swipe(self, display_id: int, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self._dispatch(display_id, "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    def type_text(self, display_id: int, text: str) -> None:
        live_id = self.display.resolve_live_id()
        if display_id != live_id:
            raise InputBackendError(
                f"action targeted display {display_id}, current shadow display is {live_id}"
            )
        if self.text_bridge is not None and self.text_bridge.available():
            self.text_bridge.set_text(live_id, text)
            return
        if not text.isascii():
            raise InputBackendError(
                "BearBless accessibility bridge is unavailable; refusing non-ASCII text "
                "because adb input text may summon the primary-display IME"
            )
        escaped = text.replace("%", "%25").replace(" ", "%s")
        self._dispatch(live_id, "text", escaped)

    def key(self, display_id: int, keycode: str | int) -> None:
        self._dispatch(display_id, "keyevent", str(keycode))


@dataclass
class ScrcpyControlBackend:
    """Capability marker for input through the display-bound scrcpy window.

    scrcpy owns this control transport; automation can expose it later without
    ever translating actions into untargeted Android input.
    """

    display: ShadowDisplay

    def available(self) -> bool:
        self.display.resolve_live_id()
        return True
