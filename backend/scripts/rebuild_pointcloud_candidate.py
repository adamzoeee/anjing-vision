"""在独立目录重建点云候选，不覆盖现有扫描、结构、尺寸或报告。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--max-frames", type=int, default=900)
    args = parser.parse_args()

    from pipeline import slam3r_runner
    from pipeline.scene_postprocess import build_outputs

    video = args.video.resolve()
    output = args.output.resolve()
    if not video.is_file():
        raise SystemExit(f"视频不存在：{video}")
    output.mkdir(parents=True, exist_ok=True)
    marker = output / "REBUILD_RUNNING.json"
    marker.write_text(json.dumps({
        "status": "running", "video": str(video), "started_at": time.time(),
        "mode": "adaptive_keyframe", "overwrites_scan": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 质量过滤后按整段时间轴均匀保留最多900帧，绝不截断后半段。
    original_cap = slam3r_runner.MAX_INPUT_FRAMES
    slam3r_runner.MAX_INPUT_FRAMES = int(args.max_frames)
    frames = output / "frames"
    count = slam3r_runner.extract_frames(video, frames, fps=args.fps)
    slam3r_runner.MAX_INPUT_FRAMES = original_cap

    recon = slam3r_runner.run_reconstruction(
        frames, output / "slam3r", test_name="scene",
        fps=args.fps, keyframe_stride=-1,
        win_r=5, num_scene_frame=10, initial_winsize=5,
        conf_thres_l2w=12.0, conf_thres_i2p=1.5,
        num_points_save=3_000_000,
    )
    post = build_outputs(recon["ply"], output / "postprocess")
    marker.write_text(json.dumps({
        "status": "candidate_ready", "video": str(video), "frames": count,
        "mode": "adaptive_keyframe", "overwrites_scan": False,
        "reconstruction": recon["metadata"],
        "outputs": {key: str(value) for key, value in post.items() if isinstance(value, Path)},
        "finished_at": time.time(), "promotion_required": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
