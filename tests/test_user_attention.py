from dataclasses import dataclass

from bearbless.runtime.user_attention import UserAttentionNotifier, login_takeover_required


@dataclass
class Result:
    ok: bool


class FakeAdb:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def shell(self, *args):
        self.calls.append(args)
        return Result(self.ok)


def test_only_login_takeover_requests_main_screen_attention():
    assert login_takeover_required("TAKE_OVER: 页面需要登录")
    assert login_takeover_required("TAKE_OVER: 请输入短信验证码")
    assert not login_takeover_required("TAKE_OVER: 请确认定位权限")
    assert not login_takeover_required("ABORT: 页面加载失败")


def test_login_notification_uses_android_notification_without_input():
    adb = FakeAdb()
    assert UserAttentionNotifier(adb).notify_login_required() is True
    command = adb.calls[0]
    assert command[:4] == ("cmd", "notification", "post", "-t")
    assert "input" not in command
    assert "keyevent" not in command


def test_verification_notification_points_user_to_shadow_window():
    adb = FakeAdb()
    assert UserAttentionNotifier(adb).notify_verification_required() is True
    assert "BearBless Shadow Display" in adb.calls[0][-1]
