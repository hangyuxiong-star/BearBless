from __future__ import annotations

import uuid
import time
import re
from typing import Callable
from urllib.parse import quote_plus

from bearbless.agent.planner import ManualPlanner
from bearbless.agent.task_profiles import resolve_task_profile
from bearbless.agent.action_policy import authorize_task_start
from bearbless.agent.model_client import build_phone_model
from bearbless.agent.vision import QQMessageVerifier, VisionPlanner, VisionVerifier, _alarm_target
from bearbless.agent.state import TaskState, TaskStatus
from bearbless.agent.skills import SkillRegistry
from bearbless.agent.verifier import EvidenceVerifier, SystemAlarmVerifier
from bearbless.composition import RuntimeBundle
from bearbless.config import Config
from bearbless.runtime.adb import AdbClient
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.device_supervisor import DeviceSupervisor
from bearbless.runtime.actions import Action, ActionCapability, ActionType
from bearbless.runtime.packages import list_installed_packages, resolve_browser_package, resolve_explicit_app_alias
from bearbless.runtime.user_attention import UserAttentionNotifier, login_takeover_required
from bearbless.runtime.monitor import Event, parse_primary_root_task_id
from bearbless.runtime.netease_music import resolve_netease_song_uri
from bearbless.schemas import ExpectedOutcome, SuccessCriterion, TaskMode, TaskSpec
from bearbless.message_intent import (
    extract_confirmed_message,
    is_qq_draft_request,
    is_wolt_to_qq_request,
    require_explicit_qq_recipient,
)
from datetime import datetime, timezone


DEFAULT_ROUTE_URL = "https://www.dsb.dk/find-produkter-og-services/dsb-udland/tyskland/hamborg/"
WOLT_ROUTE_CRITERIA = {
    "wolt_burger_categories": "Wolt Burger results",
    "wolt_chinese_categories": "Wolt Asian/Chinese restaurant results",
    "wolt_japanese_categories": "Wolt Japanese restaurant results",
}


def recover_stale_qq_share_task(adb: AdbClient, primary_activity: str | None) -> bool:
    """Remove only the transient QQ share task left by our last failed launch.

    Normal QQ screens are deliberately excluded because those may belong to
    the user. The dedicated share root task can be removed without stopping
    the user's whole QQ process.
    """
    if not primary_activity or not primary_activity.endswith("QPublicTransFragmentActivity"):
        return False
    activities = adb.shell("dumpsys", "activity", "activities")
    if not activities.ok or not isinstance(activities.stdout, str):
        return False
    task_id = parse_primary_root_task_id(activities.stdout)
    if task_id is None:
        return False
    result = adb.shell("cmd", "activity", "stack", "remove", str(task_id))
    return result.ok


def is_wolt_to_qq_task(goal: str) -> bool:
    """Backward-compatible wrapper around the shared request classifier."""
    return is_wolt_to_qq_request(goal)


def wolt_qq_message(
    restaurant: dict[str, object], *, dinner_invitation: bool = False, invitation_time: str = "晚上"
) -> str:
    """Build the only allowed payload from verified Wolt fields."""
    name = str(restaurant.get("name") or "").strip()
    rating = str(restaurant.get("rating") or "").strip()
    address = str(restaurant.get("address") or "").strip()
    if not all((name, rating, address)):
        raise RuntimeError("Wolt 结果缺少店名、评分或地址，禁止进入 QQ 发送阶段")
    if dinner_invitation:
        return f"{invitation_time}去{name}吃吧，评分{rating}，地址{address}"
    return f"Wolt推荐：{name}，评分{rating}，地址{address}"


def wolt_requested_category(goal: str) -> tuple[str, str]:
    folded = goal.casefold()
    if any(term in folded for term in ("中餐", "中式", "chinese", "asian", "asia", "亚洲")):
        return "中餐店", "中餐店"
    if any(term in folded for term in ("japanese", "japanse", "日料", "日本料理", "日餐")):
        return "日料店", "日料店"
    return "汉堡店", "汉堡店"


