"""Check cross-pass visual correspondences instead of geometry-only wall guesses."""
import json
import cv2
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs,ROOT,OUT,POST
from fuse_scan46_buchong2_four_regions import robust_similarity
maps,images,R,t=load_inputs()
T=np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
def metric(p):return (np.asarray(p,float)@R.T+t)@T[:3,:3].T+T[:3,3]
base=o.io.read_point_cloud(str(POST/'scene_preview_window_rim_opaque.ply'))
tree=cKDTree(np.asarray(base.points)[:507637])
sift=cv2.SIFT_create(2000,contrastThreshold=.015)
report=[]
for a,b in [(150,675),(150,687),(150,880),(160,882),(150,160)]:
    ka,da=sift.detectAndCompute(np.asarray(images[a],np.uint8),None)
    kb,db=sift.detectAndCompute(np.asarray(images[b],np.uint8),None)
    pairs=cv2.BFMatcher().knnMatch(da,db,k=2)
    matches=[m for m,n in pairs if m.distance<.7*n.distance]
    aa=np.rint([ka[m.queryIdx].pt for m in matches]).astype(int)
    bb=np.rint([kb[m.trainIdx].pt for m in matches]).astype(int)
    row=dict(source=a,target=b,matches=len(matches))
    if len(matches)>=8:
        p=metric(maps[a][aa[:,1],aa[:,0]]);q=metric(maps[b][bb[:,1],bb[:,0]])
        # Retain only target landmarks actually represented in the frozen cloud.
        near=tree.query(q)[0]<.035
        row['formal_landmarks']=int(near.sum())
        if near.sum()>=8:
            s,r,tr,e=robust_similarity(p[near],q[near])
            row.update(scale=s,rotation=r.tolist(),translation=tr.tolist(),residual=np.quantile(e,[.5,.9]).tolist())
    report.append(row)
print(json.dumps(report))
(ROOT/'data/work/46/window_components_20260907/visual_registration.json').write_text(json.dumps(report,indent=2))
