from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bearbless.runtime.commands import CommandRunner


@dataclass(frozen=True)
class MarkedElement:
    element_id: int
    label: str
    bounds: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)


@dataclass(frozen=True)
class GroundedScreen:
    annotated_path: Path
    elements: tuple[MarkedElement, ...]


class SetOfMarkGrounder:
    def __init__(self, runner: CommandRunner, *, max_elements: int = 60) -> None:
        self.runner = runner
        self.max_elements = max_elements
        self.tessdata = Path(__file__).resolve().parents[1] / "assets" / "tessdata"

    def ground(self, frame_path: str | Path) -> GroundedScreen:
        source = Path(frame_path)
        outputs: list[str] = []
        for language in ("chi_sim", "chi_sim+eng"):
            result = self.runner.run(
                [
                    "tesseract", str(source), "stdout", "-l", language,
                    "--tessdata-dir", str(self.tessdata), "--psm", "11",
                    "-c", "tessedit_create_tsv=1",
                ],
                category="ocr-grounding", timeout=30,
            )
            if result.ok and isinstance(result.stdout, str):
                outputs.append(result.stdout)
        if not outputs:
            raise RuntimeError("display-specific OCR grounding failed")
        merged: list[MarkedElement] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()
        for output in outputs:
            for item in self._parse_tsv(output):
                key = (item.label, item.bounds)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(MarkedElement(len(merged) + 1, item.label, item.bounds))
        elements = tuple(merged[: self.max_elements])
        annotated = source.with_name(f"{source.stem}_marked.png")
        self._annotate(source, annotated, elements)
        return GroundedScreen(annotated, elements)

    @staticmethod
    def _parse_tsv(tsv: str) -> list[MarkedElement]:
        lines: dict[tuple[str, ...], dict[str, object]] = {}
        rows = tsv.splitlines()
        for row in rows[1:]:
            columns = row.split("\t", 11)
            if len(columns) != 12:
                continue
            level, page, block, paragraph, line, _word, left, top, width, height, confidence, label = columns
            label = label.strip()
            try:
                score = float(confidence)
                x, y, w, h = int(left), int(top), int(width), int(height)
            except ValueError:
                continue
            if level != "5" or not label or score < 25 or w < 5 or h < 8:
                continue
            key = (page, block, paragraph, line)
            entry = lines.setdefault(key, {"labels": [], "bounds": [x, y, x + w, y + h], "score": score})
            entry["labels"].append(label)  # type: ignore[union-attr]
            bounds = entry["bounds"]  # type: ignore[assignment]
            bounds[0], bounds[1] = min(bounds[0], x), min(bounds[1], y)
            bounds[2], bounds[3] = max(bounds[2], x + w), max(bounds[3], y + h)
        ordered = sorted(lines.values(), key=lambda item: (item["bounds"][1], item["bounds"][0]))  # type: ignore[index]
        output: list[MarkedElement] = []
        for item in ordered:
            label = " ".join(item["labels"])  # type: ignore[arg-type]
            bounds = tuple(item["bounds"])  # type: ignore[arg-type]
            output.append(MarkedElement(len(output) + 1, label, bounds))  # type: ignore[arg-type]
        return output

    @staticmethod
    def _annotate(source: Path, destination: Path, elements: tuple[MarkedElement, ...]) -> None:
        image = Image.open(source).convert("RGB")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=18)
        for element in elements:
            left, top, right, bottom = element.bounds
            draw.rectangle((left, top, right, bottom), outline=(118, 87, 232), width=4)
            badge = str(element.element_id)
            box = draw.textbbox((0, 0), badge, font=font)
            badge_width = box[2] - box[0] + 12
            badge_height = box[3] - box[1] + 8
            badge_top = max(0, top - badge_height)
            draw.rounded_rectangle((left, badge_top, left + badge_width, badge_top + badge_height), radius=5, fill=(118, 87, 232))
            draw.text((left + 6, badge_top + 2), badge, fill="white", font=font)
        temp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        image.save(temp, format="PNG")
        temp.replace(destination)