def run_wolt_to_qq_task(
    goal: str,
    task_spec: TaskSpec | None,
    should_cancel: Callable[[], bool] | None,
) -> TaskState:
    """Execute verified Wolt research followed by one scoped QQ send."""
    try:
        recipient = require_explicit_qq_recipient(goal, Config.load().qq_test_recipient)
    except ValueError as exc:
        raise PermissionError(str(exc)) from exc
    cloud_consent = bool(task_spec and task_spec.constraints.get("cloud_vision_consent") is True)
    base_constraints = {
        "workspace": "shadow_display",
        "human_priority": True,
        "cloud_vision_consent": cloud_consent,
        "compound_parent_goal": goal,
    }
    category_goal, category_label = wolt_requested_category(goal)
    wolt_goal = f"打开Wolt找一家高评分{category_goal}，告诉我店名、评分和地址，不下单"
    wolt_spec = TaskSpec(
        goal=wolt_goal,
        constraints=base_constraints,
        allowed_actions=["浏览 Wolt 餐厅分类", f"比较{category_label}评分", "读取店名、评分和地址"],
        forbidden_actions=["下单", "加入购物车", "付款", "操作 Display 0", "读取系统剪贴板"],
        success_criteria=[
            SuccessCriterion(name="wolt_verified", description="店名、评分和地址均来自 Wolt 商家页"),
            SuccessCriterion(name="zero_interruption", description="Agent 对用户主屏的操作次数为 0"),
        ],
        interruption_budget=0,
        task_mode=TaskMode.READ_ONLY_QUERY,
    )
    wolt_state = run_general_device_task(wolt_goal, wolt_spec, should_cancel)
    if wolt_state.status != TaskStatus.COMPLETED:
        # Return the child state instead of raising away its task id. The
        # queue and Dashboard can then retain the real Wolt frames and display
        # id while still proving that QQ was never entered.
        wolt_state.failure_reason = (
            f"Wolt 子任务未完成，禁止进入 QQ：{wolt_state.failure_reason or '未知失败'}"
        )
        wolt_state.collected_data["compound_parent_goal"] = goal
        return wolt_state
    restaurant = wolt_state.collected_data.get("wolt_restaurant")
    if not isinstance(restaurant, dict):
        raise RuntimeError("Wolt 子任务没有产生可验证餐厅结果，禁止进入 QQ")
    dinner_invitation = "晚上去这吃" in goal or bool(re.search(r"晚上去.+吃", goal))
    invitation_time = "明天晚上" if "明天晚上" in goal else "晚上"
    message = wolt_qq_message(
        restaurant, dinner_invitation=dinner_invitation, invitation_time=invitation_time
    )
    draft_only = is_qq_draft_request(goal)
    qq_goal = (
        f"打开QQ给{recipient}编辑消息草稿：{message}，不要发送"
        if draft_only
        else f"打开QQ给{recipient}发消息：{message}"
    )
    qq_spec = TaskSpec(
        goal=qq_goal,
        constraints={
            **base_constraints,
            "user_confirmed_sensitive_action": not draft_only,
            "sensitive_scope": qq_goal,
            "compound_wolt_result": dict(restaurant),
        },
        allowed_actions=[
            f"仅为{recipient}准备由 Wolt 验证结果生成的原文草稿"
            if draft_only else
            f"仅向{recipient}发送一次由 Wolt 验证结果生成的原文消息"
        ],
        forbidden_actions=["向其他联系人发送", "改写消息", "发送第二条消息", "操作 Display 0", "读取系统剪贴板"],
        success_criteria=[
            SuccessCriterion(
                name="qq_message_drafted" if draft_only else "qq_message_sent_once",
                description=(
                    f"为{recipient}准备完整 Wolt 结果且未发送"
                    if draft_only else f"仅向{recipient}提交一次完整 Wolt 结果"
                ),
            ),
            SuccessCriterion(name="zero_interruption", description="Agent 对用户主屏的操作次数为 0"),
        ],
        interruption_budget=0,
        task_mode=TaskMode.MUTATING_TASK if draft_only else TaskMode.SENSITIVE_TASK,
    )
    qq_state = run_general_device_task(qq_goal, qq_spec, should_cancel)
    qq_state.collected_data["compound_parent_goal"] = goal
    qq_state.collected_data["compound_wolt_result"] = dict(restaurant)
    qq_state.collected_data["compound_message"] = message
    return qq_state


