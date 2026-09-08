"""Recover one opaque window assembly from registered observations.

Writes candidates only. All original records except the explicitly appended
planar rim batch remain byte-equivalent numerically. No glass or synthetic fill.
"""
import json,hashlib
import numpy as np
import cv2
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs,ROOT,OUT,POST
from audit_scan46_window_camera import camera
from fuse_scan46_buchong2_four_regions import robust_similarity

DEST=ROOT/'data/work/46/window_components_20260907'

def main():
    target=DEST/'scene_window_assembly_registered.ply'
    if target.exists():raise RuntimeError('Refuse overwrite')
    source=POST/'scene_preview_window_rim_opaque.ply'
    base=o.io.read_point_cloud(str(source));bp=np.asarray(base.points);bc=np.asarray(base.colors)
    assert len(bp)==640738
    # The previous script appended precisely 7460 ray-plane rim records.
    # They are replaced with measured-depth rim records within the same ROI.
    bp=bp[:633278];bc=bc[:633278];tree=cKDTree(bp)
    maps,images,R,t=load_inputs()
    T=np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
    def metric(p):return (np.asarray(p,float)@R.T+t)@T[:3,:3].T+T[:3,3]
    cf=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
    wp=ROOT/'data/work/46/supplement_buchong2_clip2';pred=wp/'slam3r/scene/preds'
    sm=np.load(pred/'registered_pcds.npy',mmap_mode='r');si=np.load(pred/'input_imgs.npy',mmap_mode='r');sc=np.load(pred/'registered_confs.npy',mmap_mode='r')
    src=[];dst=[]
    for e in json.loads((wp/'supplement_mapping.json').read_text())['mapping']:
        if e['kind']!='anchor' or not 873<=int(e['baseline_index'])<=879:continue
        i,j=int(e['output_index']),int(e['baseline_index'])
        if np.mean(abs(np.asarray(si[i],float)-np.asarray(images[j],float)))>2:continue
        ids=np.flatnonzero((np.asarray(sc[i]).ravel()>8)&(np.asarray(cf[j]).ravel()>8))[::25]
        src.append(np.asarray(sm[i]).reshape(-1,3)[ids]);dst.append(np.asarray(maps[j]).reshape(-1,3)[ids])
    s,r,tr,res=robust_similarity(np.concatenate(src),np.concatenate(dst))
    if np.quantile(res,.9)*np.linalg.norm(R[:,0])>.035:raise RuntimeError('Supplement anchors failed')
    def supplement(p):return metric(s*(np.asarray(p,float)@r.T)+tr)
    # Image-space opaque components reviewed against cached RGB views.
    selections=[('main',150,[(0,36,0,224),(36,190,0,59),(36,224,158,224),(207,224,0,158)]),
        ('main',160,[(120,139,60,165),(137,150,55,165),(0,145,0,30)]),
        ('supplement',20,[(0,200,0,83)]),
        ('supplement',25,[(0,195,130,224)]),
        ('supplement',55,[(0,195,125,224)])]
    points=[];colors=[];report=[]
    real=o.geometry.PointCloud(o.utility.Vector3dVector(bp[:507637]))
    real=real.voxel_down_sample(.012)
    real.estimate_normals(o.geometry.KDTreeSearchParamHybrid(radius=.06,max_nn=30))
    transforms={}
    for which,ref,rects in selections:
        pm,im,conf,transform=(maps,images,cf,metric) if which=='main' else (sm,si,sc,supplement)
        mask=np.zeros((224,224),bool)
        for a,b,c,d in rects:mask[a:b,c:d]=True
        yy,xx=np.nonzero(mask);q=transform(pm[ref][yy,xx]);rgb=np.asarray(im[ref])[yy,xx]/255.
        finite=np.isfinite(q).all(1)&(np.asarray(conf[ref])[yy,xx]>=3)
        # Observed opaque window area in the common frame, not an assumed axis wall.
        near=finite&(q[:,2]>-.65)&(q[:,2]<.20)&(q[:,0]>-.05)&(q[:,0]<2.15)&(q[:,1]>-1.0)&(q[:,1]<.85)
        votes=np.zeros(len(q),int)
        for f in [ref-3,ref-2,ref-1,ref+1,ref+2,ref+3]:
            world=transform(pm[f]);K,rot,center,rms=camera(world)
            if rms>1.5:continue
            local=(q-center)@rot.T;uv=local@K.T;xy=np.rint(uv[:,:2]/uv[:,2:]).astype(int)
            valid=(local[:,2]>0)&np.all((xy>=0)&(xy<224),axis=1)
            ids=np.flatnonzero(valid);x,y=xy[ids].T
            ok=(np.linalg.norm(world[y,x]-q[ids],axis=1)<.025)&(np.max(abs(np.asarray(im[f])[y,x]/255.-rgb[ids]),axis=1)<.12)&(np.asarray(conf[f])[y,x]>=3)
            votes[ids[ok]]+=1
        keep=near&(votes>=3);p,c=q[keep],rgb[keep]
        consistent=len(p)
        # Estimate one rigid transform for each contiguous input sequence,
        # using only existing measured geometry, never moving individual points.
        if which not in transforms:
            distance=cKDTree(np.asarray(real.points)).query(p)[0]
            anchor=p[distance<.10]
            cloud=o.geometry.PointCloud(o.utility.Vector3dVector(anchor))
            cloud=cloud.voxel_down_sample(.012)
            reg=o.pipelines.registration.registration_icp(cloud,real,.10,np.eye(4),
                o.pipelines.registration.TransformationEstimationPointToPlane(
                    o.pipelines.registration.TukeyLoss(.04)),
                o.pipelines.registration.ICPConvergenceCriteria(max_iteration=80))
            shift=float(np.linalg.norm(reg.transformation[:3,3]))
            angle=float(np.degrees(np.arccos(np.clip((np.trace(reg.transformation[:3,:3])-1)/2,-1,1))))
            if shift>.15 or angle>10 or reg.inlier_rmse>.05:raise RuntimeError('Unstable local registration')
            transforms[which]=reg.transformation
            print('registration',which,'rmse',reg.inlier_rmse,'fitness',reg.fitness,'shift',shift,'degrees',angle)
        local_T=transforms[which]
        p=p@local_T[:3,:3].T+local_T[:3,3]
        if len(p):
            _,ix=np.unique(np.floor(p/.004).astype(int),axis=0,return_index=True);p,c=p[ix],c[ix]
            gap=tree.query(p)[0]>.004;p,c=p[gap],c[gap]
        points.append(p);colors.append(c);report.append(dict(source=which,frame=ref,raw=len(q),three_views=consistent,added=len(p)))
    p=np.vstack(points);c=np.vstack(colors)
    _,ix=np.unique(np.floor(p/.004).astype(int),axis=0,return_index=True);p,c=p[ix],c[ix]
    result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])));result.colors=o.utility.Vector3dVector(np.vstack([bc,c]))
    assert o.io.write_point_cloud(str(target),result)
    check=o.io.read_point_cloud(str(target));assert np.array_equal(np.asarray(check.points)[:len(bp)],bp);assert np.array_equal(np.asarray(check.colors)[:len(bp)],bc)
    audit=dict(preserved=len(bp),replaced_planar_rim=7460,added=len(p),total=len(bp)+len(p),stages=report,status='candidate',source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    audit['local_rigid_transforms']={k:v.tolist() for k,v in transforms.items()}
    (DEST/'assembly_registered_audit.json').write_text(json.dumps(audit,indent=2));print(json.dumps(audit))

if __name__=='__main__':main()
