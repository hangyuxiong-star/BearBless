from __future__ import annotations

from dataclasses import asdict
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any

from bearbless.agent.contracts import VerificationResult
from bearbless.agent.state import TaskState, TaskStatus
from bearbless.runtime.observation_backend import Frame


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class TaskTrace:
    def __init__(self, root: Path, task_id: str) -> None:
        self.run_dir = root / task_id
        self.screens_dir = self.run_dir / "screens"
        self.screens_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.run_dir / "state.json"
        self.result_path = self.run_dir / "result.json"

    def save_state(self, state: TaskState) -> None:
        self._atomic_write(self.state_path, (json.dumps(_jsonable(asdict(state)), indent=2, sort_keys=True) + "\n").encode())

    def save_result(self, result: VerificationResult) -> None:
        self._atomic_write(self.result_path, (json.dumps(_jsonable(result.model_dump()), indent=2, sort_keys=True) + "\n").encode())

    def save_frame(self, frame: Frame, index: int) -> Path:
        path = self.screens_dir / f"frame_{index:04d}.png"
        self._atomic_write(path, frame.png)
        return path

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_bytes(data)
        temp.replace(path)

    def load_state(self) -> TaskState:
        if not self.state_path.exists():
            raise FileNotFoundError(self.state_path)
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        from bearbless.runtime.actions import Action, ActionCapability, ActionType
        payload["status"] = TaskStatus(payload["status"])
        from bearbless.schemas import ExpectedOutcome
        plan = []
        for item in payload.get("plan", []):
            item = {**item, "action": ActionType(item["action"])}
            item["capability"] = ActionCapability(item.get("capability", "UNKNOWN"))
            if item.get("expected_outcome"):
                item["expected_outcome"] = ExpectedOutcome.model_validate(item["expected_outcome"])
            plan.append(Action(**item))
        payload["plan"] = plan
        if payload.get("task_spec"):
            from bearbless.schemas import TaskSpec
            payload["task_spec"] = TaskSpec.model_validate(payload["task_spec"])
        return TaskState(**payload)
