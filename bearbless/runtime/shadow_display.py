from __future__ import annotations

import subprocess
import shlex
import time
import re

from bearbless.config import Config
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.parsers import parse_display_ids


class ShadowDisplayError(RuntimeError):
    pass


def parse_resolved_activity(output: str, package: str) -> str | None:
    """Return an explicit component only when it belongs to the target app."""
    for line in reversed(output.splitlines()):
        component = line.strip()
        if component.startswith(f"{package}/"):
            return component
    return None


class DisplayResolver:
    def __init__(self, config: Config, runner: CommandRunner) -> None:
        self.config = config
        self.runner = runner

    def list_ids(self) -> list[int]:
        # Do not start a second scrcpy server while the long-lived shadow
        # display server is connecting or running. On Huawei this races the
        # shared server transport and intermittently aborts the creator with
        # ``Server connection failed``. Android's display service exposes the
        # same ids without allocating another scrcpy transport.
        adb_argv = [self.config.adb_path]
        if self.config.android_serial:
            adb_argv.extend(["-s", self.config.android_serial])
        adb_argv.extend(["shell", "dumpsys", "display"])
        adb_result = self.runner.run(
            adb_argv,
            category="adb",
            timeout=self.config.command_timeout_seconds,
        )
        if adb_result.ok and isinstance(adb_result.stdout, str):
            ids = {
                int(value)
                for pattern in (r"\bmDisplayId=(\d+)", r'\bdisplayId\s+(\d+)"')
                for value in re.findall(pattern, adb_result.stdout)
            }
            if ids:
                return sorted(ids)

        argv = [self.config.scrcpy_path]
        if self.config.android_serial:
            argv.extend(["--serial", self.config.android_serial])
        argv.append("--list-displays")
        last_error = "could not list displays"
        for attempt in range(3):
            result = self.runner.run(argv, category="scrcpy", timeout=self.config.command_timeout_seconds)
            if result.ok:
                assert isinstance(result.stdout, str) and isinstance(result.stderr, str)
                return parse_display_ids(result.stdout + "\n" + result.stderr)
            error = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
            last_error = error.strip() or last_error
            transient = any(marker in last_error.casefold() for marker in (
                "address already in use", "server connection failed", "could not listen on port",
            ))
            if not transient or attempt == 2:
                break
            time.sleep(0.35 * (attempt + 1))
        raise ShadowDisplayError(last_error)

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
            # scrcpy's server owns the permission required to launch onto a
            # Huawei virtual display.  A plain `adb am start --display` is
            # rejected, and reusing QQ's existing process can leave a resumed
            # SplashActivity with no touchable/rendered window.  `+` performs
            # a process cold-start without clearing app data.
            f"--start-app=+{package}",
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
        # A previous scrcpy display can remain visible to Android briefly while
        # it is being torn down. We already own an exact display id and process;
        # requiring global uniqueness turns that harmless overlap into repeated
        # task failure. Validate ownership by presence, never select another id.
        live_ids = self.resolver.list_ids()
        if self.id not in live_ids:
            raise ShadowDisplayError(
                f"owned shadow display {self.id} disappeared; live displays={live_ids}"
            )
        return self.id

    def ensure(self, package: str) -> int:
        """Reuse the long-lived shadow workspace or create it once."""
        if self.id is not None and self._process is not None and self._process.poll() is None:
            display_id = self.resolve_live_id()
            self.launch_app(package)
            return display_id
        if self.id is not None or self._process is not None:
            self.stop()
        return self.start(package)

    def ensure_staged(self, package: str, bootstrap_package: str = "com.android.settings") -> int:
        """Create the display with a native host before launching a WebView app.

        Some Huawei builds produce a permanently white surface when a heavy
        WebView application is used as scrcpy's initial ``--start-app``.  A
        small native Activity establishes the display first; the target is
        then launched onto that already-composed display.
        """
        if self.id is not None and self._process is not None and self._process.poll() is None:
            display_id = self.resolve_live_id()
            self.launch_app(package)
            return display_id
        if self.id is not None or self._process is not None:
            self.stop()
        display_id = self.start(bootstrap_package)
        self.launch_app(package)
        return display_id

    def launch_app(self, package: str, uri: str | None = None) -> None:
        display_id = self.resolve_live_id()
        argv = [self.config.adb_path]
        if self.config.android_serial:
            argv.extend(["-s", self.config.android_serial])
        argv.extend(["shell", "am", "start", "--user", "0", "--display", str(display_id)])
        if uri:
            argv.extend(["-a", "android.intent.action.VIEW", "-d", uri, "-p", package])
        else:
            resolve_argv = [self.config.adb_path]
            if self.config.android_serial:
                resolve_argv.extend(["-s", self.config.android_serial])
            resolve_argv.extend([
                "shell", "cmd", "package", "resolve-activity", "--brief", "--user", "0",
                "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", package,
            ])
            resolved = self.runner.run(
                resolve_argv,
                category="adb",
                timeout=self.config.command_timeout_seconds,
            )
            component = parse_resolved_activity(
                resolved.stdout if resolved.ok and isinstance(resolved.stdout, str) else "",
                package,
            )
            argv.extend(["-n", component] if component else [package])
        result = self.runner.run(argv, category="adb", timeout=self.config.command_timeout_seconds)
        if not result.ok:
            raise ShadowDisplayError("secondary-display app launch failed")

    def launch_share_text(self, package: str, text: str) -> None:
        """Open an app's native text-share flow on the owned shadow display."""
        display_id = self.resolve_live_id()
        argv = [self.config.adb_path]
        if self.config.android_serial:
            argv.extend(["-s", self.config.android_serial])
        argv.extend([
            "shell", "am", "start", "--user", "0", "--display", str(display_id),
            "-a", "android.intent.action.SEND", "-t", "text/plain",
            # Arguments after ``adb shell`` are reconstructed into a remote
            # shell command. Quote free-form text here so spaces do not make
            # the remainder (notably ``-p``) part of the message value.
            "--es", "android.intent.extra.TEXT", shlex.quote(text),
            "-p", package,
        ])
        result = self.runner.run(argv, category="adb", timeout=self.config.command_timeout_seconds)
        if not result.ok:
            raise ShadowDisplayError("secondary-display text share launch failed")

    def set_alarm(self, hour: int, minute: int) -> None:
        """Create an enabled alarm through Android's semantic alarm intent.

        SKIP_UI avoids focusing Huawei Clock's editable alarm-name field,
        which otherwise summons the singleton IME on Display 0.
        """
        display_id = self.resolve_live_id()
        argv = [self.config.adb_path]
        if self.config.android_serial:
            argv.extend(["-s", self.config.android_serial])
        argv.extend([
            "shell", "am", "start", "--user", "0", "--display", str(display_id),
            "-a", "android.intent.action.SET_ALARM",
            "--ei", "android.intent.extra.alarm.HOUR", str(hour),
            "--ei", "android.intent.extra.alarm.MINUTES", str(minute),
            "--ez", "android.intent.extra.alarm.SKIP_UI", "true",
            # Huawei exposes more than one alarm handler.  Leaving the intent
            # implicit can be swallowed by the Activity already resumed on the
            # virtual display (we observed Settings remaining foreground while
            # ``am start`` still returned success).  Bind the semantic intent
            # to the clock package so Android must dispatch HandleSetAlarm.
            "-p", "com.huawei.deskclock",
        ])
        result = self.runner.run(argv, category="adb", timeout=self.config.command_timeout_seconds)
        if not result.ok:
            raise ShadowDisplayError("display-scoped semantic alarm creation failed")

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
