"""SFM：pycolmap 从图片集恢复相机位姿与稀疏点云。"""
import logging
import math
import shutil
import time
from pathlib import Path

import numpy as np

OUTPUT_DIR = "sfm"
MAX_EXHAUSTIVE_IMAGES = 240
SIFT_MAX_NUM_FEATURES = 12_000
SIFT_PEAK_THRESHOLD = 0.004
LONG_RANGE_MAX_ANCHORS = 60
LONG_RANGE_CANDIDATES_PER_ANCHOR = 3
LONG_RANGE_MAX_PAIRS = 600
LONG_RANGE_MIN_GOOD_MATCHES = 12
LONG_RANGE_ORB_FEATURES = 400
LONG_RANGE_THUMBNAIL_SIZE = 320
REFINE_MAX_REPROJ_ERROR = 2.0
REFINE_MAX_POINTS_FOR_BA = 500_000
logger = logging.getLogger("anjing.pipeline")


def undistort_registered_view(image: np.ndarray, camera: dict) -> tuple[np.ndarray, dict]:
    """把 COLMAP 径向畸变图转换为针孔图，并保留同步内参。

    当前视频统一使用 SIMPLE_RADIAL。保持输出尺寸和 K 不变可避免裁剪视野，
    同时让训练、语义投影和浏览器预览共享同一套针孔坐标约定。
    """
    model = str(camera.get("camera_model", "PINHOLE")).upper()
    radial = np.asarray(camera.get("radial_distortion", []), dtype=np.float64)
    result_camera = dict(camera)
    result_camera["K"] = np.asarray(camera["K"], dtype=np.float64).copy()
    if model not in {"SIMPLE_RADIAL", "RADIAL"} or radial.size == 0 or not np.any(radial):
        result_camera["undistorted"] = model in {"PINHOLE", "SIMPLE_PINHOLE"}
        return np.asarray(image).copy(), result_camera

    import cv2

    coefficients = np.zeros(5, dtype=np.float64)
    coefficients[0] = radial[0]
    if radial.size > 1:
        coefficients[1] = radial[1]
    rectified = cv2.undistort(
        np.asarray(image), result_camera["K"], coefficients, None, result_camera["K"]
    )
    result_camera["source_camera_model"] = model
    result_camera["source_radial_distortion"] = radial.copy()
    result_camera["camera_model"] = "PINHOLE"
    result_camera["radial_distortion"] = np.zeros_like(radial)
    result_camera["undistorted"] = True
    return rectified, result_camera


def build_synthetic_cameras(n: int = 12, radius: float = 2.0, height: float = 1.5):
    """合成绕圈相机位姿（用于测试与调试）。返回 [{center, R, K}]。"""
    cams = []
    for i in range(n):
        theta = 2 * np.pi * i / n
        center = np.array([radius * np.cos(theta), radius * np.sin(theta), height])
        forward = -center.copy()
        forward[2] = 0.0
        forward /= np.linalg.norm(forward)
        up = np.array([0.0, 0.0, 1.0])
        right = np.cross(forward, up)
        # 针孔约定：x 右、y 下、z 前（光轴=forward 指向圆心）；列 = [x_cam, y_cam, z_cam]
        R = np.stack([right, -up, forward], axis=1)
        K = np.array([[600.0, 0, 320], [0, 600, 240], [0, 0, 1.0]])
        cams.append({"center": center, "R": R, "K": K})
    return cams


