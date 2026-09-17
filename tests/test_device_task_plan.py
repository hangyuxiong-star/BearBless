from bearbless.device_task import DEFAULT_ROUTE_URL
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
