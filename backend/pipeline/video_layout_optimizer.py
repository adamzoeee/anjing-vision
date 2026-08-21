"""Video-primary 3D box placement using saved detections and camera poses.

The semantic model decides what an object is.  Its 3D placement is fitted to
all available 2D boxes by reprojection, instead of copying a noisy point-cloud
centroid or a room-specific hand-authored position.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.optimize import linear_sum_assignment
from scipy.cluster.hierarchy import fclusterdata


CANONICAL = {
    "床": "bed", "书桌": "desk", "桌子": "desk",
    "书架": "bookshelf", "柜子": "bookshelf", "衣柜": "bookshelf",
    "收纳箱": "storage_rack", "纸箱": "storage_rack",
}
INSTANCE_CANONICAL = {
    "table": "desk", "desk": "desk", "small_table": "desk",
    "bookshelf": "bookshelf", "cabinet": "bookshelf", "wardrobe": "bookshelf",
    "storage_rack": "storage_rack", "box": "storage_rack", "bed": "bed",
}


def _box_corners(center: np.ndarray, size: np.ndarray, yaw_deg: float) -> np.ndarray:
    signs = np.asarray([
        [-1, -1, -1], [-1, -1, 1], [-1, 1, -1], [-1, 1, 1],
        [1, -1, -1], [1, -1, 1], [1, 1, -1], [1, 1, 1],
    ], dtype=float)
    local = signs * size / 2.0
    angle = np.deg2rad(yaw_deg)
    rotation = np.asarray([[np.cos(angle), -np.sin(angle), 0],
                           [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    return local @ rotation.T + center


def _project_bbox(center: np.ndarray, size: np.ndarray, yaw: float, camera: dict) -> np.ndarray | None:
    corners = _box_corners(center, size, yaw)
    rotation = np.asarray(camera["rotation"], dtype=float)
    position = np.asarray(camera["position"], dtype=float)
    cam = (corners - position) @ rotation.T
    if np.count_nonzero(cam[:, 2] > 0.05) < 4:
        return None
    cam = cam[cam[:, 2] > 0.05]
    u = float(camera["fx"]) * cam[:, 0] / cam[:, 2] + float(camera["cx"])
    v = float(camera["fy"]) * cam[:, 1] / cam[:, 2] + float(camera["cy"])
    return np.asarray([u.min(), v.min(), u.max(), v.max()])


def _bbox_residual(predicted: np.ndarray | None, observed: np.ndarray, width: int, height: int) -> list[float]:
    scale = float(max(width, height))
    result: list[float] = []
    # An edge touching the image border is censored: it says the object extends
    # beyond the frame, not that the true projected box ends at that pixel.
    for index, border in ((0, 2.0), (1, 2.0), (2, width - 3.0), (3, height - 3.0)):
        if (index < 2 and observed[index] > border) or (index >= 2 and observed[index] < border):
            result.append(3.0 if predicted is None else float((predicted[index] - observed[index]) / scale))
    if not result:
        # Fully clipped detections still constrain projected centre weakly.
        if predicted is None:
            result.extend([3.0, 3.0])
        else:
            result.extend(((predicted[[0, 1]] + predicted[[2, 3]] - observed[[0, 1]] - observed[[2, 3]]) / (2 * scale)).tolist())
    return result


def _fit_track(observations: list[dict], camera_by_id: dict[int, dict], size: np.ndarray,
               anchor: np.ndarray, initial_yaw: float,
               room_min: np.ndarray, room_max: np.ndarray) -> dict:
    usable = [item for item in observations if int(item["view_id"]) in camera_by_id]
    if len(usable) < 2:
        return {"status": "insufficient", "views": len(usable)}

    def residual(params: np.ndarray) -> np.ndarray:
        fit_size = size.copy()
        fit_size[2] = size[2] * np.exp(params[3])
        center = np.asarray([params[0], params[1], fit_size[2] / 2.0])
        values: list[float] = []
        for item in usable:
            camera = camera_by_id[int(item["view_id"])]
            predicted = _project_bbox(center, fit_size, params[2], camera)
            values.extend(_bbox_residual(
                predicted, np.asarray(item["bbox"], dtype=float),
                int(camera["width"]), int(camera["height"]),
            ))
        # Weak anchor only resolves otherwise under-constrained, fully clipped views.
        values.extend(((params[:2] - anchor[:2]) / 0.20 * 0.45).tolist())
        # Height is learned from all visible top/bottom image edges, but stays
        # close to the 3D geometry estimate when views are clipped.
        values.append(float(params[3] / 0.18 * 0.35))
        return np.asarray(values)

    # SAM-to-3D centroids are the primary location evidence.  Reprojection may
    # refine them, but clipped 2D boxes must never drag an object across a room.
    lower = np.asarray([max(room_min[0] + 0.05, anchor[0] - 0.28),
                        max(room_min[1] + 0.05, anchor[1] - 0.28), -180.0,
                        np.log(0.78)])
    upper = np.asarray([min(room_max[0] - 0.05, anchor[0] + 0.28),
                        min(room_max[1] - 0.05, anchor[1] + 0.28), 180.0,
                        np.log(1.15)])
    starts = [np.asarray([anchor[0], anchor[1], angle, 0.0], dtype=float)
              for angle in (initial_yaw, 0, 90, -90, 180)]
    best = None
    for start in starts:
        fit = least_squares(residual, np.clip(start, lower, upper), bounds=(lower, upper),
                            loss="soft_l1", f_scale=0.08, max_nfev=500)
        score = float(np.mean(np.square(residual(fit.x))))
        if best is None or score < best[0]:
            best = (score, fit.x)
    assert best is not None
    return {"status": "fitted", "views": len(usable), "score": best[0],
            "center_xy": best[1][:2].tolist(), "yaw_deg": float(best[1][2]),
            "height": float(size[2] * np.exp(best[1][3])),
            "height_method": "multiview_visible_top_bottom_edges"}


def _spatial_tracks(instance_observations: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    assigned: dict[tuple[str, str], list[dict]] = {}
    seen: set[tuple[str, int, tuple[int, int]]] = set()
    for item in instance_observations.get("observations", []):
        kind = INSTANCE_CANONICAL.get(str(item.get("normalized_label")))
        center = np.asarray(item.get("centroid_3d", []), dtype=float)
        if not kind or center.shape != (3,) or int(item.get("projected_point_count", 0)) < 10:
            continue
        # DINO synonyms can share an identical SAM mask in one frame.
        identity = (kind, int(item["frame_id"]), tuple(np.round(center[:2] * 100).astype(int)))
        if identity in seen:
            continue
        seen.add(identity)
        row = {"view_id": int(item["frame_id"]), "center": center}
        instance_ids = [str(value) for value in item.get("assigned_instances", []) if value]
        if instance_ids:
            # Instance fusion has already associated this mask across views.
            # Preserve that identity: distance-only single-link clustering can
            # chain two neighbouring desks/bookshelves into one long object.
            for instance_id in instance_ids:
                assigned.setdefault((kind, instance_id), []).append(row)
        else:
            grouped.setdefault(kind, []).append(row)

    tracks: dict[str, list[dict]] = {}
    for (kind, instance_id), rows in assigned.items():
        if len(rows) < 2:
            continue
        centers = np.asarray([row["center"] for row in rows])
        tracks.setdefault(kind, []).append({
            "instance_id": instance_id,
            "anchor": np.median(centers, axis=0),
            "view_ids": sorted({row["view_id"] for row in rows}),
            "support": len(rows),
            "association": "semantic_instance_id",
        })
    for kind, rows in grouped.items():
        xy = np.asarray([row["center"][:2] for row in rows])
        threshold = 0.58 if kind in {"bed", "bookshelf"} else 0.48
        labels = fclusterdata(xy, t=threshold, criterion="distance", method="complete") if len(xy) > 1 else np.ones(1, int)
        clusters = []
        for label in sorted(set(labels.tolist())):
            members = [row for row, assigned in zip(rows, labels) if assigned == label]
            if len(members) < 2:
                continue
            centers = np.asarray([row["center"] for row in members])
            clusters.append({
                "anchor": np.median(centers, axis=0),
                "view_ids": sorted({row["view_id"] for row in members}),
                "support": len(members),
                "association": "complete_link_fallback",
            })
        tracks.setdefault(kind, []).extend(clusters)
    for kind in tracks:
        tracks[kind] = sorted(tracks[kind], key=lambda item: -item["support"])
    return tracks


def optimize_video_layout(structure_json: Path, semantic_evidence_json: Path,
                          instance_observations_json: Path, cameras_json: Path,
                          diagnostics_json: Path) -> dict:
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    evidence = json.loads(Path(semantic_evidence_json).read_text(encoding="utf-8"))
    instance_observations = json.loads(Path(instance_observations_json).read_text(encoding="utf-8"))
    cameras = json.loads(Path(cameras_json).read_text(encoding="utf-8"))
    camera_by_id = {int(item["id"]): item for item in cameras}
    room = structure["room"]["bounds_xy"]
    room_min, room_max = np.asarray(room["min"], float), np.asarray(room["max"], float)

    observations: dict[str, list[dict]] = {}
    for view in evidence.get("views", []):
        for detection in view.get("detections", []):
            canonical = CANONICAL.get(str(detection.get("label")))
            if canonical and detection.get("bbox"):
                observations.setdefault(canonical, []).append({
                    "view_id": int(view["view_id"]), "bbox": detection["bbox"],
                    "score": float(detection.get("score") or 0.0),
                })

    instances = [item for item in structure.get("semantic_instances", [])
                 if item.get("measurement_ready") and (item.get("size") or item.get("bbox", {}).get("size"))]
    by_kind: dict[str, list[dict]] = {}
    for item in instances:
        kind = INSTANCE_CANONICAL.get(str(item.get("normalized_label")))
        if kind:
            by_kind.setdefault(kind, []).append(item)

    spatial_tracks = _spatial_tracks(instance_observations)

    results = []
    for kind, candidates in by_kind.items():
        rows = observations.get(kind, [])
        tracks = spatial_tracks.get(kind, [])
        if not rows or not tracks:
            continue
        tracks = tracks[:max(len(candidates), 1)]
        candidate_by_id = {str(item.get("instance_id")): item for item in candidates}
        assignments = []
        used_candidates: set[str] = set()
        used_tracks: set[int] = set()
        for index, track in enumerate(tracks):
            instance_id = str(track.get("instance_id") or "")
            if instance_id and instance_id in candidate_by_id:
                assignments.append((candidate_by_id[instance_id], track))
                used_candidates.add(instance_id)
                used_tracks.add(index)
        remaining_candidates = [item for item in candidates if str(item.get("instance_id")) not in used_candidates]
        remaining_tracks = [track for index, track in enumerate(tracks) if index not in used_tracks]
        if remaining_candidates and remaining_tracks:
            current_xy = np.asarray([(item.get("center") or item["bbox"]["center"])[:2] for item in remaining_candidates])
            anchor_xy = np.asarray([track["anchor"][:2] for track in remaining_tracks])
            row_ids, col_ids = linear_sum_assignment(np.linalg.norm(current_xy[:, None] - anchor_xy[None, :], axis=2))
            assignments.extend((remaining_candidates[int(row)], remaining_tracks[int(col)]) for row, col in zip(row_ids, col_ids))
        for item, spatial_track in assignments:
            size = np.asarray(item.get("size") or item["bbox"]["size"], dtype=float)
            center = np.asarray(item.get("center") or item["bbox"]["center"], dtype=float)
            view_ids = set(spatial_track["view_ids"])
            track_rows = [row for row in rows if row["view_id"] in view_ids]
            fitted = _fit_track(
                track_rows, camera_by_id, size, spatial_track["anchor"],
                float(item.get("rotation_z_deg", 0.0)), room_min, room_max,
            )
            fitted.update(instance_id=item.get("instance_id"), kind=kind,
                          anchor_xy=spatial_track["anchor"][:2].tolist(),
                          view_ids=sorted(view_ids))
            if fitted["status"] == "fitted":
                size[2] = min(float(fitted["height"]), float(structure["room"].get("height_m", size[2] / 0.9)) * 0.92)
                center[:2] = fitted["center_xy"]
                center[2] = size[2] / 2.0
                item["center"] = center.tolist()
                item["rotation_z_deg"] = fitted["yaw_deg"]
                item["bbox"] = {"center": center.tolist(), "size": size.tolist(),
                                "rotation_z_deg": fitted["yaw_deg"]}
                item["geometry_method"] = "video_multiview_box_reprojection"
                item["height_method"] = fitted["height_method"]
                item["layout_status"] = "video_fitted"
            results.append(fitted)

    # A video fit is publishable only when the whole room remains physically
    # possible.  This prevents a locally plausible box from overlapping a bed
    # or blocking a door in future scans.
    from pipeline.structure_review import _validate_layout
    by_id = {str(item.get("instance_id")): item for item in structure.get("semantic_instances", [])}
    structure["layout_validation"] = _validate_layout(
        structure, by_id,
        {"layout_constraints": {"door_clearance_m": 0.60, "overlap_tolerance_m2": 0.004}},
    )
    structure["layout_source"] = "video_multiview_box_reprojection"
    Path(structure_json).write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {"status": "applied", "results": results}
    Path(diagnostics_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