def run_sfm(image_dir: Path, work_dir: Path) -> dict:
    """对 image_dir 内 jpg 运行增量式 SFM。

    返回 {"cameras": [...], "points3D": np.ndarray (N,3), "model_path": Path}。
    相机坐标系为 COLMAP 约定（camera-to-world 的逆）。
    """
    image_dir = Path(image_dir)
    if not image_dir.exists() or not list(image_dir.glob("*.jpg")):
        raise FileNotFoundError(f"图片目录不存在或没有 jpg: {image_dir}")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "database.db"
    model_path = work_dir / OUTPUT_DIR
    for p in (db_path, model_path):
        if p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    import pycolmap

    # 限制 SIFT 线程与输入尺寸：Windows 上默认 24 线程提取 1080p 大图
    # 易触发 pycolmap C++ 访问冲突（0xc0000005）拖垮整个 solo worker
    options = pycolmap.FeatureExtractionOptions()
    options.num_threads = 8
    options.max_image_size = 1600
    options.sift.max_num_features = SIFT_MAX_NUM_FEATURES
    options.sift.peak_threshold = SIFT_PEAK_THRESHOLD
    # 指定相机内参：视频帧无 EXIF 焦距，默认 1.2×max(w,h) 对手机广角视频偏长焦，
    # 几何验证不会产生 CALIBRATED 对，初始图像对永远找不到（No good initial pair）。
    # 按广角先验取 0.75×max(w,h)，后续 A4 标定/门高先验会修正尺度。
    sample = next(iter(image_dir.glob("*.jpg")))
    import cv2
    probe = cv2.imread(str(sample))
    reader = None
    if probe is not None:
        h, w = probe.shape[:2]
        reader = pycolmap.ImageReaderOptions()
        reader.camera_model = "SIMPLE_RADIAL"
        reader.camera_params = f"{0.75 * max(w, h):.1f},{w / 2:.1f},{h / 2:.1f},0"
    if reader is None:
        # 无法读取参考帧（异常输入/测试环境）：回退默认，由 pycolmap 自行估计内参
        pycolmap.extract_features(db_path, str(image_dir), extraction_options=options)
    else:
        pycolmap.extract_features(
            db_path, str(image_dir),
            # 同一视频的所有帧来自同一个手机镜头，必须共享相机内参；AUTO 会在
            # 无 EXIF 的抽帧图片上为每帧创建独立 Camera，导致位姿恢复严重不稳。
            camera_mode=pycolmap.CameraMode.SINGLE,
            reader_options=reader, extraction_options=options,
        )
    image_count = len(list(image_dir.glob("*.jpg")))
    matching = pycolmap.FeatureMatchingOptions()
    matching.guided_matching = True
    # 输入来自按时间排序的单段视频。全量两两匹配会随帧数平方增长，并容易让
    # 室内重复纹理/窗外画面产生远距离误匹配。先覆盖相邻 15 帧；若轨迹仍被
    # 分成多个局部模型，再扩大到 30 帧重试，避免所有视频都承担高匹配开销。
    # quadratic_overlap 额外连接较远邻帧，既保留连续轨迹又控制错误匹配。
    pairing = pycolmap.SequentialPairingOptions()
    pairing.overlap = 15
    pairing.quadratic_overlap = True
    pairing.num_threads = 8
    pycolmap.match_sequential(
        db_path,
        matching_options=matching,
        pairing_options=pairing,
    )
    long_range_pairs = _build_long_range_pairs(image_dir)
    if long_range_pairs:
        pair_list_path = work_dir / "long_range_pairs.txt"
        pair_list_path.write_text(
            "".join(f"{left} {right}\n" for left, right in long_range_pairs),
            encoding="utf-8",
        )
        imported = pycolmap.ImportedPairingOptions()
        imported.match_list_path = pair_list_path
        pycolmap.match_image_pairs(
            db_path,
            matching_options=matching,
            pairing_options=imported,
        )
    logger.info(
        "sfm_matching image_count=%d sift_max_num_features=%d sift_peak_threshold=%.4f "
        "guided_matching=%s sequential_overlap=%d long_range_pairs=%d",
        image_count,
        SIFT_MAX_NUM_FEATURES,
        SIFT_PEAK_THRESHOLD,
        matching.guided_matching,
        pairing.overlap,
        len(long_range_pairs),
    )
    maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    initial_models = (
        [model for model in maps.values() if model is not None]
        if isinstance(maps, dict)
        else list(maps) if isinstance(maps, (list, tuple)) else []
    )
    if len(initial_models) > 1:
        shutil.rmtree(model_path, ignore_errors=True)
        pairing.overlap = 30
        pycolmap.match_sequential(
            db_path,
            matching_options=matching,
            pairing_options=pairing,
        )
        maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    # 两轮顺序匹配仍可能在快速转向或短暂模糊处把同一房间切成多个局部模型。
    # 不能因为最大局部模型碰巧存在就直接宣告失败：仅当主模型注册率不足质量
    # 门槛时，小数据集追加全量匹配；高密度抽帧可能超过 240 张，此时全量匹配
    # 会平方级增长，因此改为扩大顺序窗口，避免为提高视角密度而耗尽内存/磁盘。
    models_after_sequential = _reconstructions(maps)
    component_sizes = sorted(
        (len(model.images) for model in models_after_sequential), reverse=True
    )
    best_registered = component_sizes[0] if component_sizes else 0
    # 多 component 不代表应该自动做 O(N²) 全量匹配，更不能把独立坐标系直接拼接。
    # 主轨迹已覆盖至少 70% 时保留主 component，并把断裂写入质量指标；只有主轨迹
    # 本身不足时才承担 exhaustive 兜底成本。
    exhaustive_fallback_used = best_registered / max(image_count, 1) < 0.70
    if exhaustive_fallback_used:
        shutil.rmtree(model_path, ignore_errors=True)
        if image_count <= MAX_EXHAUSTIVE_IMAGES:
            logger.info("sfm_fallback mode=exhaustive image_count=%d", image_count)
            pycolmap.match_exhaustive(db_path, matching_options=matching)
        else:
            pairing.overlap = 60
            logger.info(
                "sfm_fallback mode=expanded_sequential image_count=%d overlap=%d",
                image_count,
                pairing.overlap,
            )
            pycolmap.match_sequential(
                db_path,
                matching_options=matching,
                pairing_options=pairing,
            )
        maps = pycolmap.incremental_mapping(db_path, str(image_dir), str(model_path))
    # 4.x 返回 {model_index: Reconstruction}（失败为空 dict）；
    # 多模型时取注册帧最多的主模型；兼容旧版本返回列表/元组或 None
    recons = _reconstructions(maps)
    recon = max(recons, key=lambda r: len(r.images)) if recons else None
    if recon is None:
        raise RuntimeError("SFM 失败：无法恢复相机位姿（图片过少或纹理不足）")
    # 长视频快速转向/短暂模糊处可能把同一房间切成多个局部模型：主模型
    # （注册帧最多）直接作为后续管线输入，小分量不合并、不拼接（独立坐标系），
    # 只通过 quality.component_registered_images 暴露给质量门禁判断。
    component_sizes = sorted((len(model.images) for model in recons), reverse=True)
    logger.info(
        "sfm_model_selection component_count=%d component_sizes=%s "
        "main_component_images=%d main_component_points=%d",
        len(recons),
        component_sizes,
        len(recon.images),
        len(recon.points3D),
    )
    refine_diagnostics = _refine_reconstruction(recon)
    cameras, points, colors, errors, track_lengths = [], [], [], [], []
    for img_id in recon.images:
        img = recon.images[img_id]
        cam = recon.cameras[img.camera_id]
        pose = img.cam_from_world()  # pycolmap 4.x: 方法，返回 Rigid3d（世界→相机）
        R = np.asarray(pose.rotation.matrix(), dtype=np.float64)  # 3x3
        t = np.asarray(pose.translation, dtype=np.float64).reshape(3)
        c = -R.T @ t
        fx = cam.focal_length_x
        fy = cam.focal_length_y
        cx = cam.principal_point_x
        cy = cam.principal_point_y
        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])
        model_name = getattr(getattr(cam, "model", None), "name", None) or str(
            getattr(cam, "model_name", "PINHOLE")
        )
        params = np.asarray(getattr(cam, "params", []), dtype=np.float64)
        radial = np.zeros(0, dtype=np.float64)
        if model_name == "SIMPLE_RADIAL" and params.size >= 4:
            radial = params[3:4]
        elif model_name == "RADIAL" and params.size >= 5:
            radial = params[3:5]
        cameras.append({
            "name": img.name,
            "R": R,
            "t": t,
            "K": K,
            "center": c,
            "camera_model": model_name,
            "camera_params": params,
            "radial_distortion": radial,
            "image_size": [int(getattr(cam, "width", 0)), int(getattr(cam, "height", 0))],
            "undistorted": model_name in {"PINHOLE", "SIMPLE_PINHOLE"},
        })
    for pid in recon.points3D:
        point = recon.points3D[pid]
        points.append(point.xyz)
        colors.append(getattr(point, "color", [128, 128, 128]))
        errors.append(float(getattr(point, "error", np.nan)))
        track = getattr(point, "track", None)
        track_lengths.append(len(track.elements) if track is not None and hasattr(track, "elements") else 0)
    finite_errors = np.asarray(errors, dtype=np.float64)
    finite_errors = finite_errors[np.isfinite(finite_errors)]
    return {
        "cameras": cameras,
        "points3D": np.asarray(points, dtype=np.float64).reshape(-1, 3),
        "colors3D": np.asarray(colors, dtype=np.uint8).reshape(-1, 3),
        "quality": {
            "registered_images": len(cameras),
            "points3D": len(points),
            "median_reprojection_error": float(np.median(finite_errors)) if len(finite_errors) else None,
            "mean_reprojection_error": float(np.mean(finite_errors)) if len(finite_errors) else None,
            "mean_track_length": float(np.mean(track_lengths)) if track_lengths else 0.0,
            "component_count": len(recons),
            "component_registered_images": sorted(
                (len(model.images) for model in recons), reverse=True
            ),
            "exhaustive_fallback_used": exhaustive_fallback_used,
            "refinement": refine_diagnostics,
        },
        "model_path": model_path,
    }


