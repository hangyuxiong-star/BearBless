from pathlib import Path

import pytest

from bearbless.agent.state import TaskState
from bearbless.agent.grounding import GroundedScreen, MarkedElement
from bearbless.agent.vision import (
    VisionAgentError,
    QQMessageVerifier,
    VisionPlanner,
    _alarm_picker_action,
    _alarm_save_confirmed,
    _alarm_target,
    _ground_alarm_picker_swipe,
    _normalize_model_decision,
    _music_search_action,
    _wolt_burger_action,
    _wolt_ranked_candidate,
)
from bearbless.errors import AgentTerminalDecision
from bearbless.runtime.actions import ActionCapability, ActionType
from bearbless.schemas import SuccessCriterion, TaskMode, TaskSpec


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


class QQSendGrounder:
    def ground(self, frame_path):
        return GroundedScreen(Path(frame_path), (
            MarkedElement(1, "红枣桂花熊", (150, 150, 400, 210)),
            MarkedElement(2, "发送", (880, 2180, 1040, 2320)),
        ))


class QQRecipientGrounder:
    def ground(self, frame_path):
        return GroundedScreen(Path(frame_path), (
            MarkedElement(2, "最近聊天", (30, 400, 260, 480)),
            MarkedElement(1, "红枣桂花熊", (150, 600, 430, 720)),
        ))


class QQDraftGrounder:
    def ground(self, frame_path):
        return GroundedScreen(Path(frame_path), (
            MarkedElement(3, "最近聊天", (30, 400, 260, 480)),
            MarkedElement(1, "乱码联系人", (150, 150, 400, 210)),
            MarkedElement(2, "去 MAX Premium Burgers Herlev 吃吧，评...", (200, 620, 900, 720)),
        ))


class QQShareResolverGrounder:
    def ground(self, frame_path):
        return GroundedScreen(Path(frame_path), (
            MarkedElement(1, "使用以下方式打开", (120, 1850, 620, 1920)),
            MarkedElement(2, "发送给好友", (170, 2020, 330, 2200)),
            MarkedElement(3, "仅此一次", (170, 2300, 430, 2380)),
            MarkedElement(4, "红枣桂花熊", (150, 150, 430, 220)),
        ))


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


def test_confirmed_qq_message_uses_deterministic_sensitive_send(tmp_path: Path):
    message = "去 MAX Premium Burgers Herlev 吃吧，评分 8.3，地址是 Herlev Hovedgade 55。"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={
            "user_confirmed_sensitive_action": True,
            "sensitive_scope": state.goal,
        },
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    state.action_history.append({"action": "TYPE", "text": message})
    planner = VisionPlanner(
        FakeClient({"action": "ABORT", "reason": "model must not be called"}),
        31,
        {"QQ": "com.tencent.mobileqq"},
        QQSendGrounder(),
        package_identity_checker=lambda package: package == "com.tencent.mobileqq",
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.CLICK_TEXT
    assert action.capability == ActionCapability.SENSITIVE
    assert action.package == "com.tencent.mobileqq"
    assert action.text == "发送"


def test_qq_draft_types_once_then_finishes_without_send(tmp_path: Path):
    message = "明天中午去 Itacho Charlottenlund 吃饭吧。"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊编辑消息草稿：{message}，不要发送"
    state.task_spec = TaskSpec(
        goal=state.goal,
        constraints={"cloud_vision_consent": True},
        success_criteria=[SuccessCriterion(name="draft", description="draft")],
    )
    state.collected_data["target_package"] = "com.tencent.mobileqq"
    state.collected_data["qq_recipient_selected"] = "红枣桂花熊"

    class ChatGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "QQ", (20, 20, 80, 60)),
                MarkedElement(2, "红枣桂花熊", (200, 40, 500, 100)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        ChatGrounder(), package_identity_checker=lambda _package: True,
    )

    type_action = planner.plan(state)[0]
    assert type_action.action == ActionType.TYPE_BOTTOM
    assert type_action.text == message
    assert type_action.capability == ActionCapability.ENTER_TEXT

    state.action_history.append({"action": "TYPE_BOTTOM", "text": message})
    finish_action = planner.plan(state)[0]
    assert finish_action.action == ActionType.FINISH
    assert state.collected_data["qq_draft"]["sent"] is False


def test_wolt_candidate_binds_compact_ocr_rating_to_row_and_chooses_highest():
    labels = [
        (MarkedElement(1, "Jernbane Pizza Durum Kebab", (180, 1000, 650, 1060)), "Jernbane Pizza Durum Kebab"),
        (MarkedElement(2, "35-45 min © 84", (600, 1070, 900, 1120)), "35-45 min © 84"),
        (MarkedElement(3, "Mr. Bittu", (180, 1800, 480, 1860)), "Mr. Bittu"),
        (MarkedElement(4, "© 88", (740, 1870, 900, 1920)), "© 88"),
        (MarkedElement(5, "55-65 min", (520, 1870, 700, 1920)), "55-65 min"),
    ]
    ranked = _wolt_ranked_candidate(labels)
    assert ranked is not None
    assert ranked[2:] == ("Mr. Bittu", "8.8", "55-65 min")


