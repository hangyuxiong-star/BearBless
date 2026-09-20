from bearbless.dashboard_data import build_local_task_analysis


def test_qq_analysis_exposes_deterministic_route_and_exact_scope():
    analysis = build_local_task_analysis("打开qq给红枣桂花熊发消息问他吃饭了吗")
    assert "红枣桂花熊" in analysis["intent"]
    assert "吃饭了吗" in analysis["intent"]
    assert "ACTION_SEND" in analysis["route"]
    assert "不可重放" in analysis["guard"]


def test_wolt_analysis_names_proof_fields():
    analysis = build_local_task_analysis("打开 Wolt 找评分高的汉堡店和地址")
    assert "Food type" in analysis["route"]
    assert "评分" in analysis["proof"]
    assert "地址" in analysis["proof"]


def test_youtube_analysis_uses_search_deep_link_route():
    analysis = build_local_task_analysis("去youtobe上搜索关于dtu的视频")
    assert "YouTube" in analysis["intent"]
    assert "搜索深链" in analysis["route"]
    assert "不点赞" in analysis["guard"]
