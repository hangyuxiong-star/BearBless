from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from bearbless.agent.policy import infer_task_mode
from bearbless.schemas import TaskMode


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    goal: str
    expected_mode: TaskMode
    must_not_mutate: bool


def load_evaluation_cases(path: Path = Path("evals/mobile_tasks.json")) -> tuple[EvaluationCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(EvaluationCase(
        case_id=str(item["id"]),
        goal=str(item["goal"]),
        expected_mode=TaskMode(item["expected_mode"]),
        must_not_mutate=bool(item["must_not_mutate"]),
    ) for item in payload)


def validate_evaluation_policy(cases: tuple[EvaluationCase, ...]) -> list[str]:
    return [
        f"{case.case_id}: expected {case.expected_mode.value}, got {infer_task_mode(case.goal).value}"
        for case in cases
        if infer_task_mode(case.goal) != case.expected_mode
    ]
