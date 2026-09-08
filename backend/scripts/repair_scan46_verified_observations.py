"""Append only multi-frame, plane-verified real observations to the locked cloud.

No texture rectangles, axis-based floor assumptions, synthetic points, or writes
to selection/analysis. Registration is estimated, never a hand-entered offset.
"""
import hashlib
import json
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs, fit_plane, POST, OUT, ROOT

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run():
    baseline=POST/'scene_preview_local_surface_repair_candidate.ply'
    expected='d69f79e46870812c63f4217901815ff3daaf14563684f878f8ce9418327546a1'
    if digest(baseline)!=expected: raise RuntimeError('Baseline identity changed')
    base=o.io.read_point_cloud(str(baseline)); xyz=np.asarray(base.points); rgb=np.asarray(base.colors)
    # The historical manifest identifies the first 507637 records as real observations.
    real=xyz[:507637]; tree=cKDTree(real); alltree=cKDTree(xyz)
    maps,images,R,t=load_inputs()
    contract=json.loads((OUT/'registration_contract.json').read_text())
    T=np.array(contract['transform'])
    if contract['after_fitness']<.7 or contract['after_rmse']>.035:
        raise RuntimeError('Global registration failed')
    def metric(p): return (np.asarray(p,float)@R.T+t)@T[:3,:3].T+T[:3,3]
    regions={}
    for name,frame,rects in [('floor',199,[(40,210,15,200)]),
                              ('window',145,[(30,50,10,130),(50,200,10,32),(50,200,110,145),(195,220,15,135)])]:
        p=metric(maps[frame])
        obs=np.concatenate([p[a:b,c:d].reshape(-1,3) for a,b,c,d in rects])
        distance,ids=tree.query(obs)
        anchors=real[np.unique(ids[distance<.03])]
        if len(anchors)<100: raise RuntimeError(f'{name}: too few real anchors')
        plane,support=fit_plane(anchors)
        # Fit only measured surrounding surfaces. A majority and low residual
        # are necessary; a wall inferred solely from an axis is never accepted.
        if len(support)<.5*len(anchors): raise RuntimeError(f'{name}: ambiguous plane')
        _,_,vt=np.linalg.svd(support-support.mean(0),full_matrices=False)
        axes=vt[:2].T
        uv=obs@axes
        if name=='floor':
            # Visible hole located in the fixed XZ audit projection (its depth
            # axis is Y). Bounds refer to the visible hole, not the old XY ROI.
            corners=np.array([[x,0.,z] for x in [-.35,.50] for z in [1.35,2.00]])
            corners[:,1]=-(corners[:,0]*plane[0]+corners[:,2]*plane[2]+plane[3])/plane[1]
            uv=corners@axes
        regions[name]={'plane':plane,'axes':axes,'lower':np.quantile(uv,.02,axis=0),
                       'upper':np.quantile(uv,.98,axis=0),'support':len(support),'votes':{}}
    if abs(regions['floor']['plane'][:3]@regions['window']['plane'][:3])>.3:
        raise RuntimeError('Physical window/floor planes are not distinguishable')
    conf=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
    def consume(points,colors,confidence,frame_id):
        valid=np.isfinite(points).all(1)&np.isfinite(confidence)&(confidence>=3.)
        points=points[valid];colors=colors[valid]
        for region in regions.values():
            plane=region['plane']; uv=points@region['axes']
            mask=(abs(points@plane[:3]+plane[3])<.025)&np.all((uv>=region['lower'])&(uv<=region['upper']),axis=1)
            ps,cs=points[mask],colors[mask]
            if not len(ps):continue
            # One vote per frame per voxel, avoiding repeated pixels as evidence.
            keys=np.floor(ps/.008).astype(np.int64)
            _,inds=np.unique(keys,axis=0,return_index=True)
            for k,p,c in zip(keys[inds],ps[inds],cs[inds]):
                key=tuple(k); row=region['votes'].setdefault(key,[p,c,set()]); row[2].add(frame_id)
    for i in range(0,len(maps),3):
        consume(metric(maps[i]).reshape(-1,3),np.asarray(images[i]).reshape(-1,3)/255.,np.asarray(conf[i]).ravel(),('main',i))
    # Supplement sequences contain duplicated main frames as explicit anchors.
    # Their pixel correspondences establish a similarity transform without ICP
    # guesses. Independent supplement images must confirm accepted voxels.
    from fuse_scan46_buchong2_four_regions import robust_similarity
    supplement_reports=[]
    for clip in [2,3]:
        work=ROOT/f'data/work/46/supplement_buchong2_clip{clip}'
        mapping=json.loads((work/'supplement_mapping.json').read_text())['mapping']
        pred=work/'slam3r/scene/preds'
        sm=np.load(pred/'registered_pcds.npy',mmap_mode='r'); si=np.load(pred/'input_imgs.npy',mmap_mode='r'); sc=np.load(pred/'registered_confs.npy',mmap_mode='r')
        src=[];dst=[]
        first_supp=min(int(e['output_index']) for e in mapping if e['kind']=='supplement')
        preceding=[e for e in mapping if e['kind']=='anchor' and int(e['output_index'])<first_supp]
        nearest_base=int(preceding[-1]['baseline_index'])
        # Use the adjacent contiguous anchor block, not unrelated earlier
        # video passes with different accumulated reconstruction drift.
        adjacent_ids=set(range(nearest_base-6,nearest_base+1))
        for entry in mapping:
            if entry['kind']!='anchor':continue
            i,j=int(entry['output_index']),int(entry['baseline_index'])
            if j not in adjacent_ids:continue
            # Guard against stale frame manifests by verifying actual images.
            if np.mean(abs(np.asarray(si[i],float)-np.asarray(images[j],float)))>2.:continue
            valid=(np.asarray(sc[i]).ravel()>8)&(np.asarray(conf[j]).ravel()>8)
            ids=np.flatnonzero(valid)[::20]
            src.append(np.asarray(sm[i]).reshape(-1,3)[ids]);dst.append(np.asarray(maps[j]).reshape(-1,3)[ids])
        if not src:
            supplement_reports.append({'clip':clip,'accepted':False,'reason':'no verified identical anchor images'});continue
        src,dst=np.concatenate(src),np.concatenate(dst)
        s,rot,trans,res=robust_similarity(src,dst)
        residual=float(np.quantile(res,.9)*np.linalg.norm(R[:,0]))
        supplement_reports.append({'clip':clip,'accepted':residual<.035,'anchor_p90_m':residual})
        if residual>=.035:continue
        for entry in mapping:
            if entry['kind']!='supplement':continue
            i=int(entry['output_index'])
            p=s*(np.asarray(sm[i],float).reshape(-1,3)@rot.T)+trans
            consume(metric(p),np.asarray(si[i]).reshape(-1,3)/255.,np.asarray(sc[i]).ravel(),(clip,i))
    additions=[];colors=[];report={}
    for name,region in regions.items():
        rows=[row for row in region['votes'].values() if len(row[2])>=3]
        pts=np.array([r[0] for r in rows]).reshape(-1,3);cols=np.array([r[1] for r in rows]).reshape(-1,3)
        if len(pts):
            distances,_=alltree.query(pts);keep=distances>.009
            pts,cols=pts[keep],cols[keep]
        report[name]={'baseline_plane':region['plane'].tolist(),'support':region['support'],
                      'multi_frame_voxels':len(rows),'added':len(pts),'roi_lower':region['lower'].tolist(),'roi_upper':region['upper'].tolist()}
        additions.append(pts);colors.append(cols)
    # Preserve *all* 627241 locked records numerically, including their colours.
    result=o.geometry.PointCloud();result.points=o.utility.Vector3dVector(np.vstack([xyz,*additions]));result.colors=o.utility.Vector3dVector(np.vstack([rgb,*colors]))
    output=OUT/'scene_preview_verified_observations_v3.ply'
    if output.exists():raise RuntimeError('Candidate already exists; refuse overwrite')
    o.io.write_point_cloud(str(output),result)
    check=o.io.read_point_cloud(str(output))
    assert np.array_equal(np.asarray(check.points)[:len(xyz)],xyz)
    assert np.array_equal(np.asarray(check.colors)[:len(rgb)],rgb)
    report.update({'baseline_sha256':expected,'baseline_points_unchanged':len(xyz),'candidate_sha256':digest(output),
        'supplements':supplement_reports,'formal_selection_changed':False,'status':'candidate_requires_visual_review',
        'synthetic_points':0,'output':str(output)})
    (OUT/'verified_repair_v3.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':run()
