"""Synthetic, metre-scale indoor demo; independent of real scan pipelines."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path

import numpy as np

from .house_demo_assets import enriched_specs, surface_object, window_openings

DOOR_WIDTH = 0.9
DOOR_HEIGHT = 2.1
ROOMS = (
    {"id": "living_room", "label": "客厅", "origin": [0, 0, 0], "size": [6, 4, 3], "color": [0.79, 0.65, 0.48]},
    {"id": "bedroom", "label": "卧室", "origin": [2, 4, 0], "size": [4, 3.5, 3], "color": [0.52, 0.70, 0.85]},
    {"id": "kitchen", "label": "厨房", "origin": [7.5, 4, 0], "size": [3, 3, 3], "color": [0.61, 0.80, 0.61]},
    {"id": "bathroom", "label": "卫生间", "origin": [7.5, 0, 0], "size": [2.5, 2, 3], "color": [0.69, 0.64, 0.83]},
    {"id": "corridor", "label": "走廊", "origin": [6, 0, 0], "size": [1.5, 7.5, 3], "color": [0.77, 0.77, 0.73]},
)
# A single portal definition cuts BOTH adjoining walls. Coordinates are global.
PORTALS = tuple(
    {"rooms": [room, "corridor"], "axis": "x", "plane": x, "center": y,
     "width": DOOR_WIDTH, "height": DOOR_HEIGHT}
    for room, x, y in (("living_room", 6, 2), ("bedroom", 6, 5.7),
                       ("kitchen", 7.5, 5.5), ("bathroom", 7.5, 1))
)


def _grid(length: float, spacing: float) -> np.ndarray:
    count = max(1, math.ceil(length / spacing))
    return (np.arange(count) + 0.5) * length / count


def _plane(origin, axis: int, u: float, v: float, spacing: float) -> np.ndarray:
    remaining = [i for i in range(3) if i != axis]
    a, b = np.meshgrid(_grid(u, spacing), _grid(v, spacing), indexing="ij")
    points = np.tile(np.asarray(origin, dtype=float), (a.size, 1))
    points[:, remaining[0]] += a.ravel()
    points[:, remaining[1]] += b.ravel()
    return points


def _box(center, size, spacing: float) -> np.ndarray:
    origin = np.asarray(center) - np.asarray(size) / 2
    faces = []
    for axis in range(3):
        rest = [i for i in range(3) if i != axis]
        for high in (False, True):
            start = origin.copy()
            start[axis] += size[axis] if high else 0
            faces.append(_plane(start, axis, size[rest[0]], size[rest[1]], spacing))
    return np.concatenate(faces)


def furniture_parts(category: str, size) -> list[tuple[list, list]]:
    """Simple multipart geometry, NOT a recognition algorithm."""
    l, w, h = size
    if category in ("table", "desk", "side_table", "chair"):
        top = h - 0.04 if category != "chair" else h * 0.46
        parts = [([0, 0, top], [l, w, 0.08])]
        for x in (-l / 2 + 0.06, l / 2 - 0.06):
            for y in (-w / 2 + 0.06, w / 2 - 0.06):
                parts.append(([x, y, top / 2], [0.065, 0.065, top]))
        if category == "chair":
            parts.append(([0, w / 2 - 0.04, (h + top) / 2], [l, 0.08, h - top]))
        return parts
    if category in ("bed", "sofa"):
        parts = [([0, 0, h * 0.28], [l, w, h * 0.56]),
                 ([0, w / 2 - 0.06, h / 2], [l, 0.12, h])]
        if category == "sofa":
            parts += [([x, 0, h * 0.35], [0.15, w, h * 0.7]) for x in (-l / 2 + 0.075, l / 2 - 0.075)]
        return parts
    if category == "tv":
        return [([0, 0, h * 0.58], [l, 0.07, h * 0.84]),
                ([0, 0, h * 0.08], [l * 0.55, w, h * 0.16])]
    return [([0, 0, h / 2], [l, w, h])]


def furniture_specs(room_id: str) -> list[dict]:
    # Local floor-centres; only generator ground truth consumes these definitions.
    definitions = {
        "living_room": [("sofa", [1.65, 3.35], [2.4, 0.85, 0.9], 0),
                        ("table", [2.6, 2.1], [1.2, 0.65, 0.48], 0),
                        ("tv", [2, 0.35], [1.25, 0.35, 0.85], 0),
                        ("chair", [4.4, 3.1], [0.5, 0.5, 0.9], 15)],
        "bedroom": [("bed", [1.3, 2.05], [1.6, 2, 0.85], 0),
                    ("cabinet", [3.15, 2.9], [1.2, 0.55, 2.1], 0)],
        "kitchen": [("cabinet", [1.6, 2.65], [2.2, 0.55, 0.9], 0),
                    ("table", [1.9, 1.15], [1.1, 0.65, 0.75], 0),
                    ("chair", [1.9, 0.45], [0.45, 0.45, 0.85], 0)],
        "bathroom": [("cabinet", [1.8, 1.65], [0.9, 0.5, 0.85], 0)],
        "corridor": [],
    }
    return enriched_specs(room_id, [{"object": cat, "floor_center": xy, "size": size, "rotation": yaw}
                                  for cat, xy, size, yaw in definitions[room_id]])


def generate_room(room: dict, spacing: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
    if not math.isfinite(spacing) or not 0.02 <= spacing <= 0.15:
        raise ValueError("spacing must be 0.02–0.15 metres")
    origin, size = np.array(room["origin"], float), room["size"]
    pieces, colors, layers, truth = [], [], [], []

    def add(points, color, layer):
        pieces.append(points)
        colors.append(np.tile(color, (len(points), 1)))
        layers.append(np.full(len(points), layer, dtype=np.uint8))

    for axis in range(3):
        rest = [i for i in range(3) if i != axis]
        for high in (False, True):
            start = origin.copy()
            start[axis] += size[axis] if high else 0
            points = _plane(start, axis, size[rest[0]], size[rest[1]], spacing)
            for opening in window_openings(room["id"]):
                if axis != opening["axis"] or abs(start[axis] - origin[axis] - opening["plane"]) > 1e-8:
                    continue
                along = 1 - axis
                inside = ((np.abs(points[:, along] - origin[along] - opening["center"]) < opening["width"] / 2)
                          & (points[:, 2] > opening["bottom"]) & (points[:, 2] < opening["bottom"] + opening["height"]))
                points = points[~inside]
            if axis == 0:
                for door in PORTALS:
                    if room["id"] in door["rooms"] and abs(start[0] - door["plane"]) < 1e-8:
                        inside = (np.abs(points[:, 1] - door["center"]) < door["width"] / 2) & (points[:, 2] < door["height"])
                        points = points[~inside]
            ceiling = axis == 2 and high
            color = [0.87, 0.87, 0.84] if axis != 2 else ([0.92, 0.92, 0.90] if high else [0.61, 0.53, 0.42])
            add(points, color, 1 if ceiling else 0)
    # Frames are OUTSIDE the clear aperture, never across the threshold.
    for door in PORTALS:
        if room["id"] not in door["rooms"]:
            continue
        x, y, h, w = door["plane"], door["center"], door["height"], door["width"]
        for dy in (-w / 2 - 0.035, w / 2 + 0.035):
            add(_box([x, y + dy, h / 2], [0.08, 0.07, h], spacing), [0.42, 0.27, 0.16], 0)
        add(_box([x, y, h + 0.035], [0.08, w + 0.14, 0.07], spacing), [0.42, 0.27, 0.16], 0)
    palette = {"bed": [0.35, 0.53, 0.76], "sofa": [0.38, 0.52, 0.46], "table": [0.57, 0.35, 0.19],
               "chair": [0.65, 0.40, 0.22], "cabinet": [0.77, 0.71, 0.59], "tv": [0.10, 0.13, 0.18]}
    for index, item in enumerate(furniture_specs(room["id"])):
        theta = math.radians(item["rotation"])
        rotation = np.array([[math.cos(theta), -math.sin(theta), 0], [math.sin(theta), math.cos(theta), 0], [0, 0, 1]])
        floor = origin + [*item["floor_center"], item.get("elevation", 0)]
        start_index = sum(len(piece) for piece in pieces)
        sampled = surface_object(item, spacing, _box)
        if sampled is None:
            for center, dimensions in furniture_parts(item["object"], item["size"]):
                add(_box(center, dimensions, spacing) @ rotation.T + floor, palette.get(item["object"], palette["table"]), 2)
        else:
            xyz, rgb = sampled
            pieces.append(xyz @ rotation.T + floor)
            colors.append(rgb)
            layers.append(np.full(len(xyz), 2, dtype=np.uint8))
        end_index = sum(len(piece) for piece in pieces)
        truth.append({"instance_id": f'{room["id"]}_{index + 1:03d}', "room_id": room["id"],
                      "object": item["object"], "position": (floor + [0, 0, item["size"][2] / 2]).tolist(),
                      "size": item["size"], "rotation": item["rotation"], "rotation_unit": "degrees",
                      "source": "synthetic_ground_truth", "label": item.get("label", item["object"]),
                      "point_range": [start_index, end_index], "role": item.get("role", "major"),
                      "supported_by": item.get("supported_by"), "assembly": item.get("assembly")})
    return np.concatenate(pieces), np.concatenate(colors), np.concatenate(layers), truth


def _json(path: Path, value):
    # The viewer can refresh while inference is running: never expose half a JSON.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def build_demo(output: Path, *, spacing: float = 0.05) -> dict:
    """Create a NEW isolated output; refuse to overwrite scans or prior demos."""
    import open3d as o3d

    if not math.isfinite(spacing) or not 0.02 <= spacing <= 0.15:
        raise ValueError("spacing must be 0.02–0.15 metres")
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"synthetic": True, "unit": "m", "up_axis": "z", "spacing_m": spacing, "scene_version": "rich-v2",
                "portal_clear_size_m": [DOOR_WIDTH, DOOR_HEIGHT], "portals": PORTALS,
                "rooms": [], "predictions": "predictions.json"}
    if os.getenv("HOUSE_DEMO_PROFILE", "rich") in {"lived", "risk"}:
        manifest.update(scene_version="risk-v4" if os.getenv("HOUSE_DEMO_PROFILE") == "risk" else "lived-v3", layout_analysis="layout_analysis.json",
                        model_layout_analysis="model_layout_analysis.json")
    all_points, all_colors, truth = [], [], []

    def write_cloud(path, points, colors):
        cloud = o3d.geometry.PointCloud()
        cloud.points = o3d.utility.Vector3dVector(points)
        cloud.colors = o3d.utility.Vector3dVector(colors)
        if not o3d.io.write_point_cloud(str(path), cloud):
            raise OSError(f"Unable to write {path}")

    for room in ROOMS:
        points, colors, layers, objects = generate_room(room, spacing)
        rid = room["id"]
        write_cloud(output / f"{rid}.pcd", points, colors)
        write_cloud(output / f"{rid}.ply", points, colors)
        np.column_stack((points, colors)).astype("<f4").tofile(output / f"{rid}.bin")
        layers.tofile(output / f"{rid}.layers.bin")
        manifest["rooms"].append({**room, "points": len(points), "pcd": f"{rid}.pcd", "ply": f"{rid}.ply",
                                  "buffer": f"{rid}.bin", "layers": f"{rid}.layers.bin"})
        all_points.append(points)
        all_colors.append(colors)
        truth.extend(objects)
    points, colors = np.concatenate(all_points), np.concatenate(all_colors)
    # Remove coincident shared-wall/frame samples, without changing any coordinates.
    _, ids = np.unique(np.round(points, 7), axis=0, return_index=True)
    ids.sort()
    write_cloud(output / "house_complete.pcd", points[ids], colors[ids])
    write_cloud(output / "house_complete.ply", points[ids], colors[ids])
    manifest["complete_points"] = len(ids)
    manifest["ground_truth"] = "ground_truth.json"
    _json(output / "manifest.json", manifest)
    _json(output / "ground_truth.json", {"source": "synthetic_ground_truth", "objects": truth})
    _json(output / "predictions.json", {"source": "spatiallm", "unit": "m", "up_axis": "z", "rooms": {
        r["id"]: {"status": "not_run", "objects": []} for r in ROOMS}})
    if manifest["scene_version"] in {"lived-v3", "risk-v4"}:
        from .house_demo_layout import analyze_house
        _json(output / "layout_analysis.json", analyze_house(manifest, truth, "synthetic_ground_truth"))
    static = Path(__file__).resolve().parents[1] / "app/static"
    for name in ("index.html", "demo.js"):
        shutil.copyfile(static / "house_demo" / name, output / name)
    for name in ("three.min.js", "OrbitControls.js"):
        shutil.copyfile(static / "preview" / name, output / name)
    return manifest


def infer_rooms(output: Path, room_ids: list[str] | None = None, runner=None) -> dict:
    """Reuse SpatialLM serially. Never substitute generator labels on failure."""
    output = Path(output).resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not True:
        raise ValueError("Only synthetic demo manifests are supported")
    known = {room["id"] for room in ROOMS}
    requested = set(room_ids) if room_ids is not None else known - {"corridor"}
    if not requested <= known:
        raise ValueError("Unknown room ID")
    if runner is None:
        from .spatiallm_runner import run_inference
        runner = run_inference
    path = output / "predictions.json"
    predictions = json.loads(path.read_text(encoding="utf-8"))
    for rid in sorted(requested):
        predictions["rooms"][rid] = {"status": "running", "objects": []}
        _json(path, predictions)
        try:
            result = runner(output / f"{rid}.ply", output / "model" / rid / "layout.txt", detect_type="all")
            # Native SpatialLM Bbox.angle_z is radians. The existing parser names
            # this field rotation_z_deg without converting it. Use the original
            # text as evidence, not a magnitude heuristic or generator truth.
            native_angles = {}
            if "layout_txt" in result:
                text = Path(result["layout_txt"]).read_text(encoding="utf-8")
                for match in re.finditer(r"bbox_\d+\s*=\s*Bbox\(([^\n)]+)\)", text):
                    fields = [part.strip() for part in match.group(1).split(",")]
                    if len(fields) == 8:
                        key = (fields[0], *(float(v) for v in fields[1:4]),
                               *(max(float(v), 0.01) for v in fields[5:8]))
                        native_angles[key] = math.degrees(float(fields[4]))
            objects = []
            for index, item in enumerate(result["boxes"]["objects"]):
                key = (item["category"], *item["center"], *item["size"])
                if "layout_txt" in result and key not in native_angles:
                    raise ValueError("Cannot trace model rotation to native layout")
                angle = native_angles[key] if "layout_txt" in result else item.get("rotation_z_deg", 0)
                values = np.array([*item["center"], *item["size"], angle], float)
                if values.shape != (7,) or not np.isfinite(values).all() or np.any(values[3:6] <= 0):
                    raise ValueError("SpatialLM returned invalid geometry")
                objects.append({"instance_id": f"{rid}_prediction_{index + 1:03d}", "room_id": rid,
                                "object": item["category"], "position": values[:3].tolist(),
                                "size": values[3:6].tolist(), "rotation": float(values[6]),
                                "rotation_unit": "degrees", "source": "spatiallm"})
            structure = {**result["boxes"], "objects": [
                {**item, "rotation_z_deg": obj["rotation"]}
                for item, obj in zip(result["boxes"]["objects"], objects)]}
            predictions["rooms"][rid] = {"status": "done", "objects": objects,
                                          "seconds": result["seconds"], "structure": structure}
        except Exception as exc:
            predictions["rooms"][rid] = {"status": "failed", "objects": [],
                                          "error": f"{type(exc).__name__}: {exc}"}
        _json(path, predictions)
    if manifest.get("scene_version") in {"lived-v3", "risk-v4"}:
        from .house_demo_layout import analyze_house
        _json(output / "model_layout_analysis.json", analyze_house(
            manifest, [obj for room in predictions["rooms"].values() for obj in room["objects"]],
            "spatiallm_predictions", predictions["rooms"]))
    return predictions
