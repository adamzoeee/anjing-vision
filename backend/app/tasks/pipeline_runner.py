"""整条管道编排：抽帧→SFM→训练→导出→标定→分割→几何→评分→报告。

每个阶段更新 Scan.status/progress；失败置 failed 并记录 message。
"""
import logging
import os
from pathlib import Path

import numpy as np
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import SessionLocal
from ..models import Report, Scan
from ..storage import media_path

logger = logging.getLogger("anjing.pipeline")
DEFAULT_MAX_TRAINING_VIEWS = 120  # 8GB 显存下约 1.3GB Float32 训练图像，给高斯优化保留余量
MAX_CONFIGURED_TRAINING_VIEWS = 240

STAGES = [
    ("extracting", 5, "抽帧中"), ("sfm", 25, "相机位姿估计中"),
    ("training", 45, "3D 重建训练中"), ("calibrating", 65, "尺度标定中"),
    ("segmenting", 75, "语义分割中"), ("analyzing", 85, "几何分析中"),
    ("scoring", 90, "风险评分中"), ("reporting", 95, "生成报告中"),
]


def _step_measurements(step: float) -> dict:
    """保留“确认未检测到门槛”的 0.0；台阶高度不再重复作为门槛评分。"""
    return {
        "threshold_m": step if 0.0 <= step < 0.3 else None,
        "stairs_exist": step >= 0.3,
    }


