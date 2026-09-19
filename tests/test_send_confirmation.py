from types import SimpleNamespace

import pytest

from bearbless.agent.state import TaskState
from bearbless.errors import PolicyViolation
from bearbless.runtime.actions import Action, ActionCapability, ActionType
from bearbless.runtime.send_confirmation import NotificationSensitiveConfirmer, confirmation_details
from bearbless.schemas import SuccessCriterion, TaskMode, TaskSpec


class FakeBridge:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.requests = []

    def request_send_confirmation(self, token, recipient, message):
        self.requests.append((token, recipient, message))

    def send_confirmation_status(self, token):
        return next(self.statuses)


def sensitive_state(*, confirmed=True):
    scope = "打开qq，给红枣桂花熊发消息：今晚18:00去吃饭"
    return TaskState(
        "agent-confirm",
        scope,
        task_spec=TaskSpec(
            goal=scope,
            task_mode=TaskMode.SENSITIVE_TASK,
            constraints={"user_confirmed_sensitive_action": confirmed, "sensitive_scope": scope},
            success_criteria=[SuccessCriterion(name="sent", description="sent")],
        ),
    )


def send_action():
    return Action(
        ActionType.TAP, display_id=8, x=900, y=2200,
        reason="点击发送", capability=ActionCapability.SENSITIVE,
    )


def test_confirmation_waits_for_phone_approval_and_is_idempotent():
    state = sensitive_state(confirmed=False)
    bridge = FakeBridge(["pending", "approved"])
    confirmer = NotificationSensitiveConfirmer(bridge, timeout_seconds=1, poll_seconds=0)
    confirmer.confirm(state, send_action())
    confirmer.confirm(state, send_action())
    assert len(bridge.requests) == 1
    assert confirmation_details(state) == ("红枣桂花熊", "今晚18:00去吃饭")


def test_confirmation_cancel_fails_closed():
    state = sensitive_state(confirmed=False)
    confirmer = NotificationSensitiveConfirmer(FakeBridge(["cancelled"]), timeout_seconds=1, poll_seconds=0)
    with pytest.raises(PolicyViolation, match="cancelled"):
        confirmer.confirm(state, send_action())


def test_explicit_user_confirmation_skips_disruptive_phone_notification():
    state = sensitive_state(confirmed=True)
    bridge = FakeBridge([])
    confirmer = NotificationSensitiveConfirmer(bridge, timeout_seconds=1, poll_seconds=0)
    confirmer.confirm(state, send_action())
    assert bridge.requests == []
    assert len(state.collected_data["approved_sensitive_actions"]) == 1
