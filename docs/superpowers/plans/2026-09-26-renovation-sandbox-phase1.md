# 改造沙盒（阶段一：同源重算与结果闭环）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让改造建议与改后评分全部走正式评估链路，并把改后布局以叠影图返回前端，消除"许愿分"与平行指标引擎。

**Architecture:** 新增 `pipeline/scheme_state.py`（内存态编辑 + 约束校验）与 `pipeline/scheme_assessment.py`（复用 `analyze_structure_passages → build_spatial_foundation → build_spatial_assessment_inputs → build_risk_assessment`）作为唯一评分入口；`renovation_simulator.py` 的投影路径删除；新增 `scheme_solver.py` 做搜索式求解；方案持久化到 `renovation_schemes` 表；Flutter 端用一套 `CustomPainter` 画叠影图并导出 PNG，PDF 由服务端封装。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / Alembic / pytest / reportlab（后端）；Flutter 3 / Dart 3.12 / dio / provider（前端）。

**Spec:** `docs/superpowers/specs/2026-09-26-renovation-sandbox-design.md`

## Global Constraints

- 只读输入：`data/work/{scan_id}/postprocess/measurements.json` 与 `structure_calibrated.json`（缺失回退 `structure.json`）；禁止读取视频、图像、点云、SLAM3R/SpatialLM 中间产物。
- 所有模拟在内存副本上进行，不写回任何真实产物；响应固定带 `scope: {simulation: true, raw_media_accessed: false, structured_inputs_only: true}`。
- 评分唯一真源：`analyze_structure_passages → build_spatial_foundation → build_spatial_assessment_inputs → build_risk_assessment`。
- 家具一律用 `instance_id` 引用，禁止数组下标。
- 新增物体必须带 `measurement_status="assumed"`，界面与导出标注"假设放置，非实测"。
- 正式 `spatial_metrics.json` / `risk_assessment.json` / 报告保持只读，实测分数永不被覆盖。
- 后端既有 4 个已知失败测试不得增加（见 `docs/risk-assessment-baseline.md`）；新增测试必须全绿。
- 单位统一米，角度统一度（`rotation_z_deg`）；风险等级取值 `high|medium|low`。
- 所有后端命令在 `backend/` 下用 `.venv` 执行；所有 Flutter 命令在 `app/` 下执行。

**工作目录约定：** 后端命令 `cd backend`；使用 `.\.venv\Scripts\python.exe -m pytest …`。前端命令 `cd app`；使用 `flutter test …`。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `backend/pipeline/scheme_state.py`（新增） | edits → 派生 measurements/structure；边界与重叠约束；冲突报告 |
| `backend/pipeline/scheme_assessment.py`（新增） | 官方链路评估入口 + 前后差异计算 |
| `backend/pipeline/scheme_layout.py`（新增） | 派生状态 → layout（2D/3D 共用的渲染数据） |
| `backend/pipeline/scheme_solver.py`（新增） | 搜索式求解：候选生成 → 官方链路验证 → 取最优 |
| `backend/pipeline/scheme_pdf.py`（新增） | 方案附录 PDF 封装 |
| `backend/pipeline/space_foundation.py`（修改） | `_prepare_inputs` 接受 `assumed` 状态 |
| `backend/pipeline/renovation_simulator.py`（修改） | 删除投影评分路径与平行指标出分 |
| `backend/app/models.py`（修改） | 新增 `RenovationScheme` 表 |
| `backend/app/routers/renovation.py`（修改） | `/simulate` 同源化、`/solve`、`/schemes` CRUD、导出 |
| `backend/migrations/versions/20260926_0005_renovation_schemes.py`（新增） | 建表迁移 |
| `app/lib/models/scheme_layout.dart`（新增） | 布局与叠影数据模型 |
| `app/lib/widgets/scheme_plan_painter.dart`（新增） | 叠影图绘制（屏幕与导出共用一套） |
| `app/lib/services/scheme_export.dart`（新增） | 离屏渲染 PNG + 保存/分享、调用 PDF 接口 |
| `app/lib/api/client.dart`（修改） | 新增方案相关接口 |
| `app/lib/pages/report_page.dart`（修改） | 助手卡片 → 方案结果卡 |

---

### Task 1: 内存态方案状态与官方评估入口

**Files:**
- Create: `backend/pipeline/scheme_state.py`
- Create: `backend/pipeline/scheme_layout.py`
- Create: `backend/pipeline/scheme_assessment.py`
- Modify: `backend/pipeline/space_foundation.py`（`_prepare_inputs`，约第 269 行）
- Test: `backend/tests/test_scheme_state.py`

**Interfaces:**
- Consumes: `pipeline.space_foundation.analyze_structure_passages(measurements, structure) -> dict`、`build_spatial_foundation(measurements, structure, passage) -> dict`、`pipeline.spatial_assessment_inputs.build_spatial_assessment_inputs(measurements, passage, foundation) -> dict`、`pipeline.risk_assessment.build_risk_assessment(payload) -> dict`
- Produces:
  - `pipeline.scheme_state.apply_edits(measurements: dict, structure: dict, edits: list[dict]) -> dict`，返回 `{"measurements", "structure", "conflicts", "applied_edits", "assumed_ids"}`
  - `pipeline.scheme_state.overlap_depth(a_corners, b_corners) -> float`
  - `pipeline.scheme_assessment.assess_scheme(measurements, structure) -> dict`，返回 `{"metrics", "paths", "risks", "overall", "category_scores", "top_risks"}`
  - `pipeline.scheme_assessment.build_scheme(measurements, structure, edits) -> dict`（`apply_edits` + `assess_scheme` + `scope`）
  - `pipeline.scheme_assessment.diff_assessments(before: dict, after: dict) -> dict`，返回 `{"comparison", "metric_changes", "risk_changes"}`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_scheme_state.py`：

```python
import json
from pathlib import Path

import pytest

from pipeline.scheme_assessment import assess_scheme, build_scheme, diff_assessments
from pipeline.scheme_state import apply_edits, overlap_depth, _rect_of_measurement

SCAN46 = Path(__file__).resolve().parents[1] / "data" / "work" / "46" / "postprocess"


def _measurements():
    return {
        "room": {"length_m": 4.0, "width_m": 4.0, "height_m": 2.7,
                 "measurement_status": "verified", "confidence": "medium"},
        "openings": [
            {"id": "door_01", "type": "door", "center": [0.6, 0.0, 1.05],
             "width_m": 0.85, "height_m": 2.1, "rotation_z_deg": 0.0,
             "measurement_status": "verified"},
        ],
        "objects": [
            {"id": "bed_001", "instance_id": "bed_001", "type": "bed",
             "center": [1.0, 3.0, 0.3], "length_m": 2.0, "width_m": 1.2, "height_m": 0.6,
             "rotation_z_deg": 0.0, "measurement_status": "verified", "confidence": "medium"},
            {"id": "desk_001", "instance_id": "desk_001", "type": "desk",
             "center": [1.2, 2.0, 0.4], "length_m": 1.0, "width_m": 0.5, "height_m": 0.8,
             "rotation_z_deg": 0.0, "measurement_status": "verified", "confidence": "medium"},
        ],
    }


def _structure():
    return {
        "room": {"bounds_xy": {"min": [0.0, 0.0], "max": [4.0, 4.0]}, "height_m": 2.7},
        "walls": [], "geometric_obstacles": [],
        "doors": [{"instance_id": "door_01", "center": [0.6, 0.0, 1.05],
                   "size": [0.85, 0.12, 2.1], "rotation_z_deg": 0.0}],
        "semantic_instances": [
            {"instance_id": "bed_001", "label": "bed", "center": [1.0, 3.0, 0.3], "size": [2.0, 1.2, 0.6]},
            {"instance_id": "desk_001", "label": "desk", "center": [1.2, 2.0, 0.4], "size": [1.0, 0.5, 0.8]},
        ],
    }


def test_overlap_depth_of_two_rects():
    a = [{"center": [0.0, 0.0, 0.0], "length_m": 1.0, "width_m": 1.0, "rotation_z_deg": 0.0}]
    b = [{"center": [0.7, 0.0, 0.0], "length_m": 1.0, "width_m": 1.0, "rotation_z_deg": 0.0}]
    assert overlap_depth(_rect_of_measurement(a[0]), _rect_of_measurement(b[0])) == pytest.approx(0.3, abs=1e-6)


def test_empty_edits_reproduce_official_assessment():
    m, s = _measurements(), _structure()
    direct = assess_scheme(m, s)
    result = build_scheme(m, s, [])
    assert result["overall"] == direct["overall"]
    assert [x["metric_code"] for x in result["metrics"]] == [x["metric_code"] for x in direct["metrics"]]
    assert result["scope"]["simulation"] is True
    assert result["scope"]["raw_media_accessed"] is False


def test_move_changes_metric_and_keeps_inputs_untouched():
    m, s = _measurements(), _structure()
    before = build_scheme(m, s, [])
    after = build_scheme(m, s, [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -1.0}])
    spacing_before = next(x["value"] for x in before["metrics"] if x["metric_code"] == "furniture_spacing")
    spacing_after = next(x["value"] for x in after["metrics"] if x["metric_code"] == "furniture_spacing")
    assert spacing_after != spacing_before
    assert m["objects"][1]["center"] == [1.2, 2.0, 0.4]  # 原输入未被修改


def test_overlap_is_reported_and_rejected():
    m, s = _measurements(), _structure()
    baseline = build_scheme(m, s, [])
    result = build_scheme(m, s, [{"op": "move", "target": "desk_001", "dx": 0.2, "dy": 0.9}])
    assert result["conflicts"], "重叠必须被报告"
    assert result["conflicts"][0]["type"] == "overlap"
    assert result["overall"]["score"] == baseline["overall"]["score"]


def test_out_of_room_is_reported_and_rejected():
    m, s = _measurements(), _structure()
    result = build_scheme(m, s, [{"op": "move", "target": "bed_001", "dx": 0.0, "dy": 2.0}])
    assert result["conflicts"][0]["type"] == "out_of_room"


def test_remove_and_add_participate_in_scoring():
    m, s = _measurements(), _structure()
    before = build_scheme(m, s, [])
    removed = build_scheme(m, s, [{"op": "remove", "target": "desk_001"}])
    added = build_scheme(m, s, [{"op": "add", "label": "箱子", "size": [0.6, 0.4, 0.5], "center": [3.0, 3.0, 0.25]}])
    crowd_before = next(x["value"] for x in before["metrics"] if x["metric_code"] == "crowding")
    crowd_removed = next(x["value"] for x in removed["metrics"] if x["metric_code"] == "crowding")
    crowd_added = next(x["value"] for x in added["metrics"] if x["metric_code"] == "crowding")
    assert crowd_removed < crowd_before < crowd_added
    assert added["assumed_ids"] == ["assumed_1"]
    assert added["scope"]["contains_assumed_objects"] is True


