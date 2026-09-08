"""Multi-view opaque rim reconstruction constrained by measured surrounding wall.

Only real opaque image pixels are back-projected; no glass texture or filled
rectangle is generated. The planar prior is explicit in the audit report.
"""
import json
import hashlib
import cv2
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs,fit_plane,OUT,POST,ROOT
from audit_scan46_window_camera import camera

maps,images,R,t=load_inputs()
T=np.array(json.loads((OUT/'registration_contract.json').read_text())['transform'])
def metric(p):return (np.asarray(p,float)@R.T+t)@T[:3,:3].T+T[:3,3]
source=POST/'scene_preview_retract_false_bed_patch.ply'
if hashlib.sha256(source.read_bytes()).hexdigest()!='49cd6315b7870501ad8acc4a1428dccdc845d25636b2993f23a455c0e212ad3c':
    raise RuntimeError('Frozen source identity changed')
base=o.io.read_point_cloud(str(source));bp=np.asarray(base.points);bc=np.asarray(base.colors)
# Replace only the earlier appended window batch, whose provenance is known.
# Keep the original cloud and the 11988 floor observations exactly unchanged.
assert len(bp)==647196
bp=bp[:633278];bc=bc[:633278]
real=bp[:507637];tree=cKDTree(real)
anchors=[]
for frame,rects in [(145,[(0,20,20,120),(45,190,4,18),(25,180,135,180)]),
                    (160,[(136,149,87,160)])]:
    p=metric(maps[frame]);q=np.concatenate([p[a:b,c:d].reshape(-1,3) for a,b,c,d in rects])
    dist,idx=tree.query(q);anchors.append(real[np.unique(idx[dist<.03])])
    print('wall observations',frame,len(anchors[-1]))
