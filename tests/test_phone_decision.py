import pytest
from pydantic import ValidationError

from bearbless.schemas import PhoneDecision


def test_report_carries_observed_result_without_becoming_runtime_input():
    decision = PhoneDecision(action="REPORT", observed_result="蓝牙当前关闭")
    assert decision.observed_result == "蓝牙当前关闭"


def test_report_accepts_structured_facts():
    decision = PhoneDecision.model_validate({
        "action": "REPORT",
        "result": {
            "summary": "蓝牙当前关闭",
            "facts": [{"name": "bluetooth_enabled", "value": False, "evidence": "详情页开关为灰色关闭状态"}],
        },
    })
    assert decision.result is not None
    assert decision.result.facts[0].value is False


def test_unknown_model_action_is_rejected_at_boundary():
    with pytest.raises(ValidationError):
        PhoneDecision(action="SHELL")