def test_rotate_90_swaps_footprint():
    m, s = _measurements(), _structure()
    rotated = build_scheme(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": 1}])
    desk = next(x for x in rotated["layout_objects"] if x["id"] == "desk_001")
    assert desk["size"][0] == pytest.approx(0.5)
    assert desk["size"][1] == pytest.approx(1.0)


def test_resize_door_changes_door_width_metric():
    m, s = _measurements(), _structure()
    widened = build_scheme(m, s, [{"op": "resize_door", "width_m": 0.90}])
    value = next(x["value"] for x in widened["metrics"] if x["metric_code"] == "door_width")
    assert value == pytest.approx(0.90, abs=1e-6)


def test_diff_assessments_reports_changes():
    m, s = _measurements(), _structure()
    before = build_scheme(m, s, [])
    after = build_scheme(m, s, [{"op": "remove", "target": "desk_001"}])
    diff = diff_assessments(before, after)
    assert "before_score" in diff["comparison"] and "after_score" in diff["comparison"]
    assert any(x["metric_code"] == "crowding" for x in diff["metric_changes"])


@pytest.mark.skipif(not (SCAN46 / "spatial_metrics.json").is_file(), reason="scan 46 artifacts not present")
def test_golden_scan46_matches_official_metrics():
    m = json.loads((SCAN46 / "measurements.json").read_text(encoding="utf-8"))
    s = json.loads((SCAN46 / "structure_calibrated.json").read_text(encoding="utf-8"))
    official = json.loads((SCAN46 / "spatial_metrics.json").read_text(encoding="utf-8"))
    official_risks = json.loads((SCAN46 / "risk_assessment.json").read_text(encoding="utf-8"))
    result = build_scheme(m, s, [])
    assert {x["metric_code"]: x["value"] for x in result["metrics"]} == \
           {x["metric_code"]: x["value"] for x in official["metrics"]}
    assert result["overall"]["score"] == official_risks["overall"]["score"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_scheme_state.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'pipeline.scheme_state'`

- [ ] **Step 3: 实现 scheme_state.py**

创建 `backend/pipeline/scheme_state.py`：

```python
"""改造方案的派生状态：在内存副本上应用 edits，绝不触碰真实产物。

约束：
- 只处理 measurements.objects / measurements.openings / structure.semantic_instances；
- 越界、与其他家具重叠的编辑一律拒绝并记入 conflicts；
- 新增物体的 measurement_status 恒为 "assumed"，由 space_foundation 显式接受。
"""
from __future__ import annotations

import math
from copy import deepcopy

ASSUMED_STATUS = "assumed"
_EDGE_TOL = 1e-6


def object_id(item: dict) -> str:
    return str(item.get("instance_id") or item.get("id") or "")


def _corners(center, length, width, rotation_deg) -> list[tuple[float, float]]:
    t = math.radians(float(rotation_deg or 0.0))
    c, s = math.cos(t), math.sin(t)
    hx, hy = float(length) / 2.0, float(width) / 2.0
    return [
        (center[0] + sx * hx * c - sy * hy * s, center[1] + sx * hx * s + sy * hy * c)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]


def _rect_of_measurement(item: dict) -> list[tuple[float, float]]:
    return _corners(item["center"], item.get("length_m") or 0.0,
                    item.get("width_m") or 0.0, item.get("rotation_z_deg") or 0.0)


def _axes(corners):
    axes = []
    for i, (x1, y1) in enumerate(corners):
        x2, y2 = corners[(i + 1) % len(corners)]
        nx, ny = -(y2 - y1), (x2 - x1)
        norm = math.hypot(nx, ny) or 1.0
        axes.append((nx / norm, ny / norm))
    return axes


def _project(corners, axis) -> tuple[float, float]:
    values = [x * axis[0] + y * axis[1] for x, y in corners]
    return min(values), max(values)


def overlap_depth(a_corners, b_corners) -> float:
    """两个矩形的重叠深度（米）；不相交返回 0.0（SAT）。"""
    if not a_corners or not b_corners:
        return 0.0
    best = float("inf")
    for axis in _axes(a_corners) + _axes(b_corners):
        a_min, a_max = _project(a_corners, axis)
        b_min, b_max = _project(b_corners, axis)
        overlap = min(a_max, b_max) - max(a_min, b_min)
        if overlap <= _EDGE_TOL:
            return 0.0
        best = min(best, overlap)
    return float(best)


def _inside_room(corners, bounds: dict) -> bool:
    lo = (bounds or {}).get("min")
    hi = (bounds or {}).get("max")
    if not lo or not hi:
        return True
    return all(lo[0] - _EDGE_TOL <= x <= hi[0] + _EDGE_TOL
               and lo[1] - _EDGE_TOL <= y <= hi[1] + _EDGE_TOL for x, y in corners)


def _find(container: list[dict], target: str) -> dict | None:
    for item in container:
        if object_id(item) == target:
            return item
    return None


def apply_edits(measurements: dict, structure: dict, edits: list[dict]) -> dict:
    """返回改后的 measurements / structure，以及冲突与被采纳的编辑。"""
    m, s = deepcopy(measurements), deepcopy(structure)
    conflicts: list[dict] = []
    applied: list[dict] = []
    assumed_ids: list[str] = []
    bounds = (s.get("room") or {}).get("bounds_xy") or {}

    for edit in edits or []:
        op = edit.get("op")
        if op == "resize_door":
            width = float(edit.get("width_m") or 0.90)
            for opening in m.get("openings", []):
                if opening.get("type") == "door":
                    opening["width_m"] = width
            for door in s.get("doors", []):
                if door.get("size"):
                    door["size"][0] = width
            applied.append(dict(edit))
            continue

        target = str(edit.get("target") or "")
        if op == "add":
            size = [float(v) for v in edit.get("size") or [0.4, 0.4, 0.4]]
            center = [float(v) for v in edit.get("center") or [0.0, 0.0, size[2] / 2]]
            oid = f"assumed_{len(assumed_ids) + 1}"
            candidate = {"center": center, "length_m": size[0], "width_m": size[1],
                         "rotation_z_deg": float(edit.get("rotation_z_deg") or 0.0)}
            corners = _rect_of_measurement(candidate)
            if not _inside_room(corners, bounds):
                conflicts.append({"type": "out_of_room", "target": oid, "op": "add"})
                continue
            other = next(({"id": object_id(o), "depth": overlap_depth(corners, _rect_of_measurement(o))}
                          for o in m.get("objects", []) if overlap_depth(corners, _rect_of_measurement(o)) > _EDGE_TOL), None)
            if other:
                conflicts.append({"type": "overlap", "target": oid, "other": other["id"],
                                  "overlap_m": round(other["depth"], 3), "op": "add"})
                continue
            label = edit.get("label") or "object"
            m.setdefault("objects", []).append({
                "id": oid, "instance_id": oid, "type": label, "center": center,
                "length_m": size[0], "width_m": size[1], "height_m": size[2],
                "rotation_z_deg": candidate["rotation_z_deg"],
                "measurement_status": ASSUMED_STATUS, "measurement_reason": "user_assumption",
                "confidence": None,
            })
            s.setdefault("semantic_instances", []).append({
                "instance_id": oid, "label": label, "center": center, "size": list(size),
                "rotation_z_deg": candidate["rotation_z_deg"], "assumed": True,
            })
            assumed_ids.append(oid)
            applied.append({**edit, "target": oid})
            continue

        item = _find(m.get("objects", []), target)
        sit = _find(s.get("semantic_instances", []), target)
        if item is None and sit is None:
            conflicts.append({"type": "unknown_object", "target": target, "op": op})
            continue

        if op == "remove":
            if item is not None:
                m["objects"].remove(item)
            if sit is not None:
                s["semantic_instances"].remove(sit)
            applied.append(dict(edit))
            continue

        if op == "move":
            if item is None:
                conflicts.append({"type": "unmeasured_object", "target": target, "op": "move"})
                continue
            dx, dy = float(edit.get("dx") or 0.0), float(edit.get("dy") or 0.0)
            moved = {**item, "center": [item["center"][0] + dx, item["center"][1] + dy, item["center"][2]]}
            corners = _rect_of_measurement(moved)
            if not _inside_room(corners, bounds):
                conflicts.append({"type": "out_of_room", "target": target, "op": "move"})
                continue
            hit = None
            for other in m.get("objects", []):
                if object_id(other) == target:
                    continue
                depth = overlap_depth(corners, _rect_of_measurement(other))
                if depth > _EDGE_TOL:
                    hit = {"id": object_id(other), "depth": depth}
                    break
            if hit:
                conflicts.append({"type": "overlap", "target": target, "other": hit["id"],
                                  "overlap_m": round(hit["depth"], 3), "op": "move"})
                continue
            item["center"] = moved["center"]
            if sit is not None:
                sit["center"] = [moved["center"][0], moved["center"][1], sit["center"][2]]
            applied.append(dict(edit))
            continue

        if op == "rotate":
            turns = int(edit.get("quarter_turns") or 1) % 4
            if turns == 0:
                continue
            if item is None:
                conflicts.append({"type": "unmeasured_object", "target": target, "op": "rotate"})
                continue
            rotated = {
                **item,
                "length_m": item["width_m"], "width_m": item["length_m"],
                "rotation_z_deg": ((float(item.get("rotation_z_deg") or 0.0) + 90.0 * turns) % 360.0),
            }
            corners = _rect_of_measurement(rotated)
            if not _inside_room(corners, bounds):
                conflicts.append({"type": "out_of_room", "target": target, "op": "rotate"})
                continue
            hit = next((other for other in m.get("objects", [])
                        if object_id(other) != target
                        and overlap_depth(corners, _rect_of_measurement(other)) > _EDGE_TOL), None)
            if hit is not None:
                conflicts.append({"type": "overlap", "target": target, "other": object_id(hit),
                                  "op": "rotate"})
                continue
            item["rotation_z_deg"] = rotated["rotation_z_deg"]
            item["length_m"], item["width_m"] = item["width_m"], item["length_m"]
            if sit is not None and sit.get("size"):
                sit["size"] = [sit["size"][1], sit["size"][0], sit["size"][2]]
                sit["rotation_z_deg"] = rotated["rotation_z_deg"]
            applied.append(dict(edit))
            continue

        conflicts.append({"type": "unsupported_op", "target": target, "op": op})

    return {"measurements": m, "structure": s, "conflicts": conflicts,
            "applied_edits": applied, "assumed_ids": assumed_ids}
```

- [ ] **Step 4: 让 space_foundation 接受 assumed 状态**

修改 `backend/pipeline/space_foundation.py` 的 `_prepare_inputs`（约第 275 行）：把

```python
    objects = [
        item for item in measurements.get("objects", [])
        if item.get("measurement_status") == "verified"
        and item.get("center")
        and item.get("length_m") is not None
        and item.get("width_m") is not None
    ]
```

改为

```python
    accepted_status = {"verified", "assumed"}  # assumed 只可能来自内存沙盒编辑
    objects = [
        item for item in measurements.get("objects", [])
        if item.get("measurement_status") in accepted_status
        and item.get("center")
        and item.get("length_m") is not None
        and item.get("width_m") is not None
    ]
```

并在同文件的 `build_spatial_foundation` 家具循环里，为每个对象补一个字段：

```python
            "assumed": item.get("measurement_status") == "assumed",
```

- [ ] **Step 5: 实现 scheme_layout.py**

创建 `backend/pipeline/scheme_layout.py`：

```python
"""派生状态 → layout：2D 叠影图与 3D 查看器共用的一份渲染数据。"""
from __future__ import annotations


def _box(center, size, rotation_z_deg, **extra) -> dict:
    return {
        "center": [float(v) for v in (center or [0.0, 0.0, 0.0])],
        "size": [float(v) for v in (size or [0.0, 0.0, 0.0])],
        "rotation_z_deg": float(rotation_z_deg or 0.0),
        **extra,
    }


def build_layout_objects(structure: dict) -> list[dict]:
    """家具与障碍物：与 measurements 同口径（米、z-up）。"""
    items = []
    for item in structure.get("semantic_instances") or structure.get("objects") or []:
        items.append(_box(
            item.get("center"), item.get("size"), item.get("rotation_z_deg"),
            id=str(item.get("instance_id") or item.get("id") or ""),
            label=item.get("label") or item.get("category") or "object",
            kind="object", assumed=bool(item.get("assumed")),
        ))
    for index, item in enumerate(structure.get("geometric_obstacles") or []):
        items.append(_box(
            item.get("center"), item.get("size"), item.get("rotation_z_deg"),
            id=str(item.get("instance_id") or f"obstacle_{index}"),
            label="箱子", kind="obstacle", assumed=bool(item.get("assumed")),
        ))
    return items


def build_layout(measurements: dict, structure: dict) -> dict:
    """与 preview 的 layout.json 同形状，供 2D 叠影图与 3D 查看器直接消费。"""
    room = structure.get("room") or {}
    doors = [_box(d.get("center"), d.get("size"), d.get("rotation_z_deg"), kind="door",
                  id=str(d.get("instance_id") or d.get("id") or "door"))
             for d in structure.get("doors", [])]
    windows = [_box(w.get("center"), w.get("size"), w.get("rotation_z_deg"), kind="window",
                    id=str(w.get("instance_id") or w.get("id") or "window"))
               for w in structure.get("windows", [])]
    walls = [_box(w.get("center"), w.get("size"), w.get("rotation_z_deg"), id=str(w.get("id", i)))
             for i, w in enumerate(structure.get("walls", []))]
    objects = build_layout_objects(structure)
    return {
        "coordinate_unit": "meters",
        "z_up": True,
        "room": {"bounds_xy": room.get("bounds_xy"), "height_m": room.get("height_m"),
                 "floor_polygon": room.get("floor_polygon")},
        "walls": walls,
        "doors": doors,
        "windows": windows,
        "objects": objects,
        "counts": {"walls": len(walls), "doors": len(doors),
                   "windows": len(windows), "objects": len(objects)},
    }
```
- [ ] **Step 6: 实现 scheme_assessment.py**

创建 `backend/pipeline/scheme_assessment.py`：

```python
"""官方评估链路的唯一内存入口：改造模拟与正式报告共用同一套算法。"""
from __future__ import annotations

from pipeline.risk_assessment import build_risk_assessment
from pipeline.scheme_state import apply_edits
from pipeline.space_foundation import analyze_structure_passages, build_spatial_foundation
from pipeline.spatial_assessment_inputs import build_spatial_assessment_inputs

LEVEL_ORDER = {None: -1, "low": 0, "medium": 1, "high": 2}


def assess_scheme(measurements: dict, structure: dict) -> dict:
    """只读内存输入，跑正式链路，返回指标/风险/分数。"""
    passage = analyze_structure_passages(measurements, structure)
    foundation = build_spatial_foundation(measurements, structure, passage)
    payload = build_spatial_assessment_inputs(measurements, passage, foundation)
    assessment = build_risk_assessment(payload)
    return {
        "metrics": payload.get("metrics", []),
        "paths": payload.get("paths", []),
        "risks": assessment.get("risks", []),
        "overall": assessment.get("overall", {}),
        "category_scores": assessment.get("category_scores", {}),
        "top_risks": assessment.get("top_risks", []),
    }


def build_scheme(measurements: dict, structure: dict, edits: list[dict]) -> dict:
    from pipeline.scheme_layout import build_layout, build_layout_objects

    state = apply_edits(measurements, structure, edits or [])
    result = assess_scheme(state["measurements"], state["structure"])
    return {
        **state,
        **result,
        "layout_objects": build_layout_objects(state["structure"]),
        "layout_before": build_layout(measurements, structure),
        "layout_after": build_layout(state["measurements"], state["structure"]),
        "scope": {
            "simulation": True,
            "raw_media_accessed": False,
            "structured_inputs_only": True,
            "contains_assumed_objects": bool(state["assumed_ids"]),
        },
    }


def _metric_map(assessment: dict) -> dict:
    return {item["metric_code"]: item for item in assessment.get("metrics", [])}


def diff_assessments(before: dict, after: dict) -> dict:
    """前后对比：分数、指标变化、风险等级变化。"""
    before_score = (before.get("overall") or {}).get("score")
    after_score = (after.get("overall") or {}).get("score")
    delta = None if before_score is None or after_score is None else round(after_score - before_score, 1)
    before_metrics, after_metrics = _metric_map(before), _metric_map(after)
    metric_changes = []
    for code, item in after_metrics.items():
        old = before_metrics.get(code)
        if old is None or old.get("status") == "not_evaluable":
            continue
        if old.get("value") != item.get("value"):
            metric_changes.append({"metric_code": code, "name": item.get("name"), "unit": item.get("unit"),
                                   "before": old.get("value"), "after": item.get("value")})
    before_levels = {r["metric_code"]: r.get("risk_level") for r in before.get("risks", [])}
    risk_changes = []
    for risk in after.get("risks", []):
        old = before_levels.get(risk["metric_code"])
        if old is not None and old != risk.get("risk_level"):
            risk_changes.append({"metric_code": risk["metric_code"], "name": risk.get("risk_name"),
                                 "before": old, "after": risk.get("risk_level")})
    return {
        "comparison": {"before_score": before_score, "after_score": after_score, "score_delta": delta},
        "metric_changes": metric_changes,
        "risk_changes": risk_changes,
    }
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_scheme_state.py -v`
Expected: PASS（scan 46 数据存在时含 golden 用例）

- [ ] **Step 8: 跑既有回归**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_space_foundation.py tests/test_renovation_assistant.py tests/test_spatial_metrics.py -q`
Expected: 与基线一致（不新增失败）

- [ ] **Step 9: 提交**

```bash
git add backend/pipeline/scheme_state.py backend/pipeline/scheme_assessment.py backend/pipeline/space_foundation.py backend/tests/test_scheme_state.py
git commit -m "feat(assistant): add in-memory scheme state and official assessment entry"
```

---
### Task 2: `/simulate` 同源化与自然语言入口

**Files:**
- Create: `backend/assistant/intent_edits.py`
- Modify: `backend/app/routers/renovation.py`（替换 `RenovationRequest` 与 `simulate_renovation`）
- Modify: `backend/tests/conftest.py`（新增 `scheme_inputs` fixture）
- Test: `backend/tests/test_renovation_simulate_api.py`

**Interfaces:**
- Consumes: `pipeline.scheme_assessment.build_scheme(measurements, structure, edits)`、`diff_assessments(before, after)`
- Produces: `POST /api/assistant/scans/{scan_id}/simulate` 新响应体；`assistant.intent_edits.resolve_target(measurements, structure, name) -> str | None`、`intent_to_edits(intent, measurements, structure) -> list[dict]`

- [ ] **Step 1: conftest 增加共享 fixture**

在 `backend/tests/conftest.py` 顶部（`import pytest` 之后）追加：

```python
def _scheme_measurements():
    return {
        "room": {"length_m": 4.0, "width_m": 4.0, "height_m": 2.7,
                 "measurement_status": "verified", "confidence": "medium"},
        "openings": [
            {"id": "door_01", "type": "door", "center": [0.6, 0.0, 1.05], "width_m": 0.85,
             "height_m": 2.1, "rotation_z_deg": 0.0, "measurement_status": "verified"},
        ],
        "objects": [
            {"id": "bed_001", "instance_id": "bed_001", "type": "bed", "center": [1.0, 3.0, 0.3],
             "length_m": 2.0, "width_m": 1.2, "height_m": 0.6, "rotation_z_deg": 0.0,
             "measurement_status": "verified", "confidence": "medium"},
            {"id": "desk_001", "instance_id": "desk_001", "type": "desk", "center": [1.2, 2.0, 0.4],
             "length_m": 1.0, "width_m": 0.5, "height_m": 0.8, "rotation_z_deg": 0.0,
             "measurement_status": "verified", "confidence": "medium"},
        ],
    }


def _scheme_structure():
    return {
        "room": {"bounds_xy": {"min": [0.0, 0.0], "max": [4.0, 4.0]}, "height_m": 2.7},
        "walls": [], "geometric_obstacles": [],
        "doors": [{"instance_id": "door_01", "center": [0.6, 0.0, 1.05],
                   "size": [0.85, 0.12, 2.1], "rotation_z_deg": 0.0}],
        "semantic_instances": [
            {"instance_id": "bed_001", "label": "bed", "center": [1.0, 3.0, 0.3], "size": [2.0, 1.2, 0.6]},
            {"instance_id": "desk_001", "label": "desk", "center": [1.2, 2.0, 0.4], "size": [1.0, 0.5, 0.8]},
        ],
    }


@pytest.fixture
def scheme_inputs():
    """一套可评分的合成房间：4×4m、一床一桌一门。"""
    return _scheme_measurements(), _scheme_structure()
```

- [ ] **Step 2: 写失败测试**

创建 `backend/tests/test_renovation_simulate_api.py`：

```python
"""改造模拟 API：同源重算、布局返回与约束冲突。"""
import json

from app.config import get_settings
from pipeline.scheme_assessment import assess_scheme
from assistant.intent_edits import intent_to_edits, resolve_target
from pathlib import Path


def _auth(client, org="养老院A", email="sandbox@x.com"):
    client.post("/api/auth/register", json={
        "org_name": org, "name": "甲", "email": email, "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _scan_with_artifacts(client, headers, measurements, structure):
    pid = client.post("/api/projects", json={"name": "沙盒房间"}, headers=headers).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"},
                      headers=headers).json()["id"]
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    (post / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False), encoding="utf-8")
    (post / "structure_calibrated.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    return sid


def test_simulate_with_empty_edits_matches_official_assessment(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, m, s)
    expected = assess_scheme(m, s)["overall"]["score"]
    r = client.post(f"/api/assistant/scans/{sid}/simulate", json={"edits": []}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["overall"]["score"] == expected
    assert body["scope"] == {"simulation": True, "raw_media_accessed": False,
                             "structured_inputs_only": True, "contains_assumed_objects": False}


def test_simulate_returns_layouts_and_changes(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="layout@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    r = client.post(f"/api/assistant/scans/{sid}/simulate",
                    json={"edits": [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -1.0}]},
                    headers=h)
    body = r.json()
    assert r.status_code == 200
    assert body["layout_before"]["counts"]["objects"] == 2
    assert body["layout_after"]["counts"]["objects"] == 2
    desk_after = next(o for o in body["layout_after"]["objects"] if o["id"] == "desk_001")
    desk_before = next(o for o in body["layout_before"]["objects"] if o["id"] == "desk_001")
    assert desk_after["center"][1] == desk_before["center"][1] - 1.0
    assert body["changed_ids"] == ["desk_001"]
    assert body["metric_changes"], "位移必须引起指标变化"
    assert body["comparison"]["after_score"] is not None


def test_simulate_reports_conflicts_without_changing_score(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="conflict@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    baseline = client.post(f"/api/assistant/scans/{sid}/simulate", json={"edits": []}, headers=h).json()
    r = client.post(f"/api/assistant/scans/{sid}/simulate",
                    json={"edits": [{"op": "move", "target": "desk_001", "dx": 0.2, "dy": 0.9}]}, headers=h)
    body = r.json()
    assert body["conflicts"] and body["conflicts"][0]["type"] == "overlap"
    assert body["overall"]["score"] == baseline["overall"]["score"]
    assert body["applied_edits"] == []


def test_simulate_rejects_missing_artifacts(client):
    h = _auth(client, email="missing@x.com")
    pid = client.post("/api/projects", json={"name": "空房间"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    r = client.post(f"/api/assistant/scans/{sid}/simulate", json={"edits": []}, headers=h)
    assert r.status_code == 409


def test_intent_to_edits_resolves_chinese_alias(scheme_inputs):
    m, s = scheme_inputs
    assert resolve_target(m, s, "书桌") == "desk_001"
    edits = intent_to_edits({"action": "MOVE", "target": "书桌", "dx": -1.0, "dy": 0.0, "distance_m": 0.3}, m, s)
    assert edits == [{"op": "move", "target": "desk_001", "dx": -0.3, "dy": 0.0}]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_renovation_simulate_api.py -v`
Expected: FAIL —— `/simulate` 返回 422 或缺少 `layout_before` 字段

- [ ] **Step 4: 实现 intent_edits.py**

创建 `backend/assistant/intent_edits.py`：

```python
"""自然语言改造指令 → 沙盒 edits（只生成编辑，不出分数）。"""
from __future__ import annotations

from pipeline.scheme_state import object_id

_CN_ALIAS = {
    "床": "bed", "书桌": "desk", "桌子": "desk", "桌": "desk", "书架": "bookshelf",
    "柜子": "cabinet", "衣柜": "wardrobe", "沙发": "sofa", "椅子": "chair",
    "凳子": "stool", "箱子": "box", "置物架": "storage_rack", "电视柜": "tv_stand",
}


def resolve_target(measurements: dict, structure: dict, name: str) -> str | None:
    """按 instance_id / 英文类型 / 中文别名匹配家具或开口。"""
    if not name:
        return None
    wanted = _CN_ALIAS.get(name, name)
    objects = list(measurements.get("objects", []))
    for item in objects:
        if object_id(item) == name or str(item.get("type") or "") == wanted:
            return object_id(item)
    for item in objects:
        if wanted in str(item.get("type") or ""):
            return object_id(item)
    for item in structure.get("semantic_instances", []):
        if object_id(item) == name or wanted in str(item.get("label") or ""):
            return object_id(item)
    for opening in measurements.get("openings", []):
        if object_id(opening) == name or wanted in str(opening.get("type") or ""):
            return object_id(opening)
    return None


def _anchor_center(intent: dict, measurements: dict, structure: dict, size: list[float]) -> list[float]:
    bounds = (structure.get("room") or {}).get("bounds_xy") or {}
    lo = bounds.get("min") or [0.0, 0.0]
    hi = bounds.get("max") or [4.0, 4.0]
    anchor = None
    target = resolve_target(measurements, structure, str(intent.get("target") or ""))
    if target:
        anchor = next((o for o in measurements.get("objects", []) if object_id(o) == target), None)
    if anchor is None and "门" in str(intent.get("position") or intent.get("raw") or ""):
        anchor = next((o for o in measurements.get("openings", []) if o.get("type") == "door"), None)
    if anchor is None:
        return [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, size[2] / 2]
    ax, ay = float(anchor["center"][0]), float(anchor["center"][1])
    half = float(anchor.get("length_m") or anchor.get("width_m") or 0.5) / 2
    x = ax + half + size[0] / 2 + 0.05
    x = min(max(x, lo[0] + size[0] / 2), hi[0] - size[0] / 2)
    y = min(max(ay, lo[1] + size[1] / 2), hi[1] - size[1] / 2)
    return [x, y, size[2] / 2]


def intent_to_edits(intent: dict, measurements: dict, structure: dict) -> list[dict]:
    """把 parse_intent 的结果转成 edits；找不到目标时抛 ValueError（由路由转 422）。"""
    action = intent.get("action")
    if action == "MOVE":
        target = resolve_target(measurements, structure, str(intent.get("target") or ""))
        if target is None:
            raise ValueError(f"没有找到要移动的家具：{intent.get('target')}")
        distance = float(intent.get("distance_m") or 0.3)
        return [{"op": "move", "target": target,
                 "dx": float(intent.get("dx") or 0.0) * distance,
                 "dy": float(intent.get("dy") or 0.0) * distance}]
    if action == "REMOVE":
        target = resolve_target(measurements, structure, str(intent.get("target") or ""))
        if target is None:
            raise ValueError(f"没有找到要移除的物体：{intent.get('target')}")
        return [{"op": "remove", "target": target}]
    if action == "ADD":
        size = [float(v) for v in intent.get("size_m") or [0.4, 0.4, 0.4]]
        center = _anchor_center(intent, measurements, structure, size)
        return [{"op": "add", "label": intent.get("label") or "箱子", "size": size, "center": center}]
    raise ValueError(f"暂不支持的改造指令：{action}")
```

- [ ] **Step 5: 重写 `/simulate` 路由**

修改 `backend/app/routers/renovation.py`：删除 `simulate` 导入与 `_build_before`，改为：

```python
from assistant.intent_edits import intent_to_edits
from assistant.intent_parser import parse_intent
from pipeline.scheme_assessment import build_scheme, diff_assessments


class RenovationRequest(BaseModel):
    edits: list[dict] | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=300)


def _require_measurements(work: Path) -> dict:
    path = work / "postprocess" / "measurements.json"
    if not path.is_file():
        raise HTTPException(409, "当前扫描缺少结构化测量数据，无法进行改造模拟")
    return _read_json(path)


@router.post("/scans/{scan_id}/simulate")
def simulate_renovation(
    scan_id: int,
    payload: RenovationRequest,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements = _require_measurements(work)
    structure = _load_structure(work)

    if payload.edits is not None:
        edits = payload.edits
    elif payload.prompt:
        try:
            edits = intent_to_edits(parse_intent(payload.prompt), measurements, structure)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    else:
        raise HTTPException(422, "请提供改造指令或编辑列表")

    baseline = build_scheme(measurements, structure, [])
    result = build_scheme(measurements, structure, edits)
    diff = diff_assessments(baseline, result)
    applied = result["applied_edits"]
    return {
        "scan_id": scan_id,
        "edits": edits,
        "applied_edits": applied,
        "conflicts": result["conflicts"],
        "assumed_ids": result["assumed_ids"],
        "layout_before": result["layout_before"],
        "layout_after": result["layout_after"],
        "changed_ids": sorted({e["target"] for e in applied if e.get("target")}),
        "removed_ids": sorted({e["target"] for e in applied if e.get("op") == "remove"}),
        "metrics": result["metrics"],
        "risks": result["risks"],
        "overall": result["overall"],
        "category_scores": result["category_scores"],
        **diff,
        "scope": result["scope"],
        "disclaimer": "仅为内存沙盒模拟：未修改真实房间、点云、结构数据或正式报告；"
                      "分数由正式评估链路在改后布局上重算。",
    }
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_renovation_simulate_api.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add backend/assistant/intent_edits.py backend/app/routers/renovation.py backend/tests/conftest.py backend/tests/test_renovation_simulate_api.py
git commit -m "feat(assistant): score simulations through the official pipeline"
```

---

### Task 3: 求解器与 `/solve`

**Files:**
- Create: `backend/pipeline/scheme_solver.py`
- Modify: `backend/app/routers/renovation.py`（新增 `/solve` 与新签名 `/suggestions`）
- Test: `backend/tests/test_scheme_solver.py`、`backend/tests/test_renovation_suggestions_api.py`

**Interfaces:**
- Consumes: `pipeline.scheme_assessment.build_scheme`
- Produces: `pipeline.scheme_solver.solve_metric(measurements, structure, metric_code, *, budget=300) -> dict`；`POST /api/assistant/scans/{scan_id}/solve` 返回 `{"scan_id": int, "results": list[dict]}`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_scheme_solver.py`：

```python
import pytest

from pipeline.scheme_assessment import build_scheme
from pipeline.scheme_solver import candidate_edits, solve_metric


def test_candidate_edits_never_touch_bed_without_need(scheme_inputs):
    m, s = scheme_inputs
    candidates = candidate_edits(m, s, "furniture_spacing", max_steps=2)
    assert candidates and all(isinstance(c, list) and c for c in candidates)
    assert all(c[0]["target"] in {"desk_001", "bed_001"} for c in candidates)


def test_solver_never_claims_unverified_improvement(scheme_inputs):
    m, s = scheme_inputs
    for code in ("minimum_passage_width", "crowding", "door_width"):
        result = solve_metric(m, s, code)
        assert result["metric_code"] == code
        if result["solved"]:
            replay = build_scheme(m, s, result["edits"])
            assert replay["overall"]["score"] == result["score"]["after"]
        else:
            assert result["shortfall"]


def test_solver_marks_door_widening_as_construction(scheme_inputs):
    m, s = scheme_inputs
    result = solve_metric(m, s, "door_width")
    assert result["requires_construction"] is True
    assert any(e["op"] == "resize_door" for e in result["edits"])


def test_solver_respects_budget(scheme_inputs):
    m, s = scheme_inputs
    result = solve_metric(m, s, "minimum_passage_width", budget=1)
    assert result["evaluations"] <= 1
    assert result["budget_exhausted"] is True


def test_solver_result_shape(scheme_inputs):
    m, s = scheme_inputs
    result = solve_metric(m, s, "crowding", budget=5)
    for key in ("metric_code", "risk_name", "solved", "edits", "before", "after",
                "score", "shortfall", "requires_construction", "evaluations",
                "elapsed_ms", "budget_exhausted"):
        assert key in result
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_scheme_solver.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'pipeline.scheme_solver'`

- [ ] **Step 3: 实现求解器**

创建 `backend/pipeline/scheme_solver.py`：

```python
"""建议求解器：候选生成 → 官方链路验证 → 取达标且改动最小的解。

原则：任何展示出来的分数都必须来自 build_scheme（正式链路）的实算结果；
无解时返回 shortfall，不虚构提升。
"""
from __future__ import annotations

import time

from pipeline.scheme_assessment import build_scheme

STEP_M = 0.10
MAX_BUDGET = 300
AXES = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))
CONSTRUCTION_METRICS = {"door_width"}
LEVEL_RANK = {None: 0, "low": 0, "medium": 1, "high": 2}
MOVABLE_KINDS = {"furniture", "obstacle"}


