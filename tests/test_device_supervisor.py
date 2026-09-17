from dataclasses import replace
from types import SimpleNamespace

import pytest

from bearbless.config import Config
from bearbless.errors import DeviceUnavailable
from bearbless.runtime.device_supervisor import DeviceSupervisor


class FakeAdb:
    def __init__(self, states):
        self.states = iter(states)
        self.commands = []

    def command(self, args):
        self.commands.append(args)
        if args == ["start-server"]:
            return SimpleNamespace(ok=True, stdout="")
        return next(self.states)


def result(ok, stdout=""):
    return SimpleNamespace(ok=ok, stdout=stdout)


def test_device_supervisor_recovers_before_task_without_replaying_actions():
    adb = FakeAdb([result(False), result(True, "device\n")])
    config = replace(Config(), device_reconnect_attempts=1, device_reconnect_delay_seconds=0)
    DeviceSupervisor(adb, config, sleeper=lambda _: None).ensure_ready()
    assert adb.commands == [["get-state"], ["start-server"], ["get-state"]]


def test_device_supervisor_fails_with_typed_device_lost_error():
    adb = FakeAdb([result(False), result(False)])
    config = replace(Config(), device_reconnect_attempts=1, device_reconnect_delay_seconds=0)
    with pytest.raises(DeviceUnavailable, match="DEVICE_LOST"):
        DeviceSupervisor(adb, config, sleeper=lambda _: None).ensure_ready()
