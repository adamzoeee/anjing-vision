"""用单个真实视频验证 video → SFM → 3DGS → 可视化资源，不依赖数据库。"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pipeline.exporter import export_gaussian_ply, export_pointcloud, statistical_filter
from pipeline.frame_extractor import extract_frames, filter_sharp_frames
from pipeline.quality import assess_gaussians, assess_sfm
from pipeline.report_builder import build_preview_assets
from pipeline.sfm import run_sfm
from pipeline.trainer import denormalize_gaussians, normalize_scene, prepare_tensors, train_gaussians


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--frames", type=int, default=80)
    parser.add_argument("--iterations", type=int, default=500)
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"video does not exist: {args.video}")
    args.output.mkdir(parents=True, exist_ok=True)
    frames = extract_frames(args.video, args.output / "frames", target_count=args.frames)
    kept, _ = filter_sharp_frames(frames)
    clean = args.output / "frames_clean"
    shutil.rmtree(clean, ignore_errors=True)
    clean.mkdir()
    for path in kept:
        shutil.copy2(path, clean / path.name)
    sfm = run_sfm(clean, args.output / "sfm")
    sfm_quality = assess_sfm(sfm["cameras"], sfm["points3D"], len(kept), sfm.get("quality"))
    if not sfm_quality.ok:
        raise RuntimeError(sfm_quality.reason)
    cameras_by_name = {camera["name"]: camera for camera in sfm["cameras"]}
    paired = [(path, cameras_by_name[path.name]) for path in sorted(clean.glob("*.jpg")) if path.name in cameras_by_name]
    images = [np.asarray(Image.open(path).convert("RGB")) for path, _ in paired]
    cameras = [camera for _, camera in paired]
    normalized_cameras, normalized_points, transform = normalize_scene(cameras, sfm["points3D"])
    gt = prepare_tensors(normalized_cameras, images)
    colors = sfm["colors3D"].astype(np.float32) / 255.0
    gaussians = train_gaussians(gt, normalized_points, colors, num_iter=args.iterations)
    gaussians = denormalize_gaussians(gaussians, transform)
    gaussian_quality = assess_gaussians(gaussians["means"].numpy(), sfm["points3D"])
    if not gaussian_quality.ok:
        raise RuntimeError(gaussian_quality.reason)
    preview_dir = args.output / "preview"
    point_path = preview_dir / "scene.ply"
    export_pointcloud(gaussians, point_path)
    import open3d as o3d
    cloud = statistical_filter(o3d.io.read_point_cloud(str(point_path)))
    o3d.io.write_point_cloud(str(point_path), cloud)
    gaussian_name = "scene_gaussian.ply"
    export_gaussian_ply(gaussians, preview_dir / gaussian_name)
    manifest = build_preview_assets(
        np.asarray(cloud.points),
        preview_dir,
        title=args.video.stem,
        colors=np.asarray(cloud.colors),
        gaussian_filename=gaussian_name,
        cameras=cameras,
        image_shapes=[image.shape[:2] for image in images],
        quality={"sfm": sfm_quality.metrics, "gaussian": gaussian_quality.metrics},
    )
    summary = {
        "video": str(args.video),
        "kept_frames": len(kept),
        "registered_frames": len(cameras),
        "manifest": manifest,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
