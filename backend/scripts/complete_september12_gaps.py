"""Append only validated shelf-top observations to an immutable baseline."""
import json,hashlib
import numpy as np,open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import ROOT,POST
from audit_scan46_window_camera import camera
from review_window_camera import render
from window_observed_surface import sample_cells

DEST=ROOT/'data/work/46/local_gaps_20260912'
def transform(p,t):return t['scale']*(np.asarray(p,float)@np.asarray(t['rotation']).T)+t['translation']

def new2_candidates():
    """Separate observed ROI candidates from the two new clips; never select them."""
    from audit_scan46_observation_planes import load_inputs,OUT
    from fuse_anchored_supplements import robust_similarity
    root=DEST/'new2';base=o.io.read_point_cloud(str(POST/'local_gaps_20260912.ply'));bp=np.asarray(base.points);bc=np.asarray(base.colors);tree=cKDTree(bp)
    main_maps,_,rotation,shift=load_inputs();contract=np.array(json.loads((OUT/'registration_contract.json').read_text())['transform'])
    primary=ROOT/'data/work/46/window_local_sequence_20260907';pm=np.load(primary/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r');pt=json.loads((primary/'integration_components_extent.json').read_text())['transform']
    y,x=np.mgrid[:224,:224];reports={}
    for name in ['shelf2','bed2','window2']:
        folder=root/name;pred=folder/'slam3r/scene/preds';m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r')
        if name=='shelf2':
            z=np.load(folder/'correspondences.npz');train=z['pairs'][:,0]==0;a=z['source'];b=z['target']
        else:
            masks=({0:(x<120)&(y<180)&(y>15),5:(x>110)&(y<150),10:((x>187)&(y<170))|((x>125)&(x<172)&(y>130)),15:((x>207)&(y<145))|((x>130)&(x<185)&(y>150))} if name=='bed2' else {8:(x<32)&(y>60)&(y<190),12:(x<35)&(y>65)&(y<175),16:(x>106)&(x<171)&(y>185)&(y<205),20:(x>103)&(x<163)&(y>178)&(y<200)})
            a=[];b=[];frameids=[];mapping=json.loads((folder/'mapping.json').read_text())
            for f,mask in masks.items():
                j=mapping[f]['baseline_index']
                target=transform(pm[j-125],pt) if name=='window2' else (np.asarray(main_maps[j],float)@rotation.T+shift)@contract[:3,:3].T+contract[:3,3]
                d,ix=tree.query(target.reshape(-1,3));good=mask.ravel()&(d<.025)&(cf[f].ravel()>8);ids=np.flatnonzero(good)[::4]
                a.extend(m[f].reshape(-1,3)[ids]);b.extend(bp[ix[ids]]);frameids.extend([f]*len(ids))
            a,b=np.array(a),np.array(b);train=np.isin(frameids,[0,10] if name=='bed2' else [8,16])
        s,r,t,_=robust_similarity(a[train],b[train]);error=np.linalg.norm(s*(a[~train]@r.T)+t-b[~train],axis=1)
        reg={'scale':s,'rotation':r.tolist(),'translation':t.tolist(),'heldout_m':np.quantile(error,[.5,.9,.95]).tolist(),'train':int(train.sum()),'heldout':int((~train).sum())}
        (folder/'validated_registration.json').write_text(json.dumps(reg,indent=2));print(name,reg,flush=True)
        if reg['heldout_m'][1]>.035:
            reports[name]={'status':'registration_rejected','registration':reg};continue
        selections=([(0,y<53),(10,y<103),(20,y<108)] if name=='shelf2' else [(21,(x>130)&(x<202)&(y>65)&(y<185)),(25,(x>142)&(x<209)&(y>58)&(y<177)),(30,(x>142)&(x<202)&(y>55)&(y<175))] if name=='bed2' else [(30,(x<105)&(x>5)&(y>62)&(y<104)),(35,(x>5)&(x<154)&(y>60)&(y<102)),(40,(x>5)&(x<215)&(y>44)&(y<94))])
        parts=[];colors=[];stages=[]
        for f,mask in selections:
            q=transform(m[f],reg);rgb=im[f]/255.;raw=mask&np.isfinite(q).all(-1)&(cf[f]>5);p=q[raw];c=rgb[raw];votes=np.zeros(len(p),int)
            for k in range(max(0 if name=='shelf2' else 21,f-4),min(len(m),f+5)):
                if k==f:continue
                valid=(cf[k]>5)&np.isfinite(m[k]).all(-1);other=transform(m[k],reg)[valid];col=(im[k]/255.)[valid];d,ix=cKDTree(other).query(p);votes+=(d<.018)&(np.max(abs(col[ix]-c),axis=1)<.15)
            valid=np.zeros((224,224),bool);yy,xx=np.nonzero(raw);valid[yy[votes>=3],xx[votes>=3]]=True
            dense,dc=sample_cells(q,rgb,valid,subdivisions=3,max_edge=.025);p=np.vstack([q[valid],dense]);c=np.vstack([rgb[valid],dc]);gap=tree.query(p)[0]>.008;p,c=p[gap],c[gap];parts.append(p);colors.append(c)
            stages.append({'frame':f,'roi_pixels':int(mask.sum()),'confidence_kept':int(raw.sum()),'multiview_kept':int(valid.sum()),'gap_candidates':len(p)})
        p,c=np.vstack(parts),np.vstack(colors);_,ids=np.unique(np.floor(p/.003).astype(int),axis=0,return_index=True);p,c=p[ids],c[ids]
        cloud=o.geometry.PointCloud(o.utility.Vector3dVector(p));cloud.colors=o.utility.Vector3dVector(c);o.io.write_point_cloud(str(folder/'observed_additions.ply'),cloud)
        reports[name]={'status':'candidate_only','registration':reg,'stages':stages,'points':len(p)}
    (root/'component_audit.json').write_text(json.dumps(reports,indent=2));print(json.dumps(reports),flush=True)

def shelf_patch():
    """Joint multi-view patch first; seam overlap is allowed, baseline stays exact."""
    from local_gap_geometry import fuse_multiview,missing_voxels,largest_surface_hole
    from verify_september12_candidate import VIEWS
    import cv2
    folder=DEST/'new2/shelf2';out=DEST/'new2/shelf_patch';out.mkdir(exist_ok=True)
    pred=folder/'slam3r/scene/preds';m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r');reg=json.loads((folder/'validated_registration.json').read_text())
    base=o.io.read_point_cloud(str(POST/'local_gaps_20260912.ply'));bp,bc=np.asarray(base.points),np.asarray(base.colors);tree=cKDTree(bp)
    # Existing joint reconstruction of the same 29-frame clip is reused, not rerun.
    pp=[];cc=[];ff=[]
    for f in range(0,len(m),2):
        q=transform(m[f],reg);rgb=im[f]/255.;valid=(cf[f]>5)&np.isfinite(q).all(-1)
        dense,color=sample_cells(q,rgb,valid,subdivisions=2,max_edge=.03)
        p=np.vstack([q[valid],dense]);c=np.vstack([rgb[valid],color]);pp.append(p);cc.append(c);ff.extend([f]*len(p))
    p,c=fuse_multiview(np.vstack(pp),np.vstack(cc),np.array(ff),.006,3)
    full=o.geometry.PointCloud(o.utility.Vector3dVector(p));full.colors=o.utility.Vector3dVector(c);o.io.write_point_cloud(str(out/'joint_patch.ply'),full)
    anchors=np.load(folder/'correspondences.npz');train=anchors['pairs'][:,0]==0;source=transform(anchors['source'],reg);target=anchors['target']
    # Static book-spine correspondences are registration support, not curtains.
    pc=o.geometry.PointCloud(o.utility.Vector3dVector(source[train]));ids=np.unique(tree.query(target[train],k=12)[1]);tc=o.geometry.PointCloud(o.utility.Vector3dVector(bp[ids]));tc.estimate_normals(o.geometry.KDTreeSearchParamHybrid(radius=.05,max_nn=30))
    fit=o.pipelines.registration.registration_icp(pc,tc,.035,np.eye(4),o.pipelines.registration.TransformationEstimationPointToPlane(o.pipelines.registration.TukeyLoss(k=.015)),o.pipelines.registration.ICPConvergenceCriteria(max_iteration=30))
    t=fit.transformation;before=np.linalg.norm(source[~train]-target[~train],axis=1);after=np.linalg.norm(source[~train]@t[:3,:3].T+t[:3,3]-target[~train],axis=1)
    use=bool(np.quantile(after,.9)<np.quantile(before,.9));report={'heldout_before_p90_m':float(np.quantile(before,.9)),'heldout_after_icp_p90_m':float(np.quantile(after,.9)),'icp_applied':use,'joint_patch_points':len(p)}
    if not use:t=np.eye(4)
    p=p@t[:3,:3].T+t[:3,3]
    # Check an affine depth bias against static anchors, without automatically applying it.
    k,r,center,_=camera(transform(m[0],reg));depth=(source-center)@r[2];wanted=(target-center)@r[2]
    ab=np.linalg.lstsq(np.c_[depth[train],np.ones(train.sum())],wanted[train],rcond=None)[0]
    raw=abs(depth[~train]-wanted[~train]);corrected=abs(ab[0]*depth[~train]+ab[1]-wanted[~train]);report['depth_bias_check']={'a':float(ab[0]),'b_m':float(ab[1]),'before_p90_m':float(np.quantile(raw,.9)),'after_p90_m':float(np.quantile(corrected,.9)),'applied':False,'reason':'diagnostic only; affine correction must improve heldout geometry without double-counting rigid alignment'}
    local=(p-center)@r.T;uv=local@k.T;uv=uv[:,:2]/uv[:,2:];roi=(local[:,2]>0)&(uv[:,0]>3)&(uv[:,0]<221)&(uv[:,1]>0)&(uv[:,1]<65)
    p,c=p[roi],c[roi];dist,ix=tree.query(p,k=24);neighbors=bp[ix];centroid=neighbors.mean(1);_,_,v=np.linalg.svd(neighbors-centroid[:,None],full_matrices=False);res=abs(np.sum((p-centroid)*v[:,-1],axis=1))
    overlap=dist[:,0]<.03;report['seam_residual_p90_m']=float(np.quantile(res[overlap],.9));report['seam_overlap_points']=int(overlap.sum())
    # Do not cut a 12 mm moat around a 6 mm patch. Occupied voxels deduplicate;
    # nearby disagreement still rejects a second surface instead of moving old points.
    keep=missing_voxels(p,bp,.006)&((dist[:,0]>.03)|(res<.02))
    report['overlap_disagreement_rejected']=int(((dist[:,0]<=.03)&(res>=.02)).sum())
    seam=keep&overlap
    report['accepted_seam_residual_p90_m']=float(np.quantile(res[seam],.9)) if seam.any() else None
    report['seam_metric_note']='PCA residual to nearby baseline; not an independent surface-thickness measurement'
    p,c=p[keep],c[keep];merged=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])));merged.colors=o.utility.Vector3dVector(np.vstack([bc,c]));path=out/'shelf_patch_candidate.ply';o.io.write_point_cloud(str(path),merged)
    check=o.io.read_point_cloud(str(path));assert np.array_equal(np.asarray(check.points)[:len(bp)],bp);assert np.array_equal(np.asarray(check.colors)[:len(bp)],bc)
    report['added_missing_voxel_points']=len(p);report['baseline_points_preserved']=len(bp)
    visual=json.loads((POST/'local_gaps_20260912.visual.json').read_text());visual['pointcloud']=path.name;path.with_suffix('.visual.json').write_text(json.dumps(visual))
    for f in [0,10,20]:
        k,r,center,_=camera(transform(m[f],reg));before=render(bp,bc,k,r,center);after=render(np.vstack([bp,p]),np.vstack([bc,c]),k,r,center);before.save(out/f'before_{f}.png');after.save(out/f'after_{f}.png')
        region=np.zeros((896,896),bool);region[12:440,12:884]=True
        metrics={}
        for kernel in [9,13,17]:
            a=largest_surface_hole(~(np.asarray(before)==[15,19,26]).all(-1),region,kernel)
            b=largest_surface_hole(~(np.asarray(after)==[15,19,26]).all(-1),region,kernel)
            metrics[str(kernel)]={'before':a,'after':b,'reduction_fraction':1-b/a if a else 0}
        report[f'view_{f}']={'sampling_gap_sensitivity':metrics}
        # Same render settings for both sides; these images are not used to inflate metrics.
        before=render(bp,bc,k,r,center,size=448,radius=2)
        after=render(np.vstack([bp,p]),np.vstack([bc,c]),k,r,center,size=448,radius=2)
        from PIL import Image
        comparison=Image.new('RGB',(896,448));comparison.paste(before,(0,0));comparison.paste(after,(448,0));comparison.save(out/f'comparison_{f}.png')
    qa=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'))
    vp=np.vstack([bp,np.asarray(qa.points)[1023460:]])
    vc=np.vstack([bc,np.asarray(qa.colors)[1023460:]])
    for label,eye in [('room',[1.,-4.,-1.7]),('room_oblique',[3.,-3.,-1.4])]:
        center=np.array(eye);forward=np.array([1.,0.,1.])-center;forward/=np.linalg.norm(forward)
        right=np.cross(forward,[0,-1,0]);right/=np.linalg.norm(right);down=np.cross(forward,right)
        rot=np.vstack([right,down,forward]);k=np.array([[240.,0,111.5],[0,240.,111.5],[0,0,1]])
        before=render(vp,vc,k,rot,center);after=render(np.vstack([vp,p]),np.vstack([vc,c]),k,rot,center)
        comparison=Image.new('RGB',(1792,896));comparison.paste(before,(0,0));comparison.paste(after,(896,0));comparison.save(out/f'comparison_{label}.png')
    report['status']='independent bookshelf-top candidate; formal selection unchanged'
    (out/'audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

def window_patch(primary=False):
    """One continuous window-side clip, rigid registration and immutable fusion."""
    from local_gap_geometry import fuse_multiview,missing_voxels,screen_hits
    from audit_screen_gaps import baseline,REGIONS
    from verify_september12_candidate import VIEWS
    from PIL import Image,ImageDraw
    import cv2
    folder=DEST/'new2/window2';out=DEST/('new2/window_primary_patch' if primary else 'new2/window_patch');out.mkdir(exist_ok=True)
    pred=folder/'slam3r/scene/preds';m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r')
    reg=json.loads((folder/'validated_registration.json').read_text())
    segment=json.loads((DEST/'new2/screen_first/window_segments.json').read_text())[0]
    assert segment['accepted']
    t=np.array(segment['correction']);bp,bc,vp,vc=baseline();tree=cKDTree(bp)
    if primary:
        folder=ROOT/'data/work/46/window_local_sequence_20260907';pred=folder/'slam3r/window/preds'
        m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r')
        reg=json.loads((folder/'integration_components_extent.json').read_text())['transform'];t=np.eye(4)
    world=lambda f:transform(m[f],reg)@t[:3,:3].T+t[:3,3]
    pp=[];cc=[];ff=[];yy,xx=np.mgrid[:224,:224]
    # 22–26.5 seconds: one curtain state. No primary-video curtain points mixed in.
    frames=list(range(0,20)) if primary else list(range(21,31))
    for f in frames:
        q=world(f);rgb=im[f]/255.
        # Window glass above / bedding below are excluded in source images.
        valid=(yy>=48)&(yy<125)&(cf[f]>5)&np.isfinite(q).all(-1)
        if primary:
            # The same primary sequence used by the good curtain baseline.
            left=np.interp(f,[0,5,10,15,19],[65,95,120,120,120])
            right=np.interp(f,[0,5,10,15,19],[180,200,220,220,220])
            bottom=np.interp(f,[0,5,10,15,19],[113,150,190,214,220])
            dy,dx=np.gradient(q,axis=(0,1));normal=np.cross(dx,dy);length=np.linalg.norm(normal,axis=-1)
            curtain=(xx>=left)&(xx<right)&(yy<bottom)
            # Below the curtain, the actual visible surface is a grey cushion,
            # not an unseen wall. Recover that observed window-side object only;
            # blue bedding remains outside the source support.
            cushion=(f<10)&(xx>=140)&(xx<214)&(yy>=bottom)&(yy<214)&(abs(rgb[:,:,0]-rgb[:,:,1])<.12)&(rgb[:,:,1]-rgb[:,:,2]>-.035)
            valid=(curtain|cushion)&(cf[f]>5)&np.isfinite(q).all(-1)&(length>1e-10)&(abs(normal[:,:,1])<.65*length)
        dense,color=sample_cells(q,rgb,valid,subdivisions=3,max_edge=.03)
        a=np.vstack([q[valid],dense]);b=np.vstack([rgb[valid],color]);pp.append(a);cc.append(b);ff.extend([f]*len(a))
    p,c=fuse_multiview(np.vstack(pp),np.vstack(cc),np.array(ff),.006,3)
    report={'source_frames':frames,'source_sequence':'primary' if primary else 'window2','joint_patch_points':len(p),'registration':reg if primary else segment,
            'registration_caveat':('reused baseline registration; rigid alignment does not validate local curtain depth' if primary else 'heldout nearest-baseline correspondences, not independent metrology'),
            'depth_correction_applied':False,'depth_correction_reason':'no independent multi-depth rigid correspondences supporting an affine depth correction'}
    full=o.geometry.PointCloud(o.utility.Vector3dVector(p));full.colors=o.utility.Vector3dVector(c);o.io.write_point_cloud(str(out/'joint_patch.ply'),full)
    # Anatomical extent, not black-pixel masks: overlap is allowed at the seam.
    scope=np.ones(len(p),bool)
    for label in ['front','oblique']:
        mask=np.zeros((896,896),np.uint8)
        for name,(view,poly) in REGIONS.items():
            if view==label and name in ['window_lower','window_lower_oblique','curtain_corner','curtain_corner_front']:
                cv2.fillPoly(mask,[np.array(poly,np.int32)],1)
        if primary:
            poly=([(535,245),(866,250),(865,440),(775,445),(775,710),(820,735),(820,790),(535,790)] if label=='front' else [(500,265),(736,300),(720,450),(672,465),(675,620),(580,680),(710,720),(635,780),(505,760)])
            cv2.fillPoly(mask,[np.array(poly,np.int32)],1)
        k,r,center=VIEWS[label];ks=k.copy();ks[:2]*=4
        scope&=screen_hits(p,ks,r,center,mask.astype(bool))
    p,c=p[scope],c[scope];report['component_scope_points']=len(p)
    dist,ix=tree.query(p,k=24);near=bp[ix];centroid=near.mean(1)
    _,singular,v=np.linalg.svd(near-centroid[:,None],full_matrices=False)
    residual=abs(np.sum((p-centroid)*v[:,-1],axis=1))
    # Vertical window-side surfaces only; do not add another layer of bedding.
    vertical=abs(v[:,-1,1])<.65
    seam=(dist[:,0]<.03);consistent=(~seam)|(residual<.02)
    # Fusion sampling (6 mm) is finer than alignment accuracy. Occupancy must
    # reflect the latter to avoid layering new curtain points over old fabric.
    keep=missing_voxels(p,bp,.02 if primary else .006)&consistent
    if not primary:keep&=vertical&(dist[:,0]<.12)
    report['rejected_seam_disagreement']=int((~consistent).sum());report['rejected_horizontal_neighborhood']=int((~vertical).sum())
    p,c=p[keep],c[keep]
    cloud=o.geometry.PointCloud(o.utility.Vector3dVector(p));labels=np.array(cloud.cluster_dbscan(eps=.02,min_points=6))
    ids,counts=np.unique(labels,return_counts=True);large=ids[(ids>=0)&(counts>=100)]
    good=np.isin(labels,large);p,c=p[good],c[good];report['connected_component_sizes']=counts[(ids>=0)&(counts>=100)].tolist()
    # Missing interiors may be far from old points; each whole patch must still
    # have a supported seam. Do not reject interiors just for being a large hole.
    if primary:
        retained=[];component_report=[];labels=labels[good]
        for ident in large:
            ids=np.flatnonzero(labels==ident);seam_count=int((tree.query(p[ids])[0]<.03).sum())
            component_report.append({'points':len(ids),'seam_points':seam_count})
            if seam_count>=20:retained.extend(ids.tolist())
        p,c=p[retained],c[retained];report['component_seams']=component_report
    path=out/'window_patch_candidate.ply';cloud=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])));cloud.colors=o.utility.Vector3dVector(np.vstack([bc,c]));o.io.write_point_cloud(str(path),cloud)
    saved=o.io.read_point_cloud(str(path));assert np.array_equal(np.asarray(saved.points)[:len(bp)],bp);assert np.array_equal(np.asarray(saved.colors)[:len(bp)],bc)
    visual=json.loads((POST/'local_gaps_20260912.visual.json').read_text());visual['pointcloud']=path.name;path.with_suffix('.visual.json').write_text(json.dumps(visual))
    report['added_points']=len(p);report['baseline_points_preserved']=len(bp)
    views=dict(VIEWS)
    for f in ([0,10,19] if primary else [21,25,29]):
        k,r,center,_=camera(world(f));views[f'close_{f}']=(k,r,center)
    for label,(k,r,center) in views.items():
        before=render(vp,vc,k,r,center);after=render(np.vstack([vp,p]),np.vstack([vc,c]),k,r,center)
        comparison=Image.new('RGB',(1792,928));comparison.paste(before,(0,32));comparison.paste(after,(896,32));draw=ImageDraw.Draw(comparison);draw.text((12,8),'BASELINE',fill='white');draw.text((908,8),'WINDOW PATCH CANDIDATE',fill='white');comparison.save(out/f'comparison_{label}.png')
        render(np.vstack([vp,p]),np.vstack([vc*.35,np.tile([1.,0.,1.],(len(p),1))]),k,r,center).save(out/f'additions_{label}.png')
    report['status']='candidate only; requires multi-view visual review; no formal selection change'
    (out/'audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

def window_seam():
    """Replace only independently contradicted pale curtain-edge remnants."""
    from audit_screen_gaps import baseline
    from verify_september12_candidate import VIEWS,project
    from local_gap_geometry import replace_local,missing_voxels
    from PIL import Image,ImageDraw
    out=DEST/'new2/window_seam';out.mkdir(exist_ok=True)
    bp,bc,vp,vc=baseline();xy,z,visible=project(bp,VIEWS['front'])
    allowed=visible&(xy[:,0]>615)&(xy[:,0]<790)&(xy[:,1]>650)&(xy[:,1]<672)
    # A second world view must locate the same narrow hem, not a window frame.
    uv,_,visible2=project(bp,VIEWS['oblique'])
    allowed&=visible2&(uv[:,0]>530)&(uv[:,0]<720)&(uv[:,1]>580)&(uv[:,1]<710)
    ids=np.flatnonzero(allowed);old=bp[ids];votes=np.zeros(len(ids),int);observations=[]
    folder=ROOT/'data/work/46/window_local_sequence_20260907';pred=folder/'slam3r/window/preds'
    m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r');reg=json.loads((folder/'integration_components_extent.json').read_text())['transform']
    for f in range(20):
        q=transform(m[f],reg);k,r,center,_=camera(q);local=(old-center)@r.T;projected=local@k.T;pixel=np.rint(projected[:,:2]/projected[:,2:]).astype(int)
        x,y=pixel.T;valid=(local[:,2]>0)&(x>=0)&(x<224)&(y>=0)&(y<224);j=np.flatnonzero(valid)
        error=np.linalg.norm(q[y[j],x[j]]-old[j],axis=1);delta=np.max(abs(im[f,y[j],x[j]]/255.-bc[ids[j]]),axis=1)
        evidence=(error<.03)&(delta>.15)&(cf[f,y[j],x[j]]>5)
        votes[j[evidence]]+=1;observations.append({'frame':f,'contradicting_points':int(evidence.sum())})
    remove=np.zeros(len(bp),bool);remove[ids[votes>=5]]=True
    assert remove.any(),'No independently supported removal'
    extent=np.ptp(bp[remove],axis=0);assert extent[1]<.08,'Seam removal exceeds 8 cm vertical extent'
    patch=o.io.read_point_cloud(str(DEST/'new2/window_primary_patch/joint_patch.ply'));p,c=np.asarray(patch.points),np.asarray(patch.colors)
    distance=cKDTree(bp[remove]).query(p)[0]
    # Only a narrow observed repair band: no whole-curtain replacement.
    near=distance<.08;p,c=p[near],c[near]
    surviving=bp[~remove];d=cKDTree(surviving).query(p)[0]
    good=missing_voxels(p,surviving,.012)&(d>.003);p,c=p[good],c[good]
    points,colors=replace_local(bp,bc,remove,allowed,p,c)
    path=out/'window_seam_candidate.ply';cloud=o.geometry.PointCloud(o.utility.Vector3dVector(points));cloud.colors=o.utility.Vector3dVector(colors);o.io.write_point_cloud(str(path),cloud)
    saved=o.io.read_point_cloud(str(path));assert np.array_equal(np.asarray(saved.points)[:len(surviving)],surviving);assert np.array_equal(np.asarray(saved.colors)[:len(surviving)],bc[~remove])
    # Persist exact reversible removal indices, not a broad delete operation.
    np.savez_compressed(out/'seam_evidence.npz',removed_indices=np.flatnonzero(remove),removed_points=bp[remove],removed_colors=bc[remove],eligible_indices=ids,evidence_votes=votes)
    visual=json.loads((POST/'local_gaps_20260912.visual.json').read_text());visual['pointcloud']=path.name;path.with_suffix('.visual.json').write_text(json.dumps(visual))
    base_display=np.vstack([surviving,vp[len(bp):]]);base_colors=np.vstack([bc[~remove],vc[len(bp):]])
    for label,(k,r,center) in VIEWS.items():
        before=render(vp,vc,k,r,center);after=render(np.vstack([base_display,p]),np.vstack([base_colors,c]),k,r,center)
        canvas=Image.new('RGB',(1792,928));canvas.paste(before,(0,32));canvas.paste(after,(896,32));draw=ImageDraw.Draw(canvas);draw.text((12,8),'BASELINE',fill='white');draw.text((908,8),'NARROW SEAM REPLACEMENT',fill='white');canvas.save(out/f'comparison_{label}.png')
        marked=vc.copy();marked[:len(bp)][remove]=[1.,0.,0.];render(vp,marked,k,r,center).save(out/f'removal_{label}.png')
    report={'removed_points':int(remove.sum()),'removed_world_extent_m':extent.tolist(),'added_points':len(p),'maximum_distance_from_verified_seam_m':.08,'retained_baseline_points_exact':len(surviving),'source_frames':list(range(20)),'evidence':observations,'status':'candidate only; no formal modification','scope_note':'new observation conflicts establish unreliable color/surface state, not proof of absolute old depth error'}
    (out/'audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

def main():
    folder=DEST/'shelf';reg=json.loads((folder/'registration.json').read_text());pred=folder/'slam3r/scene/preds'
    maps=np.load(pred/'registered_pcds.npy',mmap_mode='r');imgs=np.load(pred/'input_imgs.npy',mmap_mode='r');confs=np.load(pred/'registered_confs.npy',mmap_mode='r')
    source=POST/'window_components_reveal_review.ply';base=o.io.read_point_cloud(str(source));bp=np.asarray(base.points);bc=np.asarray(base.colors);tree=cKDTree(bp)
    y,x=np.mgrid[:224,:224];parts=[];colors=[];report=[]
    for ref in [0,8,16]:
        world=transform(maps[ref],reg);rgb=np.asarray(imgs[ref])/255
        mask=(y<(105 if ref==0 else 128 if ref==8 else 120))&(x>3)&(x<221)
        # Shelf top only; the bookcase front is registration support, never replaced.
        valid=mask&np.isfinite(world).all(-1)&(confs[ref]>=4)
        yy,xx=np.nonzero(valid);p=world[valid];c=rgb[valid];votes=np.zeros(len(p),int)
        for f in range(max(0,ref-4),min(len(maps),ref+5)):
            if f==ref:continue
            q=transform(maps[f],reg).reshape(-1,3);col=imgs[f].reshape(-1,3)/255
            good=np.isfinite(q).all(1)&(confs[f].ravel()>=4);q,col=q[good],col[good]
            d,ix=cKDTree(q).query(p,k=1)
            votes+=(d<.018)&(np.max(abs(col[ix]-c),axis=1)<.15)
        verified=np.zeros((224,224),bool);verified[yy[votes>=3],xx[votes>=3]]=True
        dp,dc=sample_cells(world,rgb,verified,subdivisions=3,max_edge=.025)
        p=np.vstack([world[verified],dp]);c=np.vstack([rgb[verified],dc])
        gap=tree.query(p)[0]>.006;p,c=p[gap],c[gap]
        parts.append(p);colors.append(c);report.append({'ref':ref,'roi':int(mask.sum()),'supported':int(verified.sum()),'gap_points':len(p),'bounds':np.quantile(p,[0,.5,1],axis=0).tolist()})
    p,c=np.vstack(parts),np.vstack(colors);_,ix=np.unique(np.floor(p/.003).astype(int),axis=0,return_index=True);p,c=p[ix],c[ix]
    candidate=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])));candidate.colors=o.utility.Vector3dVector(np.vstack([bc,c]));target=DEST/'shelf_gap_candidate.ply'
    o.io.write_point_cloud(str(target),candidate);check=o.io.read_point_cloud(str(target))
    assert np.array_equal(np.asarray(check.points)[:len(bp)],bp) and np.array_equal(np.asarray(check.colors)[:len(bp)],bc)
    for ref in [0,8,16,28]:
        K,rot,center,e=camera(transform(maps[ref],reg))
        render(bp,bc,K,rot,center).save(DEST/f'shelf_before_{ref}.png')
        render(np.asarray(candidate.points),np.asarray(candidate.colors),K,rot,center).save(DEST/f'shelf_after_{ref}.png')
    visual=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'))
    vp,vc=np.asarray(visual.points),np.asarray(visual.colors)
    for label,center,targetpoint in [('overview',[1.,-4.,-1.7],[1.,0.,1.]),('oblique',[3.,-3.,-1.4],[1.,0.,1.])]:
        center=np.array(center);forward=np.array(targetpoint)-center;forward/=np.linalg.norm(forward)
        right=np.cross(forward,[0,-1,0]);right/=np.linalg.norm(right);down=np.cross(forward,right);rot=np.vstack([right,down,forward]);K=np.array([[240.,0,111.5],[0,240.,111.5],[0,0,1]])
        render(vp,vc,K,rot,center).save(DEST/f'baseline_{label}.png')
        render(np.vstack([vp,p]),np.vstack([vc,c]),K,rot,center).save(DEST/f'shelf_candidate_{label}.png')
    (DEST/'shelf_gap_audit.json').write_text(json.dumps({'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'preserved':len(bp),'added':len(p),'components':report},indent=2));print(json.dumps(report),flush=True)

if __name__=='__main__':
    import sys
    if '--window-seam' in sys.argv:window_seam()
    elif '--window-primary-patch' in sys.argv:window_patch(primary=True)
    elif '--window-patch' in sys.argv:window_patch()
    elif '--shelf-patch' in sys.argv:shelf_patch()
    elif '--new2' in sys.argv:new2_candidates()
    else:main()
