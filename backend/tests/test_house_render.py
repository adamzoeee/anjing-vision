import importlib.util
import json
from pathlib import Path
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/house_render_prepare.py'
spec=importlib.util.spec_from_file_location('render_prepare',SCRIPT)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def contains(panel,p):
    return all(abs(p[i]-panel['position'][i])<panel['size'][i]/2-1e-7 for i in range(3))


def test_wall_door_and_window_are_real_openings():
    rooms=[dict(id='a',origin=[0,0,0],size=[6,4,3]),dict(id='b',origin=[6,0,0],size=[1.5,4,3])]
    doors=[dict(axis='x',plane=6,center=2,width=.9,height=2.1)]
    windows=[dict(axis=1,plane=4,center=2,width=1.8,bottom=1,height=1.3)]
    walls=module.wall_panels(rooms,doors,windows)
    for y in (1.551,2,2.449):
        for z in (.01,1,2.099):assert not any(contains(w,(6,y,z)) for w in walls)
    assert not any(contains(w,(2,4,1.5)) for w in walls)
    assert any(contains(w,(6,2,2.6)) for w in walls)
    assert any(contains(w,(2,4,.5)) for w in walls)
    keys=[tuple(w['position']+w['size']) for w in walls]
    assert len(keys)==len(set(keys))


@pytest.fixture
def source(tmp_path):
    room=dict(id='living_room',origin=[0,0,0],size=[6,4,3],buffer='room.bin',points=1)
    (tmp_path/'manifest.json').write_text(json.dumps(dict(synthetic=True,unit='m',up_axis='z',rooms=[room],portals=[])))
    (tmp_path/'room.bin').write_bytes(bytes(24))
    obj=dict(instance_id='one',room_id='living_room',object='bed',position=[2,2,.5],size=[2,1.8,1],rotation=15,rotation_unit='degrees')
    (tmp_path/'ground_truth.json').write_text(json.dumps(dict(objects=[obj])))
    return tmp_path


def test_truth_preserves_transform_and_provenance(source):
    data=module.prepare(source)
    assert data['objects'][0]['rotation']==15
    assert data['objects'][0]['size']==[2,1.8,1]
    assert data['furniture_source']=='synthetic_ground_truth'
    assert 'manifest.json' in data['source_hashes']


def test_model_never_fills_missing_furniture(source):
    (source/'predictions.json').write_text(json.dumps(dict(rooms=dict(living_room=dict(status='done',objects=[])))))
    data=module.prepare(source,'model')
    assert data['objects']==[] and data['furniture_source']=='spatiallm_predictions'
    (source/'predictions.json').write_text(json.dumps(dict(rooms={})))
    with pytest.raises(ValueError,match='completed'):module.prepare(source,'model')


def test_wrong_unit_and_damaged_cloud_rejected(source):
    (source/'room.bin').write_bytes(b'bad')
    with pytest.raises(ValueError,match='buffer'):module.prepare(source)
    data=module.read(source/'manifest.json');data['unit']='cm'
    (source/'manifest.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='metre'):module.prepare(source)
