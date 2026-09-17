from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import re
from collections.abc import Callable
from urllib.request import Request, urlopen

from PIL import Image

from bearbless.agent.state import TaskState
from bearbless.agent.grounding import SetOfMarkGrounder
from bearbless.agent.model_client import PhoneModelClient
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.actions import Action, ActionCapability, ActionType
from bearbless.errors import AgentTerminalDecision, ModelProtocolError
from bearbless.schemas import AgentAction, PhoneDecision, VerificationResult


class VisionAgentError(ModelProtocolError):
    pass


def _normalize_model_decision(raw: dict, width: int = 1080, height: int = 2400) -> dict:
    """Normalize common model dialects without inventing semantic intent.

    GUI models variously express a swipe as start/end arrays or as a start
    point plus a direction.  Convert those mechanical representations into
    our strict schema.  An incomplete swipe with no explicit direction stays
    incomplete and is therefore rejected by ``PhoneDecision``.
    """
    decision = dict(raw)
    action = str(decision.get("action") or "").upper()
    decision["action"] = action
    if action != "SWIPE":
        return decision

    def take_point(*names: str) -> tuple[int, int] | None:
        for name in names:
            value = decision.pop(name, None)
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                try:
                    return int(float(value[0])), int(float(value[1]))
                except (TypeError, ValueError):
                    pass
        return None

    start = take_point("start", "start_point", "coordinate")
    end = take_point("end", "end_point", "coordinate2", "end_coordinate")
    if start:
        decision.setdefault("x", start[0])
        decision.setdefault("y", start[1])
    if end:
        decision.setdefault("x2", end[0])
        decision.setdefault("y2", end[1])

    direction_value = str(decision.pop("direction", "") or "").lower()
    if not direction_value:
        # Some models put the explicit direction in their short rationale.
        direction_value = str(decision.get("reason") or "").lower()
    direction = next((name for name, markers in {
        "up": ("up", "向上", "上滑"),
        "down": ("down", "向下", "下滑"),
        "left": ("left", "向左", "左滑"),
        "right": ("right", "向右", "右滑"),
    }.items() if any(marker in direction_value for marker in markers)), None)

    missing_end = decision.get("x2") is None or decision.get("y2") is None
    if direction and missing_end:
        x = int(decision.get("x") if decision.get("x") is not None else width // 2)
        y = int(decision.get("y") if decision.get("y") is not None else height // 2)
        picker = any(marker in direction_value for marker in (
            "picker", "wheel", "滚轮", "齿轮", "时间", "小时", "分钟", "日期",
        ))
        distance = int((height if direction in {"up", "down"} else width) * (.10 if picker else .35))
        endpoints = {
            "up": (x, max(0, y - distance)),
            "down": (x, min(height - 1, y + distance)),
            "left": (max(0, x - distance), y),
            "right": (min(width - 1, x + distance), y),
        }
        decision.setdefault("x", x)
        decision.setdefault("y", y)
        decision["x2"], decision["y2"] = endpoints[direction]
    decision.setdefault("duration_ms", 400)
    return decision


def _ground_alarm_picker_swipe(
    decision: dict, grounded_text: str, *, width: int = 1080, height: int = 2400,
) -> dict:
    """Snap Huawei alarm-picker gestures to one real column and one row."""
    if decision.get("action") != "SWIPE" or "新建闹钟" not in grounded_text:
        return decision
    try:
        raw_x = int(decision.get("x"))
        raw_y = int(decision.get("y"))
        raw_y2 = int(decision.get("y2"))
    except (TypeError, ValueError):
        return decision
    columns = (int(width * .204), int(width * .5), int(width * .794))
    column_x = min(columns, key=lambda candidate: abs(candidate - raw_x))
    center_y = int(height * .21)
    row_step = max(90, int(height * .05))
    direction = 1 if raw_y2 > raw_y else -1
    return {
        **decision,
        "x": column_x,
        "x2": column_x,
        "y": center_y,
        "y2": center_y + direction * row_step,
        "duration_ms": 320,
    }


def _alarm_target(goal: str) -> tuple[str, int, int] | None:
    match = re.search(r"(\d{1,2})\s*点\s*(半|\d{1,2})?", goal)
    if not match:
        return None
    hour = int(match.group(1))
    minute_text = match.group(2) or "0"
    minute = 30 if minute_text == "半" else int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    period = "下午" if hour >= 12 and hour != 24 else "上午"
    hour12 = hour % 12 or 12
    return period, hour12, minute


def _blue_score(frame_path: str, bounds: tuple[int, int, int, int]) -> int:
    if not frame_path:
        return 0
    try:
        with Image.open(frame_path).convert("RGB") as image:
            crop = image.crop(bounds)
            pixels = crop.get_flattened_data()
            return sum(
                1 for red, green, blue in pixels
                if blue >= 150 and blue > red * 1.35 and blue > green * 1.15
            )
    except (OSError, ValueError):
        return 0


def _alarm_picker_action(
    goal: str, elements, display_id: int, frame_path: str = "",
) -> Action | None:
    """Drive a visible Huawei alarm picker from OCR instead of model guesses."""
    target = _alarm_target(goal)
    period_candidates = [
        item for item in elements
        if item.label.replace(" ", "") in {"上午", "下午"}
    ]
    period_item = max(
        period_candidates,
        key=lambda item: _blue_score(frame_path, item.bounds),
        default=None,
    )
    if target is None or period_item is None:
        return None
    selected_y = period_item.center[1]
    numbers = [
        item for item in elements
        if re.fullmatch(r"\d{1,2}", item.label.strip())
        and abs(item.center[1] - selected_y) <= 65
    ]
    hour_candidates = [item for item in numbers if abs(item.center[0] - 540) < 140]
    minute_candidates = [item for item in numbers if abs(item.center[0] - 858) < 140]
    hour_item = max(
        hour_candidates,
        key=lambda item: (_blue_score(frame_path, item.bounds), -abs(item.center[1] - selected_y)),
        default=None,
    )
    minute_item = max(
        minute_candidates,
        key=lambda item: (_blue_score(frame_path, item.bounds), -abs(item.center[1] - selected_y)),
        default=None,
    )
    if hour_item is None or minute_item is None:
        return None
    try:
        current_hour = int(hour_item.label)
        current_minute = int(minute_item.label)
    except ValueError:
        return None
    target_period, target_hour, target_minute = target
    row_step = 120
    if period_item.label.replace(" ", "") != target_period:
        move_down = target_period == "上午"
        return Action(
            ActionType.SWIPE, display_id=display_id,
            x=220, y=selected_y, x2=220,
            y2=selected_y + (row_step if move_down else -row_step),
            duration_ms=320, reason=f"切换到{target_period}",
            capability=ActionCapability.CHANGE_SETTING,
        )
    if current_hour != target_hour:
        forward = (target_hour - current_hour) % 12
        backward = (current_hour - target_hour) % 12
        increase = forward <= backward
        return Action(
            ActionType.SWIPE, display_id=display_id,
            x=540, y=selected_y, x2=540,
            y2=selected_y + (-row_step if increase else row_step),
            duration_ms=320,
            reason=f"小时 {current_hour:02d} 调整到 {target_hour:02d}",
            capability=ActionCapability.CHANGE_SETTING,
        )
    if current_minute != target_minute:
        forward = (target_minute - current_minute) % 60
        backward = (current_minute - target_minute) % 60
        increase = forward <= backward
        return Action(
            ActionType.SWIPE, display_id=display_id,
            x=858, y=selected_y, x2=858,
            y2=selected_y + (-row_step if increase else row_step),
            duration_ms=320,
            reason=f"分钟 {current_minute:02d} 调整到 {target_minute:02d}",
            capability=ActionCapability.CHANGE_SETTING,
        )
    return Action(
        ActionType.TAP, display_id=display_id, x=840, y=195,
        reason=f"确认闹钟 {target_period}{target_hour:02d}:{target_minute:02d}",
        capability=ActionCapability.CHANGE_SETTING,
    )


def _json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise VisionAgentError(f"vision model returned invalid JSON: {text[:240]}") from exc
    if not isinstance(payload, dict):
        raise VisionAgentError("vision model response must be a JSON object")
    return payload


@dataclass(frozen=True)
class OllamaVisionClient:
    model: str = "qwen3-vl:4b"
    endpoint: str = "http://127.0.0.1:11434/api/chat"
    timeout_seconds: float = 90

    def complete(self, prompt: str, frame_path: str | None = None) -> dict:
        message: dict[str, object] = {"role": "user", "content": prompt}
        if frame_path:
            path = Path(frame_path)
            if not path.exists():
                raise VisionAgentError(f"observation frame is missing: {path}")
            message["images"] = [base64.b64encode(path.read_bytes()).decode("ascii")]
        body = json.dumps({
            "model": self.model,
            "messages": [message],
            "format": "json",
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 512},
        }).encode("utf-8")
        request = Request(self.endpoint, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise VisionAgentError(f"local vision model unavailable: {exc}") from exc
        model_message = envelope.get("message", {})
        content = model_message.get("content") or model_message.get("thinking", "")
        return _json_object(str(content))

    def select_package(self, goal: str, installed_packages: list[str]) -> str:
        prompt = f"""根据用户任务，从手机已安装包名中选出应启动的唯一应用。
用户任务：{goal}
已安装包名：{json.dumps(installed_packages, ensure_ascii=False)}
只返回 JSON：{{"package":"完整包名","reason":"简体中文理由"}}。package 必须逐字来自列表；无法确定则返回空字符串。"""
        payload = self.complete(prompt)
        package = str(payload.get("package") or "")
        if package not in installed_packages:
            raise VisionAgentError(f"无法从已安装应用中解析目标：{goal}")
        return package


class VisionPlanner:
    """Reactive screenshot planner: one bounded action per observation."""

    reactive = True

    def __init__(self, client: PhoneModelClient, display_id: int, allowed_packages: dict[str, str], grounder=None, takeover_notifier: Callable[[str], None] | None = None, skill_instructions: tuple[str, ...] = ()) -> None:
        self.client = client
        self.display_id = display_id
        self.allowed_packages = allowed_packages
        self.grounder = grounder or SetOfMarkGrounder(CommandRunner())
        self.takeover_notifier = takeover_notifier
        self.skill_instructions = skill_instructions

    def plan(self, state: TaskState) -> list[Action]:
        if state.last_observation is None:
            return [Action(ActionType.OBSERVE, reason="观察 Agent 虚拟屏的初始状态")]
        fingerprint = str(state.last_observation.get("fingerprint") or "")
        # Uniform white/black frames are rendering states, not semantic UI.
        # Never spend a model call on them: let WebView/Activity composition
        # settle, capture again, and fail with an honest rendering diagnosis.
        if fingerprint and len(set(fingerprint)) == 1:
            blank_count = int(state.collected_data.get("blank_frame_count", 0)) + 1
            state.collected_data["blank_frame_count"] = blank_count
            if blank_count <= 3:
                return [Action(
                    ActionType.WAIT,
                    seconds=min(1.5 * blank_count, 4.0),
                    reason=f"检测到空白渲染帧，等待页面完成绘制（{blank_count}/3）",
                )]
            raise AgentTerminalDecision(
                "ABORT",
                "虚拟屏连续出现空白帧，目标页面可能不兼容副显示渲染",
            )
        state.collected_data.pop("blank_frame_count", None)
        history = [
            {"action": item.get("action"), "reason": item.get("reason")}
            for item in state.action_history[-8:]
        ]
        spec = state.task_spec.model_dump(mode="json") if state.task_spec else {"goal": state.goal}
        skill_context = "\n".join(f"- {item}" for item in self.skill_instructions)
        frame_path = str(state.last_observation.get("frame_path") or "")
        grounded = self.grounder.ground(frame_path)
        # Authentication is a human boundary, not a navigation path for the
        # model to explore. Keep these markers narrow: an optional generic
        # "登录" button must not stop an otherwise anonymous task.
        auth_boundary_markers = (
            "登录已过期",
            "请重新登录",
            "输入密码",
            "短信验证码",
            "手机号登录",
            "账号验证",
        )
        grounded_text = "".join(item.label.replace(" ", "") for item in grounded.elements)
        if "新建闹钟" in grounded_text and "闹钟" in state.goal:
            picker_action = _alarm_picker_action(
                state.goal, grounded.elements, self.display_id, frame_path,
            )
            if picker_action is not None:
                return [picker_action]
        verification_markers = (
            "人机验证", "滑块验证", "滑动滑块", "向右滑动", "安全验证", "完成验证",
        )
        if any(marker in grounded_text for marker in verification_markers):
            waits = int(state.collected_data.get("human_verification_waits", 0)) + 1
            state.collected_data["human_verification_waits"] = waits
            state.collected_data["human_takeover"] = {
                "kind": "verification",
                "message": "请在电脑的 BearBless Shadow Display 窗口手动完成验证",
            }
            if waits == 1 and self.takeover_notifier:
                self.takeover_notifier("verification")
            if waits > 18:
                raise AgentTerminalDecision("TAKE_OVER", "人机验证等待超时，请完成验证后重新提交任务")
            return [Action(
                ActionType.WAIT,
                display_id=self.display_id,
                seconds=5,
                reason=f"等待用户手动完成人机验证（{waits}/18）",
            )]
        state.collected_data.pop("human_verification_waits", None)
        state.collected_data.pop("human_takeover", None)
        if any(marker in grounded_text for marker in auth_boundary_markers):
            raise AgentTerminalDecision("TAKE_OVER", "页面需要身份验证，请用户手动完成后再继续")
        confirmed_sensitive = bool(
            state.task_spec
            and state.task_spec.constraints.get("user_confirmed_sensitive_action") is True
        )
        if confirmed_sensitive and "QQ" in state.goal:
            qq_identity_markers = ("QQ", "消息", "联系人", "动态", "登录")
            if not any(marker in grounded_text for marker in qq_identity_markers):
                raise AgentTerminalDecision(
                    "ABORT",
                    "敏感任务无法确认当前虚拟屏属于 QQ，已在发送前停止",
                )
        banned_signatures = [str(item) for item in state.collected_data.get("banned_actions", [])]

        def element_is_banned(item) -> bool:
            x, y = item.center
            return any(f":TAP:{x}:{y}:" in signature for signature in banned_signatures)

        element_catalog = [
            {
                "element_id": item.element_id,
                "label": item.label,
                "bounds": item.bounds,
                "forbidden_no_effect": element_is_banned(item),
            }
            for item in grounded.elements
        ]
        sensitive_instruction = (
            "用户已在任务契约中明确确认一次敏感操作。只能严格按 goal 中的联系人和消息原文发送一次；"
            "导航和输入分别声明 NAVIGATE/SEARCH/ENTER_TEXT，最终点击发送必须声明 SENSITIVE。"
            if confirmed_sensitive
            else "禁止下单、支付、发送消息、删除数据、授权敏感权限。"
        )
        if getattr(self.client, "native_tool_protocol", False):
            prompt = f"""Please generate the next move according to the UI screenshot, instruction and previous actions.
Instruction: {state.goal}
Task contract: {json.dumps(spec, ensure_ascii=False)}
Selected skill guidance:\n{skill_context or '- general GUI capability only'}
Previous actions: {json.dumps(history, ensure_ascii=False)}
Last deterministic check: {json.dumps(state.collected_data.get('last_step_outcome', {}), ensure_ascii=False)}
The target app is already open on an isolated secondary display. Never use action=open. Do only one next action.
The on-screen keyboard is intentionally hidden to protect the user's primary display. For non-ASCII search terms, first click an exact visible history item, suggestion, category or result matching the instruction. Do not use action=type for Chinese text. If no semantically correct visible target exists and Chinese input is required, use interact for human takeover. ASCII text may use action=type.
For login, password or verification code use interact. Do not purchase, pay, send messages, delete data or press Home unless explicitly authorized by the task contract.
Use terminate success only after every required success criterion is visibly satisfied."""
        else:
            prompt = f"""你是 BearBless 手机视觉 Agent。根据最新虚拟屏 Set-of-Mark 截图，只决定下一步，不要编造页面内容。
任务契约：{json.dumps(spec, ensure_ascii=False)}
已选择 Skill 指引：\n{skill_context or '- 仅使用通用 GUI 能力'}
最近动作：{json.dumps(history, ensure_ascii=False)}
上一步确定性检查：{json.dumps(state.collected_data.get('last_step_outcome', {}), ensure_ascii=False)}
禁止重复的动作签名：{json.dumps(state.collected_data.get('banned_actions', []), ensure_ascii=False)}
当前 OCR 元素目录：{json.dumps(element_catalog, ensure_ascii=False)}
屏幕尺寸：1080x2400。当前 Display ID：{self.display_id}。
允许的应用包：{json.dumps(self.allowed_packages, ensure_ascii=False)}

只返回一个 JSON 对象。action 只能是 CLICK_ELEMENT、TAP、SWIPE、TYPE、BACK、WAIT、REPORT、TAKE_OVER、ABORT。
每个操作型动作必须声明 capability，只能是 NAVIGATE、READ、SEARCH、ENTER_TEXT、MEDIA_CONTROL、CHANGE_SETTING、WRITE_DATA、SENSITIVE。
禁止 TAP/CLICK 搜索框、输入框、地址栏或任何可能获得文本焦点的区域；仅仅聚焦就可能让输入法出现在用户主屏。需要输入时直接返回 TYPE，由 display-scoped Accessibility Bridge 写入；若当前页面没有标准输入节点，应请求 TAKE_OVER 或改用语义 URI，不能先点击输入区。
优先使用 CLICK_ELEMENT，并返回 element_id。只有目标没有编号时才允许 TAP: x,y；SWIPE: x,y,x2,y2,duration_ms；TYPE: text；WAIT: seconds。
OCR 目录可能漏字或错字：如果截图中目标清晰可见但没有语义一致的编号，必须对目标中心使用 TAP；绝不能为了使用 CLICK_ELEMENT 而选择文字无关的编号。
每次只做一个最小动作。{sensitive_instruction}
如果上一步是 NO_EFFECT，必须基于当前截图换一种操作；如果是 LOOP_DETECTED，绝不能重复被禁止的动作。
目录中 forbidden_no_effect=true 的元素已经被确定性验证为点击无效，严禁再次选择。
如果 task_mode=READ_ONLY_QUERY，看到答案后必须 REPORT，并通过 result={{summary,facts:[{{name,value,evidence}}]}} 报告结构化真实状态；绝不能切换开关或修改状态。
REPORT 代表整个用户目标已经完成，不是当前子步骤完成。必须逐项满足任务契约中全部 required=true 的完成条件后才能 REPORT；仅仅打开目标应用绝不能 REPORT。
如果任务已经通过当前画面明确完成，返回 REPORT；遇到登录、验证码或必须人工处理的步骤返回 TAKE_OVER；无法安全继续返回 ABORT。
所有操作型动作必须给 display_id={self.display_id}，并返回 target（准备操作的可见对象）和 confidence（0到1）。返回字段仅限 action/element_id/display_id/x/y/x2/y2/duration_ms/text/keycode/seconds/reason/observed_result/result/capability/target/confidence。
"""
        # GUI-Plus is trained to ground directly on clean screenshots. OCR
        # overlays obscure dense mobile UIs and also depart from Alibaba's
        # recommended mobile loop. Keep Set-of-Mark for local/general VLMs
        # and for deterministic safety checks only.
        model_frame = frame_path if getattr(self.client, "native_tool_protocol", False) else str(grounded.annotated_path)
        raw_decision = _normalize_model_decision(self.client.complete(prompt, model_frame))
        raw_decision = _ground_alarm_picker_swipe(raw_decision, grounded_text)
        if raw_decision.get("action") == "CLICK_ELEMENT":
            raw_element_id = raw_decision.get("element_id")
            if isinstance(raw_element_id, str) and not raw_element_id.strip().isdigit():
                requested_label = raw_element_id.replace(" ", "").strip()
                matches = [
                    item for item in grounded.elements
                    if requested_label and (
                        requested_label == item.label.replace(" ", "").strip()
                        or requested_label in item.label.replace(" ", "")
                    )
                ]
                if len(matches) == 1:
                    raw_decision = {**raw_decision, "element_id": matches[0].element_id}
        # Compatibility shim with a narrow safety proof: OCR Set-of-Mark
        # targets are text bounds, so an unclassified CLICK_ELEMENT can only
        # be treated as navigation. Raw coordinate taps never receive this
        # fallback because they may target an unlabeled toggle.
        if raw_decision.get("action") == "CLICK_ELEMENT" and not raw_decision.get("capability"):
            raw_decision = {**raw_decision, "capability": "NAVIGATE"}
        try:
            decision = PhoneDecision.model_validate(raw_decision)
        except ValueError as exc:
            raise VisionAgentError(f"phone model returned an invalid decision: {exc}") from exc
        if decision.action in {"CLICK_ELEMENT", "TAP", "SWIPE", "TYPE", "KEY", "BACK"}:
            threshold = 0.90 if decision.capability == "SENSITIVE" else 0.68
            if decision.confidence is not None and decision.confidence < threshold:
                raise VisionAgentError(
                    f"low-confidence action rejected: {decision.confidence:.2f} < {threshold:.2f}"
                )
        payload = decision.model_dump(exclude_none=True)
        # These fields are evidence for pre-execution policy, not runtime ADB
        # parameters. Keep them out of the strict AgentAction schema.
        payload.pop("confidence", None)
        payload.pop("target", None)
        if decision.action in {"REPORT", "FINISH"}:
            structured = decision.result.model_dump(mode="json") if decision.result else None
            reason = (decision.result.summary if decision.result else None) or decision.observed_result or decision.reason or "视觉模型确认任务完成"
            required_text = " ".join(
                f"{criterion.name} {criterion.description}"
                for criterion in (state.task_spec.success_criteria if state.task_spec else [])
                if criterion.required and criterion.name != "zero_interruption"
            )
            report_text = " ".join((reason, json.dumps(structured or {}, ensure_ascii=False)))
            outcome_terms = [
                term for term in ("搜索", "比较", "播放", "状态", "开启", "关闭", "天气", "路线", "价格", "结果")
                if term in required_text
            ]
            if outcome_terms and not any(term in report_text for term in outcome_terms):
                raise VisionAgentError(
                    "REPORT rejected: result only covers a subgoal, not the required task outcome"
                )
            state.collected_data["agent_result"] = reason
            if structured:
                state.collected_data["structured_result"] = structured
            state.collected_data["decision_kind"] = "REPORT"
            return [Action(ActionType.FINISH, reason=reason)]
        if decision.action in {"TAKE_OVER", "ABORT"}:
            raise AgentTerminalDecision(decision.action, decision.reason or "模型请求安全停止")
        action_name = decision.action
        if action_name == "CLICK_ELEMENT":
            try:
                element_id = int(payload.pop("element_id"))
            except (KeyError, TypeError, ValueError) as exc:
                raise VisionAgentError("CLICK_ELEMENT requires a valid element_id") from exc
            selected = next((item for item in grounded.elements if item.element_id == element_id), None)
            if selected is None:
                raise VisionAgentError(f"model selected unknown element_id={element_id}")
            if element_is_banned(selected):
                raise VisionAgentError(f"model repeated a deterministic no-effect element_id={element_id}")
            payload["action"] = "TAP"
            payload["x"], payload["y"] = selected.center
            payload["reason"] = str(payload.get("reason") or f"点击元素 {element_id}: {selected.label}")
            action_name = "TAP"
        if action_name == "WAIT":
            try:
                payload["seconds"] = min(max(float(payload.get("seconds", 1)), 0.5), 5.0)
            except (TypeError, ValueError):
                payload["seconds"] = 1.0
        if action_name == "SWIPE":
            try:
                payload["duration_ms"] = min(max(int(payload.get("duration_ms", 350)), 100), 1500)
            except (TypeError, ValueError):
                payload["duration_ms"] = 350
        payload["display_id"] = self.display_id
        action = AgentAction.model_validate(payload).to_runtime()
        if action.action in {ActionType.CONDITIONAL_TAP, ActionType.OPEN_APP, ActionType.OBSERVE}:
            raise VisionAgentError(f"model selected unsupported reactive action: {action.action.value}")
        if action.irreversible:
            raise VisionAgentError("irreversible action rejected")
        return [action]


class VisionVerifier:
    """A second model call independently checks the final screenshot."""

    def __init__(self, client: PhoneModelClient) -> None:
        self.client = client

    def verify(self, state: TaskState) -> VerificationResult:
        frame_path = str((state.last_observation or {}).get("frame_path") or "")
        spec = state.task_spec.model_dump(mode="json") if state.task_spec else {"goal": state.goal}
        prompt = f"""你是独立验证器。只根据截图判断任务是否完成。
用户目标：{state.goal}
任务契约：{json.dumps(spec, ensure_ascii=False)}
Agent 声称的结果：{state.collected_data.get('agent_result', '')}
结构化事实：{json.dumps(state.collected_data.get('structured_result', {}), ensure_ascii=False)}
禁止把“打开了应用”当作搜索任务完成。必须在截图中看到与目标相关的真实结果。
如果 task_mode=READ_ONLY_QUERY，任务成功表示已正确观察并回答当前真实状态；状态值为“关闭、未开启、没有”不代表任务失败。只要截图支持报告的值就应 passed=true。
只有截图不支持报告、尚未到达目标页面或答案仍未知时才 passed=false。
只返回 JSON：{{"passed":true或false,"reason":"简体中文说明","evidence":"截图中实际看到的证据"}}。
"""
        payload = self.client.complete(prompt, frame_path)
        passed = payload.get("passed") is True
        reason = str(payload.get("reason") or ("验证通过" if passed else "最终截图未证明任务完成"))
        transient_markers = ("空白", "为空", "加载", "尚未", "未显示", "未看到", "未到达", "未知")
        return VerificationResult(
            passed=passed,
            retryable=not passed and any(marker in reason for marker in transient_markers),
            reason=reason,
            evidence=[{
                "criterion": "视觉独立验证",
                "passed": passed,
                "evidence": frame_path,
                "summary": str(payload.get("evidence") or ""),
            }],
        )
