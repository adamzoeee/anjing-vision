"""Deterministic surface geometry for the synthetic household, not detections.

All object coordinates are local metres, centred in XY with their bottom at Z=0.
Placement supplies elevation and yaw; semantic colours are a viewer-only option.
"""
from __future__ import annotations

import math
import os
import numpy as np

LABELS = {
    "sofa": "沙发", "table": "桌子", "chair": "椅子", "tv": "电视", "tv_stand": "电视柜",
    "bed": "床", "nightstand": "床头柜", "wardrobe": "衣柜", "desk": "书桌", "cabinet": "橱柜",
    "window": "窗户", "curtain": "窗帘", "air_conditioner": "空调", "rug": "地毯", "plant": "绿植",
    "lamp": "台灯", "sink": "水槽", "fridge": "冰箱", "stove": "灶台", "range_hood": "抽油烟机",
    "toilet": "马桶", "vanity": "洗手台", "mirror": "镜子", "shower": "淋浴区域",
    "shower_head": "花洒", "towel_rack": "毛巾架", "shoe_cabinet": "鞋柜",
    "wall_art": "墙面装饰", "ceiling_light": "顶灯",
    "side_table": "小桌", "book": "书本", "cup": "杯子", "countertop": "操作台",
    "washing_machine": "洗衣机", "storage_cabinet": "储物柜", "coat_rack": "衣帽架",
    "umbrella_stand": "伞架", "storage_box": "收纳箱",
}


def spec(category, xy, size, yaw=0, elevation=0, **extras):
    return {"object": category, "floor_center": xy, "size": size, "rotation": yaw,
            "elevation": elevation, "label": LABELS[category], **extras}


def window_openings(room_id):
    # External walls only. axis is the wall normal; center is along the wall.
    return {
        "living_room": [{"axis": 0, "plane": 0, "center": 1.7, "width": 1.8, "bottom": 1.05, "height": 1.35}],
        "bedroom": [{"axis": 1, "plane": 3.5, "center": 1.65, "width": 1.7, "bottom": 1.1, "height": 1.25}],
        "kitchen": [{"axis": 0, "plane": 3, "center": 1.4, "width": 1.25, "bottom": 1.1, "height": 1.25}],
    }.get(room_id, [])


