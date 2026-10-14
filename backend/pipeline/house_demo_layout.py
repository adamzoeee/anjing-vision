"""Traceable demo layout evidence; no production risk scores or LLM claims."""
from __future__ import annotations

import math
import numpy as np
from scipy.ndimage import distance_transform_edt, binary_dilation

from .space_foundation import _rect_distance, _rasterize_rect, _cell_of, _astar_clearance
from .house_demo_lived import ACTIVITY_TARGETS
from .house_demo_assets import LABELS


def _label(item):
    aliases={"coffee_table":"茶几","dining_chair":"餐椅","hand_sink":"洗手台","shower_room":"淋浴区","refrigerator":"冰箱","cupboard":"柜子","clothes_rack":"衣帽架"}
    return item.get("label") or LABELS.get(item["object"],aliases.get(item["object"],item["object"]))


def _rect(item):
    return {"center": item["position"], "size": item["size"], "rotation_z_deg": item["rotation"]}


def _floor_object(item):
    return (item.get("role") != "detail" and not item.get("supported_by")
            and item["position"][2]-item["size"][2]/2 <= .15
            and item["object"] not in {"rug", "carpet", "window", "curtain"})


def analyze_room(room, objects, portals, source, model_status=None, cell=.04):
    result = {"room_id": room["id"], "source": source, "unit": "m", "resolution_m": cell,
              "uncertainty": "model_may_miss_objects" if source == "spatiallm_predictions" else "synthetic_geometry_not_real_scan",
              "status": "derived", "pairs": [], "doorways": [], "routes": [], "advice": [],
              "major_object_count": sum(o.get("role", "major") == "major" for o in objects),
              "detail_object_count": sum(o.get("role") == "detail" for o in objects),
              "safety_status": "not_assessed"}
    if source == "spatiallm_predictions" and (model_status != "done" or not objects):
        result.update(status="not_evaluable", reason="model_not_ready_or_no_objects")
        return result
    floor = [o for o in objects if _floor_object(o)]
    for index, first in enumerate(floor):
        for second in floor[index+1:]:
            if first.get("assembly") and first.get("assembly") == second.get("assembly"):
                continue
            distance = _rect_distance(_rect(first), _rect(second))
            pair = {"between": [first["instance_id"], second["instance_id"]],
                    "categories": [first["object"], second["object"]],
                    "labels": [_label(o) for o in (first,second)],
                    "clearance_m": round(distance,3),
                    "delta_xy_m": np.round(np.subtract(second["position"][:2], first["position"][:2]),3).tolist(),
                    "relation": "overlap_or_touch" if distance <= 1e-6 else "separated"}
            result["pairs"].append(pair)
            categories = set(pair["categories"])
            relevant = ("sofa" in categories and bool(categories & {"table","coffee_table","side_table"})) or ("bed" in categories and bool(categories & {"chair","dining_chair","nightstand"}))
            if relevant and distance < .30:
                result["advice"].append({"rule": "near_furniture_demo", "object_ids": pair["between"],
                    "value_m": round(distance,3), "demo_threshold_m": .30,
                    "text": f'{pair["labels"][0]}与{pair["labels"][1]}的平面间距约 {distance:.2f} m，建议复核活动空间并调整位置；此间距不等同于主通道宽度。'})
    minimum = np.array(room["origin"][:2],float)
    maximum = minimum + room["size"][:2]
    origin = minimum - .60
    shape = np.ceil((np.array(room["size"][:2])+1.2)/cell).astype(int)
    yy,xx = np.indices((shape[1],shape[0]))
    wx,wy = origin[0]+(xx+.5)*cell, origin[1]+(yy+.5)*cell
    interior=(wx>minimum[0])&(wx<maximum[0])&(wy>minimum[1])&(wy<maximum[1])
    occupied=~interior
    room_portals=[p for p in portals if room["id"] in p["rooms"]]
    # Extend each door outside the room for a correct side clearance; never
    # erase furniture near the door. Boundaries elsewhere remain obstacles.
    for door in room_portals:
        tunnel=(np.abs(wx-door["plane"])<.56)&(np.abs(wy-door["center"])<door["width"]/2)
        occupied[tunnel & ~interior]=False
    furniture=np.zeros_like(occupied)
    for item in floor:
        _rasterize_rect(furniture,origin,cell,_rect(item))
    occupied |= furniture
    clearance=distance_transform_edt(~occupied)*cell
    # One-cell conservative margin also prevents diagonal point-contact paths.
    navigable=~binary_dilation(occupied, structure=np.ones((3,3),bool))
    target=minimum+ACTIVITY_TARGETS[room["id"]]
    goal=_cell_of(target,origin,cell,occupied.shape)
    for index, door in enumerate(room_portals):
        sign=1 if abs(door["plane"]-minimum[0]) < .01 else -1
        start_xy=np.array([door["plane"]+sign*.20,door["center"]])
        start=_cell_of(start_xy,origin,cell,occupied.shape)
        region=interior & (np.abs(wy-door["center"])<door["width"]/2) & ((wx-door["plane"])*sign>=0) & ((wx-door["plane"])*sign<1)
        area=float(region.sum())*cell*cell
        used=float((region&furniture).sum())*cell*cell
        result["doorways"].append({"id":f"door_{index}", "nominal_width_m":door["width"],
                                  "inspection_depth_m":1.0,"area_m2":round(area,3),
                                  "occupied_area_m2":round(used,3),"free_area_m2":round(area-used,3)})
        path=_astar_clearance(~navigable,start,{goal},clearance,cell) if navigable[start] and navigable[goal] else None
        width=min(door["width"],min(2*float(clearance[r,c]) for r,c in path)) if path else None
        result["routes"].append({"door_id":f"door_{index}", "status":"derived" if path else "blocked",
            "reason":None if path else "target_or_route_blocked_in_footprint_model",
            "target_xy":target.tolist(), "target_source":"explicit_demo_activity_anchor",
            "minimum_width_m":round(width,3) if width is not None else None,
            "path_xy":[[round(float(origin[0]+(c+.5)*cell),3),round(float(origin[1]+(r+.5)*cell),3)] for r,c in (path or [])],
            "method":"2x_distance_to_obstacles_along_computed_route",
            "limitation":"one_route_estimate_not_global_passage_width_or_safety_certification"})
        if path and width < .65:
            narrow=min(path,key=lambda rc:clearance[rc])
            probe={"center":[origin[0]+(narrow[1]+.5)*cell,origin[1]+(narrow[0]+.5)*cell,0],"size":[.001,.001],"rotation_z_deg":0}
            nearby=sorted(floor,key=lambda item:_rect_distance(probe,_rect(item)))[:2]
            labels="、".join(_label(item) for item in nearby) or "附近物体"
            result["advice"].append({"rule":"route_width_demo", "door_id":f"door_{index}",
                "object_ids":[item["instance_id"] for item in nearby],
                "value_m":round(width,3),"demo_threshold_m":.65,
                "text":f'门口到设定活动点的路线局部净空约 {width:.2f} m，建议复核附近的{labels}摆放。仅为栅格几何估计，需结合实际通行需求复核。'})
        elif not path:
            result["advice"].append({"rule":"route_blocked_demo", "door_id":f"door_{index}",
                "text":"当前家具占地模型中未找到通往设定活动点的路线，请复核目标点和障碍物；不据此认定整个房间不可通行。"})
        if used > .04:
            result["advice"].append({"rule":"entrance_occupancy_demo", "door_id":f"door_{index}",
                "value_m2":round(used,3),"text":f'门内 1 m 检查区约 {used:.2f} m² 被家具占用，建议确认是否影响开门和进出。未模拟门扇开启轨迹。'})
    result["pairs"].sort(key=lambda item:item["clearance_m"])
    return result


def analyze_house(manifest, objects, source, model_rooms=None):
    return {"source":source, "analysis_kind":"deterministic_geometry_demo_not_llm_or_formal_risk",
            "room_bounds_source":"synthetic_manifest", "safety_score":None,
            "warning":"未命中规则不代表安全；模型漏检会高估净空。不得据此判断真实住宅安全。",
            "rooms":{room["id"]:analyze_room(room,[o for o in objects if o["room_id"]==room["id"]],
                manifest["portals"],source,(model_rooms or {}).get(room["id"],{}).get("status"))
                for room in manifest["rooms"]}}
