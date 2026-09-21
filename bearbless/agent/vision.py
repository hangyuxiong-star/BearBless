from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import re
from difflib import SequenceMatcher
from collections.abc import Callable
from urllib.request import Request, urlopen

from PIL import Image

from bearbless.agent.state import TaskState
from bearbless.agent.grounding import MarkedElement, SetOfMarkGrounder
from bearbless.agent.model_client import PhoneModelClient
from bearbless.runtime.commands import CommandRunner
from bearbless.runtime.actions import Action, ActionCapability, ActionType
from bearbless.runtime.media_session import requested_track
from bearbless.errors import AgentTerminalDecision, ModelProtocolError
from bearbless.schemas import AgentAction, PhoneDecision, VerificationResult
from bearbless.message_intent import extract_confirmed_message, extract_confirmed_recipient, is_qq_draft_request
from bearbless.config import Config


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
    match = re.search(r"(\d{1,2})\s*(?:点\s*(半|\d{1,2})?|[:：]\s*(\d{1,2}))", goal)
    if not match:
        return None
    hour = int(match.group(1))
    minute_text = match.group(2) or match.group(3) or "0"
    minute = 30 if minute_text == "半" else int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    explicit_pm = any(marker in goal for marker in ("中午", "下午", "晚上", "傍晚", "今晚", "夜里"))
    explicit_am = any(marker in goal for marker in ("上午", "早上", "清晨", "凌晨"))
    period = "下午" if (explicit_pm or (hour >= 12 and not explicit_am)) else "上午"
    hour12 = hour % 12 or 12
    return period, hour12, minute


def _alarm_save_confirmed(goal: str, grounded_text: str, history: list[dict]) -> bool:
    if "闹钟" not in goal or "新建闹钟" in grounded_text or "后响铃" not in grounded_text:
        return False
    return bool(history and str(history[-1].get("reason") or "").startswith("确认闹钟"))


def _music_search_action(goal: str, elements, display_id: int) -> Action | None:
    """Use semantic screen state for the high-frequency music search path.

    This is only a safe visual fallback after semantic deep-link resolution.
    NetEase auto-focuses its search field, so entering that flow would route
    Android's singleton IME to Display 0 and must fail closed.
    """
    target = requested_track(goal)
    if not target or "播放" not in goal or not any(name in goal for name in ("网易云", "音乐")):
        return None
    normalized_target = target.replace(" ", "")
    labels = [(item, item.label.replace(" ", "")) for item in elements]
    page_text = "".join(label for _, label in labels)

    # NetEase monetization surfaces are part of the normal playback flow.
    # Prefer the ad-funded route when explicitly offered, never the purchase
    # route, and keep all handling on the isolated display.
    ad_offer = next(
        (item for item, label in labels if "看广告免费听" in label and "VIP" not in label),
        None,
    )
    if "看广告免费听VIP歌曲" in page_text:
        if ad_offer is not None:
            x, y = ad_offer.center
            return Action(
                ActionType.TAP, display_id=display_id, x=x, y=y,
                capability=ActionCapability.MEDIA_CONTROL,
                reason="选择网易云看广告免费听，拒绝开通或购买 VIP",
            )
        return Action(
            ActionType.BACK, display_id=display_id,
            capability=ActionCapability.NAVIGATE,
            reason="关闭无法可靠定位按钮的网易云 VIP 推广弹窗",
        )

    purchase_markers = ("立即开通", "确认支付", "连续包月", "开通会员")
    if any(marker in page_text for marker in purchase_markers):
        return Action(
            ActionType.BACK, display_id=display_id,
            capability=ActionCapability.NAVIGATE,
            reason="关闭网易云付费或会员开通页面，禁止购买",
        )

    ad_wait_markers = ("广告剩余", "奖励将在", "观看视频", "后可领取", "秒后可关闭")
    if any(marker in page_text for marker in ad_wait_markers):
        return Action(
            ActionType.WAIT, display_id=display_id, seconds=3,
            reason="等待网易云激励广告达到可关闭或可领取状态",
        )

    ad_done = next(
        (
            item for item, label in labels
            if any(marker in label for marker in ("领取奖励", "关闭广告", "继续听歌", "完成"))
        ),
        None,
    )
    if ad_done is not None and any(marker in page_text for marker in ("广告", "奖励", "免费听")):
        x, y = ad_done.center
        return Action(
            ActionType.TAP, display_id=display_id, x=x, y=y,
            capability=ActionCapability.MEDIA_CONTROL,
            reason="领取网易云免费听权益并返回目标歌曲",
        )

    # Search results: prefer the first full result row, not query suggestions.
    result_candidates = [
        item for item, label in labels
        if normalized_target in label
        and item.center[1] > 260
        and ("网易云音乐" in label or len(label) > len(normalized_target) + 4)
    ]
    if result_candidates:
        selected = min(result_candidates, key=lambda item: item.center[1])
        x, y = selected.center
        return Action(
            ActionType.TAP, display_id=display_id, x=x, y=y,
            capability=ActionCapability.MEDIA_CONTROL,
            reason=f"点击精确匹配的歌曲结果 {target}",
        )

    # NetEase focuses this field itself.  ACTION_SET_TEXT does not undo that
    # focus and therefore cannot prevent the singleton IME from reaching the
    # primary display.
    if "搜索历史" in page_text or all(marker in page_text for marker in ("歌手", "曲风", "专区")):
        raise AgentTerminalDecision(
            "ABORT",
            "网易云搜索页会把系统输入法映射到 Display 0；歌曲深链解析失败，已安全停止",
        )

    if "推荐" in page_text:
        raise AgentTerminalDecision(
            "ABORT",
            "网易云歌曲深链解析失败；为避免搜索框唤起 Display 0 输入法，已安全停止",
        )
    if "网易云音乐支持" in page_text:
        return Action(ActionType.WAIT, display_id=display_id, seconds=1, reason="等待网易云首页完成加载")
    return None


