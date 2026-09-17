from __future__ import annotations

from dataclasses import dataclass

from bearbless.agent.state import TaskState
from bearbless.runtime.actions import Action


@dataclass(frozen=True)
class ManualPlanner:
    """A deterministic plan source used before any model integration."""

    actions: tuple[Action, ...]

    def plan(self, state: TaskState) -> list[Action]:
        del state
        return list(self.actions)
