"""Assemble completed room scans into a project-level, door-connected scan."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np

from .routers.preview import _selected_preview_ply


def _point(value, length=3):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError("空间几何坐标不完整")
    result = [float(part) for part in value]
    if not all(math.isfinite(part) for part in result):
        raise ValueError("空间几何坐标无效")
    return result


def _rotate(xy, angle):
    c, s = math.cos(angle), math.sin(angle)
    return [c * xy[0] - s * xy[1], s * xy[0] + c * xy[1]]


def _distance_to_edge(point, a, b):
    edge = [b[i] - a[i] for i in range(2)]
    length_sq = sum(value * value for value in edge)
    if length_sq < 1e-8:
        return math.inf, [0.0, 0.0]
    t = max(0.0, min(1.0, sum((point[i] - a[i]) * edge[i] for i in range(2)) / length_sq))
    nearest = [a[i] + t * edge[i] for i in range(2)]
    return math.dist(point[:2], nearest), [value / math.sqrt(length_sq) for value in edge]


def read_room(scan, data_dir: str) -> dict:
    work = Path(data_dir) / "work" / str(scan.id)
    post = work / "postprocess"
    if (post / "frame_calibration.json").is_file():
        raise ValueError("此房间使用单独的框架校准，需先将点云与结构统一到米制坐标后再拼接")
    structure_path = next((p for p in (post / "structure_calibrated.json", post / "structure.json") if p.is_file()), None)
    cloud_path = _selected_preview_ply(work)
    if structure_path is None or not cloud_path.is_file():
        raise ValueError("缺少房间点云或结构识别结果")
    source = json.loads(structure_path.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ValueError("房间结构文件格式无效")
    if source.get("coordinate_unit") not in ("meters", "m") or source.get("z_up") is not True:
        raise ValueError("房间结构必须采用米制、Z 轴向上的坐标")
    cloud_scale = float((source.get("measurement_scale") or {}).get("applied") or 1.0)
    if not math.isfinite(cloud_scale) or cloud_scale <= 0:
        raise ValueError("房间比例标定无效")
    polygon = source.get("room", {}).get("floor_polygon")
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError("缺少房间地面轮廓")
    floor = [_point(vertex) for vertex in polygon]
    centroid = [sum(p[i] for p in floor) / len(floor) for i in range(2)]
    doors = []
    for index, item in enumerate(source.get("doors") or [], 1):
        if not isinstance(item, dict):
            continue
        center, size = _point(item.get("center")), _point(item.get("size"))
        if not (0.3 <= size[0] <= 3.0 and 1.2 <= size[2] <= 3.5):
            continue
        yaw = math.radians(float(item.get("rotation_z_deg") or 0))
        if not math.isfinite(yaw):
            continue
        tangent = _rotate([1, 0], yaw)
        closest, edge_tangent = min((_distance_to_edge(center, floor[i], floor[(i + 1) % len(floor)])
                                     for i in range(len(floor))), key=lambda entry: entry[0])
        if closest > 0.35 or abs(sum(tangent[i] * edge_tangent[i] for i in range(2))) < 0.8:
            continue
        normal = [-tangent[1], tangent[0]]
        to_door = [center[i] - centroid[i] for i in range(2)]
        if abs(sum(normal[i] * to_door[i] for i in range(2))) < 0.15:
            continue
        if sum(normal[i] * to_door[i] for i in range(2)) < 0:
            normal = [-normal[0], -normal[1]]
        side = ("东侧" if normal[0] > 0 else "西侧") if abs(normal[0]) >= abs(normal[1]) else ("北侧" if normal[1] > 0 else "南侧")
        doors.append({"id": f"door_{index:02d}", "center": center, "normal": normal,
                      "side": side, "width_m": size[0], "height_m": size[2]})
    return {"id": str(scan.id), "scan_id": scan.id, "label": f"房间 #{scan.id}",
            "structure": source, "cloud_path": cloud_path, "cloud_scale": cloud_scale,
            "floor": floor, "doors": doors}


def room_catalog(scans, data_dir: str) -> list[dict]:
    result = []
    for scan in scans:
        if scan.capture_type not in ("video", "photos") or scan.status != "done":
            continue
        try:
            room = read_room(scan, data_dir)
            result.append({"scan_id": scan.id, "label": room["label"], "ready": bool(room["doors"]),
                           "doors": room["doors"], "reason": "" if room["doors"] else "未识别到可用于配对的门"})
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            result.append({"scan_id": scan.id, "label": f"房间 #{scan.id}", "ready": False,
                           "doors": [], "reason": str(exc)})
    return result


def solve_assembly(rooms: list[dict], links: list[dict]) -> dict:
    if len(rooms) < 2:
        raise ValueError("至少需要两个已完成的房间 Scan")
    by_id = {room["id"]: room for room in rooms}
    if len(by_id) != len(rooms):
        raise ValueError("房间不能重复")
    if not links:
        raise ValueError("请至少配对一组门")
    doors = {(room["id"], door["id"]): door for room in rooms for door in room["doors"]}
    occupied = set()
    connections = []
    for index, link in enumerate(links, 1):
        a = (str(link["scan_a"]), str(link["door_a"]))
        b = (str(link["scan_b"]), str(link["door_b"]))
        if a[0] == b[0] or a not in doors or b not in doors:
            raise ValueError(f"第 {index} 组门必须来自两个不同的所选房间")
        if a in occupied or b in occupied:
            raise ValueError("一扇门只能配对一次")
        occupied.update((a, b))
        da, db = doors[a], doors[b]
        if abs(da["width_m"] - db["width_m"]) > 0.2 or abs(da["height_m"] - db["height_m"]) > 0.25:
            raise ValueError(f"第 {index} 组门的宽度或高度不匹配")
        connections.append({"id": f"D{index:02d}", "rooms": [a[0], b[0]],
                            "doors": [{"id": a[1], "room_id": a[0]}, {"id": b[1], "room_id": b[0]}],
                            "width_m": (da["width_m"] + db["width_m"]) / 2,
                            "height_m": (da["height_m"] + db["height_m"]) / 2})
    poses = {rooms[0]["id"]: {"rotation_z_deg": 0.0, "translation_m": [0.0, 0.0, 0.0]}}
    while len(poses) < len(rooms):
        changed = False
        for link in connections:
            for first, second in ((0, 1), (1, 0)):
                a, b = link["doors"][first], link["doors"][second]
                if a["room_id"] not in poses or b["room_id"] in poses:
                    continue
                known = doors[(a["room_id"], a["id"])]
                unknown = doors[(b["room_id"], b["id"])]
                pose = poses[a["room_id"]]
                known_angle = math.radians(pose["rotation_z_deg"])
                outward = _rotate(known["normal"], known_angle)
                target = [-outward[0], -outward[1]]
                angle = math.atan2(target[1], target[0]) - math.atan2(unknown["normal"][1], unknown["normal"][0])
                angle = math.atan2(math.sin(angle), math.cos(angle))
                known_xy = _rotate(known["center"][:2], known_angle)
                unknown_xy = _rotate(unknown["center"][:2], angle)
                translation = [known_xy[i] + pose["translation_m"][i] - unknown_xy[i] for i in range(2)]
                translation.append(known["center"][2] + pose["translation_m"][2] - unknown["center"][2])
                poses[b["room_id"]] = {"rotation_z_deg": math.degrees(angle), "translation_m": translation}
                changed = True
        if not changed:
            raise ValueError("门连接图未覆盖所有所选房间")
    for link in connections:
        placed = []
        facing = []
        for endpoint in link["doors"]:
            door = doors[(endpoint["room_id"], endpoint["id"])]
            pose = poses[endpoint["room_id"]]
            angle = math.radians(pose["rotation_z_deg"])
            xy = _rotate(door["center"][:2], angle)
            placed.append([xy[0] + pose["translation_m"][0], xy[1] + pose["translation_m"][1],
                           door["center"][2] + pose["translation_m"][2]])
            facing.append(_rotate(door["normal"], angle))
        if math.dist(placed[0], placed[1]) > 0.12 or math.dist(facing[0], [-v for v in facing[1]]) > 0.12:
            raise ValueError("门配对形成冲突的房间位置，请核对对应关系")
        floor_levels = [min(point[2] for point in by_id[endpoint["room_id"]]["floor"])
                        + poses[endpoint["room_id"]]["translation_m"][2] for endpoint in link["doors"]]
        if abs(floor_levels[0] - floor_levels[1]) > 0.2:
            raise ValueError("门两侧地面高度不一致；目前仅支持同一楼层的房间拼接")
        link["center"] = [(placed[0][i] + placed[1][i]) / 2 for i in range(3)]
    room_rows = []
    all_points = []
    for room in rooms:
        pose = poses[room["id"]]
        angle = math.radians(pose["rotation_z_deg"])
        floor = [transform_point(point, pose) for point in room["floor"]]
        low = [min(p[i] for p in floor) for i in range(3)]
        high = [max(p[i] for p in floor) for i in range(3)]
        height = float(room["structure"].get("room", {}).get("height_m") or 2.7)
        room_rows.append({"id": room["id"], "scan_id": room["scan_id"], "label": room["label"],
                          "origin": low, "size": [high[0] - low[0], high[1] - low[1], height],
                          "door_ids": [door["id"] for door in room["doors"]], "floor_polygon": floor})
        all_points.extend(floor)
        all_points.extend([[p[0], p[1], p[2] + height] for p in floor])
    return {"schema_version": 2, "source": "room_scans", "unit": "m", "up_axis": "z",
            "rooms": room_rows, "connections": connections, "transforms": poses,
            "bounds_m": {"min": [min(p[i] for p in all_points) for i in range(3)],
                         "max": [max(p[i] for p in all_points) for i in range(3)]}}


def transform_point(point, pose):
    xy = _rotate(point[:2], math.radians(pose["rotation_z_deg"]))
    return [xy[0] + pose["translation_m"][0], xy[1] + pose["translation_m"][1],
            point[2] + pose["translation_m"][2]]


def write_assembly(work: Path, rooms: list[dict], graph: dict) -> None:
    import open3d as o3d

    post = work / "postprocess"
    post.mkdir(parents=True, exist_ok=False)
    clouds = []
    structure = {"schema_version": 1, "coordinate_unit": "meters", "z_up": True,
                 "source": {"room_scan_ids": [room["scan_id"] for room in rooms]},
                 "room": {"bounds_xy": {"min": graph["bounds_m"]["min"][:2],
                                        "max": graph["bounds_m"]["max"][:2]}},
                 "walls": [], "doors": [], "windows": [], "semantic_instances": []}
    linked = {(endpoint["room_id"], endpoint["id"]): link["id"]
              for link in graph["connections"] for endpoint in link["doors"]}
    for room in rooms:
        pose = graph["transforms"][room["id"]]
        cloud = o3d.io.read_point_cloud(str(room["cloud_path"]))
        if cloud.is_empty():
            raise ValueError(f"房间 {room['scan_id']} 点云为空")
        cloud.scale(room["cloud_scale"], center=(0, 0, 0))
        cloud.rotate(o3d.geometry.get_rotation_matrix_from_axis_angle([0, 0, math.radians(pose["rotation_z_deg"])]), center=(0, 0, 0))
        cloud.translate(pose["translation_m"])
        clouds.append(cloud)
        for kind in ("walls", "doors", "windows", "semantic_instances"):
            for index, original in enumerate(room["structure"].get(kind) or [], 1):
                if not isinstance(original, dict) or "center" not in original or "size" not in original:
                    continue
                item = copy.deepcopy(original)
                item["center"] = transform_point(_point(item["center"]), pose)
                item["rotation_z_deg"] = (float(item.get("rotation_z_deg") or 0) + pose["rotation_z_deg"]) % 360
                item["instance_id"] = (linked.get((room["id"], f"door_{index:02d}")) if kind == "doors" else None) or f"scan{room['scan_id']}_{kind}_{index}"
                item["source_scan_id"] = room["scan_id"]
                structure[kind].append(item)
    merged = o3d.geometry.PointCloud()
    for cloud in clouds:
        merged += cloud
    if not o3d.io.write_point_cloud(str(post / "scene_preview.ply"), merged, write_ascii=False):
        raise OSError("整屋点云写入失败")
    (post / "house_connections.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    (post / "structure.json").write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
