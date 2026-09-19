import json

import pytest

from bearbless.dashboard_data import TaskRequestError, compile_dynamic_mission_contract, compile_mission_contract, submit_task_request


def test_submit_task_request_is_local_and_structured(tmp_path) -> None:
    path = submit_task_request("  比较   三个出行方案  ", tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["goal"] == "比较 三个出行方案"
    assert payload["status"] == "QUEUED"
    assert payload["source"] == "dashboard"
    assert payload["mission_contract"]["interruption_budget"] == 0
    assert "操作 Display 0" in payload["mission_contract"]["forbidden_actions"]


def test_submit_task_rejects_invalid_length(tmp_path) -> None:
    with pytest.raises(TaskRequestError):
        submit_task_request("x", tmp_path)


def test_map_tasks_are_rejected_with_wolt_address_guidance() -> None:
    with pytest.raises(TaskRequestError, match="Wolt 店铺详情页"):
        compile_mission_contract("打开地图导航去餐厅")


def test_compile_mission_contract_is_bounded_and_verifiable() -> None:
    contract = compile_mission_contract("比较三个出行方案并保存")
    assert contract.constraints["workspace"] == "shadow_display"
    assert contract.allowed_actions
    assert {item.name for item in contract.success_criteria} == {
        "result_created", "result_verified", "zero_interruption",
    }


def test_dynamic_contract_reflects_task_and_keeps_hard_boundaries() -> None:
    class Analyzer:
        def complete(self, prompt):
            assert "播放" in prompt
            return {
                "allowed_actions": ["打开音乐应用", "搜索歌曲", "播放歌曲"],
                "forbidden_actions": ["购买会员"],
                "success_criteria": [{"name": "playing", "description": "目标歌曲标题与播放状态可见", "required": True}],
            }

    contract = compile_dynamic_mission_contract("打开网易云播放这就是爱", Analyzer())
    assert "播放歌曲" in contract.allowed_actions
    assert "操作 Display 0" in contract.forbidden_actions
    assert contract.interruption_budget == 0


def test_dynamic_contract_marks_status_question_read_only() -> None:
    class Analyzer:
        def complete(self, prompt):
            return {
                "allowed_actions": ["打开设置"],
                "forbidden_actions": ["查看蓝牙是否已开启", "修改蓝牙状态"],
                "success_criteria": [{"name": "status", "description": "显示当前状态", "required": True}],
            }

    contract = compile_dynamic_mission_contract("打开设置，查看蓝牙是否已开启", Analyzer())
    assert contract.task_mode.value == "READ_ONLY_QUERY"
    assert "读取并报告任务相关的当前状态" in contract.allowed_actions
    assert "查看蓝牙是否已开启" not in contract.forbidden_actions
    assert "修改蓝牙状态" in contract.forbidden_actions


def test_explicit_message_contract_is_confirmed_and_scoped() -> None:
    class Analyzer:
        def complete(self, prompt):
            return {
                "allowed_actions": ["打开QQ", "查找联系人", "发送指定消息"],
                "forbidden_actions": ["发送消息", "支付"],
                "success_criteria": [{"name": "sent", "description": "指定消息已发送给指定联系人", "required": True}],
            }

    contract = compile_dynamic_mission_contract("打开QQ给红枣桂花熊发消息：你吃饭了吗？", Analyzer())
    assert contract.task_mode.value == "SENSITIVE_TASK"
    assert contract.constraints["user_confirmed_sensitive_action"] is True
    assert "发送消息" not in contract.forbidden_actions
    assert "付款或购买" in contract.forbidden_actions


def test_dynamic_contract_retries_non_object_model_response() -> None:
    class Analyzer:
        calls = 0

        def complete(self, prompt):
            self.calls += 1
            if self.calls == 1:
                return True
            return {
                "allowed_actions": ["打开时钟", "设置闹钟"],
                "forbidden_actions": ["删除其他闹钟"],
                "success_criteria": [{
                    "name": "alarm_created",
                    "description": "明天早上七点半的闹钟可见",
                    "required": True,
                }],
            }

    analyzer = Analyzer()
    contract = compile_dynamic_mission_contract("设置明天早上七点半的闹钟", analyzer)
    assert analyzer.calls == 2
    assert "设置闹钟" in contract.allowed_actions


def test_dynamic_contract_falls_back_after_repeated_invalid_responses() -> None:
    class Analyzer:
        def complete(self, prompt):
            return False

    contract = compile_dynamic_mission_contract("设置明天早上七点半的闹钟", Analyzer())
    assert contract.constraints["contract_fallback"] is True
    assert contract.constraints["workspace"] == "shadow_display"


def test_explicit_message_fallback_preserves_user_confirmation() -> None:
    class OfflineAnalyzer:
        def complete(self, prompt):
            raise RuntimeError("offline")

    goal = "打开QQ给红枣桂花熊发消息：去吃汉堡吧。"
    contract = compile_dynamic_mission_contract(goal, OfflineAnalyzer())

    assert contract.task_mode.value == "SENSITIVE_TASK"
    assert contract.constraints["user_confirmed_sensitive_action"] is True
    assert contract.constraints["sensitive_scope"] == goal
    assert "发送消息" not in contract.forbidden_actions


def test_explicit_faxinxi_message_preserves_user_confirmation() -> None:
    class Analyzer:
        def complete(self, prompt):
            return {
                "allowed_actions": ["打开QQ", "向红枣桂花熊发送原文消息"],
                "forbidden_actions": ["发送消息", "支付"],
                "success_criteria": [{
                    "name": "sent",
                    "description": "聊天中出现晚上好消息气泡",
                    "required": True,
                }],
            }

    goal = "打开QQ，给红枣桂花熊发信息：晚上好"
    contract = compile_dynamic_mission_contract(goal, Analyzer())

    assert contract.task_mode.value == "SENSITIVE_TASK"
    assert contract.constraints["user_confirmed_sensitive_action"] is True
    assert contract.constraints["sensitive_scope"] == goal
    assert "发送消息" not in contract.forbidden_actions
