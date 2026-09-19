from bearbless.device_task import resolve_youtube_search_query, resolve_youtube_search_uri


def test_youtube_search_uri_avoids_ime() -> None:
    uri = resolve_youtube_search_uri("打开YouTube搜索有关宋亚东的视频")
    assert uri == "https://www.youtube.com/results?search_query=%E5%AE%8B%E4%BA%9A%E4%B8%9C"


def test_youtube_non_search_task_has_no_route() -> None:
    assert resolve_youtube_search_uri("打开YouTube") is None


def test_youtube_misspelling_still_extracts_query() -> None:
    assert resolve_youtube_search_query("去youtobe上搜索关于dtu的视频") == "dtu"