def _movable_objects(measurements: dict) -> list[dict]:
    return [o for o in measurements.get("objects", []) if o.get("length_m") and o.get("width_m")]


def candidate_edits(measurements: dict, structure: dict, metric_code: str,
                    *, max_steps: int = 10) -> list[list[dict]]:
    """生成候选编辑：平移（4 轴 × 步长）→ 搬出房间 → 放宽门洞。"""
    candidates: list[list[dict]] = []
    if metric_code in CONSTRUCTION_METRICS:
        width = max(float(o.get("width_m") or 0.0) for o in measurements.get("openings", [])
                    if o.get("type") == "door") if any(
            o.get("type") == "door" for o in measurements.get("openings", [])) else 0.0
        if width and width < 0.90:
            candidates.append([{"op": "resize_door", "width_m": 0.90}])
    for obj in _movable_objects(measurements):
        oid = str(obj.get("instance_id") or obj.get("id"))
        for distance in (STEP_M * (i + 1) for i in range(max_steps)):
            for dx, dy in AXES:
                candidates.append([{"op": "move", "target": oid,
                                    "dx": round(dx * distance, 3), "dy": round(dy * distance, 3)}])
        candidates.append([{"op": "remove", "target": oid}])
    return candidates


def _metric_value(assessment: dict, metric_code: str) -> float | None:
    item = next((x for x in assessment.get("metrics", []) if x["metric_code"] == metric_code), None)
    return None if item is None else item.get("value")


