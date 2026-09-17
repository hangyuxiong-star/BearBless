from __future__ import annotations

from dataclasses import dataclass
import subprocess
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
        try:
            completed = subprocess.run(
                safe_argv,
                capture_output=True,
                text=not binary,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CommandNotFound(f"{category} executable not found: {safe_argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandTimedOut(f"{category} timed out after {timeout}s") from exc
        stdout = completed.stdout
        stderr = completed.stderr
        return CommandResult(category, safe_argv, completed.returncode, stdout, stderr)
