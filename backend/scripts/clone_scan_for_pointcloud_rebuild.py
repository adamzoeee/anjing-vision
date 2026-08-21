"""复制已验收扫描的非点云结果，新扫描仅等待点云候选替换。"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=int)
    parser.add_argument("target", type=int)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()

    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import Report, Scan

    settings = get_settings()
    data = Path(settings.data_dir)
    db = SessionLocal()
    try:
        source = db.get(Scan, args.source)
        if source is None:
            raise SystemExit(f"源扫描不存在：{args.source}")
        if db.get(Scan, args.target) is not None:
            raise SystemExit(f"目标扫描已存在：{args.target}")

        source_media = data / source.media_path
        target_media = data / "media" / str(args.target) / source_media.name
        _link_or_copy(source_media, target_media)
        target = Scan(
            id=args.target,
            project_id=source.project_id,
            status="reconstructing",
            progress=15,
            message=f"基于扫描{args.source}重新生成点云；结构与测量已迁移",
            capture_type=source.capture_type,
            media_path=str(target_media.relative_to(data)).replace("\\", "/"),
            reference_measurements=copy.deepcopy(source.reference_measurements or []),
        )
        db.add(target)
        db.flush()
        if source.report is not None:
            report = source.report
            db.add(Report(
                scan_id=args.target,
                score=report.score,
                risks=copy.deepcopy(report.risks or []),
                measures=copy.deepcopy(report.measures or {}),
                advice=copy.deepcopy(report.advice or []),
                images=copy.deepcopy(report.images or []),
                preview=copy.deepcopy(report.preview or {}),
                calibrated=report.calibrated,
            ))
        db.commit()
    finally:
        db.close()

    source_work = data / "work" / str(args.source)
    target_work = data / "work" / str(args.target)
    target_post = target_work / "postprocess"
    target_post.mkdir(parents=True, exist_ok=True)

    # 结构、尺寸、布局与图像报告原样复制。两份基线点云使用硬链接节省空间，
    # 候选晋升时通过原子替换目标路径，不会修改源扫描45。
    for source_path in (source_work / "postprocess").iterdir():
        if not source_path.is_file():
            continue
        name = source_path.name
        keep = (
            name.startswith(("structure", "layout", "measurement", "passage", "alignment"))
            or name in {"scene_preview.ply", "scene_aligned.ply", "room_frame.json"}
        )
        if not keep:
            continue
        target_path = target_post / name
        if target_path.exists():
            target_path.unlink()
        if source_path.suffix == ".ply":
            _link_or_copy(source_path, target_path)
        else:
            shutil.copy2(source_path, target_path)

    target_diag = target_work / "diagnostics"
    target_diag.mkdir(parents=True, exist_ok=True)
    source_diag = source_work / "diagnostics"
    if source_diag.is_dir():
        for source_path in source_diag.glob("*.json"):
            shutil.copy2(source_path, target_diag / source_path.name)

    (target_work / "pointcloud_rebuild.json").write_text(json.dumps({
        "source_scan": args.source,
        "target_scan": args.target,
        "candidate_dir": str(args.candidate.resolve()),
        "migration": "structure_measurements_report_exact_copy",
        "pointcloud_baseline": "hardlink_to_source_until_validated_candidate",
        "promotion_requires_alignment_to_source_coordinates": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"created": args.target, "work": str(target_work)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
