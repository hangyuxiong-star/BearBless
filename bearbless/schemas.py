from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SuccessCriterion(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    required: bool = True


class TaskMode(str, Enum):
    READ_ONLY_QUERY = "READ_ONLY_QUERY"
    MUTATING_TASK = "MUTATING_TASK"
    SENSITIVE_TASK = "SENSITIVE_TASK"


class TaskSpec(StrictSchema):
    goal: str = Field(min_length=3, max_length=4000)
    constraints: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: list[str] = Field(default_factory=list, max_length=50)
    success_criteria: list[SuccessCriterion] = Field(min_length=1, max_length=20)
    forbidden_actions: list[str] = Field(default_factory=list, max_length=50)
    interruption_budget: int = Field(default=0, ge=0, le=20)
    task_mode: TaskMode = TaskMode.MUTATING_TASK


class ResultFact(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    value: Any
    evidence: str = Field(min_length=1, max_length=1000)


class TaskResult(StrictSchema):
    summary: str = Field(min_length=1, max_length=2000)
    facts: list[ResultFact] = Field(default_factory=list, max_length=50)


DecisionName = Literal[
    "CLICK_ELEMENT", "TAP", "SWIPE", "TYPE", "KEY", "BACK", "WAIT",
    "REPORT", "TAKE_OVER", "ABORT", "FINISH",
]
CapabilityName = Literal[
    "NAVIGATE", "READ", "SEARCH", "ENTER_TEXT", "MEDIA_CONTROL",
    "CHANGE_SETTING", "WRITE_DATA", "SENSITIVE",
]


class PhoneDecision(StrictSchema):
    """Untrusted model proposal; never executed without runtime conversion."""

    action: DecisionName
    element_id: int | None = Field(default=None, ge=1)
    display_id: int | None = None
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int | None = None
    text: str | None = Field(default=None, max_length=2000)
    keycode: str | int | None = None
    seconds: float | None = None
    reason: str = Field(default="", max_length=1000)
    observed_result: str | None = Field(default=None, max_length=2000)
    capability: CapabilityName | None = None
    target: str | None = Field(default=None, max_length=500)
    confidence: float | None = Field(default=None, ge=0, le=1)
    result: TaskResult | None = None

    @model_validator(mode="after")
    def decision_fields(self):
        if self.action == "CLICK_ELEMENT" and self.element_id is None:
            raise ValueError("CLICK_ELEMENT requires element_id")
        if self.action == "TAP" and (self.x is None or self.y is None):
            raise ValueError("TAP requires x and y")
        if self.action == "SWIPE" and None in (self.x, self.y, self.x2, self.y2):
            raise ValueError("SWIPE requires both points")
        if self.action == "TYPE" and self.text is None:
            raise ValueError("TYPE requires text")
        if self.action == "KEY" and self.keycode is None:
            raise ValueError("KEY requires keycode")
        if self.action in {"CLICK_ELEMENT", "TAP", "SWIPE", "TYPE", "KEY", "BACK"} and self.capability is None:
            raise ValueError("interactive decision requires capability")
        if self.action in {"REPORT", "FINISH"} and not (self.result or self.observed_result or self.reason):
            raise ValueError("REPORT requires an observed result or reason")
        return self


class UIElement(StrictSchema):
    role: str = Field(min_length=1, max_length=100)
    label: str | None = Field(default=None, max_length=500)
    bounds: tuple[int, int, int, int] | None = None

    @field_validator("bounds")
    @classmethod
    def valid_bounds(cls, value: tuple[int, int, int, int] | None):
        if value is not None and (value[0] > value[2] or value[1] > value[3]):
            raise ValueError("bounds must be ordered left, top, right, bottom")
        return value


class Observation(StrictSchema):
    display_id: int = Field(gt=0)
    captured_at: str
    frame_path: str | None = None
    package: str | None = None
    activity: str | None = None
    visible_text: list[str] = Field(default_factory=list, max_length=500)
    elements: list[UIElement] = Field(default_factory=list, max_length=1000)


class ExpectedOutcome(StrictSchema):
    description: str = Field(min_length=1, max_length=1000)
    package: str | None = None
    required_text: list[str] = Field(default_factory=list, max_length=50)
    forbidden_text: list[str] = Field(default_factory=list, max_length=50)


ActionName = Literal["OPEN_APP", "TAP", "CONDITIONAL_TAP", "SWIPE", "TYPE", "KEY", "BACK", "WAIT", "OBSERVE", "FINISH"]


class AgentAction(StrictSchema):
    action: ActionName
    display_id: int | None = None
    package: str | None = None
    uri: str | None = Field(default=None, max_length=4000)
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int | None = Field(default=None, ge=1, le=60_000)
    text: str | None = Field(default=None, max_length=2000)
    condition_text: tuple[str, ...] = Field(default=(), max_length=20)
    keycode: str | int | None = None
    seconds: float | None = Field(default=None, ge=0, le=60)
    reason: str = Field(default="", max_length=1000)
    expected_outcome: ExpectedOutcome | None = None
    irreversible: bool = False
    capability: CapabilityName | None = None

    @model_validator(mode="after")
    def required_fields(self):
        interactive = {"OPEN_APP", "TAP", "CONDITIONAL_TAP", "SWIPE", "TYPE", "KEY", "BACK"}
        if self.action in interactive and self.display_id is None:
            raise ValueError("interactive action requires display_id")
        if self.action == "OPEN_APP" and not self.package:
            raise ValueError("OPEN_APP requires package")
        if self.action in {"TAP", "CONDITIONAL_TAP"} and (self.x is None or self.y is None):
            raise ValueError(f"{self.action} requires x and y")
        if self.action == "CONDITIONAL_TAP" and not self.condition_text:
            raise ValueError("CONDITIONAL_TAP requires condition_text")
        if self.action == "SWIPE" and None in (self.x, self.y, self.x2, self.y2, self.duration_ms):
            raise ValueError("SWIPE requires both points and duration_ms")
        if self.action == "TYPE" and self.text is None:
            raise ValueError("TYPE requires text")
        if self.action == "KEY" and self.keycode is None:
            raise ValueError("KEY requires keycode")
        return self

    def to_runtime(self):
        from bearbless.runtime.actions import Action, ActionCapability, ActionType
        data = self.model_dump(exclude={"expected_outcome", "irreversible"})
        data["action"] = ActionType(data["action"])
        data["capability"] = ActionCapability(data.get("capability") or "UNKNOWN")
        return Action(**data, expected_outcome=self.expected_outcome, irreversible=self.irreversible)


class ActionRecord(StrictSchema):
    index: int = Field(ge=0)
    action: AgentAction
    result: Literal["ok", "blocked", "error"]
    started_at: str
    finished_at: str
    verification: "VerificationResult | None" = None


class VerificationResult(StrictSchema):
    passed: bool
    retryable: bool = False
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    reason: str = ""
