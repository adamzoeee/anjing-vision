"""Screen-first gap experiment; no formal selection or baseline writes."""
import json
import numpy as np
import open3d as o
import cv2
from scipy.spatial import cKDTree
from PIL import Image,ImageDraw
from verify_september12_candidate import D,VIEWS,depths,project
from audit_scan46_observation_planes import ROOT,POST
from complete_september12_gaps import transform
from local_gap_geometry import hole_mask,screen_hits

OUT=D/'new2/screen_first'
# Image polygons identify named gaps, then depth emptiness defines the actual mask.
REGIONS={
 'shelf_top':('overhead',[(360,235),(510,235),(510,310),(360,310)]),
 'shelf_side_lower':('oblique',[(670,430),(790,430),(780,745),(570,745),(585,650),(675,620)]),
 'bedside':('front',[(118,700),(360,705),(690,730),(745,808),(640,817),(570,755),(300,758),(145,795)]),
 'window_lower':('front',[(228,656),(782,645),(805,752),(335,768),(220,728)]),
 'curtain_corner':('oblique',[(542,270),(592,310),(565,670),(504,660),(517,543)]),
 # The same physical gaps in the other camera, for cross-view scope checking.
 'curtain_corner_front':('front',[(543,208),(618,208),(618,686),(530,686)]),
 'shelf_side_front':('front',[(754,427),(893,427),(893,805),(690,805),(631,745),(630,646),(762,643)]),
 'window_lower_oblique':('oblique',[(128,695),(580,646),(714,720),(630,765),(153,820)])}

def baseline():
    base=o.io.read_point_cloud(str(POST/'local_gaps_20260912.ply'));bp,bc=np.asarray(base.points),np.asarray(base.colors)
    display=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'))
    prior=o.io.read_point_cloud(str(POST/'window_components_reveal_review.ply'))
    return bp,bc,np.vstack([bp,np.asarray(display.points)[len(prior.points):]]),np.vstack([bc,np.asarray(display.colors)[len(prior.points):]])

