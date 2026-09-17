from pathlib import Path

import pytest

from bearbless.agent.state import TaskState
from bearbless.agent.grounding import GroundedScreen, MarkedElement
from bearbless.agent.vision import (
    VisionAgentError,
    VisionPlanner,
    _alarm_picker_action,
    _alarm_target,
    _ground_alarm_picker_swipe,
    _normalize_model_decision,
)
from bearbless.errors import AgentTerminalDecision
from bearbless.runtime.actions import ActionType


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, prompt, frame_path=None):
        assert "只决定下一步" in prompt
        return dict(self.payload)


class NativeFakeClient(FakeClient):
    native_tool_protocol = True

    def complete(self, prompt, frame_path=None):
        self.seen_prompt = prompt
        self.seen_frame = frame_path
        return dict(self.payload)


class FakeGrounder:
    def ground(self, frame_path):
        return GroundedScreen(Path(frame_path), (MarkedElement(1, "搜索", (10, 20, 110, 80)),))


def observed_state(tmp_path: Path) -> TaskState:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    return TaskState("agent-1", "打开美团找猪脚饭", last_observation={"frame_path": str(frame)})


def test_vision_planner_returns_one_guardable_action(tmp_path: Path):
    planner = VisionPlanner(FakeClient({"action": "TAP", "x": 100, "y": 200, "reason": "点击搜索", "capability": "SEARCH"}), 31, {"美团": "com.sankuai.meituan"}, FakeGrounder())
    actions = planner.plan(observed_state(tmp_path))
    assert len(actions) == 1
    assert actions[0].action == ActionType.TAP
    assert actions[0].display_id == 31


def test_vision_planner_rejects_model_open_app(tmp_path: Path):
    planner = VisionPlanner(FakeClient({"action": "OPEN_APP", "package": "evil"}), 31, {"美团": "com.sankuai.meituan"}, FakeGrounder())
    with pytest.raises(VisionAgentError):
        planner.plan(observed_state(tmp_path))


def test_vision_planner_records_finish_result(tmp_path: Path):
    state = observed_state(tmp_path)
    planner = VisionPlanner(FakeClient({"action": "FINISH", "reason": "已找到一家猪脚饭"}), 31, {}, FakeGrounder())
    assert planner.plan(state)[0].action == ActionType.FINISH
    assert state.collected_data["agent_result"] == "已找到一家猪脚饭"


def test_vision_planner_clamps_wait_to_safe_runtime_bound(tmp_path: Path):
    planner = VisionPlanner(FakeClient({"action": "WAIT", "seconds": 999}), 31, {}, FakeGrounder())
    action = planner.plan(observed_state(tmp_path))[0]
    assert action.seconds == 5.0


def test_click_element_maps_to_center(tmp_path: Path):
    planner = VisionPlanner(FakeClient({"action": "CLICK_ELEMENT", "element_id": 1, "capability": "NAVIGATE"}), 31, {}, FakeGrounder())
    action = planner.plan(observed_state(tmp_path))[0]
    assert action.action == ActionType.TAP
    assert (action.x, action.y) == (60, 50)


def test_click_element_label_is_normalized_to_unique_ocr_id(tmp_path: Path):
    planner = VisionPlanner(FakeClient({"action": "CLICK_ELEMENT", "element_id": "搜索", "capability": "SEARCH"}), 31, {}, FakeGrounder())
    action = planner.plan(observed_state(tmp_path))[0]
    assert action.action == ActionType.TAP
    assert (action.x, action.y) == (60, 50)
    assert action.capability.value == "SEARCH"


def test_legacy_click_element_without_capability_gets_navigation_only(tmp_path: Path):
    planner = VisionPlanner(FakeClient({"action": "CLICK_ELEMENT", "element_id": 1}), 31, {}, FakeGrounder())
    action = planner.plan(observed_state(tmp_path))[0]
    assert action.capability.value == "NAVIGATE"


def test_raw_tap_without_capability_is_still_rejected(tmp_path: Path):
    planner = VisionPlanner(FakeClient({"action": "TAP", "x": 10, "y": 10}), 31, {}, FakeGrounder())
    with pytest.raises(VisionAgentError, match="capability"):
        planner.plan(observed_state(tmp_path))


def test_swipe_array_dialect_is_normalized():
    decision = _normalize_model_decision({
        "action": "swipe", "start": [500, 1700], "end": [500, 800],
        "capability": "NAVIGATE",
    })
    assert (decision["x"], decision["y"], decision["x2"], decision["y2"]) == (500, 1700, 500, 800)
    assert decision["duration_ms"] == 400


def test_directional_swipe_gets_bounded_points():
    decision = _normalize_model_decision({
        "action": "SWIPE", "direction": "向上滑动时间选择器",
        "capability": "CHANGE_SETTING",
    })
    assert decision["y"] > decision["y2"]
    assert 0 <= decision["x"] < 1080
    assert 0 <= decision["y2"] < 2400


def test_picker_swipe_preserves_column_and_uses_short_drag():
    decision = _normalize_model_decision({
        "action": "SWIPE", "x": 730, "y": 950,
        "direction": "向上滑动分钟滚轮", "capability": "CHANGE_SETTING",
    })
    assert decision["x2"] == 730
    assert decision["y2"] == 710