def run_pipeline(scan_id: int) -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        scan = db.get(Scan, scan_id)
        if scan is None:
            return
        work = Path(s.data_dir) / "work" / str(scan_id)
        work.mkdir(parents=True, exist_ok=True)
        src = media_path(scan.media_path)
        frames = work / "frames"
        _stage(db, scan, "extracting", 5, "抽帧中")

        from pipeline.frame_extractor import (
            extract_frames,
            filter_sharp_frames,
            protect_sfm_continuity,
        )
        if src.is_dir():
            # 照片模式：目录下的图片直接作为帧
            all_frames = sorted(list(src.glob("*.jpg")) + list(src.glob("*.jpeg")) + list(src.glob("*.JPG")))
        else:
            all_frames = extract_frames(src, frames)
        sharp_frames, dropped = filter_sharp_frames(all_frames)
        sfm_frames, bridge_frames, continuity = protect_sfm_continuity(
            all_frames,
            sharp_frames,
        )
        logger.info(
            "frame_filter scan_id=%s candidate_frames=%d sharp_frames=%d blurred_frames=%d "
            "sfm_bridge_frames_restored=%d sfm_input_frames=%d "
            "max_dropped_run_before_recovery=%d max_dropped_run_after_recovery=%d",
            scan_id,
            len(all_frames),
            len(sharp_frames),
            len(dropped),
            len(bridge_frames),
            len(sfm_frames),
            continuity["max_dropped_run_before_recovery"],
            continuity["max_dropped_run_after_recovery"],
        )
        if len(sfm_frames) < 30:
            _fail(db, scan, f"抽帧后 SfM 输入图片仅 {len(sfm_frames)} 张，请重录（保证光线充足、慢速移动）")
            return

        _stage(db, scan, "sfm", 25, "相机位姿估计中")
        from pipeline.sfm import run_sfm, undistort_registered_view
        # SfM 使用严格清晰帧和少量连续性桥接帧；桥接帧只用于恢复相机轨迹，
        # 后续 3DGS 仍只使用严格清晰且成功注册的帧。
        frames_clean = work / "frames_clean"
        import shutil
        shutil.rmtree(frames_clean, ignore_errors=True)  # 清空重跑残留
        frames_clean.mkdir(parents=True, exist_ok=True)
        for p in sfm_frames:
            shutil.copy(p, frames_clean / p.name)
        sfm_out = run_sfm(frames_clean, work / "sfm")
        from pipeline.quality import assess_sfm
        sfm_quality = assess_sfm(
            sfm_out["cameras"], sfm_out["points3D"], len(sfm_frames), sfm_out.get("quality")
        )
        if not sfm_quality.ok:
            _fail(db, scan, sfm_quality.reason)
            return

        _stage(db, scan, "training", 45, "3D 重建训练中")
        from pipeline.trainer import (
            denormalize_gaussians,
            normalize_scene,
            prepare_tensors,
            train_gaussians,
        )
        from PIL import Image
        # 对齐：相机与图像按文件名排序后一一对应（SFM 可能漏注册部分帧，过滤掉）
        paired = _pair_registered_training_frames(sharp_frames, sfm_out["cameras"])
        logger.info(
            "sfm_registration scan_id=%s sfm_input_frames=%d sharp_frames=%d "
            "sfm_registered_frames=%d training_eligible_frames=%d bridge_frames_excluded=%d",
            scan_id,
            len(sfm_frames),
            len(sharp_frames),
            len(sfm_out["cameras"]),
            len(paired),
            len(bridge_frames),
        )
        if len(paired) < 5:
            _fail(db, scan, "SFM 注册帧过少，无法训练")
            return
        raw_imgs = [np.asarray(Image.open(p).convert("RGB")) for p, _ in paired]
        rectified = [
            undistort_registered_view(image, camera)
            for image, (_, camera) in zip(raw_imgs, paired)
        ]
        imgs = [item[0] for item in rectified]
        cams = [item[1] for item in rectified]
        training_cams, training_imgs = _prepare_training_views(
            cams,
            imgs,
            max_views=_configured_training_view_limit(),
        )
        logger.info(
            "reconstruction_frame_counts scan_id=%s extracted_frames=%d sharp_frames=%d "
            "sfm_input_frames=%d sfm_bridge_frames=%d sfm_registered_frames=%d "
            "training_eligible_frames=%d training_views=%d",
            scan_id,
            len(all_frames),
            len(sharp_frames),
            len(sfm_frames),
            len(bridge_frames),
            len(sfm_out["cameras"]),
            len(paired),
            len(training_imgs),
        )
        normalized_cams, normalized_points, scene_transform = normalize_scene(
            training_cams, sfm_out["points3D"]
        )
        gt = prepare_tensors(normalized_cams, training_imgs)
        init_colors = sfm_out.get("colors3D")
        if init_colors is not None:
            init_colors = np.asarray(init_colors, dtype=np.float32) / 255.0
        gaussians = train_gaussians(gt, normalized_points, init_colors)
        gaussians = denormalize_gaussians(gaussians, scene_transform)
        # 训练结果已经转回 CPU；语义阶段还要同时加载 GroundingDINO 与 SAM。
        # 及时释放整批训练图像张量，避免 8GB 级显卡在模型切换时无谓 OOM。
        del gt
        import torch
        torch.cuda.empty_cache()

        from pipeline.quality import assess_gaussians
        gaussian_quality = assess_gaussians(
            gaussians["means"].numpy(),
            sfm_out["points3D"],
            gaussians.get("training_metrics"),
        )
        if not gaussian_quality.ok:
            _fail(db, scan, gaussian_quality.reason)
            return

        from pipeline.exporter import export_pointcloud, statistical_filter
        import open3d as o3d
        pcd_path = work / "pointcloud.ply"
        export_pointcloud(gaussians, pcd_path)
        pcd = statistical_filter(o3d.io.read_point_cloud(str(pcd_path)))
        o3d.io.write_point_cloud(str(pcd_path), pcd)
        points = np.asarray(pcd.points)
        point_colors = np.asarray(pcd.colors)
        if len(points) < 100:
            _fail(db, scan, "重建点云过少，评估失败")
            return
        # 相机外参仍处于原始 SFM 坐标系；语义 mask 投影必须使用未缩放点云。
        semantic_points = points.copy()

        _stage(db, scan, "segmenting", 65, "语义分割与参考物识别中")
        semantic_result = _find_obstacles(imgs, cams, semantic_points)

        _stage(db, scan, "calibrating", 75, "多参考物尺度标定中")
        from pipeline.calibrator import estimate_scale_from_references
        from pipeline.quality import assess_metric_scene
        scale, calibration_details = estimate_scale_from_references(
            points,
            semantic_result["semantic_point_ids"],
            scan.reference_measurements or [],
        )
        calibration_quality = {"method": "known_objects", **calibration_details}
        calibrated_flag = 0
        if scale is not None:
            metric_quality = assess_metric_scene(points * scale, calibrated=1)
            if metric_quality.ok:
                calibrated_flag = 3
            else:
                calibration_quality["reason"] = metric_quality.reason
                scale = None
        scale = scale or 1.0
        points = points * scale
        if calibrated_flag:
            gaussians["means"] = gaussians["means"] * scale
            gaussians["scales"] = gaussians["scales"] + np.log(scale)

        _stage(db, scan, "analyzing", 85, "几何分析中")
        # COLMAP 世界坐标没有天然“向上”方向，且稀疏点云不能可靠推断自由通道。
        # 在真实地面/墙面空间判定接入前，不能把点云包围盒或最大空隙冒充门宽、通道宽。
        references = scan.reference_measurements or []
        door_w = _known_reference_value(references, "door", "width")
        room_extent = _robust_scene_extents(points) if calibrated_flag else None
        measures = {
            "door_width_m": door_w,
            "passage_width_m": None,
            "threshold_m": None,
            "stairs_exist": None,
            "slope": None,
            "uneven_m": None,
            "scale_status": "metric_references" if calibrated_flag else "relative",
            "calibration_quality": calibration_quality,
            "reference_measurements": references,
            "reconstruction_extent_m": room_extent,
            "geometry_assessment_status": "pending_spatial_validation",
            # 2D 检测不是通道风险。只有后续空间判定确认位于通道内时才填入此字段。
            "obstacles_in_passage": semantic_result["obstacles_in_passage"],
            "obstacle_assessment_status": semantic_result["obstacle_assessment_status"],
            "detected_objects": semantic_result["detected_objects"],
            "semantic_point_counts": semantic_result["semantic_point_counts"],
            "bathroom_door_m": None,
        }

        _stage(db, scan, "scoring", 90, "风险评分中")
        from pipeline.rules import compute_score, evaluate_risks
        risks = evaluate_risks(measures)
        score, detail = compute_score(measures)
        measures["assessment_completeness"] = detail["assessment_completeness"]
        advice = [r["advice"] for r in risks if r["level"] in ("red", "yellow")]

        _stage(db, scan, "reporting", 95, "生成报告中")
        from pipeline.exporter import export_gaussian_ply
        from pipeline.report_builder import build_preview_assets, render_annotation_images
        img_dir = work / "images"
        images = render_annotation_images(points, risks, img_dir)
        preview_dir = work / "preview"
        gaussian_filename = "scene_gaussian.ply"
        export_gaussian_ply(gaussians, preview_dir / gaussian_filename)
        preview = build_preview_assets(
            points,
            preview_dir,
            title=scan.project.name,
            colors=point_colors,
            gaussian_filename=gaussian_filename,
            scale_status="metric_references" if calibrated_flag else "relative",
            cameras=cams,
            image_shapes=[image.shape[:2] for image in imgs],
            camera_scale=scale,
            quality={
                "sfm": sfm_quality.metrics,
                "gaussian": gaussian_quality.metrics,
                "training": gaussians.get("training_metrics", {}),
                "calibration": calibration_quality,
            },
        )

        _upsert_report(
            db,
            scan_id=scan_id,
            score=score,
            risks=risks,
            measures=measures,
            advice=advice,
            images=[str(p) for p in images],
            preview=preview,
            calibrated=calibrated_flag,
        )
        _stage(db, scan, "done", 100, "评估完成")
    except Exception as e:  # noqa: BLE001 - 管道任一步失败都落到 failed
        db.rollback()
        logger.error(
            "pipeline_failed scan_id=%s exception_type=%s",
            scan_id,
            type(e).__name__,
        )
        logger.exception("pipeline_failed traceback scan_id=%s", scan_id)
        scan = db.get(Scan, scan_id)
        if scan:
            _fail(db, scan, "管道处理失败，请稍后重试")
    finally:
        db.close()


