"""Round-trip exported meshes with Blender; explicitly not a C4D validation."""
import bpy
import json
import sys
from pathlib import Path
from mathutils import Vector


def stats():
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH' and not o.get('optional_not_observed')]
    points=[o.matrix_world@Vector(v) for o in meshes for v in o.bound_box]
    return dict(meshes=len(meshes),materials=len(bpy.data.materials),
                cameras=sum(o.type=='CAMERA' for o in bpy.context.scene.objects),
                lights=sum(o.type=='LIGHT' for o in bpy.context.scene.objects),
                bounds=[[min(p[i] for p in points) for i in range(3)],
                        [max(p[i] for p in points) for i in range(3)]])


root=Path(sys.argv[sys.argv.index('--')+1]).resolve();bpy.ops.wm.open_mainfile(filepath=str(root/'house.blend'))
expected=stats();result={'validator':'Blender round-trip, not C4D','expected':expected,'formats':{}}
transforms={o.name:o.matrix_world.copy() for o in bpy.context.scene.objects if o.get('instance_id')}
for ext in ('fbx','glb','obj'):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    path=str(root/('house.'+ext))
    if ext=='fbx':bpy.ops.import_scene.fbx(filepath=path)
    elif ext=='glb':bpy.ops.import_scene.gltf(filepath=path)
    else:bpy.ops.wm.obj_import(filepath=path,forward_axis='NEGATIVE_Z',up_axis='Y')
    current=stats()
    assert current['meshes']>100,(ext,current)
    assert current['materials']>10,(ext,current)
    error=max(abs(current['bounds'][a][b]-expected['bounds'][a][b]) for a in range(2) for b in range(3))
    assert error<.05,(ext,'scale/axes bounds mismatch',error)
    if ext!='obj':
        assert current['cameras']==6,(ext,current)
        assert bpy.data.objects.get('house') is not None
        for name,matrix in transforms.items():
            obj=bpy.data.objects.get(name)
            assert obj is not None,(ext,'missing furniture root',name)
            assert max(abs(obj.matrix_world[i][j]-matrix[i][j]) for i in range(4) for j in range(4))<1e-4,(ext,'transform mismatch',name)
        current['furniture_transforms_checked']=len(transforms)
    current['max_bounds_error_m']=error;current['status']='passed';result['formats'][ext]=current
(root/'export_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print('EXPORT_ROUNDTRIP_PASS',json.dumps(result))