def test_confirmed_qq_message_opens_exact_recipient_without_model(tmp_path: Path):
    message = "去吃汉堡吧。"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        QQRecipientGrounder(), package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.TAP
    assert action.capability == ActionCapability.READ
    assert (action.x, action.y) == (290, 660)
    assert "红枣桂花熊" in action.reason


def test_confirmed_qq_share_list_accepts_duplicate_exact_recipient_labels(tmp_path: Path):
    message = "明天见"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )

    class DuplicateRecipientShareGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "发送给", (30, 40, 200, 100)),
                MarkedElement(2, "搜索", (30, 120, 900, 220)),
                MarkedElement(3, "最近转发", (30, 260, 250, 320)),
                MarkedElement(4, "萱草", (100, 400, 260, 520)),
                MarkedElement(5, "红枣桂花熊", (300, 400, 520, 520)),
                MarkedElement(6, "最近聊天", (30, 650, 250, 710)),
                MarkedElement(7, "红枣桂花熊", (180, 900, 520, 1020)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "TYPE", "text": message}),
        31,
        {"QQ": "com.tencent.mobileqq"},
        DuplicateRecipientShareGrounder(),
        package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.TAP
    assert action.capability == ActionCapability.READ
    assert (action.x, action.y) == (350, 960)
    assert "红枣桂花熊" in action.reason


def test_confirmed_qq_share_list_uses_exact_accessibility_when_ocr_is_partial(tmp_path: Path):
    message = "明天见"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )

    class RealDamagedOcrGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "最近转发", (95, 522, 222, 564)),
                MarkedElement(2, "红 性 花", (251, 802, 376, 862)),
                MarkedElement(3, "最近聊天", (137, 986, 222, 1026)),
                MarkedElement(4, "萱草", (182, 1286, 271, 1334)),
                MarkedElement(5, "红 桂", (181, 1426, 312, 1466)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "TYPE", "text": message}),
        31,
        {"QQ": "com.tencent.mobileqq"},
        RealDamagedOcrGrounder(),
        package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.CLICK_TEXT
    assert action.capability == ActionCapability.READ
    assert action.text == "红枣桂花熊"
    assert action.allow_multiple is True


def test_confirmed_qq_share_list_uses_accessibility_exact_text_instead_of_model_typing(tmp_path: Path):
    message = "明天见"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )

    class MissingRecipientShareGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "发送给", (30, 40, 200, 100)),
                MarkedElement(2, "搜索", (30, 120, 900, 220)),
                MarkedElement(3, "最近转发", (30, 260, 250, 320)),
                MarkedElement(4, "萱草", (100, 400, 260, 520)),
                MarkedElement(5, "最近聊天", (30, 650, 250, 710)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "TYPE", "text": message}),
        31,
        {"QQ": "com.tencent.mobileqq"},
        MissingRecipientShareGrounder(),
        package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.CLICK_TEXT
    assert action.capability == ActionCapability.READ
    assert action.package == "com.tencent.mobileqq"
    assert action.text == "红枣桂花熊"
    assert action.allow_multiple is True


def test_confirmed_qq_message_waits_while_share_request_is_processing(tmp_path: Path):
    message = "晚上去吃汉堡吧"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )

    class ProcessingGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "红枣桂花熊", (130, 120, 420, 220)),
                MarkedElement(2, "正在处理", (420, 1050, 680, 1150)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        ProcessingGrounder(), package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.WAIT
    assert "等待 QQ 分享联系人页面" in action.reason


def test_confirmed_qq_message_waits_on_plain_inbox_until_share_surface_appears(tmp_path: Path):
    message = "明天见"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )

    class InboxGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "红枣桂花熊", (140, 130, 430, 220)),
                MarkedElement(2, "消息", (30, 2200, 180, 2300)),
                MarkedElement(3, "联系人", (420, 2200, 650, 2300)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "TAP", "x": 280, "y": 170}),
        31,
        {"QQ": "com.tencent.mobileqq"},
        InboxGrounder(),
        package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.WAIT
    assert "禁止点击普通收件箱联系人" in action.reason


