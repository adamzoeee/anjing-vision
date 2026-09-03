"""视觉风险检测与 3D 定位（第三阶段 B 类）：积水/湿滑/电线/拖鞋/小杂物。

路线图核心：检测到障碍物 → 获得 3D 位置 → 和主要通行区域求空间关系 →
真的侵占通道才判定为风险。本模块实现"检测结果 → 3D 定位 → 侵占判定"的
空间推理层，2D 检测本身由 semantic 层（GroundingDINO+SAM）提供。

模块职责：
1. ``VISUAL_RISK_LABELS``：视觉风险类别与中文标签（开放词汇检测提示词）；
2. ``project_detection_to_ground``：相机 K/R/t + 2D mask → 3D 地面射线交点；
3. ``locate_visual_risks``：多帧检测聚合 → 每类风险的 3D 地面位置（质心）；
4. ``judge_in_passage``：3D 位置与通路自由区域求空间关系 → 是否侵占通道；
5. ``analyze_visual_risks``：组合入口，输出统一风险字典（供 rules/可视化消费）。

纯 numpy 实现，不 import 业务模块，可在管道运行期间独立测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# 视觉风险类别：英文提示词 → 中文标签（与 semantic.OBJECT_PROMPTS 风格一致）
VISUAL_RISK_PROMPTS: tuple[tuple[str, str], ...] = (
    ("water puddle", "积水"),
    ("wet floor", "湿滑地面"),
    ("electric wire", "电线"),
    ("cable on floor", "电线"),
    ("slipper", "拖鞋"),
    ("small object on floor", "地面小物"),
    ("rug corner folded", "地毯卷边"),
)
VISUAL_RISK_LABELS = frozenset(label for _prompt, label in VISUAL_RISK_PROMPTS)


@dataclass
class VisualRisk:
    """一个视觉风险实例。"""
    label: str
    ground_position: list[float]     # 3D 地面位置（房间坐标系）
    detections: int = 0              # 支持该风险的检测次数（跨帧）
    views: int = 0                   # 出现的视角数
    in_passage: bool | None = None   # 是否侵占通道（None=未判定）
    distance_to_path_m: float | None = None  # 到通行路径的最小距离
    confidence: float | None = None  # 识别置信度（检测得分均值）


def project_detection_to_ground(
    mask: np.ndarray,
    K: np.ndarray,
    R: np.ndarray,
    t: np.ndarray,
    ground_height: float = 0.0,
) -> np.ndarray | None:
    """2D mask 像素 → 3D 地面交点（房间坐标系，z=ground_height 平面）。

    相机约定：p_cam = R @ p_world + t（与 semantic.project_mask_to_points 一致）。
    返回 mask 内像素对应的地面 3D 点集 (M,3)；无有效交点返回 None。
    """
    mask = np.asarray(mask)
    K = np.asarray(K, dtype=np.float64)
    R = np.asarray(R, dtype=np.float64)
    t = np.asarray(t, dtype=np.float64)
    if mask.ndim != 2 or K.shape != (3, 3) or R.shape != (3, 3) or t.size != 3:
        raise ValueError("mask 需为 HxW；K/R 为 3x3；t 为 3 维")
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    # 每像素相机系射线方向
    uv = np.stack([xs.astype(np.float64), ys.astype(np.float64), np.ones(len(xs))], axis=1)
    rays_cam = (np.linalg.inv(K) @ uv.T).T  # (M,3)
    rays_world = (R.T @ rays_cam.T).T       # 世界系方向
    centers = -R.T @ t                       # 相机中心（世界）
    # 射线与 z=ground_height 平面求交：center + s*dir, z=gh → s = (gh - cz)/dz
    dz = rays_world[:, 2]
    valid = np.abs(dz) > 1e-9
    s = np.full(len(rays_world), np.nan)
    s[valid] = (ground_height - centers[2]) / dz[valid]
    hits = rays_world * s[:, None] + centers
    finite = valid & np.isfinite(hits).all(axis=1) & (s > 0)
    if not finite.any():
        return None
    return hits[finite]


def locate_visual_risks(
    detections_per_frame: list[list[dict]],
    cameras: list[dict],
    ground_height: float = 0.0,
) -> dict[str, VisualRisk]:
    """多帧视觉风险检测 → 每类风险的 3D 地面质心。

    detections_per_frame[i] = 第 i 帧的检测列表，每项：
        {"label": str, "mask": HxW bool, "score": float}
    cameras[i] = 对应帧相机 {K, R, t}（与 semantic 约定一致）。
    同类多帧位置取中位数质心，消除单帧噪声。
    """
    located: dict[str, dict] = {}
    for frame_dets, cam in zip(detections_per_frame, cameras):
        for det in frame_dets:
            label = det.get("label")
            if label not in VISUAL_RISK_LABELS:
                continue
            points = project_detection_to_ground(
                det["mask"], cam["K"], cam["R"], cam["t"], ground_height
            )
            if points is None or len(points) < 3:
                continue
            entry = located.setdefault(label, {"positions": [], "scores": [], "views": 0})
            entry["positions"].append(np.median(points, axis=0))
            entry["scores"].append(float(det.get("score", 0.0)))
            entry["views"] += 1
    result: dict[str, VisualRisk] = {}
    for label, entry in located.items():
        positions = np.asarray(entry["positions"])
        center = np.median(positions, axis=0)
        result[label] = VisualRisk(
            label=label,
            ground_position=[float(v) for v in center],
            detections=len(positions),
            views=entry["views"],
            confidence=round(float(np.mean(entry["scores"])), 3) if entry["scores"] else None,
        )
    return result


def judge_in_passage(
    risks: dict[str, VisualRisk],
    free_mask: np.ndarray,
    origin: np.ndarray,
    cell_size: float,
    path_cells: list[tuple[int, int]] | None = None,
    corridor_half_width_m: float = 0.6,
) -> dict[str, VisualRisk]:
    """视觉风险 3D 位置与通行区域求空间关系 → 侵占判定。

    free_mask：从门可达的自由区域（passage_metrics 产物）。
    判定：风险点所在栅格在自由区域内，且（若提供路径）距路径最近点
    <= corridor_half_width_m；无路径时仅要求位于自由区域。
    """
    free_mask = np.asarray(free_mask, dtype=bool)
    for risk in risks.values():
        pos = np.asarray(risk.ground_position)
        col = int(round((pos[0] - origin[0]) / cell_size))
        row = int(round((pos[1] - origin[1]) / cell_size))
        in_free = (
            0 <= row < free_mask.shape[0]
            and 0 <= col < free_mask.shape[1]
            and free_mask[row, col]
        )
        risk.in_passage = False
        if not in_free:
            continue
        if path_cells:
            cells = np.asarray(path_cells, dtype=np.float64)
            if len(cells):
                dists = np.linalg.norm(cells - np.array([row, col]), axis=1)
                min_cells = float(dists.min())
                risk.distance_to_path_m = round(min_cells * cell_size, 3)
                risk.in_passage = min_cells * cell_size <= corridor_half_width_m
        else:
            risk.in_passage = True
    return risks


def analyze_visual_risks(
    detections_per_frame: list[list[dict]],
    cameras: list[dict],
    *,
    ground_height: float = 0.0,
    free_mask: np.ndarray | None = None,
    origin: np.ndarray | None = None,
    cell_size: float = 0.05,
    path_cells: list[tuple[int, int]] | None = None,
) -> dict[str, VisualRisk]:
    """组合入口：检测 → 3D 定位 → 侵占判定，一步完成。"""
    risks = locate_visual_risks(detections_per_frame, cameras, ground_height)
    if free_mask is not None and origin is not None:
        risks = judge_in_passage(risks, free_mask, origin, cell_size, path_cells)
    return risks
