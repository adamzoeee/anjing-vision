"""整条管道编排：抽帧→SFM→训练→导出→标定→分割→几何→评分→报告。

每个阶段更新 Scan.status/progress；失败置 failed 并记录 message。
"""
import logging
import os
import time
import re
import traceback
from pathlib import Path

import numpy as np
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import SessionLocal
from ..models import Report, Scan
from ..storage import media_path

logger = logging.getLogger("anjing.pipeline")

STAGES = [
    ("extracting", 5, "抽帧中"), ("reconstructing", 25, "3D 重建中"),
    ("sfm", 25, "相机位姿估计中"),
    ("training", 45, "3D 重建训练中"), ("calibrating", 65, "尺度标定中"),
    ("segmenting", 75, "语义分割中"), ("understanding", 82, "空间语义理解中"),
    ("analyzing", 85, "几何分析中"),
    ("scoring", 90, "风险评分中"), ("reporting", 95, "生成报告中"),
]


def _step_measurements(step: float) -> dict:
    """保留“确认未检测到门槛”的 0.0；台阶高度不再重复作为门槛评分。"""
    return {
        "threshold_m": step if 0.0 <= step < 0.3 else None,
        "stairs_exist": step >= 0.3,
    }


def run_pipeline(scan_id: int) -> None:
    pipeline_started = time.perf_counter()
    timings = {}
    s = get_settings()
    db = SessionLocal()
    scan = None
    try:
        scan = db.get(Scan, scan_id)
        if scan is None:
            return
        work = Path(s.data_dir) / "work" / str(scan_id)
        work.mkdir(parents=True, exist_ok=True)
        src = media_path(scan.media_path)
        # 正式重建主线统一为 vid2scene；视频与照片目录都由同一个适配层处理。
        _run_vid2scene_pipeline(db, scan, work, src, timings, pipeline_started)
        return
    except Exception as e:  # noqa: BLE001 - 管道任一步失败都落到 failed
        failed_stage = getattr(scan, "status", "unknown")
        db.rollback()
        safe_message = _sanitize_log_text(str(e))
        logger.error(
            "pipeline_failed scan_id=%s stage=%s exception_type=%s exception_message=%s",
            scan_id,
            failed_stage,
            type(e).__name__,
            safe_message,
        )
        logger.error(
            "pipeline_failed traceback scan_id=%s stage=%s\n%s",
            scan_id,
            failed_stage,
            _sanitize_log_text("".join(traceback.format_exception(type(e), e, e.__traceback__))),
        )
        scan = db.get(Scan, scan_id)
        if scan:
            _fail(db, scan, _user_failure_message(failed_stage, e))
    finally:
        db.close()


def _stage(db, scan, status, progress, message):
    scan.status, scan.progress, scan.message = status, progress, message
    db.commit()