def test_confirmed_qq_message_closes_forward_preview_modal(tmp_path: Path):
    message = "晚上去 MAX Premium Burgers Herlev 吃吧"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )

    class PreviewGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "转发消息预览", (330, 700, 760, 800)),
                MarkedElement(2, message, (220, 1000, 850, 1300)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        PreviewGrounder(), package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.BACK
    assert action.reason == "关闭 QQ 转发消息预览并返回联系人选择页"


def test_confirmed_qq_message_matches_recipient_split_across_ocr_lines(tmp_path: Path):
    message = "你吃饭了吗"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )

    class SplitRecipientGrounder:
        def ground(self, _path):
            return GroundedScreen(
                Path(_path),
                (
                    MarkedElement(1, "红枣桂", (30, 700, 180, 820)),
                    MarkedElement(2, "花熊", (181, 700, 250, 820)),
                    MarkedElement(3, "最近聊天", (30, 400, 260, 480)),
                ),
            )

    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        SplitRecipientGrounder(), package_identity_checker=lambda _package: False,
    )

    action = planner.plan(state)[0]
    assert action.action == ActionType.TAP
    assert action.capability == ActionCapability.READ


def test_confirmed_qq_share_dialog_sends_preloaded_message_without_typing_comment(tmp_path: Path):
    message = "去吃汉堡吧。"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        QQSendGrounder(), package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.CLICK_TEXT
    assert action.capability == ActionCapability.SENSITIVE
    assert action.package == "com.tencent.mobileqq"
    assert action.text == "发送"


def test_confirmed_qq_never_sends_when_visible_recipient_is_different(tmp_path: Path):
    message = "晚上去吃汉堡吧"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    state.collected_data["qq_recipient_selected"] = "红枣桂花熊"

    class WrongRecipientDialogGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "发送给：", (200, 800, 380, 900)),
                MarkedElement(2, "萱草", (380, 940, 520, 1020)),
                MarkedElement(3, "发送", (680, 1450, 800, 1570)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "ABORT", "reason": "recipient mismatch"}),
        31,
        {"QQ": "com.tencent.mobileqq"},
        WrongRecipientDialogGrounder(),
        package_identity_checker=lambda _package: True,
    )

    with pytest.raises(AgentTerminalDecision):
        planner.plan(state)


def test_confirmed_qq_share_dialog_accepts_spaced_send_and_split_recipient_ocr(tmp_path: Path):
    message = "明天中午去 Itacho Charlottenlund 吃饭吧。"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    state.collected_data["qq_recipient_tap_attempted"] = "红枣桂花熊"

    class RealDialogOcrGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "发 送 给 :", (232, 885, 377, 926)),
                MarkedElement(2, "红枣桂", (363, 1000, 494, 1040)),
                MarkedElement(5, "花熊", (495, 1000, 590, 1040)),
                MarkedElement(3, "最近转发", (40, 500, 300, 560)),
                MarkedElement(4, "发 送", (691, 1495, 778, 1561)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        RealDialogOcrGrounder(), package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.CLICK_TEXT
    assert action.capability == ActionCapability.SENSITIVE
    assert action.package == "com.tencent.mobileqq"
    assert action.text == "发送"


def test_confirmed_qq_share_dialog_trusts_prior_accessibility_exact_recipient_selection(tmp_path: Path):
    message = "明天见"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    state.action_history.append({
        "action": "CLICK_TEXT",
        "text": "红枣桂花熊",
        "reason": "通过 Accessibility 精确选择联系人 红枣桂花熊",
    })

    class PartialDialogGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "发 送 给 :", (232, 906, 377, 947)),
                MarkedElement(2, "红 桂", (363, 1021, 494, 1063)),
                MarkedElement(3, "明天 见", (233, 1186, 339, 1236)),
                MarkedElement(4, "发 送", (691, 1473, 778, 1539)),
            ))

    planner = VisionPlanner(
        FakeClient({"action": "TAP", "x": 734, "y": 1506}),
        31,
        {"QQ": "com.tencent.mobileqq"},
        PartialDialogGrounder(),
        package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.CLICK_TEXT
    assert action.capability == ActionCapability.SENSITIVE
    assert action.text == "发送"


def test_qq_message_verifier_marks_committed_visible_message_complete(tmp_path: Path):
    message = "明天见"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    state.collected_data["sensitive_effect"] = {"phase": "COMMITTED"}

    class SentChatGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "红枣桂花熊", (140, 130, 430, 220)),
                MarkedElement(2, "明天见", (700, 1800, 900, 1900)),
            ))

    result = QQMessageVerifier(SentChatGrounder()).verify(state)

    assert result.passed is True
    assert "已向红枣桂花熊发送" in state.collected_data["agent_result"]


