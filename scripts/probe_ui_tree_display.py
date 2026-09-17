from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time

from bearbless.config import Config
from bearbless.runtime.adb import AdbClient
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.observation_backend import AdbScreencapBackend
from bearbless.runtime.shadow_display import ShadowDisplay


def text(result) -> str:
    value = result.stdout
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path("artifacts/probes/ui-tree") / stamp
    root.mkdir(parents=True, exist_ok=True)
    config = Config.load()
    runner = CommandRunner()
    adb = AdbClient(config, runner)
    display = ShadowDisplay(config, runner)
    summary: dict[str, object] = {"probe": "uiautomator-display-attribution"}
    before = text(adb.shell("dumpsys", "activity", "activities"))
    (root / "activities_before.txt").write_text(before, encoding="utf-8")
    try:
        display_id = display.start("com.android.settings")
        summary["shadow_display_id"] = display_id
        time.sleep(2)
        frame = AdbScreencapBackend(adb, display).capture(display_id)
        (root / "shadow.png").write_bytes(frame.png)
        dump = adb.shell("uiautomator", "dump", "/sdcard/bearbless-window.xml", timeout=20)
        summary["dump_ok"] = dump.ok
        summary["dump_output"] = text(dump).strip()
        xml = adb.shell("cat", "/sdcard/bearbless-window.xml", timeout=10)
        (root / "window.xml").write_text(text(xml), encoding="utf-8")
    finally:
        if display.id is not None:
            display.stop()
    after = text(adb.shell("dumpsys", "activity", "activities"))
    (root / "activities_after.txt").write_text(after, encoding="utf-8")
    summary["artifact_dir"] = str(root)
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
