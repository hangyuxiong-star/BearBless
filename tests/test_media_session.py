from types import SimpleNamespace

from bearbless.agent.state import TaskState
from bearbless.runtime.media_session import MediaSessionCompletionProbe, requested_track


class Adb:
    def __init__(self, output):
        self.output = output

    def shell(self, *args):
        return SimpleNamespace(ok=True, stdout=self.output)


def test_requested_track_parses_chinese_music_goal():
    assert requested_track("打开网易云，播放歌曲：银河赴约") == "银河赴约"
    assert requested_track("打开网易云播放歌曲归去来兮") == "归去来兮"


def test_media_session_completes_playing_target_without_another_model_step():
    output = """package=com.netease.cloudmusic
state=PlaybackState {state=3, position=1000, speed=1.0}
metadata: description=银河赴约 (网易云音乐助力高考自制曲目), 网易云音乐校园/CMJ"""
    result = MediaSessionCompletionProbe(Adb(output)).verify(  # type: ignore[arg-type]
        TaskState("t", "打开网易云，播放歌曲：银河赴约")
    )
    assert result is not None and result.passed


def test_media_session_does_not_complete_wrong_or_paused_track():
    state = TaskState("t", "打开网易云，播放歌曲：银河赴约")
    wrong = MediaSessionCompletionProbe(Adb("state=PlaybackState {state=3,}\n别的歌")).verify(state)  # type: ignore[arg-type]
    paused = MediaSessionCompletionProbe(Adb("state=PlaybackState {state=2,}\n银河赴约")).verify(state)  # type: ignore[arg-type]
    assert wrong is None and paused is None


def test_media_session_never_completes_a_do_not_play_query():
    output = "state=PlaybackState {state=3,}\n银河赴约"
    result = MediaSessionCompletionProbe(Adb(output)).verify(  # type: ignore[arg-type]
        TaskState("t", "打开网易云搜索银河赴约，不要播放")
    )
    assert result is None
