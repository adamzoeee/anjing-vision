"""Optional constrained, lived-in risk demonstration; not detected hazards."""
from copy import deepcopy
import math

from .house_demo_assets import spec


def risk_specs(room_id, items):
    """Preserve proportions and attached objects while moving selected furniture."""
    items = deepcopy(items)
    placements = {
        "living_room": {"table": ([2.35, 2.46], 8),
                        "side_table": ([4.95, 1.43], -7),
                        "plant": ([5.48, 2.66], 0)},
        "bedroom": {"chair": ([2.53, 1.43], 165)},
        "kitchen": {"table": ([2.02, 1.65], 11),
                    "chair": ([1.28, .95], 165)},
        "bathroom": {"washing_machine": ([.49, .64], 0),
                     "storage_cabinet": ([1.23, .43], 0)},
        "corridor": {"shoe_cabinet": ([1.10, 3.0], 0)},
    }.get(room_id, {})
    for parent in items:
        if parent["object"] not in placements:
            continue
        xy, yaw = placements[parent["object"]]
        old_xy = parent["floor_center"]
        angle = math.radians(yaw - parent["rotation"])
        c, s = math.cos(angle), math.sin(angle)
        for child in items:
            if child.get("supported_by") != parent["object"]:
                continue
            dx, dy = (child["floor_center"][i] - old_xy[i] for i in (0, 1))
            child["floor_center"] = [xy[0]+c*dx-s*dy, xy[1]+s*dx+c*dy]
            child["rotation"] += yaw - parent["rotation"]
        parent.update(floor_center=list(xy), rotation=yaw)
    if room_id == "corridor":
        items += [spec("plant", [.28, 2.95], [.42, .42, 1.05], role="major"),
                  spec("storage_cabinet", [1.25, 6.0], [.40, .65, 1.05], role="major")]
    return items
