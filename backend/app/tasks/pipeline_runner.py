"""整条管道编排：抽帧→SFM→训练→导出→标定→分割→几何→评分→报告。

每个阶段更新 Scan.status/progress；失败置 failed 并记录 message。
"""
import logging
from pathlib import Path

import numpy as np
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import SessionLocal
from ..models import Report, Scan
from ..storage import media_path

logger = logging.getLogger("anjing.pipeline")

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

        from pipeline.frame_extractor import extract_frames, filter_sharp_frames
        if src.is_dir():
            # 照片模式：目录下的图片直接作为帧
            all_frames = sorted(list(src.glob("*.jpg")) + list(src.glob("*.jpeg")) + list(src.glob("*.JPG")))
        else:
            all_frames = extract_frames(src, frames)
        kept, _ = filter_sharp_frames(all_frames)
        if len(kept) < 30:
            _fail(db, scan, f"抽帧后有效图片仅 {len(kept)} 张，请重录（保证光线充足、慢速移动）")
            return

        _stage(db, scan, "sfm", 25, "相机位姿估计中")
        from pipeline.sfm import run_sfm
        # SFM 只跑清晰帧（模糊帧会污染特征匹配），复制到独立目录保证位姿与图像一一对应
        frames_clean = work / "frames_clean"
        import shutil
        shutil.rmtree(frames_clean, ignore_errors=True)  # 清空重跑残留
        frames_clean.mkdir(parents=True, exist_ok=True)
        for p in kept:
            shutil.copy(p, frames_clean / p.name)
        sfm_out = run_sfm(frames_clean, work / "sfm")
        if len(sfm_out["cameras"]) < 5:
            _fail(db, scan, "SFM 恢复的相机过少，请重录（保证画面重叠、纹理充足）")
            return

        _stage(db, scan, "training", 45, "3D 重建训练中")
        from pipeline.trainer import prepare_tensors, train_gaussians
        from PIL import Image
        # 对齐：相机与图像按文件名排序后一一对应（SFM 可能漏注册部分帧，过滤掉）
        name_to_cam = {c["name"]: c for c in sfm_out["cameras"]}
        paired = [(p, name_to_cam[p.name]) for p in sorted(kept) if p.name in name_to_cam]
        if len(paired) < 5:
            _fail(db, scan, "SFM 注册帧过少，无法训练")
            return
        imgs = [np.asarray(Image.open(p).convert("RGB")) for p, _ in paired]
        cams = [c for _, c in paired]
        gt = prepare_tensors(cams, imgs)
        gaussians = train_gaussians(gt, sfm_out["points3D"])

        from pipeline.exporter import export_pointcloud, statistical_filter
        import open3d as o3d
        pcd_path = work / "pointcloud.ply"
        export_pointcloud(gaussians, pcd_path)
        pcd = statistical_filter(o3d.io.read_point_cloud(str(pcd_path)))
        o3d.io.write_point_cloud(str(pcd_path), pcd)
        points = np.asarray(pcd.points)
        if len(points) < 100:
            _fail(db, scan, "重建点云过少，评估失败")
            return
        # 相机外参仍处于原始 SFM 坐标系；语义 mask 投影必须使用未缩放点云。
        semantic_points = points.copy()

        _stage(db, scan, "calibrating", 65, "尺度标定中")
        from pipeline.calibrator import scale_from_door_prior
        scale, calibrated = 1.0, 0
        a4_scale = _calibrate_with_a4(imgs, cams)
        if a4_scale:
            scale, calibrated = a4_scale, 1
        else:
            door_h = _measure_door_height(points)
            if door_h and door_h > 1.0:
                scale, calibrated = scale_from_door_prior(door_h), 2
        points = points * scale

        _stage(db, scan, "segmenting", 75, "语义分割中")
        semantic_result = _find_obstacles(imgs, cams, semantic_points)

        _stage(db, scan, "analyzing", 85, "几何分析中")
        from pipeline.geometry import (fit_ground_plane, measure_door_width,
                                       measure_floor_slope, measure_step_height)
        slope = measure_floor_slope(points)
        step = measure_step_height(points)
        door_w = measure_door_width(points, wall_x=_dominant_wall_x(points))
        plane, inliers = fit_ground_plane(points)
        measures = {
            "door_width_m": door_w,
            "passage_width_m": _passage_width(points, inliers),
            **_step_measurements(step),
            "slope": slope,
            "uneven_m": _unevenness(points, inliers),
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
        from pipeline.report_builder import build_preview_assets, render_annotation_images
        img_dir = work / "images"
        images = render_annotation_images(points, risks, img_dir)
        preview = build_preview_assets(points, work / "preview", title=scan.project.name)

        _upsert_report(
            db,
            scan_id=scan_id,
            score=score,
            risks=risks,
            measures=measures,
            advice=advice,
            images=[str(p) for p in images],
            preview=preview,
            calibrated=calibrated,
        )
        _stage(db, scan, "done", 100, "评估完成")
    except Exception as e:  # noqa: BLE001 - 管道任一步失败都落到 failed
        db.rollback()
        logger.error(
            "pipeline_failed scan_id=%s exception_type=%s",
            scan_id,
            type(e).__name__,
        )
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


def _measure_door_height(points) -> float | None:
    z = points[:, 2]
    z = z[z > 0.5]
    if len(z) < 50:
        return None
    return float(np.percentile(z, 98))


def _dominant_wall_x(points) -> float:
    x = points[:, 0]
    return float(x[np.abs(x - np.median(x)).argsort()[-1]])


def _passage_width(points, inliers) -> float | None:
    floor = points[inliers]
    if len(floor) < 100:
        return None
    return float(np.percentile(floor[:, 1], 95) - np.percentile(floor[:, 1], 5))


def _unevenness(points, inliers) -> float | None:
    floor = points[inliers]
    if len(floor) < 100:
        return None
    return float(np.std(floor[:, 2]))


def _find_obstacles(imgs, cams, points=None, *, frame_stride: int = 10) -> dict:
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
        # None 表示尚未完成空间判定；[] 只保留给“已确认通道内无障碍”。
        "obstacles_in_passage": None,
        "obstacle_assessment_status": "pending_spatial_validation",
    }
