"""风险评估规则与综合评分（源自 PDF 阈值）。"""

RULES = [
    {"code": "door_width", "name": "门宽", "red": 0.80, "yellow": 0.90, "unit": "m",
     "advice": "门宽不足 80cm，轮椅无法通行，建议扩门或改用折叠门。"},
    {"code": "passage_width", "name": "通道净宽", "red": 0.90, "yellow": 1.20, "unit": "m",
     "advice": "通道过窄，建议清理通道或调整家具布局。"},
    {"code": "threshold", "name": "门槛高度", "red": 0.02, "yellow": 0.01, "unit": "m",
     "advice": "门槛过高易绊倒，建议安装斜坡过渡条。"},
    {"code": "stairs", "name": "台阶", "red": 0.0, "yellow": None, "unit": "",
     "advice": "存在台阶且无扶手，建议安装扶手或坡道。"},
    {"code": "slope", "name": "地面坡度", "red": 0.05, "yellow": 0.02, "unit": "",
     "advice": "地面坡度超标，轮椅有溜坡风险。"},
    {"code": "uneven", "name": "地面高差/不平", "red": 0.015, "yellow": None, "unit": "m",
     "advice": "地面高差超过 1.5cm，建议找平或加缓坡。"},
    {"code": "obstacle", "name": "通道障碍物", "red": 0.0, "yellow": None, "unit": "",
     "advice": "通道内存在杂物/障碍物，建议移除以保证通行。"},
    {"code": "bathroom_door", "name": "卫生间门口", "red": 0.75, "yellow": 0.85, "unit": "m",
     "advice": "卫生间门口过窄，轮椅无法进出。"},
]

WEIGHTS = {"通行性": 0.4, "跌倒风险": 0.4, "无障碍": 0.2}

# 数值越大越危险的规则（超过阈值才判风险）；其余规则为越小越危险（低于阈值判风险）。
_HIGHER_IS_WORSE = {"threshold", "slope", "uneven"}


def _level(rule: dict, value: float | None) -> str:
    if value is None:
        return "unknown"
    if rule["code"] in _HIGHER_IS_WORSE:
        if rule["red"] is not None and value > rule["red"]:
            return "red"
        if rule["yellow"] is not None and value > rule["yellow"]:
            return "yellow"
    else:
        if rule["red"] is not None and value < rule["red"]:
            return "red"
        if rule["yellow"] is not None and value < rule["yellow"]:
            return "yellow"
    return "green"


def evaluate_risks(measures: dict) -> list[dict]:
    """输入测量值（含 obstacles_in_passage 列表），输出风险项列表。"""
    risks = []
    for rule in RULES:
        key = rule["code"]
        if key == "stairs":
            if measures.get("stairs_exist"):
                risks.append({**rule, "level": "red", "measure": None})
            continue
        if key == "obstacle":
            obs = measures.get("obstacles_in_passage", [])
            if obs:
                risks.append({**rule, "level": "red", "measure": obs})
            continue
        if key not in measures:
            alt = key + "_m"
            if alt not in measures:
                continue
            value = measures[alt]
        else:
            value = measures[key]
        risks.append({**rule, "level": _level(rule, value), "measure": value})
    return risks


def compute_score(measures: dict) -> tuple[float, dict]:
    """加权评分：每类取最差风险扣分。返回 (总分 0~100, 明细)。"""
    risks = evaluate_risks(measures)
    cat_map = {
        "door_width": "通行性", "passage_width": "通行性", "bathroom_door": "通行性",
        "threshold": "跌倒风险", "stairs": "跌倒风险", "slope": "跌倒风险", "uneven": "跌倒风险",
        "obstacle": "无障碍",
    }
    parts = {}
    for cat, w in WEIGHTS.items():
        cat_risks = [r for r in risks if cat_map.get(r["code"]) == cat]
        worst = max(({"red": 0, "yellow": 0.5, "green": 1.0, "unknown": 0.6}.get(r["level"], 0.6)
                     for r in cat_risks), default=1.0)
        parts[cat] = round(worst * 100, 1)
    score = round(sum(parts[c] * WEIGHTS[c] for c in parts), 1)
    return score, {"parts": parts, "risks": risks}
