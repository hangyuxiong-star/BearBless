from pathlib import Path

from bearbless.config import Config
from bearbless.runtime.capabilities import Doctor, save_report, verify_display_id_freshness
from bearbless.runtime.commands import CommandResult


class FakeRunner:
    outputs = {
        ("adb", "version"): "Android Debug Bridge version 1.0.41\n",
        ("adb", "devices", "-l"): "List of devices attached\nSERIAL device product:test\n",
        ("adb", "shell", "getprop", "ro.product.manufacturer"): "HUAWEI\n",
        ("adb", "shell", "getprop", "ro.product.model"): "ELS-AN00\n",
        ("adb", "shell", "getprop", "ro.build.version.release"): "12\n",
        ("adb", "shell", "getprop", "ro.build.version.sdk"): "31\n",
        ("adb", "shell", "wm", "size"): "Physical size: 1080x2340\n",
        ("adb", "shell", "input", "--help"): "Usage: input [-d DISPLAY_ID] <command>\n",
        ("adb", "shell", "dumpsys", "input_method"): "mCurTokenDisplayId=0 mCurToken=abc\n",
        ("adb", "shell", "pm", "list", "packages"): "package:com.android.settings\npackage:com.android.browser\n",
        ("scrcpy", "--version"): "scrcpy 4.1\n",
        ("scrcpy", "--list-displays"): "Display 0: 1080x2340\n",
    }

    def run(self, argv, *, category, timeout, binary=False):
        key = tuple(argv)
        return CommandResult(category, key, 0, self.outputs[key], "")


def test_doctor_without_mutating_hardware(tmp_path: Path) -> None:
    report = Doctor(Config(), FakeRunner()).run(hardware_probes=False)
    assert report["checks"]["model"]["detail"] == "ELS-AN00"
    assert report["checks"]["input_display_targeting"]["detail"] is True
    assert report["checks"]["virtual_display_creation"]["status"] == "skipped"
    assert save_report(report, tmp_path).exists()


def test_backend_a_rejects_stale_display_id() -> None:
    assert verify_display_id_freshness(7, 8, [0, 8])
    assert not verify_display_id_freshness(7, 7, [0, 7])
    assert not verify_display_id_freshness(7, 8, [0, 7, 8])


def test_huawei_input_help_falls_back_to_bare_command() -> None:
    runner = FakeRunner()
    runner.outputs = dict(runner.outputs)
    runner.outputs[("adb", "shell", "input", "--help")] = "Unknown command: --help\n"
    runner.outputs[("adb", "shell", "input")] = "Usage: input [<source>] [-d DISPLAY_ID] <command>\n"
    report = Doctor(Config(), runner).run(hardware_probes=False)
    assert report["checks"]["input_display_targeting"]["detail"] is True
