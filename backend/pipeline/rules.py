"""风险评估规则与综合评分（源自 PDF 阈值）。"""


def _formal_rule(
    rule_code: str,
    metric_code: str,
    category: str,
    threshold,
    direction: str,
    severity: str,
    advice_template: str,
) -> dict:
    return {
        "rule_code": rule_code,
        "metric_code": metric_code,
        "category": category,
        "threshold": threshold,
        "direction": direction,
        "severity": severity,
        "advice_template": advice_template,
        "reference": "reference_pending",
        "version": "1.0",
    }


FORMAL_RULES = [
    _formal_rule("main_passage_width_high", "main_passage_width", "mobility", 0.80, "below", "high",
                 "优先移开主要通道最窄处两侧家具，使净宽达到0.80米以上。"),
    _formal_rule("main_passage_width_medium", "main_passage_width", "mobility", 0.90, "below", "medium",
                 "调整主要通道家具布局，建议把连续净宽提升到0.90米以上。"),
    _formal_rule("minimum_passage_width_high", "minimum_passage_width", "mobility", 0.70, "below", "high",
                 "立即清理全屋最窄通行点，避免老人或助行器被卡住。"),
    _formal_rule("minimum_passage_width_medium", "minimum_passage_width", "mobility", 0.85, "below", "medium",
                 "扩大最窄通行点，并保持该位置长期无临时杂物。"),
    _formal_rule("door_width_high", "door_width", "mobility", 0.80, "below", "high",
                 "评估扩宽门洞或更换折叠门，优先保障助行器通行。"),
    _formal_rule("door_width_medium", "door_width", "mobility", 0.90, "below", "medium",
                 "减少门口占用并评估门扇开启后实际净宽。"),
    _formal_rule("entrance_space_high", "entrance_space", "mobility", 1.00, "below", "high",
                 "清空入口转身区域，移走影响进出和转向的家具或杂物。"),
    _formal_rule("entrance_space_medium", "entrance_space", "mobility", 1.50, "below", "medium",
                 "优化入口附近摆放，增加连续可用的转身空间。"),
    _formal_rule("path_length_medium", "path_length", "mobility", 8.00, "above", "medium",
                 "缩短入口到主要目标的绕行距离，并沿途设置稳定支撑点。"),
    _formal_rule("path_continuity_high", "path_continuity", "mobility", False, "equals", "high",
                 "恢复连续通路，消除路线中断并确认全程可达。"),
    _formal_rule("path_obstruction_high", "path_obstruction", "mobility", True, "equals", "high",
                 "移除路径上的固定或临时障碍，并重新确认通路连续。"),
    _formal_rule("furniture_spacing_high", "furniture_spacing", "layout", 0.30, "below", "high",
                 "拉开相邻家具，优先处理最小净距不足0.30米的位置。"),
    _formal_rule("furniture_spacing_medium", "furniture_spacing", "layout", 0.60, "below", "medium",
                 "调整相邻家具间距，减少侧身通过和碰撞风险。"),
    _formal_rule("wall_furniture_clearance_medium", "wall_furniture_clearance", "layout", 0.05, "below", "medium",
                 "检查贴墙家具是否形成难清洁或易夹碰的狭小缝隙。"),
    _formal_rule("bed_wall_distance_medium", "bed_wall_distance", "layout", 0.10, "below", "medium",
                 "根据照护方式评估床贴墙侧，并确保另一侧保留可靠照护空间。"),
    _formal_rule("bedside_clearance_high", "bedside_clearance", "layout", 0.40, "below", "high",
                 "移开床侧最近家具，为上下床和照护操作腾出空间。"),
    _formal_rule("bedside_clearance_medium", "bedside_clearance", "layout", 0.60, "below", "medium",
                 "扩大床侧净空，减少夜间起身时的磕碰风险。"),
    _formal_rule("activity_area_high", "activity_area", "layout", 2.00, "below", "high",
                 "清理主要活动区域，形成连续且无障碍的活动空间。"),
    _formal_rule("activity_area_medium", "activity_area", "layout", 3.00, "below", "medium",
                 "减少活动区内非必要家具，扩大可自由转身范围。"),
    _formal_rule("crowding_high", "crowding", "layout", 0.60, "above", "high",
                 "降低家具占地比例，优先移除低频使用或阻挡通路的物品。"),
    _formal_rule("crowding_medium", "crowding", "layout", 0.45, "above", "medium",
                 "优化家具布局，避免活动空间被连续占用。"),
    _formal_rule("bed_surrounding_space_high", "bed_surrounding_space", "usage_safety", 0.40, "below", "high",
                 "立即移开床周边最近障碍，保证起身和照护路线。"),
    _formal_rule("bed_surrounding_space_medium", "bed_surrounding_space", "usage_safety", 0.60, "below", "medium",
                 "扩大床周边最小净空，并保持夜间路线无临时物品。"),
    _formal_rule("main_activity_area_safety_high", "main_activity_area_safety", "usage_safety", False, "equals", "high",
                 "修复入口到主要活动区的中断或障碍，再开放该区域使用。"),
]

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


