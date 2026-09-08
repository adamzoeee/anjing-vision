"""Keep measured jamb depth instead of projecting a recessed window onto a wall."""
import json
import cv2
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs,ROOT,OUT

maps,images,R,t=load_inputs()
T=np.array(json.loads((OUT/'registration_contract.json').read_text())['transform'])
plane=np.array(json.loads((OUT/'verified_repair_v3.json').read_text())['window']['baseline_plane'])
confidence=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
accepted=[];colors=[];reports=[]
for ref,rects in [(145,[(32,46,23,120),(47,190,22,32),(45,200,116,129)]),
                 (687,[(20,33,63,213),(34,219,53,67),(34,219,213,224)])]:
    mask=np.zeros((224,224),np.uint8)
    for a,b,c,d in rects:mask[a:b,c:d]=1
    gray=cv2.cvtColor(np.asarray(images[ref],np.uint8),cv2.COLOR_RGB2GRAY)
    yy,xx=np.nonzero(mask);pixels=np.c_[xx,yy].astype(np.float32).reshape(-1,1,2)
    samples=[]
    for frame in range(ref-4,ref+5):
        img=np.asarray(images[frame],np.uint8);g=cv2.cvtColor(img,cv2.COLOR_RGB2GRAY)
        uv,status,_=cv2.calcOpticalFlowPyrLK(gray,g,pixels,None,winSize=(15,15),maxLevel=2)
        back,bs,_=cv2.calcOpticalFlowPyrLK(g,gray,uv,None,winSize=(15,15),maxLevel=2)
        xy=np.rint(uv[:,0]).astype(int)
        valid=(status.ravel()>0)&(bs.ravel()>0)&(np.linalg.norm(back[:,0]-pixels[:,0],axis=1)<.6)&np.all((xy>=0)&(xy<224),axis=1)
        x,y=xy[valid].T
        p=(np.asarray(maps[frame][y,x],float)@R.T+t)@T[:3,:3].T+T[:3,3]
        depth=p@plane[:3]+plane[3]
        valid=np.isfinite(p).all(1)&(np.asarray(confidence[frame][y,x])>=3)&(depth>-.30)&(depth<.15)
        samples.append((p[valid],img[y,x][valid]/255.))
    trees=[cKDTree(p) for p,c in samples]
    count=0
    for p,c in samples:
        votes=sum((tree.query(p)[0]<.012).astype(int) for tree in trees)
        accepted.append(p[votes>=4]);colors.append(c[votes>=4]);count+=int((votes>=4).sum())
    reports.append({'reference':ref,'tracked_frames':9,'supported_samples':count})
p=np.concatenate(accepted);c=np.concatenate(colors)
_,idx=np.unique(np.floor(p/.005).astype(int),axis=0,return_index=True);p,c=p[idx],c[idx]
source=OUT/'scene_preview_retract_false_bed_patch.ply'
base=o.io.read_point_cloud(str(source));bp=np.asarray(base.points);bc=np.asarray(base.colors)
keep=cKDTree(bp).query(p)[0]>.006;p,c=p[keep],c[keep]
target=OUT/'scene_preview_window_recess_candidate.ply'
if target.exists():raise RuntimeError('Refuse overwrite')
result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])))
result.colors=o.utility.Vector3dVector(np.vstack([bc,c]));o.io.write_point_cloud(str(target),result)
check=o.io.read_point_cloud(str(target))
assert np.array_equal(np.asarray(check.points)[:len(bp)],bp)
assert np.array_equal(np.asarray(check.colors)[:len(bc)],bc)
report={'added':len(p),'retained':len(bp),'total':len(bp)+len(p),'observations':reports,
 'synthetic_points':0,'depth_modified':False,'status':'candidate_not_promoted'}
target.with_suffix('.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
