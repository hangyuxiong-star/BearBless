from __future__ import annotations

from pathlib import Path
import tempfile

from bearbless.runtime.actions import Action
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.observation_backend import Frame
from bearbless.schemas import Observation


class TesseractObservationBuilder:
    def __init__(self, runner: CommandRunner, executable: str = "tesseract") -> None:
        self.runner = runner
        self.executable = executable

    def build(self, frame: Frame, action: Action) -> Observation:
        with tempfile.TemporaryDirectory(prefix="bearbless-ocr-") as temp_dir:
            image_path = Path(temp_dir) / "frame.png"
            image_path.write_bytes(frame.png)
            result = self.runner.run(
                [self.executable, str(image_path), "stdout", "-l", "eng"],
                category="ocr",
                timeout=20,
            )
        text = result.stdout if result.ok and isinstance(result.stdout, str) else ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return Observation(
            display_id=frame.display_id,
            captured_at=frame.captured_at,
            package=action.package if action.action.value == "OPEN_APP" else None,
            visible_text=lines,
        )
