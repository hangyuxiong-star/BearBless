from bearbless.agent.verifier import ExpectedOutcomeVerifier
from bearbless.schemas import ExpectedOutcome, Observation
from bearbless.runtime.actions import Action, ActionType


def test_step_verifier_passes_structured_evidence() -> None:
    action = Action(
        ActionType.OPEN_APP,
        display_id=8,
        package="com.android.settings",
        expected_outcome=ExpectedOutcome(
            description="Settings visible",
            package="com.android.settings",
            required_text=["Settings"],
        ),
    )
    observation = Observation(
        display_id=8,
        captured_at="now",
        package="com.android.settings",
        visible_text=["Settings", "Display"],
    )
    assert ExpectedOutcomeVerifier().verify_step(None, action, observation).passed


def test_step_verifier_reports_missing_evidence_as_retryable() -> None:
    action = Action(
        ActionType.OPEN_APP,
        display_id=8,
        package="com.android.settings",
        expected_outcome=ExpectedOutcome(description="Settings visible", required_text=["Settings"]),
    )
    observation = Observation(display_id=8, captured_at="now", visible_text=["Home"])
    result = ExpectedOutcomeVerifier().verify_step(None, action, observation)
    assert not result.passed
    assert result.retryable
    assert result.missing