def _risk_level(assessment: dict, metric_code: str) -> str | None:
    item = next((x for x in assessment.get("risks", []) if x["metric_code"] == metric_code), None)
    return None if item is None else item.get("risk_level")


def _risk_name(assessment: dict, metric_code: str) -> str:
    item = next((x for x in assessment.get("risks", []) if x["metric_code"] == metric_code), None)
    return (item or {}).get("risk_name") or metric_code


def _displacement(edits: list[dict]) -> float:
    return sum(abs(float(e.get("dx") or 0.0)) + abs(float(e.get("dy") or 0.0)) for e in edits) + len(edits) * STEP_M


def solve_metric(measurements: dict, structure: dict, metric_code: str,
                 *, budget: int = MAX_BUDGET) -> dict:
    """为单个风险求解；返回 4.3 约定的结果结构。"""
    started = time.perf_counter()
    baseline = build_scheme(measurements, structure, [])
    before_value, before_level = _metric_value(baseline, metric_code), _risk_level(baseline, metric_code)
    before_score = (baseline.get("overall") or {}).get("score")

    best_solved = None
    best_partial = None
    evaluations = 0
    budget_exhausted = False

    for edits in candidate_edits(measurements, structure, metric_code):
        if evaluations >= budget:
            budget_exhausted = True
            break
        result = build_scheme(measurements, structure, edits)
        if result["conflicts"]:
            continue
        evaluations += 1
        level = _risk_level(result, metric_code)
        score = (result.get("overall") or {}).get("score")
        candidate = {
            "edits": edits,
            "value": _metric_value(result, metric_code),
            "level": level,
            "score": score,
            "requires_construction": any(e.get("op") == "resize_door" for e in edits),
        }
        if level is None or LEVEL_RANK.get(level, 9) == 0:
            key = (candidate["requires_construction"], _displacement(edits))
            if best_solved is None or key < best_solved[0]:
                best_solved = (key, candidate)
        partial_key = (-(score or 0), LEVEL_RANK.get(level, 9))
        if best_partial is None or partial_key > best_partial[0]:
            best_partial = (partial_key, candidate)

    chosen = (best_solved or best_partial or (None, None))[1]
    solved = best_solved is not None
    if chosen is None:
        return {
            "metric_code": metric_code,
            "risk_name": _risk_name(baseline, metric_code),
            "solved": False,
            "edits": [],
            "requires_construction": False,
            "before": {"value": before_value, "level": before_level},
            "after": {"value": before_value, "level": before_level},
            "score": {"before": before_score, "after": before_score},
            "shortfall": "当前房间结构下没有可用的家具调整候选方案。",
            "evaluations": evaluations,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "budget_exhausted": budget_exhausted,
        }

    shortfall = None
    if not solved:
        unit = next((x.get("unit") for x in baseline.get("metrics", []) if x["metric_code"] == metric_code), "") or ""
        shortfall = (f"已尝试 {evaluations} 个候选方案仍未达标："
                     f"目标指标 {chosen['value']}{unit}（{chosen['level'] or 'low'}）。"
                     "当前房间条件下需要搬出家具或施工改造才能达成。")

    return {
        "metric_code": metric_code,
        "risk_name": _risk_name(baseline, metric_code),
        "solved": solved,
        "edits": chosen["edits"],
        "requires_construction": chosen["requires_construction"],
        "before": {"value": before_value, "level": before_level},
        "after": {"value": chosen["value"], "level": chosen["level"]},
        "score": {"before": before_score, "after": chosen["score"]},
        "shortfall": shortfall,
        "evaluations": evaluations,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "budget_exhausted": budget_exhausted,
    }
