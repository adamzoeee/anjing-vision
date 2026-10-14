"""Optional lived-in placements. Deterministic habits, never random clutter."""
from __future__ import annotations

from .house_demo_assets import spec


def lived_specs(room_id, items):
    items = [dict(item, role="major") for item in items]
    if room_id == "living_room":
        items = [i for i in items if i["object"] not in {"chair", "air_conditioner", "rug"}]
        for i in items:
            if i["object"] == "sofa":
                i["floor_center"] = [1.55, 3.35]
            if i["object"] == "table":
                i.update(floor_center=[2.35, 2.37], rotation=8)
            if i["object"] == "tv":
                i["supported_by"] = "tv_stand"
        items += [spec("side_table", [3.20, 3.43], [.55, .55, .6], yaw=-7, role="major")]
        items += [spec("book", [2.23, 2.36], [.24, .17, .035], yaw=19, elevation=.48,
                       role="detail", supported_by="table"),
                  spec("cup", [2.68, 2.40], [.085, .085, .12], elevation=.48,
                       role="detail", supported_by="table")]
    elif room_id == "bedroom":
        items = [i for i in items if i["object"] != "air_conditioner"]
        for i in items:
            if i["object"] == "desk":
                i["floor_center"] = [2.55, .40]
            if i["object"] == "chair":
                i.update(floor_center=[2.53, 1.12], rotation=162)
            if i["object"] == "lamp":
                i["supported_by"] = "nightstand"
        items += [spec("book", [2.35, .43], [.24, .17, .035], yaw=-14, elevation=.75,
                       role="detail", supported_by="desk"),
                  spec("cup", [2.87, .35], [.085, .085, .12], elevation=.75,
                       role="detail", supported_by="desk")]
    elif room_id == "kitchen":
        for i in items:
            if i["object"] == "cabinet":
                i["size"] = [2.2, .6, .84]
                i["assembly"] = "worktop"
            if i["object"] in {"sink", "stove"}:
                i["assembly"] = "worktop"
            if i["object"] == "window":
                i["role"] = "detail"
            if i["object"] == "table":
                i.update(floor_center=[1.87, 1.02], rotation=11)
            if i["object"] == "chair":
                i.update(floor_center=[1.30, .50], rotation=155)
        items += [spec("countertop", [1.2, 2.65], [2.2, .6, .06], elevation=.84,
                       role="major", assembly="worktop",
                       cutout={"center": [-.5, 0], "size": [.64, .42], "bottom": -.01})]
    elif room_id == "bathroom":
        items += [spec("washing_machine", [.42, .39], [.55, .55, .84], role="major"),
                  spec("storage_cabinet", [1.22, .28], [.4, .42, 1.05], role="major")]
    elif room_id == "corridor":
        items += [spec("coat_rack", [.22, 4.3], [.32, .32, 1.7], role="major"),
                  spec("umbrella_stand", [1.30, 6.8], [.26, .26, .65], role="major"),
                  spec("storage_box", [1.24, 4.1], [.4, .4, .3], yaw=5, role="major")]
    return items


ACTIVITY_TARGETS = {
    "living_room": [3.7, 2.0], "bedroom": [2.7, 1.9], "kitchen": [1.15, 1.5],
    "bathroom": [1.20, 1.05], "corridor": [.75, 3.8],
}
