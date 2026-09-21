from bearbless.device_task import (
    DEFAULT_ROUTE_URL,
    WOLT_ROUTE_CRITERIA,
    is_wolt_to_qq_task,
    requires_staged_shadow_start,
    wolt_qq_message,
    wolt_requested_category,
    run_wolt_to_qq_task,
)
from bearbless.agent.state import TaskState, TaskStatus
from bearbless.agent.verifier import EvidenceVerifier
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
    assert wolt_requested_category("去Wolt找一家Asia店") == ("中餐店", "中餐店")
    assert is_wolt_to_qq_task(
        "在wolt上找一家汉堡店，然后打开qq给红枣桂花熊发信息说晚上去这里吃"
    )


def test_wolt_asian_evidence_uses_the_same_criterion_as_runtime_verifier() -> None:
    criterion = WOLT_ROUTE_CRITERIA["wolt_chinese_categories"]
    state = TaskState("agent-asian", "在 Wolt 找一家中餐店")
    state.evidence.append({"criterion": criterion, "passed": True, "evidence": "frame.png"})

    result = EvidenceVerifier((criterion,)).verify(state)

    assert criterion == "Wolt Asian/Chinese restaurant results"
    assert result.passed
    assert result.missing == []


def test_generated_qq_payload_does_not_reenter_wolt_orchestrator() -> None:
    # The child message necessarily contains “Wolt推荐”, but QQ is the
    # destination at this stage. Reclassifying it as a new compound parent
    # would recurse into another Wolt run instead of sending the verified data.
    child = "打开QQ给红枣桂花熊发消息：Wolt推荐：Shishbar Restaurant，评分9.4，地址Lyngby"
    assert not is_wolt_to_qq_task(child)


def test_compound_wolt_failure_preserves_child_task_for_dashboard(monkeypatch) -> None:
    child = TaskState("agent-child", "Wolt child")
    child.status = TaskStatus.FAILED
    child.failure_reason = "category unavailable"
    monkeypatch.setattr("bearbless.device_task.run_general_device_task", lambda *_args, **_kwargs: child)

    result = run_wolt_to_qq_task(
        "打开Wolt找中餐店，去QQ把地址发给红枣桂花熊",
        None,
        None,
    )

    assert result.task_id == "agent-child"
    assert result.status == TaskStatus.FAILED
    assert "禁止进入 QQ" in str(result.failure_reason)
