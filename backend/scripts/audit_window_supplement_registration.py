"""Validate supplement anchors without altering any reconstruction or selection."""
import json
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs,ROOT,OUT,POST
from fuse_scan46_buchong2_four_regions import robust_similarity

maps,images,R,t=load_inputs()
T=np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
def metric(p):return (np.asarray(p,float)@R.T+t)@T[:3,:3].T+T[:3,3]
pred=ROOT/'data/work/46/supplement_buchong2_clip2/slam3r/scene/preds'
sm=np.load(pred/'registered_pcds.npy',mmap_mode='r')
si=np.load(pred/'input_imgs.npy',mmap_mode='r')
sc=np.load(pred/'registered_confs.npy',mmap_mode='r')
conf=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
mapping=json.loads((pred.parents[2]/'supplement_mapping.json').read_text())['mapping']
src=[];dst=[];frames=[]
for e in mapping:
    if e['kind']!='anchor':continue
    i,j=int(e['output_index']),int(e['baseline_index'])
    image_error=float(np.mean(abs(np.asarray(si[i],float)-np.asarray(images[j],float))))
    if image_error>2:continue
    ids=np.flatnonzero((np.asarray(sc[i]).ravel()>8)&(np.asarray(conf[j]).ravel()>8))[::40]
    src.append(np.asarray(sm[i]).reshape(-1,3)[ids]);dst.append(np.asarray(maps[j]).reshape(-1,3)[ids]);frames.append((i,j,len(ids)))
src,dst=np.concatenate(src),np.concatenate(dst)
s,rot,trans,res=robust_similarity(src,dst)
base=o.io.read_point_cloud(str(POST/'scene_preview_window_rim_opaque.ply'))
tree=cKDTree(np.asarray(base.points)[:507637])
converted=metric(s*(src@rot.T)+trans)
d=tree.query(converted)[0]
report=dict(anchor_frames=frames,correspondences=len(src),
    similarity_residual_m=np.quantile(res*np.linalg.norm(R[:,0]),[.5,.9,.95]).tolist(),
    distance_to_formal_real_points_m=np.quantile(d,[.5,.9,.95]).tolist(),
    within_3cm_fraction=float(np.mean(d<.03)),
    conclusion='Anchor agreement alone does not verify registration to the formal window wall')
print(json.dumps(report))
offset=0;group_ids={'698_705':[],'873_879':[]}
for i,j,n in frames:
    group_ids['698_705' if j<800 else '873_879'].extend(range(offset,offset+n));offset+=n
report['separate_anchor_passes']={}
for name,ids in group_ids.items():
    ids=np.asarray(ids)
    ss,rr,tt,ee=robust_similarity(src[ids],dst[ids])
    dd=tree.query(metric(ss*(src[ids]@rr.T)+tt))[0]
    row=dict(similarity_residual_m=np.quantile(ee*np.linalg.norm(R[:,0]),[.5,.9]).tolist(),
        formal_distance_m=np.quantile(dd,[.5,.9]).tolist(),within_3cm=float(np.mean(dd<.03)))
    report['separate_anchor_passes'][name]=row
    print(name,json.dumps(row))
(ROOT/'data/work/46/window_components_20260907/supplement_registration.json').write_text(json.dumps(report,indent=2))
