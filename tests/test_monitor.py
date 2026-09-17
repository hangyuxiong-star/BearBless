import json

from bearbless.runtime.actions import Action, ActionType
from bearbless.runtime.monitor import DeviceState, DeviceMonitor, EventStore, attribute_primary_change


def state(package: str, when: float) -> DeviceState:
    return DeviceState("2026-01-01T00:00:00+00:00", when, package, ".Main", 8)


def test_attribution_detects_agent_package_leak() -> None:
    action = Action(ActionType.OPEN_APP, display_id=8, package="com.target")
    result = attribute_primary_change(state("com.human", 1), state("com.target", 2), action, attribution_window_seconds=2)
    assert result.kind == "violation"


def test_human_app_switch_is_unattributed_not_violation() -> None:
    action = Action(ActionType.OPEN_APP, display_id=8, package="com.target")
    result = attribute_primary_change(state("com.human", 1), state("com.other", 2), action, attribution_window_seconds=2)
    assert result.kind == "unattributed_change"


def test_late_matching_change_is_unattributed() -> None:
    action = Action(ActionType.OPEN_APP, display_id=8, package="com.target")
    result = attribute_primary_change(state("com.human", 1), state("com.target", 10), action, attribution_window_seconds=2)
    assert result.kind == "unattributed_change"


def test_monitor_persists_timeline_and_metrics(tmp_path) -> None:
    monitor = DeviceMonitor(None, EventStore(tmp_path))
    action = Action(ActionType.OPEN_APP, display_id=8, package="com.target")
    monitor.record_agent_action(action, "ok", {"package": "com.target"})
    attribution = monitor.assess(state("com.human", 1), state("com.target", 2), action, shadow_display_id=8)
    assert attribution.kind == "violation"
    assert monitor.metrics.isolation_violations == 1
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 2
    assert json.loads((tmp_path / "metrics.json").read_text())["agent_actions_targeting_primary_display"] == 0
