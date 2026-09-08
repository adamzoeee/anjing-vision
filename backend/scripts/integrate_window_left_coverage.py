"""Use known crop pixel correspondence, validating on entirely held-out frames."""
import json
import sys
import cv2
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import ROOT
from audit_scan46_window_camera import camera
from fuse_scan46_buchong2_four_regions import robust_similarity

def main():
    dest=ROOT/'data/work/46/window_local_sequence_20260907'
    left=ROOT/'data/work/46/window_left_coverage_20260907'
    def arrays(folder):
        p=folder/'slam3r/window/preds'
        return [np.load(p/name,mmap_mode='r') for name in ['registered_pcds.npy','input_imgs.npy','registered_confs.npy']]
    am,ai,ac=arrays(dest);bm,bi,bc=arrays(left)
    contract=json.loads((dest/'integration_components_extent.json').read_text())['transform']
    def original(p):return contract['scale']*(np.asarray(p)@np.asarray(contract['rotation']).T)+contract['translation']
    yy,xx=np.mgrid[6:218:3,66:218:3];mx=(xx-300/1080*224).astype('float32');my=yy.astype('float32')
    src=[];dst=[];frame_ids=[];color_errors=[]
    # This transform serves only the curtain visit. Bed anchors from the ends
    # of the sequence bias the fit when the network has accumulated drift.
    for frame in range(25,36):
        q=original(cv2.remap(np.asarray(am[frame]),mx,my,cv2.INTER_LINEAR)).reshape(-1,3)
        col=cv2.remap(np.asarray(ai[frame]),mx,my,cv2.INTER_LINEAR).reshape(-1,3)
        confidence=cv2.remap(np.asarray(ac[frame]),mx,my,cv2.INTER_LINEAR).ravel()
        color=np.max(abs(col-bi[frame][yy,xx].reshape(-1,3)),axis=1)
        valid=(color<12)&(confidence>8)&(bc[frame][yy,xx].ravel()>8)&(q[:,2]>-.6)&(q[:,2]<.2)&np.isfinite(q).all(1)
        src.append(bm[frame][yy,xx].reshape(-1,3)[valid]);dst.append(q[valid]);frame_ids.extend([frame]*int(valid.sum()))
        color_errors.extend(color[valid])
    src,dst=np.vstack(src),np.vstack(dst);frame_ids=np.asarray(frame_ids)
    train=frame_ids%2==0
    s,r,t,res=robust_similarity(src[train],dst[train])
    def transform(p):return s*(np.asarray(p)@r.T)+t
    errors=np.linalg.norm(transform(src)-dst,axis=1)
    audit={'samples':len(src),'holdout_m':np.quantile(errors[~train],[.5,.9,.95]).tolist(),
        'per_frame_m':{int(f):np.quantile(errors[frame_ids==f],[.5,.9]).tolist() for f in np.unique(frame_ids)},
        'crop_rgb_error_median':float(np.median(color_errors)),'scale':s,'rotation':r.tolist(),'translation':t.tolist()}
    print(json.dumps(audit),flush=True)
    if np.quantile(errors[~train],.9)>.05:raise RuntimeError('Held-out crop alignment fails')
    selections=[('left_curtain',30,[(0,200,51,91)]),('left_wall',30,[(0,160,0,51)])]
    ps=[];cs=[];audit['components']={}
    for name,ref,rects in selections:
        mask=np.zeros((224,224),bool)
        for a,b,c,d in rects:mask[a:b,c:d]=True
        y,x=np.nonzero(mask);q=transform(bm[ref][y,x]);rgb=bi[ref][y,x]/255.;votes=np.zeros(len(q),int)
        for f in [ref-3,ref-2,ref-1,ref+1,ref+2,ref+3]:
            world=transform(bm[f]);K,rot,center,e=camera(world)
            if e>1.5:continue
            xyz=(q-center)@rot.T;uv=xyz@K.T;xy=np.rint(uv[:,:2]/uv[:,2:]).astype(int)
            good=(xyz[:,2]>0)&np.all((xy>=0)&(xy<224),axis=1)
            ids=np.flatnonzero(good);xx2,yy2=xy[ids].T
            ok=(np.linalg.norm(world[yy2,xx2]-q[ids],axis=1)<.025)&(np.max(abs(bi[f][yy2,xx2]/255.-rgb[ids]),axis=1)<.12)
            votes[ids[ok]]+=1
        keep=(votes>=3)&(bc[ref][y,x]>=3)&(q[:,2]>-.65)&(q[:,2]<.2)&(q[:,1]>-1)&(q[:,1]<1.2)
        ps.append(q[keep]);cs.append(rgb[keep]);audit['components'][name]={'raw':len(q),'retained':int(keep.sum())}
    p,c=np.vstack(ps),np.vstack(cs)
    source=dest/(sys.argv[1] if len(sys.argv)>1 else 'window_local_components_extent.ply')
    base=o.io.read_point_cloud(str(source));bp=np.asarray(base.points);colors=np.asarray(base.colors)
    _,idx=np.unique(np.floor(p/.004).astype(int),axis=0,return_index=True);p,c=p[idx],c[idx]
    # Add only new surface coverage, not a second shifted curtain skin.
    good=cKDTree(bp).query(p)[0]>.015;p,c=p[good],c[good]
    target=dest/(source.stem+'_left.ply');assert not target.exists()
    result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])));result.colors=o.utility.Vector3dVector(np.vstack([colors,c]))
    assert o.io.write_point_cloud(str(target),result)
    audit['added']=len(p);target.with_suffix('.json').write_text(json.dumps(audit,indent=2));print('added',len(p))

if __name__=='__main__':main()
