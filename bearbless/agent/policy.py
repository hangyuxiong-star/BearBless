from __future__ import annotations

import re

from bearbless.schemas import TaskMode


_SENSITIVE = ("支付", "付款", "购买", "转账", "发送", "发消息", "删除", "下单", "授权")
_MUTATING = ("播放", "暂停", "修改", "输入", "保存", "创建", "添加", "切换", "开启", "关闭")
_QUERY = ("查看", "查询", "是否", "多少", "是什么", "告诉我", "比较", "搜索", "找")


def infer_task_mode(goal: str) -> TaskMode:
    """Conservative deterministic mode classification for the policy gate."""
    effective = re.sub(
        r"(?:不要|无需|禁止|不许)(?:支付|付款|购买|转账|发送|删除|下单|授权|播放|暂停|修改|输入|保存|创建|添加|切换|开启|关闭)",
        "",
        goal,
    )
    if any(word in effective for word in _SENSITIVE):
        return TaskMode.SENSITIVE_TASK
    # Interrogative wording describes a fact to observe, even when the fact is
    # an enabled/disabled state ("是否已开启").
    if "是否" in effective or ("查看" in effective and "状态" in effective):
        return TaskMode.READ_ONLY_QUERY
    if any(word in effective for word in _QUERY) and not any(word in effective for word in _MUTATING):
        return TaskMode.READ_ONLY_QUERY
    return TaskMode.MUTATING_TASK


def may_mutate_phone(mode: TaskMode) -> bool:
    return mode == TaskMode.MUTATING_TASK
