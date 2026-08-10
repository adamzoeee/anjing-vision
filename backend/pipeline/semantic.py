"""GroundingDINO + SAM 语义链路，以及 2D mask 到 3D 点云的投票辅助。"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterable

import numpy as np
import torch

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEVICE = torch.device(os.environ.get("SEMANTIC_DEVICE", "cuda" if torch.cuda.is_available() else "cpu"))
DEFAULT_BOX_THRESHOLD = 0.30
DEFAULT_TEXT_THRESHOLD = 0.25
DEFAULT_NMS_IOU_THRESHOLD = 0.50
CONTAINER_LABELS = frozenset({"纸箱", "收纳箱"})

# GroundingDINO 对英文短语的效果显著稳定于中文类别列表；输出统一映射回项目中文语义。
OBJECT_PROMPTS: tuple[tuple[str, str], ...] = (
    ("cardboard box", "纸箱"),
    ("floor clutter", "杂物"),
    ("chair", "椅子"),
    ("table", "桌子"),
    ("potted plant", "盆栽"),
    ("indoor plant", "盆栽"),
    ("pet", "宠物"),
    ("cat", "宠物"),
    ("dog", "宠物"),
    ("bucket", "水桶"),
    ("plastic bucket", "水桶"),
    ("suitcase", "行李箱"),
    ("storage box", "收纳箱"),
    ("door frame", "门"),
    ("bed", "床"),
    ("sofa", "沙发"),
    ("cabinet", "柜子"),
)
PROMPT_OBJECTS = [prompt for prompt, _label in OBJECT_PROMPTS]
CHINESE_PROMPT_OBJECTS = [label for _prompt, label in OBJECT_PROMPTS]

_sam_predictor = None
_dino_model = None
_dino_processor = None
_model_lock = threading.Lock()
_sam_inference_lock = threading.Lock()


def build_prompt(texts: Iterable[str] | None = None) -> str:
    """构造 GroundingDINO 单图文本提示；短语以句点分隔。"""
    prompts = [str(text).strip().rstrip(".") for text in (texts or PROMPT_OBJECTS)]
    prompts = [prompt for prompt in prompts if prompt]
    if not prompts:
        raise ValueError("GroundingDINO prompt must contain at least one non-empty phrase")
    return ". ".join(prompts) + "."


def normalize_prompt_label(raw_label: str) -> str | None:
    """把 Processor 返回的英文/中文短语归一为项目中文标签。"""
    normalized = " ".join(str(raw_label).lower().replace(".", " ").split())
    for prompt, label in OBJECT_PROMPTS:
        if prompt in normalized:
            return label
    for _prompt, label in OBJECT_PROMPTS:
        if label in raw_label:
            return label
    return None


def clamp_bbox(bbox: Iterable[float], image_shape: tuple[int, ...]) -> list[float] | None:
    """裁剪 xyxy 到图像边界；无面积、非有限框返回 None。"""
    if len(image_shape) < 2:
        raise ValueError("image_shape must contain height and width")
    height, width = int(image_shape[0]), int(image_shape[1])
    if height <= 0 or width <= 0:
        raise ValueError("image dimensions must be positive")
    values = np.asarray(list(bbox), dtype=np.float32)
    if values.shape != (4,) or not np.isfinite(values).all():
        return None
    x1, y1, x2, y2 = values.tolist()
    x1 = float(np.clip(x1, 0, width - 1))
    y1 = float(np.clip(y1, 0, height - 1))
    x2 = float(np.clip(x2, 0, width - 1))
    y2 = float(np.clip(y2, 0, height - 1))
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _validate_rgb(rgb: np.ndarray) -> np.ndarray:
    image = np.asarray(rgb)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("rgb image must have shape HxWx3")
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(image)


def _load_dino():
    global _dino_model, _dino_processor
    if _dino_model is None:
        with _model_lock:
            if _dino_model is None:
                from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

                model_id = os.environ.get("DINO_MODEL", "IDEA-Research/grounding-dino-base")
                cache_dir = Path(os.environ.get("HF_HOME", _BACKEND_ROOT / ".cache" / "huggingface"))
                _dino_processor = AutoProcessor.from_pretrained(model_id, cache_dir=cache_dir)
                _dino_model = AutoModelForZeroShotObjectDetection.from_pretrained(
                    model_id, cache_dir=cache_dir
                ).to(DEVICE)
                _dino_model.eval()
    return _dino_model, _dino_processor


def _load_sam():
    global _sam_predictor
    if _sam_predictor is None:
        with _model_lock:
            if _sam_predictor is None:
                from segment_anything import SamPredictor, sam_model_registry

                checkpoint = Path(
                    os.environ.get("SAM_CHECKPOINT", _BACKEND_ROOT / "models" / "sam_vit_h_4b8939.pth")
                )
                if not checkpoint.is_file():
                    raise FileNotFoundError(f"SAM checkpoint not found: {checkpoint}")
                sam = sam_model_registry["vit_h"](checkpoint=str(checkpoint)).to(DEVICE)
                sam.eval()
                _sam_predictor = SamPredictor(sam)
    return _sam_predictor


def _nms_indices(boxes: list[list[float]], scores: list[float], iou_threshold: float) -> list[int]:
    """小规模 CPU NMS，保持确定性并避免额外依赖。"""
    if not boxes:
        return []
    arr = np.asarray(boxes, dtype=np.float32)
    order = sorted(range(len(scores)), key=lambda index: (-scores[index], index))
    kept: list[int] = []
    while order:
        current = order.pop(0)
        kept.append(current)
        x1 = np.maximum(arr[current, 0], arr[order, 0]) if order else np.array([])
        y1 = np.maximum(arr[current, 1], arr[order, 1]) if order else np.array([])
        x2 = np.minimum(arr[current, 2], arr[order, 2]) if order else np.array([])
        y2 = np.minimum(arr[current, 3], arr[order, 3]) if order else np.array([])
        intersection = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        current_area = (arr[current, 2] - arr[current, 0]) * (arr[current, 3] - arr[current, 1])
        other_area = (arr[order, 2] - arr[order, 0]) * (arr[order, 3] - arr[order, 1]) if order else np.array([])
        union = np.maximum(current_area + other_area - intersection, 1e-6)
        order = [index for index, iou in zip(order, intersection / union) if iou <= iou_threshold]
    return kept


def postprocess_detections(
    raw_result: dict,
    image_shape: tuple[int, ...],
    *,
    nms_iou_threshold: float = DEFAULT_NMS_IOU_THRESHOLD,
) -> list[dict]:
    """归一化 Processor 结果、裁边并按中文语义标签做 NMS。"""
    scores = raw_result.get("scores", [])
    labels = raw_result.get("labels", [])
    boxes = raw_result.get("boxes", [])
    candidates: list[dict] = []
    for score, raw_label, box in zip(scores, labels, boxes):
        label = normalize_prompt_label(str(raw_label))
        clipped = clamp_bbox(box.tolist() if hasattr(box, "tolist") else box, image_shape)
        if label is None or clipped is None:
            continue
        candidates.append(
            {
                "bbox": clipped,
                "label": label,
                "raw_label": str(raw_label),
                "score": float(score),
            }
        )

    results: list[dict] = []
    for label in dict.fromkeys(candidate["label"] for candidate in candidates):
        group = [candidate for candidate in candidates if candidate["label"] == label]
        keep = _nms_indices(
            [candidate["bbox"] for candidate in group],
            [candidate["score"] for candidate in group],
            nms_iou_threshold,
        )
        results.extend(group[index] for index in keep)

    # GroundingDINO 常会为同一个箱体同时返回 cardboard box 与 storage box。
    # 两者在本项目中是互斥的容器语义；仅对高度重叠框做跨标签去重，避免重复 mask/计数。
    containers = [item for item in results if item["label"] in CONTAINER_LABELS]
    others = [item for item in results if item["label"] not in CONTAINER_LABELS]
    if containers:
        keep = _nms_indices(
            [item["bbox"] for item in containers],
            [item["score"] for item in containers],
            0.85,
        )
        results = others + [containers[index] for index in keep]
    return sorted(results, key=lambda item: item["score"], reverse=True)


def detect_objects(
    rgb: np.ndarray,
    texts: list[str] | None = None,
    *,
    box_threshold: float = DEFAULT_BOX_THRESHOLD,
    text_threshold: float = DEFAULT_TEXT_THRESHOLD,
    nms_iou_threshold: float = DEFAULT_NMS_IOU_THRESHOLD,
) -> list[dict]:
    """真实 GroundingDINO 检测，返回像素坐标 xyxy 与中文语义标签。"""
    image = _validate_rgb(rgb)
    dino, processor = _load_dino()
    inputs = processor(images=image, text=build_prompt(texts), return_tensors="pt").to(DEVICE)
    with torch.inference_mode():
        outputs = dino(**inputs)
    raw = processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        box_threshold=box_threshold,
        text_threshold=text_threshold,
        target_sizes=[(image.shape[0], image.shape[1])],
    )[0]
    return postprocess_detections(raw, image.shape, nms_iou_threshold=nms_iou_threshold)


def segment_detections(
    rgb: np.ndarray,
    detections: list[dict],
    *,
    batch_size: int = 16,
) -> list[dict]:
    """单次计算图像 embedding，批量把多个 xyxy 框转换为 SAM 实例 mask。"""
    image = _validate_rgb(rgb)
    prepared: list[dict] = []
    for detection in detections:
        bbox = clamp_bbox(detection.get("bbox", []), image.shape)
        if bbox is not None:
            prepared.append({**detection, "bbox": bbox})
    if not prepared:
        return []
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    predictor = _load_sam()
    device = next(predictor.model.parameters()).device
    boxes = torch.as_tensor([item["bbox"] for item in prepared], dtype=torch.float32, device=device)
    segmented: list[dict] = []
    # SamPredictor 在 set_image() 中保存当前图像 embedding 和尺寸，属于有状态对象。
    # 共享单例时必须让 set_image → predict_torch 成为一个不可交错的推理事务。
    with _sam_inference_lock, torch.inference_mode():
        predictor.set_image(image)
        transformed = predictor.transform.apply_boxes_torch(boxes, image.shape[:2])
        for start in range(0, len(prepared), batch_size):
            masks, mask_scores, _ = predictor.predict_torch(
                point_coords=None,
                point_labels=None,
                boxes=transformed[start : start + batch_size],
                multimask_output=False,
            )
            mask_batch = masks[:, 0].detach().cpu().numpy().astype(bool)
            score_batch = mask_scores[:, 0].detach().cpu().numpy()
            for item, mask, mask_score in zip(
                prepared[start : start + batch_size], mask_batch, score_batch
            ):
                if mask.shape != image.shape[:2]:
                    raise RuntimeError(
                        f"SAM mask shape {mask.shape} does not match image shape {image.shape[:2]}"
                    )
                area = int(mask.sum())
                segmented.append(
                    {
                        **item,
                        "mask": mask,
                        "mask_score": float(mask_score),
                        "mask_area_px": area,
                        "mask_area_ratio": area / float(mask.size),
                        "mask_valid": area > 0,
                    }
                )
    return segmented


def analyze_image(rgb: np.ndarray, **detection_kwargs) -> list[dict]:
    """项目真实 2D 语义入口：GroundingDINO bbox → SAM 实例 mask。"""
    detections = detect_objects(rgb, **detection_kwargs)
    return segment_detections(rgb, detections)


def sam_mask(rgb: np.ndarray, bbox: list[float]) -> np.ndarray:
    """兼容单框调用；内部仍复用统一的批量 SAM 路径。"""
    results = segment_detections(rgb, [{"bbox": bbox, "label": "unknown", "score": 1.0}])
    if not results:
        return np.zeros(np.asarray(rgb).shape[:2], dtype=np.uint8)
    return results[0]["mask"].astype(np.uint8)


def project_mask_to_points(
    points: np.ndarray,
    mask: np.ndarray,
    K: np.ndarray,
    R: np.ndarray,
    t: np.ndarray,
) -> list[int]:
    """把 2D mask 投影到 3D；相机约定为 ``cam = R @ world + t``。"""
    points = np.asarray(points, dtype=float)
    mask = np.asarray(mask)
    K = np.asarray(K, dtype=float)
    R = np.asarray(R, dtype=float)
    t = np.asarray(t, dtype=float)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points must have shape Nx3")
    if mask.ndim != 2:
        raise ValueError("mask must have shape HxW")
    if K.shape != (3, 3) or R.shape != (3, 3) or t.size != 3:
        raise ValueError("K and R must be 3x3 and t must contain three values")
    if len(points) == 0 or mask.size == 0:
        return []
    finite = np.isfinite(points).all(axis=1)
    cam_pts = (R @ points.T + t.reshape(3, 1)).T
    front = finite & np.isfinite(cam_pts).all(axis=1) & (cam_pts[:, 2] > 0.1)
    idx = np.where(front)[0]
    if len(idx) == 0:
        return []
    uvw = (K @ cam_pts[idx].T).T
    uv = uvw[:, :2] / uvw[:, 2:3]
    x, y = np.rint(uv[:, 0]).astype(int), np.rint(uv[:, 1]).astype(int)
    height, width = mask.shape
    inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
    inside_local = np.where(inside)[0]
    masked_local = inside_local[mask[y[inside], x[inside]].astype(bool)]
    if len(masked_local) == 0:
        return []

    # 2D 实例 mask 只描述当前相机看见的物体表面。旧实现把同一像素射线后方
    # 的墙、柜子和其他物体也全部投票给前景标签，导致床/桌的 3D 包围盒被背景
    # 拉长，米制参考尺寸严重失真。使用轻量 z-buffer，仅保留每个像素最前方
    # 3% 深度层内的点；相对容差适用于 COLMAP 的任意比例尺度。
    candidate_ids = idx[masked_local]
    candidate_x = x[masked_local]
    candidate_y = y[masked_local]
    candidate_depth = cam_pts[candidate_ids, 2]
    pixel_ids = candidate_y * width + candidate_x
    nearest_depth = np.full(height * width, np.inf, dtype=np.float64)
    np.minimum.at(nearest_depth, pixel_ids, candidate_depth)
    visible = candidate_depth <= nearest_depth[pixel_ids] * 1.03 + 1e-9
    return candidate_ids[visible].tolist()


def merge_votes(votes: dict[int, dict[str, int]]) -> dict[int, str]:
    """多关键帧投票；空候选忽略，平票按标签字典序保证确定性。"""
    labels: dict[int, str] = {}
    for point_id, candidates in votes.items():
        valid = [(label, int(count)) for label, count in candidates.items() if count > 0]
        if valid:
            labels[point_id] = sorted(valid, key=lambda item: (-item[1], item[0]))[0][0]
    return labels


def model_runtime_info() -> dict:
    """供本地验证脚本记录真实模型设备与缓存状态。"""
    dino_device = str(next(_dino_model.parameters()).device) if _dino_model is not None else None
    sam_device = str(next(_sam_predictor.model.parameters()).device) if _sam_predictor is not None else None
    return {
        "configured_device": str(DEVICE),
        "cuda_available": torch.cuda.is_available(),
        "dino_loaded": _dino_model is not None,
        "dino_device": dino_device,
        "dino_eval": bool(_dino_model is not None and not _dino_model.training),
        "sam_loaded": _sam_predictor is not None,
        "sam_device": sam_device,
        "sam_eval": bool(_sam_predictor is not None and not _sam_predictor.model.training),
    }
