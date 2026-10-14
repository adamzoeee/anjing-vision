"""Blender 4.5: independent, editable household proxy scene and interchange exports.

blender --background --factory-startup --python this.py -- --spec render_spec.json
"""
import argparse
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

M={}
COUNTS={}


def empty(name,parent=None):
    o=bpy.data.objects.new(name,None);bpy.context.scene.collection.objects.link(o);o.parent=parent
    return o


def material(name,color,rough=.55,metal=0,transmission=0,noise=False):
    m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True
    bs=m.node_tree.nodes.get('Principled BSDF')
    bs.inputs['Base Color'].default_value=(*color,1)
    bs.inputs['Roughness'].default_value=rough;bs.inputs['Metallic'].default_value=metal
    bs.inputs['Transmission Weight'].default_value=transmission
    if noise:
        n=m.node_tree.nodes.new('ShaderNodeTexNoise');n.inputs['Scale'].default_value=75
        bump=m.node_tree.nodes.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.12;bump.inputs['Distance'].default_value=.015
        m.node_tree.links.new(n.outputs['Fac'],bump.inputs['Height']);m.node_tree.links.new(bump.outputs['Normal'],bs.inputs['Normal'])
    M[name]=m


def finish(o,name,parent,mat):
    o.name=name;o.parent=parent;o.data.materials.append(M[mat])
    return o


def box(name,center,size,parent,mat='oak',bevel=.015):
    bpy.ops.mesh.primitive_cube_add(size=1)
    o=bpy.context.object;o.location=center;o.scale=size
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    if bevel:
        mod=o.modifiers.new('soft_edges','BEVEL');mod.width=min(bevel,min(size)*.2);mod.segments=3
        mod=o.modifiers.new('weighted_normals','WEIGHTED_NORMAL')
    return finish(o,name,parent,mat)


def sphere(name,center,size,parent,mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=20,ring_count=12,radius=.5)
    o=bpy.context.object;o.location=center;o.scale=size
    for p in o.data.polygons:p.use_smooth=True
    return finish(o,name,parent,mat)


def cylinder(name,center,radius,depth,parent,mat='metal',rotation=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=32,radius=radius,depth=depth)
    o=bpy.context.object;o.location=center
    if rotation:o.rotation_euler=rotation
    for p in o.data.polygons:p.use_smooth=True
    return finish(o,name,parent,mat)


def ring_bowl(name,parent,cx,cy,rx,ry,z,depth,mat='ceramic'):
    # Outer lip, inner lip, inner bottom: a genuinely open bowl, not a solid ellipsoid.
    verts=[];faces=[];n=48
    for factor,height in ((1,z),(.84,z),(.58,z-depth)):
        for i in range(n):
            t=2*math.pi*i/n;verts.append((cx+rx*factor*math.cos(t),cy+ry*factor*math.sin(t),height))
    for k in range(2):
        for i in range(n):j=(i+1)%n;faces.append((k*n+i,k*n+j,(k+1)*n+j,(k+1)*n+i))
    faces.append(tuple(range(2*n,3*n)))
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update()
    o=bpy.data.objects.new(name,mesh);bpy.context.scene.collection.objects.link(o)
    for p in mesh.polygons:p.use_smooth=True
    return finish(o,name,parent,mat)


