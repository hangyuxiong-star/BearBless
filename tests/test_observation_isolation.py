from io import BytesIO

import pytest
from PIL import Image

from bearbless.errors import IsolationViolation
from bearbless.runtime.commands import CommandResult
from bearbless.runtime.observation_backend import AdbScreencapBackend


def png(color):
    output = BytesIO()
    Image.new("RGB", (16, 16), color).save(output, format="PNG")
    return output.getvalue()


class Display:
    def resolve_live_id(self):
        return 8


class Adb:
    def __init__(self, shadow, primary):
        self.frames = {8: shadow, 0: primary}

    def shell(self, *args, **kwargs):
        display_id = int(args[2])
        return CommandResult("adb", tuple(args), 0, self.frames[display_id], b"")


def test_capture_rejects_primary_surface_alias():
    same = png("white")
    backend = AdbScreencapBackend(Adb(same, same), Display())  # type: ignore[arg-type]
    with pytest.raises(IsolationViolation, match="Display 0"):
        backend.capture(8)


def test_capture_accepts_distinct_shadow_surface():
    backend = AdbScreencapBackend(Adb(png("purple"), png("white")), Display())  # type: ignore[arg-type]
    assert backend.capture(8).display_id == 8