def _stage(db, scan, status, progress, message):
    scan.status, scan.progress, scan.message = status, progress, message
    db.commit()


def _fail(db, scan, message):
    scan.status, scan.progress, scan.message = "failed", 100, message
    db.commit()


def _upsert_report(
    db,
    *,
    scan_id: int,
    score: float,
    risks: list,
    measures: dict,
    advice: list,
    images: list[str],
    preview: dict,
    calibrated: int,
) -> Report:
    """Create or replace a scan report so pipeline retries are idempotent."""
    values = {
        "score": score,
        "risks": risks,
        "measures": measures,
        "advice": advice,
        "images": images,
        "preview": preview,
        "calibrated": calibrated,
    }
    report = db.query(Report).filter(Report.scan_id == scan_id).one_or_none()
    if report is None:
        report = Report(scan_id=scan_id, **values)
        db.add(report)
        try:
            db.flush()
            return report
        except IntegrityError:
            db.rollback()
            report = db.query(Report).filter(Report.scan_id == scan_id).one_or_none()
            if report is None:
                raise
    for field, value in values.items():
        setattr(report, field, value)
    return report


def _calibrate_with_a4(imgs, cams) -> float | None:
    """A4 纸标定：双视角三角化 A4 中心 → SFM 单位深度；成像尺寸 → 米制深度。

    返回尺度因子（米/单位）。任意两帧组合求射线最近点，取合理性范围内的结果。
    米制深度用平行面近似（A4 平放地面、镜头接近水平时误差小）。
    """
    from pipeline.calibrator import A4_LONG, compute_scale_from_pixel, detect_a4_in_image

    dets = []  # [(cam, long_px, cx, cy)]
    for img, cam in zip(imgs[:20], cams[:20]):
        det = detect_a4_in_image(img)
        if det is not None:
            long_px, _short_px, cx, cy = det
            dets.append((cam, long_px, cx, cy))
    if len(dets) < 2:
        return None

    for i in range(min(len(dets), 5)):
        for j in range(i + 1, min(len(dets), 5)):
            cam_i, long_i, cx_i, cy_i = dets[i]
            cam_j, long_j, cx_j, cy_j = dets[j]
            # A4 中心的相机系射线（单位方向）
            ray_i = _pixel_ray(cam_i, cx_i, cy_i)
            ray_j = _pixel_ray(cam_j, cx_j, cy_j)
            # 世界系射线
            d_i = cam_i["R"].T @ ray_i
            d_j = cam_j["R"].T @ ray_j
            C_i = -cam_i["R"].T @ cam_i["t"]
            C_j = -cam_j["R"].T @ cam_j["t"]
            P = _triangulate(C_i, d_i, C_j, d_j)
            if P is None:
                continue
            d_units = float(np.linalg.norm(P - C_i))
            if d_units <= 0.1:
                continue
            # 米制深度（两帧平均，平行面近似）
            d_m = (cam_i["K"][0, 0] * A4_LONG / long_i + cam_j["K"][0, 0] * A4_LONG / long_j) / 2.0
            scale = d_m / d_units
            if 0.2 < scale < 5.0:  # 合理性检查（20cm~5m 每单位）
                return scale
    return None


