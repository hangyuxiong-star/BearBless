from __future__ import annotations

import re

from bearbless.agent.state import TaskState
from bearbless.errors import PolicyViolation
from bearbless.runtime.actions import Action, ActionCapability, ActionType
from bearbless.schemas import TaskMode


_NON_INTERACTIVE = {ActionType.OBSERVE, ActionType.WAIT, ActionType.FINISH}
_READ_ONLY_ALLOWED = {
    ActionCapability.NAVIGATE,
    ActionCapability.READ,
    ActionCapability.SEARCH,
}
_CONFIRMED_MESSAGE_ALLOWED = {
    ActionCapability.NAVIGATE,
    ActionCapability.SEARCH,
    ActionCapability.ENTER_TEXT,
    ActionCapability.SENSITIVE,
}


class TaskActionPolicy:
    """Deterministic capability gate between model decisions and execution."""

    def authorize(self, state: TaskState, action: Action) -> None:
        if action.action in _NON_INTERACTIVE:
            return
        if action.action in {ActionType.TAP, ActionType.CONDITIONAL_TAP} and self._may_focus_text_input(action):
            raise PolicyViolation(
                "tapping a text-entry surface is forbidden because focus may summon the primary-display IME; "
                "use the accessibility bridge or a semantic URI/deep link"
            )
        mode = state.task_spec.task_mode if state.task_spec else TaskMode.MUTATING_TASK
        capability = action.capability
        if capability == ActionCapability.UNKNOWN and action.action in {
            ActionType.OPEN_APP, ActionType.CONDITIONAL_TAP, ActionType.SWIPE, ActionType.BACK,
        }:
            capability = ActionCapability.NAVIGATE
        if capability == ActionCapability.UNKNOWN:
            raise PolicyViolation(f"interactive {action.action.value} has no declared capability")
        confirmed = bool(
            state.task_spec
            and state.task_spec.constraints.get("user_confirmed_sensitive_action") is True
        )
        if mode == TaskMode.SENSITIVE_TASK and not confirmed:
            raise PolicyViolation("sensitive task requires explicit human takeover")
        if mode == TaskMode.SENSITIVE_TASK and capability not in _CONFIRMED_MESSAGE_ALLOWED:
            raise PolicyViolation(f"capability {capability.value} is outside the confirmed sensitive scope")
        if capability == ActionCapability.SENSITIVE and not confirmed:
            raise PolicyViolation("sensitive capability is never auto-executed")
        if mode == TaskMode.SENSITIVE_TASK and confirmed:
            scope = str(state.task_spec.constraints.get("sensitive_scope") or state.goal)
            if capability == ActionCapability.SENSITIVE and "发送" not in (action.reason or ""):
                raise PolicyViolation("sensitive capability is reserved for the explicit final send action")
            if capability == ActionCapability.ENTER_TEXT:
                match = re.search(r"(?:消息|发消息)\s*[：:]\s*(.+)$", scope)
                expected = match.group(1).strip() if match else ""
                if not expected or action.text != expected:
                    raise PolicyViolation("message text differs from the user-confirmed original")
        if mode == TaskMode.READ_ONLY_QUERY and capability not in _READ_ONLY_ALLOWED:
            if capability == ActionCapability.ENTER_TEXT and self._is_scoped_search_input(state, action):
                return
            raise PolicyViolation(f"read-only task cannot execute capability {capability.value}")

    @staticmethod
    def _is_scoped_search_input(state: TaskState, action: Action) -> bool:
        """Allow ephemeral query text without weakening read-only semantics.

        The text must be copied from the user's goal and the goal must
        explicitly request a search. This does not authorize form filling,
        messages, settings changes, or persistent writes.
        """
        if action.action != ActionType.TYPE or not action.text:
            return False
        goal = re.sub(r"\s+", "", state.goal)
        text = re.sub(r"\s+", "", action.text)
        has_search_intent = any(marker in goal for marker in ("搜索", "搜一下", "搜", "查找", "找一家", "找一个"))
        return has_search_intent and 0 < len(text) <= 100 and text in goal

    @staticmethod
    def _may_focus_text_input(action: Action) -> bool:
        description = re.sub(r"\s+", "", action.reason).casefold()
        markers = (
            "搜索框", "输入框", "地址栏", "搜索栏", "文本框", "编辑框",
            "searchbox", "searchbar", "inputfield", "addressbar", "textfield",
        )
        return any(marker in description for marker in markers)


def authorize_task_start(state: TaskState) -> None:
    """Fail before app resolution/display creation for sensitive missions."""
    if (
        state.task_spec
        and state.task_spec.task_mode == TaskMode.SENSITIVE_TASK
        and state.task_spec.constraints.get("user_confirmed_sensitive_action") is not True
    ):
        raise PolicyViolation("sensitive task requires explicit human takeover before app launch")
