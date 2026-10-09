from display_project.localization import action_zh, class_zh, decision_zh, step_zh, task_zh, verdict_zh


def test_detection_and_sop_terms_translate_to_chinese():
    assert class_zh("screwdriver") == "螺丝刀"
    assert class_zh("partial assembly") == "部分装配体"
    assert step_zh("insert_bolt") == "插入螺栓"
    assert step_zh("verify_assembly") == "检查装配"
    assert action_zh("tighten_bolt_with_hands") == "用手紧固螺栓"
    assert task_zh("meccano") == "MECCANO装配"
    assert decision_zh("unknown") == "证据不足"
    assert verdict_zh("uncertain") == "不确定"


def test_missing_state_has_chinese_fallback():
    assert step_zh(None) == "未开始"
    assert action_zh(None) == "证据不足"
