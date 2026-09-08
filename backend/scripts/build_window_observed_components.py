"""Window-only observed-surface fusion, with explicit masks and source lineage.

No plane filling, whole-room filtering, or changes to production selection.
"""
import json,hashlib,argparse,base64
from pathlib import Path
import cv2
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import ROOT,POST
from window_observed_surface import sample_cells

DEST=ROOT/'data/work/46/window_local_sequence_20260907'


def transform(p,t):
    return t['scale']*(np.asarray(p,float)@np.asarray(t['rotation']).T)+t['translation']


def arrays(folder):
    p=folder/'slam3r/window/preds'
    return [np.load(p/n,mmap_mode='r') for n in ['registered_pcds.npy','input_imgs.npy','registered_confs.npy']]


def mask_rect(*rects):
    result=np.zeros((224,224),bool)
    for a,b,c,d in rects:result[a:b,c:d]=True
    return result


def lower_mask():
    # Visible wall/radiator above the photographed bed boundary in frame160.
    # This excludes every bed pixel, rather than using a world-coordinate cut.
    mask=np.zeros((224,224),np.uint8)
    poly=np.array([[40,140],[174,141],[214,157],[214,192],[180,186],
                   [150,180],[110,174],[70,166],[40,158]],np.int32)
    cv2.fillPoly(mask,[poly],1)
    return mask.astype(bool)