```

- [ ] **Step 4: 新增 `/solve` 路由**

在 `backend/app/routers/renovation.py` 追加：

```python
from pipeline.scheme_solver import solve_metric


class SolveRequest(BaseModel):
    metric_codes: list[str] = Field(min_length=1, max_length=5)


@router.post("/scans/{scan_id}/solve")
def solve_suggestions(
    scan_id: int,
    payload: SolveRequest,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements = _require_measurements(work)
    structure = _load_structure(work)
    baseline = build_scheme(measurements, structure, [])
    known = {r["metric_code"] for r in baseline.get("risks", [])}
    unknown = [code for code in payload.metric_codes if code not in known]
    if unknown:
        raise HTTPException(422, f"未知的风险指标：{unknown}")
    return {"scan_id": scan_id,
            "baseline_score": (baseline.get("overall") or {}).get("score"),
            "results": [solve_metric(measurements, structure, code) for code in payload.metric_codes]}
```

- [ ] **Step 5: 重写 `/suggestions` 返回可达成预判**

修改 `backend/app/routers/renovation.py` 的 `list_suggestions`：不再使用旧的 `_build_before`，改为官方链路 + 达成预判。

```python
from pipeline.scheme_solver import CONSTRUCTION_METRICS, _movable_objects

_REACHABILITY_LABEL = {
    "movable": "可达成（移动家具）",
    "construction": "需施工（改门洞/换门）",
    "unknown": "无法达成",
}


def _reachability(measurements: dict, metric_code: str) -> str:
    if metric_code in CONSTRUCTION_METRICS:
        return "construction"
    return "movable" if _movable_objects(measurements) else "unknown"


@router.get("/scans/{scan_id}/suggestions")
def list_suggestions(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements, structure = _require_measurements(work), _load_structure(work)
    assessment = build_scheme(measurements, structure, [])
    suggestions = []
    for index, risk in enumerate(assessment.get("top_risks", []), 1):
        code = risk.get("metric_code")
        tier = _reachability(measurements, code)
        suggestions.append({
            "id": index,
            "name": risk.get("risk_name"),
            "level": risk.get("risk_level"),
            "advice": risk.get("advice"),
            "metric_code": code,
            "reachability": tier,
            "reachability_label": _REACHABILITY_LABEL[tier],
        })
    return {
        "scan_id": scan_id,
        "score": (assessment.get("overall") or {}).get("score"),
        "suggestions": suggestions,
    }
```

说明：预判只回答"有没有候选可试"，不承诺达标；达标与否由 `/solve` 的 `solved` 字段给出。

新增 `backend/tests/test_renovation_suggestions_api.py`：

```python
"""建议列表：达成预判标签。"""
import json
from pathlib import Path

from app.config import get_settings


def _auth(client, email="reach@x.com"):
    client.post("/api/auth/register", json={
        "org_name": "养老院A", "name": "甲", "email": email, "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _scan_with_artifacts(client, headers, measurements, structure):
    pid = client.post("/api/projects", json={"name": "沙盒房间"}, headers=headers).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"},
                      headers=headers).json()["id"]
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    (post / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False), encoding="utf-8")
    (post / "structure_calibrated.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    return sid


def test_suggestions_expose_reachability_labels(client, scheme_inputs):
    m, s = scheme_inputs
    headers = _auth(client)
    sid = _scan_with_artifacts(client, headers, m, s)
    body = client.get(f"/api/assistant/scans/{sid}/suggestions", headers=headers).json()
    assert body["suggestions"], "合成房间应至少给出一项风险建议"
    for item in body["suggestions"]:
        assert item["reachability"] in {"movable", "construction", "unknown"}
        assert item["reachability_label"]
        assert item["metric_code"]
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_scheme_solver.py tests/test_renovation_suggestions_api.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add backend/pipeline/scheme_solver.py backend/tests/test_renovation_suggestions_api.py backend/app/routers/renovation.py backend/tests/test_scheme_solver.py
git commit -m "feat(assistant): add search-based suggestion solver"
```

---
### Task 4: 方案持久化与 CRUD

**Files:**
- Modify: `backend/app/models.py`（新增 `RenovationScheme`，并在 `Scan` 增加 `schemes` 关系）
- Create: `backend/migrations/versions/20260926_0005_renovation_schemes.py`
- Modify: `backend/app/routers/renovation.py`（抽出 `_scheme_payload`，新增 schemes 端点）
- Test: `backend/tests/test_renovation_schemes_api.py`

**Interfaces:**
- Consumes: `build_scheme`、`diff_assessments`、`_require_measurements`、`_load_structure`
- Produces: `RenovationScheme` 模型；`_scheme_payload(scan_id, measurements, structure, edits) -> dict`；`GET/POST /api/assistant/scans/{scan_id}/schemes`、`GET/PATCH/DELETE /api/assistant/scans/{scan_id}/schemes/{scheme_id}`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_renovation_schemes_api.py`：

```python
"""方案 CRUD：机构隔离、校验与可复现性。"""
import json
from pathlib import Path

from app.config import get_settings


def _auth(client, org="养老院A", email="scheme@x.com"):
    client.post("/api/auth/register", json={
        "org_name": org, "name": "甲", "email": email, "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _scan_with_artifacts(client, headers, measurements, structure):
    pid = client.post("/api/projects", json={"name": "沙盒房间"}, headers=headers).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"},
                      headers=headers).json()["id"]
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    (post / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False), encoding="utf-8")
    (post / "structure_calibrated.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    return sid


def test_scheme_crud_and_reproducible_score(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, m, s)
    edits = [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -0.6}]
    created = client.post(f"/api/assistant/scans/{sid}/schemes",
                          json={"name": "方案A", "edits": edits}, headers=h)
    assert created.status_code == 200
    scheme_id = created.json()["id"]

    listed = client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()
    assert len(listed["schemes"]) == 1
    listed_score = listed["schemes"][0]["score"]

    detail = client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}", headers=h)
    assert detail.status_code == 200
    body = detail.json()
    assert body["score"] == listed_score
    assert body["layout_after"]["counts"]["objects"] == 2
    assert body["plan"]["edits"] == edits

    renamed = client.patch(f"/api/assistant/scans/{sid}/schemes/{scheme_id}",
                           json={"name": "方案A · 书桌下移"}, headers=h)
    assert renamed.json()["name"] == "方案A · 书桌下移"

    assert client.delete(f"/api/assistant/scans/{sid}/schemes/{scheme_id}", headers=h).status_code == 200
    assert client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()["schemes"] == []


def test_scheme_rejects_conflicting_edits(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="bad-edits@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    r = client.post(f"/api/assistant/scans/{sid}/schemes",
                    json={"name": "撞床方案", "edits": [{"op": "move", "target": "desk_001", "dx": 0.2, "dy": 0.9}]},
                    headers=h)
    assert r.status_code == 422


def test_scheme_is_isolated_by_org(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, m, s)
    scheme_id = client.post(f"/api/assistant/scans/{sid}/schemes",
                            json={"name": "方案A", "edits": []}, headers=h).json()["id"]
    h2 = _auth(client, org="养老院B", email="other@x.com")
    assert client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}", headers=h2).status_code == 404
    assert client.get(f"/api/assistant/scans/{sid}/schemes", headers=h2).status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_renovation_schemes_api.py -v`
Expected: FAIL —— 404 Not Found（端点不存在）

- [ ] **Step 3: 增加模型与迁移**

在 `backend/app/models.py` 末尾追加：

```python
class RenovationScheme(Base):
    __tablename__ = "renovation_schemes"
    id = Column(Integer, primary_key=True)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    edits = Column(JSON, default=list, nullable=False, server_default=text("'[]'"))
    based_on = Column(JSON, default=dict, nullable=False, server_default=text("'{}'"))
    source = Column(String(20), default="manual")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    scan = relationship("Scan", back_populates="schemes")
```

并在 `Scan` 类的 `report` 关系之后增加：

```python
    schemes = relationship("RenovationScheme", back_populates="scan", cascade="all, delete-orphan")
```

创建 `backend/migrations/versions/20260926_0005_renovation_schemes.py`：

```python
"""Add renovation schemes.

Revision ID: 20260926_0005
Revises: 20260809_0004
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260926_0005"
down_revision: str | None = "20260809_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "renovation_schemes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("edits", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("based_on", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_renovation_schemes_scan_id", "renovation_schemes", ["scan_id"])


def downgrade() -> None:
    op.drop_index("ix_renovation_schemes_scan_id", table_name="renovation_schemes")
    op.drop_table("renovation_schemes")
```
- [ ] **Step 4: 抽出 `_scheme_payload` 并新增 schemes 端点**

在 `backend/app/routers/renovation.py` 中，把 Task 2 里 `/simulate` 构造响应的部分抽成模块级函数：

```python
def _input_fingerprints(work: Path) -> dict:
    import hashlib
    out = {}
    for name in ("measurements.json", "structure_calibrated.json", "structure.json"):
        path = work / "postprocess" / name
        if path.is_file():
            out[name.replace(".json", "_sha256")] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _scheme_payload(scan_id: int, measurements: dict, structure: dict, edits: list[dict]) -> dict:
    baseline = build_scheme(measurements, structure, [])
    result = build_scheme(measurements, structure, edits)
    diff = diff_assessments(baseline, result)
    applied = result["applied_edits"]
    return {
        "scan_id": scan_id,
        "edits": edits,
        "applied_edits": applied,
        "conflicts": result["conflicts"],
        "assumed_ids": result["assumed_ids"],
        "layout_before": result["layout_before"],
        "layout_after": result["layout_after"],
        "changed_ids": sorted({e["target"] for e in applied if e.get("target")}),
        "removed_ids": sorted({e["target"] for e in applied if e.get("op") == "remove"}),
        "metrics": result["metrics"],
        "risks": result["risks"],
        "overall": result["overall"],
        "category_scores": result["category_scores"],
        **diff,
        "scope": result["scope"],
        "disclaimer": "仅为内存沙盒模拟：未修改真实房间、点云、结构数据或正式报告；"
                      "分数由正式评估链路在改后布局上重算。",
    }
```

`/simulate` 改为 `return _scheme_payload(scan_id, measurements, structure, edits)`。新增端点：

```python
from ..models import RenovationScheme


class SchemeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    edits: list[dict] = Field(default_factory=list)
    source: str = Field(default="manual", max_length=20)


class SchemePatchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _get_scheme(db: Session, scan_id: int, scheme_id: int) -> RenovationScheme:
    scheme = db.get(RenovationScheme, scheme_id)
    if scheme is None or scheme.scan_id != scan_id:
        raise HTTPException(404, "方案不存在")
    return scheme


@router.get("/scans/{scan_id}/schemes")
def list_schemes(scan_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope),
                 settings: Settings = Depends(get_settings)):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements, structure = _require_measurements(work), _load_structure(work)
    baseline_score = (build_scheme(measurements, structure, []).get("overall") or {}).get("score")
    schemes = (db.query(RenovationScheme)
               .filter(RenovationScheme.scan_id == scan_id)
               .order_by(RenovationScheme.id).all())
    items = []
    for scheme in schemes:
        result = build_scheme(measurements, structure, scheme.edits or [])
        items.append({
            "id": scheme.id, "name": scheme.name, "edits": scheme.edits, "source": scheme.source,
            "created_at": scheme.created_at.isoformat() if scheme.created_at else None,
            "score": (result.get("overall") or {}).get("score"),
            "baseline_score": baseline_score,
            "conflicts": result["conflicts"],
        })
    return {"scan_id": scan_id, "baseline_score": baseline_score, "schemes": items}


@router.post("/scans/{scan_id}/schemes")
def create_scheme(scan_id: int, payload: SchemeCreateRequest, db: Session = Depends(get_db),
                  org_id: int = Depends(get_org_scope), settings: Settings = Depends(get_settings)):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements, structure = _require_measurements(work), _load_structure(work)
    validation = build_scheme(measurements, structure, payload.edits)
    if validation["conflicts"]:
        raise HTTPException(422, f"方案包含无效编辑：{validation['conflicts'][0]}")
    scheme = RenovationScheme(scan_id=scan_id, name=payload.name, edits=payload.edits,
                              source=payload.source, based_on=_input_fingerprints(work))
    db.add(scheme)
    db.commit()
    db.refresh(scheme)
    return {"id": scheme.id, "name": scheme.name, "edits": scheme.edits,
            "score": (validation.get("overall") or {}).get("score")}


@router.get("/scans/{scan_id}/schemes/{scheme_id}")
def get_scheme(scan_id: int, scheme_id: int, db: Session = Depends(get_db),
               org_id: int = Depends(get_org_scope), settings: Settings = Depends(get_settings)):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    measurements, structure = _require_measurements(work), _load_structure(work)
    payload = _scheme_payload(scan_id, measurements, structure, scheme.edits or [])
    return {**payload, "score": (payload["overall"] or {}).get("score"),
            "plan": {"id": scheme.id, "name": scheme.name, "edits": scheme.edits,
                     "source": scheme.source, "based_on": scheme.based_on}}


@router.patch("/scans/{scan_id}/schemes/{scheme_id}")
def rename_scheme(scan_id: int, scheme_id: int, payload: SchemePatchRequest, db: Session = Depends(get_db),
                  org_id: int = Depends(get_org_scope), settings: Settings = Depends(get_settings)):
    _scan_and_work(scan_id, db, org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    scheme.name = payload.name
    db.commit()
    db.refresh(scheme)
    return {"id": scheme.id, "name": scheme.name}


@router.delete("/scans/{scan_id}/schemes/{scheme_id}")
def delete_scheme(scan_id: int, scheme_id: int, db: Session = Depends(get_db),
                  org_id: int = Depends(get_org_scope), settings: Settings = Depends(get_settings)):
    _scan_and_work(scan_id, db, org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    db.delete(scheme)
    db.commit()
    return {"deleted": scheme_id}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_renovation_schemes_api.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/app/models.py backend/migrations/versions/20260926_0005_renovation_schemes.py backend/app/routers/renovation.py backend/tests/test_renovation_schemes_api.py
git commit -m "feat(assistant): persist renovation schemes with organization isolation"
```

---
### Task 5: Flutter 叠影图与方案结果卡

**Files:**
- Create: `app/lib/models/scheme_layout.dart`
- Create: `app/lib/widgets/scheme_plan_painter.dart`
- Create: `app/lib/widgets/scheme_result_card.dart`
- Modify: `app/lib/api/client.dart`（新增 `simulateScheme` / `solveSuggestions`）
- Modify: `app/lib/pages/report_page.dart`（助手卡片结果区改为方案结果卡）
- Test: `app/test/scheme_plan_geometry_test.dart`、`app/test/scheme_result_card_test.dart`

**Interfaces:**
- Produces:
  - `SchemeLayout.fromJson(Map<String, dynamic>) -> SchemeLayout`
  - `buildSchemeScene({required SchemeLayout before, required SchemeLayout after, required Size size, required double padding, required Set<String> changedIds, required Set<String> removedIds}) -> SchemeScene`
  - `SchemeBoxScene`（`id`、`label`、`assumed`、`changed`、`removed`、`beforeCenter`、`afterCenter`、`size`、`hidden`）
  - `paintSchemePlan(Canvas canvas, SchemeScene scene, {Color accent})`（屏幕与导出共用）
  - `SchemePlanPainter`、`SchemeResultCard`
  - `ApiClient.simulateScheme(int scanId, List<Map<String, dynamic>> edits)`、`ApiClient.solveSuggestions(int scanId, List<String> metricCodes)`

- [ ] **Step 1: 写失败测试**

创建 `app/test/scheme_plan_geometry_test.dart`：

```dart
import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/models/scheme_layout.dart';

Map<String, dynamic> _layout() => {
      'coordinate_unit': 'meters',
      'z_up': true,
      'room': {
        'bounds_xy': {
          'min': [0.0, 0.0],
          'max': [4.0, 4.0],
        },
        'height_m': 2.7,
      },
      'walls': [],
      'doors': [
        {'id': 'door_01', 'center': [0.6, 0.0, 1.05], 'size': [0.85, 0.12, 2.1], 'rotation_z_deg': 0.0}
      ],
      'windows': [],
      'objects': [
        {'id': 'bed_001', 'label': 'bed', 'center': [1.0, 1.0, 0.3], 'size': [2.0, 1.2, 0.6], 'rotation_z_deg': 0.0},
      ],
    };

void main() {
  test('房间坐标映射到画布时保持比例并且 y 轴向上', () {
    final layout = SchemeLayout.fromJson(_layout());
    final scene = buildSchemeScene(
      before: layout, after: layout, size: const Size(400, 400),
      padding: 20, changedIds: const {}, removedIds: const {},
    );
    expect(scene.roomRect.width, closeTo(360, 0.01));
    expect(scene.roomRect.height, closeTo(360, 0.01));
    final box = scene.boxes.single;
    expect(box.afterCenter.dx, closeTo(200, 0.5));
    expect(box.afterCenter.dy, greaterThan(200)); // 世界 y=1 在房间下部
  });

  test('位移后的盒子同时给出原位与现位矩形', () {
    final before = SchemeLayout.fromJson(_layout());
    final afterJson = _layout();
    ((afterJson['objects'] as List).first as Map)['center'] = [1.0, 2.0, 0.3];
    final after = SchemeLayout.fromJson(afterJson);
    final scene = buildSchemeScene(
      before: before, after: after, size: const Size(400, 400),
      padding: 20, changedIds: const {'bed_001'}, removedIds: const {},
    );
    final box = scene.boxes.single;
    expect(box.changed, isTrue);
    expect(box.beforeCenter.dy - box.afterCenter.dy, closeTo(90, 1.0)); // 1m = 90px
  });

  test('假放置物体被标记为 assumed', () {
    final json = _layout();
    ((json['objects'] as List).first as Map)['assumed'] = true;
    expect(SchemeLayout.fromJson(json).objects.single.assumed, isTrue);
  });
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd app; flutter test test/scheme_plan_geometry_test.dart`
Expected: FAIL —— `Target of URI doesn't exist: 'package:anjing_app/models/scheme_layout.dart'`

- [ ] **Step 3: 实现数据模型与场景计算**

创建 `app/lib/models/scheme_layout.dart`：

```dart
import 'dart:math' as math;
import 'dart:ui';

class SchemeBox {
  const SchemeBox({
    required this.id, required this.label, required this.kind,
    required this.cx, required this.cy, required this.sx, required this.sy,
    required this.rotationDeg, required this.assumed,
  });

  final String id;
  final String label;
  final String kind;
  final double cx;
  final double cy;
  final double sx;
  final double sy;
  final double rotationDeg;
  final bool assumed;

  factory SchemeBox.fromJson(Map<String, dynamic> json) {
    final center = (json['center'] as List? ?? const [0, 0, 0])
        .map((v) => (v as num).toDouble()).toList();
    final size = (json['size'] as List? ?? const [0, 0, 0])
        .map((v) => (v as num).toDouble()).toList();
    return SchemeBox(
      id: json['id']?.toString() ?? '',
      label: (json['label'] ?? json['category'] ?? 'object').toString(),
      kind: (json['kind'] ?? 'object').toString(),
      cx: center.isNotEmpty ? center[0] : 0,
      cy: center.length > 1 ? center[1] : 0,
      sx: size.isNotEmpty ? size[0] : 0,
      sy: size.length > 1 ? size[1] : 0,
      rotationDeg: (json['rotation_z_deg'] as num?)?.toDouble() ?? 0,
      assumed: json['assumed'] == true,
    );
  }
}

class SchemeLayout {
  const SchemeLayout({
    required this.minX, required this.minY, required this.maxX, required this.maxY,
    required this.objects,
  });

  final double minX, minY, maxX, maxY;
  final List<SchemeBox> objects;

  factory SchemeLayout.fromJson(Map<String, dynamic> json) {
    final room = (json['room'] as Map?)?.cast<String, dynamic>() ?? const {};
    final bounds = (room['bounds_xy'] as Map?)?.cast<String, dynamic>() ?? const {};
    final min = (bounds['min'] as List? ?? const [0, 0]).map((v) => (v as num).toDouble()).toList();
    final max = (bounds['max'] as List? ?? const [4, 4]).map((v) => (v as num).toDouble()).toList();
    final objects = (json['objects'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => SchemeBox.fromJson(item.cast<String, dynamic>()))
        .toList();
    return SchemeLayout(
      minX: min.isNotEmpty ? min[0] : 0, minY: min.length > 1 ? min[1] : 0,
      maxX: max.isNotEmpty ? max[0] : 4, maxY: max.length > 1 ? max[1] : 4,
      objects: objects,
    );
  }
}

class SchemeBoxScene {
  const SchemeBoxScene({
    required this.id, required this.label, required this.assumed, required this.changed,
    required this.removed, required this.beforeCenter, required this.afterCenter,
    required this.size, required this.hidden,
  });

  final String id;
  final String label;
  final bool assumed;
  final bool changed;
  final bool removed;
  final Offset beforeCenter;
  final Offset afterCenter;
  final Size size;
  final bool hidden;
}

class SchemeScene {
  const SchemeScene({required this.roomRect, required this.boxes, required this.metersToPixels});

  final Rect roomRect;
  final List<SchemeBoxScene> boxes;
  final double metersToPixels;
}

/// 世界坐标 → 画布坐标。y 轴向上：世界 y 越大，画布越靠上。
SchemeScene buildSchemeScene({
  required SchemeLayout before,
  required SchemeLayout after,
  required Size size,
  required double padding,
  required Set<String> changedIds,
  required Set<String> removedIds,
}) {
  final roomWidth = math.max(after.maxX - after.minX, 0.001);
  final roomHeight = math.max(after.maxY - after.minY, 0.001);
  final usableWidth = math.max(size.width - padding * 2, 1.0);
  final usableHeight = math.max(size.height - padding * 2, 1.0);
  final scale = math.min(usableWidth / roomWidth, usableHeight / roomHeight);
  final width = roomWidth * scale;
  final height = roomHeight * scale;
  final left = (size.width - width) / 2;
  final top = (size.height - height) / 2;

  Offset project(double x, double y) =>
      Offset(left + (x - after.minX) * scale, top + (after.maxY - y) * scale);

  final beforeById = {for (final box in before.objects) box.id: box};
  final afterById = {for (final box in after.objects) box.id: box};
  final ids = <String>{...beforeById.keys, ...afterById.keys};
  final boxes = <SchemeBoxScene>[];
  for (final id in ids) {
    final b = beforeById[id];
    final a = afterById[id];
    final removed = removedIds.contains(id) || (b != null && a == null);
    final source = a ?? b!;
    final rotated = (source.rotationDeg % 180).abs() == 90;
    final sizeMeters = rotated ? Size(source.sy, source.sx) : Size(source.sx, source.sy);
    boxes.add(SchemeBoxScene(
      id: id,
      label: source.label,
      assumed: source.assumed,
      changed: changedIds.contains(id),
      removed: removed,
      beforeCenter: b == null ? project(source.cx, source.cy) : project(b.cx, b.cy),
      afterCenter: a == null ? project(source.cx, source.cy) : project(a.cx, a.cy),
      size: Size(sizeMeters.width * scale, sizeMeters.height * scale),
      hidden: removed,
    ));
  }
  return SchemeScene(roomRect: Rect.fromLTWH(left, top, width, height),
      boxes: boxes, metersToPixels: scale);
}
```
- [ ] **Step 4: 运行几何测试确认通过**

Run: `cd app; flutter test test/scheme_plan_geometry_test.dart`
Expected: PASS

- [ ] **Step 5: 实现绘制函数与画布**

创建 `app/lib/widgets/scheme_plan_painter.dart`（绘制逻辑抽成自由函数，导出时复用同一份代码）：

```dart
import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/scheme_layout.dart';

const kSchemeAccent = Color(0xFF0071E3);
const kSchemeGhost = Color(0xFFB6BCC6);
const kSchemeAssumed = Color(0xFFFF9F0A);
const kSchemeInk = Color(0xFF5B6470);

/// 屏幕与导出共用的绘制入口。
void paintSchemePlan(Canvas canvas, SchemeScene scene, {Color accent = kSchemeAccent}) {
  canvas.drawRect(scene.roomRect, Paint()..color = const Color(0xFFFBFBFD));
  canvas.drawRect(scene.roomRect,
      Paint()..style = PaintingStyle.stroke..strokeWidth = 2..color = kSchemeGhost);

  Rect rectOf(Offset center, Size size) =>
      Rect.fromCenter(center: center, width: size.width, height: size.height);

  for (final box in scene.boxes) {
    if (box.changed || box.removed) {
      final ghost = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..color = kSchemeGhost;
      canvas.drawPath(_dashed(Path()..addRect(rectOf(box.beforeCenter, box.size))), ghost);
    }
    if (box.hidden) continue;
    final rect = rectOf(box.afterCenter, box.size);
    canvas.drawRect(rect,
        Paint()..color = box.assumed ? kSchemeAssumed.withValues(alpha: 0.18) : Colors.white);
    canvas.drawRect(rect, Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = box.changed ? 2.5 : 1.5
      ..color = box.assumed ? kSchemeAssumed : (box.changed ? accent : kSchemeInk));
  }

  for (final box in scene.boxes.where((b) => b.changed && !b.hidden)) {
    final from = box.beforeCenter;
    final to = box.afterCenter;
    if ((from - to).distance < 1) continue;
    canvas.drawLine(from, to, Paint()..color = accent..strokeWidth = 2);
    _arrowHead(canvas, from, to, accent);
    final meters = (from.dy - to.dy) / scene.metersToPixels;
    _text(canvas, '↓${meters.abs().toStringAsFixed(2)}m',
        Offset(math.max(from.dx, to.dx) + 6, (from.dy + to.dy) / 2), accent);
  }
}

class SchemePlanPainter extends CustomPainter {
  SchemePlanPainter({
    required this.before, required this.after,
    required this.changedIds, required this.removedIds,
  });

  final SchemeLayout before;
  final SchemeLayout after;
  final Set<String> changedIds;
  final Set<String> removedIds;

  @override
  void paint(Canvas canvas, Size size) {
    final scene = buildSchemeScene(
      before: before, after: after, size: size, padding: 16,
      changedIds: changedIds, removedIds: removedIds,
    );
    paintSchemePlan(canvas, scene);
  }

  @override
  bool shouldRepaint(covariant SchemePlanPainter old) =>
      old.before != before || old.after != after ||
      old.changedIds != changedIds || old.removedIds != removedIds;
}

Path _dashed(Path source) {
  const dash = 6.0, gap = 4.0;
  final result = Path();
  for (final metric in source.computeMetrics()) {
    var distance = 0.0;
    while (distance < metric.length) {
      final next = math.min(distance + dash, metric.length);
      result.addPath(metric.extractPath(distance, next), Offset.zero);
      distance = next + gap;
    }
  }
  return result;
}

void _arrowHead(Canvas canvas, Offset from, Offset to, Color color) {
  final angle = math.atan2(to.dy - from.dy, to.dx - from.dx);
  const size = 7.0;
  final path = Path()
    ..moveTo(to.dx, to.dy)
    ..lineTo(to.dx - size * math.cos(angle - 0.4), to.dy - size * math.sin(angle - 0.4))
    ..lineTo(to.dx - size * math.cos(angle + 0.4), to.dy - size * math.sin(angle + 0.4))
    ..close();
  canvas.drawPath(path, Paint()..color = color);
}

void _text(Canvas canvas, String value, Offset at, Color color) {
  final painter = TextPainter(
    text: TextSpan(text: value, style: TextStyle(color: color, fontSize: 11)),
    textDirection: TextDirection.ltr,
  )..layout();
  painter.paint(canvas, at);
}
```

- [ ] **Step 6: 实现方案结果卡**

创建 `app/lib/widgets/scheme_result_card.dart`：

```dart
import 'package:flutter/material.dart';
import '../models/scheme_layout.dart';
import 'scheme_plan_painter.dart';

class SchemeResultCard extends StatelessWidget {
  const SchemeResultCard({
    super.key, required this.payload, this.onSave, this.onEdit, this.onExport,
  });

  final Map<String, dynamic> payload;
  final VoidCallback? onSave;
  final VoidCallback? onEdit;
  final VoidCallback? onExport;

  String _score(Object? value) => value is num ? value.toStringAsFixed(1) : '无法评分';

  @override
  Widget build(BuildContext context) {
    final before = SchemeLayout.fromJson((payload['layout_before'] as Map).cast<String, dynamic>());
    final after = SchemeLayout.fromJson((payload['layout_after'] as Map).cast<String, dynamic>());
    final changed = ((payload['changed_ids'] as List?) ?? const []).map((e) => e.toString()).toSet();
    final removed = ((payload['removed_ids'] as List?) ?? const []).map((e) => e.toString()).toSet();
    final comparison = (payload['comparison'] as Map?)?.cast<String, dynamic>() ?? const {};
    final metricChanges = (payload['metric_changes'] as List? ?? const []).whereType<Map>().toList();
    final conflicts = (payload['conflicts'] as List? ?? const []).whereType<Map>().toList();
    final hasAssumed = (payload['assumed_ids'] as List? ?? const []).isNotEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('${_score(comparison['before_score'])} → ${_score(comparison['after_score'])}',
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 18)),
        const SizedBox(height: 8),
        AspectRatio(
          aspectRatio: 1,
          child: CustomPaint(
            painter: SchemePlanPainter(
              before: before, after: after, changedIds: changed, removedIds: removed,
            ),
            child: const SizedBox.expand(),
          ),
        ),
        if (conflicts.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text('该位置与 ${conflicts.first['other']} 重叠，已拒绝',
                style: const TextStyle(color: Colors.red)),
          ),
        if (hasAssumed)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text('含假设放置物体 · 非实测', style: TextStyle(color: kSchemeAssumed)),
          ),
        const SizedBox(height: 8),
        ...metricChanges.take(6).map((item) => Text(
            '· ${item['name']}：${item['before']}${item['unit'] ?? ''} → ${item['after']}${item['unit'] ?? ''}')),
        const SizedBox(height: 8),
        Wrap(spacing: 8, children: [
          if (onEdit != null) OutlinedButton(onPressed: onEdit, child: const Text('进入沙盒编辑')),
          if (onSave != null) OutlinedButton(onPressed: onSave, child: const Text('保存为方案')),
          if (onExport != null) FilledButton(onPressed: onExport, child: const Text('导出')),
        ]),
        const Padding(
          padding: EdgeInsets.only(top: 8),
          child: Text('沙盒模拟 · 非实测', style: TextStyle(fontSize: 12, color: Colors.grey)),
        ),
      ],
    );
  }
}
```

- [ ] **Step 7: 写结果卡测试**

创建 `app/test/scheme_result_card_test.dart`：

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/widgets/scheme_result_card.dart';

Map<String, dynamic> _layout(double deskY) => {
      'room': {
        'bounds_xy': {
          'min': [0.0, 0.0],
          'max': [4.0, 4.0],
        },
      },
      'objects': [
        {'id': 'bed_001', 'label': '床', 'center': [1.0, 3.0, 0.3], 'size': [2.0, 1.2, 0.6], 'rotation_z_deg': 0.0},
        {'id': 'desk_001', 'label': '书桌', 'center': [1.2, deskY, 0.4], 'size': [1.0, 0.5, 0.8], 'rotation_z_deg': 0.0},
      ],
    };

Map<String, dynamic> _payload() => {
      'layout_before': _layout(2.0),
      'layout_after': _layout(1.0),
      'changed_ids': ['desk_001'],
      'removed_ids': <String>[],
      'assumed_ids': <String>[],
      'conflicts': <Map<String, dynamic>>[],
      'comparison': {'before_score': 80.0, 'after_score': 85.0, 'score_delta': 5.0},
      'metric_changes': [
        {'metric_code': 'bedside_clearance', 'name': '床边活动空间', 'unit': 'm', 'before': 0.30, 'after': 0.55},
      ],
    };

void main() {
  testWidgets('方案结果卡展示分数、叠影图与指标变化', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SingleChildScrollView(child: SchemeResultCard(payload: _payload()))),
    ));
    expect(find.text('80.0 → 85.0'), findsOneWidget);
    expect(find.text('沙盒模拟 · 非实测'), findsOneWidget);
    expect(find.textContaining('床边活动空间'), findsOneWidget);
    expect(find.byType(CustomPaint), findsWidgets);
  });
}
```

- [ ] **Step 8: 接入报告页与 API**

`app/lib/api/client.dart` 新增：

```dart
  Future<Map<String, dynamic>> simulateScheme(int scanId, List<Map<String, dynamic>> edits) async =>
      (await dio.post('/api/assistant/scans/$scanId/simulate', data: {'edits': edits})).data;

  Future<Map<String, dynamic>> solveSuggestions(int scanId, List<String> metricCodes) async =>
      (await dio.post('/api/assistant/scans/$scanId/solve', data: {'metric_codes': metricCodes})).data;
