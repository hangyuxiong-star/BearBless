from bearbless.device_task import DEFAULT_ROUTE_URL, requires_staged_shadow_start
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
    assert not requires_staged_shadow_start("com.huawei.deskclock")
    assert not requires_staged_shadow_start("com.netease.cloudmusic")