def enriched_specs(room_id, base_items):
    """Keep existing categories while making space for additional indoor assets."""
    items = [dict(item, label=LABELS[item["object"]], elevation=0) for item in base_items]
    if room_id == "living_room":
        for item in items:
            if item["object"] == "tv":
                item.update(elevation=0.48, size=[1.25, .30, .78])
        items += [spec("tv_stand", [2, .35], [1.8, .55, .48]),
                  spec("air_conditioner", [4.4, 3.87], [.95, .24, .30], elevation=2.5),
                  spec("rug", [2.55, 2.1], [2.6, 1.6, .018]),
                  spec("plant", [5.3, .55], [.55, .55, 1.15])]
    elif room_id == "bedroom":
        for item in items:
            if item["object"] == "bed":
                item.update(floor_center=[1.15, 2.35], size=[1.8, 2.0, 1.0])
            elif item["object"] == "cabinet":
                item.update(object="wardrobe", label=LABELS["wardrobe"], floor_center=[3.55, 2.85], size=[.7, 1.2, 2.1], rotation=90)
                # Use local width across doors; after rotation the depth is .60.
                item.update(size=[1.2, .6, 2.1])
        items += [spec("nightstand", [2.45, 3.06], [.55, .6, .5]),
                  spec("desk", [1.25, .38], [1.25, .65, .75]),
                  spec("chair", [1.25, 1.0], [.48, .48, .9], yaw=180),
                  spec("lamp", [2.45, 3.06], [.26, .26, .4], elevation=.5),
                  spec("air_conditioner", [3.4, .13], [.85, .24, .3], elevation=2.5)]
    elif room_id == "kitchen":
        for item in items:
            if item["object"] == "cabinet":
                item.update(floor_center=[1.2, 2.65], size=[2.2, .6, .9],
                            cutout={"center": [-.5, 0], "size": [.64, .42], "bottom": .70})
            elif item["object"] == "table":
                item.update(floor_center=[1.85, .95], size=[.85, .6, .75])
            elif item["object"] == "chair":
                item.update(floor_center=[1.85, .3], size=[.42, .42, .85])
        items += [spec("sink", [.7, 2.65], [.64, .42, .48], elevation=.72),
                  spec("fridge", [2.65, 2.6], [.65, .70, 1.85]),
                  spec("stove", [1.75, 2.65], [.65, .5, .05], elevation=.9),
                  spec("range_hood", [1.75, 2.76], [.70, .42, .68], elevation=1.65)]
    elif room_id == "bathroom":
        for item in items:
            item.update(object="vanity", label=LABELS["vanity"], floor_center=[1.55, 1.73], size=[.6, .5, .85])
        items += [spec("toilet", [.64, 1.63], [.4, .68, .80]),
                  spec("mirror", [1.55, 1.975], [.58, .035, .65], elevation=1.15),
                  spec("shower", [2.04, .5], [.85, .9, 2.0]),
                  spec("shower_head", [2.33, .5], [.1, .28, 1.1], yaw=90, elevation=1.0),
                  spec("towel_rack", [.70, .10], [.62, .15, .45], elevation=1.0)]
    elif room_id == "corridor":
        items += [spec("shoe_cabinet", [1.29, 3], [.38, 1.2, 1.0], yaw=0),
                  spec("wall_art", [.025, 3.2], [.65, .035, .6], yaw=90, elevation=1.5),
                  spec("ceiling_light", [.75, 3.75], [.5, .5, .08], elevation=2.88)]
    for window in window_openings(room_id):
        normal = window["axis"]
        xy = [window["plane"], window["center"]] if normal == 0 else [window["center"], window["plane"]]
        # Frame thickness stays inside the room, avoiding protrusion into a neighbour.
        inward = .045 if window["plane"] == 0 else -.045
        xy[normal] += inward
        yaw = 90 if normal == 0 else 0
        items.append(spec("window", xy, [window["width"], .08, window["height"]], yaw, window["bottom"]))
        if room_id != "kitchen":
            curtain_xy = xy.copy()
            curtain_xy[normal] += .14 if window["plane"] == 0 else -.14
            items.append(spec("curtain", curtain_xy, [window["width"] + .35, .15, 1.5], yaw, 1.0))
    if os.getenv("HOUSE_DEMO_PROFILE", "rich") in {"lived", "risk"}:
        from .house_demo_lived import lived_specs
        items = lived_specs(room_id, items)
        if os.getenv("HOUSE_DEMO_PROFILE") == "risk":
            from .house_demo_risk import risk_specs
            items = risk_specs(room_id, items)
        return items
    return items


