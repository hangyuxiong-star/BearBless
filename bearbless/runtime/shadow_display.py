from __future__ import annotations

import subprocess
import time

from bearbless.config import Config
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.parsers import parse_display_ids


class ShadowDisplayError(RuntimeError):
    pass


class DisplayResolver:
    def __init__(self, config: Config, runner: CommandRunner) -> None:
        self.config = config
        self.runner = runner

    def list_ids(self) -> list[int]:
        argv = [self.config.scrcpy_path]
        if self.config.android_serial:
            argv.extend(["--serial", self.config.android_serial])
        argv.append("--list-displays")
        result = self.runner.run(argv, category="scrcpy", timeout=self.config.command_timeout_seconds)
        if not result.ok:
            error = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
            raise ShadowDisplayError(error.strip() or "could not list displays")
        assert isinstance(result.stdout, str) and isinstance(result.stderr, str)
        return parse_display_ids(result.stdout + "\n" + result.stderr)

    def resolve_unique_shadow(self) -> int:
        shadow_ids = [display_id for display_id in self.list_ids() if display_id != 0]
        if len(shadow_ids) != 1:
            raise ShadowDisplayError(f"expected exactly one shadow display, found {shadow_ids}")
        return shadow_ids[0]


class ShadowDisplay:
    def __init__(self, config: Config, runner: CommandRunner | None = None) -> None:
        self.config = config
        self.runner = runner or CommandRunner()
        self.resolver = DisplayResolver(config, self.runner)
        self.id: int | None = None
        self._process: subprocess.Popen[str] | None = None
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def start(self, package: str = "com.android.settings") -> int:
        if self._process is not None:
            raise ShadowDisplayError("shadow display is already running")
        before = set(self.resolver.list_ids())
        argv = [self.config.scrcpy_path]
        if self.config.android_serial:
            argv.extend(["--serial", self.config.android_serial])
        argv.extend([
            f"--new-display={self.config.shadow_width}x{self.config.shadow_height}/{self.config.shadow_dpi}",
            f"--start-app={package}",
            # Do not request a display IME policy here. Huawei Android 12
            # rejects policy changes for this untrusted virtual display and
            # scrcpy exits before the workspace is created. Text entry is
            # routed through the display-scoped Accessibility Bridge instead.
            "--keyboard=uhid",
            "--no-clipboard-autosync",
            "--no-audio",
            "--window-title=BearBless Shadow Display",
        ])
        self._process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + self.config.display_start_timeout_seconds
        try:
            while time.monotonic() < deadline:
                created = sorted(set(self.resolver.list_ids()) - before - {0})
                if len(created) == 1:
                    self.id = created[0]
                    self._generation += 1
                    return self.id
                if self._process.poll() is not None:
                    _, stderr = self._process.communicate(timeout=1)
                    raise ShadowDisplayError(stderr.strip() or "scrcpy exited before display creation")
                time.sleep(0.25)
        except Exception:
            self.stop()
            raise
        self.stop()
        raise ShadowDisplayError("timed out waiting for a unique shadow display")

    def resolve_live_id(self) -> int:
        if self.id is None or self._process is None or self._process.poll() is not None:
            raise ShadowDisplayError("shadow display is not running")
        live_id = self.resolver.resolve_unique_shadow()
        if live_id != self.id:
            raise ShadowDisplayError(f"display ID changed from {self.id} to {live_id}; refusing operation")
        return live_id

    def ensure(self, package: str) -> int:
        """Reuse the long-lived shadow workspace or create it once."""
        if self.id is not None and self._process is not None and self._process.poll() is None:
            display_id = self.resolve_live_id()
            self.launch_app(package)
            return display_id
        if self.id is not None or self._process is not None:
            self.stop()
        return self.start(package)

    def launch_app(self, package: str, uri: str | None = None) -> None:
        display_id = self.resolve_live_id()
        argv = [self.config.adb_path]
        if self.config.android_serial:
            argv.extend(["-s", self.config.android_serial])
        argv.extend(["shell", "am", "start", "--display", str(display_id)])
        if uri:
            argv.extend(["-a", "android.intent.action.VIEW", "-d", uri, package])
        else:
            argv.append(package)
        result = self.runner.run(argv, category="adb", timeout=self.config.command_timeout_seconds)
        if not result.ok:
            raise ShadowDisplayError("secondary-display app launch failed")

    def stop(self) -> None:
        process, old_id = self._process, self.id
        self._process = None
        self.id = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if old_id is not None and old_id in self.resolver.list_ids():
            raise ShadowDisplayError(f"display {old_id} still exists after cleanup")

    def __enter__(self) -> "ShadowDisplay":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
