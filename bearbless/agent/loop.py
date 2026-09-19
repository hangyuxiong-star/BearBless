from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from bearbless.agent.contracts import ActionExecutor, ActionPolicy, CompletionProbe, ObservationBuilder, Planner, StepVerifier, Verifier
from bearbless.agent.model_client import ModelClientError
from bearbless.agent.screen_state import same_screen, screen_fingerprint
from bearbless.agent.state import BudgetExceeded, TaskState, TaskStatus
from bearbless.agent.trace import TaskTrace
from bearbless.errors import AgentTerminalDecision, GuardViolation, IsolationViolation, ModelProtocolError, PolicyViolation
from bearbless.runtime.actions import ActionCapability, ActionType
from bearbless.runtime.shadow_display import ShadowDisplayError


class AgentLoop:
    """Small explicit PLAN → EXECUTE → VERIFY state machine."""

    def __init__(
        self,
        planner: Planner,
        executor: ActionExecutor,
        verifier: Verifier,
        trace: TaskTrace | None = None,
        step_verifier: StepVerifier | None = None,
        observation_builder: ObservationBuilder | None = None,
        action_policy: ActionPolicy | None = None,
        completion_probe: CompletionProbe | None = None,
        should_cancel: Callable[[], bool] | None = None,
        sensitive_confirmer: object | None = None,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.trace = trace
        self.step_verifier = step_verifier
        self.observation_builder = observation_builder
        self.action_policy = action_policy
        self.completion_probe = completion_probe
        self.should_cancel = should_cancel
        self.sensitive_confirmer = sensitive_confirmer

    def run(self, state: TaskState) -> TaskState:
        if state.interruption_budget != 0:
            return self._fail(state, "interruption_budget must be zero")
        if state.status in (TaskStatus.OBSERVING, TaskStatus.GUARDING, TaskStatus.EXECUTING):
            sensitive_effect = state.collected_data.get("sensitive_effect")
            if (
                state.status == TaskStatus.EXECUTING
                and isinstance(sensitive_effect, dict)
                and sensitive_effect.get("phase") in {"PREPARED", "COMMITTED"}
            ):
                # A crash between dispatch and persistence leaves the external
                # effect uncertain. Never replay a send; verify the resulting
                # app state instead.
                state.failure_reason = "recovered uncertain sensitive effect; verifying without replay"
                state.status = TaskStatus.VERIFYING
                self._save(state)
            else:
                try:
                    state.consume_replan()
                except BudgetExceeded as exc:
                    return self._fail(state, str(exc))
                state.failure_reason = f"recovered from uncertain transient state: {state.status.value}"
                state.status = TaskStatus.REPLANNING
                self._save(state)
        while state.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            if self.should_cancel and self.should_cancel():
                return self._fail(state, "cancelled by user")
            try:
                if state.status in (TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.REPLANNING):
                    state.status = TaskStatus.PLANNING
                    state.plan = self.planner.plan(state)
                    if not state.plan:
                        return self._fail(state, "planner returned an empty plan")
                    state.status = TaskStatus.RUNNING
                    self._save(state)
                elif state.status == TaskStatus.RUNNING:
                    self._run_plan(state)
                elif state.status == TaskStatus.VERIFYING:
                    result = self.verifier.verify(state)
                    state.evidence = [dict(item) for item in result.evidence]
                    state.verification_history.append(result.model_dump())
                    if self.trace:
                        self.trace.save_result(result)
                    if result.passed:
                        sensitive_effect = state.collected_data.get("sensitive_effect")
                        if isinstance(sensitive_effect, dict):
                            sensitive_effect["phase"] = "VERIFIED"
                        state.failure_reason = None
                        state.status = TaskStatus.COMPLETED
                    elif isinstance(state.collected_data.get("sensitive_effect"), dict):
                        # Retrying an externally visible action is more harmful
                        # than returning an honest unverified result.
                        return self._fail(
                            state,
                            result.reason or "sensitive effect committed but could not be verified",
                        )
                    elif not result.retryable:
                        return self._fail(state, result.reason or "final verification failed")
                    else:
                        state.consume_replan()
                        state.failure_reason = result.reason
                        state.status = TaskStatus.REPLANNING
                    self._save(state)
            except (AgentTerminalDecision, PolicyViolation) as exc:
                return self._fail(state, str(exc))
            except IsolationViolation as exc:
                return self._fail(state, f"isolation violation: {exc}")
            except ShadowDisplayError as exc:
                return self._fail(state, f"shadow display unavailable: {exc}")
            except BudgetExceeded as exc:
                return self._fail(state, str(exc))
            except ModelProtocolError as exc:
                failures = state.collected_data.setdefault("recoverable_failures", [])
                failures.append({"type": "MODEL_PROTOCOL_ERROR", "detail": str(exc)})
                del failures[:-10]
                try:
                    state.consume_protocol_retry()
                except BudgetExceeded as budget_exc:
                    return self._fail(state, str(budget_exc))
                state.failure_reason = str(exc)
                state.status = TaskStatus.REPLANNING
                self._save(state)
            except ModelClientError as exc:
                # A transport/provider outage is not a phone-state problem.
                # Re-observing the same screen and issuing the same expensive
                # request only makes the UI look stuck, so fail immediately
                # and preserve the exact provider error for the operator.
                failures = state.collected_data.setdefault("recoverable_failures", [])
                failures.append({"type": "MODEL_UNAVAILABLE", "detail": str(exc)})
                del failures[:-10]
                return self._fail(state, str(exc))
            except (GuardViolation, RuntimeError, ValueError) as exc:
                failures = state.collected_data.setdefault("recoverable_failures", [])
                failures.append({"type": type(exc).__name__, "detail": str(exc)})
                del failures[:-10]
                try:
                    state.consume_replan()
                except BudgetExceeded as budget_exc:
                    return self._fail(state, str(budget_exc))
                state.failure_reason = str(exc)
                state.status = TaskStatus.REPLANNING
                self._save(state)
        return state

    def _run_plan(self, state: TaskState) -> None:
        while state.plan:
            if self.should_cancel and self.should_cancel():
                self._fail(state, "cancelled by user")
                return
            action = state.plan.pop(0)
            if action.action == ActionType.FINISH:
                state.status = TaskStatus.VERIFYING
                self._save(state)
                return
            state.consume_step()
            if self.action_policy is not None:
                self.action_policy.authorize(state, action)
            if self.sensitive_confirmer is not None:
                self.sensitive_confirmer.confirm(state, action, self.should_cancel)  # type: ignore[attr-defined]
                self._save(state)
            state.current_subgoal = action.reason or action.action.value
            state.status = TaskStatus.OBSERVING if action.action == ActionType.OBSERVE else TaskStatus.GUARDING
            self._save(state)
            if action.action != ActionType.OBSERVE:
                state.status = TaskStatus.EXECUTING
                if action.capability == ActionCapability.SENSITIVE:
                    state.collected_data["sensitive_effect"] = {
                        "phase": "PREPARED",
                        "action": action.action.value,
                        "package": action.package,
                        "text": action.text,
                        "reason": action.reason,
                    }
                self._save(state)
            frame = self.executor.execute(action)
            if action.capability == ActionCapability.SENSITIVE:
                state.collected_data["sensitive_effect"]["phase"] = "COMMITTED"
                self._save(state)
            history = asdict(action)
            history["action"] = action.action.value
            state.action_history.append(history)
            if frame is not None:
                previous_fingerprint = str((state.last_observation or {}).get("fingerprint") or "")
                current_fingerprint = screen_fingerprint(frame.png)
                frame_path = self.trace.save_frame(frame, state.step_index) if self.trace else None
                state.last_observation = {
                    "display_id": frame.display_id,
                    "captured_at": frame.captured_at,
                    "bytes": len(frame.png),
                    "frame_path": str(frame_path) if frame_path else None,
                    "fingerprint": current_fingerprint,
                }
                if action.action not in (ActionType.OBSERVE, ActionType.WAIT):
                    self._record_step_effect(state, action, previous_fingerprint, current_fingerprint)
            if action.expected_outcome is not None:
                if frame is None or self.step_verifier is None or self.observation_builder is None:
                    raise RuntimeError("expected outcome requires post-action observation and step verifier")
                observation = self.observation_builder.build(frame, action)
                if frame_path:
                    observation = observation.model_copy(update={"frame_path": str(frame_path)})
                previous = None
                step_result = self.step_verifier.verify_step(previous, action, observation)
                state.verification_history.append(step_result.model_dump())
                state.evidence.extend(step_result.evidence)
                if not step_result.passed:
                    raise RuntimeError(step_result.reason or "post-action verification failed")
            if self.completion_probe is not None:
                completion = self.completion_probe.verify(state)
                if completion is not None and completion.passed:
                    state.evidence.extend(completion.evidence)
                    state.verification_history.append(completion.model_dump())
                    state.collected_data["agent_result"] = completion.reason
                    state.failure_reason = None
                    state.plan.clear()
                    state.status = TaskStatus.COMPLETED
                    if self.trace:
                        self.trace.save_result(completion)
                    self._save(state)
                    return
            # A sensitive action is never followed by another model-planned
            # interaction. Re-observe via the executor, then hand the fresh
            # evidence directly to the final verifier. This prevents a
            # successful send from being mistaken for an intermediate state
            # and repeated.
            if action.capability == ActionCapability.SENSITIVE:
                state.plan.clear()
                state.status = TaskStatus.VERIFYING
                self._save(state)
                return
            state.status = TaskStatus.RUNNING
            self._save(state)
        if getattr(self.planner, "reactive", False):
            state.status = TaskStatus.PLANNING
        else:
            state.status = TaskStatus.VERIFYING
        self._save(state)

    @staticmethod
    def _record_step_effect(state: TaskState, action, before: str, after: str) -> None:
        if not same_screen(before, after):
            state.collected_data["consecutive_no_effect"] = 0
            state.collected_data["last_step_outcome"] = {
                "code": "CHANGED",
                "detail": "shadow display changed after the action",
            }
            return
        signature = ":".join(str(value) for value in (
            before, action.action.value, action.x, action.y, action.x2, action.y2,
        ))
        attempts = state.collected_data.setdefault("screen_action_attempts", {})
        count = int(attempts.get(signature, 0)) + 1
        attempts[signature] = count
        consecutive = int(state.collected_data.get("consecutive_no_effect", 0)) + 1
        state.collected_data["consecutive_no_effect"] = consecutive
        code = "LOOP_DETECTED" if count >= 2 or consecutive >= 2 else "NO_EFFECT"
        state.collected_data["last_step_outcome"] = {
            "code": code,
            "detail": "shadow display fingerprint did not change",
            "action_signature": signature,
        }
        # One deterministic no-effect result is enough to prohibit the exact
        # same action on the same screen. A second tap adds no information.
        banned = state.collected_data.setdefault("banned_actions", [])
        if signature not in banned:
            banned.append(signature)
        if code == "LOOP_DETECTED":
            raise RuntimeError(
                "LOOP_DETECTED: two consecutive actions left the shadow screen unchanged"
            )

    def _fail(self, state: TaskState, reason: str) -> TaskState:
        state.status = TaskStatus.FAILED
        state.failure_reason = reason
        self._save(state)
        return state

    def _save(self, state: TaskState) -> None:
        if self.trace:
            self.trace.save_state(state)
