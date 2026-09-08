"""Register one clear supplemental window visit through exact main-video anchors."""
import json
import numpy as np
import open3d as o
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import ROOT
from audit_scan46_window_camera import camera
from fuse_scan46_buchong2_four_regions import robust_similarity
from integrate_window_local_sequence import component_mask

def main():
    dest=ROOT/'data/work/46/window_local_sequence_20260907'
    folder=ROOT/'data/work/46/window_supplement_bridge_20260908'
    a=np.load(dest/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r')
    pred=folder/'slam3r/window/preds'
    b=np.load(pred/'registered_pcds.npy',mmap_mode='r');images=np.load(pred/'input_imgs.npy',mmap_mode='r');conf=np.load(pred/'registered_confs.npy',mmap_mode='r')
    contract=json.loads((dest/'integration_components_extent.json').read_text())['transform']
    q=contract['scale']*(np.array(a[16:37,::6,::6])@np.array(contract['rotation']).T)+contract['translation']
    p=np.array(b[:21,::6,::6]);valid=(q[:,:,:,2]>-.65)&(q[:,:,:,2]<.2)
    f=np.broadcast_to(np.arange(21)[:,None,None],valid.shape)[valid];q,p=q[valid],p[valid]
    train=f%2==0;s,r,t,res=robust_similarity(p[train],q[train])
    def world(p):return s*(np.asarray(p,float)@r.T)+t
    err=np.linalg.norm(world(p)-q,axis=1)
    audit={'heldout_m':np.quantile(err[~train],[.5,.9,.95]).tolist(),'scale':s,'rotation':r.tolist(),'translation':t.tolist()}
    print(json.dumps(audit),flush=True);assert np.quantile(err[~train],.9)<.04
    ps=[];cs=[]
    for ref,left_edge in [(31,142),(34,115)]:
        y,x=np.mgrid[:224,:224];mask=x>=left_edge;y,x=np.nonzero(mask)
        p=world(b[ref][y,x]);c=images[ref][y,x]/255.;votes=np.zeros(len(p),int)
        for frame in range(max(21,ref-3),min(37,ref+4)):
            if frame==ref:continue
            wp=world(b[frame]);K,rot,center,e=camera(wp)
            if e>1.5:continue
            v=(p-center)@rot.T;uv=v@K.T;xy=np.rint(uv[:,:2]/uv[:,2:]).astype(int)
            good=(v[:,2]>0)&np.all((xy>=0)&(xy<224),axis=1)
            ids=np.flatnonzero(good);ix,iy=xy[ids].T
            ok=(np.linalg.norm(wp[iy,ix]-p[ids],axis=1)<.025)&(np.max(abs(images[frame][iy,ix]/255.-c[ids]),axis=1)<.12)
            votes[ids[ok]]+=1
        good=(votes>=3)&(conf[ref][y,x]>=3)&(p[:,2]>-.65)&(p[:,2]<.2)&(p[:,1]>-1)&(p[:,1]<1.2)
        ps.append(p[good]);cs.append(c[good])
    p,c=np.vstack(ps),np.vstack(cs)
    raw=o.geometry.PointCloud(o.utility.Vector3dVector(p));raw.colors=o.utility.Vector3dVector(c)
    o.io.write_point_cloud(str(dest/'supplement_curtain_only.ply'),raw)
    base=o.io.read_point_cloud(str(dest/'window_local_components_rim_v2_left.ply'));bp=np.asarray(base.points);bc=np.asarray(base.colors)
    # Exact same physical curtain region, replacing new samples only. Original
    # baseline observations remain protected even during this comparison.
    def primary_world(p):return contract['scale']*(np.asarray(p,float)@np.asarray(contract['rotation']).T)+contract['translation']
    m1=component_mask('right_frame_and_curtain',[(36,224,148,224)])
    m2=component_mask('right_curtain_full',[(0,224,85,200),(0,105,200,224)])
    source_samples=np.vstack([primary_world(a[25][m1]),primary_world(a[15][m2])])
    # Scene anchors do not eliminate a residual local curtain offset. Register
    # this component against its measured body, excluding the uncertain fold.
    body=np.vstack([primary_world(a[25][:,160:224]).reshape(-1,3),
        primary_world(a[15][:,95:200]).reshape(-1,3)])
    target_cloud=o.geometry.PointCloud(o.utility.Vector3dVector(body)).voxel_down_sample(.01)
    target_cloud.estimate_normals(o.geometry.KDTreeSearchParamHybrid(radius=.05,max_nn=25))
    source_cloud=o.geometry.PointCloud(o.utility.Vector3dVector(p)).voxel_down_sample(.01)
    reg=o.pipelines.registration.registration_icp(source_cloud,target_cloud,.15,np.eye(4),
        o.pipelines.registration.TransformationEstimationPointToPlane(),
        o.pipelines.registration.ICPConvergenceCriteria(max_iteration=40))
    tr=reg.transformation
    angle=float(np.degrees(np.arccos(np.clip((np.trace(tr[:3,:3])-1)/2,-1,1))))
    shift=float(np.linalg.norm(tr[:3,3]))
    audit['curtain_registration']={'rmse':reg.inlier_rmse,'fitness':reg.fitness,'shift':shift,'angle_deg':angle,'transform':tr.tolist()}
    print(json.dumps(audit['curtain_registration']),flush=True)
    assert reg.inlier_rmse<.04 and shift<.15 and angle<5
    p=p@tr[:3,:3].T+tr[:3,3]
    distance,idx=cKDTree(bp[633278:]).query(source_samples)
    owned=np.zeros(len(bp),bool);owned[633278+idx[distance<1e-8]]=True
    d=cKDTree(p).query(bp)[0];remove=owned&(d<.10)
    distance=cKDTree(bp[~remove]).query(p)[0]
    p,c=p[distance>.004],c[distance>.004]
    _,idx=np.unique(np.floor(p/.004).astype(int),axis=0,return_index=True);p,c=p[idx],c[idx]
    target=dest/'window_local_supplement_curtain_registered.ply';assert not target.exists()
    result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp[~remove],p])));result.colors=o.utility.Vector3dVector(np.vstack([bc[~remove],c]))
    assert o.io.write_point_cloud(str(target),result)
    audit.update(removed_new_samples=int(remove.sum()),supplement_points=len(p))
    target.with_suffix('.json').write_text(json.dumps(audit,indent=2));print(json.dumps(audit))

if __name__=='__main__':main()
