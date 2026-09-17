import pytest
from pathlib import Path

from bearbless.agent.model_client import GUIPlusPhoneModel, ModelClientError


def test_gui_plus_uses_official_mobile_swipe_protocol():
    assert '"name":"mobile_use"' in GUIPlusPhoneModel.SYSTEM_PROMPT
    assert '"coordinate2"' in GUIPlusPhoneModel.SYSTEM_PROMPT
    assert '"swipe"' in GUIPlusPhoneModel.SYSTEM_PROMPT


def test_gui_plus_translates_native_tap_tool_call():
    result = GUIPlusPhoneModel._parse_tool_call(
        '<tool_call>{"name":"phone_use","arguments":'
        '{"action":"tap","coordinate":[123,456],"capability":"NAVIGATE","reason":"打开搜索"}}'
        '</tool_call>'
    )
    assert result == {
        "action": "TAP",
        "x": 123,
        "y": 456,
        "capability": "NAVIGATE",
        "reason": "打开搜索",
    }


def test_gui_plus_translates_successful_terminate():
    result = GUIPlusPhoneModel._parse_tool_call(
        '<tool_call>{"name":"phone_use","arguments":'
        '{"action":"terminate","status":"success","text":"已找到咖啡店"}}'
        '</tool_call>'
    )
    assert result["action"] == "REPORT"
    assert result["observed_result"] == "已找到咖啡店"


def test_gui_plus_rejects_unknown_or_unwrapped_actions():
    with pytest.raises(ModelClientError, match="invalid GUI-Plus"):
        GUIPlusPhoneModel._parse_tool_call('{"message":"tap"}')
    with pytest.raises(ModelClientError, match="unsupported"):
        GUIPlusPhoneModel._parse_tool_call(
            '<tool_call>{"name":"phone_use","arguments":{"action":"shell"}}</tool_call>'
        )


def test_gui_plus_accepts_repeated_opening_tag_quirk():
    result = GUIPlusPhoneModel._parse_tool_call(
        '<tool_call>\n{"name":"phone_use","arguments":{"action":"wait","seconds":1}}\n<tool_call>'
    )
    assert result == {"action": "WAIT", "seconds": 1}


def test_gui_plus_accepts_bare_phone_use_envelope():
    result = GUIPlusPhoneModel._parse_tool_call(
        '{"name":"phone_use","arguments":{"action":"wait","seconds":1}}'
    )
    assert result == {"action": "WAIT", "seconds": 1}


def test_gui_plus_accepts_official_computer_use_name():
    result = GUIPlusPhoneModel._parse_tool_call(
        '{"name":"computer_use","arguments":{"action":"wait","seconds":1}}'
    )
    assert result == {"action": "WAIT", "seconds": 1}


def test_gui_plus_accepts_bare_whitelisted_action():
    result = GUIPlusPhoneModel._parse_tool_call('{"action":"wait","seconds":1}')
    assert result == {"action": "WAIT", "seconds": 1}


def test_gui_plus_preserves_grounding_confidence_and_target():
    result = GUIPlusPhoneModel._parse_tool_call(
        '{"action":"tap","coordinate":[10,20],"capability":"SEARCH",'
        '"target":"搜索框","confidence":0.93}'
    )
    assert result["target"] == "搜索框"
    assert result["confidence"] == 0.93


def test_gui_plus_accepts_click_alias_with_coordinate():
    result = GUIPlusPhoneModel._parse_tool_call(
        '{"action":"click","coordinate":[12,34],"capability":"NAVIGATE"}'
    )
    assert result["action"] == "TAP"
    assert (result["x"], result["y"]) == (12, 34)


def test_gui_plus_accepts_click_element_with_som_id():
    result = GUIPlusPhoneModel._parse_tool_call(
        '{"action":"click_element","element_id":7,"capability":"SEARCH"}'
    )
    assert result["action"] == "CLICK_ELEMENT"
    assert result["element_id"] == 7


def test_gui_plus_click_element_coordinate_falls_back_to_tap():
    result = GUIPlusPhoneModel._parse_tool_call(
        '{"action":"click_element","coordinate":[21,43],"capability":"SEARCH"}'
    )
    assert result["action"] == "TAP"


