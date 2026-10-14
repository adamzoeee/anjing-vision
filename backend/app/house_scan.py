"""Import the shipped synthetic household as one authenticated scan."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from pipeline.house_connectivity import build_house_connections, build_house_structure


_BACKEND = Path(__file__).resolve().parents[1]
_DEMO = _BACKEND / "data" / "demos" / "house_demo_risk_20261013"
_RENDER = _BACKEND / "data" / "demos" / "house_render_20261014_final"


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid demo JSON: {path.name}")
    return value


def _check_ply(path: Path, expected_points: int) -> None:
    with path.open("rb") as stream:
        header = stream.read(2048).split(b"end_header\n", 1)[0]
    if b"format binary_little_endian 1.0" not in header or f"element vertex {expected_points}".encode() not in header:
        raise ValueError("Whole-house PLY does not match the delivered manifest")


def load_house_package() -> tuple[dict, dict]:
    manifest_path = _DEMO / "manifest.json"
    truth_path = _DEMO / "ground_truth.json"
    render_path = _RENDER / "render_spec.json"
    cloud_path = _DEMO / "house_complete.ply"
    manifest = _read_json(manifest_path)
    truth = _read_json(truth_path)
    render = _read_json(render_path)
    for name, path in (("manifest.json", manifest_path), ("ground_truth.json", truth_path)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if render.get("source_hashes", {}).get(name) != digest:
            raise ValueError(f"House render shell was built from another {name}")
    expected_points = int(manifest.get("complete_points") or 0)
    if expected_points <= 0:
        raise ValueError("Whole-house point count is missing")
    _check_ply(cloud_path, expected_points)
    graph = build_house_connections(manifest)
    structure = build_house_structure(graph, render, truth)
    if not structure["walls"] or not structure["doors"]:
        raise ValueError("Whole-house framework is incomplete")
    return graph, structure


def write_house_scan(work: Path, graph: dict, structure: dict) -> None:
    """Write a scan snapshot; work must be new so existing scans stay untouched."""
    postprocess = work / "postprocess"
    postprocess.mkdir(parents=True, exist_ok=False)
    write_house_cloud(postprocess / "scene_preview.ply", graph)
    (postprocess / "house_connections.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (postprocess / "structure.json").write_text(
        json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_house_cloud(destination: Path, graph: dict) -> int:
    """Merge room-local clouds using the poses solved from paired doorways."""
    import open3d as o3d

    points_by_room = []
    colors_by_room = []
    for room in graph["rooms"]:
        path = _DEMO / f"{room['id']}.ply"
        cloud = o3d.io.read_point_cloud(str(path))
        points = np.asarray(cloud.points)
        colors = np.asarray(cloud.colors)
        if len(points) != room["points"] or len(colors) != len(points):
            raise ValueError(f"Room cloud does not match manifest: {room['id']}")
        pose = graph["transforms"][room["id"]]
        angle = math.radians(pose["rotation_z_deg"])
        rotation = np.array([[math.cos(angle), -math.sin(angle), 0],
                             [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
        local_points = points - np.asarray(room["origin"])
        points_by_room.append(local_points @ rotation.T + np.asarray(pose["translation_m"]))
        colors_by_room.append(colors)
    points = np.concatenate(points_by_room)
    colors = np.concatenate(colors_by_room)
    # Two room clouds contain the same doorway frame / shared wall samples.
    _, unique_ids = np.unique(np.round(points, 7), axis=0, return_index=True)
    unique_ids.sort()
    expected = graph["complete_points"]
    if expected and len(unique_ids) != expected:
        raise ValueError(f"Door-aligned cloud has {len(unique_ids)} points; expected {expected}")
    merged = o3d.geometry.PointCloud()
    merged.points = o3d.utility.Vector3dVector(points[unique_ids])
    merged.colors = o3d.utility.Vector3dVector(colors[unique_ids])
    if not o3d.io.write_point_cloud(str(destination), merged):
        raise OSError("Unable to write door-aligned whole-house point cloud")
    return len(unique_ids)
