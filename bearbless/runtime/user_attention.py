from __future__ import annotations

from bearbless.runtime.adb import AdbClient


LOGIN_MARKERS = ("登录", "身份验证", "验证码", "密码", "账号验证", "重新登录")


def login_takeover_required(failure_reason: str | None) -> bool:
    reason = failure_reason or ""
    return "TAKE_OVER" in reason and any(marker in reason for marker in LOGIN_MARKERS)


class UserAttentionNotifier:
    """Posts a non-focus-stealing Android notification for human takeover."""

    def __init__(self, adb: AdbClient) -> None:
        self.adb = adb

    def notify_login_required(self) -> bool:
        result = self.adb.shell(
            "cmd", "notification", "post",
            "-t", "BearBless 需要你登录",
            "bearbless_login_required",
            "请在手机上完成登录或验证码，完成后回到 Dashboard 重新继续任务。",
        )
        return result.ok

    def notify_verification_required(self) -> bool:
        result = self.adb.shell(
            "cmd", "notification", "post",
            "-t", "BearBless 需要人工验证",
            "bearbless_verification_required",
            "请在电脑的 BearBless Shadow Display 窗口完成滑块或人机验证。",
        )
        return result.ok
