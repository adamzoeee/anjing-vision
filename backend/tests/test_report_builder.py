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
    cameras = [{
        "name": "frame.jpg",
        "R": np.eye(3),
        "t": np.array([1.0, 2.0, 3.0]),
        "K": np.array([[600.0, 0, 320], [0, 610.0, 240], [0, 0, 1]]),
    }]
    manifest = build_preview_assets(
        pts,
        tmp_path,
        title="测试房间",
        cameras=cameras,
        image_shapes=[(480, 640)],
        camera_scale=2.0,
    )
    assert (tmp_path / "scene.ply").exists()
    assert (tmp_path / "manifest.json").exists()
    assert manifest["title"] == "测试房间"
    assert manifest["point_count"] == 500
    assert manifest["cameras"] == "cameras.json"
    viewer_cameras = json.loads((tmp_path / "cameras.json").read_text(encoding="utf-8"))
    assert viewer_cameras[0]["position"] == [2.0, 4.0, 6.0]
    assert viewer_cameras[0]["width"] == 640
    assert viewer_cameras[0]["fy"] == 610.0


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