def test_qq_message_verifier_accepts_committed_exact_send_when_blue_bubble_ocr_fails(tmp_path: Path):
    message = "明天吃什么"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    state.collected_data["sensitive_effect"] = {"phase": "COMMITTED"}
    state.action_history.extend((
        {
            "action": "CLICK_TEXT",
            "text": "红枣桂花熊",
            "reason": "通过 Accessibility 精确选择联系人 红枣桂花熊",
            "capability": "READ",
        },
        {
            "action": "CLICK_TEXT",
            "text": "发送",
            "reason": "点击发送已确认且由分享意图预载的 QQ 消息",
            "capability": "SENSITIVE",
        },
    ))

    class BlueBubbleMissedGrounder:
        def ground(self, frame_path):
            return GroundedScreen(Path(frame_path), (
                MarkedElement(1, "水逆退散", (200, 200, 400, 260)),
                MarkedElement(2, "晚上 11:09", (470, 2000, 620, 2060)),
            ))

    result = QQMessageVerifier(BlueBubbleMissedGrounder()).verify(state)

    assert result.passed is True


def test_confirmed_qq_message_never_uses_payload_preview_as_recipient(tmp_path: Path):
    message = "去 MAX Premium Burgers Herlev 吃吧，评分 8.3。"
    state = observed_state(tmp_path)
    state.goal = f"打开QQ，给红枣桂花熊发消息：{message}"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        QQDraftGrounder(), package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.CLICK_TEXT
    assert action.text == "红枣桂花熊"

    assert "qq_recipient_tap_attempted" not in state.collected_data


def test_lowercase_qq_uses_android_share_resolver_without_model(tmp_path: Path):
    state = observed_state(tmp_path)
    state.goal = "打开qq给红枣桂花熊发消息问他吃饭了吗"
    state.task_spec = TaskSpec(
        goal=state.goal,
        task_mode=TaskMode.SENSITIVE_TASK,
        constraints={"user_confirmed_sensitive_action": True, "sensitive_scope": state.goal},
        success_criteria=[SuccessCriterion(name="sent", description="sent")],
    )
    state.collected_data["target_package"] = "com.tencent.mobileqq"
    planner = VisionPlanner(
        FakeClient({"action": "ABORT"}), 31, {"QQ": "com.tencent.mobileqq"},
        QQShareResolverGrounder(), package_identity_checker=lambda _package: True,
    )

    action = planner.plan(state)[0]

    assert action.action == ActionType.TAP
    assert action.capability == ActionCapability.NAVIGATE
    assert action.reason.startswith("仅本次使用 QQ")
    assert (action.x, action.y) == (300, 2340)


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


def test_music_search_page_fails_closed_instead_of_leaking_ime():
    elements = (
        MarkedElement(1, "搜索", (900, 180, 1040, 240)),
        MarkedElement(2, "歌手", (100, 280, 220, 350)),
        MarkedElement(3, "曲风", (350, 280, 480, 350)),
        MarkedElement(4, "专区", (630, 280, 750, 350)),
        MarkedElement(5, "搜索历史", (40, 400, 190, 460)),
    )
    with pytest.raises(AgentTerminalDecision, match="Display 0"):
        _music_search_action("打开网易云音乐，播放歌曲银河赴约", elements, 74)


def test_music_results_tap_exact_full_result():
    elements = (
        MarkedElement(1, "银河赴约", (40, 500, 250, 550)),
        MarkedElement(2, "银河赴约 网易云音乐校园 CMJ", (190, 310, 700, 420)),
    )
    action = _music_search_action("打开网易云音乐，播放歌曲银河赴约", elements, 74)
    assert action is not None
    assert action.action == ActionType.TAP
    assert action.y == 365


def test_wolt_skill_uses_existing_restaurants_and_burger_categories():
    state = TaskState("wolt", "去wolt，帮我找一家汉堡店")
    home = (
        MarkedElement(1, "Restaurants", (59, 351, 229, 374)),
        MarkedElement(2, "Search", (429, 2274, 638, 2315)),
    )
    action = _wolt_burger_action(state, home, 144)
    assert action is not None
    assert action.action == ActionType.TAP
    assert action.reason == "进入 Wolt Restaurants"

    restaurant_page = (
        MarkedElement(1, "Restaurants", (40, 150, 500, 230)),
        MarkedElement(2, "Food type", (126, 255, 360, 325)),
    )
    action = _wolt_burger_action(state, restaurant_page, 144)
    assert action is not None
    assert action.reason == "打开 Restaurants 的 Food type 分类"

    food_types = (
        MarkedElement(1, "Food type", (40, 340, 440, 470)),
        MarkedElement(2, "Burger", (560, 1120, 760, 1230)),
        MarkedElement(3, "Search", (429, 2274, 638, 2315)),
    )
    action = _wolt_burger_action(state, food_types, 144)
    assert action is not None
    assert action.reason == "从 Food type 选择现有 Burger 分类"
    assert state.collected_data["wolt_burger_filter_selected"] is True


def test_wolt_skill_waits_for_location_page_to_finish_loading():
    state = TaskState("wolt-loading", "去wolt，帮我找一家汉堡店")
    action = _wolt_burger_action(
        state,
        (MarkedElement(1, "Choose your location", (300, 40, 700, 90)),),
        144,
    )
    assert action is not None
    assert action.action == ActionType.WAIT
    assert "位置和首页数据加载" in action.reason


