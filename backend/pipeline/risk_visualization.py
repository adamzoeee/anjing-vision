"""3D 风险可视化：把风险测量转成可展示的 3D 标注渲染图。

第四阶段"3D 风险标注"核心模块：
- 通道过窄 → 红色通道段 + 测量线（含宽度文字角标）
- 门槛 → 高度箭头
- 台阶 → 台阶边界线
- 障碍物 → 3D 红框（OBB 线框）
- 积水/湿滑 → 地面半透明风险区域（预留）
- 坡度 → 坡面法向箭头（预留）

纯 Open3D 渲染，不占 GPU 训练资源；产物为多视角 PNG 标注图，供报告与前端展示。

设计约定：本模块只做"风险几何 → 图片"的渲染层，风险几何由上游
（spatial_measurement / pipeline_runner）提供；渲染层不 import 任何业务模块，
避免与正在运行的管道相互影响。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import open3d as o3d

RISK_COLOR = {
    "red": (0.95, 0.15, 0.15),
    "yellow": (1.0, 0.75, 0.05),
    "green": (0.2, 0.8, 0.35),
}

# 每类风险的默认标注颜色（独立于等级，保证可读性）
ANNOTATION_COLOR = {
    "passage": (1.0, 0.1, 0.1),      # 红
    "door": (1.0, 0.55, 0.0),        # 橙
    "threshold": (0.9, 0.2, 0.9),    # 紫
    "stairs": (1.0, 0.3, 0.0),       # 深橙
    "obstacle": (1.0, 0.05, 0.05),   # 红
    "wet": (0.2, 0.6, 1.0),          # 蓝
    "slope": (0.9, 0.8, 0.0),        # 黄
}


@dataclass
class RiskGeometry:
    """一种风险的 3D 标注几何。type 决定渲染方式，params 为具体几何参数。

    type 取值与参数：
    - "segment"：一段风险线段。params: {"p1": (3,), "p2": (3,), "label": str}
    - "arrow"：高度箭头（起点→终点）。params: {"p1": (3,), "p2": (3,), "label": str}
    - "box"：物体红框。params: {"center": (3,), "axes": (3,3), "extents": (3,),
      "label": str}（axes 为三个主轴单位向量，extents 为对应半轴长）
    - "area"：地面半透明风险区域。params: {"center": (3,), "radius": float,
      "label": str}
    - "polyline"：台阶边界折线。params: {"points": (N,3), "label": str}
    """

    kind: str
    label: str
    level: str = "red"
    params: dict = field(default_factory=dict)


def _pcd(points: np.ndarray, colors: np.ndarray | None = None) -> o3d.geometry.PointCloud:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=np.float64).reshape(-1, 3))
    if colors is not None and len(colors) == len(pcd.points):
        pcd.colors = o3d.utility.Vector3dVector(np.clip(colors, 0.0, 1.0))
    return pcd


def _segment_lineset(p1: np.ndarray, p2: np.ndarray, color, radius: float = 0.02) -> o3d.geometry.LineSet:
    p1 = np.asarray(p1, dtype=np.float64)
    p2 = np.asarray(p2, dtype=np.float64)
    lines = o3d.geometry.LineSet()
    lines.points = o3d.utility.Vector3dVector(np.stack([p1, p2]))
    lines.lines = o3d.utility.Vector2iVector(np.array([[0, 1]]))
    colors_arr = np.asarray([color, color])
    lines.colors = o3d.utility.Vector3dVector(colors_arr)
    # 用圆柱替代细线：Open3D 线宽不可调，细线在 720p 渲染里几乎不可见
    cylinder = _cylinder_between(p1, p2, radius=radius, color=color)
    return cylinder


def _cylinder_between(p1, p2, radius: float, color) -> o3d.geometry.TriangleMesh:
    """两点间圆柱体（可视化线段），Open3D 原生 LineSet 线宽固定为 1px。"""
    p1 = np.asarray(p1, dtype=np.float64)
    p2 = np.asarray(p2, dtype=np.float64)
    direction = p2 - p1
    length = float(np.linalg.norm(direction))
    if length < 1e-9:
        length = 1e-6
        direction = np.array([0.0, 0.0, 1e-6])
    mesh = o3d.geometry.TriangleMesh.create_cylinder(radius=radius, height=length)
    # 默认圆柱沿 z 轴；旋转到 direction 方向
    z_axis = np.array([0.0, 0.0, 1.0])
    axis = np.cross(z_axis, direction / length)
    if np.linalg.norm(axis) < 1e-9:
        rotation = np.eye(3)
        if direction[2] < 0:
            rotation = np.diag([1.0, -1.0, -1.0])
    else:
        angle = float(np.arccos(np.clip(direction[2] / length, -1.0, 1.0)))
        rotation = mesh.get_rotation_matrix_from_axis_angle(axis / np.linalg.norm(axis) * angle)
    mesh.rotate(rotation, center=np.zeros(3))
    mesh.translate((p1 + p2) / 2.0)
    mesh.paint_uniform_color(color)
    return mesh


def _box_geometry(center, axes, extents, color, line_radius: float = 0.015) -> list[o3d.geometry.TriangleMesh]:
    """OBB 线框：12 条棱分别渲染为圆柱。axes：(3,3) 行向量为三个主轴。"""
    center = np.asarray(center, dtype=np.float64)
    axes = np.asarray(axes, dtype=np.float64).reshape(3, 3)
    extents = np.asarray(extents, dtype=np.float64).reshape(3)
    corners = []
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                corner = center + sx * extents[0] * axes[0] + sy * extents[1] * axes[1] + sz * extents[2] * axes[2]
                corners.append(corner)
    corners = np.asarray(corners)
    edges = [
        (0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
        (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7),
    ]
    return [_cylinder_between(corners[i], corners[j], line_radius, color) for i, j in edges]


def _area_mesh(center, radius: float, color, opacity: float = 0.35) -> o3d.geometry.TriangleMesh:
    """地面半透明圆盘（积水/湿滑风险区域）。"""
    center = np.asarray(center, dtype=np.float64)
    mesh = o3d.geometry.TriangleMesh.create_cylinder(radius=radius, height=0.01)
    mesh.translate(center + np.array([0.0, 0.0, 0.005]))
    mesh.paint_uniform_color(color)
    mesh.compute_vertex_normals()
    # Open3D 颜色 alpha：用材质（渲染层在 render 时统一设置）
    return mesh


def _arrow_geometry(p1, p2, color, radius: float = 0.02) -> o3d.geometry.TriangleMesh:
    """高度箭头：杆 + 锥头。"""
    p1 = np.asarray(p1, dtype=np.float64)
    p2 = np.asarray(p2, dtype=np.float64)
    direction = p2 - p1
    length = float(np.linalg.norm(direction))
    if length < 1e-9:
        return _cylinder_between(p1, p2, radius, color)
    unit = direction / length
    shaft_end = p1 + unit * max(length - radius * 4, length * 0.5)
    shaft = _cylinder_between(p1, shaft_end, radius, color)
    cone = o3d.geometry.TriangleMesh.create_cone(radius=radius * 2.2, height=radius * 6)
    z_axis = np.array([0.0, 0.0, 1.0])
    axis = np.cross(z_axis, unit)
    if np.linalg.norm(axis) > 1e-9:
        angle = float(np.arccos(np.clip(unit[2], -1.0, 1.0)))
        cone.rotate(cone.get_rotation_matrix_from_axis_angle(axis / np.linalg.norm(axis) * angle), center=np.zeros(3))
    elif unit[2] < 0:
        cone.rotate(np.diag([1.0, -1.0, -1.0]), center=np.zeros(3))
    cone.translate(p2 - unit * radius * 2)
    cone.paint_uniform_color(color)
    return shaft + cone


def build_annotation_geometries(risk: RiskGeometry) -> list[o3d.geometry.TriangleMesh]:
    """单个风险 → 3D 标注几何列表。"""
    color = ANNOTATION_COLOR.get(risk.kind, (1.0, 0.0, 0.0))
    params = risk.params
    if risk.kind == "segment":
        return [_cylinder_between(params["p1"], params["p2"], radius=0.025, color=color)]
    if risk.kind == "arrow":
        return [_arrow_geometry(params["p1"], params["p2"], color=color)]
    if risk.kind == "box":
        return _box_geometry(
            params["center"],
            params["axes"],
            params["extents"],
            color=color,
        )
    if risk.kind == "area":
        return [_area_mesh(params["center"], params.get("radius", 0.3), color=color)]
    if risk.kind == "polyline":
        points = np.asarray(params["points"], dtype=np.float64)
        geoms = []
        for index in range(len(points) - 1):
            geoms.append(_cylinder_between(points[index], points[index + 1], 0.015, color))
        return geoms
    raise ValueError(f"未知风险标注类型: {risk.kind}")


def render_risk_annotations(
    points: np.ndarray,
    risk_geometries: list[RiskGeometry],
    out_dir,
    *,
    colors: np.ndarray | None = None,
    n_views: int = 4,
    width: int = 1280,
    height: int = 720,
    point_size: float = 1.5,
) -> list[str]:
    """渲染带 3D 风险标注的多视角点云图。

    Args:
        points: (N,3) 场景点云（任意坐标系，米制或相对均可）
        risk_geometries: RiskGeometry 列表（各风险的 3D 标注几何）
        out_dir: 输出目录（PNG 写入 view_0.png ... view_n.png）
        colors: 可选 (N,3) 点云颜色；缺省为灰
        n_views: 环绕视角数量
        width/height: 图像分辨率

    Returns:
        输出图片路径列表（str）。
    """
    import os
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(points) < 10:
        return []

    pcd = _pcd(points, colors)
    bbox = pcd.get_axis_aligned_bounding_box()
    center = bbox.get_center()
    extent = np.asarray(bbox.get_extent())
    scene_scale = float(np.linalg.norm(extent))
    if scene_scale < 1e-6:
        return []

    annotations: list[o3d.geometry.TriangleMesh] = []
    for risk in risk_geometries:
        try:
            annotations.extend(build_annotation_geometries(risk))
        except (KeyError, ValueError) as exc:  # 几何参数缺失的风险跳过，不阻断渲染
            print(f"[risk_visualization] 跳过标注 {risk.label}: {exc}")

    paths: list[str] = []
    for index in range(n_views):
        theta = index * (2.0 * np.pi / n_views)
        eye = center + scene_scale * 0.75 * np.array([np.cos(theta), np.sin(theta), 0.55])
        vis = o3d.visualization.Visualizer()
        try:
            vis.create_window(width=width, height=height, visible=False)
            vis.add_geometry(pcd)
            for geom in annotations:
                vis.add_geometry(geom)
            opt = vis.get_render_option()
            opt.point_size = point_size
            opt.background_color = np.array([0.06, 0.07, 0.1])
            ctr = vis.get_view_control()
            ctr.set_lookat(center)
            ctr.set_front((eye - center) / np.linalg.norm(eye - center))
            ctr.set_up([0.0, 0.0, 1.0])
            ctr.set_zoom(0.75)
            vis.poll_events()
            vis.update_renderer()
            path = out_dir / f"risk_view_{index}.png"
            vis.capture_screen_image(str(path), do_render=True)
            paths.append(str(path))
        finally:
            vis.destroy_window()
    return paths
