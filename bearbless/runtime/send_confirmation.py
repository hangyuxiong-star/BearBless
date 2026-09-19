from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import time
from typing import Callable

from bearbless.agent.state import TaskState
from bearbless.errors import PolicyViolation
from bearbless.runtime.actions import Action, ActionCapability
from bearbless.runtime.input_backend import AccessibilityTextBridge
from bearbless.message_intent import extract_confirmed_message


def confirmation_details(state: TaskState) -> tuple[str, str]:
    """Extract a conservative notification summary from the authorized scope."""
    scope = str((state.task_spec.constraints if state.task_spec else {}).get("sensitive_scope") or state.goal)
    recipient_match = re.search(r"(?:给|告诉)[‘'\"“]?([^，,：:\s]{1,40})[’'\"”]?(?:发|说|，|,)", scope)
    recipient = recipient_match.group(1) if recipient_match else "指定联系人"
    message = extract_confirmed_message(scope) or scope
    return recipient, message


@dataclass
class NotificationSensitiveConfirmer:
    bridge: AccessibilityTextBridge
    timeout_seconds: float = 120.0
    poll_seconds: float = 0.5

    def confirm(
        self,
        state: TaskState,
        action: Action,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        if action.capability != ActionCapability.SENSITIVE:
            return
        approved = state.collected_data.setdefault("approved_sensitive_actions", [])
        signature = self._signature(state, action)
        if signature in approved:
            return
        # The task contract is compiled from the user's current instruction.
        # When that instruction already names the recipient and exact message,
        # asking for a second phone-notification approval both duplicates the
        # authorization and violates the zero-interruption promise.
        constraints = state.task_spec.constraints if state.task_spec else {}
        if constraints.get("user_confirmed_sensitive_action") is True:
            approved.append(signature)
            return
        recipient, message = confirmation_details(state)
        token = f"{state.task_id}-{signature[:12]}"
        self.bridge.request_send_confirmation(token, recipient, message)
        state.collected_data["pending_sensitive_confirmation"] = token
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if should_cancel and should_cancel():
                raise PolicyViolation("sensitive action cancelled by user")
            status = self.bridge.send_confirmation_status(token)
            if status == "approved":
                approved.append(signature)
                state.collected_data.pop("pending_sensitive_confirmation", None)
                return
            if status == "cancelled":
                raise PolicyViolation("sensitive action cancelled from phone notification")
            time.sleep(self.poll_seconds)
        raise PolicyViolation("sensitive action confirmation timed out")

    @staticmethod
    def _signature(state: TaskState, action: Action) -> str:
        raw = "|".join((
            state.task_id,
            action.action.value,
            str(action.display_id),
            str(action.x),
            str(action.y),
            action.reason or "",
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