def test_wolt_skill_waits_for_sparse_restaurants_loading_screen():
    state = TaskState("wolt-restaurants-loading", "去wolt，帮我找一家汉堡店")
    elements = (
        MarkedElement(1, "Home, Poppelhoegnet 21", (140, 35, 650, 95)),
        MarkedElement(2, "Home", (150, 2220, 300, 2320)),
        MarkedElement(3, "Search", (380, 2220, 680, 2320)),
    )

    action = _wolt_burger_action(state, elements, 144)

    assert action is not None
    assert action.action == ActionType.WAIT
    assert "Restaurants 内容渲染" in action.reason


def test_wolt_skill_waits_when_home_nav_is_icon_only_during_loading():
    state = TaskState(
        "wolt-restaurants-icon-loading",
        "去wolt，帮我找一家汉堡店",
        collected_data={"wolt_restaurants_attempts": 1},
    )
    elements = (
        MarkedElement(1, "Anker Engelunds Vej 1", (140, 35, 650, 95)),
        MarkedElement(2, "Search", (380, 2220, 680, 2320)),
    )

    action = _wolt_burger_action(state, elements, 144)

    assert action is not None
    assert action.action == ActionType.WAIT
    assert "Restaurants 内容渲染" in action.reason


def test_wolt_landing_opens_food_type_before_tapping_carousel_category():
    state = TaskState("wolt-landing", "去wolt，帮我找一家汉堡店")
    elements = (
        MarkedElement(1, "Restaurants", (40, 180, 500, 250)),
        MarkedElement(2, "Food type", (120, 320, 380, 370)),
        MarkedElement(3, "Burger", (90, 620, 200, 670)),
        MarkedElement(4, "All Restaurants", (40, 750, 470, 810)),
    )

    action = _wolt_burger_action(state, elements, 144)

    assert action is not None
    assert action.action == ActionType.TAP
    assert action.reason == "打开 Restaurants 的 Food type 分类"
    assert (action.x, action.y) == elements[1].center


def test_wolt_sparse_blue_shell_waits_when_ocr_has_no_elements():
    state = TaskState("wolt-sparse-shell", "打开Wolt找一家汉堡店")

    action = _wolt_burger_action(state, (), 305)

    assert action is not None
    assert action.action == ActionType.WAIT
    assert "位置和首页数据加载" in action.reason


def test_wolt_skill_finishes_from_local_ocr_evidence_without_model():
    state = TaskState(
        "wolt-result", "去wolt，帮我找一家汉堡店",
        collected_data={"wolt_burger_filter_selected": True},
    )
    results = (
        MarkedElement(1, "Restaurants", (40, 150, 500, 230)),
        MarkedElement(2, "Hubb Kitchens", (180, 900, 520, 950)),
        MarkedElement(3, "25-35 min", (300, 980, 450, 1020)),
    )
    action = _wolt_burger_action(state, results, 144)
    assert action is not None
    assert action.action == ActionType.FINISH
    assert state.evidence[0]["criterion"] == "Wolt Burger results"


def test_wolt_high_rating_requires_9_on_ten_point_scale_and_records_rating():
    state = TaskState(
        "wolt-high-rating", "打开Wolt帮我找一家评分高的汉堡店",
        collected_data={"wolt_burger_filter_selected": True},
    )
    results = (
        MarkedElement(1, "Burger", (40, 150, 500, 230)),
        MarkedElement(2, "Hubb Kitchens Bagsvaerd 4", (180, 900, 620, 950)),
        MarkedElement(3, "35-45 min", (480, 980, 650, 1020)),
        MarkedElement(4, "9.8", (760, 980, 900, 1020)),
    )

    action = _wolt_burger_action(state, results, 144)

    assert action is not None
    assert action.action == ActionType.FINISH
    assert state.collected_data["wolt_restaurant"]["name"] == "Hubb Kitchens Bagsvaerd"
    assert state.collected_data["wolt_restaurant"]["rating"] == "9.8"
    assert "rating=9.8" in state.evidence[0]["evidence"]
    assert "scale=10" in state.evidence[0]["evidence"]


def test_wolt_high_rating_does_not_finish_below_threshold():
    state = TaskState(
        "wolt-not-high", "打开Wolt帮我找一家评分高的汉堡店",
        collected_data={"wolt_burger_filter_selected": True},
    )
    results = (
        MarkedElement(1, "Burger", (40, 150, 500, 230)),
        MarkedElement(2, "Example Burger", (180, 900, 520, 950)),
        MarkedElement(3, "25-35 min", (480, 980, 650, 1020)),
        MarkedElement(4, "8.9", (760, 980, 900, 1020)),
    )

    action = _wolt_burger_action(state, results, 144)

    assert action is not None
    assert action.action == ActionType.SWIPE
    assert action.y > action.y2
    assert not state.evidence


