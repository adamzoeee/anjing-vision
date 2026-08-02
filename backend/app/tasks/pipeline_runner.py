"""整条管道编排：抽帧→SFM→训练→导出→标定→分割→几何→评分→报告。

每个阶段更新 Scan.status/progress；失败置 failed 并记录 message。
"""
from pathlib import Path

import numpy as np

from ..config import get_settings
from ..db import SessionLocal
from ..models import Report, Scan
from ..storage import media_path

STAGES = [
    ("extracting", 5, "抽帧中"), ("sfm", 25, "相机位姿估计中"),
    ("training", 45, "3D 重建训练中"), ("calibrating", 65, "尺度标定中"),
    ("segmenting", 75, "语义分割中"), ("analyzing", 85, "几何分析中"),
    ("scoring", 90, "风险评分中"), ("reporting", 95, "生成报告中"),
]


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
        sfm_out = run_sfm(frames if not src.is_dir() else src, work / "sfm")
        if len(sfm_out["cameras"]) < 5:
            _fail(db, scan, "SFM 恢复的相机过少，请重录（保证画面重叠、纹理充足）")
            return

        _stage(db, scan, "training", 45, "3D 重建训练中")
        from pipeline.trainer import prepare_tensors, train_gaussians
        from PIL import Image
        cams = sfm_out["cameras"]
        imgs = [np.asarray(Image.open(p).convert("RGB")) for p in sorted(kept)[: len(cams)]]
        gt = prepare_tensors(cams[: len(imgs)], imgs)
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

        _stage(db, scan, "calibrating", 65, "尺度标定中")
        from pipeline.calibrator import (A4_LONG, compute_scale_from_pixel,
                                         detect_a4_in_image, scale_from_door_prior)
        scale, calibrated = 1.0, 0
        a4_scale = _calibrate_with_a4(imgs, cams, work)
        if a4_scale:
            scale, calibrated = a4_scale, 1
        else:
            door_h = _measure_door_height(points)
            if door_h and door_h > 1.0:
                scale, calibrated = scale_from_door_prior(door_h), 2
        points = points * scale

        _stage(db, scan, "segmenting", 75, "语义分割中")
        obstacles = _find_obstacles(imgs, cams)

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
            "threshold_m": step if 0 < step < 0.3 else None,
            "stairs_exist": step >= 0.3,
            "slope": slope,
            "uneven_m": _unevenness(points, inliers),
            "obstacles_in_passage": obstacles,
            "bathroom_door_m": None,
        }

        _stage(db, scan, "scoring", 90, "风险评分中")
        from pipeline.rules import compute_score, evaluate_risks
        risks = evaluate_risks(measures)
        score, detail = compute_score(measures)
        advice = [r["advice"] for r in risks if r["level"] in ("red", "yellow")]

        _stage(db, scan, "reporting", 95, "生成报告中")
        from pipeline.report_builder import build_preview_assets, render_annotation_images
        img_dir = work / "images"
        images = render_annotation_images(points, risks, img_dir)
        preview = build_preview_assets(points, work / "preview", title=scan.project.name)

        report = Report(scan_id=scan_id, score=score, risks=risks, measures=measures,
                        advice=advice, images=[str(p) for p in images],
                        preview=preview, calibrated=calibrated)
        db.add(report)
        _stage(db, scan, "done", 100, "评估完成")
    except Exception as e:  # noqa: BLE001 - 管道任一步失败都落到 failed
        db.rollback()
        scan = db.get(Scan, scan_id)
        if scan:
            _fail(db, scan, f"管道失败: {e}")
    finally:
        db.close()


def _stage(db, scan, status, progress, message):
    scan.status, scan.progress, scan.message = status, progress, message
    db.commit()


def _fail(db, scan, message):
    scan.status, scan.progress, scan.message = "failed", 100, message
    db.commit()


def _calibrate_with_a4(imgs, cams, work) -> float | None:
    """在若干关键帧中找 A4 纸，用 SFM 位姿反推尺度（米/单位）。"""
    import numpy as np
    for img, cam in zip(imgs[:20], cams[:20]):
        det = detect_a4_in_image(img)
        if det is None:
            continue
        long_px, _ = det
        # 沿光轴取 1 个单位的深度，投影回世界求距离
        R, t = cam["R"], cam["t"]
        center_img = np.array([img.shape[1] / 2, img.shape[0] / 2, 1.0])
        ray = np.linalg.inv(cam["K"]) @ center_img
        ray = ray / np.linalg.norm(ray)
        cam_center = -R.T @ t
        p_cam = ray * 1.0
        p_world = R.T @ (p_cam - t)
        dist = np.linalg.norm(p_world - cam_center)
        return compute_scale_from_pixel(long_px, A4_LONG, dist, cam["K"][0, 0])
    return None


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


def _find_obstacles(imgs, cams) -> list[dict]:
    """GroundingDINO 检测常见杂物/家具（每 10 帧抽 1 帧），按标签统计计数。"""
    from pipeline.semantic import detect_objects
    seen = {}
    for img, cam in zip(imgs[::10], cams[::10]):
        for det in detect_objects(img):
            seen.setdefault(det["label"], 0)
            seen[det["label"]] += 1
    return [{"label": k, "count": v} for k, v in seen.items() if v > 0]