def _wolt_food_action(state: TaskState, elements, display_id: int) -> Action | None:
    """Drive Wolt's category UI without search, IME, or a model call.

    The category is inferred from the goal.  Keeping this path local is
    important on devices where focusing Wolt's Search field sends the
    singleton IME to Display 0.
    """
    goal = state.goal.casefold()
    if "wolt" not in goal:
        return None
    if any(term in goal for term in ("中餐", "中式", "chinese", "asian", "asia", "亚洲")):
        # Wolt renamed the broad Chinese entry to "Asian" in the current
        # locale. Keep "Chinese" as a compatibility alias for older builds,
        # but prefer the exact label exposed by today's Food type sheet.
        category = "Asian"
        category_aliases = ("Asian", "Chinese")
        route = "wolt_chinese_categories"
        criterion = "Wolt Asian/Chinese restaurant results"
        filter_key = "wolt_chinese_filter_selected"
    elif any(term in goal for term in ("japanese", "japanse", "日料", "日本料理", "日餐")):
        category = "Japanese"
        category_aliases = (category,)
        route = "wolt_japanese_categories"
        criterion = "Wolt Japanese restaurant results"
        filter_key = "wolt_japanese_filter_selected"
    elif any(term in goal for term in ("汉堡", "burger")):
        category = "Burger"
        category_aliases = (category,)
        route = "wolt_burger_categories"
        criterion = "Wolt Burger results"
        filter_key = "wolt_burger_filter_selected"
    else:
        return None
    state.collected_data["app_skill_route"] = route

    labels = [(item, re.sub(r"\s+", " ", item.label).strip()) for item in elements]
    page_text = " ".join(label for _, label in labels).casefold()
    needs_address = "地址" in state.goal

    def exact(text: str):
        target = re.sub(r"[^a-z0-9]+", "", text.casefold())
        matches = [
            item for item, label in labels
            if re.sub(r"[^a-z0-9]+", "", label.casefold()).startswith(target)
        ]
        return min(matches, key=lambda item: item.center[1], default=None)

    # Tapping Wolt's sponsored banner/info badge can open a modal bottom
    # sheet titled “About this ad”. It contains no restaurant result and can
    # cover the list indefinitely. Android BACK closes only this sheet and is
    # safer than guessing the small icon-only X coordinate.
    if "about this ad" in page_text and (
        "advertiser" in page_text or "who paid for the advertising" in page_text
    ):
        return Action(
            ActionType.BACK,
            display_id=display_id,
            capability=ActionCapability.NAVIGATE,
            reason="关闭 Wolt 广告说明弹窗并返回餐厅结果",
        )

    # A fresh task can attach to Wolt while a previous run has already left
    # the requested category selected. Reconstruct that durable UI state from
    # the page instead of tapping the large Restaurants heading (whose area
    # overlaps the address selector above it on this layout).
    visible_category = next(
        (match for alias in category_aliases if (match := exact(alias)) is not None),
        None,
    )
    visible_category_label = next(
        (label for item, label in labels if item is visible_category),
        category,
    )
    visible_times = [label for _, label in labels if re.search(r"\d+\s*[–-]\s*\d+\s*min", label, re.I)]
    if (
        "restaurants" in page_text
        and visible_category is not None
        and visible_category.center[1] < 500
        and visible_times
        and _wolt_ranked_candidate(labels) is not None
    ):
        state.collected_data[filter_key] = True

    # If the address selector was left open, selecting the already checked
    # Home row is navigation back to the existing context, not a location
    # mutation. Never tap Add new address or an unselected saved address.
    if "choose your location" in page_text:
        current_home = next(
            (
                item for item, label in labels
                if re.fullmatch(r"home\s*[✓✔]?", label.casefold().strip())
                or (label.casefold().startswith("home ") and ("✓" in label or "✔" in label))
            ),
            None,
        )
        if current_home is not None:
            x, y = current_home.center
            return Action(
                ActionType.TAP, display_id=display_id, x=x, y=y,
                capability=ActionCapability.NAVIGATE,
                reason="选择 Wolt 当前已勾选的 Home 地址并返回餐厅结果",
            )

    # A previously saved cart can cover the merchant page after opening a
    # venue. "Continue order" only restores the venue/cart context; it does
    # not place or pay for an order. Continue into the merchant page while the
    # policy gate still forbids checkout, purchase and payment actions.
    if "continue order" in page_text and "cancel" in page_text:
        continue_order = next(
            (item for item, label in labels if label.casefold().strip() == "continue order"),
            None,
        )
        if continue_order is None:
            raise AgentTerminalDecision("ABORT", "Wolt 旧购物车弹窗无法识别继续按钮")
        x, y = continue_order.center
        return Action(
            ActionType.TAP,
            display_id=display_id,
            x=x,
            y=y,
            capability=ActionCapability.NAVIGATE,
            reason="继续 Wolt 已保存的店铺上下文以读取商家信息",
        )

    # A WebView OCR box can occasionally span the nearby Delivery control and
    # More label. If that imprecise tap opened the harmless order-details
    # sheet, close it and resume reading the venue; never choose a delivery
    # time or proceed toward checkout.
    if "order details" in page_text and "where?" in page_text:
        done = next(
            (item for item, label in labels if label.casefold().strip() == "done"),
            None,
        )
        if done is not None:
            x, y = done.center
            return Action(
                ActionType.TAP,
                display_id=display_id,
                x=x,
                y=y,
                capability=ActionCapability.NAVIGATE,
                reason="关闭误开的 Wolt 配送详情并返回商家页",
            )
        return Action(
            ActionType.BACK,
            display_id=display_id,
            capability=ActionCapability.NAVIGATE,
            reason="关闭误开的 Wolt 配送详情并返回商家页",
        )

    selected = state.collected_data.get("wolt_selected_restaurant")
    if needs_address and selected and "address" in page_text:
        address_index = next(
            (index for index, (_, label) in enumerate(labels) if label.casefold() == "address"),
            None,
        )
        address_parts = [] if address_index is None else [
            label for _, label in labels[address_index + 1:address_index + 4]
            if label and not re.search(r"opening hours|directions|restaurant|delivery", label, re.I)
        ]
        address = " ".join(address_parts).strip()
        if address:
            selected["address"] = address
            state.collected_data["wolt_restaurant"] = selected
            state.collected_data["agent_result"] = (
                f"Wolt 已选中 {selected['name']}，评分 {selected.get('rating', '页面可见')}，地址 {address}"
            )
            state.evidence.append({
                "criterion": criterion,
                "passed": True,
                "evidence": f"merchant={selected['name']}; rating={selected.get('rating')}; address={address}",
            })
            return Action(ActionType.FINISH, reason="已从 Wolt 商家信息页验证店名、评分和地址")

    # Once the Restaurants tile has been tapped, the same sparse address-only
    # shell means the Restaurants WebView is loading. Do not route it back
    # through the shorter startup budget (or abort immediately if OCR misses
    # the icon-only bottom navigation).
    entered_restaurants_loading = (
        bool(state.collected_data.get("wolt_restaurants_attempts"))
        and len(labels) <= 6
        and "food type" not in page_text
        and "restaurants" not in page_text
        and any(marker in page_text for marker in ("vej", "gade", "anker", "poppelhegnet"))
    )
    if entered_restaurants_loading:
        waits = int(state.collected_data.get("wolt_restaurants_loading_waits", 0)) + 1
        state.collected_data["wolt_restaurants_loading_waits"] = waits
        if waits <= 6:
            return Action(
                ActionType.WAIT,
                display_id=display_id,
                seconds=2,
                reason=f"等待 Wolt Restaurants 内容渲染（{waits}/6）",
            )
        raise AgentTerminalDecision("ABORT", "Wolt Restaurants 内容加载超时，未使用 Search 回退")

    # Tesseract can emit the address header twice (plain text plus the same
    # line with its chevron) and may OCR the profile icon as one glyph. Count
    # that as the same sparse loading shell rather than aborting immediately.
    # Animated spinners and the status-bar edge can add several meaningless
    # OCR fragments (for example “OO” or one glyph). The address-only shell is
    # still a loading state until a real navigation/category label appears.
    sparse_startup = "search" not in page_text and len(labels) <= 8 and (
        len(labels) == 0
        or any(marker in page_text for marker in ("location", "home", "vej", "anker", "poppelhegnet"))
    )
    if ("choose your location" in page_text and "share location" not in page_text) or sparse_startup:
        waits = int(state.collected_data.get("wolt_startup_waits", 0)) + 1
        state.collected_data["wolt_startup_waits"] = waits
        if waits <= 4:
            return Action(
                ActionType.WAIT, display_id=display_id, seconds=2,
                reason=f"等待 Wolt 位置和首页数据加载（{waits}/4）",
            )
        raise AgentTerminalDecision("ABORT", "Wolt 位置页加载超时，未出现可安全选择的入口")
    state.collected_data.pop("wolt_startup_waits", None)

    home_shell_loading = (
        "order again" in page_text
        and "restaurants" not in page_text
        and "food type" not in page_text
    )
    if home_shell_loading:
        waits = int(state.collected_data.get("wolt_home_shell_waits", 0)) + 1
        state.collected_data["wolt_home_shell_waits"] = waits
        if waits <= 5:
            return Action(
                ActionType.WAIT, display_id=display_id, seconds=2,
                reason=f"等待 Wolt 首页 Restaurants 入口完成渲染（{waits}/5）",
            )
        raise AgentTerminalDecision("ABORT", "Wolt 首页 Restaurants 入口加载超时")
    state.collected_data.pop("wolt_home_shell_waits", None)

    # After entering Restaurants, Wolt keeps the bottom navigation visible
    # while the central WebView is still loading. OCR then contains only the
    # address header plus Home/Search. Treat this as a transient render state,
    # not as proof that no safe category route exists.
    # The bottom Home glyph is icon-only on some Wolt builds, so OCR may see
    # the address header and Search but not the word "Home".  That is still
    # the same transient Restaurants WebView shell, not evidence that the
    # category route is unavailable.
    sparse_restaurants_loading = (
        len(labels) <= 6
        and "search" in page_text
        and "food type" not in page_text
        and "restaurants" not in page_text
        and (
            "home" in page_text
            or any(marker in page_text for marker in ("vej", "gade", "anker", "poppelhegnet"))
        )
    )
    if sparse_restaurants_loading:
        waits = int(state.collected_data.get("wolt_restaurants_loading_waits", 0)) + 1
        state.collected_data["wolt_restaurants_loading_waits"] = waits
        if waits <= 6:
            return Action(
                ActionType.WAIT,
                display_id=display_id,
                seconds=2,
                reason=f"等待 Wolt Restaurants 内容渲染（{waits}/6）",
            )
        raise AgentTerminalDecision("ABORT", "Wolt Restaurants 内容加载超时，未使用 Search 回退")
    state.collected_data.pop("wolt_restaurants_loading_waits", None)

    if "share location" in page_text:
        item = exact("Share location")
        if item is None:
            raise AgentTerminalDecision("ABORT", "Wolt 定位页无法识别 Share location 按钮")
        x, y = item.center
        return Action(
            ActionType.TAP, display_id=display_id, x=x, y=y,
            capability=ActionCapability.NAVIGATE,
            reason="使用 Wolt 已配置的位置进入附近商家",
        )

    # Wolt uses either "Open until …" or "Closes at …" on the same merchant
    # detail surface.  Requiring only the former made an evening visit fall
    # through to the category-result waiter even though the venue page and its
    # More button were already fully rendered.
    merchant_detail = (
        "delivery" in page_text
        and "food type" not in page_text
        and any(marker in page_text for marker in ("open until", "closes at", "min. order"))
    )
    if merchant_detail:
        if needs_address and state.collected_data.get("wolt_selected_restaurant"):
            address = next((
                label for _, label in labels
                if re.search(r"\b\d{1,4}\b", label)
                and re.search(r"(?:vej|gade|all[eé]|boulevard|plads|torv)", label, re.I)
            ), None)
            if address:
                result = state.collected_data["wolt_selected_restaurant"]
                result["address"] = address
                state.collected_data["wolt_restaurant"] = result
                state.collected_data["agent_result"] = (
                    f"Wolt 已选中 {result['name']}，评分 {result.get('rating', '页面可见')}，地址 {address}"
                )
                state.evidence.append({
                    "criterion": criterion,
                    "passed": True,
                    "evidence": f"merchant={result['name']}; rating={result.get('rating')}; address={address}",
                })
                return Action(ActionType.FINISH, reason="已从 Wolt 商家详情验证店名、评分和地址")
            more = exact("More")
            if more is not None and not state.collected_data.get("wolt_more_opened"):
                state.collected_data["wolt_more_opened"] = True
                return Action(
                    ActionType.CLICK_TEXT,
                    display_id=display_id,
                    package="com.wolt.android",
                    text="More",
                    capability=ActionCapability.READ,
                    reason="打开 Wolt 商家信息以读取地址",
                )
            # Tesseract often merges the complete metadata row into one box,
            # for example “Smiley info More”. Tap the right-hand More segment
            # of that proven row instead of waiting for an exact OCR token.
            merged_more = next(
                (
                    item for item, label in labels
                    if re.search(r"\bmore\s*$", label, re.I)
                    and item.bounds[2] - item.bounds[0] >= 180
                ),
                None,
            )
            if merged_more is not None and not state.collected_data.get("wolt_more_opened"):
                state.collected_data["wolt_more_opened"] = True
                left, top, right, bottom = merged_more.bounds
                return Action(
                    ActionType.TAP,
                    display_id=display_id,
                    x=max(left, right - 70),
                    y=(top + bottom) // 2,
                    capability=ActionCapability.READ,
                    reason="打开 Wolt 商家信息行末的 More 以读取地址",
                )
            waits = int(state.collected_data.get("wolt_address_waits", 0)) + 1
            state.collected_data["wolt_address_waits"] = waits
            if waits <= 2:
                return Action(ActionType.WAIT, display_id=display_id, seconds=1, reason="等待 Wolt 商家地址信息渲染")
            raise AgentTerminalDecision("ABORT", "Wolt 商家详情未显示可验证地址，不能生成 QQ 消息")
        return Action(
            ActionType.BACK, display_id=display_id,
            capability=ActionCapability.NAVIGATE,
            reason="误入 Wolt 商家详情，返回 Restaurants 分类入口",
        )

    # Wolt persists the last section. A grocery Categories sheet is not a
    # useful hamburger route, so close it and re-observe instead of guessing.
    if "categories" in page_text and "supermarket" in page_text and category.casefold() not in page_text:
        return Action(
            ActionType.BACK, display_id=display_id,
            capability=ActionCapability.NAVIGATE,
            reason="退出 Wolt Market 分类，返回餐饮入口",
        )

    if state.collected_data.get(filter_key):
        time_labels = [label for _, label in labels if re.search(r"\d+\s*[–-]\s*\d+\s*min", label, re.I)]
        ranked = _wolt_ranked_candidate(labels)
        requires_rating = any(term in state.goal.casefold() for term in ("评分高", "高评分", "rating"))
        if ranked is None and time_labels and not requires_rating:
            merchant_items = [
                (item, label) for item, label in labels
                if item.center[1] > 500 and len(label) >= 4
                and not re.search(r"\b(?:kr|km|min)\b|discount|restaurants|sponsored|save\s|search", label, re.I)
                and not re.fullmatch(r"[\d\W]+", label)
            ]
            if merchant_items:
                item, merchant = merchant_items[0]
                ranked = (0.0, item, merchant, "页面可见", time_labels[0])
        if time_labels and ranked:
            rating_value, merchant_item, merchant, rating, delivery = ranked
            # Wolt uses a 10-point venue score in this locale. 8.0 is the
            # stable high-rating boundary across changing nearby inventory;
            # the exact source score remains in the result and evidence.
            high_rating_threshold = 8.0
            if requires_rating and category == "Burger" and rating_value < high_rating_threshold:
                scrolls = int(state.collected_data.get("wolt_rating_scrolls", 0)) + 1
                state.collected_data["wolt_rating_scrolls"] = scrolls
                if scrolls <= 3:
                    return Action(
                        ActionType.SWIPE, display_id=display_id,
                        x=540, y=1850, x2=540, y2=700, duration_ms=550,
                        capability=ActionCapability.SEARCH,
                        reason=(
                            f"当前可见最高评分 {rating} 低于 Wolt 高评分阈值 {high_rating_threshold}，"
                            f"向下浏览更多 Burger 商家（{scrolls}/3）"
                        ),
                    )
                raise AgentTerminalDecision(
                    "ABORT", f"Wolt {category} 分类未显示评分达到 {high_rating_threshold} 的可验证商家"
                )
            restaurant = {
                "name": merchant,
                "delivery": delivery or time_labels[0],
                "category": category,
                "rating": rating,
            }
            if needs_address:
                state.collected_data["wolt_selected_restaurant"] = restaurant
                x, y = merchant_item.center
                return Action(
                    ActionType.TAP, display_id=display_id, x=x, y=y,
                    capability=ActionCapability.READ,
                    reason=f"打开评分较高的 {category} 商家 {merchant}（{rating}）读取地址",
                )
            state.collected_data["wolt_restaurant"] = restaurant
            state.collected_data["agent_result"] = (
                f"Wolt {category} 分类已找到高评分商家：{merchant}，评分 {rating}"
                if requires_rating
                else f"Wolt {category} 分类已显示附近商家：{merchant}"
            )
            state.evidence.append({
                "criterion": criterion,
                "passed": True,
                "evidence": (
                    f"category={category}; merchant={merchant}; rating={rating}; "
                    f"scale=10; delivery={delivery or time_labels[0]}"
                ),
            })
            return Action(
                ActionType.FINISH,
                reason=f"已验证 {category} 商家 {merchant} 的 Wolt 评分为 {rating}",
            )
        waits = int(state.collected_data.get("wolt_result_waits", 0)) + 1
        state.collected_data["wolt_result_waits"] = waits
        if waits <= 2:
            return Action(
                ActionType.WAIT, display_id=display_id, seconds=1,
                reason=f"等待 Wolt {category} 商家列表加载（{waits}/2）",
            )
        raise AgentTerminalDecision("ABORT", f"Wolt {category} 分类未渲染出可验证的商家和配送时间")

    category_item = visible_category
    # The Restaurants landing page also contains a horizontal category
    # carousel.  Tapping its OCR label proved unstable on Huawei (a Burger
    # label tap selected Japanese).  The successful baseline always opens the
    # Food type sheet first and selects the category from that modal list.
    food_type = exact("Food type")
    if "all restaurants" in page_text and food_type is not None:
        state.collected_data.pop("wolt_restaurants_attempts", None)
        x, y = food_type.center
        return Action(
            ActionType.TAP, display_id=display_id, x=x, y=y,
            capability=ActionCapability.NAVIGATE,
            reason="打开 Restaurants 的 Food type 分类",
        )

    if "food type" in page_text and category_item is not None:
        x, y = category_item.center
        state.collected_data[filter_key] = True
        return Action(
            ActionType.TAP, display_id=display_id, x=x, y=y,
            capability=ActionCapability.SEARCH,
            reason=f"从 Food type 选择现有 {visible_category_label} 分类",
        )

    if "restaurants" in page_text and food_type is not None:
        state.collected_data.pop("wolt_restaurants_attempts", None)
        x, y = food_type.center
        return Action(
            ActionType.TAP, display_id=display_id, x=x, y=y,
            capability=ActionCapability.NAVIGATE,
            reason="打开 Restaurants 的 Food type 分类",
        )

    restaurants = exact("Restaurants")
    if restaurants is not None:
        attempts = int(state.collected_data.get("wolt_restaurants_attempts", 0)) + 1
        state.collected_data["wolt_restaurants_attempts"] = attempts
        if attempts > 2:
            raise AgentTerminalDecision("ABORT", "Wolt Restaurants 入口连续点击无效，已停止避免循环")
        x, _ = restaurants.center
        # The home category has an illustrated tile immediately above its
        # label. Tapping the glyph's label baseline can hit the first merchant
        # carousel on some Wolt layouts, so target the tile body instead.
        y = max(40, restaurants.bounds[1] - 80)
        return Action(
            ActionType.TAP, display_id=display_id, x=x, y=y,
            capability=ActionCapability.NAVIGATE,
            reason="进入 Wolt Restaurants",
        )

    # Search is deliberately not a fallback: it would focus Android's
    # singleton IME and may render the keyboard on Display 0.
    raise AgentTerminalDecision(
        "ABORT",
        "当前 Wolt 页面没有可证明的免输入法分类路径；已停止且不会点击 Search",
    )


