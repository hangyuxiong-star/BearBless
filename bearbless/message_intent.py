from __future__ import annotations

import re


def extract_confirmed_message(scope: str) -> str:
    """Extract only the message text explicitly supplied by the user."""
    patterns = (
        r"(?:消息草稿|信息草稿|草稿)\s*[：:]\s*(.+)$",
        r"(?:消息|信息)\s*[：:]\s*(.+)$",
        r"(?:发消息|发信息)\s*(?:问(?:他|她|对方)?|说|告诉(?:他|她|对方)?)\s*[：:]?\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, scope)
        if match and match.group(1).strip():
            message = match.group(1).strip()
            message = re.sub(r"[，,；;。]?\s*(?:不用|不要|不)发送\s*[。.]?$", "", message).strip()
            return message
    return ""
