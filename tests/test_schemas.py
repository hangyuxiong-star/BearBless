import pytest
from pydantic import ValidationError

from bearbless.schemas import (
    AgentAction, ExpectedOutcome, Observation, SuccessCriterion, TaskSpec,
)
from bearbless.runtime.actions import ActionType


def test_task_spec_rejects_unknown_fields_and_requires_criteria() -> None:
    spec = TaskSpec(
        goal="Compare travel options",
        success_criteria=[SuccessCriterion(name="saved", description="Result is persisted")],
    )
    assert spec.goal == "Compare travel options"
    with pytest.raises(ValidationError):
        TaskSpec(goal="Compare travel options", success_criteria=[], surprise=True)


def test_agent_action_converts_to_closed_runtime_action() -> None:
    schema = AgentAction(
        action="TAP", display_id=8, x=10, y=20,
        expected_outcome=ExpectedOutcome(description="Settings opens", package="com.android.settings"),
    )
    action = schema.to_runtime()
    assert action.action == ActionType.TAP
    assert action.expected_outcome.package == "com.android.settings"


def test_agent_action_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        AgentAction(action="TAP", display_id=8)
    with pytest.raises(ValidationError):
        AgentAction(action="TYPE", display_id=8, text="x" * 2001)
    with pytest.raises(ValidationError):
        AgentAction(action="CONDITIONAL_TAP", display_id=8, x=20, y=30)


def test_conditional_tap_requires_visible_text_guard() -> None:
    action = AgentAction(
        action="CONDITIONAL_TAP",
        display_id=8,
        x=300,
        y=1535,
        condition_text=("cookies", "persondata"),
        reason="Reject a verified cookie dialog",
    ).to_runtime()
    assert action.action == ActionType.CONDITIONAL_TAP
    assert action.condition_text == ("cookies", "persondata")
