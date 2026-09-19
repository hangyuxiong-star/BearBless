from bearbless.agent.policy import infer_task_mode, may_mutate_phone
from bearbless.schemas import TaskMode


def test_status_question_is_read_only_even_when_opening_settings():
    mode = infer_task_mode("打开设置，查看蓝牙是否已开启")
    assert mode == TaskMode.READ_ONLY_QUERY
    assert may_mutate_phone(mode) is False


def test_explicit_state_change_is_mutating():
    assert infer_task_mode("打开设置并开启蓝牙") == TaskMode.MUTATING_TASK


def test_payment_and_sending_are_sensitive():
    assert infer_task_mode("给朋友发送一条消息") == TaskMode.SENSITIVE_TASK
    assert infer_task_mode("打开qq给朋友发信息：吃饭了吗") == TaskMode.SENSITIVE_TASK
    assert infer_task_mode("打开Wolt找一家汉堡店，然后去QQ告诉红枣桂花熊晚上去那里吃") == TaskMode.SENSITIVE_TASK


def test_tell_me_remains_a_read_only_query():
    assert infer_task_mode("搜索一家汉堡店并告诉我地址") == TaskMode.READ_ONLY_QUERY


def test_short_chinese_negation_does_not_make_read_only_search_sensitive():
    assert infer_task_mode("打开Wolt找高评分汉堡店，不下单") == TaskMode.READ_ONLY_QUERY
