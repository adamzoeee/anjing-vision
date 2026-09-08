"""只读诊断补拍帧到原视频帧的图像/三维对应，不生成或替换点云。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from fuse_scan46_buchong2_four_regions import image_features, robust_similarity


def main(base_root: Path, candidate_root: Path) -> None:
    base_dir = base_root / "slam3r/scene/preds"
    new_dir = candidate_root / "slam3r/scene/preds"
    base_images = np.load(base_dir / "input_imgs.npy", mmap_mode="r")
    base_points = np.load(base_dir / "registered_pcds.npy", mmap_mode="r")
    base_conf = np.load(base_dir / "registered_confs.npy", mmap_mode="r")
    new_images = np.load(new_dir / "input_imgs.npy", mmap_mode="r")
    new_points = np.load(new_dir / "registered_pcds.npy", mmap_mode="r")
    new_conf = np.load(new_dir / "registered_confs.npy", mmap_mode="r")
    mapping = json.loads((candidate_root / "supplement_mapping.json").read_text(encoding="utf-8"))["mapping"]
    anchors = [(int(x["output_index"]), int(x["baseline_index"])) for x in mapping if x["kind"] == "anchor"]
    supplements = [int(x["output_index"]) for x in mapping if x["kind"] == "supplement"]
    sift = cv2.SIFT_create(nfeatures=2000, contrastThreshold=0.005, edgeThreshold=16)
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    base_features = {bid: image_features(base_images[bid], sift) for _, bid in anchors}
    for frame_id in supplements[::max(1, len(supplements) // 8)]:
        kp, desc = image_features(new_images[frame_id], sift)
        rows = []
        if desc is None:
            print(frame_id, "no descriptors")
            continue
        for _, bid in anchors:
            bkp, bdesc = base_features[bid]
            if bdesc is None:
                continue
            pairs = matcher.knnMatch(desc, bdesc, k=2)
            good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < .82 * p[1].distance]
            if len(good) < 8:
                continue
            s2 = np.float32([kp[m.queryIdx].pt for m in good])
            t2 = np.float32([bkp[m.trainIdx].pt for m in good])
            _, mask = cv2.findHomography(s2, t2, cv2.RANSAC, 4.0)
            if mask is None:
                continue
            matches = [m for m, ok in zip(good, mask.ravel().astype(bool)) if ok]
            source, target = [], []
            for m in matches:
                sx, sy = (int(round(v)) for v in kp[m.queryIdx].pt)
                tx, ty = (int(round(v)) for v in bkp[m.trainIdx].pt)
                sx, sy, tx, ty = np.clip([sx, sy, tx, ty], 0, 223)
                if min(new_conf[frame_id, sy, sx], base_conf[bid, ty, tx]) < 4:
                    continue
                source.append(new_points[frame_id, sy, sx]); target.append(base_points[bid, ty, tx])
            if len(source) >= 6:
                scale, _, _, residual = robust_similarity(np.asarray(source), np.asarray(target), iterations=3)
                rows.append((len(matches), len(source), bid, scale, float(np.median(residual)), float(np.quantile(residual, .9))))
        print("frame", frame_id, "features", len(kp), "best", sorted(rows, reverse=True)[:5])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("base_root", type=Path)
    parser.add_argument("candidate_root", type=Path)
    args = parser.parse_args()
    main(args.base_root, args.candidate_root)