def test_wolt_fresh_task_finishes_from_already_selected_burger_results():
    state = TaskState("wolt-resume", "打开Wolt帮我找一家评分高的汉堡店")
    results = (
        MarkedElement(1, "Restaurants", (40, 150, 500, 230)),
        MarkedElement(2, "Burger", (130, 255, 330, 325)),
        MarkedElement(3, "Hubb Kitchens Bagsvaerd", (180, 900, 620, 950)),
        MarkedElement(4, "35-45 min", (480, 980, 650, 1020)),
        MarkedElement(5, "9.8", (760, 980, 900, 1020)),
    )

    action = _wolt_burger_action(state, results, 144)

    assert action is not None
    assert action.action == ActionType.FINISH
    assert state.collected_data["wolt_burger_filter_selected"] is True
    assert "rating=9.8" in state.evidence[0]["evidence"]


def test_wolt_location_sheet_returns_via_current_checked_home():
    state = TaskState("wolt-location", "打开Wolt帮我找一家评分高的汉堡店")
    elements = (
        MarkedElement(1, "Choose your location", (40, 760, 800, 900)),
        MarkedElement(2, "Home ✓", (170, 990, 420, 1080)),
        MarkedElement(3, "Add new address", (50, 2100, 950, 2250)),
    )

    action = _wolt_burger_action(state, elements, 144)

    assert action is not None
    assert action.action == ActionType.TAP
    assert action.reason == "选择 Wolt 当前已勾选的 Home 地址并返回餐厅结果"
    assert (action.x, action.y) == elements[1].center


def test_wolt_chinese_skill_uses_category_without_search():
    state = TaskState("wolt-chinese", "在 Wolt 上找一家今晚营业、评分高的中餐店")
    food_types = (
        MarkedElement(1, "Food type", (40, 340, 440, 470)),
        MarkedElement(2, "Chinese", (560, 1120, 760, 1230)),
        MarkedElement(3, "Search", (429, 2274, 638, 2315)),
    )

    action = _wolt_burger_action(state, food_types, 144)

    assert action is not None
    assert action.action == ActionType.TAP
    assert action.reason == "从 Food type 选择现有 Chinese 分类"
    assert state.collected_data["app_skill_route"] == "wolt_chinese_categories"
    assert state.collected_data["wolt_chinese_filter_selected"] is True


def test_wolt_japanse_typo_uses_japanese_category_without_search():
    state = TaskState("wolt-japanese", "在 Wolt 上找一家 japanse 店")
    food_types = (
        MarkedElement(1, "Food type", (120, 320, 380, 370)),
        MarkedElement(2, "Japanese", (560, 1120, 760, 1230)),
        MarkedElement(3, "Search", (429, 2274, 638, 2315)),
    )

    action = _wolt_burger_action(state, food_types, 144)

    assert action is not None
    assert action.reason == "从 Food type 选择现有 Japanese 分类"
    assert state.collected_data["app_skill_route"] == "wolt_japanese_categories"
    assert state.collected_data["wolt_japanese_filter_selected"] is True


def test_wolt_continues_saved_cart_context_without_checkout():
    state = TaskState("wolt-cart", "打开Wolt找一家Japanese店并读取地址")
    elements = (
        MarkedElement(1, "Continue order?", (200, 760, 800, 850)),
        MarkedElement(2, "Continue order", (230, 1200, 850, 1320)),
        MarkedElement(3, "Cancel", (230, 1380, 850, 1500)),
    )

    action = _wolt_burger_action(state, elements, 144)

    assert action is not None
    assert action.action == ActionType.TAP
    assert action.capability == ActionCapability.NAVIGATE
    assert action.reason == "继续 Wolt 已保存的店铺上下文以读取商家信息"
    assert (action.x, action.y) == elements[1].center


def test_wolt_evening_merchant_page_opens_more_to_read_address():
    state = TaskState(
        "wolt-evening-detail",
        "打开Wolt找一家汉堡店，读取并验证店名和完整地址，不下单",
        collected_data={
            "wolt_burger_filter_selected": True,
            "wolt_selected_restaurant": {
                "name": "Cocks & Cows Lyngby",
                "delivery": "20-30 min",
                "category": "Burger",
                "rating": "8.0",
            },
        },
    )
    elements = (
        MarkedElement(1, "Cocks & Cows Lyngby", (60, 690, 830, 780)),
        MarkedElement(2, "8.0 · Closes at 22:00 · Min. order 75,00 kr", (120, 800, 900, 850)),
        MarkedElement(3, "Delivery 20-30 min", (40, 940, 660, 1030)),
        MarkedElement(4, "More", (610, 850, 760, 910)),
    )

    action = _wolt_burger_action(state, elements, 302)

    assert action is not None
    assert action.action == ActionType.CLICK_TEXT
    assert action.capability == ActionCapability.READ
    assert action.reason == "打开 Wolt 商家信息以读取地址"
    assert action.text == "More"


