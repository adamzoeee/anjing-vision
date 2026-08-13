import numpy as np
import pytest
import pipeline.sfm as sfm_module
from pipeline.sfm import (
    _build_long_range_pairs,
    build_synthetic_cameras,
    run_sfm,
    undistort_registered_view,
)


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
    width = 640
    height = 480
    params = np.array([500.0, 320.0, 240.0, 0.02])

    class model:
        name = "SIMPLE_RADIAL"


class _FakeRecon:
    def __init__(self, n_images=1):
        self.images = {i: _FakeImage(f"a{i}.jpg") for i in range(n_images)}
        self.cameras = {0: _FakeCam()}
        self.points3D = {}


def _patch_pycolmap(monkeypatch, return_value):
    import pycolmap
    captured = {}
    monkeypatch.setattr(pycolmap, "extract_features", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(
        pycolmap,
        "match_sequential",
        lambda *a, **k: captured.update({
            "pairing_options": k["pairing_options"],
            "sequential_matching_options": k["matching_options"],
        }),
    )
    monkeypatch.setattr(
        pycolmap,
        "match_exhaustive",
        lambda *a, **k: captured.update({
            "exhaustive_called": True,
            "exhaustive_matching_options": k["matching_options"],
        }),
    )
    monkeypatch.setattr(
        pycolmap,
        "match_image_pairs",
        lambda *a, **k: captured.update({
            "image_pairs_called": True,
            "image_pairs_matching_options": k["matching_options"],
            "image_pairs_pairing_options": k["pairing_options"],
        }),
    )
    monkeypatch.setattr(pycolmap, "incremental_mapping", lambda *a, **k: return_value)
    return captured


def _make_image_dir(tmp_path, count=1):
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    import cv2
    for index in range(count):
        # 零填充保证字典序 == 数字序（真实管线帧名为 frame_00001.jpg 零填充）
        cv2.imwrite(str(img_dir / f"a{index:03d}.jpg"), np.zeros((24, 32, 3), dtype=np.uint8))
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
    assert captured["pairing_options"].overlap == 30
    assert captured["pairing_options"].quadratic_overlap is True
    assert captured["extraction_options"].sift.max_num_features == 12000
    assert captured["extraction_options"].sift.peak_threshold == pytest.approx(0.004)
    assert captured["sequential_matching_options"].guided_matching is True
    assert out["cameras"][0]["camera_model"] == "SIMPLE_RADIAL"
    assert out["cameras"][0]["radial_distortion"] == pytest.approx([0.02])
    assert out["quality"]["component_count"] == 2
    assert out["quality"]["component_registered_images"] == [2, 1]


def test_undistort_registered_view_rectifies_radial_camera():
    image = np.zeros((31, 41, 3), dtype=np.uint8)
    image[5:26, 8:33] = 255
    camera = {
        "K": np.array([[35.0, 0, 20.0], [0, 35.0, 15.0], [0, 0, 1.0]]),
        "camera_model": "SIMPLE_RADIAL",
        "radial_distortion": np.array([0.1]),
    }
    rectified, pinhole = undistort_registered_view(image, camera)
    assert rectified.shape == image.shape
    assert pinhole["camera_model"] == "PINHOLE"
    assert pinhole["undistorted"] is True
    assert pinhole["source_radial_distortion"] == pytest.approx([0.1])
    assert np.array_equal(pinhole["K"], camera["K"])
    assert not np.array_equal(rectified, image)


def test_undistort_registered_view_is_noop_for_pinhole():
    image = np.arange(75, dtype=np.uint8).reshape(5, 5, 3)
    camera = {"K": np.eye(3), "camera_model": "PINHOLE"}
    rectified, pinhole = undistort_registered_view(image, camera)
    assert np.array_equal(rectified, image)
    assert rectified is not image
    assert pinhole["undistorted"] is True


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


def test_run_sfm_falls_back_to_exhaustive_when_main_track_is_incomplete(tmp_path, monkeypatch):
    """两轮顺序匹配的主轨迹不足70%时，必须用全量匹配兜底。"""
    import pycolmap

    captured = _patch_pycolmap(monkeypatch, {0: _FakeRecon(6), 1: _FakeRecon(3)})
    calls = iter([
        {0: _FakeRecon(6), 1: _FakeRecon(3)},
        {0: _FakeRecon(6), 1: _FakeRecon(3)},
        {0: _FakeRecon(9)},
    ])
    monkeypatch.setattr(pycolmap, "incremental_mapping", lambda *a, **k: next(calls))

    out = run_sfm(_make_image_dir(tmp_path, count=10), tmp_path / "work")

    assert captured["exhaustive_called"] is True
    assert captured["exhaustive_matching_options"].guided_matching is True
    assert len(out["cameras"]) == 9


def test_run_sfm_does_not_run_quadratic_fallback_when_main_track_is_complete(tmp_path, monkeypatch):
    """主模型超过70%时保留主坐标系，不为独立第二片段盲目承担全量匹配。"""
    import pycolmap

    captured = _patch_pycolmap(monkeypatch, {0: _FakeRecon(80), 1: _FakeRecon(15)})
    calls = iter([{0: _FakeRecon(80), 1: _FakeRecon(15)}, {0: _FakeRecon(80), 1: _FakeRecon(15)}])
    monkeypatch.setattr(pycolmap, "incremental_mapping", lambda *a, **k: next(calls))

    out = run_sfm(_make_image_dir(tmp_path, count=100), tmp_path / "work")

    assert captured.get("exhaustive_called", False) is False
    assert len(out["cameras"]) == 80
    assert out["quality"]["component_registered_images"] == [80, 15]


def test_run_sfm_uses_bounded_sequential_fallback_for_dense_video(tmp_path, monkeypatch):
    """高密度视频超过240张时不得触发平方级全量匹配。"""
    import pycolmap

    captured = _patch_pycolmap(monkeypatch, {0: _FakeRecon(120), 1: _FakeRecon(30)})
    calls = iter([
        {0: _FakeRecon(120), 1: _FakeRecon(30)},
        {0: _FakeRecon(120), 1: _FakeRecon(30)},
        {0: _FakeRecon(220)},
    ])
    monkeypatch.setattr(pycolmap, "incremental_mapping", lambda *a, **k: next(calls))

    out = run_sfm(_make_image_dir(tmp_path, count=241), tmp_path / "work")

    assert captured.get("exhaustive_called") is not True
    assert captured["pairing_options"].overlap == 60
    assert len(out["cameras"]) == 220


def test_long_range_candidates_are_nonlocal_and_bounded(tmp_path, monkeypatch):
    image_dir = _make_image_dir(tmp_path, count=100)
    rng = np.random.default_rng(42)
    descriptors = rng.integers(0, 256, size=(32, 32), dtype=np.uint8)
    monkeypatch.setattr(sfm_module, "_orb_signature", lambda _path: descriptors)

    pairs = _build_long_range_pairs(
        image_dir,
        max_anchors=10,
        candidates_per_anchor=2,
        max_pairs=7,
    )

    assert 0 < len(pairs) <= 7
    assert len(pairs) == len(set(pairs))
    for left, right in pairs:
        left_index = int(left.removeprefix("a").removesuffix(".jpg"))
        right_index = int(right.removeprefix("a").removesuffix(".jpg"))
        assert abs(right_index - left_index) >= 15


def test_long_range_pairs_use_guided_colmap_verification(tmp_path, monkeypatch):
    captured = _patch_pycolmap(monkeypatch, {0: _FakeRecon(2)})
    monkeypatch.setattr(
        sfm_module,
        "_build_long_range_pairs",
        lambda _image_dir: [("a0.jpg", "a1.jpg")],
    )

    run_sfm(_make_image_dir(tmp_path, count=2), tmp_path / "work")

    assert captured["image_pairs_called"] is True
    assert captured["image_pairs_matching_options"].guided_matching is True
    pair_path = captured["image_pairs_pairing_options"].match_list_path
    assert pair_path.read_text(encoding="utf-8") == "a0.jpg a1.jpg\n"


def test_loop_pairs_force_head_tail_closure(tmp_path, monkeypatch):
    """首尾窗口配对不依赖 ORB 分数，且不被 max_pairs 截断挤掉。"""
    image_dir = _make_image_dir(tmp_path, count=60)
    rng = np.random.default_rng(7)
    descriptors = rng.integers(0, 256, size=(32, 32), dtype=np.uint8)
    monkeypatch.setattr(sfm_module, "_orb_signature", lambda _path: descriptors)

    pairs = _build_long_range_pairs(
        image_dir,
        max_anchors=6,
        candidates_per_anchor=1,
        max_pairs=5,
        loop_window=8,
    )

    names = {(left, right) for left, right in pairs}
    head = {f"a{i:03d}.jpg" for i in range(8)}
    tail = {f"a{i:03d}.jpg" for i in range(52, 60)}
    forced_present = [(left, right) for left, right in names if left in head and right in tail]
    # 12×12 窗口的全组合中至少有部分出现在结果里（max_pairs=5 仍保留闭环候选）
    assert forced_present, "首尾闭环配对应优先保留"
    assert all(right in tail for left, right in forced_present if left in head)
    # 其余候选仍满足非邻接要求
    for left, right in names - set(forced_present):
        left_index = int(left.removeprefix("a").removesuffix(".jpg"))
        right_index = int(right.removeprefix("a").removesuffix(".jpg"))
        assert abs(right_index - left_index) >= 15


def _make_cameras_with_centers(centers):
    return [
        {"name": f"frame_{index:05d}.jpg", "center": np.asarray(center, dtype=np.float64)}
        for index, center in enumerate(centers)
    ]


def test_filter_trajectory_jumps_drops_jump_cluster():
    """中段快速甩动簇（步长 20× 中位）应被整体剔除，正常帧保留。"""
    rng = np.random.default_rng(3)
    centers = np.zeros((40, 3))
    for index in range(1, 40):
        centers[index] = centers[index - 1] + [0.05, 0.0, 0.0]
    # 第 20-23 帧发生快速甩动：步长 1.0（20× 中位步长 0.05）
    centers[20:, 0] += 1.0
    for index in range(20, 24):
        centers[index, 0] += 1.0 * (index - 19)
    cameras = _make_cameras_with_centers(centers)

    kept, dropped, diagnostics = sfm_module.filter_trajectory_jumps(cameras)

    assert len(dropped) > 0
    dropped_names = set(dropped)
    assert "frame_00020.jpg" in dropped_names
    assert diagnostics["dropped_count"] == len(dropped)
    assert diagnostics["kept_ratio"] > 0.7
    assert len(kept) + len(dropped) == len(cameras)


def test_filter_trajectory_jumps_keeps_smooth_trajectory():
    """匀速平滑轨迹应全部保留。"""
    centers = np.column_stack([
        np.linspace(0.0, 2.0, 50),
        np.zeros(50),
        np.full(50, 1.5),
    ])
    cameras = _make_cameras_with_centers(centers)

    kept, dropped, diagnostics = sfm_module.filter_trajectory_jumps(cameras)

    assert dropped == []
    assert len(kept) == 50
    assert diagnostics["kept_ratio"] == 1.0


def test_filter_trajectory_jumps_protects_min_kept_ratio():
    """整条轨迹剧烈跳变时只剔除最极端帧，不把输入删空。"""
    rng = np.random.default_rng(5)
    centers = rng.normal(size=(60, 3)) * 5.0
    centers[::2] += np.array([100.0, 0.0, 0.0])  # 一半帧是极端跳变
    cameras = _make_cameras_with_centers(centers)

    kept, dropped, _ = sfm_module.filter_trajectory_jumps(cameras)

    assert len(kept) >= 0.70 * len(cameras)
    assert len(dropped) <= 0.30 * len(cameras)


def test_refine_reconstruction_degrades_gracefully():
    """pycolmap 模型缺 points2D 时降级返回诊断，不抛异常。"""
    recon = _FakeRecon(n_images=2)
    diagnostics = sfm_module._refine_reconstruction(recon)

    assert diagnostics["filtered_points"] == 0
    assert "degraded_reason" in diagnostics
    assert diagnostics["bundle_adjustment"] is False
