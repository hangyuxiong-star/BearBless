from bearbless.message_intent import extract_confirmed_message


def test_extracts_colon_delimited_message():
    assert extract_confirmed_message("用QQ给红枣桂花熊发消息：今晚去吃饭") == "今晚去吃饭"


def test_extracts_natural_question_message():
    assert extract_confirmed_message("打开qq给红枣桂花熊发消息问他吃饭了吗") == "吃饭了吗"


def test_extracts_message_draft():
    assert extract_confirmed_message("给红枣桂花熊编辑消息草稿：吃饭了吗") == "吃饭了吗"
