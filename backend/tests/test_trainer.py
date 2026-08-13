import numpy as np
import torch
from pipeline.trainer import NUM_ITER, ValidationEarlyStop, _quality_curve_still_improving, _ssim, denormalize_gaussians, normalize_scene, prepare_tensors


def test_default_training_budget_allows_complex_scene_to_reach_twenty_thousand_steps():
    assert NUM_ITER == 20_000


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


def test_early_stop_ignores_densification_and_stops_after_real_plateau():
    stopper = ValidationEarlyStop(patience=3)
    assert stopper.update(18.0, ssim=.70, min_psnr=14.0, refinement_finished=False) is False
    assert stopper.update(17.0, ssim=.68, min_psnr=13.0, refinement_finished=False) is False
    assert stopper.update(17.3, ssim=.71, min_psnr=14.1, refinement_finished=True) is False
    assert stopper.update(17.31, ssim=.711, min_psnr=14.11, refinement_finished=True) is False
    assert stopper.update(17.30, ssim=.711, min_psnr=14.11, refinement_finished=True) is False
    assert stopper.update(17.30, ssim=.711, min_psnr=14.11, refinement_finished=True) is True


def test_early_stop_resets_patience_after_meaningful_holdout_gain():
    stopper = ValidationEarlyStop(patience=2)
    stopper.update(20.0, ssim=.80, min_psnr=15.0, refinement_finished=True)
    assert stopper.update(20.01, ssim=.801, min_psnr=15.01, refinement_finished=True) is False
    assert stopper.update(20.2, ssim=.81, min_psnr=15.2, refinement_finished=True) is False
    assert stopper.stale == 0


def test_early_stop_keeps_training_when_worst_holdout_view_improves():
    stopper = ValidationEarlyStop(patience=2)
    stopper.update(22.0, ssim=.82, min_psnr=14.0, refinement_finished=True)
    assert stopper.update(22.0, ssim=.82, min_psnr=14.2, refinement_finished=True) is False
    assert stopper.stale == 0


def test_mean_gain_does_not_hide_worst_holdout_regression():
    stopper = ValidationEarlyStop(patience=2)
    stopper.update(20.0, ssim=.80, min_psnr=15.0, refinement_finished=True)
    assert stopper.update(20.5, ssim=.82, min_psnr=14.8, refinement_finished=True) is False
    assert stopper.stale == 1


def test_quality_curve_reports_unconverged_at_iteration_limit():
    curve = [
        {"validation_psnr_mean": 20.0, "validation_ssim_mean": .80, "validation_psnr_min": 15.0},
        {"validation_psnr_mean": 20.2, "validation_ssim_mean": .81, "validation_psnr_min": 15.1},
    ]
    assert _quality_curve_still_improving(curve) is True
    curve[-1]["validation_psnr_min"] = 14.8
    assert _quality_curve_still_improving(curve) is False
