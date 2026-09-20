from __future__ import annotations

import re


_QQ_DRAFT_MARKERS = (
    "草稿", "不用发送", "不要发送", "不发送",
)

_QQ_SEND_MARKERS = (
    "发给", "发送给", "发消息", "发送消息", "发信息", "发送信息", "告诉",
)


def is_wolt_to_qq_request(scope: str) -> bool:
    """Classify a Wolt→QQ compound request from one shared vocabulary."""
    folded = scope.casefold()
    wolt_index = folded.find("wolt")
    qq_index = folded.find("qq")
    return 0 <= wolt_index < qq_index and any(marker in scope for marker in _QQ_SEND_MARKERS)


def is_qq_draft_request(scope: str) -> bool:
    """Require explicit no-send wording before using the QQ chat editor.

    Phrases such as “编辑信息发给…” describe message composition but still
    authorize delivery. They use the ACTION_SEND confirmation route, which
    avoids focusing QQ's chat editor and therefore avoids the singleton IME.
    """
    return "qq" in scope.casefold() and any(marker in scope for marker in _QQ_DRAFT_MARKERS)


def extract_confirmed_recipient(scope: str) -> str:
    """Extract the explicitly named QQ recipient without guessing aliases."""
    # Natural Chinese permits both “给小王发信息” and “发信息给小王”.
    # Keep the latter separate so the message verb cannot be swallowed into
    # the recipient name.
    match = re.search(
        r"(?:发消息|发送消息|发信息|发送信息)\s*(?:给|向)[‘'\"“]?"
        r"([^，,。；;：:\s]{1,40}?)[’'\"”]?"
        r"(?:告诉(?:他|她|对方)?|问(?:他|她|对方)?|说|，|,|。|；|;|：|:|$)",
        scope,
    )
    if not match:
        match = re.search(
        r"(?:给|向|告诉)[‘'\"“]?([^，,：:\s]{1,40}?)[’'\"”]?(?:发消息|发送消息|发信息|发送信息|说|编辑|写|，|,)",
        scope,
        )
    if not match:
        match = re.search(
            r"(?:发给|发送给)[‘'\"“]?([^，,。；;：:\s]{1,40}?)[’'\"”]?(?:告诉(?:他|她|对方)?|说|，|,|。|；|;|：|:|$)",
            scope,
        )
    return match.group(1).strip() if match else ""


def require_explicit_qq_recipient(scope: str, test_recipient: str | None = None) -> str:
    """Require an explicit recipient and optionally enforce a deployment test target."""
    recipient = extract_confirmed_recipient(scope)
    if not recipient:
        raise ValueError("QQ 任务必须明确指定收件人")
    if test_recipient and recipient != test_recipient:
        raise ValueError(f"当前真机测试仅允许 QQ 联系人“{test_recipient}”")
    return recipient


def extract_confirmed_message(scope: str) -> str:
    """Extract only the message text explicitly supplied by the user."""
    patterns = (
        r"(?:消息草稿|信息草稿|草稿)\s*[：:]\s*(.+)$",
        r"(?:消息|信息)\s*[：:]\s*(.+)$",
        r"(?:发消息|发信息)\s*(?:问(?:他|她|对方)?|说|告诉(?:他|她|对方)?)\s*[：:]?\s*(.+)$",
        r"(?:发消息|发送消息|发信息|发送信息)\s*(?:给|向)"
        r"[^，,。；;：:\s]{1,40}?\s*"
        r"(?:问(?:他|她|对方)?|说|告诉(?:他|她|对方)?)\s*[：:]?\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, scope)
        if match and match.group(1).strip():
            message = match.group(1).strip()
            message = re.sub(r"[，,；;。]?\s*(?:不用|不要|不)发送\s*[。.]?$", "", message).strip()
            return message
    return ""
