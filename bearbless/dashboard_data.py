from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from bearbless.agent.policy import infer_task_mode
from bearbless.schemas import SuccessCriterion, TaskSpec
from bearbless.message_intent import extract_confirmed_message


class TaskRequestError(ValueError):
    pass


def build_local_task_analysis(goal: str) -> dict[str, object]:
    """Explain the execution route without depending on a cloud model."""
    folded = goal.casefold()
    if "qq" in folded and any(term in goal for term in ("发消息", "发送消息", "发信息", "发送信息")):
        match = re.search(r"(?:给|告诉)([^，,：:\s]+?)(?:发消息|发送消息|发信息|发送信息|说)", goal)
        recipient = match.group(1) if match else "契约指定联系人"
        message = extract_confirmed_message(goal) or "契约中的消息原文"
        return {
            "intent": f"使用 QQ 向 {recipient} 单次发送“{message}”",
            "route": "Android ACTION_SEND → 发送给好友 → 仅此一次 → 精确联系人 → 单次发送",
            "steps": ["在隔离虚拟屏启动 QQ 分享入口", f"匹配完整联系人：{recipient}", f"核对原文：{message}", "发送一次并重新观察聊天气泡"],
            "guard": "不操作 Display 0；不读取剪贴板；不打开搜索框；发送不可重放",
            "proof": "联系人名称、完整原文气泡、发送次数=1、主屏操作=0",
        }
    if "wolt" in folded or any(term in goal for term in ("餐厅", "汉堡", "中餐")):
        return {
            "intent": "在 Wolt 浏览并验证符合条件的餐厅",
            "route": "Restaurants → Food type 分类 → 候选商家 → 店铺详情",
            "steps": ["打开餐厅分类", "比较可见评分", "进入最高评分候选", "读取并核对地址"],
            "guard": "不使用搜索框和系统输入法；不进入购物车、下单或支付",
            "proof": "店名、评分、营业状态、详情页地址、主屏操作=0",
        }
    if "闹钟" in goal or "时钟" in goal:
        return {
            "intent": "在系统时钟中设置或验证目标闹钟",
            "route": "先检查是否已存在 → 隔离屏时间滚轮 → 保存 → 系统状态复核",
            "steps": ["解析目标时间", "检查幂等状态", "逐列调整时间", "保存并重新读取闹钟列表"],
            "guard": "每次只调整一个滚轮；已存在则不重复创建；不操作主屏",
            "proof": "目标时间、已保存、已开启、主屏操作=0",
        }
    if any(term in goal for term in ("网易云", "音乐", "播放")):
        return {
            "intent": "通过免输入法路径打开并验证指定媒体",
            "route": "解析精确歌曲 → 官方深链 → 播放 → MediaSession 验证",
            "steps": ["解析歌曲目标", "打开官方深链", "触发播放", "核对标题和播放状态"],
            "guard": "不聚焦搜索框；成功后立即停止；不重复触发播放",
            "proof": "MediaSession 标题匹配、播放态=PLAYING、主屏操作=0",
        }
    return {
        "intent": f"在隔离虚拟屏完成：{goal}",
        "route": "识别目标应用 → 单步观察与执行 → 独立验证",
        "steps": ["确认目标应用", "观察当前页面", "执行最小必要操作", "重新观察并验证结果"],
        "guard": "不操作 Display 0；不读取剪贴板；超出契约立即停止",
        "proof": "最终页面证据、操作轨迹、隔离指标",
    }


PRODUCT_CAPABILITIES = (
    ("Wolt 找店", "从餐厅详情读取店名、评分、营业状态和地址"),
    ("时钟闹钟", "在隔离屏设置并重新验证目标时间"),
    ("QQ 草稿", "在指定联系人会话中编辑原文草稿并停留，不点击发送"),
    ("音乐播放", "通过免输入法路径搜索并验证播放状态"),
)


def ensure_supported_scope(goal: str) -> None:
    if any(marker in goal for marker in ("地图", "导航", "路线规划")):
        raise TaskRequestError(
            "地图能力已移出当前演示范围；餐厅地址会直接从 Wolt 店铺详情页读取。"
        )


def compile_mission_contract(goal: str) -> TaskSpec:
    cleaned = " ".join(goal.split())
    if not 3 <= len(cleaned) <= 1000:
        raise TaskRequestError("任务长度必须在 3–1000 个字符之间")
    ensure_supported_scope(cleaned)
    return TaskSpec(
        goal=cleaned,
        constraints={"workspace": "shadow_display", "human_priority": True},
        allowed_actions=[
            "打开完成任务所需的目标应用",
            "执行用户指令明确要求的最小操作",
            "读取并报告任务相关的当前状态",
        ],
        forbidden_actions=["付款或购买", "发送消息", "删除用户数据", "操作 Display 0", "读取系统剪贴板"],
        success_criteria=[
            SuccessCriterion(name="result_created", description=f"完成用户目标：{cleaned}"),
            SuccessCriterion(name="result_verified", description="重新观察结果并通过独立验证"),
            SuccessCriterion(name="zero_interruption", description="Agent 对用户主屏的操作次数为 0"),
        ],
        interruption_budget=0,
        task_mode=infer_task_mode(cleaned),
    )


