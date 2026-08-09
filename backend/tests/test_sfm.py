import numpy as np
import pytest
from pipeline.sfm import build_synthetic_cameras, run_sfm


def test_build_synthetic_cameras_circular():
    """绕圈相机：位姿数=帧数，相机朝向圆心附近，基线合理，R 满足针孔约定。"""
    cams = build_synthetic_cameras(n=12, radius=2.0)
    assert len(cams) == 12
    centers = np.array([c["center"] for c in cams])
    # 圆心大致在原点，z 在 1.5m 附近（模拟手持高度）
    assert np.abs(centers.mean(axis=0)[:2]).max() < 0.5
    assert 1.0 < centers[:, 2].mean() < 2.5
    # 相邻相机间距（基线）> 0.1m
    dists = np.linalg.norm(np.diff(centers, axis=0), axis=1)
    assert dists.min() > 0.1
    # R 是旋转矩阵：正交、det=+1
    for c in cams:
        R = c["R"]
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-9)
        assert np.isclose(np.linalg.det(R), 1.0, atol=1e-9)
        # 针孔约定：相机 z 轴（光轴，R 第 3 列）指向圆心方向（-center，水平）
        forward = -c["center"].copy()
        forward[2] = 0.0
        forward /= np.linalg.norm(forward)
        assert np.allclose(R[:, 2], forward, atol=1e-9)
        # 相机 y 轴指向世界 -Z（图像下方）
        assert np.allclose(R[:, 1], [0.0, 0.0, -1.0], atol=1e-9)


def test_run_sfm_accepts_minimal_inputs(tmp_path):
    """空输入应抛出明确错误而非崩溃。"""
    with pytest.raises(FileNotFoundError) as exc:
        run_sfm(tmp_path / "no_images", tmp_path / "out")
    assert "图片" in str(exc.value)


class _FakePose:
    @property
    def rotation(self):
        class _R:
            @staticmethod
            def matrix():
                return np.eye(3)
        return _R()

    @property
    def translation(self):
        return np.zeros(3)


class _FakeImage:
    def __init__(self, name):
        self.name = name
        self.camera_id = 0

    def cam_from_world(self):
        return _FakePose()


class _FakeCam:
    focal_length_x = 500.0
    focal_length_y = 500.0
    principal_point_x = 320.0
    principal_point_y = 240.0


class _FakeRecon:
    def __init__(self, n_images=1):
        self.images = {i: _FakeImage(f"a{i}.jpg") for i in range(n_images)}
        self.cameras = {0: _FakeCam()}
        self.points3D = {}


def _patch_pycolmap(monkeypatch, return_value):
    import pycolmap
    captured = {}
    monkeypatch.setattr(pycolmap, "extract_features", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(pycolmap, "match_exhaustive", lambda *a, **k: None)
    monkeypatch.setattr(pycolmap, "incremental_mapping", lambda *a, **k: return_value)
    return captured


def _make_image_dir(tmp_path):
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    import cv2
    cv2.imwrite(str(img_dir / "a.jpg"), np.zeros((24, 32, 3), dtype=np.uint8))
    return img_dir


def test_run_sfm_accepts_dict_return(tmp_path, monkeypatch):
    """pycolmap 4.x dict 返回形态：{model_index: Reconstruction}，取注册帧最多的主模型。"""
    captured = _patch_pycolmap(monkeypatch, {0: _FakeRecon(2), 1: _FakeRecon(1)})
    out = run_sfm(_make_image_dir(tmp_path), tmp_path / "work")
    assert len(out["cameras"]) == 2  # 取 2 帧的主模型而非 1 帧的次模型
    assert out["cameras"][0]["name"] == "a0.jpg"
    assert out["points3D"].shape == (0, 3)
    import pycolmap
    assert captured["camera_mode"] == pycolmap.CameraMode.SINGLE


def test_run_sfm_accepts_list_return(tmp_path, monkeypatch):
    """旧版 list/tuple 返回形态（成功路径）。"""
    _patch_pycolmap(monkeypatch, [_FakeRecon()])
    out = run_sfm(_make_image_dir(tmp_path), tmp_path / "work")
    assert len(out["cameras"]) == 1


def test_run_sfm_rejects_empty_dict(tmp_path, monkeypatch):
    """失败路径：空 dict 应抛 RuntimeError 而非崩溃。"""
    _patch_pycolmap(monkeypatch, {})
    with pytest.raises(RuntimeError):
        run_sfm(_make_image_dir(tmp_path), tmp_path / "work")
