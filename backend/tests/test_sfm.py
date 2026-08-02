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
