from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bearbless.agent.contracts import CompletionProbe
from bearbless.agent.state import TaskState
from bearbless.runtime.adb import AdbClient
from bearbless.runtime.media_session import MediaSessionCompletionProbe
from bearbless.schemas import VerificationResult


@dataclass(frozen=True)
class AgentSkill:
    """A thin semantic specialization built on the common GUI runtime."""

    name: str
    match_terms: tuple[str, ...]
    instruction: str
    probe_factory: Callable[[AdbClient], CompletionProbe] | None = None

    def matches(self, goal: str) -> bool:
        return not self.match_terms or any(term in goal for term in self.match_terms)


class CompletionProbeChain:
    def __init__(self, probes: list[CompletionProbe]) -> None:
        self.probes = probes

    def verify(self, state: TaskState) -> VerificationResult | None:
        for probe in self.probes:
            result = probe.verify(state)
            if result is not None:
                return result
        return None


class SkillRegistry:
    """Select capabilities by task semantics, never by a hard-coded app route."""

    def __init__(self, adb: AdbClient) -> None:
        self.adb = adb
        self.skills = (
            AgentSkill(
                "general_gui",
                (),
                "每一步重新观察；只操作当前画面可证明的目标；执行后验证页面变化。",
            ),
            AgentSkill(
                "media_playback",
                ("播放", "歌曲", "音乐"),
                "若任务只要求搜索则不得播放；若明确要求播放，在播放器进入目标曲目播放状态后立即停止，不要返回搜索页。",
                MediaSessionCompletionProbe,
            ),
            AgentSkill(
                "alarm_management",
                ("闹钟",),
                "只查看闹钟时不得修改；设置闹钟时分别调整日期、上午/下午、小时和分钟，确认后必须看到列表中的目标时间。",
            ),
            AgentSkill(
                "message_send",
                ("QQ", "发消息", "发送消息"),
                "只查看 QQ 时不得发送；发送任务只允许给契约中的联系人发送原文一次，登录或验证码必须交给用户。",
            ),
            AgentSkill(
                "restaurant_research",
                ("美团", "餐厅", "外卖"),
                "餐饮任务先确认地点和搜索词；只读取、筛选和比较，不得进入下单或支付流程。",
            ),
            AgentSkill(
                "map_navigation",
                ("地图", "导航", "路线"),
                "地图任务先确认目的地候选与当前地点；只有任务明确要求导航时才开始路线引导，看到路线概览或导航已开始后立即结束。",
            ),
        )

    def resolve(self, goal: str) -> list[AgentSkill]:
        return [skill for skill in self.skills if skill.matches(goal)]

    def completion_probe(self, selected: list[AgentSkill]) -> CompletionProbeChain:
        probes = [skill.probe_factory(self.adb) for skill in selected if skill.probe_factory]
        return CompletionProbeChain(probes)
