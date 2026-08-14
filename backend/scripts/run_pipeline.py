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
    ap.add_argument(
        "--reference",
        action="append",
        default=[],
        metavar="TYPE=DIM=METERS",
        help="参考物真实尺寸，可重复：如 --reference door=height=2.05 --reference bed=length=2.0",
    )
    args = ap.parse_args()

    import os
    os.environ.setdefault("TASK_SYNC", "true")

    from app.db import Base, SessionLocal, engine
    from app.models import Organization, Project, Scan
    from app.storage import save_media_stream
    from app.tasks.pipeline_runner import run_pipeline

    references = []
    for item in args.reference:
        parts = item.split("=", 2)
        if len(parts) != 3:
            print(f"错误: --reference 格式应为 TYPE=DIM=METERS，收到: {item}")
            sys.exit(1)
        try:
            meters = float(parts[2])
        except ValueError:
            print(f"错误: 米数无效: {parts[2]}")
            sys.exit(1)
        references.append({
            "object_type": parts[0],
            "dimension": parts[1],
            "meters": meters,
        })

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
    scan.reference_measurements = references
    db.add(scan)
    db.commit()
    if src.is_file():
        import io
        from app.config import get_settings
        max_bytes = get_settings().max_upload_bytes
        stored = save_media_stream(
            scan.id, src.name, io.BytesIO(src.read_bytes()), max_bytes
        )
        media = stored.path
    else:
        # 照片目录：逐张复制到 media/<scan_id>/，media_path 指向目录，
        # 触发 pipeline_runner 的 src.is_dir() 分支（直接以图片为帧）。
        import io
        from app.config import get_settings
        max_bytes = get_settings().max_upload_bytes
        files = (sorted(src.glob("*.jpg")) + sorted(src.glob("*.jpeg"))
                 + sorted(src.glob("*.JPG")))
        if not files:
            print(f"错误: 目录 {src} 中没有 jpg/jpeg/JPG 图片")
            sys.exit(1)
        media = None
        for f in files:
            stored = save_media_stream(
                scan.id, f.name, io.BytesIO(f.read_bytes()), max_bytes
            )
            media = str(Path(stored.path).parent)
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
