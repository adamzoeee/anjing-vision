"""把 scan46 的四段补拍点图只融合进各自目标区域。

安全约束：
1. 已验收基线点逐点保留，脚本只追加真实多视角观测；
2. 每段补拍只能进入与文件名对应的局部区域；
3. 新点必须贴近现有场景、获得至少四帧支持，拒绝悬浮点和单帧噪声；
4. 只产出候选，不修改 preview_selection.json。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
import cv2
from scipy.spatial import cKDTree
from scipy.ndimage import distance_transform_edt


def fit_similarity(source: np.ndarray, target: np.ndarray):
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    source_zero, target_zero = source - source_mean, target - target_mean
    covariance = source_zero.T @ target_zero / max(1, len(source))
    left, singular, right_t = np.linalg.svd(covariance)
    correction = np.eye(3)
    if np.linalg.det(right_t.T @ left.T) < 0:
        correction[-1, -1] = -1.0
    rotation = right_t.T @ correction @ left.T
    variance = float(np.mean(np.sum(source_zero * source_zero, axis=1)))
    scale = float(np.sum(singular * np.diag(correction)) / max(variance, 1e-12))
    translation = target_mean - scale * (source_mean @ rotation.T)
    return scale, rotation, translation


def robust_similarity(source: np.ndarray, target: np.ndarray, iterations: int = 5):
    keep = np.ones(len(source), dtype=bool)
    for _ in range(iterations):
        scale, rotation, translation = fit_similarity(source[keep], target[keep])
        residual = np.linalg.norm(scale * (source @ rotation.T) + translation - target, axis=1)
        cutoff = max(float(np.quantile(residual[keep], 0.72)), 1e-8)
        keep = residual <= cutoff
    scale, rotation, translation = fit_similarity(source[keep], target[keep])
    residual = np.linalg.norm(scale * (source @ rotation.T) + translation - target, axis=1)
    return scale, rotation, translation, residual


def metric(points: np.ndarray, alignment: dict) -> np.ndarray:
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    result = np.asarray(points, dtype=np.float64) @ rotation.T
    result[:, 2] -= floor_z
    return result * scale


def reduce_records(records: tuple[np.ndarray, ...]) -> tuple[np.ndarray, ...]:
    hashes, xyz_sum, rgb_sum, weight_sum, frame_count, max_conf = records
    if not len(hashes):
        return records
    order = np.argsort(hashes, kind="mergesort")
    hashes = hashes[order]
    starts = np.r_[0, np.flatnonzero(hashes[1:] != hashes[:-1]) + 1]
    return (
        hashes[starts],
        np.add.reduceat(xyz_sum[order], starts, axis=0),
        np.add.reduceat(rgb_sum[order], starts, axis=0),
        np.add.reduceat(weight_sum[order], starts),
        np.add.reduceat(frame_count[order], starts),
        np.maximum.reduceat(max_conf[order], starts),
    )


def empty_records() -> tuple[np.ndarray, ...]:
    return (
        np.empty(0, np.int64), np.empty((0, 3), np.float64),
        np.empty((0, 3), np.float64), np.empty(0, np.float64),
        np.empty(0, np.int32), np.empty(0, np.float32),
    )


def merge_records(left: tuple[np.ndarray, ...], right: tuple[np.ndarray, ...]):
    return reduce_records(tuple(np.concatenate([a, b]) for a, b in zip(left, right)))


def in_region(xyz: np.ndarray, clip_id: int) -> np.ndarray:
    """四段视频的一对一白名单；坐标来自 scan45/46 的米制 RoomFrame。"""
    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    if clip_id == 0:  # 床头墙：只接收右侧墙面附近，不碰床和房间内部
        return (x >= 1.76) & (x <= 2.02) & (y >= -0.48) & (y <= 1.31) & (z >= 0.10) & (z <= 2.54)
    if clip_id == 1:  # 橙色书架：仅书架及其紧邻侧面
        return (x >= -0.48) & (x <= 0.46) & (y >= 0.72) & (y <= 1.39) & (z >= 0.08) & (z <= 2.38)
    if clip_id == 2:  # 窗户墙：真实外墙在 y≈1.32，禁止填成室内假墙
        frame_band = (x <= .02) | (x >= 1.30) | (z <= 1.04) | (z >= 2.06)
        return ((x >= -.20) & (x <= 1.52) & (y >= 1.302) & (y <= 1.372)
                & (z >= .82) & (z <= 2.28) & frame_band)
    if clip_id == 3:  # 门前地板：仅最低地面层和门槛连接
        # 只接收贴着既有地面的真实观测。0.13m 的旧上限会把错配点
        # 留成第二层地板/门槛盖板，视觉上表现为穿透和厚边。
        return (x >= -0.48) & (x <= 0.55) & (y >= 0.55) & (y <= 1.39) & (z >= -0.035) & (z <= 0.045)
    return np.zeros(len(xyz), dtype=bool)


def image_features(image: np.ndarray, orb) -> tuple[list, np.ndarray | None]:
    image = np.asarray(image)
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    return orb.detectAndCompute(gray, None)


def render_metric_pointmap(points: np.ndarray, camera: dict, height: int = 224, width: int = 224) -> np.ndarray:
    """把已验收点云投到原视频帧，得到可靠的像素→米制3D锚点。"""
    center = np.asarray(camera["position"], np.float64)
    rotation = np.asarray(camera["rotation"], np.float64)
    cam = (points - center) @ rotation.T
    z = cam[:, 2]
    valid = z > 0.05
    cam, world, z = cam[valid], points[valid], z[valid]
    u = np.rint(float(camera["fx"]) * cam[:, 0] / z + float(camera["cx"])).astype(int)
    v = np.rint(float(camera["fy"]) * cam[:, 1] / z + float(camera["cy"])).astype(int)
    valid = (u >= 0) & (u < width) & (v >= 0) & (v < height)
    u, v, z, world = u[valid], v[valid], z[valid], world[valid]
    order = np.argsort(z)[::-1]  # 远到近覆盖，最终保留最近表面
    pointmap = np.full((height, width, 3), np.nan, np.float64)
    pointmap[v[order], u[order]] = world[order]
    occupied = np.isfinite(pointmap[..., 0])
    if np.any(occupied):
        distance, indices = distance_transform_edt(~occupied, return_indices=True)
        filled = pointmap[indices[0], indices[1]]
        pointmap[(~occupied) & (distance <= 7.0)] = filled[(~occupied) & (distance <= 7.0)]
    return pointmap


def direct_frame_transform(
    frame_id: int, clip: int, new_images: np.ndarray, base_images: np.ndarray,
    new_points: np.ndarray, baseline_metric_maps: dict[int, np.ndarray], base_conf: np.ndarray,
    new_conf: np.ndarray, baseline_features: dict[int, tuple[list, np.ndarray | None]],
    anchor_pool: dict[int, list[int]], orb, matcher, metric_scale: float,
):
    keypoints, descriptors = image_features(new_images[frame_id], orb)
    if descriptors is None or len(keypoints) < 10:
        return None
    best = None
    for baseline_id in anchor_pool[clip]:
        base_keypoints, base_descriptors = baseline_features[baseline_id]
        if base_descriptors is None:
            continue
        pairs = matcher.knnMatch(descriptors, base_descriptors, k=2)
        good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.76 * pair[1].distance]
        if len(good) < 9:
            continue
        source_2d = np.float32([keypoints[m.queryIdx].pt for m in good])
        target_2d = np.float32([base_keypoints[m.trainIdx].pt for m in good])
        _, inlier_mask = cv2.findHomography(source_2d, target_2d, cv2.RANSAC, 3.0)
        if inlier_mask is None:
            continue
        inliers = [m for m, keep in zip(good, inlier_mask.ravel().astype(bool)) if keep]
        if len(inliers) < 8 or (best is not None and len(inliers) <= len(best[1])):
            continue
        best = (baseline_id, inliers, keypoints, base_keypoints)
    if best is None:
        return None
    baseline_id, inliers, keypoints, base_keypoints = best
    height, width = new_points.shape[1:3]
    source_xyz, target_xyz = [], []
    for match in inliers:
        sx, sy = keypoints[match.queryIdx].pt
        tx, ty = base_keypoints[match.trainIdx].pt
        sx, sy = min(width - 1, max(0, int(round(sx)))), min(height - 1, max(0, int(round(sy))))
        tx, ty = min(width - 1, max(0, int(round(tx)))), min(height - 1, max(0, int(round(ty))))
        if float(new_conf[frame_id, sy, sx]) < 4.0:
            continue
        target = baseline_metric_maps[baseline_id][ty, tx]
        if not np.isfinite(target).all():
            continue
        source_xyz.append(new_points[frame_id, sy, sx])
        target_xyz.append(target)
    if len(source_xyz) < 8:
        return None
    source_xyz = np.asarray(source_xyz, np.float64).reshape(-1, 3)
    target_xyz = np.asarray(target_xyz, np.float64).reshape(-1, 3)
    valid = np.isfinite(source_xyz).all(1) & np.isfinite(target_xyz).all(1)
    source_xyz, target_xyz = source_xyz[valid], target_xyz[valid]
    if len(source_xyz) < 8:
        return None
    scale, rotation, translation, residual = robust_similarity(source_xyz, target_xyz, iterations=4)
    residual_m = residual
    metrics = {
        "baseline_id": int(baseline_id), "image_inliers": int(len(inliers)),
        "correspondences": int(len(source_xyz)), "scale": float(scale),
        "median_error_m": float(np.median(residual_m)),
        "p90_error_m": float(np.quantile(residual_m, 0.90)),
    }
    if not (0.40 <= scale <= 2.5 and metrics["median_error_m"] <= 0.08 and metrics["p90_error_m"] <= 0.20):
        return None
    return scale, rotation, translation, metrics


def main(args: argparse.Namespace) -> dict:
    baseline_work, candidate_root = args.baseline_work, args.candidate_root
    mapping = json.loads((candidate_root / "supplement_mapping.json").read_text(encoding="utf-8"))["mapping"]
    base_preds = baseline_work / "slam3r/scene/preds"
    new_preds = candidate_root / "slam3r/scene/preds"
    base_points = np.load(base_preds / "registered_pcds.npy", mmap_mode="r")
    base_conf = np.load(base_preds / "registered_confs.npy", mmap_mode="r")
    new_points = np.load(new_preds / "registered_pcds.npy", mmap_mode="r")
    new_conf = np.load(new_preds / "registered_confs.npy", mmap_mode="r")
    new_images = np.load(new_preds / "input_imgs.npy", mmap_mode="r")
    base_images = np.load(base_preds / "input_imgs.npy", mmap_mode="r")
    alignment = json.loads(args.alignment.read_text(encoding="utf-8"))
    metric_scale = float(alignment["scale"]["applied"])
    rng = np.random.default_rng(462024)

    active_clips = sorted({int(item["clip_id"]) for item in mapping if item["kind"] == "supplement"})
    supplement_ranges: dict[int, tuple[int, int]] = {}
    for clip in active_clips:
        ids = [int(item["output_index"]) for item in mapping if int(item["clip_id"]) == clip and item["kind"] == "supplement"]
        supplement_ranges[clip] = (min(ids), max(ids))
    anchors: dict[tuple[int, str], tuple[list[np.ndarray], list[np.ndarray]]] = {}
    for item in mapping:
        if item["kind"] != "anchor":
            continue
        clip, new_id, old_id = int(item["clip_id"]), int(item["output_index"]), int(item["baseline_index"])
        source = np.asarray(new_points[new_id], np.float64).reshape(-1, 3)
        target = np.asarray(base_points[old_id], np.float64).reshape(-1, 3)
        confidence = np.minimum(np.asarray(new_conf[new_id]).reshape(-1), np.asarray(base_conf[old_id]).reshape(-1))
        valid = np.isfinite(source).all(1) & np.isfinite(target).all(1) & (confidence >= args.min_confidence)
        ids = np.flatnonzero(valid)
        if len(ids) > 1600:
            ids = rng.choice(ids, 1600, replace=False)
        first_supplement, last_supplement = supplement_ranges[clip]
        side = "pre" if new_id < first_supplement else "post"
        pair = anchors.setdefault((clip, side), ([], []))
        pair[0].append(source[ids]); pair[1].append(target[ids])

    transforms, anchor_metrics = {}, {}
    for (clip, side), (source_parts, target_parts) in sorted(anchors.items()):
        source, target = np.concatenate(source_parts), np.concatenate(target_parts)
        scale, rotation, translation, residual = robust_similarity(source, target)
        residual_m = residual * metric_scale
        metrics = {
            "correspondences": int(len(source)), "scale": float(scale),
            "median_error_m": float(np.median(residual_m)),
            "p90_error_m": float(np.quantile(residual_m, 0.90)),
        }
        anchor_metrics[f"{clip}_{side}"] = metrics
        if 0.45 <= scale <= 2.2 and metrics["median_error_m"] <= 0.075 and metrics["p90_error_m"] <= 0.15:
            transforms[(clip, side)] = (scale, rotation, translation)

    anchor_pool = {
        clip: sorted({int(item["baseline_index"]) for item in mapping if item["kind"] == "anchor" and int(item["clip_id"]) == clip})
        for clip in active_clips
    }
    # 全片检索得到的真实重叠帧。原先只取补拍插入点前后帧，书架/窗户
    # 实际并不在那些画面中，SIFT 会被墙面或地砖误导到错误高度。
    verified_global_anchors = {
        1: [294, 297, 300, 303, 306, 309, 312, 315, 318, 390, 393, 396, 399, 402, 405, 417, 867],
        2: [129, 132, 138, 144, 147, 150, 153, 156, 159, 162, 165, 168, 672, 678],
        # 地面补拍的开头与原视频 22..28 重叠，结尾与 829..835 重叠。
        # 旧代码只用第24帧，容易让一个偶然匹配决定整段坐标；两端共同
        # 约束后再由 medoid 选择稳定变换，避免把地面抬到墙/天花板高度。
        3: [22, 23, 24, 25, 26, 27, 28, 829, 830, 831, 832, 833, 834, 835],
    }
    for clip in active_clips:
        if clip in verified_global_anchors:
            anchor_pool[clip] = verified_global_anchors[clip]
    # 跨视频的曝光、压缩与尺度变化会让 ORB 大量失配；SIFT 在这里仅用于
    # 找到同一真实表面的二维对应，不参与生成颜色或臆造几何。
    orb = cv2.SIFT_create(nfeatures=1600, contrastThreshold=0.008, edgeThreshold=14)
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    baseline_features = {
        baseline_id: image_features(base_images[baseline_id], orb)
        for ids in anchor_pool.values() for baseline_id in ids
    }
    baseline = o3d.io.read_point_cloud(str(args.baseline_preview))
    base_xyz = np.asarray(baseline.points, np.float64)
    base_rgb = np.asarray(baseline.colors, np.float64)
    # 原视频 registered_pcds 与对应图像逐像素同位，先走正式 alignment
    # 变到米制 RoomFrame，作为跨视频匹配的3D目标。旧实现用 Gaussian
    # camera 把 preview 反投影；两套 camera/preview 坐标并非同一阶段，
    # 会把清晰地面错误投到 1m 以上墙面高度。
    anchor_ids = sorted({bid for ids in anchor_pool.values() for bid in ids})
    baseline_metric_maps = {
        bid: metric(np.asarray(base_points[bid], np.float64).reshape(-1, 3), alignment)
        .reshape(base_points.shape[1], base_points.shape[2], 3)
        for bid in anchor_ids
    }

    # 每段补拍的 registered_pcds 共用同一坐标系，因此只能采用一个稳定的
    # 相似变换。逐帧单独变换会把同一平面拉成双层或错位。先从全部可匹配
    # 帧中选变换 medoid，再用它处理整段补拍。
    direct_candidates: dict[int, list[tuple[int, tuple, dict]]] = {clip: [] for clip in active_clips}
    for item in mapping:
        if item["kind"] != "supplement":
            continue
        frame_id, clip = int(item["output_index"]), int(item["clip_id"])
        direct = direct_frame_transform(
            frame_id, clip, new_images, base_images, new_points, baseline_metric_maps, base_conf,
            new_conf, baseline_features, anchor_pool, orb, matcher, metric_scale,
        )
        if direct is not None:
            scale, rotation, translation, frame_metric = direct
            direct_candidates[clip].append((frame_id, (scale, rotation, translation), frame_metric))

    clip_transforms: dict[int, tuple] = {}
    direct_transforms_by_frame: dict[int, tuple] = {}
    transform_consensus: dict[int, dict] = {}
    probe = np.asarray([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
    for clip, candidates in direct_candidates.items():
        if not candidates:
            continue
        if clip == 3:
            # 这段轨迹的主要错误是高度漂移；门口平面 X/Y 仍保持连续。
            # 因此用候选在XY上的 medoid 约束整段，Z随后由每帧真实地砖
            # 主平面做刚性平移。不能用包含错误Z的3D medoid，否则会选到墙顶。
            signatures_xy = np.stack([
                (scale * (probe @ rotation.T) + translation)[:, :2].reshape(-1)
                for _, (scale, rotation, translation), _ in candidates
            ])
            distances_xy = np.linalg.norm(signatures_xy[:, None, :] - signatures_xy[None, :, :], axis=2)
            medoid = int(np.argmin(np.median(distances_xy, axis=1)))
            chosen = candidates[medoid]
            clip_transforms[clip] = chosen[1]
            transform_consensus[clip] = {
                "candidate_frames": len(candidates),
                "mode": "xy_medoid_plus_per_frame_rigid_floor_height",
                "medoid_frame": int(chosen[0]),
                "median_xy_signature_distance_m": float(np.median(distances_xy[medoid])),
            }
            continue
        signatures = np.stack([
            (scale * (probe @ rotation.T) + translation).reshape(-1) * metric_scale
            for _, (scale, rotation, translation), _ in candidates
        ])
        distances = np.linalg.norm(signatures[:, None, :] - signatures[None, :, :], axis=2)
        medoid = int(np.argmin(np.median(distances, axis=1)))
        radius = distances[medoid]
        cutoff = max(float(np.quantile(radius, 0.45)), 0.06)
        support = radius <= cutoff
        chosen = candidates[medoid]
        clip_transforms[clip] = chosen[1]
        transform_consensus[clip] = {
            "candidate_frames": len(candidates), "medoid_frame": int(chosen[0]),
            "support_frames": int(np.count_nonzero(support)),
            "median_signature_distance_m": float(np.median(radius)),
        }

    base_tree = cKDTree(base_xyz)
    voxel = args.voxel
    extents = alignment["extents_m"]
    origin = np.asarray([extents["x"][0] - .06, extents["y"][0] - .06, -.06])
    limit = np.asarray([extents["x"][1] + .06, extents["y"][1] + .06, extents["z"][1] + .02])
    shape = np.ceil((limit - origin) / voxel).astype(np.int64) + 1
    nx, ny = int(shape[0]), int(shape[1])

    per_clip = {clip: empty_records() for clip in active_clips}
    accepted_frames = {clip: 0 for clip in active_clips}
    point_counts = {clip: {"inside_room": 0, "inside_region": 0, "distance_gated": 0} for clip in active_clips}
    observed_bounds = {clip: [np.full(3, np.inf), np.full(3, -np.inf)] for clip in active_clips}
    direct_metrics = {clip: [item[2] for item in direct_candidates[clip]] for clip in active_clips}
    floor_height_offsets: list[float] = []
    window_plane_offsets: list[float] = []
    for item in mapping:
        if item["kind"] != "supplement":
            continue
        frame_id, clip = int(item["output_index"]), int(item["clip_id"])
        raw = np.asarray(new_points[frame_id], np.float64).reshape(-1, 3)
        conf = np.asarray(new_conf[frame_id], np.float32).reshape(-1)
        rgb = np.asarray(new_images[frame_id], np.float32).reshape(-1, 3)
        valid = np.isfinite(raw).all(1) & np.isfinite(conf) & (conf >= args.min_confidence)
        raw, conf, rgb = raw[valid], conf[valid], rgb[valid]
        direct = clip_transforms.get(clip)
        if direct is None:
            continue
        scale, rotation, translation = direct
        xyz = scale * (raw @ rotation.T) + translation
        if clip == 2:
            # 窗户补拍的局部轨迹把垂直窗墙转到了近水平面。真实点间
            # 几何仍连续：保持X和点间距离，做一次刚性轴交换，把主平面
            # 复位到已核验的 y=1.3368m 窗墙；中央玻璃随后由白名单排除。
            plane_source = xyz[
                (xyz[:, 0] >= -.20) & (xyz[:, 0] <= 2.10)
                & (xyz[:, 1] >= -.70) & (xyz[:, 1] <= .80)
                & np.isfinite(xyz[:, 2]), 2
            ]
            if len(plane_source) < 500:
                continue
            bins = np.floor(plane_source / .01).astype(np.int64)
            values, counts = np.unique(bins, return_counts=True)
            mode = float(values[int(np.argmax(counts))] * .01 + .005)
            band = plane_source[np.abs(plane_source - mode) <= .025]
            if len(band) < 300:
                continue
            plane_z = float(np.median(band))
            old_y = xyz[:, 1].copy()
            xyz[:, 0] -= .31
            xyz[:, 1] = xyz[:, 2] - plane_z + 1.3368
            xyz[:, 2] = old_y + 1.38
            window_plane_offsets.append(plane_z)
        if clip == 3:
            # 门口补拍在 SLAM3R 的局部轨迹中发生了近似纯高度跳变：X/Y
            # 已落在门口，地砖主体却整体位于约2.5m。对每个已通过视觉+3D
            # 残差验证的帧，取门口XY范围内占比最高的1cm高度层作为真实
            # 地砖平面，只做刚性Z平移；不投影、不插值、不改变点间几何。
            floor_xy = (
                (xyz[:, 0] >= -0.55) & (xyz[:, 0] <= 0.62)
                & (xyz[:, 1] >= 0.48) & (xyz[:, 1] <= 1.42)
                & np.isfinite(xyz[:, 2])
            )
            floor_z = xyz[floor_xy, 2]
            if len(floor_z) < 500:
                continue
            bins = np.floor(floor_z / 0.01).astype(np.int64)
            values, counts = np.unique(bins, return_counts=True)
            mode_z = float(values[int(np.argmax(counts))] * 0.01 + 0.005)
            band = floor_z[np.abs(floor_z - mode_z) <= 0.025]
            if len(band) < 300:
                continue
            height_offset = float(np.median(band))
            xyz[:, 2] -= height_offset
            floor_height_offsets.append(height_offset)
        room_mask = np.all((xyz >= origin) & (xyz <= limit), axis=1)
        if np.any(room_mask):
            observed_bounds[clip][0] = np.minimum(observed_bounds[clip][0], xyz[room_mask].min(axis=0))
            observed_bounds[clip][1] = np.maximum(observed_bounds[clip][1], xyz[room_mask].max(axis=0))
        region_mask = in_region(xyz, clip)
        point_counts[clip]["inside_room"] += int(np.count_nonzero(room_mask))
        point_counts[clip]["inside_region"] += int(np.count_nonzero(room_mask & region_mask))
        mask = room_mask & region_mask
        xyz, conf, rgb = xyz[mask], conf[mask], rgb[mask]
        if len(xyz) < 80:
            continue
        distance, _ = base_tree.query(xyz, workers=-1)
        # 只补真实缺口；离旧场景过远通常是漂移，过近则只是重复加厚。
        add = (distance >= 0.012) & (distance <= 0.18)
        point_counts[clip]["distance_gated"] += int(np.count_nonzero(add))
        xyz, conf, rgb = xyz[add], conf[add], rgb[add]
        if len(xyz) < 50:
            continue
        ijk = np.floor((xyz - origin) / voxel).astype(np.int64)
        hashes = ijk[:, 0] + nx * (ijk[:, 1] + ny * ijk[:, 2])
        order = np.lexsort((-conf, hashes))
        sorted_hash = hashes[order]
        first = np.r_[0, np.flatnonzero(sorted_hash[1:] != sorted_hash[:-1]) + 1]
        chosen = order[first]
        hashes, xyz, conf, rgb = hashes[chosen], xyz[chosen], conf[chosen], rgb[chosen]
        weight = np.log1p(np.maximum(conf, 0)).astype(np.float64)
        frame_records = (
            hashes, xyz * weight[:, None], rgb.astype(np.float64) * weight[:, None], weight,
            np.ones(len(hashes), np.int32), conf,
        )
        per_clip[clip] = merge_records(per_clip[clip], frame_records)
        accepted_frames[clip] += 1

    additions_all, colors_all, report = [], [], {}
    for clip in active_clips:
        _, xyz_sum, rgb_sum, weight_sum, frame_count, max_conf = per_clip[clip]
        keep = (frame_count >= args.min_frames) & (max_conf >= args.min_confidence)
        xyz = xyz_sum[keep] / weight_sum[keep, None]
        colors = np.clip(rgb_sum[keep] / weight_sum[keep, None], 0, 255) / 255.0
        support = frame_count[keep]
        if len(xyz):
            cloud = o3d.geometry.PointCloud()
            cloud.points = o3d.utility.Vector3dVector(xyz)
            cloud.colors = o3d.utility.Vector3dVector(colors)
            _, ids = cloud.remove_radius_outlier(nb_points=3, radius=0.028)
            ids = np.asarray(ids, np.int64)
            xyz, colors, support = xyz[ids], colors[ids], support[ids]
        additions_all.append(xyz); colors_all.append(colors)
        report[str(clip)] = {
            "accepted_frames": accepted_frames[clip], "additions": int(len(xyz)),
            "median_support_frames": float(np.median(support)) if len(support) else 0.0,
            **point_counts[clip],
            "directly_registered_frames": len(direct_metrics[clip]),
            "median_direct_registration_error_m": float(np.median([m["median_error_m"] for m in direct_metrics[clip]]))
            if direct_metrics[clip] else None,
            "observed_room_bounds": [observed_bounds[clip][0].tolist(), observed_bounds[clip][1].tolist()]
            if np.isfinite(observed_bounds[clip][0]).all() else None,
        }

    additions = np.concatenate(additions_all) if additions_all else np.empty((0, 3))
    colors = np.concatenate(colors_all) if colors_all else np.empty((0, 3))
    combined = o3d.geometry.PointCloud()
    combined.points = o3d.utility.Vector3dVector(np.vstack([base_xyz, additions]))
    combined.colors = o3d.utility.Vector3dVector(np.vstack([base_rgb, colors]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), combined, write_ascii=False, compressed=False)
    diagnostics = {
        "status": "candidate_only", "method": "four_clip_four_region_additive_multiview_fusion",
        "baseline": str(args.baseline_preview.resolve()), "base_points_preserved": int(len(base_xyz)),
        "anchor_metrics": anchor_metrics, "per_clip": report,
        "transform_consensus": {str(k): v for k, v in transform_consensus.items()},
        "floor_height_offsets_m": {
            "count": len(floor_height_offsets),
            "median": float(np.median(floor_height_offsets)) if floor_height_offsets else None,
            "p10_p90": [float(np.quantile(floor_height_offsets, 0.1)), float(np.quantile(floor_height_offsets, 0.9))]
            if floor_height_offsets else None,
        },
        "window_plane_offsets_m": {
            "count": len(window_plane_offsets),
            "median": float(np.median(window_plane_offsets)) if window_plane_offsets else None,
        },
        "total_additions": int(len(additions)), "output_points": int(len(base_xyz) + len(additions)),
        "official_preview_changed": False, "visual_qa_required": True,
    }
    args.output.with_suffix(".json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    return diagnostics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline_work", type=Path)
    parser.add_argument("candidate_root", type=Path)
    parser.add_argument("baseline_preview", type=Path)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--voxel", type=float, default=0.008)
    parser.add_argument("--min-confidence", type=float, default=12.0)
    parser.add_argument("--min-frames", type=int, default=4)
    main(parser.parse_args())
