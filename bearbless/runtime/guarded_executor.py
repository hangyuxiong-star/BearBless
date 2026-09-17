from __future__ import annotations

from dataclasses import asdict
import time

from bearbless.errors import GuardViolation, IsolationViolation
from bearbless.runtime.actions import Action, ActionType
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

        before = self.monitor.snapshot(monotonic_time=time.monotonic())
        result: Frame | None = None
        try:
            result = self._dispatch(action)
            self.monitor.record_agent_action(action, "ok", self._safe_payload(action))
        except Exception:
            self.monitor.record_agent_action(action, "error", self._safe_payload(action))
            raise
        after = self.monitor.snapshot(monotonic_time=time.monotonic())
        if live_id is not None:
            attribution = self.monitor.assess(before, after, action, shadow_display_id=live_id)
            if attribution.kind == "violation" or self.monitor.metrics.ime_policy_violations:
                self.display.stop()
                raise IsolationViolation(attribution.reason)
            if result is None:
                if action.action in {
                    ActionType.TAP, ActionType.CONDITIONAL_TAP, ActionType.SWIPE,
                    ActionType.TYPE, ActionType.KEY, ActionType.BACK, ActionType.WAIT,
                }:
                    if action.action != ActionType.WAIT:
                        time.sleep(0.4)
                result = self.observer.capture(live_id)
        return result

    def _dispatch(self, action: Action) -> Frame | None:
        display_id = action.display_id
        if action.action == ActionType.OPEN_APP:
            assert action.package is not None
            self.display.launch_app(action.package, action.uri)
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