def test_wolt_closes_accidentally_opened_order_details():
    state = TaskState(
        "wolt-order-details",
        "打开Wolt找一家汉堡店，读取并验证店名和完整地址，不下单",
        collected_data={"wolt_more_opened": True},
    )
    elements = (
        MarkedElement(1, "Order details", (330, 700, 750, 800)),
        MarkedElement(2, "Where?", (40, 980, 300, 1050)),
        MarkedElement(3, "Done", (40, 2200, 1040, 2360)),
    )

    action = _wolt_burger_action(state, elements, 303)

    assert action is not None
    assert action.action == ActionType.TAP
    assert action.reason == "关闭误开的 Wolt 配送详情并返回商家页"
    assert (action.x, action.y) == elements[2].center


def test_wolt_chinese_skill_records_structured_restaurant_result():
    state = TaskState(
        "wolt-chinese-result",
        "在 Wolt 上找一家今晚营业、评分高的中餐店",
        collected_data={"wolt_chinese_filter_selected": True},
    )
    results = (
        MarkedElement(1, "Chinese", (40, 150, 500, 230)),
        MarkedElement(2, "China Palace", (180, 900, 520, 950)),
        MarkedElement(3, "25-35 min", (300, 980, 450, 1020)),
        MarkedElement(4, "© 89", (760, 980, 900, 1020)),
    )

    action = _wolt_burger_action(state, results, 144)

    assert action is not None
    assert action.action == ActionType.FINISH
    assert state.collected_data["wolt_restaurant"] == {
        "name": "China Palace",
        "delivery": "25-35 min",
        "category": "Chinese",
        "rating": "8.9",
    }


def test_wolt_skill_never_falls_back_to_search():
    state = TaskState("wolt-unknown", "去wolt，帮我找一家汉堡店")
    with pytest.raises(AgentTerminalDecision, match="不会点击 Search"):
        _wolt_burger_action(
            state,
            (MarkedElement(1, "Search", (429, 2274, 638, 2315)),),
            144,
        )


def test_alarm_target_parses_half_hour():
    assert _alarm_target("设置明天早上7点半的闹钟") == ("上午", 7, 30)


def test_alarm_target_understands_evening_twelve_hour_phrase():
    assert _alarm_target("设置今天晚上6点出发吃饭的闹钟") == ("下午", 6, 0)


def test_alarm_target_parses_noon_colon_format():
    assert _alarm_target("设置明天中午12:00的闹钟") == ("下午", 12, 0)


def test_alarm_save_finishes_after_confirm_returns_to_list():
    history = [{"action": "TAP", "reason": "确认闹钟 上午07:30"}]
    assert _alarm_save_confirmed("设置早上7点30分闹钟", "闹钟7小时2分钟后响铃", history)
    assert not _alarm_save_confirmed("设置早上7点30分闹钟", "新建闹钟后响铃", history)


def test_alarm_picker_uses_stable_direction_from_current_value(tmp_path: Path):
    from PIL import Image, ImageDraw
    path = tmp_path / "picker.png"
    image = Image.new("RGB", (1080, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 535, 270, 605), fill=(20, 90, 245))
    draw.rectangle((516, 546, 560, 583), fill=(20, 90, 245))
    draw.rectangle((834, 546, 884, 583), fill=(20, 90, 245))
    image.save(path)
    elements = (
        MarkedElement(1, "上午", (170, 535, 270, 605)),
        MarkedElement(2, "11", (516, 546, 560, 583)),
        MarkedElement(3, "15", (834, 546, 884, 583)),
    )
    action = _alarm_picker_action("设置早上7点30分闹钟", elements, 91, str(path))
    assert action is not None
    assert action.x == action.x2 == 540
    assert action.y2 > action.y
    assert action.y2 - action.y == 120
    assert "快速调整 1 格" in action.reason


def test_alarm_picker_switches_to_minute_after_hour_matches(tmp_path: Path):
    from PIL import Image, ImageDraw
    path = tmp_path / "picker.png"
    image = Image.new("RGB", (1080, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 535, 270, 605), fill=(20, 90, 245))
    draw.rectangle((516, 546, 560, 583), fill=(20, 90, 245))
    draw.rectangle((834, 546, 884, 583), fill=(20, 90, 245))
    image.save(path)
    elements = (
        MarkedElement(1, "上午", (170, 535, 270, 605)),
        MarkedElement(2, "07", (516, 546, 560, 583)),
        MarkedElement(3, "15", (834, 546, 884, 583)),
    )
    action = _alarm_picker_action("设置早上7点30分闹钟", elements, 91, str(path))
    assert action is not None
    assert action.x == action.x2 == 858
    assert action.y2 < action.y


