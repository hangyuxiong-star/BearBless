from __future__ import annotations

import time
from collections.abc import Callable

from bearbless.config import Config
from bearbless.errors import DeviceUnavailable
from bearbless.runtime.adb import AdbClient


class DeviceSupervisor:
    """Bounded ADB readiness recovery; never replays a phone action."""

    def __init__(self, adb: AdbClient, config: Config, sleeper: Callable[[float], None] = time.sleep) -> None:
        self.adb = adb
        self.config = config
        self.sleeper = sleeper

    def ensure_ready(self) -> None:
        attempts = self.config.device_reconnect_attempts + 1
        for index in range(attempts):
            state = self.adb.command(["get-state"])
            if state.ok and str(state.stdout).strip() == "device":
                return
            if index == 0:
                self.adb.command(["start-server"])
            if index + 1 < attempts:
                self.sleeper(self.config.device_reconnect_delay_seconds)
        raise DeviceUnavailable("DEVICE_LOST: Android device is not available after bounded ADB recovery")
