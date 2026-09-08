"""Append image-selected, independently depth-confirmed opaque components.

No ray-plane projection, texture plane, inferred colours or source overwrite.
The result stays a candidate until visual review; analysis assets are untouched.
"""
import hashlib
import json
import cv2
import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs, OUT, POST, ROOT
from audit_window_components import COMPONENTS
from audit_scan46_window_camera import camera

def main():
    output = ROOT/'data/work/46/window_components_20260907'
    target = output/'scene_window_components_multiview.ply'
    if target.exists(): raise RuntimeError('Candidate already exists; refuse overwrite')
    source = POST/'scene_preview_window_rim_opaque.ply'
    identity = hashlib.sha256(source.read_bytes()).hexdigest()
    base = o3d.io.read_point_cloud(str(source))
    bp,bc = np.asarray(base.points),np.asarray(base.colors)
    maps,images,R,t = load_inputs()
    T = np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
    plane = np.asarray(json.loads((OUT/'scene_preview_window_rim_opaque.json').read_text())['wall_plane'])
    conf = np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
    def metric(p): return (np.asarray(p,float)@R.T+t)@T[:3,:3].T+T[:3,3]
    additions=[]; colors=[]; report={}; tree=cKDTree(bp)
    for name,(ref,rects) in COMPONENTS.items():
        mask=np.zeros((224,224),np.uint8)
        for a,b,c,d in rects: mask[a:b,c:d]=1
        yy,xx=np.nonzero(mask)
        uv=np.c_[xx,yy].astype(np.float32).reshape(-1,1,2)
        q=metric(maps[ref][yy,xx]); rgb=np.asarray(images[ref])[yy,xx]/255.
        finite=np.isfinite(q).all(1)
        confident=finite&(np.asarray(conf[ref])[yy,xx]>=3)
        # A neighbourhood bound rejects unrelated walls, without flattening
        # the sill or curtains onto the frame. Cross-view depth is decisive.
        near=confident&(np.abs(q@plane[:3]+plane[3])<.50)
        gray=cv2.cvtColor(np.asarray(images[ref],np.uint8),cv2.COLOR_RGB2GRAY)
        votes=np.zeros(len(q),int); stages=[]
        depth_votes=np.zeros(len(q),int)
        for frame in [ref-6,ref-3,ref-2,ref-1,ref+1,ref+2,ref+3,ref+6]:
            image=np.asarray(images[frame],np.uint8)
            g=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
            f,status,_=cv2.calcOpticalFlowPyrLK(gray,g,uv,None,winSize=(15,15),maxLevel=2)
            b,bs,_=cv2.calcOpticalFlowPyrLK(g,gray,f,None,winSize=(15,15),maxLevel=2)
            xy=np.rint(f[:,0]).astype(int)
            valid=(status.ravel()>0)&(bs.ravel()>0)&np.all((xy>=0)&(xy<224),axis=1)&(np.linalg.norm(b[:,0]-uv[:,0],axis=1)<.7)
            ids=np.flatnonzero(valid); x,y=xy[ids].T
            observed=metric(maps[frame][y,x])
            error=np.linalg.norm(observed-q[ids],axis=1)
            good=(error<.025)&(np.asarray(conf[frame])[y,x]>=3)
            votes[ids[good]]+=1
            # Textureless wall/cloth pixels cannot be tracked reliably with LK.
            # Project their existing 3D position into other cameras and require
            # independently predicted 3D and RGB agreement there instead.
            world=metric(maps[frame])
            K,rot,center,rms=camera(world)
            if rms>1.5: raise RuntimeError('Camera calibration failed')
            local=(q-center)@rot.T; projected=local@K.T
            xy2=np.rint(projected[:,:2]/projected[:,2:]).astype(int)
            valid2=(local[:,2]>0)&np.all((xy2>=0)&(xy2<224),axis=1)
            ids2=np.flatnonzero(valid2); x2,y2=xy2[ids2].T
            e2=np.linalg.norm(world[y2,x2]-q[ids2],axis=1)
            rgb_error=np.max(abs(image[y2,x2]/255.-rgb[ids2]),axis=1)
            ok=(e2<.025)&(rgb_error<.12)&(np.asarray(conf[frame])[y2,x2]>=3)
            depth_votes[ids2[ok]]+=1
            stages.append(dict(frame=frame,tracked=len(ids),depth_agreement=int(good.sum()),
                reprojection_depth_rgb=int(ok.sum()),camera_rms=rms))
        keep=near&((votes>=3)|(depth_votes>=3))
        p,c=q[keep],rgb[keep]
        consistent=len(p)
        if len(p):
            _,ix=np.unique(np.floor(p/.005).astype(np.int64),axis=0,return_index=True)
            p,c=p[ix],c[ix]
        voxel=len(p)
        if len(p):
            keep=tree.query(p)[0]>.006
            p,c=p[keep],c[keep]
        report[name]=dict(raw=len(q),finite=int(finite.sum()),confidence=int(confident.sum()),
            neighbourhood=int(near.sum()),three_other_views=consistent,voxel=voxel,
            added=len(p),views=stages)
        additions.append(p);colors.append(c)
    p=np.vstack(additions);c=np.vstack(colors)
    result=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.vstack([bp,p])))
    result.colors=o3d.utility.Vector3dVector(np.vstack([bc,c]))
    output.mkdir(exist_ok=True)
    if not o3d.io.write_point_cloud(str(target),result): raise RuntimeError('Write failed')
    check=o3d.io.read_point_cloud(str(target))
    assert np.array_equal(np.asarray(check.points)[:len(bp)],bp)
    assert np.array_equal(np.asarray(check.colors)[:len(bp)],bc)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==identity
    audit=dict(source_sha256=identity,preserved_points=len(bp),added=len(p),total=len(bp)+len(p),
        components=report,source_unchanged=True,status='candidate_requires_visual_review',
        method='Original registered depth and RGB; optical-flow correspondence; three independent depth confirmations')
    (output/'audit_multiview.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit))

if __name__=='__main__': main()
