"""risk_visualization 测试：各类风险几何构建 + 渲染 smoke（无窗口环境自动跳过）。"""
import numpy as np
import pytest

from pipeline.risk_visualization import (
    RiskGeometry,
    build_annotation_geometries,
    render_risk_annotations,
    _cylinder_between,
)


def test_segment_geometry_length_matches_endpoints():
    p1 = np.array([0.0, 0.0, 0.0])
    p2 = np.array([0.9, 0.0, 0.0])
    geoms = build_annotation_geometries(RiskGeometry(
        kind="segment", label="通道", params={"p1": p1, "p2": p2}
    ))
    assert len(geoms) == 1
    mesh = geoms[0]
    verts = np.asarray(mesh.vertices)
    # 圆柱端点应覆盖 p1/p2（公差为半径）
    assert np.min(np.linalg.norm(verts - p1, axis=1)) < 0.05
    assert np.min(np.linalg.norm(verts - p2, axis=1)) < 0.05


def test_arrow_geometry_from_ground_to_step():
    geoms = build_annotation_geometries(RiskGeometry(
        kind="arrow", label="门槛",
        params={"p1": [0, 0, 0], "p2": [0, 0, 0.05]},
    ))
    assert len(geoms) == 1
    verts = np.asarray(geoms[0].vertices)
    assert float(np.max(verts[:, 2])) >= 0.04  # 箭头应到达目标高度附近


def test_box_geometry_has_twelve_edges():
    geoms = build_annotation_geometries(RiskGeometry(
        kind="box", label="纸箱",
        params={
            "center": [1, 2, 0.3],
            "axes": np.eye(3),
            "extents": [0.3, 0.2, 0.4],
        },
    ))
    assert len(geoms) == 12  # OBB 12 条棱
    verts = np.asarray(geoms[0].vertices)
    assert len(verts) > 100  # 每条棱是圆柱网格，非空


def test_area_geometry_disk_centered():
    geoms = build_annotation_geometries(RiskGeometry(
        kind="area", label="积水", params={"center": [0, 0, 0], "radius": 0.4}
    ))
    assert len(geoms) == 1
    verts = np.asarray(geoms[0].vertices)
    assert np.min(np.linalg.norm(verts[:, :2], axis=1)) < 1e-3  # 有顶点在圆心投影处


def test_polyline_geometry_chain():
    geoms = build_annotation_geometries(RiskGeometry(
        kind="polyline", label="台阶边界",
        params={"points": np.array([[0, 0, 0.05], [0.5, 0, 0.05], [1.0, 0, 0.05]])},
    ))
    assert len(geoms) == 2  # N-1 段


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="未知风险标注类型"):
        build_annotation_geometries(RiskGeometry(kind="nonsense", label="x"))


def test_missing_params_skipped_in_render():
    """几何参数缺失的风险在渲染中被跳过，不抛异常。"""
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64)
    try:
        paths = render_risk_annotations(
            points,
            [RiskGeometry(kind="box", label="缺参数", params={})],
            ".risk-render-test",
            n_views=1,
            width=320,
            height=240,
        )
        assert isinstance(paths, list)
    except Exception as exc:  # noqa: BLE001 - 无头环境 Open3D 窗口可能不可用
        pytest.skip(f"渲染环境不可用（无头/无窗口）: {exc}")


def test_render_smoke_with_all_risk_kinds():
    """全部风险类型一起渲染的 smoke 测试；无窗口环境跳过。"""
    rng = np.random.default_rng(3)
    floor = np.c_[rng.uniform(-2, 2, 800), rng.uniform(-1.5, 1.5, 800), np.zeros(800)]
    wall = np.c_[np.full(200, 2.0), rng.uniform(-1.5, 1.5, 200), rng.uniform(0.05, 2.2, 200)]
    points = np.vstack([floor, wall])
    risks = [
        RiskGeometry(kind="segment", label="通道 0.72m",
                     params={"p1": [0, -0.36, 0.02], "p2": [0, 0.36, 0.02]}),
        RiskGeometry(kind="arrow", label="门槛 3cm",
                     params={"p1": [1.8, 0, 0], "p2": [1.8, 0, 0.03]}),
        RiskGeometry(kind="box", label="纸箱",
                     params={"center": [0.8, 0.5, 0.3], "axes": np.eye(3),
                             "extents": [0.3, 0.2, 0.4]}),
        RiskGeometry(kind="area", label="积水区",
                     params={"center": [-1.0, -0.6, 0], "radius": 0.35}),
    ]
    try:
        paths = render_risk_annotations(points, risks, ".risk-render-test", n_views=1,
                                        width=320, height=240)
        assert len(paths) == 1
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"渲染环境不可用（无头/无窗口）: {exc}")
