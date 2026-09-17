from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from bearbless.config import Config
from bearbless.runtime.adb import AdbClient
from bearbless.runtime.input_backend import AdbDisplayInputBackend
from bearbless.runtime.observation_backend import AdbScreencapBackend
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.shadow_display import ShadowDisplay


@dataclass
class Step:
    number: int
    name: str
    status: str
    detail: object = None


class ShadowTest:
    def __init__(self, config: Config, runner: CommandRunner | None = None) -> None:
        self.config = config
        self.runner = runner or CommandRunner()
        self.adb = AdbClient(config, self.runner)
        self.display = ShadowDisplay(config, self.runner)
        self.input = AdbDisplayInputBackend(self.adb, self.display)
        self.observer = AdbScreencapBackend(self.adb, self.display)

    def run(self, artifact_root: Path = Path("artifacts/shadow-test")) -> dict[str, object]:
        steps: list[Step] = []
        display_id: int | None = None
        artifact_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        frame_path = artifact_root / f"{stamp}-shadow.png"
        try:
            display_id = self.display.start()
            steps.append(Step(1, "create virtual display via scrcpy", "pass"))
            steps.append(Step(2, "parse actual display ID", "pass", display_id))

            time.sleep(1)
            self.display.resolve_live_id()
            steps.append(Step(3, "launch safe app on shadow display", "pass", "com.android.settings"))

            frame = self.observer.capture(display_id)
            frame_path.write_bytes(frame.png)
            steps.append(Step(4, "obtain shadow-display frame", "pass", str(frame_path)))

            self.input.tap(display_id, 600, 500)
            steps.append(Step(5, "display-targeted test tap", "pass", {"display_id": display_id, "x": 600, "y": 500}))
            self.input.swipe(display_id, 600, 1800, 600, 900, 350)
            steps.append(Step(6, "display-targeted test swipe", "pass", {"display_id": display_id}))
        except Exception as exc:
            next_number = len(steps) + 1
            names = [
                "create virtual display via scrcpy",
                "parse actual display ID",
                "launch safe app on shadow display",
                "obtain shadow-display frame",
                "display-targeted test tap",
                "display-targeted test swipe",
            ]
            if next_number <= 6:
                steps.append(Step(next_number, names[next_number - 1], "fail", str(exc)))
        finally:
            self.display.stop()
            closed = display_id is None or display_id not in self.display.resolver.list_ids()
            steps.append(Step(7, "cleanly close virtual display", "pass" if closed else "fail", display_id))

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "display_id": display_id,
            "steps": [asdict(step) for step in steps],
            "passed": len(steps) == 7 and all(step.status == "pass" for step in steps),
        }
        report_path = artifact_root / f"{stamp}.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        report["report_path"] = str(report_path)
        return report
