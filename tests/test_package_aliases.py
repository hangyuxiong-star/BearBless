from types import SimpleNamespace

from bearbless.runtime.packages import resolve_explicit_app_alias


class FakeAdb:
    def shell(self, *args):
        package = args[-1]
        installed = {
            "com.android.settings", "com.netease.cloudmusic",
            "com.huawei.deskclock", "com.autonavi.minimap",
            "com.wolt.android", "com.tencent.mobileqq",
        }
        return SimpleNamespace(ok=package in installed, stdout=f"package:{package}\n")


def test_system_settings_alias_resolves_even_though_it_is_not_a_user_package():
    assert resolve_explicit_app_alias("打开设置，查看蓝牙", FakeAdb()) == "com.android.settings"


def test_known_user_app_resolves_without_a_model_call():
    assert resolve_explicit_app_alias("打开网易云播放音乐", FakeAdb()) == "com.netease.cloudmusic"


def test_huawei_clock_resolves_without_a_model_call():
    assert resolve_explicit_app_alias("打开时钟，设置明早七点的闹钟", FakeAdb()) == "com.huawei.deskclock"


def test_generic_map_goal_is_not_in_frozen_scope():
    assert resolve_explicit_app_alias("打开地图导航去公司", FakeAdb()) is None


def test_unmentioned_app_does_not_hijack_goal():
    assert resolve_explicit_app_alias("打开相册", FakeAdb()) is None


def test_wolt_resolves_without_model_package_guessing():
    assert resolve_explicit_app_alias("打开wolt帮我找家汉堡店", FakeAdb()) == "com.wolt.android"


def test_lowercase_qq_resolves_without_model_package_guessing():
    assert resolve_explicit_app_alias("打开qq给朋友发消息", FakeAdb()) == "com.tencent.mobileqq"


def test_qq_send_route_ignores_other_app_names_inside_payload():
    goal = "打开QQ给红枣桂花熊发消息：Wolt推荐：Shishbar Restaurant"
    assert resolve_explicit_app_alias(goal, FakeAdb()) == "com.tencent.mobileqq"