```

`app/lib/pages/report_page.dart`：

- 助手卡片中勾选建议后，按钮改为「生成方案」：调用 `solveSuggestions(scanId, 选中项的 metric_code)`，取 `results.first`；
  - 若 `solved == false`，直接用 Text 展示 `shortfall`（不显示分数变化）；
  - 若 `solved == true`，用其 `edits` 调 `simulateScheme(scanId, edits)` 拿到完整 payload 存进 `_assistantResult`。
- 自由改造输入框：`_runSimulation(prompt: text)` 改调 `simulateScheme`（后端已支持 prompt 入口）——第一阶段先传空 edits + prompt 走同一条链路时，可继续调用 `/simulate` 的 `prompt` 字段。
- `_assistantResultView` 整体替换为：

```dart
  List<Widget> _assistantResultView(BuildContext context) => [
        SchemeResultCard(
          payload: _assistantResult!,
          onSave: _saveScheme,
          onEdit: null,   // 阶段二接入全屏编辑器
          onExport: _exportScheme,
        ),
      ];
```

其中 `_saveScheme` 调 `POST /schemes`，`_exportScheme` 在 Task 6 实现。

- [ ] **Step 9: 运行前端测试并提交**

Run: `cd app; flutter test test/scheme_plan_geometry_test.dart test/scheme_result_card_test.dart`
Expected: PASS

```bash
git add app/lib/models/scheme_layout.dart app/lib/widgets/scheme_plan_painter.dart app/lib/widgets/scheme_result_card.dart app/lib/api/client.dart app/lib/pages/report_page.dart app/test/scheme_plan_geometry_test.dart app/test/scheme_result_card_test.dart
git commit -m "feat(app): render renovation scheme overlay card"
```

---
### Task 6: 导出 PNG 与 PDF 附录

**Files:**
- Create: `backend/pipeline/scheme_pdf.py`
- Modify: `backend/app/routers/renovation.py`（新增导出端点）
- Modify: `app/pubspec.yaml`（新增 `path_provider`、`share_plus`）
- Create: `app/lib/services/scheme_export.dart`
- Test: `backend/tests/test_scheme_pdf.py`、`app/test/scheme_export_test.dart`

**Interfaces:**
- Produces: `pipeline.scheme_pdf.build_scheme_appendix_pdf(...) -> Path`；`POST /api/assistant/scans/{scan_id}/schemes/{scheme_id}/export.pdf`（multipart 字段 `plan`）；`renderSchemePng(SchemeScene, {double pixelRatio = 3.0, double size = 360}) -> Future<Uint8List>`；`exportSchemePdf({required Dio dio, required int scanId, required int schemeId, required Uint8List png, required String schemeName}) -> Future<File>`

- [ ] **Step 1: 写后端失败测试**

创建 `backend/tests/test_scheme_pdf.py`：

```python
import io