def test_gui_plus_parses_official_action_prefix_and_trailing_comma():
    result = GUIPlusPhoneModel._parse_tool_call(
        'Action: 点击搜索框\n<tool_call>{"name":"computer_use","arguments":'
        '{"action":"left_click","coordinate":[200,300],}}</tool_call>'
    )
    assert result["action"] == "TAP"
    assert result["capability"] == "NAVIGATE"


def test_gui_plus_maps_official_enter_key_for_search_submission():
    result = GUIPlusPhoneModel._parse_tool_call(
        '<tool_call>{"name":"computer_use","arguments":'
        '{"action":"key","keys":["ENTER"]}}</tool_call>'
    )
    assert result == {"action": "KEY", "keycode": "ENTER", "capability": "SEARCH"}


def test_gui_plus_maps_official_scroll_to_bounded_swipe():
    result = GUIPlusPhoneModel._parse_tool_call(
        '<tool_call>{"name":"computer_use","arguments":'
        '{"action":"scroll","pixels":-700}}</tool_call>'
    )
    assert result["action"] == "SWIPE"
    assert result["duration_ms"] == 400


def test_gui_plus_maps_normalized_mobile_coordinates_to_real_frame():
    result = GUIPlusPhoneModel._parse_tool_call(
        '<tool_call>{"name":"mobile_use","arguments":'
        '{"action":"click","coordinate":[500,500]}}</tool_call>',
        (1080, 2400),
    )
    assert (result["x"], result["y"]) == (540, 1200)


def test_gui_plus_accepts_string_encoded_arguments():
    result = GUIPlusPhoneModel._parse_tool_call(
        r'<tool_call>{"name":"computer_use","arguments":"{\"action\":\"wait\",\"time\":1}"}</tool_call>'
    )
    assert result == {"action": "WAIT", "seconds": 1}


def test_gui_plus_protocol_failure_can_fall_back_to_strict_json(monkeypatch, tmp_path: Path):
    frame = tmp_path / "frame.png"
    # Minimal 2x3 PNG with a valid IHDR width/height for _plan.
    from PIL import Image
    Image.new("RGB", (1080, 2400), "white").save(frame)
    model = GUIPlusPhoneModel("https://example.invalid", "gui-plus", "secret")

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return None
        def read(self):
            return b'{"choices":[{"message":{"content":"Action: click search"}}],"usage":{}}'

    monkeypatch.setattr("bearbless.agent.model_client.urlopen", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        model.fallback,
        "complete",
        lambda prompt, frame_path: {
            "action": "TAP", "x": 500, "y": 200, "capability": "NAVIGATE"
        },
    )
    assert model._plan("context", str(frame)) == {
        "action": "TAP", "x": 500, "y": 200, "capability": "NAVIGATE"
    }


def test_gui_plus_maps_mobile_system_enter():
    result = GUIPlusPhoneModel._parse_tool_call(
        '<tool_call>{"name":"mobile_use","arguments":'
        '{"action":"system_button","button":"Enter"}}</tool_call>'
    )
    assert result == {"action": "KEY", "keycode": "ENTER", "capability": "SEARCH"}


def test_gui_plus_routes_official_mobile_prompt_to_native_planner(monkeypatch):
    model = GUIPlusPhoneModel("https://example.invalid", "gui-plus", "secret")
    monkeypatch.setattr(model, "_plan", lambda prompt, frame: {"action": "WAIT", "seconds": 1})
    result = model.complete(
        "Please generate the next move according to the UI screenshot, instruction and previous actions.",
        "/tmp/frame.png",
    )
    assert result == {"action": "WAIT", "seconds": 1}


def test_gui_plus_parses_real_empty_tag_then_parameters_payload():
    content = (
        'Action: 点击搜索栏。\n<tool_call></tool_call>\n'
        '{"name":"mobile_use","name_for_human":"mobile_use",'
        '"description":"Click search",'
        '"parameters":{"action":"click","coordinate":[396,128]}}\n</tool_call>'
    )
    result = GUIPlusPhoneModel._parse_tool_call(content, (1080, 2400))
    assert result["action"] == "TAP"
    assert (result["x"], result["y"]) == (427, 307)