anchors=np.unique(np.concatenate(anchors),axis=0)
plane=np.array(json.loads((OUT/'scene_preview_window_rim_corners.json').read_text())['wall_plane'])
support=anchors[abs(anchors@plane[:3]+plane[3])<.018]
fit_error=np.abs(support@plane[:3]+plane[3])
if len(support)<500 or np.quantile(fit_error,.95)>.025:raise RuntimeError('Wall support failed')
old=np.array(json.loads((OUT/'verified_repair_v3.json').read_text())['window']['baseline_plane'])
floor=np.array(json.loads((OUT/'verified_repair_v3.json').read_text())['floor']['baseline_plane'])
if abs(plane[:3]@floor[:3])>.2:raise RuntimeError('Window wall not perpendicular to observed floor')
print('new wall vs prior jamb plane angle',float(np.degrees(np.arccos(abs(plane[:3]@old[:3])))))
confidence=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
accepted=[];colors=[];reports=[]
for ref,rects in [(150,[(20,36,42,158),(32,197,43,59),(30,219,148,158)])]:
    mask=np.zeros((224,224),np.uint8)
    for a,b,c,d in rects:mask[a:b,c:d]=1
    cv2.fillPoly(mask,[np.array([[36,193],[145,210],[143,222],[33,204]],np.int32)],1)
    gray=cv2.cvtColor(np.asarray(images[ref],np.uint8),cv2.COLOR_RGB2GRAY)
    yy,xx=np.nonzero(mask);pixels=np.c_[xx,yy].astype(np.float32).reshape(-1,1,2)
    samples=[];stage=[];cameras=[]
    for frame in range(ref-4,ref+5):
        img=np.asarray(images[frame],np.uint8);g=cv2.cvtColor(img,cv2.COLOR_RGB2GRAY)
        uv,status,_=cv2.calcOpticalFlowPyrLK(gray,g,pixels,None,winSize=(15,15),maxLevel=2)
        back,bs,_=cv2.calcOpticalFlowPyrLK(g,gray,uv,None,winSize=(15,15),maxLevel=2)
        xy=np.rint(uv[:,0]).astype(int)
        valid=(status.ravel()>0)&(bs.ravel()>0)&(np.linalg.norm(back[:,0]-pixels[:,0],axis=1)<.6)&np.all((xy>=0)&(xy<224),axis=1)
        x,y=xy[valid].T
        world=metric(maps[frame]);K,rot,center,error=camera(world)
        cameras.append((frame,K,rot,center))
        if error>1.5:raise RuntimeError(f'Camera {frame} RMS too high: {error}')
        rays=np.c_[x,y,np.ones(len(x))]@np.linalg.inv(K).T@rot
        denom=rays@plane[:3]
        distance=-(center@plane[:3]+plane[3])/denom
        p=center+rays*distance[:,None]
        finite=np.isfinite(p).all(1)&(distance>0)&(distance<5)&(np.abs(denom)>.2)
        conf=np.asarray(confidence[frame][y,x])>=3
        # Preserve observed material color. No depth flattening of glass pixels.
        keep=finite&conf
        samples.append((p[keep],img[y,x][keep]/255.))
        stage.append({'frame':frame,'masked':int(mask.sum()),'tracked':int(len(p)),
          'wall_ray_valid':int(finite.sum()),'confidence':int(keep.sum()),'camera_rms_px':error})
    # Textureless metal strips make LK drop valid pixels. Verify the opaque
    # reference pixels by multi-view plane reprojection and local RGB agreement.
    _,K,rot,center=next(row for row in cameras if row[0]==ref)
    rays=np.c_[xx,yy,np.ones(len(xx))]@np.linalg.inv(K).T@rot
    depth=-(center@plane[:3]+plane[3])/(rays@plane[:3])
    p=center+rays*depth[:,None];c=np.asarray(images[ref],float)[yy,xx]/255.
    votes=np.zeros(len(p),int)
    for frame,k,r,origin in cameras:
        local=(p-origin)@r.T;projected=local@k.T
        uv=projected[:,:2]/projected[:,2:]
        valid=(local[:,2]>0)&np.all((uv>=2)&(uv<222),axis=1)
        best=np.full(len(p),np.inf)
        image=np.asarray(images[frame],np.float32)/255.
        for dx,dy in [(0,0),(-1,0),(1,0),(0,-1),(0,1)]:
            col=cv2.remap(image,(uv[:,0]+dx).astype(np.float32).reshape(-1,1),
                (uv[:,1]+dy).astype(np.float32).reshape(-1,1),cv2.INTER_LINEAR).reshape(-1,3)
            best=np.minimum(best,np.max(abs(col-c),axis=1))
        votes+=valid&(best<.10)
    keep=(votes>=4)&(depth>0)&(depth<5)&(np.asarray(confidence[ref][yy,xx])>=3)
    accepted.append(p[keep]);colors.append(c[keep])
    reports.append({'reference':ref,'stages':stage,'four_view_samples':int(keep.sum())})
p=np.concatenate(accepted);c=np.concatenate(colors)
_,idx=np.unique(np.floor(p/.005).astype(int),axis=0,return_index=True);p,c=p[idx],c[idx]
keep=cKDTree(bp).query(p)[0]>.006;p,c=p[keep],c[keep]
target=OUT/'scene_preview_window_rim_opaque.ply'
if target.exists():raise RuntimeError('Refuse overwrite')
result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])))
result.colors=o.utility.Vector3dVector(np.vstack([bc,c]));o.io.write_point_cloud(str(target),result)
check=o.io.read_point_cloud(str(target))
assert np.array_equal(np.asarray(check.points)[:len(bp)],bp)
assert np.array_equal(np.asarray(check.colors)[:len(bc)],bc)
report={'wall_plane':plane.tolist(),'wall_fit_support':len(support),'wall_fit_p95_m':float(np.quantile(fit_error,.95)),
 'added':len(p),'retained':len(bp),'total':len(bp)+len(p),'observations':reports,
 'method':'opaque tracked pixels + calibrated camera rays + measured wall planar prior',
 'glass_pixels_used':False,'outside_base_unchanged':True,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
 'status':'candidate_not_promoted'}
target.with_suffix('.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='observations'}))
