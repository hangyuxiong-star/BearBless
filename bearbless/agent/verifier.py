from __future__ import annotations

from dataclasses import dataclass

from bearbless.agent.state import TaskState
from bearbless.schemas import Observation, VerificationResult
from bearbless.runtime.actions import Action


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
