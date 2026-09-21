"""Read-only stage audit. Diagnostic outputs never change preview selection."""
import json
import numpy as np
import open3d as o
from verify_september12_candidate import D, ROOT, POST, VIEWS, depths, project
from review_window_camera import render
from scipy.spatial import cKDTree
from complete_september12_gaps import transform
from fuse_anchored_supplements import robust_similarity

def main():
    out=D/'rejection_audit';out.mkdir(exist_ok=True)
    base=o.io.read_point_cloud(str(POST/'window_components_reveal_review.ply'))
    display=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'))
    vp,vc=np.asarray(display.points),np.asarray(display.colors)
    rows={}
    for name,path,skip in [('shelf',D/'shelf_gap_candidate.ply',len(base.points)),('window',D/'window_bridge/additions.ply',0)]:
        cloud=o.io.read_point_cloud(str(path));p=np.asarray(cloud.points)[skip:];c=np.asarray(cloud.colors)[skip:]
        gaps=np.zeros(len(p),int);occludes=np.zeros(len(p),bool)
        per_view={}
        for label,v in VIEWS.items():
            zbuf=depths(vp,v);xy,z,valid=project(p,v);ids=np.flatnonzero(valid);xx,yy=xy[ids].T
            gaps[ids]+=~np.isfinite(zbuf[yy,xx])
            overlap=np.zeros(len(p),bool)
            for dy in range(-2,3):
                for dx in range(-2,3):
                    old=zbuf[yy+dy,xx+dx];overlap[ids]|=np.isfinite(old)&(z[ids]<=old)
            occludes|=overlap
            per_view[label]={'visible_candidates':len(ids),'occlusion_rejections':int(overlap.sum())}
            K,r,center=v
            with np.errstate(divide='ignore',invalid='ignore'):
                render(np.vstack([vp,p]),np.vstack([vc,c]),K,r,center).save(out/f'{name}_unfiltered_{label}.png')
                render(np.vstack([vp,p]),np.vstack([vc*.3,np.tile([1.,0.,1.],(len(p),1))]),K,r,center).save(out/f'{name}_new_points_{label}.png')
        keep=(gaps>=1)&~occludes
        rows[name]={'before_export_filter':len(p),'no_black_center_in_any_view':int((gaps==0).sum()),'overlap_rejected':int(occludes.sum()),'survives_before_cluster_filter':int(keep.sum()),'views':per_view}
    (out/'counts.json').write_text(json.dumps(rows,indent=2))
    print(json.dumps(rows),flush=True)

