from __future__ import annotations

import uuid
import time

from bearbless.agent.planner import ManualPlanner
from bearbless.agent.action_policy import authorize_task_start
from bearbless.agent.model_client import build_phone_model
from bearbless.agent.vision import VisionPlanner, VisionVerifier
from bearbless.agent.state import TaskState
from bearbless.agent.skills import SkillRegistry
from bearbless.agent.verifier import EvidenceVerifier
from bearbless.composition import RuntimeBundle
from bearbless.config import Config
from bearbless.runtime.adb import AdbClient
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.device_supervisor import DeviceSupervisor
from bearbless.runtime.actions import Action, ActionType
from bearbless.runtime.packages import list_installed_packages, resolve_browser_package, resolve_explicit_app_alias
from bearbless.runtime.user_attention import UserAttentionNotifier, login_takeover_required
from bearbless.runtime.monitor import Event
from bearbless.schemas import ExpectedOutcome, SuccessCriterion, TaskSpec
from datetime import datetime, timezone


DEFAULT_ROUTE_URL = "https://www.dsb.dk/find-produkter-og-services/dsb-udland/tyskland/hamborg/"


def run_general_device_task(goal: str, task_spec: TaskSpec | None = None) -> TaskState:
    task_id = f"agent-{uuid.uuid4().hex[:10]}"
    config = Config.load()
    if config.phone_model_provider != "ollama" and not (
        task_spec and task_spec.constraints.get("cloud_vision_consent") is True
    ):
        raise PermissionError("云端视觉模型需要用户明确同意上传 Agent 虚拟屏截图")
    # Resolve the target before creating run artifacts. A package-resolution
    # failure must not leave an empty run that the dashboard mistakes for an
    # active task.
    bootstrap_adb = AdbClient(config, CommandRunner())
    DeviceSupervisor(bootstrap_adb, config).ensure_ready()
    state = TaskState(
        task_id,
        goal,
        task_spec=task_spec,
        max_steps=40,
        # App creation happens once outside the reactive loop and is never
        # replayed. This budget is only for gentle shadow-screen replanning.
        max_replans=3,
        max_sensitive_retries=0,
        interruption_budget=0,
    )
    authorize_task_start(state)
    client = build_phone_model(config)
    package = resolve_explicit_app_alias(goal, bootstrap_adb)
    if package is None:
        package = client.select_package(goal, list_installed_packages(bootstrap_adb))
    bundle = RuntimeBundle(config, task_id)
    allowed_packages = {"任务目标应用": package}
    try:
        primary_before = bundle.monitor.snapshot(monotonic_time=time.monotonic())
        display_id = bundle.display.ensure(package)
        state.shadow_display_id = display_id
        primary_after = bundle.monitor.snapshot(monotonic_time=time.monotonic())
        if primary_after.primary_package == package:
            bundle.monitor.metrics.agent_actions_targeting_primary_display += 1
            bundle.monitor.metrics.primary_display_agent_package_leaks += 1
            bundle.monitor.metrics.isolation_violations += 1
            bundle.monitor.metrics.task_status = "FAILED"
            bundle.monitor.record(Event(
                datetime.now(timezone.utc).isoformat(),
                "system",
                0,
                "startup_isolation_violation",
                {
                    "before": primary_before.primary_package,
                    "after": primary_after.primary_package,
                    "target": package,
                },
                "blocked",
            ))
            bundle.store.save_metrics(bundle.monitor.metrics)
            raise RuntimeError(
                "target app is foreground on Display 0 after shadow launch; refusing execution"
            )
        notifier = UserAttentionNotifier(bundle.adb)

        def notify_takeover(kind: str) -> None:
            if kind != "verification":
                return
            notified = notifier.notify_verification_required()
            bundle.monitor.metrics.user_attention_notifications += int(notified)
            bundle.monitor.metrics.human_verification_takeovers += 1
            bundle.monitor.record(Event(
                datetime.now(timezone.utc).isoformat(), "system", 0,
                "user_attention_notification", {"reason": "human_verification"},
                "posted" if notified else "failed",
            ))

        skills = SkillRegistry(bundle.adb).resolve(goal)
        state.collected_data["active_skills"] = [skill.name for skill in skills]
        planner = VisionPlanner(
            client,
            display_id,
            allowed_packages,
            takeover_notifier=notify_takeover,
            skill_instructions=tuple(skill.instruction for skill in skills),
        )
        result = bundle.agent(planner, VisionVerifier(client), goal).run(state)
        if login_takeover_required(result.failure_reason):
            notified = notifier.notify_login_required()
            bundle.monitor.metrics.user_attention_notifications += int(notified)
            bundle.monitor.record(Event(
                datetime.now(timezone.utc).isoformat(),
                "system",
                0,
                "user_attention_notification",
                {"reason": "login_required"},
                "posted" if notified else "failed",
            ))
        bundle.monitor.metrics.task_status = result.status.value
        bundle.monitor.metrics.replans = result.replans
        bundle.monitor.metrics.verification_attempts = len(result.verification_history)
        for name, value in getattr(client, "usage_metrics", lambda: {})().items():
            if hasattr(bundle.monitor.metrics, name):
                setattr(bundle.monitor.metrics, name, value)
        bundle.store.save_metrics(bundle.monitor.metrics)
        return result
    finally:
        # The shadow workspace is intentionally long-lived. Destroying a
        # focused virtual display can reparent/restart its Activity on Display
        # 0 (observed on Huawei/NetEase) and interrupts ongoing media playback.
        pass