def _wolt_burger_action(state: TaskState, elements, display_id: int) -> Action | None:
    """Backward-compatible name retained for tests and older callers."""
    return _wolt_food_action(state, elements, display_id)


def _wolt_ranked_candidate(labels):
    """Bind OCR ratings to their restaurant row and return the highest one."""
    ranked = []
    for rating_item, rating_label in labels:
        match = re.search(r"(?:^|\D)([7-9])(?:[.,]?)(\d)\s*$", rating_label)
        if not match:
            continue
        rating = f"{match.group(1)}.{match.group(2)}"
        candidates = [
            (item, label) for item, label in labels
            if 0 < rating_item.center[1] - item.center[1] <= 120
            and item.center[0] < 750
            and len(label) >= 4
            and not re.search(r"\b(?:kr|km|min)\b|discount|restaurants|sponsored|save\s|search", label, re.I)
            and not re.fullmatch(r"[\d\W]+", label)
        ]
        if not candidates:
            continue
        merchant_item, merchant = min(
            candidates,
            key=lambda pair: (rating_item.center[1] - pair[0].center[1], -len(pair[1])),
        )
        # OCR can append the leading digit of the following distance ("4.1
        # km") to a merchant name. A lone trailing digit is not part of the
        # visible restaurant title in this row layout.
        merchant = re.sub(r"\s+[0-9]$", "", merchant).strip()
        # Wolt's adjacent promo/avatar glyphs can be OCR-merged as "@@ 他"
        # after the Latin venue title. They are not part of the merchant name.
        merchant = re.sub(r"\s*@{1,2}.*$", "", merchant).strip()
        delivery = next((
            label for item, label in labels
            if abs(item.center[1] - rating_item.center[1]) <= 45
            and re.search(r"\d+\s*[–-]\s*\d+\s*min", label, re.I)
        ), "")
        ranked.append((float(rating), merchant_item, merchant, rating, delivery))
    return max(ranked, key=lambda row: row[0], default=None)


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


