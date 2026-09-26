"""派生状态 → layout：2D 叠影图与 3D 查看器共用的一份渲染数据。"""
from __future__ import annotations


def _box(center, size, rotation_z_deg, **extra) -> dict:
    return {
        "center": [float(v) for v in (center or [0.0, 0.0, 0.0])],
        "size": [float(v) for v in (size or [0.0, 0.0, 0.0])],
        "rotation_z_deg": float(rotation_z_deg or 0.0),
        **extra,
    }


def build_layout_objects(structure: dict) -> list[dict]:
    """家具与障碍物：与 measurements 同口径（米、z-up）。"""
    items = []
    for item in structure.get("semantic_instances") or structure.get("objects") or []:
        items.append(_box(
            item.get("center"), item.get("size"), item.get("rotation_z_deg"),
            id=str(item.get("instance_id") or item.get("id") or ""),
            label=item.get("label") or item.get("category") or "object",
            kind="object", assumed=bool(item.get("assumed")),
        ))
    for index, item in enumerate(structure.get("geometric_obstacles") or []):
        items.append(_box(
            item.get("center"), item.get("size"), item.get("rotation_z_deg"),
            id=str(item.get("instance_id") or f"obstacle_{index}"),
            label="箱子", kind="obstacle", assumed=bool(item.get("assumed")),
        ))
    return items


def build_layout(measurements: dict, structure: dict) -> dict:
    """与 preview 的 layout.json 同形状，供 2D 叠影图与 3D 查看器直接消费。"""
    room = structure.get("room") or {}
    doors = [_box(d.get("center"), d.get("size"), d.get("rotation_z_deg"), kind="door",
                  id=str(d.get("instance_id") or d.get("id") or "door"))
             for d in structure.get("doors", [])]
    windows = [_box(w.get("center"), w.get("size"), w.get("rotation_z_deg"), kind="window",
                    id=str(w.get("instance_id") or w.get("id") or "window"))
               for w in structure.get("windows", [])]
    walls = [_box(w.get("center"), w.get("size"), w.get("rotation_z_deg"), id=str(w.get("id", i)))
             for i, w in enumerate(structure.get("walls", []))]
    objects = build_layout_objects(structure)
    return {
        "coordinate_unit": "meters",
        "z_up": True,
        "room": {"bounds_xy": room.get("bounds_xy"), "height_m": room.get("height_m"),
                 "floor_polygon": room.get("floor_polygon")},
        "walls": walls,
        "doors": doors,
        "windows": windows,
        "objects": objects,
        "counts": {"walls": len(walls), "doors": len(doors),
                   "windows": len(windows), "objects": len(objects)},
    }