def surface_object(item, spacing, box_sampler):
    """Return XYZ/RGB for an asset; fallback None leaves legacy primitives intact."""
    category = item["object"]
    l, w, h = item["size"]
    points, colors = [], []
    wood, white, metal = [.56, .37, .23], [.88, .88, .83], [.42, .46, .48]

    def add(p, color):
        points.append(p)
        colors.append(np.tile(color, (len(p), 1)))

    def box(center, dimensions, color, fine=False):
        add(box_sampler(center, dimensions, min(spacing, .025) if fine else spacing), color)

    def ellipse(center, radii, color, *, cylinder=None, bowl=None, ring=None):
        # Arc-length-controlled rings: no random particles, dense poles or textures.
        step = min(spacing, .025)
        rx, ry, rz = radii
        if cylinder is not None:
            levels = [(r, z) for z in np.linspace(0, cylinder, max(2, math.ceil(cylinder / step) + 1)) for r in [1]]
            levels += [(r, z) for z in (0, cylinder) for r in np.linspace(0, 1, max(2, math.ceil(max(rx, ry) / step) + 1))]
        elif bowl is not None:
            levels = [(r, -bowl * (1-r*r)) for r in np.linspace(0, 1, max(2, math.ceil(max(rx, ry, bowl) / step) + 1))]
        elif ring is not None:
            levels = [(r, 0) for r in np.linspace(ring, 1, max(2, math.ceil(max(rx, ry) * (1-ring) / step) + 1))]
        else:
            levels = [(math.sin(phi), rz * math.cos(phi)) for phi in np.linspace(0, math.pi, max(3, math.ceil(math.pi * max(rx, ry, rz) / step) + 1))]
        samples = []
        for r, z in levels:
            n = max(1, math.ceil(2 * math.pi * max(rx, ry) * r / step))
            theta = np.arange(n) * 2 * math.pi / n
            samples.append(np.column_stack((rx*r*np.cos(theta), ry*r*np.sin(theta), np.full(n, z))) + center)
        add(np.concatenate(samples), color)

    if category == "washing_machine":
        box([0,0,h/2], [l,w,h], white)
        # Front loading glass/rim, not a photograph. Cylinder sampled upright,
        # then rotated onto the front face.
        start = len(points)
        ellipse([0,0,0], [l*.32,l*.32,0], metal, ring=.78)
        ellipse([0,0,0], [l*.24,l*.24,0], [.20,.28,.30], ring=0)
        for index in range(start, len(points)):
            p=points[index].copy()
            points[index]=np.column_stack((p[:,0], np.full(len(p),-w/2-.004), p[:,1]+h*.46))
        box([0,-w/2-.004,h*.88], [l*.85,.008,.055], metal, True)
    elif category in {"book", "storage_box", "storage_cabinet", "countertop"}:
        box([0,0,h/2], [l,w,h], [.66,.49,.32] if category != "countertop" else [.77,.77,.72])
        if category == "book":
            box([0,0,h*.5], [l*.97,w*1.002,h*.65], [.89,.88,.8], True)
    elif category in {"cup", "umbrella_stand"}:
        ellipse([0,0,0], [l*.48,w*.48,0], [.38,.49,.55], cylinder=h)
    elif category == "coat_rack":
        box([0,0,.025], [l,w,.05], wood)
        box([0,0,h/2], [.045,.045,h], wood, True)
        box([0,0,h*.85], [l,.045,.045], wood, True)
    elif category == "bed":
        box([0, 0, .17], [l, w, .25], wood)
        box([0, -.025, .39], [l-.05, w-.1, .22], [.87, .87, .83])
        box([0, w/2-.04, h/2], [l, .08, h], wood)
        box([0, -.25, .511], [l-.1, w*.65, .018], [.30, .48, .65])
        for x in (-l/4, l/4):
            ellipse([x, w*.29, .555], [l*.19, .22, .055], [.94, .92, .86])
    elif category in ("cabinet", "wardrobe", "nightstand", "tv_stand", "shoe_cabinet", "fridge"):
        body = [.75, .78, .78] if category == "fridge" else wood
        box([0, 0, h/2], [l, w, h], body)
        count = 2 if l > .75 else 1
        for i in range(count):
            x = -l/2 + (i+.5)*l/count
            box([x, -w/2-.006, h*.50], [l/count-.025, .012, h*.92], [.77, .70, .58] if category != "fridge" else [.83, .85, .85])
            box([x+l/count*.28, -w/2-.018, h*.55], [.025, .024, .15], metal, True)
        if category == "fridge":
            box([0, -w/2-.014, h*.68], [l-.02, .013, .015], metal, True)
    elif category == "window":
        # Glass is a physical sampled plane (not alpha textures).
        glass = box_sampler([0, 0, h/2], [l, .002, h], spacing)
        add(glass, [.57, .72, .80])
        for x in (-l/2+.025, 0, l/2-.025):
            box([x, 0, h/2], [.05, w, h], [.85, .86, .84])
        for z in (.025, h-.025):
            box([0, 0, z], [l, w, .05], [.85, .86, .84])
    elif category == "curtain":
        step = min(spacing, .025)
        # Two drawn-back pleated panels leave the actual window visible.
        for side in (-1, 1):
            x = np.linspace(side*l*.35-l*.15, side*l*.35+l*.15, max(3, math.ceil(l*.3/step)))
            z = np.linspace(0, h-.08, max(3, math.ceil(h/step)))
            xx, zz = np.meshgrid(x, z)
            yy = .045*np.sin((xx+l/2) * 2*math.pi/.12)
            add(np.column_stack((xx.ravel(), yy.ravel(), zz.ravel())), [.60, .49, .38])
        box([0, 0, h-.025], [l, .03, .03], metal, True)
    elif category == "air_conditioner":
        box([0, 0, h/2], [l, w, h], white)
        for z in np.linspace(.04, h*.40, 4):
            box([0, -w/2-.002, z], [l*.85, .008, .01], metal, True)
    elif category == "rug":
        box([0, 0, h/2], [l, w, h], [.39, .42, .40])
    elif category == "plant":
        ellipse([0, 0, 0], [l*.25, w*.25, 0], [.58, .31, .20], cylinder=h*.28)
        box([0, 0, h*.52], [.025, .025, h*.48], [.30, .31, .14], True)
        for i in range(7):
            angle = i*2.399963
            ellipse([math.cos(angle)*l*.22, math.sin(angle)*w*.22, h*(.45+.065*i)], [l*.22,w*.13,h*.065], [.24,.45,.22])
    elif category == "lamp":
        ellipse([0, 0, .015], [l*.4, w*.4, 0], metal, cylinder=.02)
        box([0, 0, h*.42], [.025,.025,h*.75], metal, True)
        ellipse([0, 0, h*.8], [l*.5,w*.5,h*.2], [.93,.84,.61])
    elif category in ("sink", "vanity"):
        if category == "vanity":
            box([0,0,h*.43], [l,w,h*.86], wood)
            bowl_top, depth = h, .13
            rx, ry = l*.36, w*.34
        else:
            bowl_top, depth = .18, .17
            rx, ry = l*.47, w*.46
        ellipse([0,0,bowl_top], [rx,ry,0], white if category == "vanity" else metal, bowl=depth)
        ellipse([0,0,bowl_top+.003], [rx*1.07,ry*1.07,0], white, ring=.93)
        if category == "sink":
            box([0,w*.43,bowl_top+.13], [.025,.025,.26], metal, True)
            box([0,w*.24,bowl_top+.255], [.025,w*.42,.025], metal, True)
    elif category == "stove":
        box([0,0,h*.3], [l,w,h*.6], [.13,.15,.16])
        for x in (-l*.26,l*.26):
            ellipse([x,0,h], [l*.17,l*.17,0], metal, ring=.7)
    elif category == "range_hood":
        box([0,0,.07], [l,w,.14], metal)
        box([0,w*.23,h*.55], [l*.38,w*.45,h*.90], [.67,.69,.70])
    elif category == "toilet":
        ellipse([0,-w*.10,0], [l*.30,w*.25,0], white, cylinder=h*.25)
        ellipse([0,-w*.11,h*.49], [l*.49,w*.34,0], white, bowl=h*.24)
        ellipse([0,-w*.11,h*.50], [l*.50,w*.35,0], [.94,.93,.88], ring=.72)
        box([0,w*.36,h*.62], [l*.90,w*.22,h*.76], white)
    elif category == "mirror":
        box([0,0,h/2], [l,w,h], [.65,.76,.78])
        for x in (-l/2+.015,l/2-.015):
            box([x,-w/2,h/2], [.03,.02,h], metal, True)
    elif category == "shower":
        box([0,0,.018], [l,w,.036], [.74,.79,.78])
        # A side screen; open front entry. This is not a filled shower cubicle.
        box([-l/2+.008,0,h/2], [.016,w,h], [.58,.74,.78])
        ellipse([l*.2,0,.038], [.06,.06,0], metal, ring=0)
    elif category == "shower_head":
        box([0,w*.35,h*.45], [.025,.025,h*.90], metal, True)
        box([0,0,h-.04], [.03,w,.03], metal, True)
        ellipse([0,-w*.35,h-.05], [l/2,l/2,.015], metal)
    elif category == "towel_rack":
        box([0,0,h-.025], [l,.025,.025], metal, True)
        box([0,-.01,h*.43], [l*.65,.015,h*.85], [.55,.67,.73])
    elif category == "wall_art":
        box([0,0,h/2], [l,w,h], wood)
        box([0,-w/2-.002,h/2], [l*.85,.003,h*.85], [.33,.49,.43])
    elif category == "ceiling_light":
        ellipse([0,0,h/2], [l/2,w/2,h/2], [.94,.91,.74])
    else:
        return None
    xyz, rgb = np.concatenate(points), np.concatenate(colors)
    if "cutout" in item:
        hole = item["cutout"]
        inside = ((np.abs(xyz[:,0]-hole["center"][0]) < hole["size"][0]/2)
                  & (np.abs(xyz[:,1]-hole["center"][1]) < hole["size"][1]/2)
                  & (xyz[:,2] > hole["bottom"]))
        xyz, rgb = xyz[~inside], rgb[~inside]
    return xyz, rgb
