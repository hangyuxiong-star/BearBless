from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Milestone:
    name: str
    completion_evidence: tuple[str, ...]
    replay_safe: bool = True


@dataclass(frozen=True)
class TaskProfile:
    name: str
    package: str
    milestones: tuple[Milestone, ...]
    preferred_channels: tuple[str, ...]
    terminal_constraints: tuple[str, ...]

    def snapshot(self) -> dict[str, object]:
        return {
            "name": self.name,
            "package": self.package,
            "preferred_channels": list(self.preferred_channels),
            "terminal_constraints": list(self.terminal_constraints),
            "milestones": [asdict(item) for item in self.milestones],
        }

    def planner_instruction(self) -> str:
        steps = " → ".join(item.name for item in self.milestones)
        constraints = "；".join(self.terminal_constraints)
        return f"任务里程碑：{steps}。终止约束：{constraints}。"


PROFILES = (
    TaskProfile(
        "wolt_restaurant_research",
        "com.wolt.android",
        (
            Milestone("进入餐厅分类", ("Wolt 分类或餐厅列表可见",)),
            Milestone("选择高评分候选", ("店名和评分同时可见",)),
            Milestone("读取店铺详情", ("店名", "评分", "地址", "营业状态")),
        ),
        ("existing_ui", "location_already_granted", "visual_gui"),
        ("禁止搜索框和系统输入法", "禁止下单、购物车和支付"),
    ),
    TaskProfile(
        "qq_single_message",
        "com.tencent.mobileqq",
        (
            Milestone("打开系统分享页", ("QQ 分享目标页可见",)),
            Milestone("匹配完整联系人", ("完整联系人名称可见",)),
            Milestone("核对原文", ("完整消息原文可见",)),
            Milestone("用户批准发送", ("通知确认状态=approved",), replay_safe=False),
            Milestone("发送并验证", ("会话出现原文气泡",), replay_safe=False),
        ),
        ("android_send_intent", "existing_ui", "notification_confirmation"),
        ("禁止聊天输入框和系统输入法", "发送动作不可重放", "只发送一次"),
    ),
    TaskProfile(
        "netease_exact_playback",
        "com.netease.cloudmusic",
        (
            Milestone("解析精确歌曲", ("官方歌曲 ID",)),
            Milestone("打开歌曲深链", ("目标歌曲页可见",)),
            Milestone("开始播放", ("MediaSession 标题匹配", "播放状态=PLAYING"), replay_safe=False),
        ),
        ("official_deep_link", "existing_ui", "media_session"),
        ("禁止搜索框和系统输入法", "播放成功后立即停止规划"),
    ),
    TaskProfile(
        "huawei_alarm",
        "com.huawei.deskclock",
        (
            Milestone("进入新建闹钟", ("时间滚轮可见",)),
            Milestone("调整目标时间", ("目标小时", "目标分钟", "上午或下午")),
            Milestone("保存闹钟", ("闹钟列表出现目标时间", "闹钟已开启"), replay_safe=False),
        ),
        ("existing_ui", "display_scoped_gesture", "system_state_probe"),
        ("每次只调整一个滚轮", "保存成功后不可重复创建"),
    ),
)


def resolve_task_profile(package: str, goal: str) -> TaskProfile | None:
    for profile in PROFILES:
        if profile.package != package:
            continue
        if profile.name == "qq_single_message" and not any(term in goal for term in ("发消息", "发送消息", "发给", "告诉")):
            return None
        if profile.name == "netease_exact_playback" and "播放" not in goal:
            return None
        return profile
    return None
