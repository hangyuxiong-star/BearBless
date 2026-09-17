import pytest

from bearbless.agent.action_policy import TaskActionPolicy, authorize_task_start
from bearbless.agent.state import TaskState
from bearbless.errors import PolicyViolation
from bearbless.runtime.actions import Action, ActionCapability, ActionType
from bearbless.schemas import SuccessCriterion, TaskMode, TaskSpec


def state(mode: TaskMode) -> TaskState:
    return TaskState(
        "policy-test",
        "goal",
        task_spec=TaskSpec(
            goal="test goal",
            task_mode=mode,
            success_criteria=[SuccessCriterion(name="done", description="done")],
        ),
    )


def test_read_only_allows_navigation_but_blocks_setting_change():
    policy = TaskActionPolicy()
    policy.authorize(state(TaskMode.READ_ONLY_QUERY), Action(
        ActionType.TAP, display_id=8, x=1, y=1, capability=ActionCapability.NAVIGATE,
    ))
    with pytest.raises(PolicyViolation, match="read-only"):
        policy.authorize(state(TaskMode.READ_ONLY_QUERY), Action(
            ActionType.TAP, display_id=8, x=1, y=1, capability=ActionCapability.CHANGE_SETTING,
        ))


def test_read_only_search_allows_only_goal_scoped_query_text():
    search_state = state(TaskMode.READ_ONLY_QUERY)
    search_state.goal = "打开美团找一家最近的咖啡店"
    policy = TaskActionPolicy()
    policy.authorize(search_state, Action(
        ActionType.TYPE, display_id=8, text="咖啡店", capability=ActionCapability.ENTER_TEXT,
    ))
    with pytest.raises(PolicyViolation, match="read-only"):
        policy.authorize(search_state, Action(
            ActionType.TYPE, display_id=8, text="替我填写手机号", capability=ActionCapability.ENTER_TEXT,
        ))


def test_read_only_status_query_still_blocks_text_entry():
    status_state = state(TaskMode.READ_ONLY_QUERY)
    status_state.goal = "查看蓝牙是否开启"
    with pytest.raises(PolicyViolation, match="read-only"):
        TaskActionPolicy().authorize(status_state, Action(
            ActionType.TYPE, display_id=8, text="蓝牙", capability=ActionCapability.ENTER_TEXT,
        ))


def test_sensitive_task_requires_takeover_before_any_interaction():
    with pytest.raises(PolicyViolation, match="takeover"):
        TaskActionPolicy().authorize(state(TaskMode.SENSITIVE_TASK), Action(
            ActionType.TAP, display_id=8, x=1, y=1, capability=ActionCapability.NAVIGATE,
        ))
    with pytest.raises(PolicyViolation, match="before app launch"):
        authorize_task_start(state(TaskMode.SENSITIVE_TASK))


def test_explicitly_confirmed_message_allows_scoped_sensitive_actions():
    confirmed = state(TaskMode.SENSITIVE_TASK)
    confirmed.task_spec.constraints["user_confirmed_sensitive_action"] = True
    confirmed.task_spec.constraints["sensitive_scope"] = "给小熊发消息：你吃饭了吗？"
    authorize_task_start(confirmed)
    TaskActionPolicy().authorize(confirmed, Action(
        ActionType.TAP, display_id=8, x=1, y=1, reason="点击发送", capability=ActionCapability.SENSITIVE,
    ))
    TaskActionPolicy().authorize(confirmed, Action(
        ActionType.TYPE, display_id=8, text="你吃饭了吗？", capability=ActionCapability.ENTER_TEXT,
    ))
    with pytest.raises(PolicyViolation, match="differs"):
        TaskActionPolicy().authorize(confirmed, Action(
            ActionType.TYPE, display_id=8, text="错误内容", capability=ActionCapability.ENTER_TEXT,
        ))
    with pytest.raises(PolicyViolation, match="final send"):
        TaskActionPolicy().authorize(confirmed, Action(
            ActionType.TAP, display_id=8, x=1, y=1, reason="点击联系人", capability=ActionCapability.SENSITIVE,
        ))
    with pytest.raises(PolicyViolation, match="outside the confirmed"):
        TaskActionPolicy().authorize(confirmed, Action(
            ActionType.TAP, display_id=8, x=1, y=1, capability=ActionCapability.CHANGE_SETTING,
        ))


def test_unknown_tap_capability_fails_closed():
    with pytest.raises(PolicyViolation, match="no declared capability"):
        TaskActionPolicy().authorize(state(TaskMode.MUTATING_TASK), Action(
            ActionType.TAP, display_id=8, x=1, y=1,
        ))


@pytest.mark.parametrize("reason", [
    "点击搜索框", "Tap search box", "点击地址栏", "选择 input field",
])
def test_text_focus_taps_are_blocked_before_execution(reason):
    with pytest.raises(PolicyViolation, match="primary-display IME"):
        TaskActionPolicy().authorize(state(TaskMode.READ_ONLY_QUERY), Action(
            ActionType.TAP,
            display_id=8,
            x=100,
            y=200,
            reason=reason,
            capability=ActionCapability.SEARCH,
        ))