def _assessment_status(level: str) -> str:
    if level == "unknown":
        return "not_evaluable"
    return "evaluated_safe" if level == "green" else "evaluated_risk"


def _eligibility(measures: dict, code: str) -> tuple[bool, str | None]:
    raw = (measures.get("risk_eligibility") or {}).get(code)
    if raw is None:
        return True, None
    if isinstance(raw, dict):
        status = raw.get("status") or raw.get("risk_eligibility")
        return status in {"eligible", "verified", True}, raw.get("reason")
    return raw in {"eligible", "verified", True}, None


def _risk(rule: dict, level: str, measure, reason: str | None = None) -> dict:
    return {
        **rule, "level": level, "measure": measure,
        "assessment_status": _assessment_status(level),
        "reason": reason if level == "unknown" else None,
    }


def evaluate_risks(measures: dict, *, include_not_evaluable: bool = False) -> list[dict]:
    """只让通过 measurement gate 的值进入风险判定。"""
    risks = []
    for rule in RULES:
        key = rule["code"]
        eligible, eligibility_reason = _eligibility(measures, key)
        if not eligible:
            if include_not_evaluable:
                risks.append(_risk(
                    rule, "unknown", None,
                    eligibility_reason or "insufficient_measurement_confidence",
                ))
            continue
        if key == "stairs":
            if "stairs_exist" not in measures:
                if include_not_evaluable:
                    risks.append(_risk(rule, "unknown", None, "measurement_unavailable"))
            elif measures.get("stairs_exist") is None:
                risks.append(_risk(rule, "unknown", None, "measurement_unavailable"))
            elif measures.get("stairs_exist"):
                risks.append(_risk(rule, "red", True))
            elif include_not_evaluable:
                risks.append(_risk(rule, "green", False))
            continue
        if key == "obstacle":
            if "obstacles_in_passage" not in measures:
                if include_not_evaluable:
                    risks.append(_risk(rule, "unknown", None, "measurement_unavailable"))
                continue
            obs = measures["obstacles_in_passage"]
            if obs is None:
                risks.append(_risk(rule, "unknown", None, "insufficient_measurement_confidence"))
            elif obs:
                risks.append(_risk(rule, "red", obs))
            else:
                risks.append(_risk(rule, "green", []))
            continue
        if key not in measures:
            alt = key + "_m"
            if alt not in measures:
                if include_not_evaluable:
                    risks.append(_risk(rule, "unknown", None, "measurement_unavailable"))
                continue
            value = measures[alt]
        else:
            value = measures[key]
        level = _level(rule, value)
        risks.append(_risk(
            rule, level, value,
            "insufficient_measurement_confidence" if level == "unknown" else None,
        ))
    return risks


def compute_score(measures: dict, *, include_not_evaluable: bool = False) -> tuple[float | None, dict]:
    """只按已确认风险加权评分；未知项单独计入评估完整度。

    仅当所有风险项均为 unknown 时返回 score=None——「无法评分」必须与
    「零风险」区分，避免给用户一个假满分；部分未知时保持原有加权逻辑。
    """
    risks = evaluate_risks(measures, include_not_evaluable=include_not_evaluable)
    cat_map = {
        "door_width": "通行性", "passage_width": "通行性", "bathroom_door": "通行性",
        "threshold": "跌倒风险", "stairs": "跌倒风险", "slope": "跌倒风险", "uneven": "跌倒风险",
        "obstacle": "无障碍",
    }
    parts = {}
    for cat, w in WEIGHTS.items():
        cat_risks = [r for r in risks if cat_map.get(r["code"]) == cat]
        confirmed_risks = [r for r in cat_risks if r["level"] != "unknown"]
        if not confirmed_risks:
            parts[cat] = None
            continue
        worst = min({"red": 0, "yellow": 0.5, "green": 1.0}[r["level"]]
                    for r in confirmed_risks)
        parts[cat] = round(worst * 100, 1)
    confirmed = [r for r in risks if r["level"] != "unknown"]
    evaluated_categories = [category for category, value in parts.items() if value is not None]
    evaluated_weight = sum(WEIGHTS[category] for category in evaluated_categories)
    score = (
        round(sum(parts[category] * WEIGHTS[category] for category in evaluated_categories) / evaluated_weight, 1)
        if confirmed and evaluated_weight > 0 else None
    )
    unknown_count = sum(r["level"] == "unknown" for r in risks)
    known_count = len(risks) - unknown_count
    completeness = round(known_count / len(risks) * 100, 1) if risks else 100.0
    safe_count = sum(r["assessment_status"] == "evaluated_safe" for r in risks)
    risk_count = sum(r["assessment_status"] == "evaluated_risk" for r in risks)
    return score, {
        "parts": parts,
        "risks": risks,
        "assessment_completeness": {
            "known_count": known_count,
            "unknown_count": unknown_count,
            "percent": completeness,
        },
        "risk_assessment_coverage": {
            "evaluated_count": known_count, "not_evaluable_count": unknown_count,
            "total_count": len(risks), "percent": completeness,
            "evaluated_safe_count": safe_count, "evaluated_risk_count": risk_count,
        },
    }
