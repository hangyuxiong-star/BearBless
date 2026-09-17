from __future__ import annotations

from typing import Protocol

from bearbless.schemas import Observation, VerificationResult
from bearbless.agent.state import TaskState
from bearbless.runtime.actions import Action
from bearbless.runtime.observation_backend import Frame


class Planner(Protocol):
    def plan(self, state: TaskState) -> list[Action]: ...


class ActionExecutor(Protocol):
    def execute(self, action: Action) -> Frame | None: ...


class Verifier(Protocol):
    def verify(self, state: TaskState) -> VerificationResult: ...


class StepVerifier(Protocol):
    def verify_step(self, previous: Observation | None, action: Action, current: Observation) -> VerificationResult: ...


class ObservationBuilder(Protocol):
    def build(self, frame: Frame, action: Action) -> Observation: ...


class ActionPolicy(Protocol):
    def authorize(self, state: TaskState, action: Action) -> None: ...


class CompletionProbe(Protocol):
    def verify(self, state: TaskState) -> VerificationResult | None: ...
