from pathlib import Path

from bearbless.agent.contracts import VerificationResult
from bearbless.agent.loop import AgentLoop
from bearbless.agent.planner import ManualPlanner
from bearbless.agent.state import TaskState, TaskStatus
from bearbless.agent.trace import TaskTrace
from bearbless.agent.verifier import EvidenceVerifier
from bearbless.runtime.actions import Action, ActionType
from bearbless.errors import ModelProtocolError


class Executor:
    def __init__(self):
        self.actions = []
    def execute(self, action):
        self.actions.append(action)
        return None


class AlwaysPass:
    def verify(self, state):
        return VerificationResult(passed=True, evidence=[{"criterion": "done", "passed": True}])


class AlwaysFail:
    def verify(self, state):
        return VerificationResult(passed=False, retryable=True, evidence=[], reason="not verified")


def test_deterministic_loop_completes_only_after_verifier(tmp_path: Path) -> None:
    executor = Executor()
    planner = ManualPlanner((
        Action(ActionType.WAIT, seconds=0),
        Action(ActionType.FINISH),
    ))
    state = TaskState("task-1", "test")
    result = AgentLoop(planner, executor, AlwaysPass(), TaskTrace(tmp_path, state.task_id)).run(state)
    assert result.status == TaskStatus.COMPLETED
    assert result.step_index == 1
    assert (tmp_path / "task-1" / "state.json").exists()
    assert (tmp_path / "task-1" / "result.json").exists()
    restored = TaskTrace(tmp_path, state.task_id).load_state()
    assert restored.status == TaskStatus.COMPLETED
    assert restored.verification_history[0]["passed"] is True


def test_step_budget_fails_closed() -> None:
    planner = ManualPlanner((Action(ActionType.WAIT, seconds=0), Action(ActionType.FINISH)))
    state = TaskState("task-2", "test", max_steps=0)
    result = AgentLoop(planner, Executor(), AlwaysPass()).run(state)
    assert result.status == TaskStatus.FAILED
    assert "step budget" in (result.failure_reason or "")


def test_replan_budget_fails_after_verification_failures() -> None:
    planner = ManualPlanner((Action(ActionType.FINISH),))
    state = TaskState("task-3", "test", max_replans=1)
    result = AgentLoop(planner, Executor(), AlwaysFail()).run(state)
    assert result.status == TaskStatus.FAILED
    assert result.replans == 1
    assert "replan budget" in (result.failure_reason or "")


def test_evidence_verifier_checks_named_criteria() -> None:
    state = TaskState("task-4", "test", evidence=[{"criterion": "persisted", "passed": True, "evidence": "frame.png"}])
    assert EvidenceVerifier(("persisted",)).verify(state).passed
    assert not EvidenceVerifier(("persisted", "title visible")).verify(state).passed


def test_nonzero_interruption_budget_is_rejected() -> None:
    state = TaskState("task-5", "test", interruption_budget=1)
    result = AgentLoop(ManualPlanner((Action(ActionType.FINISH),)), Executor(), AlwaysPass()).run(state)
    assert result.status == TaskStatus.FAILED


def test_model_protocol_failure_has_separate_retry_budget() -> None:
    class FlakyPlanner:
        reactive = True

        def __init__(self):
            self.calls = 0

        def plan(self, state):
            self.calls += 1
            if self.calls == 1:
                raise ModelProtocolError("missing swipe endpoint")
            return [Action(ActionType.FINISH)]

    state = TaskState("protocol", "test", max_replans=0, max_protocol_retries=1)
    result = AgentLoop(FlakyPlanner(), Executor(), AlwaysPass()).run(state)
    assert result.status == TaskStatus.COMPLETED
    assert result.protocol_retries == 1
    assert result.replans == 0


def test_success_clears_stale_recoverable_failure() -> None:
    state = TaskState("recovered", "test", failure_reason="old transient failure")
    result = AgentLoop(
        ManualPlanner((Action(ActionType.FINISH),)), Executor(), AlwaysPass()
    ).run(state)
    assert result.status == TaskStatus.COMPLETED
    assert result.failure_reason is None
