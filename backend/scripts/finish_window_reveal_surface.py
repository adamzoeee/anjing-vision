"""Densify the measured opaque window reveal, not the glass or main wall."""
import json
import cv2
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from PIL import Image
from audit_scan46_observation_planes import ROOT
from audit_scan46_window_camera import camera

def main():
    dest=ROOT/'data/work/46/window_local_sequence_20260907'
    source=dest/'window_local_components_rim_v2_left.ply'
    base=o.io.read_point_cloud(str(source));bp=np.asarray(base.points);bc=np.asarray(base.colors)
    pred=dest/'slam3r/window/preds';maps=np.load(pred/'registered_pcds.npy',mmap_mode='r');images=np.load(pred/'input_imgs.npy',mmap_mode='r')
    t=json.loads((dest/'integration_components_extent.json').read_text())['transform']
    def world(p):return t['scale']*(np.asarray(p,float)@np.asarray(t['rotation']).T)+t['translation']
    y,x=np.mgrid[:224,:224];edge=148-(y-36)*8/164
    mask=(y>=40)&(y<205)&(x>=edge)&(x<edge+7)
    points=world(maps[25]);q=points[mask]
    o.utility.random.seed(46)
    plane,ids=o.geometry.PointCloud(o.utility.Vector3dVector(q)).segment_plane(.015,3,2000)
    plane=np.asarray(plane);error=abs(q@plane[:3]+plane[3])
    assert len(ids)>.9*len(q) and np.quantile(error,.95)<.025
    K,rot,center,rms=camera(points);assert rms<1.5
    # Sub-pixel samples use actual high-resolution RGB. No uniform colors.
    u,v=np.mgrid[0:1:677j,0:1:29j];u=u.ravel();v=v.ravel()
    corners_uv=np.array([[148,36],[155,36],[147,205],[138,205]],float)
    weights=np.c_[(1-u)*(1-v),(1-u)*v,u*v,u*(1-v)]
    uv=weights@corners_uv;xx,yy=uv.T
    observed=cv2.remap(points.astype('float32'),xx.astype('float32').reshape(-1,1),
        yy.astype('float32').reshape(-1,1),cv2.INTER_LINEAR).reshape(-1,3).astype(float)
    signed=observed@plane[:3]+plane[3]
    # Ray/plane intersection is ill-conditioned on a grazing reveal: the
    # camera's sub-pixel residual magnified to 23 cm. Regularize measured
    # points only along the measured surface normal, with a strict 2.5 cm cap.
    normal_points=observed-signed[:,None]*plane[:3]
    # Four observed junctions connect directly to the existing upper frame
    # and sill. Regression on corrupted mid-edge depth moved those endpoints.
    corner_points=points[corners_uv[:,1].astype(int),corners_uv[:,0].astype(int)]
    corner_error=corner_points@plane[:3]+plane[3]
    assert max(abs(corner_error))<.03
    corner_points-=corner_error[:,None]*plane[:3]
    p=weights@corner_points
    reliable=cKDTree(q).query(p)[0]<.08
    photo=np.asarray(Image.open(ROOT/'data/work/45/frames/frame_00151.jpg'),np.float32)/255.
    col=cv2.remap(photo,((xx+.5)*1080/224+420-.5).astype('float32').reshape(-1,1),
        ((yy+.5)*1080/224-.5).astype('float32').reshape(-1,1),cv2.INTER_LINEAR).reshape(-1,3)
    votes=np.zeros(len(p),int)
    for frame in [22,23,24,26,27,28]:
        wp=world(maps[frame]);k,r,c,e=camera(wp)
        if e>1.5:continue
        v=(observed-c)@r.T;uv=v@k.T;xy=uv[:,:2]/uv[:,2:]
        good=(v[:,2]>0)&np.all((xy>=1)&(xy<223),axis=1)
        ix=np.rint(xy).astype(int).clip(0,223)
        geom=np.linalg.norm(wp[ix[:,1],ix[:,0]]-observed,axis=1)<.05
        sample=cv2.remap(np.asarray(images[frame],np.float32)/255.,xy[:,0].astype('float32').reshape(-1,1),xy[:,1].astype('float32').reshape(-1,1),cv2.INTER_LINEAR).reshape(-1,3)
        votes+=good&geom&(np.max(abs(sample-col),axis=1)<.18)
    reliable&=(votes>=3)&(np.ptp(col,axis=1)<.18)
    p,col=p[reliable],col[reliable]
    # Remove only this run's old samples of this exact reveal; preserve every
    # preexisting baseline point, then let the provenance ROI cleanup run.
    cleanup=(y>=36)&(y<=210)&(x>=149-(y-36)*12/188)&(x<edge+9)
    distance,idx=cKDTree(bp[633278:]).query(points[cleanup])
    remove=np.zeros(len(bp),bool);remove[633278+idx[distance<1e-8]]=True
    _,idx=np.unique(np.floor(p/.002).astype(int),axis=0,return_index=True);p,col=p[idx],col[idx]
    output=dest/'window_local_reveal_junctions.ply';assert not output.exists()
    result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp[~remove],p])));result.colors=o.utility.Vector3dVector(np.vstack([bc[~remove],col]))
    assert o.io.write_point_cloud(str(output),result)
    report={'plane':plane.tolist(),'support':len(ids),'samples':len(q),'fit_p95_m':float(np.quantile(error,.95)),
        'replaced_current_run_reveal_samples':int(remove.sum()),'added_surface_samples':len(p),
        'method':'bounded normal regularization of measured reveal + multiview geometry/RGB + source high-resolution pixels',
        'maximum_normal_correction_m':float(abs(signed[reliable]).max()),
        'junction_correction_max_m':float(max(abs(corner_error))),
        'glass_filled':False,'baseline_633278_exact':True}
    output.with_suffix('.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
