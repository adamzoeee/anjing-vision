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


def _build_formal_assessment(work: Path, scan_id: int) -> dict:
    """Build formal assessment artifacts after accepted structure measurements."""
    from pipeline.spatial_assessment_inputs import build_formal_assessment_files

    postprocess = Path(work) / "postprocess"
    structure_json = postprocess / "structure_calibrated.json"
    if not structure_json.is_file():
        structure_json = postprocess / "structure.json"
    outputs = build_formal_assessment_files(
        postprocess / "measurements.json", structure_json, postprocess,
    )
    assessment = outputs["risk_payload"]
    logger.info(
        "formal_risk_assessment scan_id=%s status=%s score=%s coverage=%s",
        scan_id,
        assessment["overall"]["status"],
        assessment["overall"]["score"],
        assessment["overall"]["coverage_percent"],
    )
    return outputs


def _build_formal_pdf(work: Path, scan_id: int, assessment: dict, measures: dict) -> str | None:
    """Generate the PDF from the exact formal assessment persisted by the pipeline."""
    from pipeline.report_composer import compose_report

    composed = compose_report(
        title=f"扫描 {scan_id}",
        score=(assessment.get("overall") or {}).get("score"),
        risks=list(assessment.get("risks") or []),
        measures=measures,
        advice=list(assessment.get("advice") or []),
        points=None,
        out_dir=Path(work) / "report",
        risk_assessment=assessment,
    )
    if composed.status != "ok":
        logger.warning(
            "formal_pdf_partial scan_id=%s status=%s reason=%s",
            scan_id, composed.status, composed.reason,
        )
    return composed.pdf_path


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
            _stage(db, scan, "reconstructing", int(round(value)), "SLAM3R 稠密重建中")

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

    # ---- 3.5. 视频证据：恢复逐帧相机位姿（点云+视频融合测量用，与 Gaussian 开关无关）----
    pose_meta: dict | None = None
    _stage(db, scan, "cleaning", 78, "恢复视频视角位姿中")
    stage_started = time.perf_counter()
    try:
        from pipeline.gaussian_runner import run_pose_recovery

        pose_meta = run_pose_recovery(work)
        timings["pose_recovery_seconds"] = time.perf_counter() - stage_started
    except Exception as exc:  # noqa: BLE001 - 位姿恢复失败退化为纯点云测量
        logger.warning("pose_recovery_skipped scan_id=%s reason=%s", scan.id, str(exc)[:300])
        timings["pose_recovery_seconds"] = time.perf_counter() - stage_started

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

    # 定向家具识别：避免 detect_type=all 只输出窗帘/地毯等显著类别而漏掉床和柜子。
    furniture_json: Path | None = None
    try:
        furniture = spatiallm_runner.run_inference(
            post["aligned_ply"], work / "postprocess" / "layout_furniture.txt",
            detect_type="object",
            categories=[
                "bed", "multifunctional_combination_bed", "sofa", "combination_sofa",
                "chair", "dining_chair", "bar_chair", "stool", "wardrobe", "nightstand",
                "tv_cabinet", "cupboard", "sideboard", "bookcase", "coffee_table",
                "dining_table", "side_table", "desk",
            ],
            temperature=0.2, top_k=3,
        )
        furniture_json = furniture["boxes_json"]
    except RuntimeError as exc:
        logger.warning("targeted_furniture_detection_skipped scan_id=%s reason=%s", scan.id, str(exc)[:300])

    # ---- 4.25. 点云几何验证后的 2.5D 结构契约（含点云+视频融合的长度修正）----
    from pipeline.structure_builder import build_structure
    structure = build_structure(
        post["aligned_ply"], spatial["boxes_json"], work / "postprocess" / "alignment.json",
        work / "postprocess" / "structure.json",
        furniture_json,
        cameras_json=(work / "gaussian" / "cameras.json") if pose_meta else None,
        images_dir=(work / "gaussian" / "images") if pose_meta else None,
    )

    # ---- 4.3. 用户实测参考值 → 统一米/当前单位比例 → 长度结果 ----
    # 前两个参考用于求统一比例；第三个（如有）保留为独立验收，避免拿答案自测。
    from pipeline.measurement_builder import build_measurements_file
    reference_measurements = list(scan.reference_measurements or [])
    validation_keys = {
        (str(item.get("object_type")), str(item.get("dimension")))
        for item in reference_measurements[2:]
    }
    measurements = build_measurements_file(
        work / "postprocess" / "structure.json",
        work / "postprocess" / "measurements.json",
        reference_measurements,
        validation_keys=validation_keys,
        calibrated_structure_json=work / "postprocess" / "structure_calibrated.json",
    )
    # 通路与净距：家具 footprint 净距、可行走面积、门→床路径与最窄通道宽
    try:
        from pipeline.passage_builder import build_passage_metrics

        measurements = build_passage_metrics(
            post["aligned_ply"],
            work / "postprocess" / "structure_calibrated.json"
            if (work / "postprocess" / "structure_calibrated.json").is_file()
            else work / "postprocess" / "structure.json",
            work / "postprocess" / "measurements.json",
        )
    except Exception as exc:  # noqa: BLE001 - 通路失败不阻断主链
        logger.warning("passage_builder_failed scan_id=%s reason=%s", scan.id, str(exc)[:300])
    # 2.5D 结构图（中文标注 + 家具尺寸）
    try:
        from pipeline.structure_figure import render_structure_plan

        render_structure_plan(
            work / "postprocess" / "measurements.json",
            work / "postprocess" / "structure_calibrated.json"
            if (work / "postprocess" / "structure_calibrated.json").is_file()
            else work / "postprocess" / "structure.json",
            work / "postprocess" / "structure_plan.png",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("structure_figure_failed scan_id=%s reason=%s", scan.id, str(exc)[:300])

    # ---- 4.4. 已有结构化结果 → 正式空间指标与风险评估 ----
    assessment_outputs: dict | None = None
    assessment: dict | None = None
    try:
        assessment_outputs = _build_formal_assessment(work, scan.id)
        assessment = assessment_outputs["risk_payload"]
    except Exception as exc:  # noqa: BLE001 - 评估失败不应丢失重建产物
        logger.exception("formal_risk_assessment_failed scan_id=%s", scan.id)

    # ---- 4.5. Gaussian 仅保留为显式实验分支；正式双视图不依赖它 ----
    gaussian_meta: dict | None = None

    def on_gaussian_progress(_name: str, _line: str) -> None:
        pass  # 进度由扫描状态粗粒度呈现

    if os.getenv("GAUSSIAN_EXPERIMENTAL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        _stage(db, scan, "gaussian", 92, "Gaussian 实验场景重建中")
        stage_started = time.perf_counter()
        try:
            from pipeline import gaussian_runner

            gaussian_meta = gaussian_runner.run_gaussian(
                work / "slam3r" / "scene" / "preds", work,
                progress_callback=on_gaussian_progress,
            )
            timings["gaussian_seconds"] = gaussian_meta["seconds"]
        except RuntimeError as exc:
            logger.warning("gaussian_skipped scan_id=%s reason=%s", scan.id, str(exc)[:300])
            timings["gaussian_seconds"] = time.perf_counter() - stage_started

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
        "scale_status": measurements.get("scale", {}).get("status", "calibration_failed"),
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
        "structure": structure,
        "measurements": measurements,
        "alignment": post["metadata"]["alignment"],
        "scale": post["metadata"]["scale"],
        "extents_m": post["metadata"]["extents_m"],
        "points": {
            "aligned": post["metadata"]["points_aligned"],
            "preview": post["metadata"]["points_preview"],
        },
        "timings": {**timings, "total_seconds": round(time.perf_counter() - pipeline_started, 1)},
        "gaussian": {
            "available": bool(gaussian_meta),
            "views": gaussian_meta["views"] if gaussian_meta else None,
            "seconds": gaussian_meta["seconds"] if gaussian_meta else None,
        },
        "risk_assessment": assessment,
        "assessment_status": (
            assessment["overall"]["status"] if assessment else "generation_failed"
        ),
    }
    preview = {
        "viewer": f"/preview/{scan.id}",
        "manifest": f"/api/preview/{scan.id}/manifest.json",
        "ply": f"/api/preview/{scan.id}/scene.ply",
        "gaussian_ply": f"/api/preview/{scan.id}/gaussian.ply" if gaussian_meta else None,
        "layout": f"/api/preview/{scan.id}/layout.json",
        "structure": f"/api/preview/{scan.id}/structure.json",
        "measurements": f"/api/preview/{scan.id}/measurements.json",
        "spatial_metrics": f"/api/preview/{scan.id}/spatial-metrics.json" if assessment_outputs else None,
        "risk_assessment": f"/api/preview/{scan.id}/risk-assessment.json" if assessment_outputs else None,
        "backend": "slam3r",
    }
    if assessment:
        try:
            pdf_path = _build_formal_pdf(work, scan.id, assessment, measures)
            preview["pdf"] = f"/static/{scan.id}/pdf" if pdf_path else None
        except Exception as exc:  # noqa: BLE001 - PDF 失败不阻断结构化报告
            logger.exception("formal_pdf_failed scan_id=%s", scan.id)
            preview["pdf"] = None
    _upsert_report(
        db,
        scan_id=scan.id,
        score=assessment["overall"]["score"] if assessment else None,
        risks=assessment["risks"] if assessment else [],
        measures=measures,
        advice=assessment["advice"] if assessment else [],
        images=[],
        preview=preview,
        calibrated=1 if measurements.get("scale", {}).get("status") == "metric_references" else 0,
    )
    _stage(db, scan, "done", 100, "重建完成")
    logger.info(
        "pipeline_done scan_id=%s seconds=%.1f walls=%d doors=%d windows=%d objects=%d",
        scan.id, time.perf_counter() - pipeline_started,
        counts["walls"], counts["doors"], counts["windows"], counts["objects"],
    )
