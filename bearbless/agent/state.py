from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bearbless.schemas import TaskSpec
from bearbless.runtime.actions import Action


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    OBSERVING = "OBSERVING"
    GUARDING = "GUARDING"
    EXECUTING = "EXECUTING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    REPLANNING = "REPLANNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class TaskState:
    task_id: str
    goal: str
    task_spec: TaskSpec | None = None
    status: TaskStatus = TaskStatus.PENDING
    shadow_display_id: int | None = None
    step_index: int = 0
    max_steps: int = 40
    replans: int = 0
    max_replans: int = 5
    protocol_retries: int = 0
    max_protocol_retries: int = 2
    sensitive_retries: int = 0
    max_sensitive_retries: int = 0
    plan: list[Action] = field(default_factory=list)
    collected_data: dict[str, Any] = field(default_factory=dict)
    last_observation: dict[str, Any] | None = None
    action_history: list[dict[str, Any]] = field(default_factory=list)
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    interruption_budget: int = 0
    failure_reason: str | None = None
    current_subgoal: str | None = None

    def consume_step(self) -> None:
        if self.step_index >= self.max_steps:
            raise BudgetExceeded(f"step budget exhausted ({self.max_steps})")
        self.step_index += 1

    def consume_replan(self) -> None:
        if self.replans >= self.max_replans:
            raise BudgetExceeded(f"replan budget exhausted ({self.max_replans})")
        self.replans += 1

    def consume_sensitive_retry(self) -> None:
        if self.sensitive_retries >= self.max_sensitive_retries:
            raise BudgetExceeded(f"sensitive retry budget exhausted ({self.max_sensitive_retries})")
        self.sensitive_retries += 1

    def consume_protocol_retry(self) -> None:
        if self.protocol_retries >= self.max_protocol_retries:
            raise BudgetExceeded(f"model protocol retry budget exhausted ({self.max_protocol_retries})")
        self.protocol_retries += 1
