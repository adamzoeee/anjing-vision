"""为补拍短视频准备带旧扫描锚点的 SLAM3R 输入序列。

补拍片段不能直接追加到既有点云：每段视频都是新的局部坐标系。该脚本先用
ORB+单应性内点在旧扫描帧中寻找视觉锚点，再把锚点邻域放到每个补拍片段前后。
后续重建可用这些完全相同的锚点像素恢复候选坐标系到旧扫描坐标系的相似变换。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.slam3r_runner import find_ffmpeg


def _descriptor(path: Path, orb: cv2.ORB) -> tuple[list, np.ndarray | None]:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return [], None
    height, width = image.shape
    if width > 640:
        image = cv2.resize(image, (640, max(1, round(height * 640 / width))))
    return orb.detectAndCompute(image, None)


def _match_score(
    query: tuple[list, np.ndarray | None], reference: tuple[list, np.ndarray | None],
) -> tuple[int, int]:
    q_kp, q_desc = query
    r_kp, r_desc = reference
    if q_desc is None or r_desc is None or len(q_desc) < 12 or len(r_desc) < 12:
        return 0, 0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(q_desc, r_desc, k=2)
    good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < 0.72 * pair[1].distance]
    if len(good) < 8:
        return len(good), 0
    src = np.float32([q_kp[item.queryIdx].pt for item in good])
    dst = np.float32([r_kp[item.trainIdx].pt for item in good])
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    return len(good), int(mask.sum()) if mask is not None else 0


def _extract(video: Path, target: Path, fps: float) -> list[Path]:
    target.mkdir(parents=True, exist_ok=False)
    command = [
        find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
        "-vf", f"fps={fps}", "-q:v", "2", str(target / "frame_%05d.jpg"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"补拍视频抽帧失败 {video.name}: {result.stderr[-800:]}")
    frames = sorted(target.glob("frame_*.jpg"))
    if len(frames) < 8:
        raise RuntimeError(f"补拍视频有效帧过少：{video.name} ({len(frames)})")
    return frames


def prepare(
    baseline_frames: Path, supplement_dir: Path, output: Path, *, fps: float = 4.0,
    anchor_radius: int = 3, min_inliers: int = 10,
) -> dict:
    baseline = sorted(Path(baseline_frames).glob("frame_*.jpg"))
    videos = sorted(Path(supplement_dir).glob("*.mp4"))
    if not baseline or not videos:
        raise RuntimeError("旧扫描帧或补拍视频为空")
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"输出目录已存在，为保护旧候选不覆盖：{output}")
    frame_output = output / "frames"
    clip_output = output / "extracted"
    frame_output.mkdir(parents=True)
    clip_output.mkdir(parents=True)

    orb = cv2.ORB_create(nfeatures=1400, fastThreshold=8)
    baseline_desc = [_descriptor(path, orb) for path in baseline]
    mapping: list[dict] = []
    clip_diagnostics: list[dict] = []

    def append(source: Path, payload: dict) -> None:
        target = frame_output / f"frame_{len(mapping) + 1:05d}.jpg"
        shutil.copy2(source, target)
        mapping.append({"output_index": len(mapping), "output": target.name, **payload})

    for clip_id, video in enumerate(videos):
        extracted = _extract(video, clip_output / f"clip_{clip_id:02d}", fps)
        representative_ids = sorted(set([0, len(extracted) // 2, len(extracted) - 1]))
        anchors: list[dict] = []
        for rep_id in representative_ids:
            query = _descriptor(extracted[rep_id], orb)
            scores = [(*_match_score(query, candidate), index) for index, candidate in enumerate(baseline_desc)]
            good, inliers, baseline_id = max(scores, key=lambda item: (item[1], item[0]))
            anchors.append({
                "supplement_frame": rep_id, "baseline_index": baseline_id,
                "good_matches": good, "homography_inliers": inliers,
            })
        best_inliers = max(item["homography_inliers"] for item in anchors)
        if best_inliers < min_inliers:
            raise RuntimeError(
                f"补拍片段 {video.name} 无可靠旧扫描锚点：最佳单应性内点 {best_inliers} < {min_inliers}"
            )

        anchor_ids: list[int] = []
        for anchor in anchors:
            center = int(anchor["baseline_index"])
            anchor_ids.extend(range(max(0, center - anchor_radius), min(len(baseline), center + anchor_radius + 1)))
        anchor_ids = sorted(set(anchor_ids))
        for baseline_id in anchor_ids:
            append(baseline[baseline_id], {
                "kind": "anchor", "baseline_index": baseline_id, "clip_id": clip_id,
                "source": str(baseline[baseline_id]),
            })
        for supplement_id, source in enumerate(extracted):
            append(source, {
                "kind": "supplement", "clip_id": clip_id, "supplement_index": supplement_id,
                "source_video": str(video), "time_s": round(supplement_id / fps, 3),
            })
        for baseline_id in reversed(anchor_ids):
            append(baseline[baseline_id], {
                "kind": "anchor", "baseline_index": baseline_id, "clip_id": clip_id,
                "source": str(baseline[baseline_id]),
            })
        clip_diagnostics.append({
            "clip_id": clip_id, "video": str(video), "extracted_frames": len(extracted),
            "anchor_indices": anchor_ids, "matches": anchors, "best_inliers": best_inliers,
        })

    result = {
        "status": "prepared", "method": "orb_homography_anchored_supplement_sequence",
        "baseline_frames": str(Path(baseline_frames).resolve()),
        "supplement_dir": str(Path(supplement_dir).resolve()), "fps": fps,
        "output_frames": len(mapping), "anchor_frames": sum(x["kind"] == "anchor" for x in mapping),
        "supplement_frames": sum(x["kind"] == "supplement" for x in mapping),
        "min_homography_inliers": min_inliers, "clips": clip_diagnostics, "mapping": mapping,
    }
    (output / "supplement_mapping.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline_frames", type=Path)
    parser.add_argument("supplement_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--anchor-radius", type=int, default=3)
    parser.add_argument("--min-inliers", type=int, default=10)
    args = parser.parse_args()
    outcome = prepare(
        args.baseline_frames, args.supplement_dir, args.output, fps=args.fps,
        anchor_radius=args.anchor_radius, min_inliers=args.min_inliers,
    )
    print(json.dumps({key: value for key, value in outcome.items() if key != "mapping"}, ensure_ascii=False, indent=2))
