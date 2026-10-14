"""Prepare a traceable, metre/Z-up render specification without changing the demo."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wall_panels(rooms, portals, windows, thickness=.06):
    """Partition shared wall planes once and remove actual door/window rectangles."""
    planes = {}
    for room in rooms:
        x, y, z = room["origin"]
        l, w, h = room["size"]
        for axis, plane, lo, hi in ((0,x,y,y+w),(0,x+l,y,y+w),(1,y,x,x+l),(1,y+w,x,x+l)):
            planes.setdefault((axis,round(plane,6)), []).append((lo,hi,z,z+h,room["id"]))
    result=[]
    for (axis,plane), spans in planes.items():
        holes=[]
        for p in portals:
            if (0 if p["axis"]=="x" else 1)==axis and abs(p["plane"]-plane)<1e-6:
                holes.append((p["center"]-p["width"]/2,p["center"]+p["width"]/2,0,p["height"]))
        for p in windows:
            if p["axis"]==axis and abs(p["plane"]-plane)<1e-6:
                holes.append((p["center"]-p["width"]/2,p["center"]+p["width"]/2,p["bottom"],p["bottom"]+p["height"]))
        us=sorted({round(v,6) for s in spans+holes for v in s[:2]})
        zs=sorted({round(v,6) for s in spans+holes for v in s[2:4]})
        for a,b in zip(us,us[1:]):
            for c,d in zip(zs,zs[1:]):
                u,z=(a+b)/2,(c+d)/2
                owners=[s[4] for s in spans if s[0]<u<s[1] and s[2]<z<s[3]]
                if not owners or any(s[0]<u<s[1] and s[2]<z<s[3] for s in holes): continue
                pos=[plane,u,z] if axis==0 else [u,plane,z]
                size=[thickness,b-a,d-c] if axis==0 else [b-a,thickness,d-c]
                result.append(dict(position=pos,size=size,rooms=owners,axis=axis,plane=plane))
    return result


def prepare(source, mode="truth"):
    source=Path(source).resolve()
    manifest=read(source/"manifest.json")
    if manifest.get("synthetic") is not True or manifest.get("unit")!="m" or manifest.get("up_axis")!="z":
        raise ValueError("Expected existing synthetic metre/Z-up demo")
    rooms=manifest["rooms"]
    truth=read(source/"ground_truth.json")["objects"]
    windows=[]
    for obj in truth:
        if obj["object"]!="window": continue
        room=next(r for r in rooms if r["id"]==obj["room_id"])
        x,y,z=obj["position"]; l,w,h=obj["size"]
        candidates=[(abs(obj["position"][a]-v),a,v) for a in (0,1)
                    for v in (room["origin"][a],room["origin"][a]+room["size"][a])]
        _,axis,plane=min(candidates)
        windows.append(dict(room_id=room["id"],axis=axis,plane=plane,center=obj["position"][1-axis],width=l,bottom=z-h/2,height=h))
    if mode=="truth":
        objects=truth
    elif mode=="model":
        predictions=read(source/"predictions.json")
        if any(predictions["rooms"].get(r["id"],{}).get("status")!="done" for r in rooms):
            raise ValueError("All rooms must have completed predictions; no truth fallback")
        objects=[o for r in predictions["rooms"].values() for o in r["objects"]]
    else: raise ValueError("mode must be truth or model")
    for obj in objects:
        values=[*obj["position"],*obj["size"],obj["rotation"]]
        if not all(math.isfinite(v) for v in values) or min(obj["size"])<=0 or obj.get("rotation_unit")!="degrees":
            raise ValueError(f"Invalid transform {obj.get('instance_id')}")
    paths=[source/"manifest.json",source/"ground_truth.json",source/("predictions.json" if mode=="model" else "layout_analysis.json")]
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
    # The original point buffers remain the visual evidence, not a source of invented surfaces.
    clouds=[]
    for room in rooms:
        p=source/room["buffer"]
        if p.stat().st_size != room["points"]*24: raise ValueError(f"Invalid point buffer: {p}")
        clouds.append(dict(room_id=room["id"],path=str(p),points=room["points"]))
    return dict(schema_version=1,unit="m",up_axis="z",source=str(source),source_hashes=hashes,
                method="parameter_driven_proxy_geometry_not_photogrammetric_surface_reconstruction",
                furniture_source="synthetic_ground_truth" if mode=="truth" else "spatiallm_predictions",
                shell_source="synthetic_manifest_and_window_layout",rooms=rooms,portals=manifest["portals"],
                windows=windows,walls=wall_panels(rooms,manifest["portals"],windows),objects=objects,pointclouds=clouds)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--mode",choices=("truth","model"),default="truth")
    args=p.parse_args();data=prepare(args.source,args.mode)
    if args.output.exists(): raise FileExistsError("Use a new output directory; never overwrite a demo")
    args.output.mkdir(parents=True)
    (args.output/"render_spec.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    backend=Path(__file__).resolve().parents[1]
    for name in ("index.html","render-viewer.js","GLTFLoader.js"):
        shutil.copyfile(backend/"app/static/house_render"/name,args.output/name)
    for name in ("three.min.js","OrbitControls.js"):
        shutil.copyfile(backend/"app/static/preview"/name,args.output/name)
    shutil.copyfile(backend.parent/"docs/house-render-export.md",args.output/"README.md")
    print(f"Prepared {len(data['objects'])} objects, {len(data['walls'])} wall panels")


if __name__=="__main__":main()
