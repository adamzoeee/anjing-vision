"""Validate a metre-scale house layout and pair the two sides of each door.

The shipped household scene has one global coordinate system. A portal is
accepted only when it falls on opposite faces of two rooms at the same place;
room names in the source manifest alone are not enough to establish a link.
"""
from __future__ import annotations

import math
import re


_TOL = 1e-5


def _rotate_xy(xy: list[float], quarter_turns: int) -> list[float]:
    x, y = xy
    return ([x, y], [-y, x], [-x, -y], [y, -x])[quarter_turns % 4]


def _normal(side: str) -> list[float]:
    return {"min_x": [-1, 0], "max_x": [1, 0], "min_y": [0, -1], "max_y": [0, 1]}[side]


def solve_door_transforms(rooms: list[dict], connections: list[dict]) -> dict[str, dict]:
    """Place room-local frames by matching paired doorway centers and normals.

    The first room fixes the world frame. Other rooms may be quarter-turned;
    every additional doorway must agree with the pose already solved.
    """
    anchor = rooms[0]
    transforms = {anchor["id"]: {"rotation_z_deg": 0, "translation_m": anchor["origin"][:]}}
    while len(transforms) < len(rooms):
        advanced = False
        for link in connections:
            for known, unknown in ((0, 1), (1, 0)):
                first, second = link["doors"][known], link["doors"][unknown]
                if first["room_id"] not in transforms or second["room_id"] in transforms:
                    continue
                pose = transforms[first["room_id"]]
                first_quarter = (pose["rotation_z_deg"] // 90) % 4
                outward = _rotate_xy(first["normal"], first_quarter)
                desired = [-outward[0], -outward[1]]
                quarter = next((q for q in range(4) if _rotate_xy(second["normal"], q) == desired), None)
                if quarter is None:
                    raise ValueError("Paired door normals cannot face each other")
                first_xy = _rotate_xy(first["local_center"][:2], first_quarter)
                second_xy = _rotate_xy(second["local_center"][:2], quarter)
                world_xy = [first_xy[i] + pose["translation_m"][i] for i in range(2)]
                translation = [world_xy[i] - second_xy[i] for i in range(2)]
                translation.append(first["local_center"][2] + pose["translation_m"][2] - second["local_center"][2])
                transforms[second["room_id"]] = {"rotation_z_deg": quarter * 90, "translation_m": translation}
                advanced = True
        if not advanced:
            raise ValueError("Door graph does not connect every room")

    for link in connections:
        placed = []
        normals = []
        for door in link["doors"]:
            pose = transforms[door["room_id"]]
            quarter = (pose["rotation_z_deg"] // 90) % 4
            xy = _rotate_xy(door["local_center"][:2], quarter)
            placed.append([xy[0] + pose["translation_m"][0], xy[1] + pose["translation_m"][1],
                           door["local_center"][2] + pose["translation_m"][2]])
            normals.append(_rotate_xy(door["normal"], quarter))
        if any(abs(placed[0][i] - placed[1][i]) > _TOL for i in range(3)) or any(abs(normals[0][i] + normals[1][i]) > _TOL for i in range(2)):
            raise ValueError(f"Door {link['id']} has inconsistent room transforms")
    return transforms


def _number(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _vector(value: object, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must contain three coordinates")
    return [_number(v, label) for v in value]


def build_house_connections(manifest: dict) -> dict:
    if manifest.get("synthetic") is not True or manifest.get("unit") != "m" or manifest.get("up_axis") != "z":
        raise ValueError("Only the metre-scale z-up synthetic house is supported")
    source_rooms = manifest.get("rooms")
    source_portals = manifest.get("portals")
    if not isinstance(source_rooms, list) or len(source_rooms) < 2 or not isinstance(source_portals, list):
        raise ValueError("House rooms and portals are required")

    rooms: dict[str, dict] = {}
    for item in source_rooms:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_]+", item["id"]):
            raise ValueError("Room ID is required")
        room_id = item["id"]
        if room_id in rooms:
            raise ValueError(f"Duplicate room ID: {room_id}")
        origin = _vector(item.get("origin"), f"{room_id}.origin")
        size = _vector(item.get("size"), f"{room_id}.size")
        if any(length <= 0 for length in size):
            raise ValueError(f"Room {room_id} has invalid dimensions")
        rooms[room_id] = {
            "id": room_id,
            "label": str(item.get("label") or room_id),
            "origin": origin,
            "size": size,
            "points": int(item.get("points") or 0),
            "door_ids": [],
        }

    connections = []
    occupied: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
    for index, portal in enumerate(source_portals, 1):
        if not isinstance(portal, dict):
            raise ValueError(f"Portal {index} is invalid")
        pair = portal.get("rooms")
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1] or any(r not in rooms for r in pair):
            raise ValueError(f"Portal {index} must name two distinct known rooms")
        axis = portal.get("axis")
        if axis not in {"x", "y"}:
            raise ValueError(f"Portal {index} has an unsupported axis")
        normal = 0 if axis == "x" else 1
        along = 1 - normal
        plane = _number(portal.get("plane"), f"portal {index} plane")
        center = _number(portal.get("center"), f"portal {index} center")
        width = _number(portal.get("width"), f"portal {index} width")
        height = _number(portal.get("height"), f"portal {index} height")
        if width <= 0 or height <= 0:
            raise ValueError(f"Portal {index} has invalid clear dimensions")
        endpoints = []
        for room_id in pair:
            room = rooms[room_id]
            low = room["origin"][normal]
            high = low + room["size"][normal]
            if abs(plane - low) <= _TOL:
                side = "min"
            elif abs(plane - high) <= _TOL:
                side = "max"
            else:
                raise ValueError(f"Portal {index} does not touch {room_id}'s wall")
            span_low = room["origin"][along]
            span_high = span_low + room["size"][along]
            if center - width / 2 < span_low - _TOL or center + width / 2 > span_high + _TOL:
                raise ValueError(f"Portal {index} extends beyond {room_id}'s wall")
            if height > room["size"][2] + _TOL:
                raise ValueError(f"Portal {index} exceeds {room_id}'s height")
            key = (room_id, axis, side)
            interval = (center - width / 2, center + width / 2)
            if any(interval[0] < old[1] - _TOL and old[0] < interval[1] - _TOL for old in occupied.get(key, [])):
                raise ValueError(f"Portal {index} overlaps another door on {room_id}'s wall")
            occupied.setdefault(key, []).append(interval)
            door_id = f"{room_id}:door_{index:02d}"
            room["door_ids"].append(door_id)
            outward = f"{side}_{axis}"
            global_center = [plane, center, room["origin"][2] + height / 2] if axis == "x" else [center, plane, room["origin"][2] + height / 2]
            endpoints.append({"id": door_id, "room_id": room_id, "side": outward,
                              "normal": _normal(outward),
                              "local_center": [global_center[i] - room["origin"][i] for i in range(3)]})
        if endpoints[0]["side"] == endpoints[1]["side"]:
            raise ValueError(f"Portal {index} does not join opposite wall faces")
        floor_a = rooms[pair[0]]["origin"][2]
        floor_b = rooms[pair[1]]["origin"][2]
        if abs(floor_a - floor_b) > _TOL:
            raise ValueError(f"Portal {index} joins rooms on different floors")
        xyz = [plane, center, floor_a + height / 2] if axis == "x" else [center, plane, floor_a + height / 2]
        connections.append({
            "id": f"D{index:02d}", "rooms": pair, "doors": endpoints,
            "axis": axis, "plane": plane, "along": center,
            "center": xyz, "width_m": width, "height_m": height,
        })

    visited = {next(iter(rooms))}
    while True:
        expanded = visited | {room for link in connections if any(r in visited for r in link["rooms"]) for room in link["rooms"]}
        if expanded == visited:
            break
        visited = expanded
    if visited != set(rooms):
        raise ValueError("Door graph does not connect every room")

    minimum = [min(room["origin"][i] for room in rooms.values()) for i in range(3)]
    maximum = [max(room["origin"][i] + room["size"][i] for room in rooms.values()) for i in range(3)]
    graph = {
        "schema_version": 1,
        "source": "synthetic_house_manifest",
        "unit": "m",
        "up_axis": "z",
        "scene_version": manifest.get("scene_version"),
        "bounds_m": {"min": minimum, "max": maximum},
        "rooms": list(rooms.values()),
        "connections": connections,
        "complete_points": int(manifest.get("complete_points") or 0),
    }
    graph["transforms"] = solve_door_transforms(graph["rooms"], connections)
    return graph


def build_house_structure(graph: dict, render_spec: dict, ground_truth: dict) -> dict:
    """Translate the delivered shell and object boxes to the scan viewer schema."""
    room_ids = {room["id"] for room in graph["rooms"]}
    if {room["id"] for room in render_spec.get("rooms", [])} != room_ids:
        raise ValueError("Rendered shell and house manifest have different rooms")
    walls = [
        {"instance_id": f"wall_{index:03d}", "center": _vector(item["position"], "wall position"),
         "size": _vector(item["size"], "wall size"), "category": "wall", "rooms": item.get("rooms", [])}
        for index, item in enumerate(render_spec.get("walls", []), 1)
    ]
    doors = []
    for link in graph["connections"]:
        size = [0.06, link["width_m"], link["height_m"]] if link["axis"] == "x" else [link["width_m"], 0.06, link["height_m"]]
        doors.append({"instance_id": link["id"], "center": link["center"], "size": size,
                      "category": "door", "door_ids": [door["id"] for door in link["doors"]],
                      "rooms": link["rooms"]})
    windows = []
    for index, item in enumerate(render_spec.get("windows", []), 1):
        axis = int(item["axis"])
        if axis not in (0, 1) or item.get("room_id") not in room_ids:
            raise ValueError("Rendered window is not on a known room")
        plane, along = _number(item["plane"], "window plane"), _number(item["center"], "window center")
        width, bottom, height = (_number(item[key], f"window {key}") for key in ("width", "bottom", "height"))
        center = [plane, along, bottom + height / 2] if axis == 0 else [along, plane, bottom + height / 2]
        size = [0.06, width, height] if axis == 0 else [width, 0.06, height]
        windows.append({"instance_id": f"window_{index:02d}", "center": center, "size": size, "category": "window"})
    objects = [
        {"instance_id": item["instance_id"], "room_id": item["room_id"],
         "center": _vector(item["position"], "object position"), "size": _vector(item["size"], "object size"),
         "rotation_z_deg": _number(item.get("rotation", 0), "object rotation"),
         "category": item["object"], "source": "synthetic_ground_truth"}
        for item in ground_truth.get("objects", []) if item.get("room_id") in room_ids
    ]
    bounds = graph["bounds_m"]
    return {
        "source": "synthetic_house_manifest_and_render_shell",
        "unit": "m", "up_axis": "z",
        "room": {"bounds_xy": {"min": bounds["min"][:2], "max": bounds["max"][:2]},
                 "height_m": bounds["max"][2] - bounds["min"][2]},
        "walls": walls, "doors": doors, "windows": windows,
        "semantic_instances": objects,
    }
