"""对已有扫描结果重建测量 JSON；不会运行 SLAM3R/SpatialLM/Gaussian。"""
import argparse
import json
import sqlite3
from pathlib import Path

from pipeline.measurement_builder import build_measurements_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan_id", type=int)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--database", type=Path, default=Path("anjing-local.db"))
    parser.add_argument("--validation", action="append", default=[], help="object_type:dimension")
    args = parser.parse_args()
    with sqlite3.connect(args.database) as db:
        row = db.execute("select reference_measurements from scans where id=?", (args.scan_id,)).fetchone()
    if row is None:
        raise SystemExit(f"scan {args.scan_id} not found")
    references = json.loads(row[0] or "[]")
    validation_keys = {tuple(value.split(":", 1)) for value in args.validation}
    post = args.data_dir / "work" / str(args.scan_id) / "postprocess"
    result = build_measurements_file(post / "structure.json", post / "measurements.json", references,
                                     validation_keys=validation_keys,
                                     calibrated_structure_json=post / "structure_calibrated.json")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
