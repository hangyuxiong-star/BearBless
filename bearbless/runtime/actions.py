from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bearbless.schemas import ExpectedOutcome


class ActionType(str, Enum):
    OPEN_APP = "OPEN_APP"
    SET_ALARM = "SET_ALARM"
    CLICK_TEXT = "CLICK_TEXT"
    TAP = "TAP"
    CONDITIONAL_TAP = "CONDITIONAL_TAP"
    SWIPE = "SWIPE"
    TYPE = "TYPE"
    TYPE_BOTTOM = "TYPE_BOTTOM"
    KEY = "KEY"
    BACK = "BACK"
    WAIT = "WAIT"
    OBSERVE = "OBSERVE"
    FINISH = "FINISH"


class ActionCapability(str, Enum):
    UNKNOWN = "UNKNOWN"
    NAVIGATE = "NAVIGATE"
    READ = "READ"
    SEARCH = "SEARCH"
    ENTER_TEXT = "ENTER_TEXT"
    MEDIA_CONTROL = "MEDIA_CONTROL"
    CHANGE_SETTING = "CHANGE_SETTING"
    WRITE_DATA = "WRITE_DATA"
    SENSITIVE = "SENSITIVE"


class ActionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    action: ActionType
    display_id: int | None = None
    package: str | None = None
    uri: str | None = None
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int | None = None
    text: str | None = None
    allow_multiple: bool = False
    condition_text: tuple[str, ...] = ()
    keycode: str | int | None = None
    seconds: float | None = None
    hour: int | None = None
    minute: int | None = None
    reason: str = ""
    expected_outcome: ExpectedOutcome | None = None
    irreversible: bool = False
    capability: ActionCapability = ActionCapability.UNKNOWN

    def validate(self, *, width: int, height: int, max_text_length: int = 2000) -> None:
        interactive = {ActionType.OPEN_APP, ActionType.SET_ALARM, ActionType.CLICK_TEXT, ActionType.TAP, ActionType.CONDITIONAL_TAP, ActionType.SWIPE, ActionType.TYPE, ActionType.TYPE_BOTTOM, ActionType.KEY, ActionType.BACK}
        if self.action in interactive and self.display_id is None:
            raise ActionValidationError("interactive action requires display_id")
        if self.action == ActionType.OPEN_APP and not self.package:
            raise ActionValidationError("OPEN_APP requires package")
        if self.action == ActionType.SET_ALARM:
            if self.hour is None or not 0 <= self.hour <= 23:
                raise ActionValidationError("SET_ALARM hour must be between 0 and 23")
            if self.minute is None or not 0 <= self.minute <= 59:
                raise ActionValidationError("SET_ALARM minute must be between 0 and 59")
        if self.action == ActionType.CLICK_TEXT and (not self.package or not self.text):
            raise ActionValidationError("CLICK_TEXT requires package and text")
        if self.action in (ActionType.TAP, ActionType.CONDITIONAL_TAP):
            self._validate_point(self.x, self.y, width, height)
        if self.action == ActionType.CONDITIONAL_TAP and not self.condition_text:
            raise ActionValidationError("CONDITIONAL_TAP requires condition_text")
        if self.action == ActionType.SWIPE:
            self._validate_point(self.x, self.y, width, height)
            self._validate_point(self.x2, self.y2, width, height)
            if self.duration_ms is None or not 1 <= self.duration_ms <= 60_000:
                raise ActionValidationError("SWIPE duration_ms must be between 1 and 60000")
        if self.action in (ActionType.TYPE, ActionType.TYPE_BOTTOM) and (self.text is None or len(self.text) > max_text_length):
            raise ActionValidationError(f"TYPE text must be present and at most {max_text_length} characters")
        if self.action == ActionType.KEY and self.keycode is None:
            raise ActionValidationError("KEY requires keycode")
        if self.action == ActionType.WAIT and (self.seconds is None or not 0 <= self.seconds <= 60):
            raise ActionValidationError("WAIT seconds must be between 0 and 60")

    @staticmethod
    def _validate_point(x: int | None, y: int | None, width: int, height: int) -> None:
        if x is None or y is None or not (0 <= x < width and 0 <= y < height):
            raise ActionValidationError(f"coordinate ({x}, {y}) is outside {width}x{height}")
