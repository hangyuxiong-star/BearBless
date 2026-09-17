from bearbless.agent.loop import AgentLoop
from bearbless.agent.planner import ManualPlanner
from bearbless.agent.state import TaskState, TaskStatus
from bearbless.agent.verifier import ExpectedOutcomeVerifier
from bearbless.schemas import ExpectedOutcome, Observation, VerificationResult
from bearbless.runtime.actions import Action, ActionType
from bearbless.runtime.observation_backend import Frame


class Executor:
    def execute(self, action):
        return Frame(action.display_id or 8, "now", b"\x89PNGfixture")


class Builder:
    def build(self, frame, action):
        return Observation(
            display_id=frame.display_id,
            captured_at=frame.captured_at,
            package=action.package,
            visible_text=["Settings"],
        )


class FinalPass:
    def verify(self, state):
        return VerificationResult(passed=True)


def test_transient_checkpoint_recovers_by_replanning() -> None:
    state = TaskState("recovery", "goal", status=TaskStatus.EXECUTING)
    planner = ManualPlanner((Action(ActionType.FINISH),))
    result = AgentLoop(planner, Executor(), FinalPass()).run(state)
    assert result.status == TaskStatus.COMPLETED
    assert result.replans == 1


def test_expected_outcome_runs_step_verifier() -> None:
    action = Action(
        ActionType.OPEN_APP,
        display_id=8,
        package="com.android.settings",
        expected_outcome=ExpectedOutcome(
            description="Settings appears",
            package="com.android.settings",
            required_text=["Settings"],
        ),
    )
    state = TaskState("step-verify", "goal")
    planner = ManualPlanner((action, Action(ActionType.FINISH)))
    result = AgentLoop(
        planner, Executor(), FinalPass(),
        step_verifier=ExpectedOutcomeVerifier(), observation_builder=Builder(),
    ).run(state)
    assert result.status == TaskStatus.COMPLETED
    assert result.verification_history[0]["passed"] is True
