from __future__ import annotations

from dataclasses import dataclass
import subprocess
import time
from typing import Sequence


class CommandError(RuntimeError):
    """Base error for an external tool invocation."""


class CommandNotFound(CommandError):
    pass


class CommandTimedOut(CommandError):
    pass


@dataclass(frozen=True)
class CommandResult:
    category: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str | bytes
    stderr: str | bytes

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        category: str,
        timeout: float,
        binary: bool = False,
    ) -> CommandResult:
        safe_argv = tuple(str(part) for part in argv)
        attempts = 2 if category == "adb" and self._retryable_adb_read(safe_argv) else 1
        completed = None
        for attempt in range(attempts):
            try:
                completed = subprocess.run(
                    safe_argv,
                    capture_output=True,
                    text=not binary,
                    timeout=timeout,
                    check=False,
                )
                break
            except FileNotFoundError as exc:
                raise CommandNotFound(f"{category} executable not found: {safe_argv[0]}") from exc
            except subprocess.TimeoutExpired as exc:
                if attempt + 1 >= attempts:
                    raise CommandTimedOut(f"{category} timed out after {timeout}s") from exc
                time.sleep(0.35)
        assert completed is not None
        stdout = completed.stdout
        stderr = completed.stderr
        return CommandResult(category, safe_argv, completed.returncode, stdout, stderr)

    @staticmethod
    def _retryable_adb_read(argv: tuple[str, ...]) -> bool:
        """Whitelist ADB probes that are safe to repeat after a timeout."""
        try:
            adb_index = next(i for i, part in enumerate(argv) if part.endswith("adb"))
        except StopIteration:
            return False
        args = argv[adb_index + 1:]
        if args[:2] and args[0] == "-s":
            args = args[2:]
        if not args:
            return False
        if args[0] in {"get-state", "devices", "start-server"}:
            return True
        if args[0] != "shell" or len(args) < 2:
            return False
        shell_args = args[1:]
        return (
            shell_args[0] in {"dumpsys", "screencap"}
            or shell_args[:2] in {("pm", "path"), ("pm", "list")}
            or shell_args[:3] == ("cmd", "package", "resolve-activity")
            or shell_args[:2] == ("settings", "get")
        )