def extract(name,folder,t,ref,mask,report):
    maps,images,conf=arrays(folder)
    wp=transform(maps[ref],t);rgb=np.asarray(images[ref],float)/255.
    p=wp[mask];c=rgb[mask];votes=np.zeros(len(p),int)
    # A calibrated pinhole fit can lose steep cloth folds at subpixel edges.
    # Test actual nearby 3D+RGB observations instead; never expand geometry.
    for f in range(max(0,ref-5),min(len(maps),ref+6)):
        if f==ref:continue
        q=transform(maps[f],t).reshape(-1,3);col=np.asarray(images[f]).reshape(-1,3)/255.
        good=np.isfinite(q).all(1)&(np.asarray(conf[f]).ravel()>=3)
        q,col=q[good],col[good]
        d,ix=cKDTree(q).query(p,k=3)
        vote=((d<.025)&(np.max(abs(col[ix]-c[:,None,:]),axis=-1)<.12)).any(1)
        votes+=vote
    finite=np.isfinite(wp).all(-1)
    confident=mask&finite&(conf[ref]>=3)
    region=confident&(wp[:,:,2]>-.65)&(wp[:,:,2]<.2)&(wp[:,:,1]>-1)&(wp[:,:,1]<1.15)
    support=np.zeros((224,224),int);support[mask]=votes
    valid=region&(support>=3)
    dense,dc=sample_cells(wp,rgb,valid,subdivisions=3,max_edge=.04)
    p=np.vstack([wp[valid],dense]);c=np.vstack([rgb[valid],dc])
    _,ids=np.unique(np.floor(p/.003).astype(int),axis=0,return_index=True);p,c=p[ids],c[ids]
    report[name]={'reference':ref,'raw':int(mask.sum()),'confidence':int(confident.sum()),
        'region':int(region.sum()),'multiview':int(valid.sum()),'observed_resampled':len(p)}
    print(name,report[name],flush=True)
    return p,c


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--name',default='window_observed_complete_v1')
    parser.add_argument('--single-curtain', action='store_true',
                        help='Use one reference for the entire right curtain to avoid overlapping folds')
    parser.add_argument('--visual-glass', action='store_true',
                        help='Export a separate real-RGB glass display layer, excluded from the PLY')
    args=parser.parse_args();target=DEST/(args.name+'.ply');assert not target.exists()
    primary=json.loads((DEST/'integration_components_extent.json').read_text())['transform']
    left=json.loads((DEST/'window_local_components_rim_v2_left.json').read_text())
    y,x=np.mgrid[:224,:224]
    selections=[
        ('top_wall_frame',DEST,primary,25,mask_rect((0,36,0,158))),
        ('left_wall_frame',DEST,primary,25,mask_rect((36,191,0,59))),
        ('sill',DEST,primary,25,mask_rect((191,224,0,148))),
        ('lower_wall',DEST,primary,35,lower_mask()),
        ('curtain_right_edge',DEST,primary,25,(y>=36)&(x>=150-(y-36)*8/188)),
        ('curtain_right_body',DEST,primary,15,mask_rect((0,224,85,200),(0,105,200,224))),
        ('curtain_left',ROOT/'data/work/46/window_left_coverage_20260907',left,30,mask_rect((0,200,51,91))),
        ('wall_left_coverage',ROOT/'data/work/46/window_left_coverage_20260907',left,30,mask_rect((0,160,0,51))),
    ]
    if args.single_curtain:
        selections=[row for row in selections if row[0]!='curtain_right_edge']
    report={'components':{},'single_curtain':args.single_curtain};ps=[];cs=[];labels=[]
    for label,folder,t,ref,mask in selections:
        p,c=extract(label,folder,t,ref,mask,report['components'])
        if ps:
            gap=cKDTree(np.vstack(ps)).query(p)[0]>.009
            p,c=p[gap],c[gap]
        ps.append(p);cs.append(c);labels.extend([label]*len(p))
    p,c=np.vstack(ps),np.vstack(cs)
    # Withdraw only the previously audited artificial window sheets and old rim.
    source=POST/'scene_preview_window_rim_opaque.ply'
    base=o.io.read_point_cloud(str(source));bp=np.asarray(base.points);bc=np.asarray(base.colors)
    removed=np.load(DEST/'window_local_components_rim_v2_left_unoccluded.removed_indices.npy')
    keep=np.ones(633278,bool);keep[removed]=False
    assert not np.any(removed<507637) and not np.any(removed>=621290)
    result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp[:633278][keep],p])))
    result.colors=o.utility.Vector3dVector(np.vstack([bc[:633278][keep],c]))
    assert o.io.write_point_cloud(str(target),result)
    check=o.io.read_point_cloud(str(target));n=int(keep.sum())
    assert np.array_equal(np.asarray(check.points)[:n],bp[:633278][keep])
    assert np.array_equal(np.asarray(check.colors)[:n],bc[:633278][keep])
    np.savez_compressed(target.with_suffix('.lineage.npz'),baseline_indices=np.flatnonzero(keep),labels=labels)
    report.update(preserved_baseline=n,removed_artificial_window=len(removed),replaced_old_rim=7460,
        added_window=len(p),points=len(result.points),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        candidate_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),status='candidate',
        window_bounds=[p.min(0).tolist(),p.max(0).tolist()])
    target.with_suffix('.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    if args.visual_glass:
        from audit_scan46_window_camera import camera
        maps,images,_=arrays(DEST)
        world=transform(maps[25],primary)
        K,rot,origin,error=camera(world)
        if error>1.5:raise RuntimeError('Display camera reprojection failed')
        # Include the observed jamb/reveal border in the visual-only opening.
        # The physical sill and curtain surface remain independent observations.
        pixels=np.array([[43,20],[165,20],[160,215],[36,205]],float)
        border=np.vstack([world[22:28,59:145].reshape(-1,3),
                          world[38:191,46:51].reshape(-1,3)])
        center=border.mean(0);_,_,vt=np.linalg.svd(border-center,full_matrices=False)
        normal=vt[-1];plane=np.r_[normal,-normal@center]
        rays=np.c_[pixels,np.ones(4)]@np.linalg.inv(K).T@rot
        depth=-(origin@normal+plane[3])/(rays@normal)
        vertices=origin+rays*depth[:,None]
        if not np.all((depth>0)&(depth<5)):raise RuntimeError('Display plane depth failed')
        rgb=np.asarray(images[25],np.uint8)
        ok,png=cv2.imencode('.png',cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR));assert ok
        layer=dict(purpose='visual-only',source_frame=150,camera_rms_px=error,
                   vertices=vertices.tolist(),uv=(pixels/223).tolist(),
                   texture='data:image/png;base64,'+base64.b64encode(png).decode(),
                   pointcloud=target.name,excluded_from_measurements=True)
        target.with_suffix('.visual.json').write_text(json.dumps(layer))
        # Software-render-only samples verify the same plane and real RGB.
        mask=np.zeros((224,224),np.uint8);cv2.fillPoly(mask,[pixels.astype(np.int32)],1)
        yy,xx=np.nonzero(mask);rays=np.c_[xx,yy,np.ones(len(xx))]@np.linalg.inv(K).T@rot
        depth=-(origin@normal+plane[3])/(rays@normal)
        display=o.geometry.PointCloud(result)
        display+=o.geometry.PointCloud(o.utility.Vector3dVector(origin+rays*depth[:,None]))
        display.colors=o.utility.Vector3dVector(np.vstack([np.asarray(result.colors),rgb[yy,xx]/255.]))
        o.io.write_point_cloud(str(target.with_name(target.stem+'_display_qa.ply')),display)

if __name__=='__main__':main()
