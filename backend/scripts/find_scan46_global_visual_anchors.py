"""Find the original-video frames that truly overlap one supplement clip."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


def features(image, sift):
    gray = cv2.cvtColor(np.asarray(image, np.uint8), cv2.COLOR_RGB2GRAY)
    return sift.detectAndCompute(gray, None)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("base_root", type=Path)
    p.add_argument("candidate_root", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    base = np.load(args.base_root / "slam3r/scene/preds/input_imgs.npy", mmap_mode="r")
    new = np.load(args.candidate_root / "slam3r/scene/preds/input_imgs.npy", mmap_mode="r")
    mapping_path = args.candidate_root / "supplement_mapping.json"
    if mapping_path.is_file():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))["mapping"]
        ids = [int(x["output_index"]) for x in mapping if x["kind"] == "supplement"]
    else:
        # A newly recorded local video is reconstructed independently and has
        # no synthetic anchor frames.  Search its own timeline uniformly.
        ids = list(range(len(new)))
    query_ids = sorted({ids[round(i * (len(ids) - 1) / 8)] for i in range(1, 8)})
    sift = cv2.SIFT_create(nfeatures=1800, contrastThreshold=.006, edgeThreshold=16)
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    base_features = {i: features(base[i], sift) for i in range(0, len(base), 3)}
    scores = {}
    for qid in query_ids:
        qkp, qd = features(new[qid], sift)
        if qd is None:
            continue
        for bid, (bkp, bd) in base_features.items():
            if bd is None:
                continue
            pairs = matcher.knnMatch(qd, bd, k=2)
            good = [x[0] for x in pairs if len(x) == 2 and x[0].distance < .76*x[1].distance]
            if len(good) < 6:
                continue
            src = np.float32([qkp[m.queryIdx].pt for m in good])
            dst = np.float32([bkp[m.trainIdx].pt for m in good])
            _, keep = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
            if keep is None:
                continue
            count = int(keep.sum())
            scores[bid] = max(scores.get(bid, 0), count)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:18]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps({"queries": query_ids, "ranked": ranked}, indent=2), encoding="utf-8")
    tile = 224
    sheet = Image.new("RGB", (tile*6, (tile+24)*3), (15, 15, 15))
    draw = ImageDraw.Draw(sheet)
    for n, (bid, score) in enumerate(ranked):
        x, y = (n%6)*tile, (n//6)*(tile+24)
        sheet.paste(Image.fromarray(np.asarray(base[bid], np.uint8)), (x, y))
        draw.text((x+4, y+224), f"{bid} inliers={score}", fill="white")
    sheet.save(args.output)
    print(json.dumps({"queries": query_ids, "ranked": ranked}, indent=2))


if __name__ == "__main__":
    main()
