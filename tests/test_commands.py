import subprocess
from unittest.mock import patch

import pytest

from bearbless.runtime.commands import CommandRunner, CommandTimedOut


def test_command_result_keeps_category() -> None:
    completed = subprocess.CompletedProcess(["adb", "version"], 0, "ok", "")
    with patch("subprocess.run", return_value=completed):
        result = CommandRunner().run(["adb", "version"], category="adb", timeout=1)
    assert result.ok
    assert result.category == "adb"


def test_timeout_becomes_typed_error() -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["adb"], 1)):
        with pytest.raises(CommandTimedOut):
            CommandRunner().run(["adb"], category="adb", timeout=1)
