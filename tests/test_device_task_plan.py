from bearbless.device_task import (
    DEFAULT_ROUTE_URL,
    is_wolt_to_qq_task,
    requires_staged_shadow_start,
    wolt_qq_message,
    wolt_requested_category,
)
from bearbless.schemas import AgentAction


def test_structured_open_app_uri_has_no_shell_surface() -> None:
    action = AgentAction(
        action="OPEN_APP",
        display_id=8,
        package="com.huawei.browser",
        uri=DEFAULT_ROUTE_URL,
    ).to_runtime()
    assert action.uri.startswith("https://www.dsb.dk/")
    assert action.package == "com.huawei.browser"


def test_rom_sensitive_apps_use_staged_shadow_start() -> None:
    assert requires_staged_shadow_start("com.wolt.android")
    assert requires_staged_shadow_start("com.tencent.mobileqq")
    assert not requires_staged_shadow_start("com.huawei.deskclock")
    assert not requires_staged_shadow_start("com.netease.cloudmusic")


def test_wolt_to_qq_chain_is_explicit_and_payload_is_deterministic() -> None:
    goal = "打开Wolt找一家汉堡店去QQ发给红枣桂花熊"
    assert is_wolt_to_qq_task(goal)
    assert wolt_qq_message({
        "name": "Shishbar Restaurant",
        "rating": "9.4",
        "address": "Lyngbygardsvej 100B 2800 Lyngby-Gentofte",
    }) == "Wolt推荐：Shishbar Restaurant，评分9.4，地址Lyngbygardsvej 100B 2800 Lyngby-Gentofte"
    assert wolt_qq_message({
        "name": "Hot Wok",
        "rating": "9.1",
        "address": "Main Street 1",
    }, dinner_invitation=True) == "晚上去Hot Wok吃吧，评分9.1，地址Main Street 1"
    assert wolt_qq_message({
        "name": "Hot Wok", "rating": "9.1", "address": "Main Street 1",
    }, dinner_invitation=True, invitation_time="明天晚上") == "明天晚上去Hot Wok吃吧，评分9.1，地址Main Street 1"
    assert wolt_requested_category("打开Wolt找一家中餐店") == ("中餐店", "中餐店")
    assert is_wolt_to_qq_task(
        "在wolt上找一家汉堡店，然后打开qq给红枣桂花熊发信息说晚上去这里吃"
    )


def test_generated_qq_payload_does_not_reenter_wolt_orchestrator() -> None:
    # The child message necessarily contains “Wolt推荐”, but QQ is the
    # destination at this stage. Reclassifying it as a new compound parent
    # would recurse into another Wolt run instead of sending the verified data.
    child = "打开QQ给红枣桂花熊发消息：Wolt推荐：Shishbar Restaurant，评分9.4，地址Lyngby"
    assert not is_wolt_to_qq_task(child)