def test_alarm_picker_taps_visible_target_hour_in_one_action(tmp_path: Path):
    from PIL import Image, ImageDraw
    path = tmp_path / "picker.png"
    image = Image.new("RGB", (1080, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 535, 270, 605), fill=(20, 90, 245))
    draw.rectangle((516, 546, 560, 583), fill=(20, 90, 245))
    draw.rectangle((834, 546, 884, 583), fill=(20, 90, 245))
    image.save(path)
    elements = (
        MarkedElement(1, "上午", (170, 535, 270, 605)),
        MarkedElement(2, "10", (516, 546, 560, 583)),
        MarkedElement(3, "09", (516, 426, 560, 463)),
        MarkedElement(4, "11", (834, 546, 884, 583)),
    )

    action = _alarm_picker_action("设置每天早上9点闹钟", elements, 91, str(path))

    assert action is not None
    assert action.action == ActionType.TAP
    assert (action.x, action.y) == elements[2].center


def test_daily_alarm_opens_repeat_settings_before_save(tmp_path: Path):
    from PIL import Image, ImageDraw
    path = tmp_path / "picker.png"
    image = Image.new("RGB", (1080, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 535, 270, 605), fill=(20, 90, 245))
    draw.rectangle((516, 546, 560, 583), fill=(20, 90, 245))
    draw.rectangle((834, 546, 884, 583), fill=(20, 90, 245))
    image.save(path)
    elements = (
        MarkedElement(1, "上午", (170, 535, 270, 605)),
        MarkedElement(2, "09", (516, 546, 560, 583)),
        MarkedElement(3, "00", (834, 546, 884, 583)),
        MarkedElement(4, "不重复", (780, 760, 980, 820)),
    )

    action = _alarm_picker_action("设置每天早上9点闹钟", elements, 91, str(path))

    assert action is not None
    assert action.action == ActionType.TAP
    assert action.reason == "打开闹钟重复设置以选择每天"


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


def test_alarm_picker_infers_morning_when_blue_label_is_missing(tmp_path: Path):
    from PIL import Image, ImageDraw

    path = tmp_path / "picker.png"
    image = Image.new("RGB", (1080, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((516, 546, 560, 583), fill=(20, 90, 245))
    draw.rectangle((834, 546, 884, 583), fill=(20, 90, 245))
    image.save(path)
    elements = (
        MarkedElement(1, "08", (516, 546, 560, 583)),
        MarkedElement(2, "19", (834, 546, 884, 583)),
        MarkedElement(3, "下 午", (181, 629, 268, 714)),
    )
    action = _alarm_picker_action("设置早上7点30分闹钟", elements, 95, str(path))
    assert action is not None
    assert action.x == action.x2 == 540
    assert "小时 08 向目标 07 快速调整 1 格" in action.reason


def test_alarm_picker_taps_measured_confirm_icon_when_values_match(tmp_path: Path):
    from PIL import Image, ImageDraw

    path = tmp_path / "picker.png"
    image = Image.new("RGB", (1080, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 535, 270, 605), fill=(20, 90, 245))
    draw.rectangle((516, 546, 560, 583), fill=(20, 90, 245))
    draw.rectangle((834, 546, 884, 583), fill=(20, 90, 245))
    image.save(path)
    elements = (
        MarkedElement(1, "上午", (170, 535, 270, 605)),
        MarkedElement(2, "07", (516, 546, 560, 583)),
        MarkedElement(3, "30", (834, 546, 884, 583)),
    )
    action = _alarm_picker_action("设置早上7点30分闹钟", elements, 96, str(path))
    assert action is not None
    assert action.action == ActionType.TAP
    assert (action.x, action.y) == (980, 232)


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


def test_wolt_blank_frame_relaunches_once_before_abort(tmp_path: Path):
    state = observed_state(tmp_path)
    state.last_observation["fingerprint"] = "f" * 64
    state.collected_data["app_skill_route"] = "wolt_burger_categories"
    state.collected_data["blank_frame_count"] = 2
    planner = VisionPlanner(FakeClient({"action": "ABORT"}), 31, {}, FakeGrounder())

    action = planner.plan(state)[0]

    assert action.action == ActionType.OPEN_APP
    assert action.package == "com.wolt.android"
    assert state.collected_data["wolt_blank_relaunch_attempted"] is True


def test_package_selection_must_return_installed_package():
    from bearbless.agent.vision import OllamaVisionClient

    class Client(OllamaVisionClient):
        def complete(self, prompt, frame_path=None):
            assert "已安装包名" in prompt
            return {"package": "com.netease.cloudmusic"}

    client = Client()
    assert client.select_package("播放歌曲", ["com.netease.cloudmusic"]) == "com.netease.cloudmusic"
