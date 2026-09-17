import pytest

from bearbless.config import Config
from bearbless.runtime.input_backend import AccessibilityTextBridge, AdbDisplayInputBackend, InputBackendError
from bearbless.runtime.monitor import parse_primary_activity
from bearbless.runtime.shadow_display import ShadowDisplay


class AliveProcess:
    def poll(self):
        return None


class NoopAdb:
    def shell(self, *args):
        raise AssertionError("stale action must be rejected before ADB dispatch")


class Result:
    def __init__(self, stdout="", stderr="", ok=True):
        self.stdout = stdout
        self.stderr = stderr
        self.ok = ok


class RecordingAdb:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def shell(self, *args):
        self.calls.append(args)
        return next(self.results)


def test_input_backend_rejects_action_with_stale_id() -> None:
    display = ShadowDisplay(Config())
    display.id = 9
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 9]  # type: ignore[method-assign]
    backend = AdbDisplayInputBackend(NoopAdb(), display)  # type: ignore[arg-type]
    with pytest.raises(InputBackendError):
        backend.tap(8, 10, 10)


def test_input_backend_rejects_unicode_before_android_send_text() -> None:
    display = ShadowDisplay(Config())
    display.id = 8
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 8]  # type: ignore[method-assign]
    backend = AdbDisplayInputBackend(NoopAdb(), display)  # type: ignore[arg-type]
    with pytest.raises(InputBackendError, match="bridge is unavailable"):
        backend.type_text(8, "咖啡")


def test_accessibility_bridge_sends_unicode_without_plaintext_in_command() -> None:
    adb = RecordingAdb([Result("Result: Bundle[{ok=true}]")])
    bridge = AccessibilityTextBridge(adb)  # type: ignore[arg-type]
    bridge.set_text(65, "咖啡")
    command = adb.calls[0]
    assert command[-2] == "--arg"
    assert command[-1].startswith("65:")
    assert "咖啡" not in " ".join(command)


def test_input_backend_prefers_accessibility_bridge_for_unicode() -> None:
    display = ShadowDisplay(Config())
    display.id = 65
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 65]  # type: ignore[method-assign]
    adb = RecordingAdb([
        Result("Result: Bundle[{enabled=true}]") ,
        Result("Result: Bundle[{ok=true}]") ,
    ])
    bridge = AccessibilityTextBridge(adb)  # type: ignore[arg-type]
    backend = AdbDisplayInputBackend(adb, display, bridge)  # type: ignore[arg-type]
    backend.type_text(65, "咖啡")
    assert len(adb.calls) == 2
    assert all(call[0] == "content" for call in adb.calls)


def test_parse_primary_activity() -> None:
    package, activity = parse_primary_activity(
        "mResumedActivity: ActivityRecord{abc u0 com.example/.MainActivity t42}"
    )
    assert package == "com.example"
    assert activity == ".MainActivity"