def run_live_browser_task(goal: str, url: str = DEFAULT_ROUTE_URL, task_spec: TaskSpec | None = None) -> TaskState:
    task_id = f"live-{uuid.uuid4().hex[:10]}"
    config = Config.load()
    bundle = RuntimeBundle(config, task_id)
    bundle.device_supervisor.ensure_ready()
    browser = resolve_browser_package(config, bundle.adb)
    criterion = "Copenhagen-Hamburg route page visible"
    state = TaskState(
        task_id,
        goal,
        # Live GUI work fails closed on the first uncertain observation. Repeating
        # an app launch can itself disturb Display 0 on ROMs that ignore --display.
        max_replans=0,
        task_spec=task_spec or TaskSpec(
            goal=goal,
            constraints={"no_purchase": True, "display_0_forbidden": True, "source_url": url},
            allowed_actions=["open official route page", "read route information"],
            success_criteria=[SuccessCriterion(name=criterion, description="The DSB route page is visible on the shadow display")],
            forbidden_actions=["purchase", "payment", "message sending", "Display 0 input"],
            interruption_budget=0,
        ),
    )
    try:
        display_id = bundle.display.start(browser)
        state.shadow_display_id = display_id
        plan = ManualPlanner((
            Action(ActionType.OPEN_APP, display_id=display_id, package=browser, uri=url, reason="Open the fixed official DSB route page"),
            Action(ActionType.WAIT, seconds=1, reason="Allow the browser window to appear"),
            Action(ActionType.OBSERVE, reason="Capture the initial browser state"),
            Action(ActionType.WAIT, seconds=2, reason="Allow the route page to begin rendering"),
            Action(ActionType.OBSERVE, reason="Capture the page loading state"),
            Action(
                ActionType.CONDITIONAL_TAP,
                display_id=display_id,
                x=300,
                y=1535,
                condition_text=("cookies", "persondata"),
                reason="Reject DSB cookies only when the consent dialog is OCR-confirmed",
            ),
            Action(ActionType.WAIT, seconds=3, reason="Allow the route content to finish rendering"),
            Action(
                ActionType.OBSERVE,
                reason="Capture and OCR the completed shadow route page",
                expected_outcome=ExpectedOutcome(description=criterion, required_text=["Hamb"]),
            ),
            Action(ActionType.FINISH, reason="Send browser-stage evidence to the final verifier"),
        ))
        result = bundle.agent(plan, EvidenceVerifier((criterion,)), goal).run(state)
        bundle.monitor.metrics.task_status = result.status.value
        bundle.monitor.metrics.replans = result.replans
        bundle.monitor.metrics.verification_attempts = len(result.verification_history)
        bundle.store.save_metrics(bundle.monitor.metrics)
        return result
    finally:
        if bundle.display.id is not None:
            bundle.display.stop()
