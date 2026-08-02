"""语义分割：SAM 实例分割 + GroundingDINO 文本提示检测 → 2D mask 投影到 3D 点云投票。"""
import os

import numpy as np
import torch

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
PROMPT_OBJECTS = ["纸箱", "杂物", "椅子", "桌子", "盆栽", "宠物", "水桶", "行李箱"]

_sam_predictor = None
_dino_model = None
_dino_processor = None


def _load_models():
    """懒加载 SAM + GroundingDINO。checkpoint 由 scripts/download_models.py 下载。"""
    global _sam_predictor, _dino_model, _dino_processor
    if _sam_predictor is None:
        from segment_anything import SamPredictor, sam_model_registry
        ckpt = os.environ.get("SAM_CHECKPOINT", "models/sam_vit_h_4b8939.pth")
        sam = sam_model_registry["vit_h"](checkpoint=ckpt).to(DEVICE)
        _sam_predictor = SamPredictor(sam)
    if _dino_model is None:
        from transformers import GroundingDinoForObjectDetection, GroundingDinoProcessor
        model_id = os.environ.get("DINO_MODEL", "IDEA-Research/grounding-dino-base")
        _dino_model = GroundingDinoForObjectDetection.from_pretrained(model_id).to(DEVICE)
        _dino_processor = GroundingDinoProcessor.from_pretrained(model_id)
    return _sam_predictor, _dino_model, _dino_processor


def detect_objects(rgb: np.ndarray, texts: list[str] | None = None) -> list[dict]:
    """GroundingDINO 检测文本提示物体，返回 [{bbox, label, score}]。

    bbox 为 xyxy 像素坐标（SAM predict 的 box 提示要求 xyxy 格式）。
    """
    _, dino, proc = _load_models()
    texts = texts or PROMPT_OBJECTS
    inputs = proc(images=rgb, text=texts, return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        out = dino(**inputs)
    w, h = rgb.shape[1], rgb.shape[0]
    # GroundingDINO 输出形状：[B, Nq]（logits/labels）、[B, Nq, 4]（boxes，cxcywh 归一化）
    logits, labels, boxes = out.logits[0], out.pred_labels[0], out.pred_boxes[0]
    results = []
    for i in range(logits.shape[0]):
        s = float(torch.sigmoid(logits[i]))
        if s < 0.3:
            continue
        cx, cy, bw, bh = boxes[i].tolist()
        results.append({
            "bbox": [(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h],
            "label": texts[int(labels[i])],
            "score": s,
        })
    return results


def sam_mask(rgb: np.ndarray, bbox: list[float]) -> np.ndarray:
    """SAM 以 bbox（xyxy）为提示做实例分割，返回 HxW uint8 mask。"""
    pred, _, _ = _load_models()
    pred.set_image(rgb)
    masks, _, _ = pred.predict(box=np.array(bbox, dtype=np.float32), multimask_output=False)
    return (masks[0] > 0).astype(np.uint8)


def project_mask_to_points(
    points: np.ndarray, mask: np.ndarray, K: np.ndarray, R: np.ndarray, t: np.ndarray,
) -> list[int]:
    """把 2D mask 投影到 3D：点在相机前方且投影落于 mask 内 → 命中。返回命中点索引。"""
    if len(points) == 0:
        return []
    # 世界→相机（cam = R @ world + t，与 sfm.py 的 cam_from_world 约定一致）
    cam_pts = (R @ points.T + t.reshape(3, 1)).T
    front = cam_pts[:, 2] > 0.1
    idx = np.where(front)[0]
    if len(idx) == 0:
        return []
    # 透视投影到像素
    uv = (K @ cam_pts[idx].T).T
    uv = uv[:, :2] / uv[:, 2:3]
    x, y = uv[:, 0].round().astype(int), uv[:, 1].round().astype(int)
    H, W = mask.shape
    inside = (x >= 0) & (x < W) & (y >= 0) & (y < H)
    # 命中：投影在图像内且 mask 像素非零（先按 inside 压缩再映射回 front 子空间）
    hit_local = np.where(inside)[0][mask[y[inside], x[inside]] > 0]
    return idx[hit_local].tolist()


def merge_votes(votes: dict[int, dict[str, int]]) -> dict[int, str]:
    """多关键帧投票：每点取票数最高标签。"""
    return {pid: max(cands.items(), key=lambda kv: kv[1])[0] for pid, cands in votes.items()}
