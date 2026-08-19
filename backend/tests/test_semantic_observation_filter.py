import numpy as np

from pipeline.semantic_observation_filter import purify_observations


def _observation(view, label, group, ids, camera=(0.0, 0.0, -2.0)):
    ids = np.asarray(ids, dtype=np.int64)
    return {
        "observation_id": f"obs_{view}_{label}", "frame_id": view, "frame_order": view,
        "camera_id": view, "semantic_label": label, "normalized_label": label,
        "canonical_group": group, "dino_confidence": 0.9, "sam_confidence": 0.95,
        "sam_mask_id": 0, "mask_area": max(len(ids) * 4, 1),
        "raw_projected_point_ids": ids, "projected_point_ids": ids,
        "projected_point_count": len(ids), "raw_projected_point_count": len(ids),
        "projection_filtering": {}, "centroid_3d": np.zeros(3),
        "bbox_3d_coarse": {"min": np.zeros(3), "max": np.ones(3)},
        "camera_position": np.asarray(camera), "camera_direction": np.asarray([0.0, 0.0, 1.0]),
        "image_name": f"{view:05d}.jpg",
    }


def _purify(points, observations, candidate_sets=None, candidate_groups=None):
    return purify_observations(
        observations, points, candidate_sets or {}, candidate_groups or {},
    )


def test_foreground_is_kept_and_far_single_view_background_is_removed():
    rng = np.random.default_rng(71)
    target = rng.normal([0.0, 0.0, 0.4], [0.12, 0.12, 0.05], size=(180, 3))
    background = rng.normal([0.0, 0.0, 2.2], [0.15, 0.15, 0.08], size=(120, 3))
    points = np.vstack([target, background])
    observations = [
        _observation(1, "床", "bed", np.arange(300)),
        _observation(2, "床", "bed", np.arange(180)),
        _observation(3, "床", "bed", np.arange(180)),
    ]
    purified, quality, _ = _purify(points, observations)
    assert len(purified[0]["projected_point_ids"]) >= 170
    assert np.max(purified[0]["projected_point_ids"]) < 180
    assert quality["observations"][0]["rejected_point_count"] >= 115


def test_two_cross_view_supported_surfaces_are_both_kept_not_only_largest():
    rng = np.random.default_rng(72)
    first = rng.normal([-0.8, 0.0, 0.5], [0.1, 0.1, 0.05], size=(170, 3))
    second = rng.normal([0.8, 0.0, 1.4], [0.09, 0.1, 0.05], size=(90, 3))
    points = np.vstack([first, second])
    ids = np.arange(len(points))
    observations = [_observation(view, "床", "bed", ids) for view in (1, 2, 3)]
    purified, _quality, _ = _purify(points, observations)
    assert len(purified[0]["projected_point_ids"]) >= len(points) - 5
    assert np.any(purified[0]["projected_point_ids"] >= len(first))


def test_single_frame_spatial_fragment_is_removed_without_depth_prior():
    rng = np.random.default_rng(73)
    target = rng.normal([0.0, 0.0, 0.5], [0.1, 0.1, 0.05], size=(160, 3))
    contaminant = rng.normal([1.2, 0.0, 0.5], [0.08, 0.08, 0.05], size=(60, 3))
    points = np.vstack([target, contaminant])
    observations = [
        _observation(1, "桌子", "table_group", np.arange(220)),
        _observation(2, "桌子", "table_group", np.arange(160)),
        _observation(3, "书桌", "table_group", np.arange(160)),
    ]
    purified, quality, _ = _purify(points, observations)
    assert np.max(purified[0]["projected_point_ids"]) < 160
    assert "single_view_unsupported_fragment" in quality["observations"][0]["reject_reasons"]


def test_real_multiview_cabinet_surface_near_wall_is_not_deleted():
    rng = np.random.default_rng(74)
    points = rng.normal([4.92, 0.0, 1.1], [0.02, 0.25, 0.35], size=(220, 3))
    ids = np.arange(len(points))
    observations = [_observation(view, "柜子", "storage_group", ids) for view in (1, 2, 3)]
    purified, _quality, _ = _purify(points, observations)
    assert len(purified[0]["projected_point_ids"]) >= 210


def test_bed_mask_vertical_background_without_cross_view_support_is_removed():
    rng = np.random.default_rng(75)
    bed = rng.normal([0.0, 0.0, 0.4], [0.35, 0.25, 0.04], size=(240, 3))
    vertical = rng.normal([0.0, 0.0, 1.6], [0.18, 0.04, 0.35], size=(130, 3))
    points = np.vstack([bed, vertical])
    observations = [
        _observation(1, "床", "bed", np.arange(370)),
        _observation(2, "床", "bed", np.arange(240)),
        _observation(3, "床", "bed", np.arange(240)),
    ]
    purified, quality, _ = _purify(points, observations)
    assert np.sum(purified[0]["projected_point_ids"] >= 240) <= 5
    assert quality["observations"][0]["depth_cluster_count"] >= 2


def test_high_quality_observation_does_not_lose_correct_surface():
    rng = np.random.default_rng(76)
    points = rng.normal([0.0, 0.0, 0.7], [0.3, 0.2, 0.08], size=(420, 3))
    ids = np.arange(len(points))
    observations = [_observation(view, "床", "bed", ids) for view in (1, 2, 3, 4)]
    purified, quality, _ = _purify(points, observations)
    assert len(purified[0]["projected_point_ids"]) >= int(len(points) * 0.98)
    assert quality["observations"][0]["observation_status"] in {"high_quality", "usable"}


def test_equal_cross_view_semantic_group_conflict_is_ambiguous():
    rng = np.random.default_rng(77)
    points = rng.normal([0.0, 0.0, 0.8], [0.2, 0.15, 0.08], size=(260, 3))
    ids = np.arange(len(points))
    observations = [
        _observation(1, "床", "bed", ids), _observation(2, "床", "bed", ids),
        _observation(3, "桌子", "table_group", ids), _observation(4, "书桌", "table_group", ids),
    ]
    _purified, quality, _ = _purify(points, observations)
    assert all(row["observation_status"] == "ambiguous" for row in quality["observations"])
    assert all(row["cross_view_consistency"] == 0.0 for row in quality["observations"])
