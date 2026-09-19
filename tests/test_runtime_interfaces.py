import pytest

from bearbless.config import Config
from bearbless.runtime.input_backend import AccessibilityTextBridge, AdbDisplayInputBackend, InputBackendError
from bearbless.runtime.monitor import parse_primary_activity
from bearbless.runtime.shadow_display import DisplayResolver, ShadowDisplay, parse_resolved_activity


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


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        return Result("Starting: Intent")


class SequenceRunner:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def run(self, argv, **kwargs):
        self.calls += 1
        return next(self.results)


def test_input_backend_rejects_action_with_stale_id() -> None:
    display = ShadowDisplay(Config())
    display.id = 9
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 9]  # type: ignore[method-assign]
    backend = AdbDisplayInputBackend(NoopAdb(), display)  # type: ignore[arg-type]
    with pytest.raises(InputBackendError):
        backend.tap(8, 10, 10)


def test_shadow_display_keeps_owned_id_during_transient_overlap() -> None:
    display = ShadowDisplay(Config())
    display.id = 9
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 8, 9]  # type: ignore[method-assign]
    assert display.resolve_live_id() == 9


def test_display_listing_retries_transient_scrcpy_port_conflict(monkeypatch) -> None:
    runner = SequenceRunner([
        Result(stderr="bind: Address already in use\nERROR: Server connection failed", ok=False),
        Result(stdout="Display 0: 1200x2640\nDisplay 8: 1080x2400"),
    ])
    monkeypatch.setattr("bearbless.runtime.shadow_display.time.sleep", lambda _seconds: None)

    assert DisplayResolver(Config(), runner).list_ids() == [0, 8]  # type: ignore[arg-type]
    assert runner.calls == 2


def test_uri_launch_uses_package_flag() -> None:
    runner = RecordingRunner()
    display = ShadowDisplay(Config(), runner)  # type: ignore[arg-type]
    display.id = 8
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 8]  # type: ignore[method-assign]
    display.launch_app("com.quark.browser", "https://www.google.com/maps")
    assert runner.calls[-1][-2:] == ["-p", "com.quark.browser"]


def test_share_text_quotes_spaces_for_adb_remote_shell() -> None:
    runner = RecordingRunner()
    display = ShadowDisplay(Config(), runner)  # type: ignore[arg-type]
    display.id = 8
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 8]  # type: ignore[method-assign]

    display.launch_share_text("com.tencent.mobileqq", "去 Mr. Bittu 吃吧")

    command = runner.calls[-1]
    text_arg = command[command.index("android.intent.extra.TEXT") + 1]
    assert text_arg == "'去 Mr. Bittu 吃吧'"
    assert command[-2:] == ["-p", "com.tencent.mobileqq"]


def test_semantic_alarm_intent_is_display_scoped_and_skips_ui() -> None:
    runner = RecordingRunner()
    display = ShadowDisplay(Config(), runner)  # type: ignore[arg-type]
    display.id = 8
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 8]  # type: ignore[method-assign]

    display.set_alarm(18, 0)

    command = runner.calls[-1]
    assert command[command.index("--display") + 1] == "8"
    assert "android.intent.action.SET_ALARM" in command
    assert command[command.index("--ez") + 1:command.index("--ez") + 3] == [
        "android.intent.extra.alarm.SKIP_UI", "true",
    ]
    assert command[-2:] == ["-p", "com.huawei.deskclock"]


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


def test_accessibility_bridge_click_text_is_encoded_and_display_scoped() -> None:
    adb = RecordingAdb([Result("Result: Bundle[{ok=true}]")])
    bridge = AccessibilityTextBridge(adb)  # type: ignore[arg-type]

    bridge.click_text_exact(65, "com.tencent.mobileqq", "发送")

    command = adb.calls[0]
    assert command[command.index("--method") + 1] == "click_text_exact"
    payload = command[command.index("--arg") + 1]
    assert payload.startswith("65:")
    assert "com.tencent.mobileqq" not in payload
    assert "发送" not in payload


def test_input_backend_rejects_stale_semantic_click_before_bridge_call() -> None:
    display = ShadowDisplay(Config())
    display.id = 65
    display._process = AliveProcess()
    display.resolver.list_ids = lambda: [0, 65]  # type: ignore[method-assign]
    bridge = AccessibilityTextBridge(NoopAdb())  # type: ignore[arg-type]
    backend = AdbDisplayInputBackend(NoopAdb(), display, bridge)  # type: ignore[arg-type]

    with pytest.raises(InputBackendError, match="targeted display 64"):
        backend.click_text_exact(64, "com.tencent.mobileqq", "发送")


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


def test_resolved_activity_is_scoped_to_main_user_target_package() -> None:
    output = "priority=0\ncom.tencent.mobileqq/.activity.SplashActivity\n"
    assert parse_resolved_activity(output, "com.tencent.mobileqq") == (
        "com.tencent.mobileqq/.activity.SplashActivity"
    )
    assert parse_resolved_activity(output, "com.tencent.mm") is None