def filter_trajectory_jumps(
    cameras: list[dict],
    *,
    jump_factor: float = 4.0,
    min_kept_ratio: float = 0.70,
) -> tuple[list[dict], list[str], dict]:
    """按时间序剔除位姿跳变帧（快速甩动/遮挡段），避免污染 3DGS 训练与测量。

    帧名按字典序（frame_00001.jpg ...）视为拍摄时间序。对每一帧，若进入或
    离开它的相邻步长超过 ``jump_factor`` × 中位步长，则视为跳变段成员并剔除；
    被标记的相邻帧合并为连续段。剔除后若剩余帧少于 ``min_kept_ratio``，只剔除
    最极端（步长 > 2× 阈值）的帧，把保护留给质量门禁。

    返回 (保留相机列表, 被剔除帧名列表, 诊断指标)。
    """
    ordered = sorted(cameras, key=lambda item: item.get("name", ""))
    if len(ordered) < 3:
        return list(cameras), [], {
            "dropped_count": 0, "jump_threshold_units": None, "median_step_units": None,
        }
    centers = np.asarray([c["center"] for c in ordered], dtype=np.float64)
    steps = np.linalg.norm(np.diff(centers, axis=0), axis=1)
    median_step = float(np.median(steps))
    threshold = max(jump_factor * median_step, 1e-9)
    # 进入或离开帧的步长异常 → 标记；相邻标记帧合并为连续跳变段。
    flagged = np.zeros(len(ordered), dtype=bool)
    for index in range(len(ordered)):
        inbound = steps[index - 1] if index > 0 else 0.0
        outbound = steps[index] if index < len(steps) else 0.0
        flagged[index] = max(inbound, outbound) > threshold
    dropped_names = [item["name"] for item, flag in zip(ordered, flagged) if flag]
    kept = [item for item, flag in zip(ordered, flagged) if not flag]
    if len(kept) < max(3, int(len(ordered) * min_kept_ratio)):
        # 轨迹整体异常：只剔除最极端的帧，避免把整段视频删空。
        extreme_threshold = max(2.0 * threshold, float(np.median(steps)) * 2.0)
        dropped_names = [
            item["name"]
            for index, item in enumerate(ordered)
            if max(steps[index - 1] if index > 0 else 0.0,
                   steps[index] if index < len(steps) else 0.0) > extreme_threshold
        ]
        kept = [item for item in ordered if item["name"] not in set(dropped_names)]
    diagnostics = {
        "dropped_count": len(dropped_names),
        "jump_threshold_units": float(threshold),
        "median_step_units": median_step,
        "kept_ratio": round(len(kept) / max(len(ordered), 1), 4),
    }
    if dropped_names:
        logger.info(
            "trajectory_jump_filter dropped=%d kept=%d threshold=%.4f median_step=%.4f",
            len(dropped_names), len(kept), threshold, median_step,
        )
    return kept, dropped_names, diagnostics


