from bearbless.agent.state import TaskState
from bearbless.agent.verifier import SystemAlarmVerifier
from bearbless.runtime.commands import CommandResult


class FakeAdb:
    def __init__(self, next_alarm: str, dumpsys: str = "") -> None:
        self.next_alarm = next_alarm
        self.dumpsys = dumpsys

    def shell(self, *args):
        output = self.next_alarm if args[:3] == ("settings", "get", "system") else self.dumpsys
        return CommandResult("adb", tuple(args), 0, output, "")


def test_system_alarm_verifier_accepts_localized_next_alarm():
    verifier = SystemAlarmVerifier(FakeAdb("周六 6:00 下午\n"), 18, 0)  # type: ignore[arg-type]
    result = verifier.verify(TaskState("id", "set alarm"))
    assert result.passed
    assert not result.retryable
    assert result.evidence[0]["source"] == "android_system_alarm_state"


def test_system_alarm_verifier_accepts_deskclock_record_when_an_earlier_alarm_is_next():
    dumpsys = "Register time:2026-09-19 18:00:00 packageName=com.huawei.deskclock"
    verifier = SystemAlarmVerifier(FakeAdb("周六 5:00 下午\n", dumpsys), 18, 0)  # type: ignore[arg-type]
    assert verifier.verify(TaskState("id", "set alarm")).passed


def test_system_alarm_verifier_fails_closed_without_durable_evidence():
    verifier = SystemAlarmVerifier(FakeAdb("null\n"), 18, 0)  # type: ignore[arg-type]
    result = verifier.verify(TaskState("id", "set alarm"))
    assert not result.passed
    assert not result.retryable