def _run_vid2scene_pipeline(
    db, scan, work: Path, src: Path, timings: dict, pipeline_started: float,
) -> None:
    """vid2scene 端到端重建分支：重建（vid2scene）→ 语义 → 标定 → 几何 → 评分 → 报告。

    下游 exporter / calibrator / geometry / rules / report 与自研链路完全复用；
    只有「抽帧 → SfM → 3DGS 训练」被 vid2scene 替换。
    """
    from pipeline import vid2scene_runner as vr

    # 重建（可能耗时数十分钟）之前先预检语义模型，避免重建完成后才失败。
    from pipeline.semantic import preflight_semantic_models
    semantic_problems = preflight_semantic_models()
    if semantic_problems:
        _fail(db, scan, "语义模型未就绪：" + "；".join(semantic_problems))
        return

    _stage(db, scan, "reconstructing", 10, "3D 重建中（vid2scene）")
    stage_started = time.perf_counter()
    last_progress = {"value": 10.0}

    def on_progress(frac: float) -> None:
        value = 10.0 + 35.0 * float(frac)
        if value - last_progress["value"] >= 1.0:
            last_progress["value"] = value
            _stage(db, scan, "reconstructing", round(value, 1), "3D 重建中（vid2scene）")

    try:
        outputs = vr.run_reconstruction(src, work / "vid2scene", progress_callback=on_progress)
    except RuntimeError as exc:
        _fail(db, scan, f"3D 重建失败：{exc}")
        return
    timings["vid2scene_seconds"] = time.perf_counter() - stage_started
    timings["vid2scene_stage_seconds"] = float(outputs.get("seconds", 0.0))

    _stage(db, scan, "reconstructing", 47, "解析重建结果中")
    reconstruction = vr.parse_reconstruction(work / "vid2scene")
    metric_calibration = reconstruction["metric_calibration"]
    metric_scale_status = reconstruction["metric_scale_status"]
    cameras = reconstruction["cameras"]
    image_dir: Path = reconstruction["image_dir"]
    frames_on_disk = sorted(
        list(image_dir.glob("*.png")) + list(image_dir.glob("*.jpg")) + list(image_dir.glob("*.jpeg"))
    )

    from pipeline.quality import assess_sfm, grade_reconstruction
    sfm_quality = assess_sfm(
        cameras, reconstruction["points3D"], len(frames_on_disk), reconstruction["quality"]
    )
    if not sfm_quality.ok:
        _fail(db, scan, sfm_quality.reason)
        return
    from pipeline.sfm import filter_trajectory_jumps
    kept_cameras, dropped_jump_names, jump_diagnostics = filter_trajectory_jumps(cameras)
    sfm_quality.metrics["trajectory_jump_filter"] = jump_diagnostics
    cameras = kept_cameras

    # 图像/相机按名字配对；畸变校正与曝光归一化沿用自研链路约定。
    from PIL import Image
    from pipeline.sfm import undistort_registered_view
    from pipeline.trainer import normalize_exposure
    name_to_camera = {camera["name"]: camera for camera in cameras}
    imgs, cams = [], []
    for path in frames_on_disk:
        camera = name_to_camera.get(path.name)
        if camera is None:
            continue
        imgs.append(np.asarray(Image.open(path).convert("RGB")))
        cams.append(camera)
    if len(cams) < 5:
        _fail(db, scan, "vid2scene 注册相机过少，无法继续评估")
        return
    rectified = [undistort_registered_view(image, camera) for image, camera in zip(imgs, cams)]
    imgs = [item[0] for item in rectified]
    cams = [item[1] for item in rectified]
    imgs, exposure_diagnostics = normalize_exposure(imgs)

    # 测量点云取自训练后的高斯中心（不透明度过滤），替代自研训练导出。
    splat = reconstruction["gaussian"]
    from pipeline.vid2scene_runner import point_cloud_from_splat
    splat_points, splat_colors = point_cloud_from_splat(splat, opacity_threshold=0.01)
    import open3d as o3d
    from pipeline.exporter import statistical_filter
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(splat_points)
    pcd.colors = o3d.utility.Vector3dVector(splat_colors.astype(np.float64) / 255.0)
    pcd = statistical_filter(pcd)
    points = np.asarray(pcd.points)
    point_colors = np.asarray(pcd.colors)
    if len(points) < 100:
        _fail(db, scan, "重建点云过少，评估失败")
        return
    o3d.io.write_point_cloud(str(work / "pointcloud.ply"), pcd)
    semantic_points = points.copy()

    # 高斯张量（供质量门禁/预览导出复用自研 exporter 契约）
    import torch
    opacities = np.clip(splat["opacities"], 1e-7, 1 - 1e-7)
    sh_rest = splat["sh_rest"]
    # vid2scene 训练器的 SH 阶数可能不是 3（如 MCMC 默认 degree 2 → 24 个
    # f_rest 分量），列数必须从 PLY 动态读取：(N, 3*K) → (N, K, 3)。
    rest_channels = sh_rest.shape[1] // 3 if sh_rest.shape[1] else 0
    sh_rest_tensor = (
        torch.from_numpy(sh_rest.astype(np.float32))
        .reshape(-1, 3, rest_channels)
        .transpose(1, 2)
        if rest_channels else torch.zeros(len(splat["means"]), 15, 3)
    )
    gaussians = {
        "means": torch.from_numpy(splat["means"].astype(np.float32)),
        "scales": torch.from_numpy(np.log(np.clip(splat["scales"], 1e-6, None)).astype(np.float32)),
        "quats": torch.from_numpy(splat["quats"].astype(np.float32)),
        "opacities": torch.from_numpy(np.log(opacities / (1 - opacities)).astype(np.float32)),
        "sh0": torch.from_numpy(splat["sh0"].astype(np.float32)).unsqueeze(1),
        "sh_rest": sh_rest_tensor,
        "opacity_logits": True,
        "training_metrics": {
            "backend": "vid2scene",
            "iterations": int(os.getenv("VID2SCENE_TRAINING_STEPS", "20000")),
            "gaussian_count": int(len(splat["means"])),
            "training_view_count": len(cams),
            "exposure_normalization": exposure_diagnostics,
            "timings": {**timings, "total_seconds": time.perf_counter() - pipeline_started},
        },
    }
    from pipeline.quality import assess_gaussians
    gaussian_quality = assess_gaussians(
        gaussians["means"].numpy(), reconstruction["points3D"], gaussians["training_metrics"]
    )
    if not gaussian_quality.ok:
        _fail(db, scan, gaussian_quality.reason)
        return

    _stage(db, scan, "segmenting", 60, "语义分割与参考物识别中")
    semantic_result = _find_obstacles(imgs, cams, semantic_points)

    from pipeline.spatial_measurement import (
        estimate_room_frame,
        measure_room,
        measure_semantic_objects,
    )
    room_frame = estimate_room_frame(points, cams)
    object_measurements = measure_semantic_objects(
        points, semantic_result["semantic_point_ids"], room_frame
    )
    for result in object_measurements.values():
        if result.get("status") == "measured":
            result["unit"] = "model_units"

    _stage(db, scan, "calibrating", 70, "确认场景米制尺度中")
    from pipeline.calibrator import estimate_scale_from_references
    from pipeline.quality import assess_metric_scene
    if metric_scale_status == "metric_apriltag":
        # vid2scene 已在训练前将 COLMAP 相机、点云和高斯统一缩放为米，禁止再次缩放。
        scale = 1.0
        calibrated_flag = 4
        calibration_quality = {
            "method": "apriltag",
            **metric_calibration,
            "already_applied": True,
        }
        metric_quality = assess_metric_scene(points, calibrated=1)
        if not metric_quality.ok:
            raise RuntimeError(f"AprilTag 米制场景质量检查失败：{metric_quality.reason}")
    else:
        # 仅在明确关闭 AprilTag 的兼容模式下，才允许已知物体尺寸作为备选标定。
        scale, calibration_details = estimate_scale_from_references(
            points,
            semantic_result["semantic_point_ids"],
            scan.reference_measurements or [],
            object_measurements=object_measurements,
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
        if scale is None:
            from pipeline.calibrator import fallback_single_reference_scale
            fallback, fallback_meta = fallback_single_reference_scale(
                calibration_details, points, room_frame
            )
            if fallback is not None:
                scale = fallback
                calibrated_flag = 2
                calibration_quality = {**calibration_quality, **fallback_meta}
    scale = scale or 1.0
    points = points * scale
    if calibrated_flag:
        for result in object_measurements.values():
            if result.get("status") == "measured":
                result["dimensions"] = {
                    name: float(value) * scale
                    for name, value in result["dimensions"].items()
                }
                result["unit"] = "meters"
        metric_room_frame = None if room_frame is None else type(room_frame)(
            origin=room_frame.origin * scale,
            axes=room_frame.axes,
            ground_inlier_ratio=room_frame.ground_inlier_ratio,
            confidence=room_frame.confidence,
            horizontal_method=room_frame.horizontal_method,
        )
    else:
        metric_room_frame = room_frame
    if calibrated_flag:
        gaussians["means"] = gaussians["means"] * scale
        gaussians["scales"] = gaussians["scales"] + np.log(scale)

    # 第二阶段：多视角语义融合 → 3D 实例分离 → 房间坐标系 → 稳健真实尺寸。
    # 融合始终在“点云与相机同坐标系”的原始点云（semantic_points）上进行，
    # 最终单位由已确定的尺度状态决定；语义模块自身绝不估计/应用 scale。
    _stage(db, scan, "understanding", 80, "多视角语义融合与空间理解中")
    effective_scale_status = (
        metric_scale_status
        if calibrated_flag == 4
        else ("metric_references" if calibrated_flag else metric_scale_status)
    )
    semantic_space = _build_semantic_space(
        semantic_points,
        semantic_result,
        room_frame,
        unit="model_units",
        metric_scale_status=effective_scale_status,
    )
    if calibrated_flag:
        from pipeline.spatial_measurement import rescale_semantic_space, room_dimensions_for_space

        semantic_space = rescale_semantic_space(semantic_space, scale, unit="meters")
        # 房间尺寸用最终缩放后的点云/坐标系直接重算，与实例尺寸保持一致单位。
        semantic_space["room_dimensions"] = room_dimensions_for_space(
            points, metric_room_frame, unit="meters"
        )
    else:
        # 无真实尺度：语义理解保留，但绝不输出任何数值尺寸。
        for obj in semantic_space["objects"]:
            for key in list(obj.get("dimensions", {})):
                obj["dimensions"][key] = None
            door_meta = (obj.get("metadata") or {}).get("door_measurement")
            if door_meta:
                door_meta["estimated_opening_width_m"] = None
                door_meta["estimated_opening_height_m"] = None

    _stage(db, scan, "analyzing", 85, "几何分析中")
    references = scan.reference_measurements or []
    door_w = _known_reference_value(references, "door", "width")
    room_dimensions = measure_room(points, metric_room_frame) if calibrated_flag else {
        "status": "unknown", "confidence": "low", "reason": "metric_scale_unavailable"
    }
    measures = {
        "door_width_m": door_w,
        "passage_width_m": None,
        "threshold_m": None,
        "stairs_exist": None,
        "slope": None,
        "uneven_m": None,
        "scale_status": metric_scale_status if calibrated_flag == 4 else (
            "metric_references" if calibrated_flag else "relative"
        ),
        "coordinate_unit": "meters" if calibrated_flag else "model_units",
        "calibration_quality": calibration_quality,
        "reference_measurements": references,
        "room_dimensions": room_dimensions,
        "reconstruction_extent_m": _robust_scene_extents(points) if calibrated_flag else None,
        "object_dimensions": object_measurements,
        "semantic_space": semantic_space,
        "room_coordinate_system": None if room_frame is None else {
            "status": "estimated",
            "confidence": room_frame.confidence,
            "ground_inlier_ratio": room_frame.ground_inlier_ratio,
            "horizontal_method": room_frame.horizontal_method,
            "floor_plane": None if room_frame.floor_plane is None else room_frame.floor_plane.tolist(),
            "wall_normals": [normal.tolist() for normal in room_frame.wall_normals],
            "ground_support": room_frame.ground_support,
        },
        "reconstruction_quality": {
            "sfm": sfm_quality.metrics,
            "gaussian": gaussian_quality.metrics,
            "training": gaussians.get("training_metrics", {}),
        },
        "geometry_assessment_status": "pending_spatial_validation",
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
    if calibrated_flag == 2:
        advice.append(
            "多个参考尺寸推导的比例不一致，已按单一参考物标定米制尺度（精度较低），"
            "建议补充参考物或重新录制后复核"
        )
    grade, grade_reasons = grade_reconstruction(
        sfm_quality.metrics,
        gaussian_quality.metrics,
        calibration_quality,
    )
    measures["reconstruction_quality"]["grade"] = grade
    measures["reconstruction_quality"]["grade_reasons"] = grade_reasons
    if grade == "low":
        advice.extend(grade_reasons)

    _stage(db, scan, "reporting", 95, "生成报告中")
    from pipeline.exporter import export_gaussian_ply
    from pipeline.trainer import prune_gaussians
    from pipeline.report_builder import build_preview_assets, render_annotation_images
    img_dir = work / "images"
    images = render_annotation_images(points, risks, img_dir)
    preview_dir = work / "preview"
    gaussian_filename = "scene_gaussian.ply"
    gaussians, prune_diagnostics = prune_gaussians(gaussians, reconstruction["points3D"])
    gaussians["training_metrics"]["gaussian_pruning"] = prune_diagnostics
    export_gaussian_ply(
        gaussians,
        preview_dir / gaussian_filename,
        max_gaussians=int(os.getenv("PREVIEW_MAX_GAUSSIANS", "800000")),
    )
    preview = build_preview_assets(
        points,
        preview_dir,
        title=scan.project.name,
        colors=point_colors,
        gaussian_filename=gaussian_filename,
        scale_status=metric_scale_status if calibrated_flag == 4 else (
            "metric_references" if calibrated_flag else "relative"
        ),
        cameras=cams,
        image_shapes=[image.shape[:2] for image in imgs],
        camera_scale=scale,
        max_points=800_000,
        quality={
            "sfm": sfm_quality.metrics,
            "gaussian": gaussian_quality.metrics,
            "training": gaussians.get("training_metrics", {}),
            "calibration": calibration_quality,
            "grade": grade,
            "grade_reasons": grade_reasons,
        },
    )

    _upsert_report(
        db,
        scan_id=scan.id,
        score=score,
        risks=risks,
        measures=measures,
        advice=advice,
        images=[str(p) for p in images],
        preview=preview,
        calibrated=calibrated_flag,
    )
    _stage(
        db,
        scan,
        "done",
        100,
        "评估完成（重建质量较低，建议重新录制）" if grade == "low" else "评估完成",
    )


def _fail(db, scan, message):
    scan.status, scan.progress, scan.message = "failed", 100, message
    db.commit()


def _user_failure_message(stage: str, exc: Exception) -> str:
    """Return a concise frontend message while detailed diagnostics stay in logs."""
    detail = str(exc).lower()
    if stage == "training":
        if "out of memory" in detail:
            return "3DGS训练失败：GPU显存不足，请降低训练视角数量后重试"
        if any(
            token in detail
            for token in ("cuda扩展", "cuda extension", "ninja", "msvc", "nvcc")
        ):
            return "3DGS训练失败：gsplat CUDA扩展不可用，请检查CUDA/Ninja/MSVC环境"
        return "3DGS训练失败，请查看后端日志"
    return "管道处理失败，请稍后重试"


def _sanitize_log_text(value: str) -> str:
    """Retain diagnostics without leaking credentials or local filesystem paths."""
    value = re.sub(r"(?i)(password|secret|token)\s*=\s*[^\s,;]+", r"\1=<redacted>", value)
    return re.sub(r"(?i)(?:[a-z]:\\|[a-z]:/)[^\s\r\n]+", "<local-path>", value)


def _upsert_report(
    db,
    *,
    scan_id: int,
    score: float | None,
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


def _find_obstacles(imgs, cams, points=None, *, frame_stride: int | None = None) -> dict:
    """生成可供空间判断使用的语义结果，不把普通家具检测直接判为通道风险。

    每个采样帧真实执行 GroundingDINO bbox → SAM mask。若提供未缩放的 SFM 点云，
    mask 会按相机 ``K/R/t`` 投影并跨帧投票；报告只保存汇总，不保存庞大 mask/点索引。
    """
    from collections import Counter, defaultdict

    from pipeline.semantic import analyze_image, merge_votes, project_mask_to_points

    if frame_stride is None:
        frame_stride = max(1, len(imgs) // 16)
        # 真实验收时可调采样密度（更小=检测更多帧=召回更高但更慢）。
        frame_stride = max(1, int(os.getenv("SEMANTIC_FRAME_STRIDE", str(frame_stride))))
    if frame_stride <= 0:
        raise ValueError("frame_stride must be positive")
    summaries = {}
    votes = defaultdict(lambda: defaultdict(int))
    sampled = list(zip(imgs[::frame_stride], cams[::frame_stride]))
    # 第二阶段多视角语义融合直接复用这里的 GroundingDINO/SAM 结果，
    # 不重复推理；view_records 携带每帧相机与有效 mask。
    view_records = []
    for frame_index, (img, cam) in enumerate(sampled):
        frame_detections = analyze_image(img)
        view_records.append({
            "camera": cam,
            "image_shape": tuple(int(value) for value in img.shape[:2]),
            "detections": [
                {
                    "label": detection["label"],
                    "score": float(detection.get("score", 0.0)),
                    "mask": detection["mask"],
                    "mask_score": float(detection.get("mask_score", 0.0)),
                }
                for detection in frame_detections
                if detection.get("mask_valid")
            ],
        })
        for detection in frame_detections:
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

    point_labels = merge_votes(votes, min_votes=2 if len(sampled) >= 8 else 1)
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
        "view_records": view_records,
    }


def _build_semantic_space(
    points,
    semantic_result: dict,
    room_frame,
    *,
    unit: str,
    metric_scale_status: str,
) -> dict:
    """第二阶段：多视角语义融合 → 3D 实例分离 → 房间坐标系 → 稳健尺寸。

    只在已确定的尺度状态下消费重建数据，自身绝不估计/应用 scale；
    无米制尺度时语义理解照常，但所有 dimensions 为 unknown。
    """
    from pipeline.semantic import fuse_multiview_semantics
    from pipeline.spatial_measurement import build_semantic_space

    view_records = semantic_result.get("view_records") or []
    fusion = fuse_multiview_semantics(points, view_records)
    return build_semantic_space(
        points,
        fusion,
        view_records,
        room_frame,
        unit=unit,
        metric_scale_status=metric_scale_status,
    )