def _refine_reconstruction(
    recon,
    *,
    max_reproj_error: float = REFINE_MAX_REPROJ_ERROR,
    max_points_for_ba: int = REFINE_MAX_POINTS_FOR_BA,
) -> dict:
    """剔除高重投影误差点并做全局 BA；任何一步失败都降级，不抛异常。

    高误差点多来自动态遮挡/镜面反射，进入 3DGS 初始化后会变成漂浮高斯源头。
    BA 在误差点剔除后联合重优化位姿与三维点，压住长视频累积的轨迹漂移。
    """
    import pycolmap

    diagnostics = {
        "filtered_points": 0,
        "bundle_adjustment": False,
        "ba_seconds": 0.0,
        "degraded_reason": None,
    }
    try:
        bad = [
            pid for pid, point in recon.points3D.items()
            if getattr(point, "error", 0.0) > max_reproj_error
        ]
        for pid in bad:
            recon.delete_point3D(pid)
        diagnostics["filtered_points"] = len(bad)
    except Exception as exc:  # noqa: BLE001 - 精修失败不阻断整条管线
        diagnostics["degraded_reason"] = f"point_filter: {exc}"
        return diagnostics
    # 被删点的观测残留在各图像 points2D 中；置为无效，避免 BA 引用悬空点。
    try:
        for image in recon.images.values():
            cleaned = []
            for p2d in image.points2D:
                if p2d.point3D_id in recon.points3D:
                    cleaned.append(p2d)
                else:
                    cleaned.append(
                        pycolmap.Point2D(
                            xy=np.asarray(p2d.xy, dtype=np.float64).reshape(2, 1),
                            point3D_id=pycolmap.INVALID_POINT3D_ID,
                        )
                    )
            image.points2D.clear()
            image.points2D = cleaned
    except Exception as exc:  # noqa: BLE001
        diagnostics["degraded_reason"] = f"observation_cleanup: {exc}"
        return diagnostics
    if len(recon.points3D) <= max_points_for_ba:
        try:
            started = time.perf_counter()
            pycolmap.bundle_adjustment(recon, pycolmap.BundleAdjustmentOptions())
            diagnostics["bundle_adjustment"] = True
            diagnostics["ba_seconds"] = round(time.perf_counter() - started, 3)
        except Exception as exc:  # noqa: BLE001
            diagnostics["degraded_reason"] = f"bundle_adjustment: {exc}"
    else:
        diagnostics["degraded_reason"] = "model_too_large_for_ba"
    logger.info(
        "sfm_refinement filtered_points=%d bundle_adjustment=%s ba_seconds=%.1f reason=%s",
        diagnostics["filtered_points"],
        diagnostics["bundle_adjustment"],
        diagnostics["ba_seconds"],
        diagnostics.get("degraded_reason"),
    )
    return diagnostics