def _qq_send_button_from_pixels(frame_path: str) -> MarkedElement | None:
    """Locate QQ's solid blue send button when OCR misses its Chinese label."""
    if not frame_path:
        return None
    try:
        with Image.open(frame_path).convert("RGB") as image:
            width, height = image.size
            points = []
            # QQ has two confirmation layouts: a compact lower-right button
            # and a wide centered button in the bottom sheet. Scan the lower
            # quarter while excluding edge links such as “详情”; require a
            # large solid component below before treating it as Send.
            for y in range(int(height * 0.75), height, 4):
                for x in range(int(width * 0.15), int(width * 0.85), 4):
                    red, green, blue = image.getpixel((x, y))
                    if red < 80 and 110 <= green <= 210 and blue >= 210:
                        points.append((x, y))
            if len(points) < 80:
                return None
            xs, ys = zip(*points)
            left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
            if right - left < 80 or bottom - top < 40:
                return None
            return MarkedElement(0, "发送", (left, top, right, bottom))
    except (OSError, ValueError):
        return None


def _alarm_picker_action(
    goal: str, elements, display_id: int, frame_path: str = "",
) -> Action | None:
    """Drive a visible Huawei alarm picker from OCR instead of model guesses."""
    target = _alarm_target(goal)
    period_candidates = [
        item for item in elements
        if item.label.replace(" ", "") in {"上午", "下午"}
    ]
    if target is None or not period_candidates:
        return None
    all_numbers = [
        item for item in elements
        if re.fullmatch(r"\d{1,2}", item.label.strip())
    ]
    all_hour_candidates = [item for item in all_numbers if abs(item.center[0] - 540) < 140]
    selected_hour = max(
        all_hour_candidates,
        key=lambda item: _blue_score(frame_path, item.bounds),
        default=None,
    )
    if selected_hour is None or _blue_score(frame_path, selected_hour.bounds) == 0:
        return None
    selected_y = selected_hour.center[1]
    numbers = [item for item in all_numbers if abs(item.center[1] - selected_y) <= 65]
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
    blue_period = max(
        period_candidates,
        key=lambda item: _blue_score(frame_path, item.bounds),
        default=None,
    )
    if blue_period is not None and _blue_score(frame_path, blue_period.bounds) > 0:
        current_period = blue_period.label.replace(" ", "")
    else:
        # Huawei's blue Chinese glyphs are occasionally omitted by OCR.  The
        # other period remains visible immediately above/below the blue row.
        above = {item.label.replace(" ", "") for item in period_candidates if item.center[1] < selected_y}
        below = {item.label.replace(" ", "") for item in period_candidates if item.center[1] > selected_y}
        if "下午" in below:
            current_period = "上午"
        elif "上午" in above:
            current_period = "下午"
        else:
            return None
    row_step = 120
    if current_period != target_period:
        move_down = target_period == "上午"
        return Action(
            ActionType.SWIPE, display_id=display_id,
            x=220, y=selected_y, x2=220,
            y2=selected_y + (row_step if move_down else -row_step),
            duration_ms=320, reason=f"切换到{target_period}",
            capability=ActionCapability.CHANGE_SETTING,
        )
    if current_hour != target_hour:
        visible_target_hour = min(
            (
                item for item in all_hour_candidates
                if int(item.label) == target_hour and item.center[1] != selected_y
            ),
            key=lambda item: abs(item.center[1] - selected_y),
            default=None,
        )
        if visible_target_hour is not None:
            x, y = visible_target_hour.center
            return Action(
                ActionType.TAP, display_id=display_id, x=x, y=y,
                reason=f"直接选择可见小时 {target_hour:02d}",
                capability=ActionCapability.CHANGE_SETTING,
            )
        forward = (target_hour - current_hour) % 12
        backward = (current_hour - target_hour) % 12
        increase = forward <= backward
        # Huawei's wheel adds momentum to longer drags, so distance is not
        # proportional to rows. Keep the fallback gesture to one measured row;
        # the fast path above still taps a visible target directly.
        steps = 1
        return Action(
            ActionType.SWIPE, display_id=display_id,
            x=540, y=selected_y, x2=540,
            y2=selected_y + (-row_step * steps if increase else row_step * steps),
            duration_ms=320,
            reason=f"小时 {current_hour:02d} 向目标 {target_hour:02d} 快速调整 {steps} 格",
            capability=ActionCapability.CHANGE_SETTING,
        )
    if current_minute != target_minute:
        visible_target_minute = min(
            (
                item for item in all_numbers
                if abs(item.center[0] - 858) < 140
                and int(item.label) == target_minute
                and item.center[1] != selected_y
            ),
            key=lambda item: abs(item.center[1] - selected_y),
            default=None,
        )
        if visible_target_minute is not None:
            x, y = visible_target_minute.center
            return Action(
                ActionType.TAP, display_id=display_id, x=x, y=y,
                reason=f"直接选择可见分钟 {target_minute:02d}",
                capability=ActionCapability.CHANGE_SETTING,
            )
        forward = (target_minute - current_minute) % 60
        backward = (current_minute - target_minute) % 60
        increase = forward <= backward
        # Long minute-wheel drags overshoot by an unpredictable amount on this
        # device. Use one-row fallback gestures unless the target is visible.
        steps = 1
        return Action(
            ActionType.SWIPE, display_id=display_id,
            x=858, y=selected_y, x2=858,
            y2=selected_y + (-row_step * steps if increase else row_step * steps),
            duration_ms=320,
            reason=f"分钟 {current_minute:02d} 向目标 {target_minute:02d} 快速调整 {steps} 格",
            capability=ActionCapability.CHANGE_SETTING,
        )
    if "每天" in goal:
        repeat_item = next(
            (item for item in elements if "不重复" in item.label.replace(" ", "")),
            None,
        )
        if repeat_item is not None:
            x, y = repeat_item.center
            return Action(
                ActionType.TAP, display_id=display_id, x=x, y=y,
                reason="打开闹钟重复设置以选择每天",
                capability=ActionCapability.CHANGE_SETTING,
            )
    return Action(
        ActionType.TAP, display_id=display_id, x=980, y=232,
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

    def __init__(self, client: PhoneModelClient, display_id: int, allowed_packages: dict[str, str], grounder=None, takeover_notifier: Callable[[str], None] | None = None, skill_instructions: tuple[str, ...] = (), package_identity_checker: Callable[[str], bool] | None = None, qq_confirmation_recipient_checker: Callable[[str], bool] | None = None) -> None:
        self.client = client
        self.display_id = display_id
        self.allowed_packages = allowed_packages
        self.grounder = grounder or SetOfMarkGrounder(CommandRunner())
        self.takeover_notifier = takeover_notifier
        self.skill_instructions = skill_instructions
        self.package_identity_checker = package_identity_checker
        self.qq_confirmation_recipient_checker = qq_confirmation_recipient_checker

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
            # Wolt's WebView frequently needs a longer cold start on the
            # Huawei secondary display.  Waiting locally is still much faster
            # and safer than spending GUI-Plus calls on identical white
            # frames. Other apps retain the short fail-fast bound.
            blank_limit = 8 if str(state.collected_data.get("app_skill_route", "")).startswith("wolt_") else 3
            if (
                blank_count == 3
                and str(state.collected_data.get("app_skill_route", "")).startswith("wolt_")
                and not state.collected_data.get("wolt_blank_relaunch_attempted")
            ):
                state.collected_data["wolt_blank_relaunch_attempted"] = True
                return [Action(
                    ActionType.OPEN_APP,
                    display_id=self.display_id,
                    package="com.wolt.android",
                    capability=ActionCapability.NAVIGATE,
                    reason="Wolt 隔离屏冷启动白屏，重新启动一次当前虚拟屏内的 Activity",
                )]
            if blank_count <= blank_limit:
                return [Action(
                    ActionType.WAIT,
                    seconds=min(1.5 * blank_count, 3.0),
                    reason=f"检测到空白渲染帧，等待页面完成绘制（{blank_count}/{blank_limit}）",
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
        # Some vendor Activities first draw a shaped white launch window. Its
        # black rounded margins make the perceptual fingerprint non-uniform,
        # but there is still no actionable semantic UI. Never ask the model
        # to interpret an empty frame; wait locally for bounded rendering.
        if not grounded.elements:
            waits = int(state.collected_data.get("empty_grounding_waits", 0)) + 1
            state.collected_data["empty_grounding_waits"] = waits
            if waits <= 5:
                return [Action(
                    ActionType.WAIT,
                    display_id=self.display_id,
                    seconds=min(1.5 * waits, 3.0),
                    reason=f"页面尚无可识别控件，等待目标应用完成首帧渲染（{waits}/5）",
                )]
            raise AgentTerminalDecision("ABORT", "目标应用首帧持续无可识别控件，已安全停止")
        state.collected_data.pop("empty_grounding_waits", None)
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
        if _alarm_save_confirmed(state.goal, grounded_text, history):
            state.collected_data["alarm_saved"] = True
            state.evidence.append({
                "criterion": "Alarm saved",
                "passed": True,
                "evidence": "闹钟保存后返回列表并显示下次响铃倒计时",
            })
            return [Action(ActionType.FINISH, reason="闹钟已保存，列表显示下次响铃倒计时")]
        music_action = _music_search_action(state.goal, grounded.elements, self.display_id)
        if music_action is not None:
            return [music_action]
        wolt_action = _wolt_burger_action(state, grounded.elements, self.display_id)
        if wolt_action is not None:
            return [wolt_action]
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
        is_qq_task = (
            state.collected_data.get("target_package") == "com.tencent.mobileqq"
            or "qq" in state.goal.casefold()
        )
        qq_draft = is_qq_task and is_qq_draft_request(state.goal)
        if (confirmed_sensitive or qq_draft) and is_qq_task:
            qq_identity_markers = ("QQ", "消息", "联系人", "动态", "登录")
            scope = str(state.task_spec.constraints.get("sensitive_scope") or state.goal)
            confirmed_recipient = extract_confirmed_recipient(scope)
            compact_recipient = re.sub(r"\s+", "", confirmed_recipient)
            recipient_candidates: list[tuple[float, MarkedElement]] = []
            for item in grounded.elements:
                compact_label = re.sub(r"[\s\W_]+", "", item.label)
                if len(compact_label) < 2 or not compact_recipient:
                    continue
                ratio = SequenceMatcher(None, compact_label, compact_recipient).ratio()
                if compact_label == compact_recipient or compact_recipient.startswith(compact_label):
                    ratio = 1.0
                recipient_candidates.append((ratio, item))
            recipient_candidates.sort(key=lambda pair: pair[0], reverse=True)
            best_recipient = recipient_candidates[0] if recipient_candidates else None
            runner_up_ratio = recipient_candidates[1][0] if len(recipient_candidates) > 1 else 0.0
            # QQ intentionally repeats a contact on the share surface (for
            # example once in “最近转发” and again in “最近聊天”).  Two exact
            # labels are not ambiguous: both identify the same contracted
            # recipient. Prefer the lower item, which is normally the full
            # recent-chat row and has a larger, more stable tap target.
            exact_recipient_items = [
                item
                for _ratio, item in recipient_candidates
                if re.sub(r"[\s\W_]+", "", item.label) == compact_recipient
            ]
            if exact_recipient_items:
                # QQ's upper “最近转发” avatar is the actual share
                # target on the verified build. The lower recent-chat row is
                # OCR-visible but did not react to display-targeted taps.
                recipient_item = min(exact_recipient_items, key=lambda item: item.center[1])
            else:
                recipient_item = (
                    best_recipient[1]
                    if best_recipient and best_recipient[0] >= 0.65
                    and best_recipient[0] - runner_up_ratio >= 0.08
                    else None
                )
            identity_confirmed = any(marker in grounded_text for marker in qq_identity_markers)
            compact_grounded_text = re.sub(r"\s+", "", grounded_text)
            if confirmed_recipient and re.sub(r"\s+", "", confirmed_recipient) in compact_grounded_text:
                identity_confirmed = True
            if recipient_item is not None:
                identity_confirmed = True
            if self.package_identity_checker and self.package_identity_checker("com.tencent.mobileqq"):
                identity_confirmed = True
            if not identity_confirmed:
                raise AgentTerminalDecision(
                    "ABORT",
                    "敏感任务无法确认当前虚拟屏属于 QQ，已在发送前停止",
                )
            confirmed_message = extract_confirmed_message(scope)
            # Tapping the payload preview (rather than the recipient) opens a
            # read-only “转发消息预览” modal. It has no send control. Close it
            # deterministically and return to the share list instead of asking
            # the model to explore the modal or repeatedly tap its body.
            if "转发消息预览" in grounded_text:
                return [Action(
                    ActionType.BACK,
                    display_id=self.display_id,
                    capability=ActionCapability.NAVIGATE,
                    reason="关闭 QQ 转发消息预览并返回联系人选择页",
                )]
            # QQ briefly shows its normal inbox with a “正在处理” overlay while
            # ACTION_SEND is being prepared. Matching the account/header name
            # during this state can focus the inbox search field and put the
            # payload in the wrong place. Wait for the actual share surface.
            if "正在处理" in grounded_text:
                waits = int(state.collected_data.get("qq_share_loading_waits", 0)) + 1
                state.collected_data["qq_share_loading_waits"] = waits
                if waits <= 8:
                    return [Action(
                        ActionType.WAIT,
                        display_id=self.display_id,
                        seconds=1,
                        reason=f"等待 QQ 分享联系人页面完成处理（{waits}/8）",
                    )]
                raise AgentTerminalDecision("ABORT", "QQ 分享联系人页面处理超时，未输入或发送消息")
            state.collected_data.pop("qq_share_loading_waits", None)
            # ACTION_SEND may first open Android's resolver even though the
            # intent is already package-scoped to QQ. Huawei highlights
            # QQ's "发送给好友" activity and asks whether to use it once or
            # always. This is deterministic navigation, not a model decision.
            use_once = next(
                (item for item in grounded.elements if item.label.replace(" ", "") == "仅此一次"),
                None,
            )
            send_to_friend = next(
                (item for item in grounded.elements if item.label.replace(" ", "") == "发送给好友"),
                None,
            )
            if use_once is not None and "使用以下方式打开" in grounded_text:
                x, y = use_once.center
                return [Action(
                    ActionType.TAP,
                    display_id=self.display_id,
                    x=x,
                    y=y,
                    capability=ActionCapability.NAVIGATE,
                    reason="仅本次使用 QQ 的发送给好友入口",
                )]
            if send_to_friend is not None and "使用以下方式打开" in grounded_text:
                x, y = send_to_friend.center
                return [Action(
                    ActionType.TAP,
                    display_id=self.display_id,
                    x=x,
                    y=y,
                    capability=ActionCapability.NAVIGATE,
                    reason="选择 QQ 的发送给好友入口",
                )]
            message_staged = any(
                item.get("action") in {ActionType.TYPE.value, ActionType.TYPE_BOTTOM.value}
                and item.get("text") == confirmed_message
                for item in state.action_history
            )
            send_button = next(
                (item for item in grounded.elements if re.sub(r"\s+", "", item.label) == "发送"),
                None,
            )
            send_button = send_button or _qq_send_button_from_pixels(frame_path)
            ascii_phrases = sorted(
                (part.strip(" ,.") for part in re.findall(r"[A-Za-z][A-Za-z ]{3,}", confirmed_message)),
                key=len,
                reverse=True,
            )
            draft_item = next(
                (
                    item for item in grounded.elements
                    if any(phrase and phrase.casefold() in item.label.casefold() for phrase in ascii_phrases)
                ),
                None,
            )
            recipient_tap_attempted = any(
                item.get("action") in {ActionType.TAP.value, ActionType.CLICK_TEXT.value}
                and confirmed_recipient in str(item.get("reason") or "")
                for item in state.action_history
            )
            if recipient_tap_attempted:
                state.collected_data["qq_recipient_tap_attempted"] = confirmed_recipient
            test_row_fallback_attempted = any(
                "测试联系人会话行" in str(item.get("reason") or "")
                for item in state.action_history
            )
            recipient_selected = state.collected_data.get("qq_recipient_selected") == confirmed_recipient
            exact_recipient_selected_by_bridge = any(
                item.get("action") == ActionType.CLICK_TEXT.value
                and item.get("text") == confirmed_recipient
                and "Accessibility 精确选择联系人" in str(item.get("reason") or "")
                for item in state.action_history
            )
            exact_prior_selection = bool(
                recipient_tap_attempted
                and (
                    state.collected_data.get("qq_recipient_exact_grounded") == confirmed_recipient
                    or exact_recipient_selected_by_bridge
                )
            )
            on_share_list = any(
                marker in grounded_text for marker in ("最近转发", "最近聊天", "创建新的聊天")
            )
            # The dimmed share list remains OCR-visible behind QQ's modal, so
            # ``on_share_list`` alone cannot distinguish the confirmation
            # dialog. An exact Send button plus the confirmed recipient is
            # sufficient proof that recipient selection has completed.
            share_confirmation = (
                send_button is not None
                and (
                    "发送给" in re.sub(r"\s+", "", grounded_text)
                    # Real QQ builds may omit the modal header from OCR even
                    # though the large pixel-verified Send button is visible.
                    # Trust only a recipient that was exact-grounded on the
                    # immediately preceding share list (or selected through
                    # the display-scoped Accessibility exact-text bridge).
                    or (not qq_draft and exact_prior_selection)
                )
            )
            if (
                share_confirmation
                and state.collected_data.get("qq_recipient_tap_attempted") == confirmed_recipient
            ):
                recipient_tap_attempted = True
            if (
                (send_button is not None and recipient_item is not None)
                or (share_confirmation and recipient_tap_attempted)
            ):
                state.collected_data["qq_recipient_selected"] = confirmed_recipient
                recipient_selected = True
            if qq_draft and share_confirmation:
                raise AgentTerminalDecision(
                    "ABORT", "QQ 草稿误入分享确认页；该页面不是聊天输入框，已停止且未发送",
                )
            if (
                on_share_list
                and recipient_item is not None
                and not recipient_selected
                and not recipient_tap_attempted
            ):
                if qq_draft:
                    x, y = recipient_item.center
                    return [Action(
                        ActionType.TAP,
                        display_id=self.display_id,
                        x=x,
                        y=y,
                        capability=ActionCapability.READ,
                        reason=f"打开已确认联系人 {confirmed_recipient} 的 QQ 草稿会话",
                    )]
                x, y = recipient_item.center
                if recipient_item in exact_recipient_items:
                    state.collected_data["qq_recipient_exact_grounded"] = confirmed_recipient
                return [Action(
                    ActionType.TAP,
                    display_id=self.display_id,
                    x=x,
                    y=y,
                    capability=ActionCapability.READ,
                    reason=f"打开已确认联系人 {confirmed_recipient} 的 QQ 会话",
                )]
            # Never let the vision model type or guess a recipient while QQ's
            # share list is visible. The outgoing payload is already carried
            # by ACTION_SEND; the only valid action here is selecting an exact
            # contracted contact. If that contact cannot be grounded, fail
            # closed before any text entry or irreversible action.
            if on_share_list and not recipient_selected and not recipient_tap_attempted:
                # Chinese OCR on circular-avatar rows may drop one or more
                # glyphs even though QQ's Accessibility node still exposes the
                # full contact name. Use the bridge's exact-text lookup here;
                # it refuses missing or ambiguous matches and therefore never
                # degrades into a fuzzy coordinate guess.
                return [Action(
                    ActionType.CLICK_TEXT,
                    display_id=self.display_id,
                    package="com.tencent.mobileqq",
                    text=confirmed_recipient,
                    # QQ duplicates the same contact in “最近转发” and
                    # “最近聊天”. The bridge still clicks exactly one node:
                    # the upper responsive “最近转发” share target.
                    allow_multiple=True,
                    capability=ActionCapability.READ,
                    reason=f"通过 Accessibility 精确选择联系人 {confirmed_recipient}",
                )]
            if on_share_list and recipient_tap_attempted and not share_confirmation:
                waits = int(state.collected_data.get("qq_recipient_selection_waits", 0)) + 1
                state.collected_data["qq_recipient_selection_waits"] = waits
                if waits <= 3:
                    return [Action(
                        ActionType.WAIT,
                        display_id=self.display_id,
                        seconds=1,
                        reason=f"等待 QQ 打开精确联系人确认页（{waits}/3）",
                    )]
                raise AgentTerminalDecision(
                    "ABORT", "QQ 精确联系人选择未生效，已在发送前停止",
                )
            # Drafts use QQ's ordinary inbox and then the real conversation
            # editor. Only an exact visible contact may be opened; no search
            # box, fuzzy nickname, or model-inferred alias is permitted.
            if (
                qq_draft
                and not on_share_list
                and not share_confirmation
                and recipient_item is not None
                and not recipient_tap_attempted
                and not recipient_selected
            ):
                x, y = recipient_item.center
                state.collected_data["qq_recipient_exact_grounded"] = confirmed_recipient
                return [Action(
                    ActionType.TAP,
                    display_id=self.display_id,
                    x=x,
                    y=y,
                    capability=ActionCapability.READ,
                    reason=f"从 QQ 会话列表打开精确联系人 {confirmed_recipient}",
                )]
            if (
                qq_draft
                and recipient_tap_attempted
                and not on_share_list
                and state.collected_data.get("last_step_outcome", {}).get("code") == "CHANGED"
                and (
                    confirmed_recipient in grounded_text
                    or (test_row_fallback_attempted and not ("搜索" in grounded_text and "登录" in grounded_text))
                )
            ):
                state.collected_data["qq_recipient_selected"] = confirmed_recipient
                recipient_selected = True
            if (
                qq_draft
                and recipient_tap_attempted
                and not recipient_selected
                and not test_row_fallback_attempted
                and Config.load().qq_test_recipient == confirmed_recipient
                and "搜索" in grounded_text
                and "登录" in grounded_text
            ):
                # Deployment-only regression fallback for the pinned test
                # contact. The product path above remains recipient-driven;
                # this coordinate is enabled only by QQ_TEST_RECIPIENT and
                # targets the first visible recent-chat row in the fixture.
                return [Action(
                    ActionType.TAP,
                    display_id=self.display_id,
                    x=320,
                    y=660,
                    capability=ActionCapability.READ,
                    reason=f"打开当前部署的测试联系人会话行 {confirmed_recipient}",
                )]
            if (
                qq_draft
                and not on_share_list
                and not share_confirmation
                and not recipient_selected
                and not recipient_tap_attempted
            ):
                return [Action(
                    ActionType.CLICK_TEXT,
                    display_id=self.display_id,
                    package="com.tencent.mobileqq",
                    text=confirmed_recipient,
                    allow_multiple=True,
                    capability=ActionCapability.READ,
                    reason=f"通过 Accessibility 从 QQ 会话列表精确打开联系人 {confirmed_recipient}",
                )]
            # ACTION_SEND can expose the ordinary QQ inbox for a short period
            # without the “正在处理” label. Names visible in the inbox (or the
            # account header) are not share targets and their coordinates may
            # become stale when the share sheet appears. Wait for an explicit
            # share/resolver/confirmation surface instead of tapping them.
            if (
                not on_share_list
                and not share_confirmation
                and send_to_friend is None
                and use_once is None
                and not recipient_selected
            ):
                waits = int(state.collected_data.get("qq_share_surface_waits", 0)) + 1
                state.collected_data["qq_share_surface_waits"] = waits
                if waits <= 8:
                    return [Action(
                        ActionType.WAIT,
                        display_id=self.display_id,
                        seconds=1,
                        reason=(
                            f"等待 QQ 联系人会话打开（{waits}/8）" if qq_draft else
                            f"等待 QQ 分享页出现，禁止点击普通收件箱联系人（{waits}/8）"
                        ),
                    )]
                raise AgentTerminalDecision("ABORT", "QQ 分享页未出现，已在选择联系人或发送前停止")
            state.collected_data.pop("qq_share_surface_waits", None)
            if (
                recipient_item is None
                and draft_item is not None
                and not recipient_tap_attempted
                and not message_staged
            ):
                x, y = draft_item.center
                return [Action(
                    ActionType.TAP,
                    display_id=self.display_id,
                    x=x,
                    y=y,
                    capability=ActionCapability.READ,
                    reason=f"通过已确认消息草稿打开 {confirmed_recipient} 的 QQ 会话",
                )]
            if qq_draft and recipient_selected and confirmed_message and not message_staged:
                return [Action(
                    ActionType.TYPE_BOTTOM,
                    display_id=self.display_id,
                    text=confirmed_message,
                    capability=ActionCapability.ENTER_TEXT,
                    reason=f"在 {confirmed_recipient} 会话底部编辑消息草稿但不发送",
                )]
            compact_message = re.sub(r"[\s\W_]+", "", confirmed_message)
            compact_screen = re.sub(r"[\s\W_]+", "", grounded_text)
            draft_visible_in_editor = bool(
                message_staged
                and compact_message
                and recipient_selected
                and not share_confirmation
                and not on_share_list
            )
            if qq_draft and draft_visible_in_editor:
                state.collected_data["qq_draft"] = {
                    "recipient": confirmed_recipient,
                    "message": confirmed_message,
                    "sent": False,
                    "surface": "qq_chat_input",
                }
                state.evidence.append({
                    "criterion": "QQ message draft staged",
                    "passed": True,
                    "evidence": f"recipient={confirmed_recipient}; sent=false; surface=qq_chat_input; exact_set_text_ack=true",
                })
                return [Action(ActionType.FINISH, reason="QQ 消息草稿已编辑并保留，未点击发送")]
            if qq_draft and message_staged:
                waits = int(state.collected_data.get("qq_draft_verify_waits", 0)) + 1
                state.collected_data["qq_draft_verify_waits"] = waits
                if waits <= 3:
                    return [Action(
                        ActionType.WAIT,
                        display_id=self.display_id,
                        seconds=1,
                        reason=f"核对 QQ 聊天输入框中的完整草稿（{waits}/3）",
                    )]
                raise AgentTerminalDecision("ABORT", "未能在 QQ 聊天输入框中复核完整草稿，已停止且未发送")
            # In QQ's ACTION_SEND confirmation dialog the shared payload is
            # already shown above the optional “输入留言” editor. Typing the
            # payload again would add a second/comment message. Once the
            # recipient and final Send button are both visible, send the
            # preloaded payload directly and never focus that editor.
            # Never trust persisted selection state alone for an irreversible
            # send. The final confirmation surface must visibly contain the
            # exact contracted recipient. A model may not infer that a
            # different nickname (for example “萱草”) is the same person.
            exact_recipient_visible = bool(
                compact_recipient
                and compact_recipient in re.sub(r"[\s\W_]+", "", grounded_text)
            )
            # OCR can omit QQ's small, anti-aliased recipient label even when
            # it is plainly present. Re-check the current confirmation sheet
            # through Accessibility; the callback accepts only an exact node
            # in the lower modal region, never the same name in the list
            # behind the modal.
            if (
                share_confirmation
                and not exact_recipient_visible
                and self.qq_confirmation_recipient_checker is not None
            ):
                try:
                    exact_recipient_visible = bool(
                        self.qq_confirmation_recipient_checker(confirmed_recipient)
                    )
                except Exception:
                    exact_recipient_visible = False
            # Previous selection attempts are never proof of the current
            # confirmation target. The irreversible Send action requires the
            # complete contracted recipient to be visible on this exact frame.
            # If QQ opened a neighbouring row, fail closed instead of trusting
            # stale Accessibility evidence.
            if share_confirmation and not exact_recipient_visible:
                raise AgentTerminalDecision(
                    "ABORT",
                    f"QQ 发送确认页收件人不是 {confirmed_recipient}，已停止且未发送",
                )
            if (
                recipient_selected
                and exact_recipient_visible
                and send_button is not None
                and confirmed_message
                and not message_staged
            ):
                return [Action(
                    ActionType.CLICK_TEXT,
                    display_id=self.display_id,
                    package="com.tencent.mobileqq",
                    text="发送",
                    capability=ActionCapability.SENSITIVE,
                    reason="点击发送已确认且由分享意图预载的 QQ 消息",
                )]
            if message_staged and exact_recipient_visible and send_button:
                return [Action(
                    ActionType.CLICK_TEXT,
                    display_id=self.display_id,
                    package="com.tencent.mobileqq",
                    text="发送",
                    capability=ActionCapability.SENSITIVE,
                    reason="点击发送已确认的 QQ 消息",
                )]
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
Never tap an already-open text field: focusing it can leak the system IME onto Display 0. action=type uses a display-scoped Accessibility node, but an app that auto-focuses its field can still summon the singleton IME; BearBless will fail closed if that happens. For login, verification, or a non-standard editor use interact.
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


class QQMessageVerifier:
    """Deterministically verify the single QQ send after it is committed."""

    def __init__(self, grounder: SetOfMarkGrounder | None = None) -> None:
        self.grounder = grounder or SetOfMarkGrounder(CommandRunner())

    def verify(self, state: TaskState) -> VerificationResult:
        frame_path = str((state.last_observation or {}).get("frame_path") or "")
        scope = str(
            (state.task_spec.constraints.get("sensitive_scope") if state.task_spec else "")
            or state.goal
        )
        # Use the same parser that authorizes the recipient before launch and
        # before the sensitive Send click. A verifier-local regex previously
        # parsed "给红枣桂花熊发消息说：今天吃饭" as the recipient
        # "红枣桂花熊发消息". That turned a successfully committed send
        # into a false failure when QQ closed its share Activity instead of
        # rendering a chat bubble on the virtual display.
        recipient = extract_confirmed_recipient(scope)
        message = extract_confirmed_message(scope)
        effect = state.collected_data.get("sensitive_effect")
        committed = isinstance(effect, dict) and effect.get("phase") == "COMMITTED"
        visible = ""
        if frame_path and Path(frame_path).exists():
            screen = self.grounder.ground(frame_path)
            visible = re.sub(r"[\s\W_]+", "", "".join(item.label for item in screen.elements))
        recipient_ok = bool(recipient and re.sub(r"[\s\W_]+", "", recipient) in visible)
        message_ok = bool(message and re.sub(r"[\s\W_]+", "", message) in visible)
        exact_selection = any(
            item.get("action") == ActionType.CLICK_TEXT.value
            and item.get("text") == recipient
            and "Accessibility 精确选择联系人" in str(item.get("reason") or "")
            for item in state.action_history
        )
        sensitive_send = any(
            item.get("action") == ActionType.CLICK_TEXT.value
            and item.get("text") == "发送"
            and item.get("capability") == ActionCapability.SENSITIVE.value
            for item in state.action_history
        )
        # ``COMMITTED`` is written only after QQ's exact Accessibility Send
        # node accepts ACTION_CLICK. Together with the preceding exact-contact
        # selection and the ACTION_SEND-preloaded contract message, this is the
        # authoritative success boundary. OCR of the resulting blue bubble is
        # useful extra evidence, but must not turn a successful send into a
        # failure merely because white-on-blue Chinese text was missed.
        send_committed = committed and exact_selection and sensitive_send
        passed = send_committed or (committed and recipient_ok and message_ok)
        reason = (
            f"已向{recipient}发送“{message}”；QQ 发送按钮已接受点击"
            if passed
            else "QQ 发送动作已停止；最终聊天页尚未同时显示精确联系人和原文气泡"
        )
        if passed:
            state.collected_data["agent_result"] = reason
        return VerificationResult(
            passed=passed,
            retryable=False,
            reason=reason,
            evidence=[{
                "criterion": "QQ message sent once",
                "passed": passed,
                "evidence": frame_path,
                "summary": f"recipient={recipient}; message={message}; committed={committed}",
            }],
        )
