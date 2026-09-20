from __future__ import annotations

from dataclasses import asdict
import time

from bearbless.errors import GuardViolation, IsolationViolation
from bearbless.runtime.actions import Action, ActionCapability, ActionType
from bearbless.runtime.guard import ConflictGuard
from bearbless.runtime.input_backend import AdbDisplayInputBackend
from bearbless.runtime.monitor import DeviceMonitor, Event
from bearbless.runtime.observation_backend import AdbScreencapBackend, Frame
from bearbless.runtime.shadow_display import ShadowDisplay


class GuardedExecutor:
    def __init__(
        self,
        display: ShadowDisplay,
        guard: ConflictGuard,
        inputs: AdbDisplayInputBackend,
        observer: AdbScreencapBackend,
        monitor: DeviceMonitor,
        text_probe: object | None = None,
    ) -> None:
        self.display = display
        self.guard = guard
        self.inputs = inputs
        self.observer = observer
        self.monitor = monitor
        self.text_probe = text_probe

    def execute(self, action: Action) -> Frame | None:
        try:
            live_id = self.guard.check(action)
        except (GuardViolation, ValueError) as exc:
            self.monitor.metrics.guard_violations += 1
            self.monitor.record(Event(
                self._now(), "guard", action.display_id, "action_rejected",
                {"action": action.action.value, "reason": str(exc)}, "blocked",
            ))
            raise

        # Resolve-and-list is not enough on Huawei: after a USB reconnect the
        # id can remain present while screencap aliases it to Display 0. Verify
        # visual identity before dispatching anything that can mutate state.
        if live_id is not None and action.action not in {ActionType.OBSERVE, ActionType.WAIT}:
            try:
                self.observer.assert_isolated(live_id)
            except IsolationViolation as exc:
                self.monitor.metrics.display_identity_violations += 1
                self.monitor.metrics.isolation_violations += 1
                self.monitor.record(Event(
                    self._now(), "guard", live_id, "display_identity_rejected",
                    {"reason": str(exc)}, "blocked",
                ))
                self.display.stop()
                raise

        before = self.monitor.snapshot(monotonic_time=time.monotonic())
        result: Frame | None = None
        try:
            result = self._dispatch(action, suppress_primary_ime=before.ime_visible is not True)
            self.monitor.record_agent_action(action, "ok", self._safe_payload(action))
        except Exception:
            self.monitor.record_agent_action(action, "error", self._safe_payload(action))
            raise
        # A focused editor often summons Android's singleton IME a fraction of
        # a second after the input command returns.  Sample after the UI settle
        # window (which was previously spent only before frame capture), so an
        # IME rendered on Display 0 cannot slip between two actions.
        terminal_sensitive = action.capability == ActionCapability.SENSITIVE
        if result is None and not terminal_sensitive and action.action in {
            ActionType.OPEN_APP, ActionType.CLICK_TEXT, ActionType.TAP, ActionType.CONDITIONAL_TAP,
            ActionType.SET_ALARM, ActionType.SWIPE, ActionType.TYPE, ActionType.TYPE_BOTTOM, ActionType.KEY, ActionType.BACK,
        }:
            time.sleep(1.2 if action.capability.value == "SENSITIVE" else 0.4)
        after = self.monitor.snapshot(monotonic_time=time.monotonic())
        if live_id is not None:
            attribution = self.monitor.assess(before, after, action, shadow_display_id=live_id)
            if attribution.kind == "violation" or self.monitor.metrics.ime_policy_violations:
                self.display.stop()
                if self.monitor.metrics.ime_policy_violations:
                    raise IsolationViolation(
                        "agent action caused the system keyboard to appear on Display 0"
                    )
                raise IsolationViolation(attribution.reason)
            # A sensitive click is the terminal action. QQ destroys its share
            # Activity immediately after Send, so waiting and capturing here
            # records only a black teardown surface and keeps the native
            # scrcpy window visibly flashing. The Agent loop already retains
            # the pre-send confirmation frame and verifies the committed exact
            # click deterministically; return immediately so task cleanup can
            # close the virtual display.
            if result is None and not terminal_sensitive:
                result = self.observer.capture(live_id)
        return result

    def _dispatch(self, action: Action, *, suppress_primary_ime: bool = False) -> Frame | None:
        display_id = action.display_id
        if action.action == ActionType.OPEN_APP:
            assert action.package is not None
            self.display.launch_app(action.package, action.uri)
        elif action.action == ActionType.SET_ALARM:
            assert action.hour is not None and action.minute is not None
            self.display.set_alarm(action.hour, action.minute)
        elif action.action == ActionType.CLICK_TEXT:
            assert display_id is not None and action.package is not None and action.text is not None
            self.inputs.click_text_exact(
                display_id, action.package, action.text, allow_multiple=action.allow_multiple
            )
        elif action.action == ActionType.TAP:
            assert display_id is not None and action.x is not None and action.y is not None
            self.inputs.tap(display_id, action.x, action.y)
        elif action.action == ActionType.CONDITIONAL_TAP:
            assert display_id is not None and action.x is not None and action.y is not None
            if self.text_probe is None:
                raise RuntimeError("CONDITIONAL_TAP requires an OCR text probe")
            frame = self.observer.capture(display_id)
            observation = self.text_probe.build(frame, action)  # type: ignore[attr-defined]
            visible = "\n".join(observation.visible_text).casefold()
            if all(text.casefold() in visible for text in action.condition_text):
                self.inputs.tap(display_id, action.x, action.y)
            return self.observer.capture(display_id)
        elif action.action == ActionType.SWIPE:
            assert None not in (display_id, action.x, action.y, action.x2, action.y2, action.duration_ms)
            self.inputs.swipe(display_id, action.x, action.y, action.x2, action.y2, action.duration_ms)  # type: ignore[arg-type]
        elif action.action == ActionType.TYPE:
            assert display_id is not None and action.text is not None
            self.inputs.type_text(display_id, action.text)
        elif action.action == ActionType.TYPE_BOTTOM:
            assert display_id is not None and action.text is not None
            self.inputs.type_bottom_text(
                display_id, action.text, suppress_ime=suppress_primary_ime
            )
        elif action.action in (ActionType.KEY, ActionType.BACK):
            assert display_id is not None
            self.inputs.key(display_id, action.keycode if action.action == ActionType.KEY else "BACK")
        elif action.action == ActionType.WAIT:
            time.sleep(action.seconds or 0)
        elif action.action == ActionType.OBSERVE:
            return self.observer.capture(self.display.resolve_live_id())
        return None

    @staticmethod
    def _safe_payload(action: Action) -> dict[str, object]:
        payload = asdict(action)
        payload["action"] = action.action.value
        if action.expected_outcome is not None:
            payload["expected_outcome"] = action.expected_outcome.model_dump()
        # Never write arbitrary typed text into operational logs.
        if payload.get("text") is not None:
            payload["text"] = f"<{len(action.text or '')} chars>"
        return payload

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
