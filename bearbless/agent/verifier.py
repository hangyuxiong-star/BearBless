from __future__ import annotations

from dataclasses import dataclass

from bearbless.agent.state import TaskState
from bearbless.schemas import Observation, VerificationResult
from bearbless.runtime.actions import Action
from bearbless.runtime.adb import AdbClient


class ExpectedOutcomeVerifier:
    """Deterministic post-action verifier for structured expected outcomes."""

    def verify_step(self, previous: Observation | None, action: Action, current: Observation) -> VerificationResult:
        del previous
        expected = action.expected_outcome
        if expected is None:
            return VerificationResult(passed=True, evidence=[{"criterion": "no expected outcome declared"}])
        missing: list[str] = []
        visible = "\n".join(current.visible_text).casefold()
        if expected.package and current.package != expected.package:
            missing.append(f"package={expected.package}")
        for text in expected.required_text:
            if text.casefold() not in visible:
                missing.append(f"required_text={text}")
        for text in expected.forbidden_text:
            if text.casefold() in visible:
                missing.append(f"forbidden_text_absent={text}")
        return VerificationResult(
            passed=not missing,
            retryable=bool(missing),
            evidence=[{
                "criterion": expected.description,
                "passed": not missing,
                "evidence": current.frame_path,
                "visible_text_sample": current.visible_text[:20],
            }],
            missing=missing,
            reason="" if not missing else "expected outcome not observed",
        )


@dataclass(frozen=True)
class EvidenceVerifier:
    """Deterministic verifier requiring named evidence produced by observers."""

    required_criteria: tuple[str, ...]

    def verify(self, state: TaskState) -> VerificationResult:
        by_criterion = {str(item.get("criterion")): item for item in state.evidence}
        evidence: list[dict[str, object]] = []
        missing: list[str] = []
        for criterion in self.required_criteria:
            item = by_criterion.get(criterion)
            passed = bool(item and item.get("passed"))
            evidence.append({"criterion": criterion, "passed": passed, "evidence": (item or {}).get("evidence")})
            if not passed:
                missing.append(criterion)
        return VerificationResult(
            passed=not missing,
            retryable=bool(missing),
            evidence=evidence,
            missing=missing,
            reason="" if not missing else f"missing evidence: {', '.join(missing)}",
        )


@dataclass(frozen=True)
class SystemAlarmVerifier:
    """Verify an alarm from Android system state, not a fragile UI frame.

    Alarm creation is a side effect and must never be replayed merely because
    OCR missed the clock screen.  ``retryable`` is therefore deliberately
    false: callers either have durable system evidence or fail closed.
    """

    adb: AdbClient
    hour: int
    minute: int

    def verify(self, state: TaskState) -> VerificationResult:
        del state
        formatted = self.adb.shell("settings", "get", "system", "next_alarm_formatted")
        alarms = self.adb.shell("dumpsys", "alarm")
        formatted_text = formatted.stdout if formatted.ok and isinstance(formatted.stdout, str) else ""
        alarm_text = alarms.stdout if alarms.ok and isinstance(alarms.stdout, str) else ""
        hour12 = self.hour % 12 or 12
        minute = f"{self.minute:02d}"
        localized = f"{hour12}:{minute}"
        canonical = f"{self.hour:02d}:{minute}:00"
        clock_lines = [
            line.strip() for line in alarm_text.splitlines()
            if ("deskclock" in line.casefold() or "register time" in line.casefold())
            and (canonical in line or localized in line)
        ]
        passed = localized in formatted_text or bool(clock_lines)
        evidence = {
            "criterion": "Alarm saved",
            "passed": passed,
            "source": "android_system_alarm_state",
            "target": f"{self.hour:02d}:{minute}",
            "next_alarm": formatted_text.strip(),
            "matching_records": clock_lines[:5],
        }
        return VerificationResult(
            passed=passed,
            retryable=False,
            evidence=[evidence],
            missing=[] if passed else [f"system alarm {self.hour:02d}:{minute}"],
            reason=(
                f"Android system confirms alarm {self.hour:02d}:{minute}"
                if passed else f"Android system does not confirm alarm {self.hour:02d}:{minute}"
            ),
        )
