from bearbless.agent.task_profiles import PROFILES, resolve_task_profile


def test_frozen_product_profiles_have_unique_packages_and_verifiable_milestones() -> None:
    assert len(PROFILES) == 4
    assert len({profile.package for profile in PROFILES}) == 4
    for profile in PROFILES:
        assert profile.milestones
        assert all(milestone.completion_evidence for milestone in profile.milestones)
        assert profile.terminal_constraints


def test_sensitive_and_mutating_terminal_steps_are_not_replay_safe() -> None:
    qq = resolve_task_profile("com.tencent.mobileqq", "给红枣桂花熊发消息：晚上见")
    alarm = resolve_task_profile("com.huawei.deskclock", "设置晚上六点闹钟")
    assert qq is not None and qq.milestones[-1].replay_safe is False
    assert alarm is not None and alarm.milestones[-1].replay_safe is False


def test_read_only_qq_task_does_not_receive_send_profile() -> None:
    assert resolve_task_profile("com.tencent.mobileqq", "打开 QQ 看一下") is None


def test_profile_instruction_exposes_milestone_order_and_stop_rules() -> None:
    profile = resolve_task_profile("com.netease.cloudmusic", "打开网易云音乐播放银河赴约")
    assert profile is not None
    instruction = profile.planner_instruction()
    assert "解析精确歌曲" in instruction
    assert "MediaSession" not in instruction
    assert "播放成功后立即停止规划" in instruction