def _reconstructions(maps) -> list:
    """兼容 pycolmap 不同版本的 mapping 返回形态，并过滤空模型。"""
    if isinstance(maps, dict):
        return [model for model in maps.values() if model is not None]
    if isinstance(maps, (list, tuple)):
        return [model for model in maps if model is not None]
    return [maps] if maps is not None else []


def _build_long_range_pairs(
    image_dir: Path,
    *,
    max_anchors: int = LONG_RANGE_MAX_ANCHORS,
    candidates_per_anchor: int = LONG_RANGE_CANDIDATES_PER_ANCHOR,
    max_pairs: int = LONG_RANGE_MAX_PAIRS,
    loop_window: int = 12,
) -> list[tuple[str, str]]:
    """用轻量 ORB 相似度提出有限非邻接候选，最终连接仍由 COLMAP 验证。

    环绕拍摄的视频首尾画面应重叠（闭环）：首尾窗口内的配对被强制加入候选，
    不依赖 ORB 分数，为 COLMAP 提供闭环约束以对抗长轨迹尺度漂移。配对是否
    成立仍由几何验证决定，无重叠时只会多付出少量匹配时间。
    """
    paths = sorted(Path(image_dir).glob("*.jpg"))
    if len(paths) < 4 or max_anchors < 2 or candidates_per_anchor < 1 or max_pairs < 1:
        return []
    anchor_stride = max(1, math.ceil(len(paths) / max_anchors))
    anchor_indices = list(range(0, len(paths), anchor_stride))
    if anchor_indices[-1] != len(paths) - 1:
        anchor_indices.append(len(paths) - 1)
    signatures = {
        index: _orb_signature(paths[index])
        for index in anchor_indices
    }
    min_separation = max(15, len(paths) // 20)
    proposals: dict[int, list[tuple[int, int]]] = {index: [] for index in anchor_indices}
    for left_offset, left_index in enumerate(anchor_indices):
        left_descriptor = signatures[left_index]
        if left_descriptor is None:
            continue
        for right_index in anchor_indices[left_offset + 1:]:
            if right_index - left_index < min_separation:
                continue
            right_descriptor = signatures[right_index]
            if right_descriptor is None:
                continue
            score = _orb_match_score(left_descriptor, right_descriptor)
            if score < LONG_RANGE_MIN_GOOD_MATCHES:
                continue
            proposals[left_index].append((score, right_index))
            proposals[right_index].append((score, left_index))

    selected: set[tuple[int, int]] = set()
    for anchor_index, candidates in proposals.items():
        for _score, other_index in sorted(candidates, reverse=True)[:candidates_per_anchor]:
            selected.add(tuple(sorted((anchor_index, other_index))))
    # 首尾闭环强制配对：优先占用 max_pairs 预算，保证闭环候选不被 ORB 分数挤掉。
    # 窗口随视频长度自适应，避免短视频被大量无效首尾对占满预算。
    effective_window = min(loop_window, max(2, len(paths) // 20)) if loop_window > 0 else 0
    if effective_window > 0 and len(paths) > 2 * effective_window:
        head = list(range(effective_window))
        tail = list(range(len(paths) - effective_window, len(paths)))
        forced = sorted({
            tuple(sorted((left, right)))
            for left in head
            for right in tail
            if left != right
        })[:max_pairs]
        scored = sorted(
            selected - set(forced),
            key=lambda pair: (
                -_orb_match_score(signatures[pair[0]], signatures[pair[1]]),
                pair,
            ),
        )[: max(0, max_pairs - len(forced))]
        ranked = scored + sorted(forced)
    else:
        forced = set()
        ranked = sorted(
            selected,
            key=lambda pair: (
                -_orb_match_score(signatures[pair[0]], signatures[pair[1]]),
                pair,
            ),
        )[:max_pairs]
    if forced:
        logger.info(
            "long_range_loop_pairs forced=%d candidates=%d", len(forced), len(ranked)
        )
    return [(paths[left].name, paths[right].name) for left, right in ranked]


def _orb_signature(path: Path) -> np.ndarray | None:
    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    height, width = image.shape[:2]
    scale = min(1.0, LONG_RANGE_THUMBNAIL_SIZE / max(height, width))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    detector = cv2.ORB_create(nfeatures=LONG_RANGE_ORB_FEATURES)
    _keypoints, descriptors = detector.detectAndCompute(image, None)
    return descriptors


def _orb_match_score(left: np.ndarray, right: np.ndarray) -> int:
    import cv2

    if left is None or right is None or len(left) < 2 or len(right) < 2:
        return 0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(left, right, k=2)
    return sum(
        1
        for pair in matches
        if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
    )
