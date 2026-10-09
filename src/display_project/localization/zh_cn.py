from __future__ import annotations


CLASS_NAMES = {
    "hand": "手部",
    "screwdriver": "螺丝刀",
    "bolt": "螺栓",
    "metal bar": "金属条",
    "partial assembly": "部分装配体",
    "screw": "螺钉",
    "red fastener": "红色紧固件",
    "part": "部件",
    "fixture": "夹具",
    "tool": "工具",
}

STEP_NAMES = {
    "pick_components": "拿取零件",
    "align_components": "对齐零件",
    "insert_screw": "插入螺钉",
    "insert_bolt": "插入螺栓",
    "tighten_fastener": "紧固连接件",
    "tighten_screw": "拧紧螺钉",
    "verify_assembly": "检查装配",
    "retrieve_part": "取出部件",
    "fixture_part": "将部件放置到夹具",
    "perform_operation": "执行操作",
    "inspect_result": "检查结果",
}

ACTION_NAMES = {
    "align_objects": "对齐零件",
    "check_booklet": "查看装配说明书",
    "plug_screw": "插入螺钉",
    "screw_screw_with_hands": "用手旋紧螺钉",
    "take_bolt": "拿取螺栓",
    "take_gray_angled_perforated_bar": "拿取灰色角形孔条",
    "take_gray_perforated_bar": "拿取灰色孔条",
    "take_red_4_perforated_junction_bar": "拿取红色四孔连接条",
    "take_screw": "拿取螺钉",
    "tighten_bolt_with_hands": "用手紧固螺栓",
}

DECISION_NAMES = {"normal": "正常", "deviation": "偏差", "unknown": "证据不足"}
VERDICT_NAMES = {"pass": "通过", "fail": "不通过", "uncertain": "不确定"}
TASK_NAMES = {
    "meccano": "MECCANO装配",
    "industreal": "IndustReal装配",
    "industreal_component_state_audit": "IndustReal部件状态审计",
}


def class_zh(value: str) -> str:
    return CLASS_NAMES.get(value, value)


def step_zh(value: str | None) -> str:
    if value is None:
        return "未开始"
    return STEP_NAMES.get(value, value)


def action_zh(value: str | None) -> str:
    if value is None:
        return "证据不足"
    return ACTION_NAMES.get(value, value)


def decision_zh(value: str) -> str:
    return DECISION_NAMES.get(value, value)


def verdict_zh(value: str) -> str:
    return VERDICT_NAMES.get(value, value)


def task_zh(value: str | None) -> str:
    if value is None:
        return "未指定任务"
    return TASK_NAMES.get(value, value)
