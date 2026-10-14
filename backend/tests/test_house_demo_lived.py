import numpy as np
import pytest

from pipeline.house_demo import ROOMS, PORTALS, generate_room, furniture_specs
from pipeline.house_demo_layout import analyze_house, analyze_room
from pipeline.space_foundation import _corners


@pytest.fixture
def lived(monkeypatch):
    monkeypatch.setenv("HOUSE_DEMO_PROFILE", "lived")


def _overlap(a,b):
    aa=_corners(np.array(a["position"][:2]),np.array(a["size"][:2]),a["rotation"])
    bb=_corners(np.array(b["position"][:2]),np.array(b["size"][:2]),b["rotation"])
    for polygon in (aa,bb):
        for i in range(4):
            edge=polygon[(i+1)%4]-polygon[i]
            axis=np.array([-edge[1],edge[0]])/np.linalg.norm(edge)
            if min((aa@axis).max(),(bb@axis).max())-max((aa@axis).min(),(bb@axis).min()) <= .002:
                return False
    return True


@pytest.mark.parametrize("room",ROOMS,ids=lambda r:r["id"])
def test_lived_counts_points_clear_portals_and_no_unintended_intersection(lived,room):
    points,colors,layers,objects=generate_room(room,.06)
    assert 6<=sum(o["role"]=="major" for o in objects)<=8
    assert len(points)<300000 and np.isfinite(points).all()
    lo=np.array(room["origin"]);hi=lo+room["size"]
    for item in objects:
        a,b=item["point_range"]
        assert (points[a:b]>=lo-.001).all() and (points[a:b]<=hi+.001).all()
    for portal in PORTALS:
        if room["id"] not in portal["rooms"]: continue
        obstruct=(abs(points[:,0]-portal["plane"])<.041)&(abs(points[:,1]-portal["center"])<.449)&(points[:,2]>.01)&(points[:,2]<2.099)
        assert not obstruct.any()
    for i,a in enumerate(objects):
        for b in objects[i+1:]:
            if a.get("assembly") and a["assembly"]==b.get("assembly"): continue
            if {a["object"],b["object"]}=={"shower","shower_head"}: continue
            az=[a["position"][2]-a["size"][2]/2,a["position"][2]+a["size"][2]/2]
            bz=[b["position"][2]-b["size"][2]/2,b["position"][2]+b["size"][2]/2]
            if min(az[1],bz[1])-max(az[0],bz[0])<=.005: continue
            assert not _overlap(a,b), (room["id"],a["object"],b["object"])


def test_clutter_supported_by_actual_table_not_floating(lived):
    for room in ROOMS:
        _,_,_,objects=generate_room(room,.08)
        for item in objects:
            if not item.get("supported_by"): continue
            support=next(o for o in objects if o["object"]==item["supported_by"])
            bottom=item["position"][2]-item["size"][2]/2
            top=support["position"][2]+support["size"][2]/2
            assert bottom==pytest.approx(top)
            theta=np.deg2rad(support["rotation"])
            delta=np.array(item["position"][:2])-support["position"][:2]
            local=delta@np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])
            assert (abs(local)+np.array(item["size"][:2])/2 <= np.array(support["size"][:2])/2).all()


def test_all_rooms_have_a_geometric_route_and_traceable_advice(lived):
    objects=[o for room in ROOMS for o in generate_room(room,.08)[3]]
    result=analyze_house({"rooms":ROOMS,"portals":PORTALS},objects,"synthetic_ground_truth")
    for room in result["rooms"].values():
        assert all(route["status"]=="derived" for route in room["routes"]), room["room_id"]
        assert room["safety_status"]=="not_assessed"
        assert all(.08<r["minimum_width_m"]<=.9 for r in room["routes"])
    living=result["rooms"]["living_room"]
    assert any(a["rule"]=="near_furniture_demo" for a in living["advice"])
    assert all(a["value_m"]<.30 for a in living["advice"] if a["rule"]=="near_furniture_demo")


def test_missing_model_objects_are_unknown_not_safe():
    result=analyze_room(ROOMS[0],[],PORTALS,"spatiallm_predictions","done")
    assert result["status"]=="not_evaluable" and result["advice"]==[]
    assert result["routes"]==[] and result["safety_status"]=="not_assessed"


def test_model_analysis_does_not_use_truth_pairs():
    obj={"instance_id":"model_one","object":"sofa","position":[1,3,.5],"size":[1,.6,1],"rotation":0,"room_id":"living_room"}
    result=analyze_room(ROOMS[0],[obj],PORTALS,"spatiallm_predictions","done")
    assert result["pairs"]==[]
    assert result["uncertainty"]=="model_may_miss_objects"


def test_occupancy_is_not_erased_at_door():
    obj={"instance_id":"blocking","object":"cabinet","position":[5.7,2,.5],"size":[.6,.9,1],"rotation":0}
    result=analyze_room(ROOMS[0],[obj],PORTALS,"synthetic_ground_truth")
    assert result["doorways"][0]["occupied_area_m2"]>.4
    assert result["routes"][0]["status"]=="blocked"
    assert result["routes"][0]["minimum_width_m"] is None


def test_advice_changes_with_geometry_not_room_name():
    sofa={"instance_id":"a","room_id":"living_room","object":"sofa","position":[1.5,3,.5],"size":[2,.8,1],"rotation":0}
    table={"instance_id":"b","room_id":"living_room","object":"coffee_table","position":[1.5,2.2,.25],"size":[1,.5,.5],"rotation":0}
    near=analyze_room(ROOMS[0],[sofa,table],PORTALS,"spatiallm_predictions","done")
    assert any(a["rule"]=="near_furniture_demo" for a in near["advice"])
    table["position"]=[4,1,.25]
    far=analyze_room(ROOMS[0],[sofa,table],PORTALS,"spatiallm_predictions","done")
    assert not any(a["rule"]=="near_furniture_demo" for a in far["advice"])
    assert near["pairs"][0]["clearance_m"]==pytest.approx(.15)