def test_alarm_picker_swipe_snaps_to_column_and_one_row():
    decision = _ground_alarm_picker_swipe(
        {"action": "SWIPE", "x": 533, "y": 470, "x2": 533, "y2": 1600},
        "新建闹钟上午0700",
    )
    assert decision == {
        "action": "SWIPE", "x": 540, "y": 504,
        "x2": 540, "y2": 624, "duration_ms": 320,
    }


def test_non_alarm_swipe_is_not_rewritten():
    original = {"action": "SWIPE", "x": 533, "y": 470, "x2": 533, "y2": 1600}
    assert _ground_alarm_picker_swipe(original, "网易云音乐") == original


def test_alarm_target_parses_half_hour():
    assert _alarm_target("设置明天早上7点半的闹钟") == ("上午", 7, 30)


def test_alarm_picker_uses_stable_direction_from_current_value():
    elements = (
        MarkedElement(1, "上午", (170, 535, 270, 605)),
        MarkedElement(2, "11", (516, 546, 560, 583)),
        MarkedElement(3, "15", (834, 546, 884, 583)),
    )
    action = _alarm_picker_action("设置早上7点30分闹钟", elements, 91)
    assert action is not None
    assert action.x == action.x2 == 540
    assert action.y2 > action.y
    assert "11 调整到 07" in action.reason


def test_alarm_picker_switches_to_minute_after_hour_matches():
    elements = (
        MarkedElement(1, "上午", (170, 535, 270, 605)),
        MarkedElement(2, "07", (516, 546, 560, 583)),
        MarkedElement(3, "15", (834, 546, 884, 583)),
    )
    action = _alarm_picker_action("设置早上7点30分闹钟", elements, 91)
    assert action is not None
    assert action.x == action.x2 == 858
    assert action.y2 < action.y


def test_blue_score_prefers_selected_picker_text(tmp_path: Path):
    from PIL import Image, ImageDraw
    from bearbless.agent.vision import _blue_score

    path = tmp_path / "picker.png"
    image = Image.new("RGB", (100, 50), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 30, 30), fill=(20, 90, 245))
    draw.rectangle((60, 10, 80, 30), fill=(20, 20, 20))
    image.save(path)
    assert _blue_score(str(path), (10, 10, 31, 31)) > 0
    assert _blue_score(str(path), (60, 10, 81, 31)) == 0


def test_ambiguous_incomplete_swipe_is_not_invented(tmp_path: Path):
    planner = VisionPlanner(FakeClient({
        "action": "SWIPE", "x": 500, "y": 1200,
        "capability": "NAVIGATE",
    }), 31, {}, FakeGrounder())
    with pytest.raises(VisionAgentError, match="SWIPE requires both points"):
        planner.plan(observed_state(tmp_path))


def test_low_confidence_interactive_action_is_rejected(tmp_path: Path):
    planner = VisionPlanner(FakeClient({
        "action": "TAP", "x": 10, "y": 10,
        "capability": "NAVIGATE", "target": "搜索", "confidence": 0.4,
    }), 31, {}, FakeGrounder())
    with pytest.raises(VisionAgentError, match="low-confidence"):
        planner.plan(observed_state(tmp_path))


def test_confidence_metadata_is_not_forwarded_to_runtime_action(tmp_path: Path):
    planner = VisionPlanner(FakeClient({
        "action": "CLICK_ELEMENT", "element_id": 1,
        "capability": "SEARCH", "target": "搜索", "confidence": 0.92,
    }), 31, {}, FakeGrounder())
    action = planner.plan(observed_state(tmp_path))[0]
    assert action.action == ActionType.TAP


def test_native_gui_model_receives_clean_frame_and_non_conflicting_prompt(tmp_path: Path):
    state = observed_state(tmp_path)
    client = NativeFakeClient({
        "action": "TAP", "x": 10, "y": 20, "capability": "NAVIGATE",
    })
    planner = VisionPlanner(client, 31, {}, FakeGrounder())
    planner.plan(state)
    assert client.seen_frame == state.last_observation["frame_path"]
    assert "Please generate the next move" in client.seen_prompt
    assert "只返回一个 JSON 对象" not in client.seen_prompt


def test_blank_frame_waits_without_calling_model(tmp_path: Path):
    state = observed_state(tmp_path)
    state.last_observation["fingerprint"] = "f" * 64
    planner = VisionPlanner(FakeClient({"action": "ABORT"}), 31, {}, FakeGrounder())
    action = planner.plan(state)[0]
    assert action.action == ActionType.WAIT
    assert state.collected_data["blank_frame_count"] == 1


def test_repeated_blank_frames_abort_as_rendering_incompatibility(tmp_path: Path):
    state = observed_state(tmp_path)
    state.last_observation["fingerprint"] = "0" * 64
    state.collected_data["blank_frame_count"] = 3
    planner = VisionPlanner(FakeClient({"action": "ABORT"}), 31, {}, FakeGrounder())
    with pytest.raises(AgentTerminalDecision, match="不兼容副显示渲染"):
        planner.plan(state)


def test_package_selection_must_return_installed_package():
    from bearbless.agent.vision import OllamaVisionClient

    class Client(OllamaVisionClient):
        def complete(self, prompt, frame_path=None):
            assert "已安装包名" in prompt
            return {"package": "com.netease.cloudmusic"}

    client = Client()
    assert client.select_package("播放歌曲", ["com.netease.cloudmusic"]) == "com.netease.cloudmusic"
