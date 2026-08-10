import numpy as np
import torch
from pipeline.trainer import _ssim, denormalize_gaussians, normalize_scene, prepare_tensors


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
    assert gt["images_undistorted"] is False


def test_prepare_tensors_records_rectified_camera_contract():
    camera = {
        "R": np.eye(3), "t": np.zeros(3), "K": np.eye(3), "undistorted": True,
    }
    gt = prepare_tensors([camera], [np.zeros((4, 4, 3), dtype=np.uint8)])
    assert gt["images_undistorted"] is True


def test_ssim_prefers_identical_image():
    image = torch.rand(1, 32, 32, 3)
    identical = float(_ssim(image, image))
    different = float(_ssim(image, 1.0 - image))
    assert identical > 0.999
    assert identical > different


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


def test_scene_normalization_is_reversible_for_gaussian_means():
    cams = [
        {"R": np.eye(3), "t": np.array([-x, 0.0, 0.0]), "center": np.array([x, 0.0, 0.0])}
        for x in (0.0, 1.0, 2.0)
    ]
    points = np.array([[0.0, 0.0, 0.0], [2.0, 1.0, 1.0], [1.0, -1.0, 0.5]])
    normalized_cams, normalized_points, transform = normalize_scene(cams, points)
    assert np.isfinite(normalized_points).all()
    assert np.linalg.norm(normalized_cams[-1]["center"] - normalized_cams[0]["center"]) > 0
    restored = denormalize_gaussians({
        "means": torch.from_numpy(normalized_points.astype(np.float32)),
        "scales": torch.zeros((len(points), 3)),
    }, transform)
    assert np.allclose(restored["means"].numpy(), points, atol=1e-6)
