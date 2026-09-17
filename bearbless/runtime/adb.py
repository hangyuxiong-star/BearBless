from __future__ import annotations

from collections.abc import Sequence

from bearbless.config import Config
from bearbless.runtime.commands import CommandResult, CommandRunner


class AdbClient:
    def __init__(self, config: Config, runner: CommandRunner) -> None:
        self.config = config
        self.runner = runner

    def command(self, args: Sequence[str], *, timeout: float | None = None, binary: bool = False) -> CommandResult:
        argv = [self.config.adb_path]
        if self.config.android_serial:
            argv.extend(["-s", self.config.android_serial])
        argv.extend(args)
        return self.runner.run(
            argv,
            category="adb",
            timeout=timeout or self.config.command_timeout_seconds,
            binary=binary,
        )

    def shell(self, *args: str, timeout: float | None = None, binary: bool = False) -> CommandResult:
        return self.command(["shell", *args], timeout=timeout, binary=binary)
