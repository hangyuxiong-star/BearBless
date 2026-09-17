from __future__ import annotations

from dataclasses import dataclass

from bearbless.errors import GuardViolation
from bearbless.runtime.actions import Action
from bearbless.runtime.shadow_display import ShadowDisplay


@dataclass
class ConflictGuard:
    display: ShadowDisplay
    width: int
    height: int
    interruption_budget: int = 0

    def check(self, action: Action) -> int | None:
        action.validate(width=self.width, height=self.height)
        if self.interruption_budget != 0:
            raise GuardViolation("BearBless requires interruption_budget=0")
        if action.display_id == 0:
            raise GuardViolation("Display 0 is owned by the human")
        if action.irreversible:
            raise GuardViolation("irreversible actions are outside the BearBless MVP")
        if action.display_id is not None:
            live_id = self.display.resolve_live_id()
            if action.display_id != live_id:
                raise GuardViolation(f"action display {action.display_id} does not match live shadow display {live_id}")
            return live_id
        return None