def audit():
    OUT.mkdir(exist_ok=True);bp,bc,vp,vc=baseline();masks={};report={};cameras={}
    for name,(label,polygon) in REGIONS.items():
        v=VIEWS[label];buf=depths(vp,v);mask=hole_mask(buf,polygon);masks[name]=mask;Image.fromarray(mask.astype('uint8')*255).save(OUT/f'{name}_mask.png')
        image=np.array(Image.open(D/f'new2/baseline_{label}.png'));image[mask]=[255,0,255];Image.fromarray(image).save(OUT/f'{name}_located.png')
        k,r,c=v;ks=k.copy();ks[:2]*=4;cameras[name]={'view':label,'intrinsic':ks.tolist(),'rotation_world_to_camera':r.tolist(),'center':c.tolist(),'vertical_fov_degrees':float(np.degrees(2*np.arctan(448/ks[1,1]))),'polygon':polygon}
        # Boundary depth discontinuities: do not assume the missing surface is floor.
        ring=cv2.dilate(mask.astype('uint8'),np.ones((11,11),np.uint8)).astype(bool)&~mask&np.isfinite(buf)
        xy,z,valid=project(bp,v);ids=np.flatnonzero(valid);ids=ids[ring[xy[ids,1],xy[ids,0]]];p=bp[ids]
        pc=o.geometry.PointCloud(o.utility.Vector3dVector(p));pc=pc.voxel_down_sample(.015);pc.estimate_normals(o.geometry.KDTreeSearchParamHybrid(radius=.07,max_nn=30));normal=np.asarray(pc.normals)
        report[name]={'hole_pixels':int(mask.sum()),'boundary_points':len(pc.points),'horizontal_boundary_fraction':float((abs(normal[:,1])>.9).mean()) if len(normal) else None,'boundary_depth_q10_q50_q90':np.quantile(z[ids],[.1,.5,.9]).tolist() if len(ids) else [],'clips':{}}
    (OUT/'cameras.json').write_text(json.dumps(cameras,indent=2))
    for clip in ['shelf2','bed2','window2']:
        folder=D/'new2'/clip;pred=folder/'slam3r/scene/preds';maps=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r');reg=json.loads((folder/'validated_registration.json').read_text());start=0 if clip=='shelf2' else 21
        for name,mask in masks.items():
            label,_=REGIONS[name];k,r,c=VIEWS[label];ks=k.copy();ks[:2]*=4;parts=[];colors=[];origin=[];counts=[]
            for f in range(start,len(maps),2):
                q=transform(maps[f],reg).reshape(-1,3);valid=np.isfinite(q).all(-1)&(cf[f].ravel()>5);ids=np.flatnonzero(valid);ids=ids[screen_hits(q[ids],ks,r,c,mask)]
                parts.append(q[ids]);colors.append(im[f].reshape(-1,3)[ids]/255.);origin.extend([[f,int(i)] for i in ids]);counts.append([f,len(ids)])
            p,col=np.vstack(parts),np.vstack(colors)
            np.savez_compressed(OUT/f'{clip}_{name}.npz',points=p,colors=col,origin=np.asarray(origin,dtype=int).reshape(-1,2))
            report[name]['clips'][clip]={'projected_hole_points':len(p),'frames':counts,'registration_validated':reg['heldout_m'][1]<=.035}
        montage=Image.new('RGB',(224*5,248*3));draw=ImageDraw.Draw(montage)
        for j,f in enumerate(range(start,len(maps),max(1,(len(maps)-start)//14))):
            if j>=15:break
            montage.paste(Image.fromarray(im[f].astype('uint8')),((j%5)*224,(j//5)*248+24));draw.text(((j%5)*224,(j//5)*248),str(f),fill='white')
        montage.save(OUT/f'{clip}_source_frames.png')
    (OUT/'coverage_audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

def refine_window():
    """Independent short-segment rigid correction, with soft surfaces excluded."""
    bp,bc,_,_=baseline();tree=cKDTree(bp);folder=D/'new2/window2';pred=folder/'slam3r/scene/preds'
    maps=np.load(pred/'registered_pcds.npy',mmap_mode='r');imgs=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r');reg=json.loads((folder/'validated_registration.json').read_text())
    yy,xx=np.mgrid[:224,:224];reports=[]
    ranges={21:[(0,35,75,120),(180,220,75,118)],23:[(0,32,75,127),(178,220,75,125)],25:[(0,32,75,130),(163,220,75,128)],27:[(130,220,75,135)],29:[(0,90,70,132)],31:[(0,85,70,125)],33:[(0,87,72,128)],35:[(0,83,72,130)],37:[(0,108,70,128)],39:[(168,220,70,125)],41:[(173,220,65,127)],43:[(163,220,80,136)],45:[(150,215,103,168)],47:[(160,215,152,192)],49:[(179,220,176,212)]}
    # Reviewed at original image resolution: the right image band is curtain,
    # not radiator. It must not participate in rigid registration.
    ranges.update({21:[(0,18,88,115)],23:[(0,25,90,120)],25:[(0,88,95,125)],27:[(0,88,85,120)],29:[(0,88,82,120)]})
    for group in [[21,23,25,27,29]]:
        train=[];train_reference=[];held=[];reference=[];all_static={}
        for j,f in enumerate(group):
            mask=np.zeros((224,224),bool)
            for x0,x1,y0,y1 in ranges[f]:mask|=(xx>=x0)&(xx<x1)&(yy>=y0)&(yy<y1)
            mask&=cf[f]>5;q=transform(maps[f],reg)[mask];c=imgs[f][mask]/255.;dist,ix=tree.query(q);good=(dist<.07)&(np.max(abs(bc[ix]-c),axis=1)<.13);q=q[good]
            all_static[f]=mask
            if j%2==0:train.extend(q);train_reference.extend(bp[ix[good]])
            else:held.extend(q);reference.extend(bp[ix[good]])
        train=np.asarray(train);held=np.asarray(held);reference=np.asarray(reference)
        source=o.geometry.PointCloud(o.utility.Vector3dVector(train));source=source.voxel_down_sample(.007)
        neighbors=np.unique(tree.query(train,k=8)[1]);target=o.geometry.PointCloud(o.utility.Vector3dVector(bp[neighbors]));target.estimate_normals(o.geometry.KDTreeSearchParamHybrid(radius=.05,max_nn=30))
        estimate=o.pipelines.registration.TransformationEstimationPointToPlane(o.pipelines.registration.TukeyLoss(k=.02))
        fit=o.pipelines.registration.registration_icp(source,target,.05,np.eye(4),estimate,o.pipelines.registration.ICPConvergenceCriteria(max_iteration=40))
        t=fit.transformation;after=held@t[:3,:3].T+t[:3,3];before_error=np.linalg.norm(held-reference,axis=1);after_error=np.linalg.norm(after-reference,axis=1);motion=np.linalg.norm(after-held,axis=1)
        record={'frames':group,'train_points':len(train),'heldout_points':len(held),'before_p50_p90_p95_m':np.quantile(before_error,[.5,.9,.95]).tolist(),'after_p50_p90_p95_m':np.quantile(after_error,[.5,.9,.95]).tolist(),'heldout_motion_p95_m':float(np.quantile(motion,.95)),'correction':t.tolist(),'fitness':fit.fitness}
        record['accepted']=bool(record['after_p50_p90_p95_m'][1]<.035 and record['after_p50_p90_p95_m'][1]<record['before_p50_p90_p95_m'][1] and record['heldout_motion_p95_m']<.05 and fit.fitness>.5)
        # Planar ribs leave ICP tangential motion underconstrained. Check a rigid,
        # fixed-correspondence correction independently; never use heldout to fit.
        src=o.geometry.PointCloud(o.utility.Vector3dVector(train));dst=o.geometry.PointCloud(o.utility.Vector3dVector(np.asarray(train_reference)))
        pairs=o.utility.Vector2iVector(np.c_[np.arange(len(train)),np.arange(len(train))])
        tether=o.pipelines.registration.TransformationEstimationPointToPoint().compute_transformation(src,dst,pairs)
        corrected=held@tether[:3,:3].T+tether[:3,3];err=np.linalg.norm(corrected-reference,axis=1);movement=np.linalg.norm(corrected-held,axis=1)
        record['rigid_tether']={'correction':tether.tolist(),'heldout_p50_p90_p95_m':np.quantile(err,[.5,.9,.95]).tolist(),'motion_p95_m':float(np.quantile(movement,.95))}
        record['rigid_tether']['accepted']=bool(np.quantile(err,.9)<.035 and np.quantile(err,.9)<np.quantile(before_error,.9) and np.quantile(movement,.95)<.05)
        reports.append(record);print(json.dumps(record),flush=True)
    (OUT/'window_segments.json').write_text(json.dumps(reports,indent=2))

def candidate():
    from window_observed_surface import sample_cells
    from review_window_camera import render
    bp,bc,vp,vc=baseline();tree=cKDTree(bp);parts=[];cols=[];stats={}
    masks={name:np.array(Image.open(OUT/f'{name}_mask.png'))>0 for name in REGIONS}
    yy,xx=np.mgrid[:224,:224]
    segment=json.loads((OUT/'window_segments.json').read_text())[0];assert segment['accepted']
    for clip,refs in [('shelf2',[12,16,20]),('window2',[21,25,29]),('primary',[5,10,15,20,25])]:
        folder=D/'new2'/clip;pred=folder/'slam3r/scene/preds'
        if clip=='primary':
            folder=ROOT/'data/work/46/window_local_sequence_20260907';pred=folder/'slam3r/window/preds';reg=json.loads((folder/'integration_components_extent.json').read_text())['transform']
        else:reg=json.loads((folder/'validated_registration.json').read_text())
        m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r')
        t=np.asarray(segment['correction']) if clip=='window2' else np.eye(4)
        world=lambda f:transform(m[f],reg)@t[:3,:3].T+t[:3,3]
        for f in refs:
            q=world(f);rgb=im[f]/255.;valid=(cf[f]>5)&np.isfinite(q).all(-1)
            if clip=='primary':
                vertical,horizontal=np.gradient(q,axis=(0,1));normal=np.cross(horizontal,vertical);length=np.linalg.norm(normal,axis=-1)
                # These masks border vertical curtain/radiator/shelf surfaces, not
                # another layer of bedding. Exclude horizontal surfaces explicitly.
                valid&=(length>1e-10)&(abs(normal[:,:,1])<.6*length)
            # Content exclusions only. Actual inclusion is decided by hole projection.
            if clip=='window2':
                if f==21:valid&=(xx<18)&(yy>88)&(yy<115)
                elif f==25:valid&=(xx<88)&(yy>95)&(yy<125)
                else:valid&=(xx<88)&(yy>82)&(yy<120)
            hit=np.zeros((224,224),bool)
            for name,mask in masks.items():
                if clip=='shelf2' and name!='shelf_top':continue
                k,r,c=VIEWS[REGIONS[name][0]];ks=k.copy();ks[:2]*=4;hit|=screen_hits(q.reshape(-1,3),ks,r,c,mask).reshape(224,224)
            valid&=hit;p=q[valid];color=rgb[valid];votes=np.zeros(len(p),int)
            for j in range(max(21 if clip=='window2' else 0,f-4),min(len(m),f+5)):
                if j==f:continue
                other=world(j);good=cf[j]>5;dist,ix=cKDTree(other[good]).query(p);votes+=(dist<.018)&(np.max(abs((im[j]/255.)[good][ix]-color),axis=1)<.15)
            keep=np.zeros((224,224),bool);a,b=np.nonzero(valid);keep[a[votes>=3],b[votes>=3]]=True
            dense,dc=sample_cells(q,rgb,keep,subdivisions=3,max_edge=.025);p=np.vstack([q[keep],dense]);color=np.vstack([rgb[keep],dc])
            # A gap must stay locally continuous with measured geometry, not a flying island.
            dist,ix=tree.query(p,k=24);near=bp[ix];center=near.mean(1);_,_,vectors=np.linalg.svd(near-center[:,None,:],full_matrices=False);res=abs(np.sum((p-center)*vectors[:,-1],axis=1));good=(dist[:,0]>.008)&(dist[:,0]<.12)&(res<.02)
            p,color=p[good],color[good];parts.append(p);cols.append(color);stats[f'{clip}_{f}']={'hole_pixels_in_source':int(valid.sum()),'multiframe_pixels':int(keep.sum()),'continuous_points':len(p)}
    p,color=np.vstack(parts),np.vstack(cols);_,ix=np.unique(np.floor(p/.003).astype(int),axis=0,return_index=True);p,color=p[ix],color[ix]
    covers=np.zeros(len(p),bool)
    for name,v in VIEWS.items():
        buf=depths(vp,v);xy,z,valid=project(p,v);ids=np.flatnonzero(valid);xx,yy=xy[ids].T
        scope=np.zeros(buf.shape,bool)
        for region,(viewname,_) in REGIONS.items():
            if viewname==name:scope|=masks[region]
        for dy in range(-2,3):
            for dx in range(-2,3):
                old=buf[yy+dy,xx+dx];covers[ids]|=np.isfinite(old)&(z[ids]<=old)
                covers[ids]|=~np.isfinite(old)&~scope[yy+dy,xx+dx]
    stats['before_protection']=len(p);p,color=p[~covers],color[~covers]
    if len(p):
        groups=o.geometry.PointCloud(o.utility.Vector3dVector(p));labels=np.asarray(groups.cluster_dbscan(eps=.018,min_points=8));ids,counts=np.unique(labels,return_counts=True);keep=np.isin(labels,ids[(ids>=0)&(counts>=500)]);p,color=p[keep],color[keep]
    out=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])));out.colors=o.utility.Vector3dVector(np.vstack([bc,color]));o.io.write_point_cloud(str(OUT/'screen_candidate.ply'),out)
    saved=o.io.read_point_cloud(str(OUT/'screen_candidate.ply'))
    assert np.array_equal(np.asarray(saved.points)[:len(bp)],bp)
    assert np.array_equal(np.asarray(saved.colors)[:len(bp)],bc)
    visual=json.loads((POST/'local_gaps_20260912.visual.json').read_text());visual['pointcloud']='screen_candidate.ply';(OUT/'screen_candidate.visual.json').write_text(json.dumps(visual))
    for name,v in VIEWS.items():
        k,r,c=v
        with np.errstate(divide='ignore',invalid='ignore'):
            before=render(vp,vc,k,r,c);after=render(np.vstack([vp,p]),np.vstack([vc,color]),k,r,c)
        after.save(OUT/f'candidate_{name}.png');a,b=np.array(before),np.array(after);holes=(a==[15,19,26]).all(-1);newholes=(b==[15,19,26]).all(-1)
        stats[name]={'filled_black_pixels':int((holes&~newholes).sum()),'changed_existing_pixels':int(((a!=b).any(-1)&~holes).sum()),'new_holes':int((~holes&newholes).sum())}
        assert stats[name]['changed_existing_pixels']==0 and stats[name]['new_holes']==0
        union=np.zeros(holes.shape,bool)
        for region,(viewname,_) in REGIONS.items():
            if viewname==name:union|=masks[region]
        stats[name]['filled_outside_this_views_masks']=int((holes&~newholes&~union).sum())
        comparison=Image.new('RGB',(1792,928));comparison.paste(before,(0,32));comparison.paste(after,(896,32));draw=ImageDraw.Draw(comparison);draw.text((12,8),'BASELINE',fill='white');draw.text((908,8),'LOCAL CANDIDATE',fill='white');comparison.save(OUT/f'comparison_{name}.png')
        with np.errstate(divide='ignore',invalid='ignore'):
            render(np.vstack([vp,p]),np.vstack([vc*.3,np.tile([1.,0.,1.],(len(p),1))]),k,r,c).save(OUT/f'additions_{name}.png')
    stats['additions']=len(p);(OUT/'candidate_audit.json').write_text(json.dumps(stats,indent=2));print(json.dumps(stats),flush=True)

def observed_cushion():
    """Recover the identified R2 soft object from one primary sequence only."""
    from local_gap_geometry import fuse_multiview,missing_voxels
    from window_observed_surface import sample_cells
    from review_window_camera import render
    out=D/'new2/ownership';bp,bc,vp,vc=baseline()
    folder=ROOT/'data/work/46/window_local_sequence_20260907';pred=folder/'slam3r/window/preds';reg=json.loads((folder/'integration_components_extent.json').read_text())['transform']
    m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r');y,x=np.mgrid[:224,:224];parts=[];colors=[];frames=[];stages=[]
    neighbors={};neighbor_colors={}
    for f in range(13):
        good=(cf[f]>5)&np.isfinite(m[f]).all(-1);neighbors[f]=cKDTree(transform(m[f],reg)[good]);neighbor_colors[f]=(im[f]/255.)[good]
    for f in range(13):
        q=transform(m[f],reg);rgb=im[f]/255.
        left=np.interp(f,[0,2,4,6,8,10,12],[125,133,141,149,155,165,190]);top=np.interp(f,[0,2,4,6,8,10,12],[112,120,130,150,175,195,215])
        valid=(x>=left)&(x<214)&(y>=top)&(y<220)&(cf[f]>5)&np.isfinite(q).all(-1)
        pixels=np.flatnonzero(valid);points=q.reshape(-1,3)[pixels];col=rgb.reshape(-1,3)[pixels];support=np.zeros(len(points),int)
        for j in range(max(0,f-3),min(13,f+4)):
            if j==f:continue
            distance,index=neighbors[j].query(points);support+=(distance<.02)&(np.max(abs(neighbor_colors[j][index]-col),axis=1)<.12)
        valid.flat[pixels[support<3]]=False;stages.append({'frame':f,'source_candidates':len(pixels),'multiview_supported_pixels':int(valid.sum())})
        dense,dc=sample_cells(q,rgb,valid,subdivisions=3,max_edge=.03);p=np.vstack([q[valid],dense]);c=np.vstack([rgb[valid],dc]);parts.append(p);colors.append(c);frames.extend([f]*len(p))
    # Multi-view agreement is checked geometrically above. Do not require three
    # observations to land in the exact same 6 mm cell (quantization artefact).
    p,c=fuse_multiview(np.vstack(parts),np.vstack(colors),np.array(frames),.006,1)
    # Named object's visible extent, with overlap; not limited to empty pixels.
    poly=[(610,676),(781,674),(833,737),(810,795),(670,786),(616,733)];mask=np.zeros((896,896),np.uint8);cv2.fillPoly(mask,[np.array(poly,np.int32)],1)
    k,r,center=VIEWS['front'];ks=k.copy();ks[:2]*=4;scope=screen_hits(p,ks,r,center,mask.astype(bool));p,c=p[scope],c[scope]
    # Reject foreground contamination from actual blue bedding. The grey/pale
    # cushion and dark bag are content, not rigid registration anchors.
    content=(c[:,2]-c[:,0]<.09);p,c=p[content],c[content]
    keep=missing_voxels(p,bp,.012);p,c=p[keep],c[keep]
    path=out/'cushion_candidate.ply';pc=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])));pc.colors=o.utility.Vector3dVector(np.vstack([bc,c]));o.io.write_point_cloud(str(path),pc)
    saved=o.io.read_point_cloud(str(path));assert np.array_equal(np.asarray(saved.points)[:len(bp)],bp);assert np.array_equal(np.asarray(saved.colors)[:len(bp)],bc)
    visual=json.loads((POST/'local_gaps_20260912.visual.json').read_text());visual['pointcloud']=path.name;path.with_suffix('.visual.json').write_text(json.dumps(visual))
    for label,(k,r,center) in VIEWS.items():
        before=render(vp,vc,k,r,center);after=render(np.vstack([vp,p]),np.vstack([vc,c]),k,r,center);sheet=Image.new('RGB',(1792,928));sheet.paste(before,(0,32));sheet.paste(after,(896,32));draw=ImageDraw.Draw(sheet);draw.text((12,8),'BASELINE',fill='white');draw.text((908,8),'OBSERVED R2 SURFACE',fill='white');sheet.save(out/f'cushion_comparison_{label}.png')
    (out/'cushion_audit.json').write_text(json.dumps({'source_frames':list(range(13)),'stages':stages,'added_points':len(p),'original_points_unchanged':len(bp),'status':'candidate awaiting visual validation'},indent=2));print('cushion candidate',len(p),flush=True)

