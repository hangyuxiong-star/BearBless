from bearbless.message_intent import (
    extract_confirmed_message,
    extract_confirmed_recipient,
    is_qq_draft_request,
    is_wolt_to_qq_request,
)


def test_extracts_colon_delimited_message():
    assert extract_confirmed_message("用QQ给红枣桂花熊发消息：今晚去吃饭") == "今晚去吃饭"


def test_extracts_natural_question_message():
    assert extract_confirmed_message("打开qq给红枣桂花熊发消息问他吃饭了吗") == "吃饭了吗"


def test_extracts_recipient_and_message_when_send_verb_comes_first():
    scope = "打开wolt找家汉堡店，打开qq发信息给红枣桂花熊告诉他明天去吃"
    assert extract_confirmed_recipient(scope) == "红枣桂花熊"
    assert extract_confirmed_message(scope) == "明天去吃"


def test_shared_classifier_separates_single_and_compound_tasks():
    assert not is_wolt_to_qq_request("打开QQ给红枣桂花熊发信息说早点睡")
    assert not is_wolt_to_qq_request("在Wolt找一家汉堡店")
    assert is_wolt_to_qq_request(
        "在Wolt找一家汉堡店，然后打开QQ给红枣桂花熊发信息说晚上去这里吃"
    )


def test_extracts_message_draft():
    assert extract_confirmed_message("给红枣桂花熊编辑消息草稿：吃饭了吗") == "吃饭了吗"


def test_edit_information_wording_sends_unless_no_send_is_explicit():
    assert not is_qq_draft_request("去QQ编辑信息发给红枣桂花熊告诉他明天晚上去这吃")
    assert is_qq_draft_request("去QQ编辑信息草稿给红枣桂花熊，不要发送")
    assert not is_qq_draft_request("去QQ发给红枣桂花熊告诉他明天晚上去这吃")


def test_extracts_exact_qq_recipient_without_alias_guessing():
    assert extract_confirmed_recipient("打开QQ给红枣桂花熊发消息：晚上好") == "红枣桂花熊"
    assert extract_confirmed_recipient("打开QQ给其他人发消息：晚上好") == "其他人"
    assert extract_confirmed_recipient("打开Wolt找汉堡店去QQ发给红枣桂花熊") == "红枣桂花熊"
    assert extract_confirmed_recipient("去QQ给红枣桂花熊编辑消息草稿，不要发送") == "红枣桂花熊"
    assert extract_confirmed_recipient("去QQ发给红枣桂花熊告诉他晚上去这吃") == "红枣桂花熊"
    assert extract_confirmed_recipient("打开QQ，向红枣桂花熊发信息：早点睡") == "红枣桂花熊"
    assert extract_confirmed_recipient("打开QQ，向红枣桂花熊发送消息：早点睡") == "红枣桂花熊"
