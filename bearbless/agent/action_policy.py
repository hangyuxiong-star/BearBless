from __future__ import annotations

import re

from bearbless.message_intent import (
    extract_confirmed_message,
    extract_confirmed_recipient,
    is_qq_draft_request,
    require_explicit_qq_recipient,
)
from bearbless.config import Config

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
    ActionCapability.READ,
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
        draft_only = is_qq_draft_request(state.goal)
        if draft_only and capability == ActionCapability.SENSITIVE:
            raise PolicyViolation("draft-only QQ task forbids the final send action")
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
            if "qq" in scope.casefold():
                try:
                    require_explicit_qq_recipient(scope, Config.load().qq_test_recipient)
                except ValueError as exc:
                    raise PolicyViolation(str(exc)) from exc
            expected_message = extract_confirmed_message(scope)
            message_already_staged = any(
                item.get("action") in {ActionType.TYPE.value, ActionType.TYPE_BOTTOM.value}
                and item.get("text") == expected_message
                for item in state.action_history
            )
            if (
                message_already_staged
                and action.action in {ActionType.TAP, ActionType.CONDITIONAL_TAP, ActionType.CLICK_TEXT}
                and capability != ActionCapability.SENSITIVE
            ):
                raise PolicyViolation(
                    "after the confirmed message is staged, the next tap must be the sensitive final send"
                )
            if capability == ActionCapability.SENSITIVE and "发送" not in (action.reason or ""):
                raise PolicyViolation("sensitive capability is reserved for the explicit final send action")
            if capability == ActionCapability.ENTER_TEXT:
                expected = expected_message
                recipient_match = re.search(
                    r"(?:给|告诉)[‘'\"“]?([^，,：:\s]{1,40})[’'\"”]?(?:发|说|，|,)",
                    scope,
                )
                recipient = recipient_match.group(1) if recipient_match else ""
                if not expected or action.text not in {expected, recipient}:
                    raise PolicyViolation("message text differs from the user-confirmed original")
                if action.text == expected and message_already_staged:
                    raise PolicyViolation("confirmed message has already been staged once")
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
    if state.task_spec and state.task_spec.task_mode == TaskMode.SENSITIVE_TASK:
        scope = str(state.task_spec.constraints.get("sensitive_scope") or state.goal)
        if "qq" in scope.casefold():
            try:
                require_explicit_qq_recipient(scope, Config.load().qq_test_recipient)
            except ValueError as exc:
                raise PolicyViolation(str(exc)) from exc
