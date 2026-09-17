from bearbless.agent.loop import AgentLoop
from bearbless.agent.planner import ManualPlanner
from bearbless.agent.state import TaskState, TaskStatus
from bearbless.runtime.actions import Action, ActionType
from bearbless.schemas import VerificationResult


class Executor:
    def execute(self, action):
        return None


class NonRetryableFailure:
    def verify(self, state):
        return VerificationResult(passed=False, retryable=False, reason="evidence contradicts report")


def test_nonretryable_final_verification_does_not_burn_replan_budget():
    state = TaskState("verify-stop", "goal", max_replans=3)
    result = AgentLoop(ManualPlanner((Action(ActionType.FINISH),)), Executor(), NonRetryableFailure()).run(state)
    assert result.status == TaskStatus.FAILED
    assert result.replans == 0
    assert result.failure_reason == "evidence contradicts report"
