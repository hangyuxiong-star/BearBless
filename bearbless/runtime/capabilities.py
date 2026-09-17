from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from bearbless.config import Config
from bearbless.runtime.adb import AdbClient
from bearbless.runtime.commands import CommandError, CommandRunner
from bearbless.runtime.parsers import (
    parse_adb_devices,
    parse_display_ids,
    parse_ime_state,
    parse_packages,
    parse_wm_size,
    supports_display_targeted_input,
)
from bearbless.runtime.shadow_display import ShadowDisplay


def verify_display_id_freshness(first_id: int, second_id: int, live_ids: list[int]) -> bool:
    """Prove a recreated display no longer resolves to its previous ID."""
    return first_id != second_id and first_id not in live_ids and second_id in live_ids


@dataclass
class Check:
    status: str
    detail: Any = None
    error: str | None = None


class Doctor:
    def __init__(self, config: Config, runner: CommandRunner | None = None) -> None:
        self.config = config
        self.runner = runner or CommandRunner()
        self.adb = AdbClient(config, self.runner)

    def _safe(self, fn: Any) -> Check:
        try:
            detail = fn()
            return Check("pass", detail)
        except (CommandError, ValueError, RuntimeError) as exc:
            return Check("unsupported", error=str(exc))

    def _text(self, args: list[str]) -> str:
        result = self.adb.command(args)
        if not result.ok:
            error = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
            raise RuntimeError(error.strip() or f"adb exited {result.returncode}")
        assert isinstance(result.stdout, str)
        return result.stdout.strip()

    def _shell_text(self, *args: str) -> str:
        result = self.adb.shell(*args)
        if not result.ok:
            error = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
            raise RuntimeError(error.strip() or f"adb shell exited {result.returncode}")
        assert isinstance(result.stdout, str)
        return result.stdout.strip()

    def run(self, *, hardware_probes: bool = True) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        checks: dict[str, Check] = {}
        checks["adb_version"] = self._safe(lambda: self._text(["version"]).splitlines()[0])
        devices = self._safe(lambda: parse_adb_devices(self._text(["devices", "-l"])))
        if devices.status == "pass" and not devices.detail:
            devices = Check("fail", [], "no authorized Android device connected")
        checks["connected_devices"] = devices

        if devices.status != "pass":
            checks["hardware_probes"] = Check("skipped", error="requires an authorized device")
        else:
            props = {
                "manufacturer": "ro.product.manufacturer",
                "model": "ro.product.model",
                "android_release": "ro.build.version.release",
                "sdk": "ro.build.version.sdk",
            }
            for name, prop in props.items():
                checks[name] = self._safe(lambda prop=prop: self._shell_text("getprop", prop))
            checks["resolution"] = self._safe(lambda: parse_wm_size(self._shell_text("wm", "size")))
            checks["input_display_targeting"] = self._safe(self._probe_input_display_targeting)
            checks["ime_policy"] = self._safe(lambda: parse_ime_state(self._shell_text("dumpsys", "input_method")))
            checks["candidate_packages"] = self._safe(self._candidate_packages)

        checks["scrcpy_version"] = self._safe(self._scrcpy_version)
        checks["available_displays"] = self._safe(self._list_displays)
        if hardware_probes and devices.status == "pass" and checks["scrcpy_version"].status == "pass":
            checks.update(self._virtual_display_probes())
        else:
            reason = "hardware probes disabled or prerequisites unavailable"
            for name in ("virtual_display_creation", "virtual_display_id", "secondary_app_launch", "observation", "backend_a_freshness"):
                checks[name] = Check("skipped", error=reason)
        checks["clipboard_autosync"] = Check("pass", {"enabled": False, "enforced_by": "--no-clipboard-autosync"})
        return {
            "schema_version": 1,
            "generated_at": now.isoformat(),
            "config": {
                "shadow_size": f"{self.config.shadow_width}x{self.config.shadow_height}",
                "shadow_dpi": self.config.shadow_dpi,
                "android_serial_configured": bool(self.config.android_serial),
            },
            "checks": {name: asdict(check) for name, check in checks.items()},
        }

    def _scrcpy_version(self) -> str:
        result = self.runner.run([self.config.scrcpy_path, "--version"], category="scrcpy", timeout=self.config.command_timeout_seconds)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or "scrcpy version probe failed")
        return result.stdout.splitlines()[0]

    def _list_displays(self) -> list[int]:
        argv = [self.config.scrcpy_path]
        if self.config.android_serial:
            argv.extend(["--serial", self.config.android_serial])
        argv.append("--list-displays")
        result = self.runner.run(argv, category="scrcpy", timeout=self.config.command_timeout_seconds)
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or "scrcpy display listing failed")
        return parse_display_ids(result.stdout + "\n" + result.stderr)

    def _candidate_packages(self) -> list[str]:
        packages = parse_packages(self._shell_text("pm", "list", "packages"))
        needles = ("settings", "browser", "chrome", "note", "notepad")
        return [package for package in packages if any(word in package.lower() for word in needles)][:50]

    def _probe_input_display_targeting(self) -> bool:
        help_text = self._shell_text("input", "--help")
        # Huawei's Android 12 input binary returns "Unknown command" for
        # --help, but prints its usage when invoked without a subcommand.
        if "unknown command" in help_text.lower() or not supports_display_targeted_input(help_text):
            help_text = self._shell_text("input")
        if not supports_display_targeted_input(help_text):
            raise RuntimeError("adb shell input does not advertise display targeting")
        return True

    def _virtual_display_probes(self) -> dict[str, Check]:
        output: dict[str, Check] = {}
        first = ShadowDisplay(self.config, self.runner)
        second = ShadowDisplay(self.config, self.runner)
        try:
            first_id = first.start()
            output["virtual_display_creation"] = Check("pass", True)
            output["virtual_display_id"] = Check("pass", first_id)
            output["secondary_app_launch"] = Check("pass", {"package": "com.android.settings", "display_id": first_id})
            capture = self.adb.shell("screencap", "-d", str(first_id), "-p", binary=True)
            output["observation"] = Check("pass" if capture.ok else "unsupported", {"backend": "adb screencap -d", "display_id": first_id}, None if capture.ok else capture.stderr.strip())
            first.stop()
            second_id = second.start()
            live_ids = self._list_displays()
            stale_rejected = verify_display_id_freshness(first_id, second_id, live_ids)
            output["backend_a_freshness"] = Check(
                "pass" if stale_rejected else "fail",
                {"first_id": first_id, "second_id": second_id, "live_ids": live_ids, "stale_id_rejected": stale_rejected},
                None if stale_rejected else "could not prove that a stale ID is rejected after recreation",
            )
        except (CommandError, RuntimeError, OSError) as exc:
            output.setdefault("virtual_display_creation", Check("unsupported", error=str(exc)))
            for name in ("virtual_display_id", "secondary_app_launch", "observation", "backend_a_freshness"):
                output.setdefault(name, Check("skipped", error="virtual display probe did not complete"))
        finally:
            if first.id is not None:
                first.stop()
            if second.id is not None:
                second.stop()
        return output


def save_report(report: dict[str, Any], root: Path = Path("artifacts/doctor")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{stamp}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
