"""把验收产物导入后端：创建组织/项目/扫描 + 报告 + 复制预览产物。

用法（backend venv）：
  python scripts/import_acceptance.py --srcdir E:\.PJs\out\room\postprocess --name 吕昊东房间
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.environ.setdefault("TASK_SYNC", "true")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--srcdir", required=True, help="postprocess 产物目录（scene_preview.ply 等）")
    ap.add_argument("--name", default="验收房间")
    args = ap.parse_args()

    from app.config import get_settings
    from app.db import Base, SessionLocal, engine
    from app.models import Organization, Project, Report, Scan

    settings = get_settings()
    src = Path(args.srcdir)
    if not (src / "scene_preview.ply").is_file():
        print(f"错误: {src} 缺少 scene_preview.ply")
        sys.exit(1)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    org = db.query(Organization).first() or Organization(name="验收机构")
    db.add(org)
    db.flush()
    proj = Project(org_id=org.id, name=args.name)
    db.add(proj)
    db.flush()
    scan = Scan(project_id=proj.id, capture_type="video", status="done", progress=100, message="验收重建完成")
    db.add(scan)
    db.commit()
    scan_id = scan.id

    work = Path(settings.data_dir) / "work" / str(scan_id)
    dest = work / "postprocess"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("scene_preview.ply", "scene_aligned.ply", "layout_boxes.json", "layout.txt", "alignment.json"):
        if (src / name).is_file():
            shutil.copy2(src / name, dest / name)

    measures = {
        "coordinate_unit": "meters",
        "scale_status": "estimated_ceiling_height",
        "reconstruction_backend": "slam3r",
        "understanding_backend": "spatiallm1.1-qwen-0.5b",
        "preview_backend": "threejs",
    }
    preview = {
        "viewer": f"/preview/{scan_id}",
        "manifest": f"/api/preview/{scan_id}/manifest.json",
        "ply": f"/api/preview/{scan_id}/scene.ply",
        "layout": f"/api/preview/{scan_id}/layout.json",
        "backend": "slam3r",
    }
    report = Report(scan_id=scan_id, score=None, risks=[], measures=measures, advice=[], images=[], preview=preview, calibrated=0)
    db.add(report)
    db.commit()
    print(f"scan_id={scan_id} viewer=/preview/{scan_id}")
    db.close()


if __name__ == "__main__":
    main()
