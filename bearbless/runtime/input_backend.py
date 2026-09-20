from __future__ import annotations

import base64
from dataclasses import dataclass
import re

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
        self._set_text(display_id, text, method="set_text")

    def set_text_bottom(self, display_id: int, text: str, *, suppress_ime: bool = False) -> None:
        """Write only to the lowest editable node on the shadow display."""
        self._set_text(
            display_id, text,
            method="set_text_bottom_silent" if suppress_ime else "set_text_bottom",
        )

    def request_send_confirmation(self, token: str, recipient: str, message: str) -> None:
        fields = (token, self._encode(recipient), self._encode(message))
        result = self.adb.shell(
            "content", "call", "--uri", f"content://{self.authority}",
            "--method", "request_send_confirmation", "--arg", ":".join(fields),
        )
        output = self._output(result)
        if not result.ok or "ok=true" not in output:
            raise InputBackendError(output.strip() or "could not post send confirmation")

    def click_text_exact(self, display_id: int, package: str, text: str, *, allow_multiple: bool = False) -> None:
        x, y = self.find_text_exact_center(
            display_id, package, text, allow_multiple=allow_multiple,
        )
        result = self.adb.shell("input", "-d", str(display_id), "tap", str(x), str(y))
        if not result.ok:
            raise InputBackendError("display-scoped exact-text tap failed")

    def find_text_exact_center(
            self, display_id: int, package: str, text: str, *,
            allow_multiple: bool = False, prefer_bottom: bool = False,
    ) -> tuple[int, int]:
        fields = (str(display_id), self._encode(package), self._encode(text))
        if allow_multiple:
            fields = (*fields, "1")
        elif prefer_bottom:
            fields = (*fields, "0")
        if prefer_bottom:
            fields = (*fields, "bottom")
        result = self.adb.shell(
            "content", "call", "--uri", f"content://{self.authority}",
            "--method", "find_text_exact_center", "--arg", ":".join(fields),
        )
        output = self._output(result)
        if not result.ok or "ok=true" not in output:
            raise InputBackendError(output.strip() or "exact-text accessibility lookup failed")
        match = re.search(r"center=(\d+),(\d+)", output)
        if not match:
            raise InputBackendError("exact-text accessibility lookup returned no center")
        return int(match.group(1)), int(match.group(2))

    def send_confirmation_status(self, token: str) -> str:
        result = self.adb.shell(
            "content", "call", "--uri", f"content://{self.authority}",
            "--method", "send_confirmation_status", "--arg", token,
        )
        output = self._output(result)
        if not result.ok or "ok=true" not in output:
            raise InputBackendError(output.strip() or "could not read send confirmation")
        match = re.search(r"status=([a-z_]+)", output)
        return match.group(1) if match else "unknown"

    def _set_text(self, display_id: int, text: str, *, method: str) -> None:
        encoded = self._encode(text)
        result = self.adb.shell(
            "content", "call", "--uri", f"content://{self.authority}",
            "--method", method, "--arg", f"{display_id}:{encoded}",
        )
        output = self._output(result)
        if not result.ok or "ok=true" not in output:
            reason = output.strip() or "BearBless accessibility bridge rejected text entry"
            raise InputBackendError(reason)

    @staticmethod
    def _encode(text: str) -> str:
        return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")

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

    def click_text_exact(self, display_id: int, package: str, text: str, *, allow_multiple: bool = False) -> None:
        live_id = self.display.resolve_live_id()
        if display_id != live_id:
            raise InputBackendError(
                f"action targeted display {display_id}, current shadow display is {live_id}"
            )
        if self.text_bridge is None or not self.text_bridge.available():
            raise InputBackendError("BearBless accessibility bridge is unavailable")
        self.text_bridge.click_text_exact(live_id, package, text, allow_multiple=allow_multiple)

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

    def type_bottom_text(self, display_id: int, text: str, *, suppress_ime: bool = False) -> None:
        live_id = self.display.resolve_live_id()
        if display_id != live_id:
            raise InputBackendError(
                f"action targeted display {display_id}, current shadow display is {live_id}"
            )
        if self.text_bridge is None or not self.text_bridge.available():
            raise InputBackendError("BearBless accessibility bridge is unavailable")
        self.text_bridge.set_text_bottom(live_id, text, suppress_ime=suppress_ime)

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
