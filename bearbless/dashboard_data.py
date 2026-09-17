from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from bearbless.agent.policy import infer_task_mode
from bearbless.schemas import SuccessCriterion, TaskSpec


class TaskRequestError(ValueError):
    pass


def compile_mission_contract(goal: str) -> TaskSpec:
    cleaned = " ".join(goal.split())
    if not 3 <= len(cleaned) <= 1000:
        raise TaskRequestError("任务长度必须在 3–1000 个字符之间")
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
    if analyzer is None:
        from bearbless.agent.model_client import build_phone_model
        from bearbless.config import Config
        analyzer = build_phone_model(Config.load())
    explicit_message = any(term in cleaned for term in ("发消息", "发送消息", "发给", "告诉"))
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
        return fallback.model_copy(update={
            "constraints": {
                **fallback.constraints,
                "contract_fallback": True,
                "contract_fallback_reason": str(exc)[:300],
            }
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
