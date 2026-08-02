"""CLI：对本地视频/照片目录跑完整管道，输出报告 JSON 到指定目录。

用法:
  python scripts/run_pipeline.py --input clip.mp4 --outdir out/
  python scripts/run_pipeline.py --input photos_dir/ --outdir out/
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # backend/
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="视频文件或照片目录")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    import os
    os.environ.setdefault("TASK_SYNC", "true")

    from app.db import Base, SessionLocal, engine
    from app.models import Organization, Project, Scan
    from app.storage import save_media
    from app.tasks.pipeline_runner import run_pipeline

    Base.metadata.create_all(bind=engine)
    src = Path(args.input)
    db = SessionLocal()
    org = db.query(Organization).first() or Organization(name="本地调试")
    db.add(org)
    db.flush()
    proj = Project(org_id=org.id, name="CLI 调试项目")
    db.add(proj)
    db.flush()
    scan = Scan(project_id=proj.id,
                capture_type="video" if src.is_file() else "photos")
    db.add(scan)
    db.commit()
    if src.is_file():
        media = save_media(scan.id, src.name, src.read_bytes())
    else:
        # 照片目录：逐张复制到 media/<scan_id>/，media_path 指向目录，
        # 触发 pipeline_runner 的 src.is_dir() 分支（直接以图片为帧）。
        files = (sorted(src.glob("*.jpg")) + sorted(src.glob("*.jpeg"))
                 + sorted(src.glob("*.JPG")))
        if not files:
            print(f"错误: 目录 {src} 中没有 jpg/jpeg/JPG 图片")
            sys.exit(1)
        for f in files:
            save_media(scan.id, f.name, f.read_bytes())
        media = f"media/{scan.id}"
    scan.media_path = media
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
        }, ensure_ascii=False, indent=2))
        print(f"报告已写入 {out / 'report.json'}")
    else:
        print("未生成报告")
    db.close()


if __name__ == "__main__":
    main()