import PIL.Image
from pipeline.scheme_pdf import build_scheme_appendix_pdf


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    PIL.Image.new("RGB", (240, 240), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_build_scheme_appendix_pdf(tmp_path):
    out = tmp_path / "scheme.pdf"
    path = build_scheme_appendix_pdf(
        scan_name="王奶奶家", scheme_name="方案A · 书架下移",
        created_at="2026-09-26T18:20:00Z",
        plan_png=_png_bytes(),
        before_score=80.0, after_score=85.0,
        metric_changes=[{"name": "床边活动空间", "unit": "m", "before": 0.30, "after": 0.55}],
        risk_changes=[{"name": "床边活动空间不足", "before": "high", "after": "low"}],
        provenance={"measurements_sha256": "abc"},
        disclaimer="沙盒模拟 · 非实测",
        output=out,
    )
    assert path == out and out.is_file()
    assert out.read_bytes().startswith(b"%PDF")
    assert out.stat().st_size > 1500
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_scheme_pdf.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'pipeline.scheme_pdf'`

- [ ] **Step 3: 实现 PDF 附录**

创建 `backend/pipeline/scheme_pdf.py`（复用 `pipeline/pdf_report.py` 的中文字体与表格写法）：

```python
"""改造方案 PDF 附录：客户端渲染的叠影图 + 指标/风险变化 + 声明。"""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

DEFAULT_DISCLAIMER = "沙盒模拟 · 非实测：本页分数由正式评估链路在假设布局上重算，不代表现场实测结论。"


def _fmt(value) -> str:
    return "无法评分" if value is None else f"{float(value):.1f}"


def _fmt_delta(before, after) -> str:
    if before is None or after is None:
        return "—"
    delta = float(after) - float(before)
    return f"{'+' if delta >= 0 else ''}{delta:.1f}"


def _table(rows: list[list[str]], style: ParagraphStyle) -> Table:
    data = [[Paragraph(str(cell), style) for cell in row] for row in rows]
    table = Table(data, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D1D1D6")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def build_scheme_appendix_pdf(*, scan_name: str, scheme_name: str, created_at: str,
                              plan_png: bytes, before_score, after_score,
                              metric_changes: list[dict], risk_changes: list[dict],
                              provenance: dict, disclaimer: str, output: Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="STSong-Light",
                          fontSize=10, leading=15)
    title = ParagraphStyle("title", parent=body, fontSize=17, leading=24, spaceAfter=6)

    flow = [
        Paragraph("改造方案附录（沙盒模拟）", title),
        Paragraph(f"项目：{scan_name}　方案：{scheme_name}", body),
        Paragraph(f"生成时间：{created_at}", body),
        Paragraph(f"评分变化：{_fmt(before_score)} → {_fmt(after_score)}"
                  f"（Δ {_fmt_delta(before_score, after_score)}）", body),
        Spacer(1, 6 * mm),
        Image(io.BytesIO(plan_png), width=110 * mm, height=110 * mm),
        Spacer(1, 4 * mm),
        Paragraph("关键指标变化", body),
        _table([["指标", "改造前", "改造后"]] +
               [[m["name"], f"{m['before']}{m.get('unit') or ''}", f"{m['after']}{m.get('unit') or ''}"]
                for m in metric_changes], body),
        Spacer(1, 4 * mm),
        Paragraph("风险等级变化", body),
        _table([["风险项", "改造前", "改造后"]] +
               [[r["name"], r.get("before") or "-", r.get("after") or "-"] for r in risk_changes], body),
        Spacer(1, 6 * mm),
        Paragraph(disclaimer or DEFAULT_DISCLAIMER, body),
        Paragraph(f"输入指纹：{provenance}", body),
    ]
    SimpleDocTemplate(str(output), pagesize=A4, title=f"{scan_name} · {scheme_name}").build(flow)
    return output
```

- [ ] **Step 4: 新增导出端点并运行后端测试**

在 `backend/app/routers/renovation.py` 追加：

```python
from fastapi import File, UploadFile
from fastapi.responses import FileResponse
from pipeline.scheme_pdf import build_scheme_appendix_pdf


@router.post("/scans/{scan_id}/schemes/{scheme_id}/export.pdf")
async def export_scheme_pdf(
    scan_id: int,
    scheme_id: int,
    plan: UploadFile = File(...),
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan, work = _scan_and_work(scan_id, db, org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    measurements, structure = _require_measurements(work), _load_structure(work)
    payload = _scheme_payload(scan_id, measurements, structure, scheme.edits or [])
    png = await plan.read()
    if not png.startswith(b"\x89PNG"):
        raise HTTPException(422, "plan 必须是 PNG 图片")
    out_dir = work / "schemes"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"scheme_{scheme_id}.pdf"
    build_scheme_appendix_pdf(
        scan_name=(scan.project.name if scan.project else f"扫描 {scan_id}"),
        scheme_name=scheme.name,
        created_at=(scheme.created_at.isoformat() if scheme.created_at else ""),
        plan_png=png,
        before_score=(payload.get("comparison") or {}).get("before_score"),
        after_score=(payload.get("comparison") or {}).get("after_score"),
        metric_changes=payload["metric_changes"], risk_changes=payload["risk_changes"],
        provenance=scheme.based_on or {}, disclaimer=payload["disclaimer"], output=output,
    )
    return FileResponse(output, media_type="application/pdf", filename=f"{scheme.name}.pdf")
```

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_scheme_pdf.py -v`
Expected: PASS
- [ ] **Step 5: 前端导出与分享**

Run: `cd app; flutter pub add path_provider share_plus`

创建 `app/lib/services/scheme_export.dart`：

```dart
import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../models/scheme_layout.dart';
import '../widgets/scheme_plan_painter.dart';

/// 离屏渲染叠影图：与屏幕上的 CustomPainter 共用 paintSchemePlan。
Future<Uint8List> renderSchemePng(SchemeScene scene,
    {double pixelRatio = 3.0, double size = 360}) async {
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder, Rect.fromLTWH(0, 0, size, size));
  canvas.drawRect(Rect.fromLTWH(0, 0, size, size), Paint()..color = const Color(0xFFF6F4EE));
  paintSchemePlan(canvas, scene);
  final picture = recorder.endRecording();
  final image = await picture.toImage((size * pixelRatio).round(), (size * pixelRatio).round());
  final data = await image.toByteData(format: ui.ImageByteFormat.png);
  return data!.buffer.asUint8List();
}

Future<File> exportSchemePdf({
  required Dio dio,
  required int scanId,
  required int schemeId,
  required Uint8List png,
  required String schemeName,
}) async {
  final response = await dio.post(
    '/api/assistant/scans/$scanId/schemes/$schemeId/export.pdf',
    data: FormData.fromMap({'plan': MultipartFile.fromBytes(png, filename: 'plan.png')}),
    options: Options(responseType: ResponseType.bytes),
  );
  final dir = await getTemporaryDirectory();
  final file = File('${dir.path}/$schemeName.pdf');
  await file.writeAsBytes(response.data as List<int>);
  return file;
}

Future<void> shareFile(File file) =>
    Share.shareXFiles([XFile(file.path)], text: '改造方案（沙盒模拟 · 非实测）');
```

- [ ] **Step 6: 写前端导出测试**

创建 `app/test/scheme_export_test.dart`：

```dart
import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:anjing_app/models/scheme_layout.dart';
import 'package:anjing_app/services/scheme_export.dart';

void main() {
  testWidgets('离屏渲染输出 PNG 字节', (tester) async {
    final layout = SchemeLayout.fromJson({
      'room': {
        'bounds_xy': {
          'min': [0.0, 0.0],
          'max': [4.0, 4.0],
        },
      },
      'objects': [
        {'id': 'bed_001', 'label': '床', 'center': [1.0, 3.0, 0.3], 'size': [2.0, 1.2, 0.6], 'rotation_z_deg': 0.0}
      ],
    });
    final scene = buildSchemeScene(
      before: layout, after: layout, size: const Size(360, 360), padding: 16,
      changedIds: const {}, removedIds: const {},
    );
    final bytes = await renderSchemePng(scene);
    expect(bytes.length, greaterThan(1000));
    expect(bytes.sublist(0, 8), [137, 80, 78, 71, 13, 10, 26, 10]); // PNG 签名
  });
}
```

- [ ] **Step 7: 运行前端测试**

Run: `cd app; flutter test test/scheme_export_test.dart`
Expected: PASS

- [ ] **Step 8: 在报告页接上导出按钮**

`app/lib/pages/report_page.dart` 的 `_exportScheme`：

```dart
  Future<void> _exportScheme() async {
    final result = _assistantResult;
    if (result == null || _schemeId == null) return;
    final scene = buildSchemeScene(
      before: SchemeLayout.fromJson((result['layout_before'] as Map).cast<String, dynamic>()),
      after: SchemeLayout.fromJson((result['layout_after'] as Map).cast<String, dynamic>()),
      size: const Size(360, 360),
      padding: 16,
      changedIds: ((result['changed_ids'] as List?) ?? const []).map((e) => e.toString()).toSet(),
      removedIds: ((result['removed_ids'] as List?) ?? const []).map((e) => e.toString()).toSet(),
    );
    final png = await renderSchemePng(scene);
    final api = context.read<ApiClient>();
    final file = await exportSchemePdf(
      dio: api.dio, scanId: widget.scanId, schemeId: _schemeId!,
      png: png, schemeName: _schemeName ?? '改造方案',
    );
    await shareFile(file);
  }
```

- [ ] **Step 9: 提交**

```bash
git add backend/pipeline/scheme_pdf.py backend/app/routers/renovation.py backend/tests/test_scheme_pdf.py app/pubspec.yaml app/pubspec.lock app/lib/services/scheme_export.dart app/lib/pages/report_page.dart app/test/scheme_export_test.dart
git commit -m "feat: export renovation scheme as PNG and PDF appendix"
```

---

## 验收与完成定义

全部任务完成后逐条执行并留证：

- [ ] `cd backend; .\.venv\Scripts\python.exe -m pytest -q` —— 与基线相比不新增失败（基线 241 passed / 4 failed，见 `docs/risk-assessment-baseline.md`）
- [ ] `cd app; flutter test` —— 全绿
- [ ] `cd app; flutter analyze` —— 无新增告警
- [ ] **同源证明**：`tests/test_scheme_state.py::test_golden_scan46_matches_official_metrics` 通过（scan 46 的 15 项指标与 `spatial_metrics.json` 零差异，总分等于 80.0）
- [ ] **复现性**：保存任一方案后重新 `GET /schemes/{id}`，分数与保存时一致
- [ ] **诚实性**：门宽类风险返回 `requires_construction: true`；无解风险返回 `solved: false` 且 `shortfall` 非空
- [ ] **数据边界**：`/simulate` 响应 `scope.raw_media_accessed == false`，且调用前后 `postprocess/` 下文件字节不变
- [ ] **机构隔离**：跨机构访问方案返回 404
- [ ] **导出**：PNG 可打开；PDF 含对比图、指标表与输入指纹

**完成定义（DoD）**

1. `renovation_simulator.py` 中不再存在任何"未经验证的分差"路径（`_project_completed_suggestions` 及其调用被删除）。
2. 前端展示的每一个分数都能在服务端用同一次 `build_scheme` 复现。
3. 改后布局同时出现在 2D 叠影图与 3D 布局数据中，来源为同一份状态。
4. 新增物体在界面与导出中均带"假设放置，非实测"标注。
5. 正式报告、实测评分、点云与扫描产物在整条链路上保持只读。

## 阶段二（另行成计划）

按 spec 第 11 章：全屏编辑器、横屏/网页指标联动面板、多方案卡片对比、3D 叠影与播放动画。阶段二只消费本计划产出的 `layout` / `metrics` / `edits` 契约，不修改后端评分链路。
