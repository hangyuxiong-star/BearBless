from types import SimpleNamespace

from bearbless.runtime.packages import resolve_explicit_app_alias


class FakeAdb:
    def shell(self, *args):
        package = args[-1]
        installed = {"com.android.settings", "com.netease.cloudmusic", "com.huawei.deskclock"}
        return SimpleNamespace(ok=package in installed, stdout=f"package:{package}\n")


def test_system_settings_alias_resolves_even_though_it_is_not_a_user_package():
    assert resolve_explicit_app_alias("打开设置，查看蓝牙", FakeAdb()) == "com.android.settings"


def test_known_user_app_resolves_without_a_model_call():
    assert resolve_explicit_app_alias("打开网易云播放音乐", FakeAdb()) == "com.netease.cloudmusic"


def test_huawei_clock_resolves_without_a_model_call():
    assert resolve_explicit_app_alias("打开时钟，设置明早七点的闹钟", FakeAdb()) == "com.huawei.deskclock"


def test_unmentioned_app_does_not_hijack_goal():
    assert resolve_explicit_app_alias("打开地图", FakeAdb()) is None
