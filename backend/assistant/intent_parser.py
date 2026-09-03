"""把中文改造指令解析为受限动作；不接受开放域问答。"""
from __future__ import annotations

import re


SUPPORTED_ACTIONS = ("ADD", "REMOVE", "MOVE", "RESIZE")
_OBJECT_ALIASES = {
    "书架": "bookshelf", "柜子": "cabinet", "衣柜": "wardrobe",
    "床": "bed", "桌": "desk", "书桌": "desk", "椅子": "chair",
    "置物架": "storage_rack", "杂物": "obstacle", "障碍": "obstacle",
    "扶手": "handrail", "夜灯": "night_light", "防滑垫": "anti_slip_mat",
}


def _number(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(厘米|cm|米|m)?", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    if (match.group(2) or "").lower() in {"厘米", "cm"}:
        value /= 100.0
    return value


def _target(text: str) -> str:
    for label, value in sorted(_OBJECT_ALIASES.items(), key=lambda item: -len(item[0])):
        if label in text:
            return value
    return "main_obstacle"


def parse_intent(text: str) -> dict:
    normalized = text.strip()
    if not normalized:
        raise ValueError("请输入具体的改造设想")
    if any(word in normalized for word in ("模拟全部建议", "全部建议", "一键模拟")):
        return {"action": "APPLY_ALL", "target": "all_advice", "raw": normalized}
    if any(word in normalized for word in ("最佳提升", "最大提升", "最优方案")):
        return {"action": "BEST", "target": "top_risks", "raw": normalized}
    if any(word in normalized for word in ("移除主要障碍", "清理主要障碍")):
        return {"action": "REMOVE", "target": "main_obstacle", "raw": normalized}

    if any(word in normalized for word in ("移除", "删除", "搬走", "清走", "拿走")):
        action = "REMOVE"
    elif any(word in normalized for word in ("移动", "挪", "靠墙", "离开通道", "往左", "往右", "前移", "后移")):
        action = "MOVE"
    elif any(word in normalized for word in ("缩小", "扩大", "加宽", "改成", "尺寸")):
        action = "RESIZE"
    elif any(word in normalized for word in ("增加", "添加", "安装", "放一个", "加装")):
        action = "ADD"
    else:
        raise ValueError("我只处理增加、移除、移动或调整尺寸的居家安全改造问题")

    direction = next((word for word in ("左", "右", "前", "后", "靠墙", "离开通道") if word in normalized), None)
    return {
        "action": action,
        "target": _target(normalized),
        "distance_m": _number(normalized),
        "direction": direction,
        "raw": normalized,
    }
