"""CLI：对本地房间视频跑完整管道（SLAM3R 重建 → 清理/对齐 → SpatialLM → 预览），
输出报告 JSON 到指定目录。

用法:
  python scripts/run_pipeline.py --input 房间视频.mp4 --outdir out/
"""
import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # backend/
sys.path.insert(0, str(ROOT))


def main():
    import os

    # DATA_DIR/DATABASE_URL 等相对路径始终按 backend/ 目录解析，与调用方 cwd 无关
    os.chdir(ROOT)
    os.environ.setdefault("TASK_SYNC", "true")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="房间视频文件（mp4/mov/…）")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    from app.db import Base, SessionLocal, engine
    from app.models import Organization, Project, Scan
    from app.storage import save_media_stream
    from app.tasks.pipeline_runner import run_pipeline

    src = Path(args.input)
    if not src.is_file():
        print(f"错误: 找不到视频文件 {src}")
        sys.exit(1)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    org = db.query(Organization).first() or Organization(name="本地调试")
    db.add(org)
    db.flush()
    proj = Project(org_id=org.id, name="CLI 调试项目")
    db.add(proj)
    db.flush()
    scan = Scan(project_id=proj.id, capture_type="video")
    db.add(scan)
    db.commit()

    from app.config import get_settings
    max_bytes = get_settings().max_upload_bytes
    stored = save_media_stream(scan.id, src.name, io.BytesIO(src.read_bytes()), max_bytes)
    scan.media_path = stored.path
    sid = scan.id  # commit 会 expire 全部属性，先保存主键
    db.commit()
    db.close()

    run_pipeline(sid)

    db = SessionLocal()
    scan = db.get(Scan, sid)
    print("status:", scan.status, "| progress:", scan.progress, "|", scan.message)
    if scan.report:
        out = Path(args.outdir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps({
            "score": scan.report.score,
            "risks": scan.report.risks,
            "measures": scan.report.measures,
            "advice": scan.report.advice,
            "images": scan.report.images,
            "preview": scan.report.preview,
            "calibrated": scan.report.calibrated,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {out / 'report.json'}")
    else:
        print("未生成报告")
    db.close()


if __name__ == "__main__":
    main()
