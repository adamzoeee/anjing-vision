"""Test whether a coherent rigid/similarity alignment can preserve the existing rim."""
import json
import numpy as np
from audit_scan46_observation_planes import load_inputs,OUT,ROOT
from audit_scan46_window_camera import camera
from fuse_scan46_buchong2_four_regions import robust_similarity
maps,images,R,t=load_inputs()
T=np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
world=(np.asarray(maps[150],float)@R.T+t)@T[:3,:3].T+T[:3,3]
plane=np.asarray(json.loads((OUT/'scene_preview_window_rim_opaque.json').read_text())['wall_plane'])
K,rot,center,rms=camera(world)
mask=np.zeros((224,224),bool)
for a,b,c,d in [(20,36,42,158),(32,197,43,59),(30,219,148,158)]:mask[a:b,c:d]=True
y,x=np.nonzero(mask)
rays=np.c_[x,y,np.ones(len(x))]@np.linalg.inv(K).T@rot
depth=-(center@plane[:3]+plane[3])/(rays@plane[:3])
target=center+rays*depth[:,None]
s,r,tr,res=robust_similarity(world[y,x],target)
report=dict(scale=s,rotation=r.tolist(),translation=tr.tolist(),residual_quantiles=np.quantile(res,[.5,.9,.95]).tolist())
print(json.dumps(report))
(ROOT/'data/work/46/window_components_20260907/frame_correspondence.json').write_text(json.dumps(report,indent=2))