def furnishing(obj,group):
    cat=obj['object'];name=cat+'_'+obj['instance_id'];parent=empty(name,group)
    parent.location=(*obj['position'][:2],obj['position'][2]-obj['size'][2]/2)
    parent.rotation_euler[2]=math.radians(obj['rotation']);parent.scale=obj['size']
    parent['source']=obj.get('source','unknown');parent['category']=cat
    parent['instance_id']=obj['instance_id'];parent['nominal_size_m']=obj['size']
    COUNTS[cat]=COUNTS.get(cat,0)+1
    def b(part,c,s,mat='oak',bev=.015):return box(name+'_'+part,c,s,parent,mat,bev)
    def e(part,c,s,mat):return sphere(name+'_'+part,c,s,parent,mat)
    def cy(part,c,r,d,mat='metal',rot=None):return cylinder(name+'_'+part,c,r,d,parent,mat,rot)
    def legs(top=.8):
        for x in (-.4,.4):
            for y in (-.36,.36):b('leg',(x,y,top/2),(.045,.045,top),'dark_oak')
    aliases={'coffee_table':'table','dining_table':'table','dining_chair':'chair','cupboard':'cabinet',
             'refrigerator':'fridge','hand_sink':'vanity','shower_room':'shower','clothes_rack':'coat_rack','painting':'wall_art'}
    cat=aliases.get(cat,cat)
    if cat=='sofa':
        b('base',(0,0,.28),(.94,.94,.38),'fabric_green',.06)
        b('back',(0,.39,.68),(.98,.20,.64),'fabric_green',.06)
        for x in (-.45,.45):b('arm',(x,0,.52),(.10,.94,.50),'fabric_green',.035)
        for x in (-.28,0,.28):
            b('seat',(x,-.06,.52),(.27,.72,.16),'fabric_green',.035)
            p=b('cushion',(x,.27,.76),(.27,.13,.38),'linen',.04);p.rotation_euler[0]=-.12
        for x in (-.4,.4):
            for y in (-.35,.35):cy('foot',(x,y,.06),.025,.12,'dark_oak')
    elif cat=='bed':
        b('frame',(0,0,.20),(.98,.98,.23),'oak',.025)
        b('mattress',(0,-.02,.43),(.95,.92,.20),'linen',.055)
        b('headboard',(0,.46,.54),(1,.08,.92),'fabric_sand',.03)
        b('duvet',(0,-.16,.55),(.96,.63,.07),'fabric_blue',.045)
        for x in (-.23,.23):e('pillow',(x,.29,.59),(.4,.22,.12),'linen')
    elif cat in {'table','side_table','desk','chair'}:
        top=.48 if cat=='chair' else .94
        legs(top-.045);b('top',(0,0,top),(.98,.98,.09),'fabric_sand' if cat=='chair' else 'oak',.035)
        if cat=='chair':b('back',(0,.43,.76),(.94,.10,.48),'oak',.04)
        if cat=='desk':b('drawer',(0,.12,.78),(.75,.65,.14),'paint_sage')
    elif cat in {'wardrobe','cabinet','tv_stand','nightstand','shoe_cabinet','storage_cabinet','storage_box','fridge'}:
        mat='appliance' if cat=='fridge' else ('paint_sage' if cat=='cabinet' else 'oak')
        b('body',(0,.025,.50),(1,.95,1),mat)
        count=3 if cat in {'cabinet','tv_stand','shoe_cabinet'} else 2
        for i in range(count):
            x=-.5+(i+.5)/count
            b('door',(x,-.475,.51),(.98/count,.045,.96),mat)
            b('handle',(x+.27/count,-.505,.56),(.018,.018,.12),'metal',.004)
    elif cat=='countertop':
        # Match the installed sink aperture, without filling it with countertop.
        b('left',(-.44,0,.5),(.12,1,1),'stone')
        b('right',(.235,0,.5),(.53,1,1),'stone')
        b('back',(-.23,.45,.5),(.30,.10,1),'stone')
        b('front',(-.23,-.45,.5),(.30,.10,1),'stone')
    elif cat=='tv':
        b('housing',(0,0,.59),(.99,.21,.80),'black',.012)
        b('screen',(0,-.112,.59),(.95,.012,.74),'screen',.004)
        b('pedestal',(0,0,.14),(.06,.15,.27),'metal');b('foot',(0,0,.035),(.56,.95,.07),'black')
    elif cat=='window':
        for x in (-.48,0,.48):b('frame',(x,0,.5),(.035,.95,1),'white',.005)
        for z in (.02,.98):b('frame',(0,0,z),(1,.95,.04),'white',.005)
        b('glazing',(0,0,.5),(.94,.06,.93),'glass',0)
    elif cat=='curtain':
        for side in (-1,1):
            verts=[];faces=[];nx,nz=40,16
            for iz in range(nz+1):
                z=iz/nz
                for ix in range(nx+1):
                    u=ix/nx;x=side*(.24+.25*u)
                    y=.28*math.sin(u*10*math.pi)+.05*math.sin(z*math.pi)
                    verts.append((x,y,.03+.91*z))
            for iz in range(nz):
                for ix in range(nx):
                    a=iz*(nx+1)+ix;faces.append((a,a+1,a+nx+2,a+nx+1))
            mesh=bpy.data.meshes.new(name+'_folds');mesh.from_pydata(verts,[],faces);mesh.update()
            o=bpy.data.objects.new(name+'_fabric',mesh);bpy.context.scene.collection.objects.link(o);finish(o,o.name,parent,'fabric_sand')
            for p in mesh.polygons:p.use_smooth=True
        b('rail',(0,0,.98),(1,.2,.025),'metal')
    elif cat in {'sink','vanity'}:
        if cat=='vanity':b('cabinet',(0,0,.39),(.96,.94,.78),'oak')
        level=.94 if cat=='vanity' else .37
        ring_bowl(name+'_basin',parent,0,0,.47,.43,level,.18)
        cy('tap',(0,.37,min(.91,level+.18)),.025,.16,'metal')
        b('spout',(0,.26,min(.99,level+.25)),(.04,.23,.045),'metal')
    elif cat=='toilet':
        e('pedestal',(0,-.08,.24),(.5,.56,.48),'ceramic')
        ring_bowl(name+'_bowl',parent,0,-.10,.48,.37,.58,.15)
        b('tank',(0,.35,.69),(.84,.29,.59),'ceramic',.06)
        b('button',(0,.35,.996),(.15,.08,.007),'metal')
    elif cat=='washing_machine':
        b('body',(0,0,.5),(1,.95,1),'appliance',.04)
        cy('rim',(0,-.49,.46),.34,.035,'metal',(math.pi/2,0,0))
        cy('glass',(0,-.515,.46),.28,.012,'screen',(math.pi/2,0,0))
        b('controls',(0,-.49,.89),(.9,.025,.12),'white')
        cy('dial',(.25,-.51,.89),.045,.02,'metal',(math.pi/2,0,0))
    elif cat=='stove':
        b('plate',(0,0,.3),(1,1,.6),'black')
        for x in (-.25,.25):
            for y in (-.24,.24):cy('burner',(x,y,.75),.16,.20,'metal')
    elif cat=='range_hood':
        b('canopy',(0,0,.12),(1,1,.24),'metal');b('flue',(0,.23,.61),(.45,.46,.78),'metal')
    elif cat=='mirror':b('mirror',(0,0,.5),(1,1,1),'mirror',.01)
    elif cat=='shower':
        b('tray',(0,0,.018),(1,1,.036),'ceramic')
        b('screen',(.48,0,.52),(.018,1,.96),'glass',0)
        for y in (-.48,.48):b('edge',(.48,y,.52),(.02,.02,.96),'metal',0)
    elif cat=='shower_head':
        cy('pipe',(0,.3,.45),.045,.9,'metal');b('arm',(0,0,.9),(.1,.65,.05),'metal')
        cy('head',(0,-.3,.96),.35,.04,'metal')
    elif cat=='towel_rack':
        b('rail',(0,0,.9),(1,.12,.06),'metal');b('towel',(0,-.12,.43),(.75,.06,.82),'fabric_blue')
    elif cat=='plant':
        cy('pot',(0,0,.15),.28,.3,'terracotta')
        cy('soil',(0,0,.302),.255,.008,'soil');cy('stem',(0,0,.58),.018,.56,'green')
        for i in range(11):
            a=i*2.4;z=.42+.045*i
            leaf=e('leaf',(.20*math.cos(a),.20*math.sin(a),z),(.46,.13,.09),'green')
            leaf.rotation_euler=(.2*math.sin(a),.25,a)
    elif cat in {'lamp','ceiling_light'}:
        cy('base',(0,0,.06),.30,.1,'metal');cy('stem',(0,0,.36),.035,.56,'metal')
        cy('shade',(0,0,.77),.48,.44,'linen')
    elif cat=='book':
        b('pages',(0,0,.5),(.96,.96,.8),'linen');b('cover',(0,0,.95),(1,1,.1),'fabric_blue')
    elif cat in {'cup','umbrella_stand'}:
        cy('body',(0,0,.45),.44,.9,'ceramic');cy('opening',(0,0,.905),.37,.008,'soil')
    elif cat=='coat_rack':
        cy('base',(0,0,.04),.47,.08,'metal');cy('stem',(0,0,.5),.045,1,'oak')
        b('hooks',(0,0,.88),(.95,.06,.03),'oak')
    elif cat=='wall_art':
        b('frame',(0,0,.5),(1,1,1),'oak');b('art',(0,-.51,.5),(.88,.02,.88),'fabric_blue')
    else:
        b('proxy',(0,0,.5),(1,1,1),'paint_sage');parent['proxy_fallback']=True
    return parent


