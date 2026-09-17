import pytest

from bearbless.errors import GuardViolation
from bearbless.runtime.actions import Action, ActionType, ActionValidationError
from bearbless.runtime.guard import ConflictGuard


class FakeDisplay:
    def resolve_live_id(self):
        return 8


def test_action_validates_coordinates_and_text() -> None:
    Action(ActionType.TAP, display_id=8, x=10, y=20).validate(width=100, height=200)
    with pytest.raises(ActionValidationError):
        Action(ActionType.TAP, display_id=8, x=100, y=20).validate(width=100, height=200)


def test_guard_rejects_display_zero_and_stale_id() -> None:
    guard = ConflictGuard(FakeDisplay(), 100, 200)  # type: ignore[arg-type]
    with pytest.raises(GuardViolation, match="owned by the human"):
        guard.check(Action(ActionType.TAP, display_id=0, x=1, y=1))
    with pytest.raises(GuardViolation, match="does not match"):
        guard.check(Action(ActionType.TAP, display_id=7, x=1, y=1))
    assert guard.check(Action(ActionType.TAP, display_id=8, x=1, y=1)) == 8
