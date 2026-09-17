"""Cross-layer failure contract shared by Agent policy and runtime adapters."""


class BearBlessError(RuntimeError):
    pass


class AgentTerminalDecision(BearBlessError):
    """A deliberate non-retryable stop proposed by the planner."""

    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        super().__init__(f"{kind}: {reason}")


class RecoverableExecutionError(BearBlessError):
    """A safe retry or replan may be attempted within budget."""


class ModelProtocolError(BearBlessError):
    """The model response could not be normalized into the action contract.

    This is not evidence that the phone navigation failed, so it has a small
    budget separate from execution replans.
    """


class GuardViolation(RecoverableExecutionError):
    pass


class PolicyViolation(BearBlessError):
    """A terminal mismatch between mission permissions and an action."""


class IsolationViolation(BearBlessError):
    """A terminal loss of isolation; never replan on the live display."""


class DeviceUnavailable(BearBlessError):
    """The configured Android device is not available for safe execution."""
