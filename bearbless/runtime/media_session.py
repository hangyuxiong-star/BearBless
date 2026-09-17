from __future__ import annotations

from dataclasses import dataclass
import re

from bearbless.agent.state import TaskState
from bearbless.runtime.adb import AdbClient
from bearbless.schemas import VerificationResult


def requested_track(goal: str) -> str | None:
    match = re.search(
        r"(?:播放|搜索)?歌曲(?:名)?\s*(?:[：:]\s*)?[《“\"]?"
        r"([^，,。》”\"；;]+)",
        goal,
    )
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


@dataclass
class MediaSessionCompletionProbe:
    adb: AdbClient

    def verify(self, state: TaskState) -> VerificationResult | None:
        target = requested_track(state.goal)
        if not target or "播放" not in state.goal:
            return None
        result = self.adb.shell("dumpsys", "media_session")
        output = result.stdout if result.ok and isinstance(result.stdout, str) else ""
        playing = "PlaybackState {state=3," in output
        target_visible = target.casefold() in output.casefold()
        if not (playing and target_visible):
            return None
        return VerificationResult(
            passed=True,
            retryable=False,
            reason=f"MediaSession confirms {target} is playing",
            evidence=[{
                "criterion": "target_media_playing",
                "passed": True,
                "evidence": f"Android MediaSession: state=3, title contains {target}",
            }],
        )
