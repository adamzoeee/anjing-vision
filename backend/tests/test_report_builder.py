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
    assert manifest["title"] == "测试房间"
    assert manifest["point_count"] == 500
