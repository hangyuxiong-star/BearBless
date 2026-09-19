from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Literal

from bearbless.runtime.actions import Action
from bearbless.runtime.adb import AdbClient
from bearbless.runtime.parsers import parse_ime_state


@dataclass(frozen=True)
class DeviceState:
    captured_at: str
    captured_monotonic: float
    primary_package: str | None
    primary_activity: str | None
    ime_target_display_id: int | None
    ime_visible: bool | None = None


@dataclass
class MonitorMetrics:
    agent_actions_total: int = 0
    agent_actions_targeting_primary_display: int = 0
    shadow_display_id: int | None = None
    primary_display_agent_package_leaks: int = 0
    ime_policy_violations: int = 0
    clipboard_autosync_enabled: bool = False
    isolation_violations: int = 0
    display_identity_violations: int = 0
    unattributed_primary_display_changes: int = 0
    guard_violations: int = 0
    replans: int = 0
    verification_attempts: int = 0
    model_calls_total: int = 0
    model_image_calls: int = 0
    model_high_res_calls: int = 0
    model_latency_ms: int = 0
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    user_attention_notifications: int = 0
    human_verification_takeovers: int = 0
    task_status: str = "PENDING"


@dataclass(frozen=True)
class Attribution:
    kind: Literal["no_change", "violation", "unattributed_change"]
    reason: str


@dataclass(frozen=True)
class Event:
    ts: str
    actor: str
    display_id: int | None
    type: str
    payload: dict[str, Any]
    result: str


def parse_primary_activity(text: str) -> tuple[str | None, str | None]:
    display_zero = re.search(r"Display #0.*?(?=\n\s*Display #\d+|\Z)", text, re.DOTALL)
    scope = display_zero.group(0) if display_zero else text
    for pattern in (
        r"mResumedActivity:.*?\s([\w.]+)/([\w.$]+)",
        r"topResumedActivity=.*?\s([\w.]+)/([\w.$]+)",
    ):
        match = re.search(pattern, scope)
        if match:
            return match.group(1), match.group(2)
    return None, None


def attribute_primary_change(
    before: DeviceState,
    after: DeviceState,
    action: Action,
    *,
    attribution_window_seconds: float,
) -> Attribution:
    if before.primary_package == after.primary_package:
        return Attribution("no_change", "Display 0 foreground package did not change")
    package_matches = bool(action.package and after.primary_package == action.package)
    within_window = 0 <= after.captured_monotonic - before.captured_monotonic <= attribution_window_seconds
    if package_matches and within_window:
        return Attribution("violation", "Display 0 changed to the package targeted by the agent action")
    reasons = []
    if not package_matches:
        reasons.append("new Display 0 package is unrelated to the agent target")
    if not within_window:
        reasons.append("change fell outside the attribution window")
    return Attribution("unattributed_change", "; ".join(reasons))


class EventStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = run_dir / "events.jsonl"
        self.metrics_path = run_dir / "metrics.json"

    def append(self, event: Event) -> None:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")

    def save_metrics(self, metrics: MonitorMetrics) -> None:
        self.metrics_path.write_text(json.dumps(asdict(metrics), indent=2, sort_keys=True) + "\n", encoding="utf-8")


class DeviceMonitor:
    def __init__(self, adb: AdbClient | None, store: EventStore | None = None) -> None:
        self.adb = adb
        self.store = store
        self.metrics = MonitorMetrics()
        self.events: list[Event] = []
        # The IME can appear after the immediate post-action sample and before
        # the next action starts.  Keep the last sample so that this transition
        # is still attributed to the preceding shadow-display action.
        self._last_assessed_after: DeviceState | None = None

    def snapshot(self, *, monotonic_time: float) -> DeviceState:
        if self.adb is None:
            raise RuntimeError("ADB is required for device snapshots")
        activities = self.adb.shell("dumpsys", "activity", "activities")
        ime = self.adb.shell("dumpsys", "input_method")
        if not activities.ok or not ime.ok:
            raise RuntimeError("could not snapshot activity/IME state")
        assert isinstance(activities.stdout, str) and isinstance(ime.stdout, str)
        package, activity = parse_primary_activity(activities.stdout)
        ime_state = parse_ime_state(ime.stdout)
        return DeviceState(
            datetime.now(timezone.utc).isoformat(), monotonic_time, package, activity,
            ime_state["target_display_id"] if isinstance(ime_state["target_display_id"], int) else None,
            ime_state["visible"] if isinstance(ime_state["visible"], bool) else None,
        )

    def record_agent_action(self, action: Action, result: str, payload: dict[str, Any] | None = None) -> None:
        self.metrics.agent_actions_total += 1
        if action.display_id == 0:
            self.metrics.agent_actions_targeting_primary_display += 1
        self.record(Event(
            datetime.now(timezone.utc).isoformat(), "agent", action.display_id,
            action.action.value.lower(), payload or {}, result,
        ))

    def assess(
        self,
        before: DeviceState,
        after: DeviceState,
        action: Action,
        *,
        shadow_display_id: int,
        attribution_window_seconds: float = 2.0,
    ) -> Attribution:
        self.metrics.shadow_display_id = shadow_display_id
        attribution = attribute_primary_change(before, after, action, attribution_window_seconds=attribution_window_seconds)
        if attribution.kind == "violation":
            self.metrics.isolation_violations += 1
            self.metrics.primary_display_agent_package_leaks += 1
        elif attribution.kind == "unattributed_change":
            self.metrics.unattributed_primary_display_changes += 1
        ime_became_visible_after_action = bool(
            action.display_id == shadow_display_id
            and after.ime_target_display_id == 0
            and after.ime_visible is True
            and before.ime_visible is not True
        )
        ime_appeared_between_actions = bool(
            action.display_id == shadow_display_id
            and self._last_assessed_after is not None
            and self._last_assessed_after.ime_visible is not True
            and before.ime_visible is True
            and before.ime_target_display_id == 0
        )
        ime_leaked_to_primary = ime_became_visible_after_action or ime_appeared_between_actions
        if ime_leaked_to_primary:
            self.metrics.ime_policy_violations += 1
            self.metrics.isolation_violations += 1
        self.record(Event(
            datetime.now(timezone.utc).isoformat(), "monitor", 0, "isolation_assessment",
            {
                "attribution": attribution.kind,
                "reason": attribution.reason,
                "ime_before_visible": before.ime_visible,
                "ime_after_visible": after.ime_visible,
                "ime_after_display_id": after.ime_target_display_id,
                "ime_appeared_between_actions": ime_appeared_between_actions,
                "ime_leaked_to_primary": ime_leaked_to_primary,
            }, attribution.kind,
        ))
        self._last_assessed_after = after
        return attribution

    def record(self, event: Event) -> None:
        self.events.append(event)
        if self.store:
            self.store.append(event)
            self.store.save_metrics(self.metrics)
