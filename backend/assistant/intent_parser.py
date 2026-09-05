"""把中文改造指令解析为受限几何操作；不支持的功能直接拒绝。"""
from __future__ import annotations

import re

_OBJECT_ALIASES = {
    "书桌": "desk", "书柜": "bookshelf", "书架": "bookshelf", "衣柜": "wardrobe",
    "柜子": "cabinet", "床": "bed", "桌子": "table", "椅子": "chair",
    "置物架": "storage_rack", "箱子": "box", "床头柜": "nightstand",
}
_REMOVED_FEATURES = ("夜灯", "扶手", "防滑垫", "碰撞", "墙体边界", "3D改造", "预览", "撞墙")

_DIRECTIONS = {
    "左": (-1.0, 0.0), "右": (1.0, 0.0),
    "前": (0.0, 1.0), "上": (0.0, 1.0),
    "后": (0.0, -1.0), "下": (0.0, -1.0),
}


def _distance(text: str) -> float | None:
    """'30厘米' / '0.3米' / '30cm' → 米；裸数字按厘米。"""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(厘米|cm|米|m)?", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit in {"厘米", "cm"}:
        return value / 100.0
    if unit in {"米", "m"}:
        return value
    return value / 100.0  # 无单位默认厘米


def _dims(text: str) -> list[float] | None:
    """'60×40×50厘米' → [0.6, 0.4, 0.5]；'80×50厘米' → [0.8, 0.5]。"""
    match = re.search(r"(\d+(?:\.\d+)?)\s*[×xX*]\s*(\d+(?:\.\d+)?)"
                      r"(?:\s*[×xX*]\s*(\d+(?:\.\d+)?))?\s*(厘米|cm|米|m)?", text)
    if not match:
        return None
    scale = 0.01 if (match.group(4) or "").lower() in {"厘米", "cm", ""} else 1.0
    values = [float(v) * scale for v in match.groups()[:3] if v]
    return values


def _target(text: str) -> str | None:
    for label in sorted(_OBJECT_ALIASES, key=len, reverse=True):
        if label in text:
            return _OBJECT_ALIASES[label]
    return None


def parse_intent(text: str) -> dict:
    normalized = text.strip()
    if not normalized:
        raise ValueError("请输入具体的改造设想")
    for word in _REMOVED_FEATURES:
        if word in normalized:
            raise ValueError(f"“{word}”相关功能当前不做，本助手只支持移动家具和放置物品两类评估")
    if any(word in normalized for word in ("完成建议", "执行建议", "勾选建议")):
        ids = [int(x) for x in re.findall(r"建议\s*(\d+)", normalized)]
        if not ids and "全部" in normalized:
            ids = ["all"]
        if not ids:
            raise ValueError("请写明执行哪几条建议，例如：完成建议1和建议3")
        return {"action": "APPLY_SUGGESTIONS", "suggestion_ids": ids, "raw": normalized}

    action = None
    if any(word in normalized for word in ("移除", "搬走", "清走", "拿走", "删除")):
        action = "REMOVE"
    elif any(word in normalized for word in ("移动", "挪", "平移", "往", "向")):
        action = "MOVE"
    elif any(word in normalized for word in ("放", "增加", "添加", "摆")):
        action = "ADD"
    if action is None:
        raise ValueError("我只处理两类改造：移动现有家具、或在某处放置一个指定尺寸的物体")

    target = _target(normalized)
    if action == "MOVE":
        if target is None:
            raise ValueError("请说明移动哪件家具，例如：把床向左移动30厘米")
        direction = next((_DIRECTIONS[w] for w in sorted(_DIRECTIONS, key=len, reverse=True)
                          if w in normalized), None)
        distance = _distance(normalized)
        if direction is None:
            if any(w in normalized for w in ("靠墙", "贴墙")):
                return {"action": "MOVE", "target": target, "toward_wall": True,
                        "distance_m": distance, "raw": normalized}
            if "门" in normalized:
                return {"action": "MOVE", "target": target, "toward_door": True,
                        "distance_m": distance, "raw": normalized}
            raise ValueError("请说明移动方向，例如：向左/向右/向前/向后/靠墙/向门")
        if distance is None:
            raise ValueError("请说明移动距离，例如：移动30厘米")
        return {"action": "MOVE", "target": target, "dx": direction[0], "dy": direction[1],
                "distance_m": distance, "raw": normalized}
    if action == "ADD":
        dims = _dims(normalized)
        if dims is None or len(dims) < 2:
            raise ValueError("请给出物体的实际尺寸，例如：放一个60×40×50厘米的箱子")
        while len(dims) < 3:
            dims.append(0.4)
        position = normalized.split("放")[0].strip() if "放" in normalized else ""
        anchor = _target(position) if position else None
        return {"action": "ADD", "size_m": dims, "position": position,
                "target": anchor, "raw": normalized}
    # REMOVE
    if target is None:
        target = "obstacle"
    return {"action": "REMOVE", "target": target, "raw": normalized}
