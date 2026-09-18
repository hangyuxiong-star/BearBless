import json

from bearbless.runtime.netease_music import resolve_netease_song_uri


def test_resolves_exact_song_to_official_app_deep_link() -> None:
    def fetch(request, timeout):
        assert "s=%E9%93%B6%E6%B2%B3%E8%B5%B4%E7%BA%A6" in request.full_url
        assert timeout == 5.0
        return json.dumps({
            "result": {
                "songs": [
                    {"id": 1, "name": "银河赴约 Remix"},
                    {"id": 1453907054, "name": "银河赴约"},
                ],
            },
        }).encode()

    assert resolve_netease_song_uri(
        "打开网易云音乐，播放歌曲银河赴约", fetch=fetch,
    ) == "orpheus://song/1453907054"


def test_does_not_guess_when_search_has_no_exact_title() -> None:
    payload = json.dumps({"result": {"songs": [{"id": 1, "name": "银河赴约 Remix"}]}}).encode()
    assert resolve_netease_song_uri(
        "打开网易云音乐，播放歌曲银河赴约", fetch=lambda _request, _timeout: payload,
    ) is None


def test_non_playback_task_does_not_use_deep_link() -> None:
    assert resolve_netease_song_uri("打开网易云搜索歌曲银河赴约") is None
