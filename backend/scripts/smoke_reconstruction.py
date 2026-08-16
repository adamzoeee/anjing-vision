"""用单个真实视频验证 vid2scene 端到端重建（抽帧→SfM→3DGS→产物解析），不依赖数据库。

用法:
  python scripts/smoke_reconstruction.py <video.mp4> <outdir> \
      --frames 60 --steps 2000 --gaussians 400000

默认参数按 8GB 显存设计，用于快速部署自检；正式评估请用更高档参数。
旧自研管线（pipeline/sfm.py + pipeline/trainer.py）已退役，本脚本不再调用。
"""
import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pipeline.vid2scene_runner import run_reconstruction, parse_reconstruction  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--steps", type=int, default=2000)
    parser.add_argument("--gaussians", type=int, default=400_000)
    parser.add_argument(
        "--apriltag-size",
        type=float,
        default=None,
        help="提供则启用 AprilTag 米制标定（米，例如 0.09）",
    )
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"video does not exist: {args.video}")
    args.output.mkdir(parents=True, exist_ok=True)

    outputs = run_reconstruction(
        args.video,
        args.output,
        target_framecount=args.frames,
        training_num_steps=args.steps,
        max_gaussians=args.gaussians,
        apriltag_enabled=args.apriltag_size is not None,
        apriltag_size_m=args.apriltag_size or 0.09,
    )
    reconstruction = parse_reconstruction(args.output)
    cameras = reconstruction["cameras"]
    splat = reconstruction["gaussian"]
    summary = {
        "video": str(args.video),
        "seconds": outputs["seconds"],
        "registered_frames": len(cameras),
        "points3D": len(reconstruction["points3D"]),
        "gaussian_count": len(splat["means"]),
        "metric_scale_status": reconstruction["metric_scale_status"],
        "metric_calibration": reconstruction["metric_calibration"],
        "outputs": {
            "sfm_dir": str(outputs["sfm_dir"]),
            "splat_ply": str(outputs["splat_ply"]),
        },
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