def _pixel_ray(cam: dict, x: float, y: float) -> np.ndarray:
    """图像像素 → 相机系射线（单位方向）。"""
    K = cam["K"]
    ray = np.linalg.inv(K) @ np.array([x, y, 1.0])
    return ray / np.linalg.norm(ray)


def _triangulate(C_i, d_i, C_j, d_j) -> np.ndarray | None:
    """两射线（起点 C、单位方向 d）的最近点（最小二乘中点）。"""
    w = C_j - C_i
    a = float(d_i @ d_i)
    b = float(d_i @ d_j)
    c = float(d_j @ d_j)
    d = float(d_i @ w)
    e = float(d_j @ w)
    denom = a * c - b * b
    if abs(denom) < 1e-12:
        return None  # 射线平行
    # 解 [a -b; b -c][s; t] = [d; e]（最小化 |C_i+s*d_i - C_j-t*d_j|²）
    s = (c * d - b * e) / denom
    t = (b * d - a * e) / denom
    return (C_i + s * d_i + C_j + t * d_j) / 2.0


def _known_reference_value(measurements: list[dict], object_type: str, dimension: str) -> float | None:
    """只把用户明确提供的尺寸作为已确认测量值。"""
    for item in measurements:
        if item.get("object_type") == object_type and item.get("dimension") == dimension:
            return float(item["meters"])
    return None


