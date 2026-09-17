import pytest

from bearbless.errors import GuardViolation
from bearbless.runtime.actions import Action, ActionType
from bearbless.runtime.guard import ConflictGuard
from bearbless.runtime.guarded_executor import GuardedExecutor
from bearbless.runtime.monitor import DeviceMonitor, DeviceState


class Display:
    def __init__(self):
        self.stopped = False
    def resolve_live_id(self):
        return 8
    def stop(self):
        self.stopped = True


class Inputs:
    def __init__(self):
        self.taps = []
    def tap(self, display_id, x, y):
        self.taps.append((display_id, x, y))


class Observer:
    def capture(self, display_id):
        from bearbless.runtime.observation_backend import Frame
        return Frame(display_id, "now", b"\x89PNGfixture")


class Monitor(DeviceMonitor):
    def __init__(self, packages):
        super().__init__(None)
        self.packages = iter(packages)
    def snapshot(self, *, monotonic_time):
        return DeviceState("now", monotonic_time, next(self.packages), ".Main", 8)


def make_executor(packages=("com.human", "com.human")):
    display = Display()
    inputs = Inputs()
    monitor = Monitor(packages)
    executor = GuardedExecutor(
        display, ConflictGuard(display, 100, 200), inputs, Observer(), monitor  # type: ignore[arg-type]
    )
    return executor, display, inputs, monitor


def test_executor_runs_guarded_action_and_records_metrics() -> None:
    executor, _, inputs, monitor = make_executor()
    executor.execute(Action(ActionType.TAP, display_id=8, x=1, y=2))
    assert inputs.taps == [(8, 1, 2)]
    assert monitor.metrics.agent_actions_total == 1
    assert monitor.metrics.agent_actions_targeting_primary_display == 0


def test_executor_blocks_display_zero_before_snapshot_or_dispatch() -> None:
    executor, _, inputs, monitor = make_executor()
    with pytest.raises(GuardViolation):
        executor.execute(Action(ActionType.TAP, display_id=0, x=1, y=2))
    assert inputs.taps == []
    assert monitor.metrics.guard_violations == 1
