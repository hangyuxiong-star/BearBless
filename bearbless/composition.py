from __future__ import annotations

from pathlib import Path
from typing import Callable

from bearbless.agent.contracts import Planner, Verifier
from bearbless.agent.action_policy import TaskActionPolicy
from bearbless.agent.loop import AgentLoop
from bearbless.agent.ocr_observer import TesseractObservationBuilder
from bearbless.agent.trace import TaskTrace
from bearbless.agent.verifier import ExpectedOutcomeVerifier
from bearbless.config import Config
from bearbless.runtime.adb import AdbClient
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.device_supervisor import DeviceSupervisor
from bearbless.runtime.guard import ConflictGuard
from bearbless.runtime.guarded_executor import GuardedExecutor
from bearbless.runtime.input_backend import AccessibilityTextBridge, AdbDisplayInputBackend
from bearbless.runtime.monitor import DeviceMonitor, EventStore
from bearbless.agent.skills import SkillRegistry
from bearbless.runtime.observation_backend import AdbScreencapBackend
from bearbless.runtime.shadow_display import ShadowDisplay
from bearbless.runtime.send_confirmation import NotificationSensitiveConfirmer


_shared_runner: CommandRunner | None = None
_shared_display: ShadowDisplay | None = None


def shared_shadow_display(config: Config) -> tuple[CommandRunner, ShadowDisplay]:
    global _shared_runner, _shared_display
    if _shared_runner is None or _shared_display is None:
        _shared_runner = CommandRunner()
        _shared_display = ShadowDisplay(config, _shared_runner)
    return _shared_runner, _shared_display


class RuntimeBundle:
    """Composition root: the only place that wires concrete runtime adapters."""

    def __init__(self, config: Config, task_id: str) -> None:
        self.runner, self.display = shared_shadow_display(config)
        self.adb = AdbClient(config, self.runner)
        self.device_supervisor = DeviceSupervisor(self.adb, config)
        self.store = EventStore(Path("artifacts/runs") / task_id)
        self.monitor = DeviceMonitor(self.adb, self.store)
        guard = ConflictGuard(self.display, config.shadow_width, config.shadow_height)
        text_bridge = AccessibilityTextBridge(self.adb, config.accessibility_bridge_authority)
        inputs = AdbDisplayInputBackend(self.adb, self.display, text_bridge)
        observer = AdbScreencapBackend(self.adb, self.display)
        self.observation_builder = TesseractObservationBuilder(self.runner)
        self.executor = GuardedExecutor(self.display, guard, inputs, observer, self.monitor, self.observation_builder)
        self.sensitive_confirmer = NotificationSensitiveConfirmer(text_bridge)
        self.trace = TaskTrace(Path("artifacts/runs"), task_id)

    def agent(
        self,
        planner: Planner,
        verifier: Verifier,
        goal: str = "",
        should_cancel: Callable[[], bool] | None = None,
    ) -> AgentLoop:
        skills = SkillRegistry(self.adb).resolve(goal)
        return AgentLoop(
            planner,
            self.executor,
            verifier,
            self.trace,
            step_verifier=ExpectedOutcomeVerifier(),
            observation_builder=self.observation_builder,
            action_policy=TaskActionPolicy(),
            completion_probe=SkillRegistry(self.adb).completion_probe(skills),
            should_cancel=should_cancel,
            sensitive_confirmer=self.sensitive_confirmer,
        )
