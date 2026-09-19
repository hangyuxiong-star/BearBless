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
                "youtube_search",
                ("YouTube", "Youtube", "youtube", "youtobe", "油管"),
                "搜索任务优先使用搜索结果深链，禁止聚焦输入框；看到与查询词匹配的视频结果后立即完成。",
            ),
            AgentSkill(
                "alarm_management",
                ("闹钟",),
                "只查看闹钟时不得修改；设置闹钟时分别调整日期、上午/下午、小时和分钟，确认后必须看到列表中的目标时间。"
                "华为时钟的新建闹钟页是三列独立滚轮：上午/下午、小时、分钟；必须在可见数字所在列的中心短滑，"
                "严禁在两列之间滑动。对 1080 宽截图，列中心通常约为 x=180、460、735（归一化约 167、426、681）；"
                "手势起点应位于蓝色选中行附近，垂直移动约屏高 5%–10%，每次只调整一列并重新观察。",
            ),
            AgentSkill(
                "message_send",
                ("QQ", "qq", "发消息", "发送消息", "发信息", "发送信息", "草稿"),
                "QQ 默认只编辑契约指定联系人和原文的消息草稿并停留在输入框，禁止点击发送；登录或验证码必须交给用户。"
                "华为双开选择器若显示左侧无角标 QQ 与右侧带蓝色‘2’角标 QQ，默认点击左侧无角标的机主 QQ；"
                "这只是选择契约已指定应用的主用户实例，不需要 TAKE_OVER。若用户明确要求 QQ 分身才选择右侧。"
                "若最近会话列表已经出现契约中的完整联系人名称，直接进入该会话，禁止打开搜索框。"
                "敏感发送任务优先使用运行时已启动的 Android ACTION_SEND 分享页：选择契约中的完整联系人，"
                "在 QQ 确认页核对联系人和完整原文，然后只点击一次发送；此路径禁止 TYPE、禁止点击聊天输入框。"
                "仅草稿任务才可在确认输入框可安全访问时使用 TYPE。",
            ),
            AgentSkill(
                "restaurant_research",
                ("美团", "Wolt", "wolt", "WOLT", "餐厅", "外卖", "汉堡"),
                "餐饮任务只读取、筛选和比较，不得进入下单、购物车或支付流程。"
                "Wolt 任务优先从首页可见的 Restaurants、汉堡商家卡片或汉堡分类进入；"
                "若首页已经出现 Burger King 等明确汉堡商家，直接浏览该卡片并报告，禁止点击 Search 或聚焦输入框，"
                "因为 Android 系统输入法可能显示在用户主屏。Wolt 页面出现 Share location 时默认选择 Share location；"
                "这只授权使用 Wolt 已具备的定位能力。若随后出现 Android 系统级定位权限弹窗，不得代替用户新增授权，"
                "必须返回 TAKE_OVER 请求用户决定；不得修改保存地址。",
            ),
        )

    def resolve(self, goal: str) -> list[AgentSkill]:
        return [skill for skill in self.skills if skill.matches(goal)]

    def completion_probe(self, selected: list[AgentSkill]) -> CompletionProbeChain:
        probes = [skill.probe_factory(self.adb) for skill in selected if skill.probe_factory]
        return CompletionProbeChain(probes)