def _robust_scene_extents(points: np.ndarray) -> list[float] | None:
    """返回 PCA 主轴下的稳健场景范围；仅在米制标定成功后对外提供。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(points) < 100:
        return None
    center = np.median(points, axis=0)
    _, _, axes = np.linalg.svd(points - center, full_matrices=False)
    projected = (points - center) @ axes.T
    extents = np.percentile(projected, 99, axis=0) - np.percentile(projected, 1, axis=0)
    if not np.isfinite(extents).all():
        return None
    return [float(value) for value in np.sort(extents)[::-1]]


def _pair_registered_training_frames(
    sharp_frames: list[Path], cameras: list[dict],
) -> list[tuple[Path, dict]]:
    """只将严格清晰且被 SfM 注册的帧交给 3DGS，排除轨迹桥接帧。"""
    name_to_camera = {camera["name"]: camera for camera in cameras}
    return [
        (path, name_to_camera[path.name])
        for path in sorted(sharp_frames)
        if path.name in name_to_camera
    ]


def _prepare_training_views(
    cams: list[dict],
    imgs: list[np.ndarray],
    *,
    max_views: int = DEFAULT_MAX_TRAINING_VIEWS,
    max_dimension: int = 1280,
) -> tuple[list[dict], list[np.ndarray]]:
    """均匀保留全轨迹代表视角，并同步缩放图像内参。"""
    import cv2

    if len(cams) != len(imgs) or not cams:
        raise ValueError("训练相机与图片必须非空且一一对应")
    count = min(len(imgs), max_views)
    indices = np.linspace(0, len(imgs) - 1, count, dtype=int)
    selected_cams, selected_imgs = [], []
    for index in indices:
        image = imgs[int(index)]
        height, width = image.shape[:2]
        scale = min(1.0, max_dimension / max(height, width))
        camera = dict(cams[int(index)])
        camera["K"] = np.asarray(camera["K"], dtype=np.float64).copy()
        if scale < 1.0:
            resized = cv2.resize(
                image,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
            camera["K"][0, :] *= resized.shape[1] / width
            camera["K"][1, :] *= resized.shape[0] / height
            image = resized
        selected_cams.append(camera)
        selected_imgs.append(image)
    return selected_cams, selected_imgs


def _configured_training_view_limit() -> int:
    """读取可调训练视角上限，并限制到当前全量 GPU 张量实现的安全范围。"""
    raw = os.getenv("GAUSSIAN_MAX_TRAINING_VIEWS", str(DEFAULT_MAX_TRAINING_VIEWS))
    try:
        requested = int(raw)
    except ValueError:
        logger.warning(
            "invalid_gaussian_max_training_views value=%r fallback=%d",
            raw,
            DEFAULT_MAX_TRAINING_VIEWS,
        )
        requested = DEFAULT_MAX_TRAINING_VIEWS
    return max(1, min(requested, MAX_CONFIGURED_TRAINING_VIEWS))


def _find_obstacles(imgs, cams, points=None, *, frame_stride: int = 30) -> dict:
    """生成可供空间判断使用的语义结果，不把普通家具检测直接判为通道风险。

    每个采样帧真实执行 GroundingDINO bbox → SAM mask。若提供未缩放的 SFM 点云，
    mask 会按相机 ``K/R/t`` 投影并跨帧投票；报告只保存汇总，不保存庞大 mask/点索引。
    """
    from collections import Counter, defaultdict

    from pipeline.semantic import analyze_image, merge_votes, project_mask_to_points

    if frame_stride <= 0:
        raise ValueError("frame_stride must be positive")
    summaries = {}
    votes = defaultdict(lambda: defaultdict(int))
    sampled = list(zip(imgs[::frame_stride], cams[::frame_stride]))
    for frame_index, (img, cam) in enumerate(sampled):
        for detection in analyze_image(img):
            label = detection["label"]
            summary = summaries.setdefault(
                label,
                {
                    "label": label,
                    "count": 0,
                    "segmented_count": 0,
                    "frames": set(),
                    "mask_area_ratio_sum": 0.0,
                },
            )
            summary["count"] += 1
            summary["frames"].add(frame_index)
            if not detection.get("mask_valid"):
                continue
            summary["segmented_count"] += 1
            summary["mask_area_ratio_sum"] += float(detection["mask_area_ratio"])
            if points is None:
                continue
            hits = project_mask_to_points(
                points,
                detection["mask"],
                cam["K"],
                cam["R"],
                cam["t"],
            )
            for point_id in hits:
                votes[point_id][label] += 1

    point_labels = merge_votes(votes)
    point_counts = Counter(point_labels.values())
    objects = []
    for label, summary in sorted(summaries.items()):
        segmented_count = summary["segmented_count"]
        objects.append(
            {
                "label": label,
                "count": summary["count"],
                "segmented_count": segmented_count,
                "frame_count": len(summary["frames"]),
                "mean_mask_area_ratio": round(
                    summary["mask_area_ratio_sum"] / segmented_count, 6
                )
                if segmented_count
                else 0.0,
                "projected_point_count": int(point_counts.get(label, 0)),
            }
        )
    return {
        "detected_objects": objects,
        "semantic_point_counts": dict(sorted(point_counts.items())),
        "semantic_point_ids": {
            label: sorted(point_id for point_id, point_label in point_labels.items() if point_label == label)
            for label in sorted(point_counts)
        },
        # None 表示尚未完成空间判定；[] 只保留给“已确认通道内无障碍”。
        "obstacles_in_passage": None,
        "obstacle_assessment_status": "pending_spatial_validation",
    }