def compile_dynamic_mission_contract(goal: str, analyzer=None) -> TaskSpec:
    cleaned = " ".join(goal.split())
    if not 3 <= len(cleaned) <= 1000:
        raise TaskRequestError("任务长度必须在 3–1000 个字符之间")
    ensure_supported_scope(cleaned)
    if analyzer is None:
        from bearbless.agent.model_client import build_phone_model
        from bearbless.config import Config
        analyzer = build_phone_model(Config.load())
    # Keep this vocabulary aligned with ``build_local_task_analysis`` and the
    # task-mode classifier.  "发信息" is a common, equally explicit send verb;
    # omitting it produced a contradictory contract that allowed the concrete
    # send step while also retaining the generic "发送消息" prohibition.
    explicit_message = any(
        term in cleaned
        for term in ("发消息", "发送消息", "发信息", "发送信息", "发给", "告诉")
    )
    prompt = f"""分析用户的手机任务并生成最小权限任务契约。用户任务：{cleaned}
只返回 JSON，字段为 allowed_actions（简体中文字符串数组）、forbidden_actions（简体中文字符串数组）、success_criteria（对象数组，每项含 name、description、required）。
允许项必须匹配真实意图，例如音乐任务应包括搜索和播放；餐厅任务包括搜索、浏览和比较。不要把所有任务都写成网页任务。
默认禁止支付、购买、发送消息、删除数据、操作 Display 0、读取系统剪贴板。只有当用户指令明确给出收件人和消息正文时，才允许向该收件人发送该条消息；不得改写、扩展或发送第二条。完成条件必须具体且能从最终屏幕验证。
严格区分“查询状态”和“修改状态”：如果用户只问“查看/查询/是否”，成功条件是看见并报告当前真实状态，绝不能擅自要求、切换或修改该状态。例如“查看蓝牙是否开启”的条件应是“画面明确显示蓝牙当前为开启或关闭”，而不是“蓝牙必须开启”。"""
    try:
        payload = analyzer.complete(prompt)
        required_fields = {"allowed_actions", "forbidden_actions", "success_criteria"}
        if not isinstance(payload, dict) or not required_fields.issubset(payload):
            correction_prompt = (
                prompt
                + "\n上一次返回格式无效。现在必须返回 JSON 对象，不能返回 true/false、数组或解释文字；"
                + "三个字段必须全部存在。"
            )
            payload = analyzer.complete(correction_prompt)
        if not isinstance(payload, dict) or not required_fields.issubset(payload):
            raise TaskRequestError("模型连续两次没有返回有效任务契约")
        if not all(isinstance(payload.get(name), list) for name in required_fields):
            raise TaskRequestError("任务契约字段类型无效")
        mode = infer_task_mode(cleaned)
        authorized_message = explicit_message and mode.value == "SENSITIVE_TASK"
        model_allowed = [str(item) for item in payload.get("allowed_actions", [])]
        model_forbidden = [str(item) for item in payload.get("forbidden_actions", [])]
        if mode.value == "READ_ONLY_QUERY":
            model_allowed = list(dict.fromkeys([*model_allowed, "读取并报告任务相关的当前状态"]))
            # A model may confuse the requested observation with a forbidden
            # mutation. Query verbs are never valid forbidden effects.
            model_forbidden = [
                item for item in model_forbidden
                if not any(word in item for word in ("查看", "查询", "读取", "是否"))
            ]
        constraints = {"workspace": "shadow_display", "human_priority": True}
        if authorized_message:
            constraints["user_confirmed_sensitive_action"] = True
            constraints["sensitive_scope"] = cleaned
            model_allowed = list(dict.fromkeys([*model_allowed, "仅按用户原文向指定联系人发送一次消息"]))
            model_forbidden = [item for item in model_forbidden if "发送消息" not in item]
        forbidden = list(dict.fromkeys([
            *model_forbidden,
            "付款或购买", "删除用户数据", "操作 Display 0", "读取系统剪贴板",
            *([] if authorized_message else ["发送消息"]),
        ]))
        criteria = [SuccessCriterion.model_validate(item) for item in payload.get("success_criteria", [])]
        criteria.append(SuccessCriterion(name="zero_interruption", description="Agent 对用户主屏的操作次数为 0"))
        return TaskSpec(
            goal=cleaned,
            constraints=constraints,
            allowed_actions=model_allowed,
            forbidden_actions=forbidden,
            success_criteria=criteria,
            interruption_budget=0,
            task_mode=mode,
        )
    except Exception as exc:
        # A malformed remote response must not prevent a safe task from
        # entering the guarded runtime. Fall back to a conservative contract.
        fallback = compile_mission_contract(cleaned)
        fallback_constraints = {
            **fallback.constraints,
            "contract_fallback": True,
            "contract_fallback_reason": str(exc)[:300],
        }
        fallback_forbidden = list(fallback.forbidden_actions)
        fallback_allowed = list(fallback.allowed_actions)
        if explicit_message and fallback.task_mode.value == "SENSITIVE_TASK":
            fallback_constraints["user_confirmed_sensitive_action"] = True
            fallback_constraints["sensitive_scope"] = cleaned
            fallback_allowed = list(dict.fromkeys([
                *fallback_allowed,
                "仅按用户原文向指定联系人发送一次消息",
            ]))
            fallback_forbidden = [item for item in fallback_forbidden if "发送消息" not in item]
        return fallback.model_copy(update={
            "constraints": fallback_constraints,
            "allowed_actions": fallback_allowed,
            "forbidden_actions": fallback_forbidden,
        })


def submit_task_request(goal: str, root: Path, contract: TaskSpec | None = None) -> Path:
    contract = contract or compile_mission_contract(goal)
    cleaned = contract.goal
    root.mkdir(parents=True, exist_ok=True)
    request_id = f"request-{uuid.uuid4().hex[:12]}"
    path = root / f"{request_id}.json"
    payload = {
        "request_id": request_id,
        "goal": cleaned,
        "status": "QUEUED",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "source": "dashboard",
        "mission_contract": contract.model_dump(mode="json"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
