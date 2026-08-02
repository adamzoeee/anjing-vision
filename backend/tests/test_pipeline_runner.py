"""pipeline_runner 的标定逻辑单元测试（不依赖 GPU/DB）。"""
import numpy as np
import pytest

from app.tasks.pipeline_runner import _calibrate_with_a4, _triangulate, _pixel_ray


def _cam(center, look_at, focal=600.0, w=640, h=480):
    """构造针孔相机：位置 center，看向 look_at。返回 {R, t, K}（world→cam 约定）。"""
    z = np.asarray(look_at, dtype=float) - np.asarray(center, dtype=float)
    z = z / np.linalg.norm(z)
    up = np.array([0.0, 0.0, 1.0])
    if abs(z @ up) > 0.99:  # 光轴与 up 平行时换参考轴
        up = np.array([1.0, 0.0, 0.0])
    x = np.cross(up, z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    R_w2c = np.stack([x, y, z], axis=0)  # world→cam
    C = np.asarray(center, dtype=float)
    t = -R_w2c @ C
    K = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1.0]])
    return {"R": R_w2c, "t": t, "K": K}


def test_pixel_ray_unit_norm():
    cam = _cam([0, 0, 0], [0, 0, -1])
    ray = _pixel_ray(cam, 320, 240)
    assert abs(np.linalg.norm(ray) - 1.0) < 1e-9


def test_triangulate_converging_rays():
    # 两条从不同起点指向同一点的射线 → 最近点≈目标
    target = np.array([0.0, 0.0, 2.0])
    C_i = np.array([0.5, 0.0, 0.0])
    C_j = np.array([-0.5, 0.0, 0.0])
    d_i = (target - C_i) / np.linalg.norm(target - C_i)
    d_j = (target - C_j) / np.linalg.norm(target - C_j)
    P = _triangulate(C_i, d_i, C_j, d_j)
    assert P is not None
    assert np.allclose(P, target, atol=1e-6)


def test_triangulate_parallel_rays():
    d = np.array([0.0, 0.0, 1.0])
    assert _triangulate(np.array([0.0, 0.0, 0.0]), d, np.array([1.0, 0.0, 0.0]), d) is None


def test_calibrate_with_a4_two_views(monkeypatch):
    """两帧看到同一 A4（画面中心、真实距离 2.0m、SFM 单位距离 1.0）→ 尺度≈2.0。"""
    # SFM 场景：A4 在原点，相机距离 1 单位（米制深度 2.0m → 尺度≈2.0 米/单位）
    a4_pos = np.array([0.0, 0.0, 0.0])
    cams = [
        _cam([0.3, 0.0, 1.0], a4_pos),
        _cam([-0.3, 0.0, 1.0], a4_pos),
    ]
    imgs = [np.zeros((480, 640, 3), dtype=np.uint8) for _ in cams]

    def fake_detect(img):
        # 焦距 600、真实距离 2.0m → 长边像素 = 600 * 0.297 / 2.0 = 89.1
        long_px = 600 * 0.297 / 2.0
        return (long_px, long_px / 1.414, 320.0, 240.0)  # (长, 短, cx, cy)

    monkeypatch.setattr("pipeline.calibrator.detect_a4_in_image", fake_detect)
    scale = _calibrate_with_a4(imgs, cams)
    assert scale is not None
    # 米制深度 2.0 / SFM 单位距离 ~1.04 → 尺度 ~1.9
    assert 1.5 < scale < 2.5