def ownership():
    """Trace actual point-map observations behind named visible openings."""
    from review_window_camera import render
    out=D/'new2/ownership';out.mkdir(exist_ok=True)
    bp,bc,vp,vc=baseline();tree=cKDTree(bp);v=VIEWS['front'];buf=depths(vp,v)
    polygons={
      'R1_side_upper':[(778,445),(889,445),(883,622),(773,670)],
      'R2_below_curtain':[(610,676),(781,674),(833,737),(810,795),(670,786),(616,733)],
      'R3_below_radiator':[(273,727),(610,728),(668,777),(330,787),(273,761)],
      'R4_frame_curtain_slit':[(543,265),(606,265),(605,621),(535,633)]}
    masks={name:hole_mask(buf,poly) for name,poly in polygons.items()};report={name:{'hole_pixels':int(mask.sum()),'clips':{}} for name,mask in masks.items()}
    canvas=render(vp,vc,*v);draw=ImageDraw.Draw(canvas)
    palette=['#ff7f50','#ffcf33','#32c8df','#bb77ff']
    for (name,poly),color in zip(polygons.items(),palette):draw.line(poly+[poly[0]],fill=color,width=3);draw.text(poly[0],name.split('_')[0],fill=color)
    canvas.save(out/'regions_front.png')
    oblique_polys={
      'R1_side_upper':[(678,445),(765,467),(731,585),(679,633)],
      'R2_below_curtain':[(571,634),(679,622),(719,702),(665,766),(569,741)],
      'R3_below_radiator':[(278,718),(564,700),(605,748),(337,791)],
      'R4_frame_curtain_slit':[(522,272),(594,275),(556,639),(506,648)]}
    canvas=render(vp,vc,*VIEWS['oblique']);draw=ImageDraw.Draw(canvas)
    for (name,poly),color in zip(oblique_polys.items(),palette):draw.line(poly+[poly[0]],fill=color,width=3);draw.text(poly[0],name.split('_')[0],fill=color)
    canvas.save(out/'regions_oblique.png')
    for clip in ['primary','window2','bed2']:
        folder=D/'new2'/clip;pred=folder/'slam3r/scene/preds'
        if clip=='primary':
            folder=ROOT/'data/work/46/window_local_sequence_20260907';pred=folder/'slam3r/window/preds';reg=json.loads((folder/'integration_components_extent.json').read_text())['transform'];start=0
        else:reg=json.loads((folder/'validated_registration.json').read_text());start=21
        m=np.load(pred/'registered_pcds.npy',mmap_mode='r');im=np.load(pred/'input_imgs.npy',mmap_mode='r');cf=np.load(pred/'registered_confs.npy',mmap_mode='r')
        worlds={f:transform(m[f],reg).reshape(-1,3) for f in range(start,len(m))}
        trees={};rgb={}
        for f,q in worlds.items():
            good=(cf[f].ravel()>5)&np.isfinite(q).all(1);trees[f]=cKDTree(q[good]);rgb[f]=im[f].reshape(-1,3)[good]/255.
        for region,mask in masks.items():
            rows=[];illustrations=[];saved=[]
            for f in range(start,len(m),2):
                q=worlds[f];xy,z,valid=project(q,v);ids=np.flatnonzero(valid);ids=ids[mask[xy[ids,1],xy[ids,0]]];raw=len(ids);ids=ids[cf[f].ravel()[ids]>5];count=len(ids)
                if not count:rows.append({'frame':f,'raw':raw,'confidence':0,'tested':0,'stable':0});continue
                test=ids[::max(1,int(np.ceil(count/500)))];p=q[test];col=im[f].reshape(-1,3)[test]/255.;votes=np.zeros(len(test),int)
                for j in range(max(start,f-3),min(len(m),f+4)):
                    if j==f:continue
                    distance,index=trees[j].query(p);votes+=(distance<.02)&(np.max(abs(rgb[j][index]-col),axis=1)<.12)
                stable=votes>=3;distance=tree.query(p)[0]
                rows.append({'frame':f,'raw':raw,'confidence':count,'tested':len(test),'stable':int(stable.sum()),'stable_absent_2cm':int((stable&(distance>.02)).sum()),'baseline_distance_p50_p90_m':np.quantile(distance,[.5,.9]).tolist()})
                sample=im[f].astype('uint8').copy();sample.reshape(-1,3)[ids]=[255,0,255]
                illustrations.append((count,f,sample));saved.append(np.c_[np.full(stable.sum(),f),test[stable],p[stable],col[stable]])
            report[region]['clips'][clip]={'frames':rows,'note':'stability is within this source sequence; does not certify baseline registration or surface identity'}
            sheet=Image.new('RGB',(896,248*2));draw=ImageDraw.Draw(sheet)
            for i,(_,f,img) in enumerate(sorted(illustrations,reverse=True,key=lambda x:x[0])[:8]):
                sheet.paste(Image.fromarray(img),((i%4)*224,(i//4)*248+24));draw.text(((i%4)*224,(i//4)*248),f'{clip} frame {f}',fill='white')
            sheet.save(out/f'{region}_{clip}_sources.png')
            if saved:np.savez_compressed(out/f'{region}_{clip}_supported.npz',observations=np.vstack(saved))
        print('ownership traced',clip,flush=True)
    (out/'observations.json').write_text(json.dumps(report,indent=2));print('ownership audit saved',flush=True)

if __name__=='__main__':
    import sys
    if '--observed-cushion' in sys.argv:observed_cushion()
    elif '--ownership' in sys.argv:ownership()
    elif '--refine-window' in sys.argv:refine_window()
    elif '--candidate' in sys.argv:candidate()
    else:audit()
