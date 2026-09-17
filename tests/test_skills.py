from bearbless.agent.skills import SkillRegistry


class Adb:
    pass


def test_registry_always_includes_general_skill():
    names = [skill.name for skill in SkillRegistry(Adb()).resolve("打开设置查看蓝牙")]
    assert names == ["general_gui"]


def test_registry_adds_thin_semantic_skill_without_app_name():
    names = [skill.name for skill in SkillRegistry(Adb()).resolve("明天早上七点设置闹钟")]
    assert names == ["general_gui", "alarm_management"]


def test_media_skill_owns_deterministic_completion_probe():
    registry = SkillRegistry(Adb())
    selected = registry.resolve("播放歌曲银河赴约")
    assert [skill.name for skill in selected] == ["general_gui", "media_playback"]
    assert len(registry.completion_probe(selected).probes) == 1
