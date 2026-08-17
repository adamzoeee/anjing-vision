"""整条管道编排：抽帧 → SLAM3R 稠密重建 → 点云清理/对齐/缩放 → SpatialLM 结构识别 → 3D 预览。

每个阶段更新 Scan.status/progress；失败置 failed 并记录 message。
长度测量、风险识别与评分暂不在本阶段实现（后续在此文件扩展）。
"""
import logging
import os
import re
import time
import traceback
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import SessionLocal
from ..models import Report, Scan
from ..storage import media_path

logger = logging.getLogger("anjing.pipeline")

STAGES = [
    ("extracting", 5, "抽帧中"),
    ("reconstructing", 15, "SLAM3R 稠密重建中"),
    ("cleaning", 75, "点云清理与方向对齐中"),
    ("understanding", 88, "SpatialLM 空间结构识别中"),
    ("previewing", 95, "生成 3D 预览中"),
]


def run_pipeline(scan_id: int) -> None:
    pipeline_started = time.perf_counter()
    timings: dict = {}
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
        if not Path(src).is_file():
            raise RuntimeError("上传的视频文件不存在")
        _run_slam3r_pipeline(db, scan, work, src, timings, pipeline_started)
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


def _fail(db, scan, message):
    scan.status, scan.progress, scan.message = "failed", 100, message
    db.commit()


def _user_failure_message(stage: str, exc: Exception) -> str:
    """Return a concise frontend message while detailed diagnostics stay in logs."""
    detail = str(exc).lower()
    if stage in ("reconstructing", "extracting"):
        if "out of memory" in detail or "cuda" in detail:
            return "SLAM3R 重建失败：GPU 显存或 CUDA 环境异常，请检查显卡与环境"
        if "权重" in detail or "weight" in detail:
            return "SLAM3R 重建失败：模型权重缺失"
        if "ffmpeg" in detail or "抽帧" in detail:
            return "视频抽帧失败：请检查 ffmpeg 安装"
        return "SLAM3R 稠密重建失败，请查看后端日志"
    if stage == "cleaning":
        return "点云清理失败：重建结果质量不足，建议重新拍摄"
    if stage == "understanding":
        return "SpatialLM 空间结构识别失败，请查看后端日志"
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


def _run_slam3r_pipeline(
    db, scan, work: Path, src: Path, timings: dict, pipeline_started: float,
) -> None:
    """SLAM3R + SpatialLM 新链路：视频 → 稠密点云 → 清理/对齐 → 结构识别 → 预览。"""
    from pipeline import slam3r_runner, spatiallm_runner
    from pipeline.scene_postprocess import build_outputs

    # ---- 1. 抽帧 ----
    _stage(db, scan, "extracting", 5, "抽帧中")
    frames_dir = work / "frames"
    stage_started = time.perf_counter()
    frame_count = slam3r_runner.extract_frames(src, frames_dir)
    timings["extract_seconds"] = time.perf_counter() - stage_started
    if frame_count < 60:
        _fail(db, scan, "视频太短或抽帧过少，请录制 1 分钟以上")
        return

    # ---- 2. SLAM3R 稠密重建 ----
    _stage(db, scan, "reconstructing", 15, "SLAM3R 稠密重建中")
    stage_started = time.perf_counter()
    last_progress = {"value": 15.0}

    def on_recon_progress(frac: float) -> None:
        value = 15.0 + 58.0 * float(frac)
        if value - last_progress["value"] >= 1.0:
            last_progress["value"] = value
            _stage(db, scan, "reconstructing", round(value, 1), "SLAM3R 稠密重建中")

    try:
        recon = slam3r_runner.run_reconstruction(
            frames_dir,
            work / "slam3r",
            progress_callback=on_recon_progress,
        )
    except RuntimeError as exc:
        _fail(db, scan, f"SLAM3R 重建失败：{exc}")
        return
    timings["slam3r_seconds"] = time.perf_counter() - stage_started
    timings["slam3r_stage_seconds"] = float(recon.get("seconds", 0.0))

    # ---- 3. 点云清理 / z-up 与墙面方向对齐 / 米制缩放 ----
    _stage(db, scan, "cleaning", 75, "点云清理与方向对齐中")
    stage_started = time.perf_counter()
    post = build_outputs(recon["ply"], work / "postprocess")
    timings["postprocess_seconds"] = time.perf_counter() - stage_started

    # ---- 4. SpatialLM 空间结构识别 ----
    _stage(db, scan, "understanding", 88, "SpatialLM 空间结构识别中")
    stage_started = time.perf_counter()
    last_progress = {"value": 88.0}

    def on_spatial_progress(line: str) -> None:
        if last_progress["value"] < 93:
            last_progress["value"] = 93
            _stage(db, scan, "understanding", 93, "SpatialLM 空间结构识别中")

    try:
        spatial = spatiallm_runner.run_inference(
            post["aligned_ply"],
            work / "postprocess" / "layout.txt",
            detect_type="all",
            progress_callback=on_spatial_progress,
        )
    except RuntimeError as exc:
        _fail(db, scan, f"SpatialLM 识别失败：{exc}")
        return
    timings["spatiallm_seconds"] = time.perf_counter() - stage_started

    # ---- 5. 报告与预览清单 ----
    _stage(db, scan, "previewing", 95, "生成 3D 预览中")
    boxes = spatial["boxes"]
    counts = {
        "walls": len(boxes["walls"]),
        "doors": len(boxes["doors"]),
        "windows": len(boxes["windows"]),
        "objects": len(boxes["objects"]),
    }
    categories: dict[str, int] = {}
    for item in boxes["objects"]:
        categories[item["category"]] = categories.get(item["category"], 0) + 1

    measures = {
        "coordinate_unit": "meters",
        "scale_status": "estimated_ceiling_height",
        "reconstruction_backend": "slam3r",
        "understanding_backend": "spatiallm1.1-qwen-0.5b",
        "spatial_understanding": {
            "counts": counts,
            "object_categories": categories,
            "walls": boxes["walls"],
            "doors": boxes["doors"],
            "windows": boxes["windows"],
            "objects": boxes["objects"],
        },
        "alignment": post["metadata"]["alignment"],
        "scale": post["metadata"]["scale"],
        "extents_m": post["metadata"]["extents_m"],
        "points": {
            "aligned": post["metadata"]["points_aligned"],
            "preview": post["metadata"]["points_preview"],
        },
        "timings": {**timings, "total_seconds": round(time.perf_counter() - pipeline_started, 1)},
        # 长度测量、风险识别与评分：暂缓实现（后续阶段在此扩展）。
        "deferred": ["length_measurement", "risk_identification", "scoring"],
    }
    preview = {
        "viewer": f"/preview/{scan.id}",
        "manifest": f"/api/preview/{scan.id}/manifest.json",
        "ply": f"/api/preview/{scan.id}/scene.ply",
        "layout": f"/api/preview/{scan.id}/layout.json",
        "backend": "slam3r",
    }
    _upsert_report(
        db,
        scan_id=scan.id,
        score=None,  # 评分暂缓：无分数可给时保持 None（前端显示“无法评分”）
        risks=[],
        measures=measures,
        advice=[],
        images=[],
        preview=preview,
        calibrated=0,
    )
    _stage(db, scan, "done", 100, "重建完成")
    logger.info(
        "pipeline_done scan_id=%s seconds=%.1f walls=%d doors=%d windows=%d objects=%d",
        scan.id, time.perf_counter() - pipeline_started,
        counts["walls"], counts["doors"], counts["windows"], counts["objects"],
    )