def radiator_probe():
    """Test opaque-only registration and lower radiator coverage; NOT an accepted output."""
    out=D/'rejection_audit';out.mkdir(exist_ok=True)
    pred=D/'window_bridge/slam3r/window/preds'
    m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r')
    primary=ROOT/'data/work/46/window_local_sequence_20260907'
    pm=np.load(primary/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r')
    pt=json.loads((primary/'integration_components_extent.json').read_text())['transform']
    base=o.io.read_point_cloud(str(POST/'local_gaps_20260912.ply'));bp=np.asarray(base.points);tree=cKDTree(bp)
    y,x=np.mgrid[:224,:224]
    # Hand-inspected opaque wall / radiator, never glass, plants, curtain or bedding.
    masks={8:(x<32)&(y>60)&(y<190),12:(x<35)&(y>65)&(y<175),16:((x>106)&(x<171)&(y>185)&(y<205)),20:((x>103)&(x<163)&(y>178)&(y<200))}
    a=[];b=[];frames=[]
    for f,mask in masks.items():
        q=transform(pm[f+16],pt).reshape(-1,3);d,ids=tree.query(q)
        ok=mask.ravel()&(d<.025)&(cf[f].ravel()>8)
        ix=np.flatnonzero(ok)[::3];a.extend(m[f].reshape(-1,3)[ix]);b.extend(bp[ids[ix]]);frames.extend([f]*len(ix))
    a,b=np.array(a),np.array(b);train=np.isin(frames,[8,16]);s,r,t,_=robust_similarity(a[train],b[train]);err=np.linalg.norm(s*(a@r.T)+t-b,axis=1)
    reg={'scale':s,'rotation':r.tolist(),'translation':t.tolist(),'heldout_m':np.quantile(err[~train],[.5,.9,.95]).tolist(),'static_anchor_count':len(a)}
    (out/'static_registration.json').write_text(json.dumps(reg,indent=2));print(reg,flush=True)
    # Independent hypothesis: a local translation, estimated ONLY from radiator
    # in anchor 16; anchor 20 remains held out. Do not warp individual points.
    original=json.loads((D/'window_bridge/registration.json').read_text())
    predicted=transform(a,original);frames=np.asarray(frames)
    delta=np.median(b[frames==16]-predicted[frames==16],axis=0)
    local_error=np.linalg.norm(predicted+delta-b,axis=1)
    local=dict(original);local['translation']=(np.array(original['translation'])+delta).tolist()
    local['local_translation_m']=delta.tolist();local['radiator_heldout_m']=np.quantile(local_error[frames==20],[.5,.9,.95]).tolist()
    (out/'radiator_local_registration.json').write_text(json.dumps(local,indent=2));print('radiator-local',local['radiator_heldout_m'],flush=True)
    reg=local
    parts=[];colors=[];counts=[]
    for f in [61,65,69,73]:
        q=transform(m[f],reg);rgb=im[f]/255.;raw=(y>155)&(cf[f]>5)&np.isfinite(q).all(-1)
        # Lower opaque radiator only, below sill, inside window-side volume.
        valid=raw&(q[:,:,2]>-.65)&(q[:,:,2]<.25)
        p=q[valid];c=rgb[valid];dist=tree.query(p)[0]
        counts.append({'frame':f,'raw':int(raw.sum()),'volume':len(p),'baseline_distance_quantiles_m':np.quantile(dist,[.1,.5,.9]).tolist(),'old_near_filter_kept':int((dist<.045).sum())})
        support=np.zeros(len(p),int)
        for k in range(f-3,f+4):
            if k==f:continue
            kk=cf[k]>5;other=transform(m[k],reg)[kk];col=(im[k]/255.)[kk];dd,ix=cKDTree(other).query(p)
            support+=(dd<.015)&(np.max(abs(c-col[ix]),axis=1)<.12)
        keep=(support>=3)&(dist>.008)&(dist<.15);parts.append(p[keep]);colors.append(c[keep]);counts[-1]['supported_gap']=int(keep.sum())
    p,c=np.vstack(parts),np.vstack(colors)
    cloud=o.geometry.PointCloud(o.utility.Vector3dVector(p));cloud.colors=o.utility.Vector3dVector(c);o.io.write_point_cloud(str(out/'radiator_probe_NOT_ACCEPTED.ply'),cloud)
    display=o.io.read_point_cloud(str(primary/'window_components_reveal_review_display_qa.ply'));vp,vc=np.asarray(display.points),np.asarray(display.colors)
    for label,v in VIEWS.items():
        K,r,center=v
        with np.errstate(divide='ignore',invalid='ignore'):
            render(np.vstack([vp,p]),np.vstack([vc,c]),K,r,center).save(out/f'radiator_probe_{label}.png')
    (out/'radiator_stage_counts.json').write_text(json.dumps(counts,indent=2));print(counts,flush=True)

def lower_shelf_probe():
    import cv2
    from audit_scan46_window_camera import camera
    folder=D/'orange_lower';out=D/'rejection_audit';pred=folder/'slam3r/scene/preds'
    m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r');reg=json.loads((folder/'registration.json').read_text())
    base=o.io.read_point_cloud(str(POST/'local_gaps_20260912.ply'));bp,bc=np.asarray(base.points),np.asarray(base.colors);tree=cKDTree(bp)
    q=transform(m[0],reg);rgb=im[0]/255.;mask=np.zeros((224,224),np.uint8)
    cv2.fillPoly(mask,[np.array([[80,85],[119,126],[121,220],[85,174]]),np.array([[122,128],[201,104],[194,187],[124,217]])],1)
    raw=(mask>0)&(cf[0]>5);p=q[raw];c=rgb[raw];support=np.zeros(len(p),int)
    for k in range(1,7):
        valid=cf[k]>5;other=transform(m[k],reg)[valid];col=(im[k]/255.)[valid];d,ix=cKDTree(other).query(p);support+=(d<.018)&(np.max(abs(col[ix]-c),axis=1)<.15)
    dist=tree.query(p)[0];keep=(support>=3)&(dist>.009)&(dist<.12);p,c=p[keep],c[keep]
    views=dict(VIEWS)
    for f in [0,10,20]:
        K,r,center,_=camera(transform(m[f],reg));views[f'cabinet_{f}']=(K,r,center)
    display=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'));vp,vc=np.asarray(display.points),np.asarray(display.colors)
    for label,(K,r,center) in views.items():
        with np.errstate(divide='ignore',invalid='ignore'):
            render(np.vstack([vp,p]),np.vstack([vc,c]),K,r,center).save(out/f'lower_shelf_probe_{label}.png')
            render(np.vstack([vp,p]),np.vstack([vc*.3,np.tile([1.,0.,1.],(len(p),1))]),K,r,center).save(out/f'lower_shelf_new_points_{label}.png')
    cloud=o.geometry.PointCloud(o.utility.Vector3dVector(p));cloud.colors=o.utility.Vector3dVector(c);o.io.write_point_cloud(str(out/'lower_shelf_probe_NOT_ACCEPTED.ply'),cloud)
    report={'raw':int(raw.sum()),'multiview_supported':int((support>=3).sum()),'gap_candidates':len(p),'source_frame':0,'source_time_s':6,'baseline_distance_m':np.quantile(dist,[.1,.5,.9]).tolist()};(out/'lower_shelf_counts.json').write_text(json.dumps(report,indent=2));print(report,flush=True)

def white_shelf_probe():
    from window_observed_surface import sample_cells
    folder=D/'white_shelf_side';out=D/'rejection_audit';pred=folder/'slam3r/scene/preds'
    m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r');reg=json.loads((folder/'registration.json').read_text())
    base=o.io.read_point_cloud(str(POST/'local_gaps_20260912.ply'));bp,bc=np.asarray(base.points),np.asarray(base.colors);tree=cKDTree(bp)
    y,x=np.mgrid[:224,:224];q=transform(m[20],reg);rgb=im[20]/255.
    raw=(x>131)&(x<167)&(y>101)&(y<210)&(cf[20]>5);p=q[raw];c=rgb[raw];support=np.zeros(len(p),int)
    for k in range(16,25):
        if k==20:continue
        valid=cf[k]>5;other=transform(m[k],reg)[valid];col=(im[k]/255.)[valid];d,ix=cKDTree(other).query(p);support+=(d<.018)&(np.max(abs(col[ix]-c),axis=1)<.15)
    good=np.zeros((224,224),bool);yy,xx=np.nonzero(raw);good[yy[support>=3],xx[support>=3]]=True
    dense,dc=sample_cells(q,rgb,good,subdivisions=3,max_edge=.025);p=np.vstack([q[good],dense]);c=np.vstack([rgb[good],dc]);dist=tree.query(p)[0];keep=(dist>.01)&(dist<.18);p,c=p[keep],c[keep]
    display=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'));vp,vc=np.asarray(display.points),np.asarray(display.colors)
    for label,(K,r,center) in VIEWS.items():
        with np.errstate(divide='ignore',invalid='ignore'):
            render(np.vstack([vp,p]),np.vstack([vc,c]),K,r,center).save(out/f'white_shelf_probe_{label}.png')
            render(np.vstack([vp,p]),np.vstack([vc*.3,np.tile([1.,0.,1.],(len(p),1))]),K,r,center).save(out/f'white_shelf_new_points_{label}.png')
    cloud=o.geometry.PointCloud(o.utility.Vector3dVector(p));cloud.colors=o.utility.Vector3dVector(c);o.io.write_point_cloud(str(out/'white_shelf_probe_NOT_ACCEPTED.ply'),cloud)
    report={'raw':int(raw.sum()),'multiview_supported':int(good.sum()),'gap_candidates':len(p),'source_frame':20,'source_time_s':15};(out/'white_shelf_counts.json').write_text(json.dumps(report,indent=2));print(report,flush=True)

def bounded_pnp_validation():
    """One bounded camera-route validation, no export or preview selection writes."""
    import cv2
    from audit_scan46_window_camera import camera
    from local_gap_geometry import estimate_pose
    folder=D/'white_shelf_side';out=D/'pnp_validation';out.mkdir(exist_ok=True)
    evidence=np.load(folder/'correspondences.npz')
    maps=np.load(folder/'slam3r/scene/preds/registered_pcds.npy',mmap_mode='r')
    rows=[]
    for frame in [10,20]:
        mask=evidence['pairs'][:,0]==frame;p=evidence['source'][mask];q=evidence['target'][mask]
        k,rotation,center,ray_error=camera(maps[frame])
        camera_points=(p-center)@rotation.T;uv=camera_points@k.T;uv=uv[:,:2]/uv[:,2:]
        # Repeated matches to multiple target frames are NOT independent anchors.
        keys=np.rint(uv*2).astype(int);unique,labels=np.unique(keys,axis=0,return_inverse=True)
        pixels=[];targets=[];spreads=[]
        for i in range(len(unique)):
            group=q[labels==i];middle=np.median(group,axis=0);spread=np.max(np.linalg.norm(group-middle,axis=1))
            if spread>.04:continue
            pixels.append(np.mean(uv[labels==i],axis=0));targets.append(middle);spreads.append(float(spread))
        pixels=np.array(pixels);targets=np.array(targets)
        row={'frame':frame,'raw_correspondences':len(p),'unique_consistent_anchors':len(pixels),'ray_fit_rms_px':ray_error}
        if len(pixels)<20:
            row['status']='reject_insufficient_independent_anchors';rows.append(row);continue
        # Alternate spatially sorted anchors: no reuse of training anchors in validation.
        order=np.lexsort((pixels[:,1],pixels[:,0]));held=order[::3];train=np.setdiff1d(order,held)
        pose=estimate_pose(targets[train],pixels[train],k)
        if pose is None:
            row['status']='reject_camera_fit';rows.append(row);continue
        r=pose['rotation'];c=pose['center'];projected=cv2.projectPoints(targets[held],cv2.Rodrigues(r)[0],-r@c,k,None)[0].reshape(-1,2)
        errors=np.linalg.norm(projected-pixels[held],axis=1)
        row.update(train=len(train),heldout=len(held),train_inliers=len(pose['inliers']),heldout_px=np.quantile(errors,[.5,.9,.95]).tolist(),intrinsic=k.tolist(),rotation=r.tolist(),center=c.tolist())
        row['status']='pass' if row['heldout_px'][1]<=2. and len(pose['inliers'])>=12 else 'reject_heldout_reprojection'
        rows.append(row)
    (out/'white_shelf_pose_validation.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows),flush=True)

if __name__=='__main__':
    import sys
    if '--radiator-probe' in sys.argv:radiator_probe()
    elif '--shelf-probe' in sys.argv:lower_shelf_probe()
    elif '--white-shelf-probe' in sys.argv:white_shelf_probe()
    elif '--bounded-pnp' in sys.argv:bounded_pnp_validation()
    else:main()
