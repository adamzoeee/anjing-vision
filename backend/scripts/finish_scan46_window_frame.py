"""Candidate-only tracked opaque window jamb observations, no generated surfaces."""
import json
import cv2
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs, ROOT, OUT, POST

maps,images,R,t=load_inputs()
T=np.array(json.loads((OUT/'registration_contract.json').read_text())['transform'])
plane=np.array(json.loads((OUT/'verified_repair_v3.json').read_text())['window']['baseline_plane'])
confidence=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
mask=np.zeros((224,224),np.uint8)
for a,b,c,d in [(32,46,23,120),(47,190,22,32),(45,200,116,129)]:mask[a:b,c:d]=1
ref=np.asarray(images[145],np.uint8)
gray=cv2.cvtColor(ref,cv2.COLOR_RGB2GRAY)
yy,xx=np.nonzero(mask);pixels=np.c_[xx,yy].astype(np.float32).reshape(-1,1,2)
samples=[]
for frame in range(141,150):
    img=np.asarray(images[frame],np.uint8)
    g=cv2.cvtColor(img,cv2.COLOR_RGB2GRAY)
    tracked,status,_=cv2.calcOpticalFlowPyrLK(gray,g,pixels,None,winSize=(15,15),maxLevel=2)
    back,bs,_=cv2.calcOpticalFlowPyrLK(g,gray,tracked,None,winSize=(15,15),maxLevel=2)
    uv=np.rint(tracked[:,0]).astype(int)
    keep=(status.ravel()>0)&(bs.ravel()>0)&(np.linalg.norm(back[:,0]-pixels[:,0],axis=1)<.6)&np.all((uv>=0)&(uv<224),axis=1)
    uv=uv[keep];x,y=uv.T
    points=(np.asarray(maps[frame][y,x],float)@R.T+t)@T[:3,:3].T+T[:3,3]
    conf=np.asarray(confidence[frame][y,x])
    # Allow real jamb depth, but not arbitrary glass/exterior depth.
    valid=np.isfinite(points).all(1)&(conf>=3)&(abs(points@plane[:3]+plane[3])<.15)
    samples.append((points[valid],img[y,x][valid]/255.))
trees=[cKDTree(p) for p,c in samples]
accepted=[];colors=[]
for p,c in samples:
    votes=sum((tree.query(p)[0]<.012).astype(int) for tree in trees)
    accepted.append(p[votes>=4]);colors.append(c[votes>=4])
p=np.concatenate(accepted);c=np.concatenate(colors)
_,idx=np.unique(np.floor(p/.005).astype(int),axis=0,return_index=True);p,c=p[idx],c[idx]
source=POST/'scene_preview_verified_observations_20260905.ply'
base=o.io.read_point_cloud(str(source));bp=np.asarray(base.points);bc=np.asarray(base.colors)
keep=cKDTree(bp).query(p)[0]>.006;p,c=p[keep],c[keep]
target=OUT/'scene_preview_window_jambs_v4.ply'
if target.exists():raise RuntimeError('Refuse overwrite')
result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])))
result.colors=o.utility.Vector3dVector(np.vstack([bc,c]));o.io.write_point_cloud(str(target),result)
check=o.io.read_point_cloud(str(target))
assert np.array_equal(np.asarray(check.points)[:len(bp)],bp)
assert np.array_equal(np.asarray(check.colors)[:len(bc)],bc)
print(json.dumps({'added':len(p),'original_unchanged':len(bp),'frames':list(range(141,150)),
 'four_frame_support_radius_m':.012,'colors':'unaltered observed pixels','output':str(target)}))