def resolve_youtube_search_uri(goal: str) -> str | None:
    """Build a search deep link without focusing Android's singleton IME."""
    query = resolve_youtube_search_query(goal)
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}" if query else None


def resolve_youtube_search_query(goal: str) -> str | None:
    match = re.search(r"(?:搜索|搜一下|搜)\s*(?:有关|关于)?\s*(.+)$", goal, re.IGNORECASE)
    if not match:
        return None
    query = re.sub(r"(?:的)?视频\s*$", "", match.group(1)).strip(" ：:,，。")
    return query or None


def requires_netease_exact_song_route(goal: str, package: str) -> bool:
    """Return whether a task may run only through an IME-free song deep link."""
    return (
        package == "com.netease.cloudmusic"
        and "播放" in goal
        and any(name in goal for name in ("网易云", "音乐"))
    )


def requires_staged_shadow_start(package: str) -> bool:
    """Apps known to fail when used as scrcpy's initial virtual-display host."""
    # Wolt's WebView needs a composed native surface first. Huawei Clock is a
    # native app and must be the initial host: this device ignores a later
    # cross-app ``am start --display`` and otherwise leaves Settings visible.
    # QQ can likewise leave its SplashActivity attached but render an empty
    # surface when it is the initial virtual-display host. Bootstrapping with
    # Settings first gives the real inbox Activity a composed display.
    return package in {"com.wolt.android", "com.tencent.mobileqq"}


