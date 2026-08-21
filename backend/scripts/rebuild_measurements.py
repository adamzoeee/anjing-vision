"""用已有点云/结构结果重建测量和2.5D结构图，不运行任何重建或训练。"""
import argparse
import json
import sqlite3
from pathlib import Path

from pipeline.measurement_builder import build_measurements_file
from pipeline.passage_builder import build_passage_metrics
from pipeline.structure_figure import render_structure_plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan_id", type=int)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--database", type=Path, default=Path("anjing-local.db"))
    parser.add_argument("--validation", action="append", default=[], help="object_type:dimension")
    args = parser.parse_args()
    with sqlite3.connect(args.database) as db:
        row = db.execute(
            "select reference_measurements from scans where id=?", (args.scan_id,)
        ).fetchone()
    if row is None:
        raise SystemExit(f"scan {args.scan_id} not found")
    references = json.loads(row[0] or "[]")
    validation_keys = (
        {tuple(value.split(":", 1)) for value in args.validation}
        if args.validation
        else {
            (str(item.get("object_type")), str(item.get("dimension")))
            for item in references[2:]
        }
    )
    work = args.data_dir / "work" / str(args.scan_id)
    post = work / "postprocess"
    calibrated = post / "structure_calibrated.json"
    result = build_measurements_file(
        post / "structure.json",
        post / "measurements.json",
        references,
        validation_keys=validation_keys,
        calibrated_structure_json=calibrated,
        diagnostics_json=work / "diagnostics" / "measurement_diagnostics.json",
        geometry_diagnostics_json=work / "diagnostics" / "geometry_diagnostics.json",
    )
    result = build_passage_metrics(
        post / "scene_aligned.ply",
        calibrated if calibrated.is_file() else post / "structure.json",
        post / "measurements.json",
    )
    render_structure_plan(
        post / "measurements.json",
        calibrated if calibrated.is_file() else post / "structure.json",
        post / "structure_plan.png",
    )
    print(json.dumps({
        "scan_id": args.scan_id,
        "metric_scale_available": result.get("metric_scale_available"),
        "scale": result.get("scale"),
        "measurement_coverage": result.get("measurement_coverage"),
        "structure_plan": str(post / "structure_plan.png"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
