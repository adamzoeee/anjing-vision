"""visual_risks 测试：射线-地面投影 / 3D 定位 / 侵占判定（合成相机与检测）。"""
import numpy as np
import pytest

from pipeline.visual_risks import (
    VISUAL_RISK_LABELS,
    VisualRisk,
    analyze_visual_risks,
    judge_in_passage,
    locate_visual_risks,
    project_detection_to_ground,
)


def _cam(center, look_at, focal=600.0, w=640, h=480):
    z = np.asarray(look_at, dtype=float) - np.asarray(center, dtype=float)
    z = z / np.linalg.norm(z)
    up = np.array([0.0, 0.0, 1.0])
    if abs(z @ up) > 0.99:
        up = np.array([1.0, 0.0, 0.0])
    x = np.cross(up, z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    r = np.stack([x, y, z], axis=0)
    t = -r @ np.asarray(center, dtype=float)
    k = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1.0]])
    return {"K": k, "R": r, "t": t}


def test_project_detection_to_ground_recovers_position():
    """地面已知点投影到像素 → 反投影应回到该点附近。"""
    ground_point = np.array([0.5, -0.3, 0.0])
    cam = _cam([0.0, 0.0, 1.5], ground_point)
    # 正投：地面点 → 像素
    cam_pt = cam["R"] @ ground_point + cam["t"]
    uv = cam["K"] @ (cam_pt / cam_pt[2])
    mask = np.zeros((480, 640), dtype=bool)
    # 在投影点周围画一个 9x9 小 mask
    cx, cy = int(round(uv[0])), int(round(uv[1]))
    mask[max(cy - 4, 0):cy + 5, max(cx - 4, 0):cx + 5] = True
    hits = project_detection_to_ground(mask, cam["K"], cam["R"], cam["t"], ground_height=0.0)
    assert hits is not None
    center = np.median(hits, axis=0)
    assert np.linalg.norm(center[:2] - ground_point[:2]) < 0.15  # 小 mask 中心近似地面点


def test_project_detection_to_ground_sky_rays_skipped():
    """朝上看的射线（无地面交点）被跳过，全 mask 无效时返回 None。"""
    cam = _cam([0.0, 0.0, 1.5], [0.0, 0.0, 5.0])  # 向上看，无地面
    mask = np.zeros((480, 640), dtype=bool)
    mask[230:250, 310:330] = True
    assert project_detection_to_ground(mask, cam["K"], cam["R"], cam["t"]) is None


def _detection_frames():
    """两帧在通道上看到'电线'，一帧在角落看到'拖鞋'。"""
    cams = [
        _cam([0.0, -1.0, 1.5], [0.0, 0.0, 0.0]),
        _cam([0.5, -1.0, 1.5], [0.0, 0.0, 0.0]),
        _cam([-2.0, -2.0, 1.5], [-1.8, -1.5, 0.0]),
    ]
    detections = []
    for cam, target in zip(cams, ([0.2, 0.0, 0.0], [0.2, 0.0, 0.0], [-1.8, -1.5, 0.0])):
        frame_dets = []
        for label, point in [("电线", np.asarray(target, dtype=float))]:
            cam_pt = cam["R"] @ point + cam["t"]
            uv = cam["K"] @ (cam_pt / cam_pt[2])
            mask = np.zeros((480, 640), dtype=bool)
            cx, cy = int(round(uv[0])), int(round(uv[1]))
            mask[max(cy - 3, 0):cy + 4, max(cx - 3, 0):cx + 4] = True
            frame_dets.append({"label": label, "mask": mask, "score": 0.8})
        detections.append(frame_dets)
    return detections, cams


def test_locate_visual_risks_aggregates_frames():
    detections, cams = _detection_frames()
    risks = locate_visual_risks(detections, cams)
    assert "电线" in risks
    risk = risks["电线"]
    assert risk.views == 3
    assert risk.detections == 3
    pos = np.asarray(risk.ground_position)
    assert np.linalg.norm(pos[:2] - [0.2, 0.0]) < 0.3  # 三帧位置聚合接近真实点
    assert risk.confidence is not None


def test_judge_in_passage_uses_corridor():
    risks = {
        "电线": VisualRisk(label="电线", ground_position=[0.2, 0.0, 0.0], detections=3, views=3),
        "拖鞋": VisualRisk(label="拖鞋", ground_position=[-1.8, -1.5, 0.0], detections=1, views=1),
    }
    free_mask = np.ones((40, 40), dtype=bool)
    origin = np.array([-2.0, -2.0])
    cell = 0.1
    # 路径：沿 y=0（row 20）从 x=-2 到 x=2
    path = [(20, col) for col in range(40)]
    judge_in_passage(risks, free_mask, origin, cell, path, corridor_half_width_m=0.6)
    assert risks["电线"].in_passage is True  # 距路径 0 → 侵占
    assert risks["拖鞋"].in_passage is False  # 角落距路径 >0.6m
    assert risks["电线"].distance_to_path_m is not None


def test_analyze_visual_risks_full_pipeline():
    detections, cams = _detection_frames()
    free_mask = np.ones((60, 60), dtype=bool)
    origin = np.array([-3.0, -3.0])
    risks = analyze_visual_risks(
        detections, cams, free_mask=free_mask, origin=origin, cell_size=0.1,
        path_cells=[(30, col) for col in range(60)],
    )
    assert "电线" in risks
    assert risks["电线"].in_passage in (True, False)
    assert all(isinstance(r, VisualRisk) for r in risks.values())


def test_visual_risk_labels_defined():
    assert VISUAL_RISK_LABELS >= {"积水", "湿滑地面", "电线", "拖鞋", "地面小物", "地毯卷边"}
