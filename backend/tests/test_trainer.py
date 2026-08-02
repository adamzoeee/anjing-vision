import numpy as np
from pipeline.trainer import prepare_tensors


def test_prepare_tensors_shapes():
    cams = [
        {"R": np.eye(3), "t": np.array([0.0, 0.0, -1.0]), "K": np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1.0]])},
        {"R": np.eye(3), "t": np.array([0.0, 0.0, -2.0]), "K": np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1.0]])},
    ]
    imgs = [np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8) for _ in cams]
    gt = prepare_tensors(cams, imgs)
    assert gt["K"].shape == (2, 3, 3)
    assert gt["c2w"].shape == (2, 4, 4)
    assert gt["imgs"].shape == (2, 480, 640, 3)


def test_prepare_tensors_c2w_consistency():
    """c2w 与 center 一致：c2w 平移列 = center。"""
    cams = [{"R": np.eye(3), "t": np.array([0.0, 0.0, -2.0]),
             "K": np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1.0]])}]
    imgs = [np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)]
    gt = prepare_tensors(cams, imgs)
    c2w = gt["c2w"][0].cpu().numpy()
    # 世界→相机: p_cam = R @ p_world + t；c2w 平移 = -R.T @ t
    R, t = cams[0]["R"], cams[0]["t"]
    assert np.allclose(c2w[:3, 3], -R.T @ t)
    assert np.allclose(c2w[:3, :3], R.T)
