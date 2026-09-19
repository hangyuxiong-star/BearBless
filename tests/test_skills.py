from bearbless.agent.skills import SkillRegistry


class Adb:
    pass


def test_registry_always_includes_general_skill():
    names = [skill.name for skill in SkillRegistry(Adb()).resolve("打开设置查看蓝牙")]
    assert names == ["general_gui"]


def test_registry_adds_thin_semantic_skill_without_app_name():
    names = [skill.name for skill in SkillRegistry(Adb()).resolve("明天早上七点设置闹钟")]
    assert names == ["general_gui", "alarm_management"]


def test_alarm_skill_describes_picker_column_grounding():
    skills = SkillRegistry(Adb()).resolve("设置明天早上七点半的闹钟")
    alarm = next(skill for skill in skills if skill.name == "alarm_management")
    assert "x=180、460、735" in alarm.instruction
    assert "严禁在两列之间滑动" in alarm.instruction


def test_media_skill_owns_deterministic_completion_probe():
    registry = SkillRegistry(Adb())
    selected = registry.resolve("播放歌曲银河赴约")
    assert [skill.name for skill in selected] == ["general_gui", "media_playback"]
    assert len(registry.completion_probe(selected).probes) == 1


def test_lowercase_qq_send_information_selects_message_skill():
    names = [skill.name for skill in SkillRegistry(Adb()).resolve("打开qq给朋友发信息")]
    assert names == ["general_gui", "message_send"]


def test_frozen_scope_routes_restaurant_but_not_map_skill():
    registry = SkillRegistry(Adb())
    restaurant = [skill.name for skill in registry.resolve("打开美团搜索附近咖啡")]
    assert restaurant == ["general_gui", "restaurant_research"]
    assert [skill.name for skill in registry.resolve("打开地图导航去机场")] == ["general_gui"]


def test_wolt_skill_avoids_search_field_and_primary_ime():
    skills = SkillRegistry(Adb()).resolve("打开wolt帮我找家汉堡店")
    restaurant = next(skill for skill in skills if skill.name == "restaurant_research")
    assert "禁止点击 Search" in restaurant.instruction
    assert "Burger King" in restaurant.instruction
    assert "Share location" in restaurant.instruction
    assert "默认选择 Share location" in restaurant.instruction
    assert "不得代替用户新增授权" in restaurant.instruction
