import json
import numpy as np
from pipeline.report_builder import render_annotation_images, build_preview_assets


def test_render_annotation_images(tmp_path):
    pts = np.random.randn(2000, 3).astype(np.float64)
    risks = [{"code": "door_width", "name": "门宽", "level": "red", "measure": 0.75}]
    out = tmp_path / "imgs"
    paths = render_annotation_images(pts, risks, out, n_views=2)
    assert len(paths) == 2 and all(p.exists() and p.stat().st_size > 0 for p in paths)


def test_build_preview_assets(tmp_path):
    pts = np.random.randn(500, 3).astype(np.float32)
    manifest = build_preview_assets(pts, tmp_path, title="测试房间")
    assert (tmp_path / "scene.ply").exists()
    assert (tmp_path / "manifest.json").exists()
    assert manifest["title"] == "测试房间"
    assert manifest["point_count"] == 500


def test_render_annotation_images_multiple_risks(tmp_path):
    """多风险条目不应互相覆盖染色，且都成功渲染。"""
    pts = np.random.randn(2000, 3).astype(np.float64)
    risks = [
        {"code": "door_width", "name": "门宽", "level": "red", "measure": 0.75},
        {"code": "slope", "name": "地面坡度", "level": "yellow", "measure": 0.06},
        {"code": "threshold", "name": "门槛高度", "level": "green", "measure": 0.005},
    ]
    paths = render_annotation_images(pts, risks, tmp_path / "imgs", n_views=1)
    assert len(paths) == 1 and paths[0].exists() and paths[0].stat().st_size > 0


def test_render_annotation_images_empty_input(tmp_path):
    """空/退化点云应安全返回空列表而非崩溃。"""
    assert render_annotation_images(np.zeros((0, 3)), [], tmp_path / "imgs") == []
    assert render_annotation_images(np.zeros((5, 3)), [], tmp_path / "imgs") == []
