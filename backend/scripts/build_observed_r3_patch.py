"""Restore an observed bed-edge strip excluded by the old lower-wall mask.

Independent display candidate; unchanged source coordinates, no new camera fit,
no plane fill, no edits to baseline. The source is the same frame used by the
accepted radiator/lower-wall component (local35 / original160).
"""
import json
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree
from build_window_observed_components import arrays, transform, DEST
from visual_window_patch import grid_faces, safe_faces
from verify_september12_candidate import VIEWS, project
from summarize_viewer_gap import POLYS, depth
from viewer_gap_diagnostic import OUT

def main(region_name='R3',ref=35,canonical=False):
    maps,imgs,conf=arrays(DEST)
    t=json.loads((DEST/'integration_components_extent.json').read_text())['transform']
    if canonical:
        from audit_scan46_observation_planes import load_inputs,OUT as ALIGN,ROOT
        full,full_rgb,rotation,shift=load_inputs()
        pose=np.asarray(json.loads((ALIGN/'registration_contract.json').read_text())['transform'])
        scale=float(np.linalg.norm(rotation[:,0]))
        t={'scale':scale,'rotation':(pose[:3,:3]@rotation/scale).tolist(),
           'translation':(pose[:3,:3]@shift+pose[:3,3]).tolist()}
        maps=full[125:176];imgs=full_rgb[125:176]
        conf=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')[125:176]
    world=transform(maps[ref],t);rgb=imgs[ref]/255.
    polygon=([(124,704),(276,707),(351,742),(328,790),(146,794)]
             if region_name=='R5' else POLYS['front'][region_name])
    im=Image.new('1',(896,896));ImageDraw.Draw(im).polygon(polygon,fill=1)
    region=np.asarray(im);xy,z,valid=project(world.reshape(-1,3),VIEWS['front'])
    selected=np.zeros(len(valid),bool);ids=np.flatnonzero(valid)
    selected[ids]=region[xy[ids,1],xy[ids,0]]
    selected=selected.reshape(224,224)&(conf[ref]>=3)&np.isfinite(world).all(-1)
    source_proof=Image.fromarray(np.asarray(imgs[ref],np.uint8)).resize((896,896))
    proof_draw=ImageDraw.Draw(source_proof)
    for py,px in zip(*np.nonzero(selected)):
        proof_draw.point((int(px)*4,int(py)*4),fill=(255,0,255))
    source_proof.save(OUT/(region_name.lower()+'_selected_source.png'))
    if region_name=='R1' and not canonical:
        # Same state/reference as the accepted curtain body. The full source
        # shows curtain at 200..208 and the bookcase at 210..223. Keep them
        # separate; this is not an inferred wall behind the furniture.
        yy,xx=np.mgrid[:224,:224]
        selected&=(xx>=200)&(xx!=209)&(yy>=105)
    if canonical:
        # Frame130: photographed right curtain only; no sill, bookcase, glass
        # or exterior pixels. Canonical depth, not the warped local-window map.
        yy,xx=np.mgrid[:224,:224]
        selected&=(xx>=100)&(yy<158)
    pixels=np.flatnonzero(selected);p=world.reshape(-1,3)[pixels];c=rgb.reshape(-1,3)[pixels]
    votes=np.zeros(len(p),int)
    for f in range(ref-5,ref+6):
        if f==ref:continue
        q=transform(maps[f],t);good=np.isfinite(q).all(-1)&(conf[f]>=3)
        dist,ix=cKDTree(q[good]).query(p,k=3)
        votes+=((dist<.025)&(np.max(abs(imgs[f][good][ix]/255.-c[:,None,:]),axis=-1)<.12)).any(1)
    selected.flat[pixels[votes<3]]=False
    # Do not put a new bed layer over an existing surface in either audit view.
    # At occupied pixels, require <=15mm agreement; elsewhere keep observed data.
    exclusions={}
    for name in ['front','oblique','overview','overhead']:
        xy,z,valid=project(world.reshape(-1,3),VIEWS[name]);ids=np.flatnonzero(valid)
        old=depth(name+'_formal')[xy[ids,1],xy[ids,0]]
        bad=ids[np.isfinite(old)&(z[ids]<old-.015)]
        exclusions[name]=int(selected.ravel()[bad].sum());selected.flat[bad]=False
    yy,xx=np.mgrid[:224,:224];uv=np.c_[xx[selected],yy[selected]]
    index=np.full((224,224),-1);index[selected]=np.arange(selected.sum())
    labels=np.ones((224,224),int)
    if region_name=='R1' and not canonical:labels[:,210:]=2
    faces=safe_faces(world[selected],uv,grid_faces(index,labels),selected,max_edge=.04)
    patch={'positions':world[selected].tolist(),'uv':uv.tolist(),'faces':faces.tolist()}
    prefix='canonical_r1' if canonical else region_name.lower()
    (OUT/(prefix+'_patch.json')).write_text(json.dumps(patch))
    from viewer_gap_diagnostic import ROOT
    original=Image.open(ROOT/f'data/work/45/frames/frame_{126+ref:05d}.jpg').convert('RGB')
    w,h=original.size;s=round(224*max(w/h,h/w));nw,nh=round(w*s/max(w,h)),round(h*s/max(w,h));ox,oy=nw//2-112,nh//2-112
    original.resize((nw*4,nh*4),Image.Resampling.LANCZOS).crop((ox*4,oy*4,(ox+224)*4,(oy+224)*4)).save(OUT/(prefix+'_texture.png'))
    report={'source_frame':125+ref,'local_frame':ref,'source_selected':len(p),'supported':int((votes>=3).sum()),'vertices':int(selected.sum()),'triangles':len(faces),'occluding_existing_rejected':exclusions,'coordinates_unchanged':True,'status':'isolated_unreviewed','surface':{'R1':'separate_observed_curtain_edge_and_bookcase','R3':'observed_bed_cover_edge_not_wall','R5':'bedhead_region_pending_source_review'}[region_name]}
    (OUT/(prefix+'_patch_audit.json')).write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':
    import sys
    if '--canonical-right' in sys.argv:main('R1',5,canonical=True)
    elif '--bedhead' in sys.argv:main('R5',35)
    else:main();main('R1',15)
