"""Independently reconstruct one supplemental video without touching a scan.

The output directory is a disposable candidate.  No preview selection, report,
structure, measurement, or passage artifact is read or modified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.slam3r_runner import extract_frames, run_reconstruction


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--fps", type=float, default=4.0)
    args = parser.parse_args()

    candidate = args.candidate.resolve()
    count = extract_frames(args.video.resolve(), candidate / "frames", fps=args.fps)
    result = run_reconstruction(
        candidate / "frames",
        candidate / "slam3r",
        test_name="scene",
        fps=args.fps,
    )
    print(json.dumps({
        "status": "done",
        "video": str(args.video.resolve()),
        "frames": count,
        "ply": str(result["ply"]),
        "seconds": result["seconds"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
