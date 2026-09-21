"""Validate anchored local reconstruction; export only conservative gap candidates."""
import json
import numpy as np,open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import ROOT,POST
from fuse_anchored_supplements import robust_similarity
from audit_scan46_window_camera import camera
from review_window_camera import render
from window_observed_surface import sample_cells
from complete_september12_gaps import transform

D=ROOT/'data/work/46/local_gaps_20260912';W=D/'window_bridge';P=ROOT/'data/work/46/window_local_sequence_20260907'
def main():
    m=np.load(W/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r');im=np.load(W/'slam3r/window/preds/input_imgs.npy',mmap_mode='r');cf=np.load(W/'slam3r/window/preds/registered_confs.npy',mmap_mode='r')
    pm=np.load(P/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r');pt=json.loads((P/'integration_components_extent.json').read_text())['transform']
    base=o.io.read_point_cloud(str(POST/'window_components_reveal_review.ply'));bp=np.asarray(base.points);bc=np.asarray(base.colors);tree=cKDTree(bp)
    a=[];b=[];frames=[]
    for i in range(0,21,2):
        q=transform(pm[i+16],pt).reshape(-1,3);d,ix=tree.query(q)
        good=(d<.018)&(cf[i].ravel()>8);ids=np.flatnonzero(good)[::15]
        a.extend(m[i].reshape(-1,3)[ids]);b.extend(bp[ix[ids]]);frames.extend([i]*len(ids))
    a,b=np.array(a),np.array(b);train=np.array(frames)%4==0
    s,r,t,res=robust_similarity(a[train],b[train]);error=np.linalg.norm(s*(a@r.T)+t-b,axis=1)
    reg={'scale':s,'rotation':r.tolist(),'translation':t.tolist(),'heldout_m':np.quantile(error[~train],[.5,.9,.95]).tolist(),'anchors':len(a)}
    print('anchor',reg,flush=True);(W/'registration.json').write_text(json.dumps(reg,indent=2))
    if reg['heldout_m'][1]>.035:raise RuntimeError('Anchor registration failed')
    # Only opaque pixels: curtain edges, lower reveal, radiator border. No glass or flowerpots.
    y,x=np.mgrid[:224,:224]
    selections=[('right_curtain_lower',0,(y<110)&(x<190)),('right_curtain_edge',30,(x>165)),('sill_lower',40,(y>127)&(y<162)),('left_reveal',60,(x<53)),('right_reveal',90,(x>146)&(x<205))]
    parts=[];cols=[];rows=[]
    for name,ref,mask in selections:
        f=ref+21;world=transform(m[f],reg);rgb=np.asarray(im[f])/255
        raw=mask&np.isfinite(world).all(-1)&(cf[f]>5)
        near=tree.query(world.reshape(-1,3))[0].reshape(224,224)
        roi=raw&(near<.045)&(world[:,:,2]<.25)&(world[:,:,2]>-.65)
        yy,xx=np.nonzero(roi);p=world[roi];c=rgb[roi];votes=np.zeros(len(p),int)
        for k in range(max(21,f-4),min(len(m),f+5)):
            if k==f:continue
            q=transform(m[k],reg).reshape(-1,3);col=im[k].reshape(-1,3)/255;good=np.isfinite(q).all(1)&(cf[k].ravel()>5);q,col=q[good],col[good]
            if not len(q):continue
            d,ix=cKDTree(q).query(p);votes+=(d<.015)&(np.max(abs(col[ix]-c),axis=1)<.12)
        valid=np.zeros((224,224),bool);valid[yy[votes>=3],xx[votes>=3]]=True
        dense,dc=sample_cells(world,rgb,valid,subdivisions=3,max_edge=.025)
        p=np.vstack([world[valid],dense]);c=np.vstack([rgb[valid],dc]);gap=tree.query(p)[0]>.009;p,c=p[gap],c[gap]
        parts.append(p);cols.append(c);rows.append({'name':name,'raw':int(raw.sum()),'near_baseline':int(roi.sum()),'support':int(valid.sum()),'added':len(p)})
        K,rot,center,e=camera(world);render(bp,bc,K,rot,center).save(W/f'{name}_baseline.png');render(np.vstack([bp,p]),np.vstack([bc,c]),K,rot,center).save(W/f'{name}_candidate.png')
    p,c=np.vstack(parts),np.vstack(cols);_,ids=np.unique(np.floor(p/.003).astype(int),axis=0,return_index=True);p,c=p[ids],c[ids]
    cloud=o.geometry.PointCloud(o.utility.Vector3dVector(p));cloud.colors=o.utility.Vector3dVector(c);o.io.write_point_cloud(str(W/'additions.ply'),cloud)
    (W/'gap_audit.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows),flush=True)

if __name__=='__main__':main()