def run_general_device_task(
    goal: str,
    task_spec: TaskSpec | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TaskState:
    if is_wolt_to_qq_task(goal):
        return run_wolt_to_qq_task(goal, task_spec, should_cancel)
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
    state.collected_data["target_package"] = package
    task_profile = resolve_task_profile(package, goal)
    if task_profile is not None:
        state.collected_data["task_profile"] = task_profile.snapshot()
    # NetEase's search Activity auto-focuses its text field and Android routes
    # the singleton IME to Display 0. Exact-song playback therefore has no
    # visual-search fallback: resolve an official app deep link or fail before
    # creating a shadow display.
    app_uri = resolve_netease_song_uri(goal) if package == "com.netease.cloudmusic" else None
    if requires_netease_exact_song_route(goal, package) and not app_uri:
        raise RuntimeError(
            "无法解析网易云精确歌曲深链；为避免主屏输入法，任务已在操作前安全停止"
        )
    if package == "com.google.android.youtube":
        app_uri = resolve_youtube_search_uri(goal)
    bundle = RuntimeBundle(config, task_id)
    allowed_packages = {"任务目标应用": package}
    result: TaskState | None = None

    # Idempotent fast path: an alarm that already exists is the requested end
    # state, not an error. Check before allocating a virtual display so a flaky
    # USB/display session cannot turn a durable success into a false failure.
    preflight_alarm = _alarm_target(goal) if package == "com.huawei.deskclock" and "闹钟" in goal else None
    if preflight_alarm is not None:
        period, hour12, minute = preflight_alarm
        hour24 = (hour12 % 12) + (12 if period == "下午" else 0)
        preflight_verifier = SystemAlarmVerifier(bundle.adb, hour24, minute)
        preflight_result = preflight_verifier.verify(state)
        if preflight_result.passed:
            state.collected_data["app_skill_route"] = "huawei_alarm_picker"
            state.collected_data["alarm_preexisting"] = True
            state.collected_data["agent_result"] = preflight_result.reason
            state.evidence = [dict(item) for item in preflight_result.evidence]
            state.verification_history.append(preflight_result.model_dump())
            state.status = TaskStatus.COMPLETED
            bundle.monitor.metrics.task_status = state.status.value
            bundle.monitor.metrics.verification_attempts = 1
            # Fast paths must leave the same auditable artifact set as the
            # normal Agent loop. The dashboard and delivery audit consume all
            # three files; metrics alone can prove isolation but not why the
            # task was considered complete.
            bundle.trace.save_state(state)
            bundle.trace.save_result(preflight_result)
            bundle.store.save_metrics(bundle.monitor.metrics)
            return state
    try:
        primary_before = bundle.monitor.snapshot(monotonic_time=time.monotonic())
        qq_draft = package == "com.tencent.mobileqq" and is_qq_draft_request(goal)
        qq_direct_share = bool(
            package == "com.tencent.mobileqq"
            and task_spec
            and task_spec.task_mode.value == "SENSITIVE_TASK"
            and not qq_draft
        )
        # QQ's JumpActivity starts QPublicTransFragmentActivity internally
        # without forwarding ActivityOptions.  If QQ already has a process or
        # task rooted on Display 0, that second hop silently returns to the
        # primary display even though ACTION_SEND itself used --display.  A
        # scrcpy ``+package`` cold start makes the QQ process originate on the
        # owned display, so its internal share Activity inherits the shadow
        # workspace.  Never force that cold start while the user is currently
        # looking at QQ on Display 0.
        if qq_direct_share:
            if primary_before.primary_package == package:
                recovered = recover_stale_qq_share_task(bundle.adb, primary_before.primary_activity)
                if recovered:
                    bundle.monitor.record(Event(
                        datetime.now(timezone.utc).isoformat(), "system", 0,
                        "stale_qq_share_task_recovered",
                        {"activity": primary_before.primary_activity}, "removed",
                    ))
                    time.sleep(0.25)
                    primary_before = bundle.monitor.snapshot(monotonic_time=time.monotonic())
                if primary_before.primary_package == package:
                    raise RuntimeError(
                        "QQ is currently foreground on Display 0; refusing to interrupt the user"
                    )
            display_id = bundle.display.ensure(package)
        else:
            display_id = (
                bundle.display.ensure_staged(package)
                if requires_staged_shadow_start(package)
                else bundle.display.ensure(package)
            )
        state.shadow_display_id = display_id
        bundle.monitor.metrics.shadow_display_id = display_id
        if (
            package == "com.tencent.mobileqq"
            and task_spec
            and (task_spec.task_mode.value == "SENSITIVE_TASK" or qq_draft)
        ):
            scope = str(task_spec.constraints.get("sensitive_scope") or goal)
            confirmed_message = extract_confirmed_message(scope)
            if not confirmed_message:
                raise RuntimeError("QQ 发送任务缺少已确认的消息原文")
            if qq_draft:
                # A draft belongs in the real conversation editor. ACTION_SEND
                # only produces a share-confirmation surface, which is not a
                # persisted QQ input-box draft.
                state.collected_data["app_skill_route"] = "qq_chat_input_draft"
            else:
                # QQ's cold-start Activity can overwrite an ACTION_SEND delivered
                # during process initialization and leave us on the normal inbox.
                time.sleep(2)
                bundle.display.launch_share_text(package, confirmed_message)
                state.collected_data["app_skill_route"] = "qq_share_text"
        if app_uri:
            state.collected_data["app_skill_route"] = (
                "youtube_search_deep_link"
                if package == "com.google.android.youtube"
                else "netease_exact_song_deep_link"
            )
            bundle.display.launch_app(package, app_uri)
        if package == "com.wolt.android":
            folded_goal = goal.casefold()
            if any(term in folded_goal for term in ("中餐", "中式", "chinese", "asian", "asia", "亚洲")):
                state.collected_data["app_skill_route"] = "wolt_chinese_categories"
            elif any(term in folded_goal for term in ("japanese", "japanse", "日料", "日本料理", "日餐")):
                state.collected_data["app_skill_route"] = "wolt_japanese_categories"
            elif any(term in folded_goal for term in ("汉堡", "burger")):
                state.collected_data["app_skill_route"] = "wolt_burger_categories"
        if package == "com.huawei.deskclock" and "闹钟" in goal:
            state.collected_data["app_skill_route"] = "huawei_alarm_picker"
        # Huawei can flash an Activity on Display 0 and move it away again
        # before a single post-launch snapshot. Sample the high-risk startup
        # window so transient same-package Activity hijacks fail closed too.
        primary_samples = [bundle.monitor.snapshot(monotonic_time=time.monotonic())]
        if package == "com.tencent.mobileqq" and state.collected_data.get("app_skill_route") == "qq_share_text":
            for _ in range(7):
                time.sleep(0.25)
                primary_samples.append(bundle.monitor.snapshot(monotonic_time=time.monotonic()))
        primary_after = primary_samples[-1]
        if (
            primary_before.ime_visible is not True
            and primary_after.ime_visible is True
            and primary_after.ime_target_display_id == 0
        ):
            bundle.monitor.metrics.ime_policy_violations += 1
            bundle.monitor.metrics.isolation_violations += 1
            bundle.monitor.metrics.task_status = "FAILED"
            bundle.monitor.record(Event(
                datetime.now(timezone.utc).isoformat(),
                "system",
                0,
                "startup_ime_isolation_violation",
                {"target": package, "route": app_uri or "launcher"},
                "blocked",
            ))
            raise RuntimeError("shadow launch caused the system IME to appear on Display 0")
        primary_leak = next((
            sample for sample in primary_samples
            if sample.primary_package == package
            and (
                primary_before.primary_package != package
                or sample.primary_activity != primary_before.primary_activity
            )
        ), None)
        if primary_leak is not None:
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
                    "after": primary_leak.primary_package,
                    "before_activity": primary_before.primary_activity,
                    "after_activity": primary_leak.primary_activity,
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
        route = str(state.collected_data.get("app_skill_route") or "")
        # Huawei's HandleSetAlarm accepts the standard Intent but ignores
        # SKIP_UI on this device, so it does not persist the alarm. Keep the
        # system-state preflight above for idempotency, then use the visible
        # display-scoped picker and verify the saved result.
        alarm_target = None
        if alarm_target is not None:
            period, hour12, minute = alarm_target
            hour24 = (hour12 % 12) + (12 if period == "下午" else 0)
            target_text = f"{hour12}:{minute:02d}"
            verifier = SystemAlarmVerifier(bundle.adb, hour24, minute)
            already_exists = verifier.verify(state).passed
            state.collected_data["alarm_preexisting"] = already_exists
            actions = []
            if not already_exists:
                actions.append(Action(
                    ActionType.SET_ALARM,
                    display_id=display_id,
                    hour=hour24,
                    minute=minute,
                    capability=ActionCapability.WRITE_DATA,
                    reason=f"通过 Android 语义 Intent 创建{period}{target_text}闹钟，避免聚焦闹钟名称输入框",
                ))
                # SKIP_UI requests an atomic system-side creation.  A blind
                # follow-up tap is unsafe: if the vendor handler completes
                # without opening an editor, that coordinate belongs to the
                # previously visible app.  Verify durable system state only.
                actions.append(Action(ActionType.WAIT, seconds=1, reason="等待系统登记闹钟"))
            actions.append(Action(ActionType.FINISH, reason="使用 Android 系统闹钟状态验证目标时间"))
            planner = ManualPlanner(tuple(actions))
        elif state.collected_data.get("app_skill_route") == "youtube_search_deep_link":
            # Search was already executed by the semantic URI on the shadow
            # display. Avoid rediscovering the search field and triggering the
            # singleton IME on Display 0; use vision only for final evidence.
            expected_text = resolve_youtube_search_query(goal) or "YouTube"
            route_criterion = f"YouTube 搜索结果包含 {expected_text}"
            planner = ManualPlanner((
                Action(ActionType.WAIT, seconds=3, reason="等待隔离屏语义搜索结果渲染"),
                Action(
                    ActionType.OBSERVE,
                    reason="捕获隔离屏语义搜索结果",
                    expected_outcome=ExpectedOutcome(
                        description=route_criterion,
                        required_text=[expected_text],
                    ),
                ),
                Action(ActionType.FINISH, reason="验证搜索词与视频结果后结束任务"),
            ))
            verifier = EvidenceVerifier((route_criterion,))
        else:
            def package_active_on_shadow(expected_package: str) -> bool:
                result = bundle.adb.shell("dumpsys", "activity", "activities")
                if not result.ok or not isinstance(result.stdout, str):
                    return False
                display_match = re.search(
                    rf"Display\s+#?{display_id}\b(.*?)(?=\n\s*Display\s+#?\d+\b|\Z)",
                    result.stdout,
                    re.DOTALL | re.IGNORECASE,
                )
                return bool(display_match and expected_package in display_match.group(0))

            def qq_confirmation_recipient_in_sheet(recipient: str) -> bool:
                """Require the bottom-most exact name to belong to QQ's modal."""
                try:
                    bridge = bundle.executor.inputs.text_bridge
                    if bridge is None or not bridge.available():
                        return False
                    _x, y = bridge.find_text_exact_center(
                        display_id,
                        "com.tencent.mobileqq",
                        recipient,
                        allow_multiple=True,
                        prefer_bottom=True,
                    )
                    return y >= int(config.shadow_height * 0.62)
                except Exception:
                    return False

            planner = VisionPlanner(
                client,
                display_id,
                allowed_packages,
                takeover_notifier=notify_takeover,
                skill_instructions=tuple(skill.instruction for skill in skills)
                + ((task_profile.planner_instruction(),) if task_profile is not None else ()),
                package_identity_checker=package_active_on_shadow,
                qq_confirmation_recipient_checker=qq_confirmation_recipient_in_sheet,
            )
            verifier = (
                EvidenceVerifier((WOLT_ROUTE_CRITERIA[route],))
                if route in WOLT_ROUTE_CRITERIA
                else EvidenceVerifier(("QQ message draft staged",))
                if route == "qq_share_draft"
                else QQMessageVerifier()
                if route == "qq_share_text"
                else EvidenceVerifier(("Alarm saved",))
                if route == "huawei_alarm_picker"
                else VisionVerifier(client)
            )
        result = bundle.agent(planner, verifier, goal, should_cancel=should_cancel).run(state)
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
        # Only successful playback tasks keep the virtual display alive.
        # Transient QQ share Activities finish onto an empty task after Send;
        # leaving scrcpy alive produces a persistent black flashing window.
        # All non-media workspaces are therefore closed at task completion.
        keep_for_media = bool(
            result is not None
            and result.status.value == "COMPLETED"
            and state.collected_data.get("app_skill_route")
            in {"netease_exact_song_deep_link", "youtube_search_deep_link"}
        )
        if not keep_for_media:
            try:
                bundle.display.stop()
            except Exception as exc:
                bundle.monitor.record(Event(
                    datetime.now(timezone.utc).isoformat(),
                    "system",
                    bundle.monitor.metrics.shadow_display_id,
                    "failed_workspace_cleanup",
                    {"reason": str(exc)},
                    "error",
                ))


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
