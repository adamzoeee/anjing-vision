"""3DGS 训练视角选择与独立 holdout 划分。"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ViewSplit:
    train_indices: list[int]
    holdout_indices: list[int]
    diagnostics: dict


def _forward(camera: dict) -> np.ndarray:
    rotation = np.asarray(camera["R"], dtype=np.float64)
    direction = rotation.T @ np.array([0.0, 0.0, 1.0])
    return direction / max(np.linalg.norm(direction), 1e-9)


def _thumbnail(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(np.asarray(image)[..., :3], cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float64)
    small = (small - small.mean()) / max(small.std(), 1.0)
    return small.reshape(-1)


def _sharpness(image: np.ndarray) -> float:
    gray = cv2.cvtColor(np.asarray(image)[..., :3], cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _pose_features(cameras: list[dict], images: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    centers = np.asarray([camera["center"] for camera in cameras], dtype=np.float64)
    center_scale = np.percentile(np.linalg.norm(centers - np.median(centers, axis=0), axis=1), 90)
    center_scale = max(float(center_scale), 1e-6)
    positions = (centers - np.median(centers, axis=0)) / center_scale
    forwards = np.asarray([_forward(camera) for camera in cameras])
    thumbnails = np.asarray([_thumbnail(image) for image in images])
    sharpness = np.log1p(np.asarray([_sharpness(image) for image in images]))
    sharpness = (sharpness - sharpness.min()) / max(float(np.ptp(sharpness)), 1e-9)
    # 位置和观察方向主导；缩略图只用于区分同位置朝向相近但内容不同的视角。
    features = np.concatenate(
        [positions, forwards * 0.75, thumbnails * 0.08, sharpness[:, None] * 0.15], axis=1
    )
    return features, sharpness


def _farthest(features: np.ndarray, candidates: list[int], count: int, seeds: list[int]) -> list[int]:
    selected = list(dict.fromkeys(index for index in seeds if index in candidates))
    remaining = set(candidates) - set(selected)
    if not selected and remaining:
        selected.append(min(remaining))
        remaining.remove(selected[-1])
    while remaining and len(selected) < count:
        pool = np.asarray(sorted(remaining), dtype=int)
        chosen = np.asarray(selected, dtype=int)
        distances = np.linalg.norm(
            features[pool, None, :] - features[chosen][None, :, :], axis=2
        ).min(axis=1)
        index = int(pool[int(np.argmax(distances))])
        selected.append(index)
        remaining.remove(index)
    return sorted(selected)


def select_training_views(
    cameras: list[dict],
    images: list[np.ndarray],
    *,
    max_train_views: int = 80,
    holdout_ratio: float = 0.12,
    min_train_views: int = 48,
) -> ViewSplit:
    """划分未见 holdout，并以覆盖新颖性选择代表训练视角。"""
    if len(cameras) != len(images) or len(cameras) < 10:
        raise ValueError("相机与图片必须一一对应且至少包含 10 个注册视角")
    if not 0.05 <= holdout_ratio <= 0.25:
        raise ValueError("holdout_ratio 必须位于 0.05..0.25")
    count = len(cameras)
    features, sharpness = _pose_features(cameras, images)
    holdout_count = max(1, int(round(count * holdout_ratio)))
    # 避开首尾，沿时间均匀取样后再用特征最远点补齐，保证覆盖整条轨迹。
    temporal = np.linspace(1, count - 2, holdout_count, dtype=int).tolist()
    holdout = _farthest(features, list(range(1, count - 1)), holdout_count, temporal[::2])
    candidates = [index for index in range(count) if index not in set(holdout)]
    # 旧管线固定最多 80 帧；这里让长轨迹约保留 42%，同时为小场景保底 48 帧。
    # 166 个注册视角会得到约 70 个训练视角 + 20 个真正未见 holdout。
    target = min(max_train_views, len(candidates), max(min_train_views, int(round(count * 0.42))))

    forwards = np.asarray([_forward(camera) for camera in cameras])
    angular_change = np.zeros(count)
    if count > 2:
        dots = np.clip(np.sum(forwards[2:] * forwards[:-2], axis=1), -1.0, 1.0)
        angular_change[1:-1] = np.arccos(dots)
    turn_candidates = [
        int(index) for index in np.argsort(angular_change)[::-1]
        if int(index) in candidates
    ][: max(2, target // 8)]
    sharp_candidates = [
        int(index) for index in np.argsort(sharpness)[::-1]
        if int(index) in candidates
    ][: max(2, target // 10)]
    train = _farthest(features, candidates, target, [candidates[0], candidates[-1], *turn_candidates, *sharp_candidates])

    return ViewSplit(
        train_indices=train,
        holdout_indices=holdout,
        diagnostics={
            "registered_view_count": count,
            "training_view_count": len(train),
            "holdout_view_count": len(holdout),
            "training_fraction": round(len(train) / count, 4),
            "holdout_fraction": round(len(holdout) / count, 4),
            "mean_selected_sharpness": float(np.mean(sharpness[train])),
            "mean_rejected_sharpness": float(np.mean(sharpness[[i for i in range(count) if i not in train]])),
            "turn_dense_view_count": len(set(train) & set(turn_candidates)),
        },
    )
