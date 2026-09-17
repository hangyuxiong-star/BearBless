from bearbless.runtime.parsers import (
    parse_adb_devices,
    parse_display_ids,
    parse_ime_state,
    parse_wm_size,
    supports_display_targeted_input,
)


def test_parse_display_ids_from_scrcpy_output() -> None:
    text = "Display 0: 1080x2340\nDisplay 8: 1080x2400\nNew display id=8"
    assert parse_display_ids(text) == [0, 8]


def test_parse_adb_devices_ignores_unauthorized() -> None:
    text = "List of devices attached\nABC device product:x\nBAD unauthorized\n"
    assert parse_adb_devices(text) == ["ABC"]


def test_capability_and_state_parsers() -> None:
    assert supports_display_targeted_input("Usage: input [-d DISPLAY_ID] <command>")
    assert not supports_display_targeted_input("Usage: input <command>")
    assert parse_wm_size("Physical size: 1080x2340\nOverride size: 720x1560") == "720x1560"
    assert parse_ime_state("mCurTokenDisplayId=8 mCurToken=abc")["target_display_id"] == 8
