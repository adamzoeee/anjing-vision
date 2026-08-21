"""Refresh semantic structure and metric measurements from existing scan artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan_id", type=int)
    parser.add_argument("--reuse-semantic", action="store_true")
    parser.add_argument("--reuse-detections", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import Scan
    from pipeline.geometry_builder import build_instance_geometry
    from pipeline.measurement_builder import build_measurements_file, build_risk_inputs
    from pipeline.passage_builder import build_passage_metrics
    from pipeline.rules import compute_score
    from pipeline.semantic_evidence import (
        _merge_openings_from_view_rays, _semantic_camera, prepare_semantic_cloud,
        run_semantic_enrichment,
    )
    from pipeline.structure_builder import build_structure
    from pipeline.structure_figure import render_structure_plan
    from pipeline.structure_review import apply_structure_review
    from scripts.build_open_top_preview import build as build_open_top_preview

    db = SessionLocal()
    scan = db.get(Scan, args.scan_id)
    if scan is None:
        raise SystemExit(f"scan {args.scan_id} not found")
    references = list(scan.reference_measurements or [])
    work = Path(get_settings().data_dir) / "work" / str(args.scan_id)
    post = work / "postprocess"
    diagnostics = work / "diagnostics"
    structure_json = post / "structure.json"
    instances_json = post / "semantic_instances.json"
    instance_points = diagnostics / "semantic_instance_points.npz"
    semantic_cloud = prepare_semantic_cloud(
        post / "scene_aligned.ply", post / "scene_semantic.ply",
    )

    if args.reuse_semantic:
        structure = json.loads(structure_json.read_text(encoding="utf-8"))
        evidence = json.loads((diagnostics / "semantic_evidence.json").read_text(encoding="utf-8"))
        cameras = json.loads((work / "gaussian" / "cameras.json").read_text(encoding="utf-8"))
        camera_by_id = {int(item.get("id", index)): item for index, item in enumerate(cameras)}
        records = []
        for view in evidence.get("views", []):
            view_id = int(view.get("view_id", -1))
            if view_id in camera_by_id:
                records.append({
                    "view_id": view_id, "camera": _semantic_camera(camera_by_id[view_id]),
                    "detections": view.get("detections", []),
                })
        _merge_openings_from_view_rays(structure, records)
        structure_json.write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
        semantic = {"status": "reused", "instances": None}
        geometry = json.loads((diagnostics / "geometry_diagnostics.json").read_text(encoding="utf-8"))
    else:
        build_structure(
            post / "scene_aligned.ply", post / "layout.json", post / "alignment.json",
            structure_json, post / "layout_furniture.json",
            cameras_json=work / "gaussian" / "cameras.json",
            images_dir=work / "gaussian" / "images",
            diagnostics_json=diagnostics / "objects.json",
        )
        semantic = run_semantic_enrichment(
            semantic_cloud, work / "gaussian" / "cameras.json",
            work / "gaussian" / "images", structure_json, diagnostics / "objects.json",
            diagnostics / "semantic_evidence.json", instances_json=instances_json,
            instance_diagnostics_json=diagnostics / "instance_diagnostics.json",
            instance_points_npz=instance_points,
            instance_observations_json=diagnostics / "instance_observations.json",
            observation_quality_json=diagnostics / "semantic_observation_quality.json",
            purified_points_npz=diagnostics / "purified_semantic_points.npz",
            saved_detections_json=(diagnostics / "semantic_evidence.json") if args.reuse_detections else None,
        )
        geometry = build_instance_geometry(
            semantic_cloud, structure_json, instances_json, instance_points,
            post / "alignment.json", diagnostics / "geometry_diagnostics.json",
        )
    review_json = post / "structure_review_v2.json"
    if not review_json.is_file():
        review_json = post / "structure_review.json"
    if review_json.is_file():
        # 点云补洞仍使用本次扫描自己的墙平面；复核数据只纠正门窗拓扑。
        # 跨扫描房间尺寸先验不能反向制造当前点云中不存在的墙面。
        model_structure_json = post / "structure_model.json"
        current_structure = json.loads(structure_json.read_text(encoding="utf-8"))
        if not current_structure.get("independent_review"):
            model_structure_json.write_text(
                json.dumps(current_structure, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        completion_structure = json.loads(
            (model_structure_json if model_structure_json.is_file() else structure_json).read_text(encoding="utf-8")
        )
        review_payload = json.loads(review_json.read_text(encoding="utf-8"))
        for key in ("doors", "windows"):
            if key in review_payload:
                completion_structure[key] = review_payload[key]
        completion_structure_json = post / "structure_completion_geometry.json"
        completion_structure_json.write_text(
            json.dumps(completion_structure, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    if review_json.is_file():
        apply_structure_review(structure_json, review_json)
    # 从整段视频保存的逐帧注册点图重新汇总真实观测，再仅移除水平顶面。
    # 不造墙/地面点，也不覆盖原始 scene_preview.ply。
    from scripts.refuse_registered_points import refuse as fuse_registered_observations

    fused = fuse_registered_observations(work)
    build_open_top_preview(
        post / fused["outputs"]["preview"], post / "scene_preview_video_fused.ply",
    )
    measurements = build_measurements_file(
        structure_json, post / "measurements.json", references,
        validation_keys=set(), calibrated_structure_json=post / "structure_calibrated.json",
        diagnostics_json=diagnostics / "measurement_diagnostics.json",
        geometry_diagnostics_json=diagnostics / "geometry_diagnostics.json",
        force_legacy_measurements=False,
    )
    measurements = build_passage_metrics(
        post / "scene_aligned.ply", post / "structure_calibrated.json",
        post / "measurements.json",
    )
    render_structure_plan(
        post / "measurements.json", post / "structure_calibrated.json",
        post / "structure_plan.png",
    )
    from pipeline.space_foundation import build_space_foundation_files

    build_space_foundation_files(
        post / "measurements.json", post / "structure_calibrated.json", post,
    )
    # 仅对正式显示副本补地面；真实几何与测量点云保持不变。墙体补面可能
    # 遮挡室内并封住门窗，因此明确关闭。
    from scripts.complete_structural_planes import complete as complete_structural_planes

    complete_structural_planes(
        post / "scene_preview_video_fused.ply",
        post / "structure_calibrated.json",
        post / "scene_preview_video_completed.ply",
        cell=0.009,
        cameras_json=work / "gaussian" / "cameras.json",
        images_dir=work / "gaussian" / "images",
        max_video_views=120,
        fill_walls=False,
    )

    risk_inputs = build_risk_inputs(measurements)
    score, assessment = compute_score(risk_inputs, include_not_evaluable=True)
    risks = assessment["risks"]
    report = scan.report
    if report is not None:
        measures = dict(report.measures or {})
        structure = json.loads(structure_json.read_text(encoding="utf-8"))
        reviewed_instances = [
            item for item in structure.get("semantic_instances", [])
            if isinstance(item, dict)
            and item.get("status") == "stable"
            and item.get("geometry_status") == "verified"
            and item.get("measurement_ready") is True
        ]
        reviewed_categories: dict[str, int] = {}
        for item in reviewed_instances:
            label = str(
                item.get("normalized_label") or item.get("label") or "object"
            )
            reviewed_categories[label] = reviewed_categories.get(label, 0) + 1
        reviewed_understanding = {
            "counts": {
                "walls": len(structure.get("walls", [])),
                "doors": len(structure.get("doors", [])),
                "windows": len(structure.get("windows", [])),
                "objects": len(reviewed_instances),
                "geometric_obstacles": len(structure.get("geometric_obstacles", [])),
            },
            "object_categories": reviewed_categories,
            "walls": structure.get("walls", []),
            "doors": structure.get("doors", []),
            "windows": structure.get("windows", []),
            "objects": reviewed_instances,
            "geometric_obstacles": structure.get("geometric_obstacles", []),
            "source": "reviewed_structure",
        }
        confidence_summary = {
            "reconstruction_status": "completed",
            "semantic_status": structure.get("semantic_pipeline_status", "unavailable"),
            "instance_status": structure.get("semantic_instance_pipeline_status", "unavailable"),
            "geometry_status": structure.get("semantic_geometry_status", "unavailable"),
            "scale_status": measurements.get("scale", {}).get("status", "failed"),
            "metric_scale_available": measurements.get("metric_scale_available", False),
            "measurement_coverage": measurements.get("measurement_coverage", {}),
            "risk_assessment_coverage": assessment.get("risk_assessment_coverage", {}),
        }
        measures.update(
            structure=structure, measurements=measurements,
            spatial_understanding=reviewed_understanding,
            reference_measurements=references, calibration_quality=measurements.get("scale", {}),
            confidence_summary=confidence_summary,
            risk_assessment=assessment,
            assessment_completeness=assessment.get("assessment_completeness", {}),
        )
        report.score = score
        report.risks = risks
        report.advice = [
            item["advice"] for item in risks
            if item.get("assessment_status") == "evaluated_risk" and item.get("advice")
        ]
        report.measures = measures
        report.calibrated = 3 if measurements.get("metric_scale_available") else 0
    db.commit()
    db.close()
    print(json.dumps({
        "scan_id": args.scan_id,
        "semantic": semantic.get("status"),
        "semantic_instances": semantic.get("instances"),
        "geometry": geometry.get("counts"),
        "measurements": measurements.get("measurement_coverage"),
        "windows": len(json.loads(structure_json.read_text(encoding="utf-8")).get("windows", [])),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