def aim(obj,target):obj.rotation_euler=(Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler()


def camera(name,loc,target,parent,ortho):
    data=bpy.data.cameras.new(name);o=bpy.data.objects.new(name,data);bpy.context.scene.collection.objects.link(o)
    o.parent=parent;o.location=loc;data.type='ORTHO';data.ortho_scale=ortho;data.clip_end=200;aim(o,target)
    return o


def fit_room_camera(cam,room,aspect=4/3):
    """Fit all room corners, including wall tops, without a room-specific zoom."""
    x,y,z=room['origin'];l,w,h=room['size'];target=Vector((x+l/2,y+w/2,z+h/2))
    aim(cam,target)
    inverse=cam.rotation_euler.to_quaternion().inverted()
    corners=[inverse@(Vector((a,b,c))-target) for a in (x,x+l) for b in (y,y+w) for c in (z-.09,z+h+.05)]
    width=max(p.x for p in corners)-min(p.x for p in corners)
    height=max(p.y for p in corners)-min(p.y for p in corners)
    cam.data.ortho_scale=max(width,height*aspect)*1.10


def light(name,loc,power,parent,size=3):
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size
    o=bpy.data.objects.new(name,data);bpy.context.scene.collection.objects.link(o);o.location=loc;o.parent=parent
    return o


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--spec',type=Path,required=True)
    ap.add_argument('--samples',type=int,default=24);ap.add_argument('--resolution',type=int,default=1200)
    args=ap.parse_args(sys.argv[sys.argv.index('--')+1:]);args.spec=args.spec.resolve();out=args.spec.parent;spec=json.loads(args.spec.read_text(encoding='utf-8'))
    if (out/'house.blend').exists():raise FileExistsError('Existing render output; choose a new directory')
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    scene=bpy.context.scene;scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=args.samples;scene.cycles.use_denoising=True
    scene.render.threads_mode='FIXED';scene.render.threads=6
    scene.render.resolution_x=args.resolution;scene.render.resolution_y=int(args.resolution*.75);scene.render.resolution_percentage=100
    scene.world.color=(.35,.35,.35);scene.view_settings.view_transform='AgX';scene.view_settings.exposure=-.6
    for name,color,rough,metal,trans,noise in [
        ('wall',(.82,.79,.72),.85,0,0,False),('white',(.90,.90,.86),.5,0,0,False),
        ('oak',(.42,.25,.12),.5,0,0,True),('dark_oak',(.12,.07,.04),.5,0,0,False),
        ('fabric_green',(.13,.26,.22),.93,0,0,True),('fabric_blue',(.17,.31,.40),.85,0,0,True),
        ('fabric_sand',(.58,.48,.35),.9,0,0,True),('linen',(.85,.81,.70),.9,0,0,True),
        ('paint_sage',(.30,.40,.34),.55,0,0,False),('ceramic',(.92,.92,.88),.18,0,0,False),
        ('tile',(.61,.66,.65),.4,0,0,False),('stone',(.75,.73,.66),.35,0,0,True),
        ('metal',(.51,.55,.58),.25,.85,0,False),('mirror',(.85,.88,.91),.035,1,0,False),
        ('appliance',(.72,.75,.77),.32,.25,0,False),('black',(.018,.023,.028),.35,0,0,False),
        ('screen',(.025,.055,.075),.12,.3,0,False),('glass',(.88,.95,.98),.08,0,1,False),
        ('green',(.10,.28,.07),.7,0,0,False),('terracotta',(.38,.17,.10),.85,0,0,False),
        ('soil',(.055,.035,.02),1,0,0,False)]:material(name,color,rough,metal,trans,noise)
    root=empty('house');root['unit']='m';root['up_axis']='Z';root['furniture_source']=spec['furniture_source']
    groups={};furniture={};ceilings=[];walls=[]
    for room in spec['rooms']:
        rid=room['id'];g=empty(rid,root);groups[rid]=g;furniture[rid]=empty('furniture_'+rid,g)
        x,y,z=room['origin'];l,w,h=room['size']
        wet=rid in {'bathroom','kitchen'}
        box('floor_'+rid,(x+l/2,y+w/2,z-.045),(l,w,.09),g,'tile' if wet else 'oak',0)
        # Actual thin plank/tile joints; no external textures or asset licensing.
        step=.6 if wet else .22
        for i in range(1,math.ceil(w/step)):
            if i*step<w:box('joint_'+rid,(x+l/2,y+i*step,z+.0005),(l,.006,.001),g,'white' if wet else 'dark_oak',0)
        for i in range(1,math.ceil(l/(.6 if wet else 1.2))):
            px=i*(.6 if wet else 1.2)
            if px<l:box('joint_'+rid,(x+px,y+w/2,z+.0005),(.004,w,.001),g,'white' if wet else 'dark_oak',0)
        ceilings.append(box('ceiling_'+rid,(x+l/2,y+w/2,z+h+.025),(l,w,.05),g,'white',0))
    for i,wall in enumerate(spec['walls']):
        o=box('wall_'+wall['rooms'][0]+'_'+str(i).zfill(3),wall['position'],wall['size'],groups[wall['rooms'][0]],'wall',.003)
        o['rooms']=','.join(wall['rooms']);walls.append((o,wall))
    for i,p in enumerate(spec['portals']):
        g=empty('door_frame_'+str(i),root);g['rooms']=','.join(p['rooms']);x,y,w,h=p['plane'],p['center'],p['width'],p['height']
        for sign in (-1,1):box('door_jamb_'+str(i),(x,y+sign*(w/2+.025),h/2),(.09,.05,h),g,'oak',.004)
        box('door_header_'+str(i),(x,y,h+.025),(.09,w+.1,.05),g,'oak',.004)
        # Optional leaf is editable but hidden: the original pointcloud contains no door-swing evidence.
        leaf=box('door_leaf_optional_'+str(i),(x,y,h/2),(.035,w-.02,h-.02),g,'oak')
        leaf.hide_render=True;leaf.hide_viewport=True;leaf['optional_not_observed']=True
    for obj in spec['objects']:furnishing(obj,furniture[obj['room_id']])
    lights=empty('lights',root);cams=empty('cameras',root);camera_map={}
    for room in spec['rooms']:
        x,y,z=room['origin'];l,w,h=room['size'];rid=room['id']
        light('main_'+rid,(x+l/2,y+w/2,h-.18),200+25*l*w,lights,min(l,w)*.65)
        # Punctual companion survives glTF; area sources remain in Blender/FBX where supported.
        pd=bpy.data.lights.new('point_'+rid,'POINT');pd.energy=35;pd.shadow_soft_size=.3
        po=bpy.data.objects.new('point_'+rid,pd);scene.collection.objects.link(po);po.parent=lights;po.location=(x+l/2,y+w/2,h-.25)
        camera_map[rid]=camera('camera_'+rid,(x+l*1.32,y-w*.74,h*1.65),(x+l/2,y+w/2,1.1),cams,max(l,w)*1.65)
        fit_room_camera(camera_map[rid],room)
    fill=light('soft_daylight',(-3,-4,10),1800,lights,8);aim(fill,(5,3,0))
    camera_map['overview']=camera('camera_overview',(17,-14,18),(5.2,3.7,.4),cams,17)
    scene.camera=camera_map['overview']
    # Export complete geometry, including ceilings. Optional inferred door leaves remain excluded.
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'house.blend'))
    optional=[o for o in bpy.data.objects if o.get('optional_not_observed')]
    bpy.ops.object.select_all(action='DESELECT')
    for o in bpy.context.scene.objects:
        if o not in optional:o.select_set(True)
    bpy.ops.export_scene.fbx(filepath=str(out/'house.fbx'),use_selection=True,object_types={'EMPTY','MESH','LIGHT','CAMERA'},
                             axis_forward='-Z',axis_up='Y',apply_unit_scale=True,add_leaf_bones=False,bake_anim=False)
    bpy.ops.export_scene.gltf(filepath=str(out/'house.glb'),export_format='GLB',use_selection=True,
                              export_cameras=True,export_lights=True,export_extras=True)
    bpy.ops.export_scene.gltf(filepath=str(out/'house.gltf'),export_format='GLTF_SEPARATE',use_selection=True,
                              export_cameras=True,export_lights=True,export_extras=True)
    bpy.ops.wm.obj_export(filepath=str(out/'house.obj'),export_selected_objects=True,export_materials=True,
                         forward_axis='NEGATIVE_Z',up_axis='Y')
    audit={'unit':'m','blender_up_axis':'Z','interchange_up_axis':'Y','furniture_source':spec['furniture_source'],
           'objects':len(spec['objects']),'categories':COUNTS,'mesh_count':sum(o.type=='MESH' for o in scene.objects),
           'wall_panels':len(walls),'cameras':list(camera_map),'renders':[],
           'limitations':['parameter-driven synthetic proxies, not measured mesh','optional door leaves excluded from exports',
                         'area lights and procedural bump may not survive C4D interchange','no C4D installation validation']}
    # Dollhouse render visibility is temporary; saved/exported full scene remains editable.
    for o in ceilings:o.hide_render=True
    for key in ['overview','living_room','bedroom','kitchen','bathroom','corridor']:
        scene.camera=camera_map[key]
        for rid,g in groups.items():
            for o in g.children_recursive:o.hide_render=(key!='overview' and rid!=key)
        for o in ceilings:o.hide_render=True
        for o,wall in walls:
            included=key=='overview' or key in wall['rooms']
            if key=='overview':
                cut=wall['axis']==1 and wall['plane']==0
            else:
                room=next(r for r in spec['rooms'] if r['id']==key)
                cut=(wall['axis']==1 and abs(wall['plane']-room['origin'][1])<.01) or (wall['axis']==0 and abs(wall['plane']-room['origin'][0]-room['size'][0])<.01)
            o.hide_render=not included or cut
        for o in optional:o.hide_render=True
        for i,p in enumerate(spec['portals']):
            for o in bpy.data.objects['door_frame_'+str(i)].children:
                o.hide_render=o in optional or (key!='overview' and key not in p['rooms'])
        scene.render.filepath=str(out/(key+'.png'));bpy.ops.render.render(write_still=True);audit['renders'].append(key+'.png')
    (out/'build_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print('HOUSE_RENDER_EXPORT_SUCCESS',json.dumps(audit))


if __name__=='__main__':main()
