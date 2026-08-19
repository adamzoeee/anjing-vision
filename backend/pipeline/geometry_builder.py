"""阶段 4：对稳定 semantic instance 做 XY 最小矩形 + 独立稳健 Z 拟合。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d

from pipeline.structure_builder import LABEL_PLAUSIBILITY


@dataclass(frozen=True)
class GeometryConfig:
    min_points: int = 50
    statistical_neighbors: int = 20
    statistical_std_ratio: float = 2.0
    z_low_quantile: float = 0.02
    z_high_quantile: float = 0.98
    wall_layer_distance: float = 0.04
    floor_layer_height: float = 0.04
    minimum_horizontal_extent: float = 0.05
    minimum_vertical_extent: float = 0.03
    minimum_rectangle_area: float = 0.01
    wall_proximity: float = 0.25
    wall_snap_angle_deg: float = 8.0
    minimum_geometry_confidence: float = 0.35


DEFAULT_CONFIG = GeometryConfig()
WALL_ALIGNED_LABELS = {"bed", "cabinet", "wardrobe", "bookshelf", "desk"}


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _remove_structure_layers(
    xyz: np.ndarray, structure: dict, config: GeometryConfig,
) -> tuple[np.ndarray, dict]:
    finite = np.isfinite(xyz).all(axis=1)
    points = xyz[finite]
    finite_removed = int(len(xyz) - len(points))
    if len(points) == 0:
        return points, {
            "non_finite_removed": finite_removed, "floor_removed": 0,
            "wall_removed": 0,
        }
    floor = points[:, 2] <= config.floor_layer_height
    keep = ~floor
    bounds = structure.get("room", {}).get("bounds_xy", {})
    lo = np.asarray(bounds.get("min", []), dtype=float)
    hi = np.asarray(bounds.get("max", []), dtype=float)
    wall = np.zeros(len(points), dtype=bool)
    if lo.shape == (2,) and hi.shape == (2,):
        wall = (
            (np.abs(points[:, 0] - lo[0]) <= config.wall_layer_distance)
            | (np.abs(points[:, 0] - hi[0]) <= config.wall_layer_distance)
            | (np.abs(points[:, 1] - lo[1]) <= config.wall_layer_distance)
            | (np.abs(points[:, 1] - hi[1]) <= config.wall_layer_distance)
        )
        keep &= ~wall
    return points[keep], {
        "non_finite_removed": finite_removed,
        "floor_removed": int(floor.sum()),
        "wall_removed": int((~floor & wall).sum()),
    }


def _statistical_filter(xyz: np.ndarray, config: GeometryConfig) -> tuple[np.ndarray, int]:
    if len(xyz) < config.min_points:
        return xyz, 0
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz))
    _filtered, indices = cloud.remove_statistical_outlier(
        nb_neighbors=min(config.statistical_neighbors, len(xyz) - 1),
        std_ratio=config.statistical_std_ratio,
    )
    kept = np.asarray(indices, dtype=np.int64)
    if len(kept) < config.min_points:
        return xyz, 0
    return xyz[kept], int(len(xyz) - len(kept))


def _minimum_xy_rectangle(xy: np.ndarray) -> dict | None:
    if len(xy) < 3:
        return None
    (cx, cy), (width, height), angle = cv2.minAreaRect(np.asarray(xy, dtype=np.float32))
    # 统一让 length 为长边，yaw 表示长边方向。
    if width >= height:
        length, short = float(width), float(height)
        yaw = float(angle)
    else:
        length, short = float(height), float(width)
        yaw = float(angle + 90.0)
    yaw = ((yaw + 90.0) % 180.0) - 90.0
    corners = cv2.boxPoints(((cx, cy), (width, height), angle)).astype(float)
    return {
        "center_xy": [float(cx), float(cy)],
        "length": length,
        "width": short,
        "yaw_deg": yaw,
        "corners_xy": corners.tolist(),
        "area": float(length * short),
    }


def _angle_distance_deg(left: float, right: float) -> float:
    return abs(((left - right + 90.0) % 180.0) - 90.0)


def _snap_wall_axis(
    rectangle: dict,
    xy: np.ndarray,
    normalized_label: str,
    structure: dict,
    alignment: dict,
    config: GeometryConfig,
) -> tuple[dict, dict]:
    alignment_data = alignment.get("alignment", {})
    wall_theta = alignment_data.get("wall_theta_deg")
    reliable = wall_theta is not None and np.isfinite(float(wall_theta)) and len(structure.get("walls", [])) >= 2
    decision = {
        "applied": False, "reliable_wall_axis": bool(reliable),
        "reason": "wall_axis_unavailable" if not reliable else "not_near_or_not_aligned",
    }
    if not reliable or normalized_label not in WALL_ALIGNED_LABELS:
        return rectangle, decision
    bounds = structure.get("room", {}).get("bounds_xy", {})
    lo = np.asarray(bounds.get("min", []), dtype=float)
    hi = np.asarray(bounds.get("max", []), dtype=float)
    center = np.asarray(rectangle["center_xy"], dtype=float)
    if lo.shape != (2,) or hi.shape != (2,):
        return rectangle, decision
    near_wall = float(np.min(np.r_[center - lo, hi - center])) <= config.wall_proximity
    wall_axes = [0.0, 90.0]
    nearest = min(wall_axes, key=lambda axis: _angle_distance_deg(rectangle["yaw_deg"], axis))
    delta = _angle_distance_deg(rectangle["yaw_deg"], nearest)
    decision.update(near_wall=bool(near_wall), input_yaw_deg=rectangle["yaw_deg"],
                    nearest_wall_axis_deg=nearest, angle_delta_deg=delta)
    if near_wall and delta <= config.wall_snap_angle_deg:
        rectangle = dict(rectangle)
        snapped_yaw = nearest if nearest < 90 else -90.0
        radians = math.radians(snapped_yaw)
        long_axis = np.array([math.cos(radians), math.sin(radians)])
        short_axis = np.array([-math.sin(radians), math.cos(radians)])
        along = xy @ long_axis
        across = xy @ short_axis
        along_min, along_max = float(along.min()), float(along.max())
        across_min, across_max = float(across.min()), float(across.max())
        center_xy = (
            ((along_min + along_max) / 2) * long_axis
            + ((across_min + across_max) / 2) * short_axis
        )
        corners = [
            center_xy + sx * (along_max - along_min) / 2 * long_axis
            + sy * (across_max - across_min) / 2 * short_axis
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]
        rectangle.update(
            center_xy=center_xy.tolist(), length=along_max - along_min,
            width=across_max - across_min, yaw_deg=snapped_yaw,
            corners_xy=[corner.tolist() for corner in corners],
            area=float((along_max - along_min) * (across_max - across_min)),
        )
        decision.update(applied=True, reason="near_wall_and_geometry_axis_consistent")
    return rectangle, decision


def fit_instance_geometry(
    xyz: np.ndarray,
    instance: dict,
    structure: dict,
    alignment: dict,
    *,
    config: GeometryConfig = DEFAULT_CONFIG,
) -> tuple[dict, dict]:
    """拟合单个实例；非 stable 或证据不足时保持 bbox=null。"""
    result = dict(instance)
    result["instance_confidence"] = instance.get("geometry_confidence")
    diagnostic = {
        "instance_id": instance.get("instance_id"),
        "status": instance.get("status"),
        "input_points": int(len(xyz)),
        "filtered_points": 0,
        "outlier_removed": 0,
        "xy_rectangle": None,
        "z_range": None,
        "length": None, "width": None, "height": None, "rotation": None,
        "geometry_confidence": 0.0,
        "geometry_status": "not_evaluated",
        "reason": None,
    }
    if instance.get("status") != "stable":
        result.update(
            bbox=None, bbox_status="not_generated_low_confidence",
            geometry_status="low_confidence", geometry_confidence=0.0,
            dimensions=None, measurement_ready=False,
        )
        diagnostic.update(geometry_status="low_confidence", reason="instance_not_stable")
        return result, diagnostic

    cleaned, layer_stats = _remove_structure_layers(np.asarray(xyz, dtype=float), structure, config)
    cleaned, statistical_removed = _statistical_filter(cleaned, config)
    removed = int(len(xyz) - len(cleaned))
    diagnostic.update(
        filtered_points=int(len(cleaned)), outlier_removed=removed,
        filter_breakdown={**layer_stats, "statistical_removed": statistical_removed},
    )
    if len(cleaned) < config.min_points:
        result.update(
            bbox=None, bbox_status="not_generated_too_few_points",
            geometry_status="unknown", geometry_confidence=0.0, dimensions=None,
            measurement_ready=False,
        )
        diagnostic.update(geometry_status="unknown", reason="too_few_points_after_filter")
        return result, diagnostic

    rectangle = _minimum_xy_rectangle(cleaned[:, :2])
    if rectangle is None:
        result.update(bbox=None, bbox_status="not_generated_degenerate_xy",
                      geometry_status="unknown", geometry_confidence=0.0, dimensions=None,
                      measurement_ready=False)
        diagnostic.update(geometry_status="unknown", reason="degenerate_xy")
        return result, diagnostic
    rectangle, wall_snap = _snap_wall_axis(
        rectangle, cleaned[:, :2], str(instance.get("normalized_label", "")), structure, alignment, config,
    )
    z_bottom, z_top = np.quantile(
        cleaned[:, 2], [config.z_low_quantile, config.z_high_quantile], method="linear",
    )
    height = float(z_top - z_bottom)
    length, width = float(rectangle["length"]), float(rectangle["width"])
    diagnostic.update(
        xy_rectangle=rectangle,
        z_range={"z_bottom": float(z_bottom), "z_top": float(z_top), "method": "q02_q98"},
        length=length, width=width, height=height, rotation=float(rectangle["yaw_deg"]),
        wall_axis_snap=wall_snap,
    )
    extent_plausible = (
        length >= config.minimum_horizontal_extent
        and width >= config.minimum_horizontal_extent
        and height >= config.minimum_vertical_extent
        and rectangle["area"] >= config.minimum_rectangle_area
    )
    label_limits = LABEL_PLAUSIBILITY.get(str(instance.get("normalized_label", "")))
    label_plausible = True
    prior_support = 1.0
    if label_limits is not None:
        height_low, height_high, minimum_long = label_limits
        label_plausible = height_low <= height <= height_high and length >= minimum_long
        height_margin_scale = max((height_high - height_low) * 0.20, 0.05)
        height_margin = min(height - height_low, height_high - height)
        height_support = float(np.clip(height_margin / height_margin_scale, 0.0, 1.0))
        length_support = float(np.clip((length - minimum_long) / max(minimum_long * 0.25, 0.05), 0.0, 1.0))
        prior_support = min(height_support, length_support)
    plausible = extent_plausible and label_plausible
    diagnostic["plausibility"] = {
        "basic_extents": bool(extent_plausible),
        "semantic_label_prior": bool(label_plausible),
        "limits": list(label_limits) if label_limits is not None else None,
        "boundary_support": prior_support,
    }
    retained_ratio = len(cleaned) / max(len(xyz), 1)
    point_factor = min(len(cleaned) / 500.0, 1.0)
    retained_factor = float(np.clip(retained_ratio / 0.70, 0.0, 1.0))
    extent_factor = 1.0 if plausible else 0.0
    raw_confidence = 0.45 * point_factor + 0.35 * retained_factor + 0.20 * extent_factor
    confidence = float(round(raw_confidence * (0.60 + 0.40 * prior_support), 6))
    diagnostic["geometry_confidence"] = confidence
    if not plausible or confidence < config.minimum_geometry_confidence:
        if not extent_plausible:
            reason = "degenerate_or_incomplete_geometry"
        elif not label_plausible:
            reason = "implausible_for_semantic_label"
        else:
            reason = "geometry_confidence_too_low"
        result.update(
            bbox=None, bbox_status="not_generated_low_geometry_confidence",
            geometry_status="low_confidence", geometry_confidence=confidence, dimensions=None,
            measurement_ready=False,
        )
        diagnostic.update(geometry_status="low_confidence", reason=reason)
        return result, diagnostic

    center = [rectangle["center_xy"][0], rectangle["center_xy"][1], float((z_bottom + z_top) / 2)]
    size = [length, width, height]
    bbox = {
        "center": center, "size": size,
        "rotation_z_deg": float(rectangle["yaw_deg"]), "rotation_unit": "degrees",
    }
    result.update(
        bbox=bbox, bbox_status="verified_stage4", geometry_status="verified",
        geometry_confidence=confidence,
        dimensions={"length": length, "width": width, "height": height},
        center=center, size=size, rotation_z_deg=float(rectangle["yaw_deg"]),
        geometry_method="xy_minimum_area_rectangle+robust_z_quantiles",
        geometry_warnings=["near_semantic_plausibility_boundary"] if prior_support < 0.5 else [],
        measurement_ready=bool(confidence >= 0.75 and prior_support >= 0.5),
    )
    diagnostic.update(geometry_status="verified", reason="stable_instance_geometry_verified")
    return result, diagnostic


def build_instance_geometry(
    aligned_ply: Path,
    structure_json: Path,
    instances_json: Path,
    instance_points_npz: Path,
    alignment_json: Path,
    geometry_diagnostics_json: Path,
) -> dict:
    structure = _load_json(structure_json)
    instances_payload = _load_json(instances_json)
    alignment = _load_json(alignment_json)
    cloud = o3d.io.read_point_cloud(str(aligned_ply))
    points = np.asarray(cloud.points, dtype=float)
    point_sets = np.load(Path(instance_points_npz), allow_pickle=False)
    enriched: list[dict] = []
    diagnostics: list[dict] = []
    for instance in instances_payload.get("instances", []):
        instance_id = str(instance.get("instance_id", ""))
        ids = point_sets[instance_id] if instance_id in point_sets.files else np.empty(0, dtype=np.int64)
        valid_ids = np.asarray(ids, dtype=np.int64)
        valid_ids = valid_ids[(valid_ids >= 0) & (valid_ids < len(points))]
        fitted, diagnostic = fit_instance_geometry(points[valid_ids], instance, structure, alignment)
        enriched.append(fitted)
        diagnostics.append(diagnostic)
    instances_payload["geometry_stage"] = "stage4"
    instances_payload["instances"] = enriched
    instances_payload["counts"]["geometry_verified"] = sum(
        item.get("geometry_status") == "verified" for item in enriched
    )
    instances_payload["counts"]["geometry_unavailable"] = len(enriched) - instances_payload["counts"]["geometry_verified"]
    structure["semantic_geometry_status"] = "applied"
    structure["semantic_instances"] = [item for item in enriched if item.get("status") == "stable"]
    diagnostic_payload = {
        "schema_version": 1,
        "status": "applied",
        "method": "xy_minimum_area_rectangle+robust_z_quantiles",
        "config": DEFAULT_CONFIG.__dict__,
        "counts": instances_payload["counts"],
        "instances": diagnostics,
    }
    Path(instances_json).write_text(json.dumps(instances_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(structure_json).write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(geometry_diagnostics_json).parent.mkdir(parents=True, exist_ok=True)
    Path(geometry_diagnostics_json).write_text(
        json.dumps(diagnostic_payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return diagnostic_payload


def mark_geometry_unavailable(
    structure_json: Path,
    instances_json: Path,
    diagnostics_json: Path,
    reason: str,
) -> None:
    structure = _load_json(structure_json)
    structure["semantic_geometry_status"] = "unavailable"
    Path(structure_json).write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {
        "schema_version": 1, "status": "unavailable", "reason": reason[:300], "instances": [],
    }
    Path(diagnostics_json).parent.mkdir(parents=True, exist_ok=True)
    Path(diagnostics_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
