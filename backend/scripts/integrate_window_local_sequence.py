"""Align the isolated window reconstruction using held-out measured anchors."""
import json,hashlib
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs,ROOT,OUT,POST
from audit_scan46_window_camera import camera
from fuse_scan46_buchong2_four_regions import robust_similarity
DEST=ROOT/'data/work/46/window_local_sequence_20260907'

def component_mask(name,rects):
    mask=np.zeros((224,224),bool)
    for a,b,c,d in rects:mask[a:b,c:d]=True
    if name=='right_frame_and_curtain':
        y,x=np.mgrid[:224,:224]
        mask=(y>=36)&(x>=149-(y-36)*12/188)
    return mask

def main():
    source=POST/'scene_preview_window_rim_opaque.ply';base=o.io.read_point_cloud(str(source))
    bp=np.asarray(base.points);bc=np.asarray(base.colors);assert len(bp)==640738
    maps,images,R,t=load_inputs();T=np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
    def metric(p):return (np.asarray(p,float)@R.T+t)@T[:3,:3].T+T[:3,3]
    pred=DEST/'slam3r/window/preds'
    sm=np.load(pred/'registered_pcds.npy',mmap_mode='r');si=np.load(pred/'input_imgs.npy',mmap_mode='r');sc=np.load(pred/'registered_confs.npy',mmap_mode='r')
    tree=cKDTree(bp[:507637]);src=[];dst=[]
    for i in [0,2,4,6,8,44,46,48,50]:
        j=i+125
        assert np.mean(abs(np.asarray(si[i],float)-np.asarray(images[j],float)))<2
        p=metric(maps[j]).reshape(-1,3);d,idx=tree.query(p)
        good=(d<.025)&(np.asarray(sc[i]).ravel()>8)&(np.max(abs(np.asarray(si[i]).reshape(-1,3)/255.-bc[idx]),axis=1)<.12)
        ids=np.flatnonzero(good)[::8]
        src.append(np.asarray(sm[i]).reshape(-1,3)[ids]);dst.append(bp[idx[ids]])
    src,dst=np.concatenate(src),np.concatenate(dst)
    train=np.arange(len(src))%3!=0
    s,r,tr,res=robust_similarity(src[train],dst[train])
    errors=np.linalg.norm(s*(src@r.T)+tr-dst,axis=1)
    audit=dict(anchors=len(src),heldout_m=np.quantile(errors[~train],[.5,.9,.95]).tolist(),scale=s)
    print(json.dumps(audit),flush=True)
    if len(src)<100 or np.quantile(errors[~train],.9)>.05:raise RuntimeError('Held-out anchor validation failed')
    def transform(p):return s*(np.asarray(p,float)@r.T)+tr
    selections=[
        ('upper_wall_and_frame',25,[(0,36,0,158)]),
        ('left_wall_and_frame',25,[(36,190,0,59)]),
        ('right_frame_and_curtain',25,[(36,224,148,224)]),
        ('sill',25,[(191,224,0,158)]),
        ('lower_wall_and_sill',35,[(120,139,60,165),(137,150,55,165)]),
        ('left_curtain',30,[(0,200,0,27)]),
        ('right_curtain_full',15,[(0,224,85,200),(0,105,200,224)]),
        ('lower_wall',30,[(189,209,34,153)]),
    ]
    ps=[];cs=[]
    audit['components']={}
    audit['transform']={'scale':s,'rotation':r.tolist(),'translation':tr.tolist()}
    for name,ref,rects in selections:
        mask=component_mask(name,rects)
        y,x=np.nonzero(mask);q=transform(sm[ref][y,x]);rgb=si[ref][y,x]/255.
        votes=np.zeros(len(q),int)
        for f in [ref-3,ref-2,ref-1,ref+1,ref+2,ref+3]:
            world=transform(sm[f]);K,rot,center,e=camera(world)
            if e>1.5:continue
            local=(q-center)@rot.T;uv=local@K.T;xy=np.rint(uv[:,:2]/uv[:,2:]).astype(int)
            good=(local[:,2]>0)&np.all((xy>=0)&(xy<224),axis=1)
            ids=np.flatnonzero(good);xx,yy=xy[ids].T
            ok=(np.linalg.norm(world[yy,xx]-q[ids],axis=1)<.025)&(np.max(abs(si[f][yy,xx]/255.-rgb[ids]),axis=1)<.12)
            votes[ids[ok]]+=1
        finite=np.isfinite(q).all(1)
        confident=finite&(np.asarray(sc[ref])[y,x]>=3)
        region=confident&(q[:,2]>-.65)&(q[:,2]<.2)&(q[:,1]>-1)&(q[:,1]<1.2)
        keep=region&(votes>=3)
        audit['components'][name]={'frame':ref+125,'raw':len(q),'finite':int(finite.sum()),'confidence':int(confident.sum()),'region':int(region.sum()),'multiview':int(keep.sum())}
        ps.append(q[keep]);cs.append(rgb[keep])
    p=np.vstack(ps);c=np.vstack(cs)
    _,ix=np.unique(np.floor(p/.004).astype(int),axis=0,return_index=True);p,c=p[ix],c[ix]
    pbase=bp[:633278];cbase=bc[:633278]
    keep=cKDTree(pbase).query(p)[0]>.004;p,c=p[keep],c[keep]
    result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([pbase,p])));result.colors=o.utility.Vector3dVector(np.vstack([cbase,c]))
    target=DEST/'window_local_components_rim_v2.ply'
    assert not target.exists()
    assert o.io.write_point_cloud(str(target),result)
    written=o.io.read_point_cloud(str(target))
    assert np.array_equal(np.asarray(written.points)[:633278],pbase)
    assert np.array_equal(np.asarray(written.colors)[:633278],cbase)
    audit.update(added=len(p),preserved=len(pbase),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),status='candidate')
    target.with_suffix('.json').write_text(json.dumps(audit,indent=2));print(json.dumps(audit))
if __name__=='__main__':main()
