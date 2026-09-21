"""Constrain new window points to visible gaps, then render fixed-view A/B checks."""
import json,hashlib
import numpy as np,open3d as o
from audit_scan46_observation_planes import ROOT,POST
from review_window_camera import render
from PIL import Image,ImageDraw
D=ROOT/'data/work/46/local_gaps_20260912'
def view(center,target,focal):
    center=np.array(center,float);forward=np.array(target)-center;forward/=np.linalg.norm(forward)
    right=np.cross(forward,[0,-1,0]);right/=np.linalg.norm(right);down=np.cross(forward,right)
    return np.array([[focal,0,111.5],[0,focal,111.5],[0,0,1]]),np.vstack([right,down,forward]),center
VIEWS={'front':view([.95,-.08,1.3],[.95,-.08,-.22],120),'oblique':view([1.65,-.12,1.4],[.95,-.08,-.22],130),'overview':view([1,-4,-1.7],[1,0,1],240),'overhead':view([1,-4,-.5],[1,0,1],240)}
def project(p,v):
    K,r,c=v;local=(p-c)@r.T;uv=local@K.T
    pixels=np.divide(uv[:,:2],uv[:,2:],out=np.full((len(p),2),-1000.),where=abs(uv[:,2:])>1e-9)
    xy=np.rint(pixels*4).astype(int);valid=(local[:,2]>.03)&np.all((xy>=2)&(xy<894),axis=1)
    return xy,local[:,2],valid
def depths(p,v):
    xy,z,valid=project(p,v);xy,z=xy[valid],z[valid];result=np.full((896,896),np.inf)
    for dy in range(-2,3):
        for dx in range(-2,3):np.minimum.at(result,(xy[:,1]+dy,xy[:,0]+dx),z)
    return result

def new2_review():
    from scipy.spatial import cKDTree
    root=D/'new2';base=o.io.read_point_cloud(str(POST/'local_gaps_20260912.ply'));bp,bc=np.asarray(base.points),np.asarray(base.colors)
    display=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'));original=o.io.read_point_cloud(str(POST/'window_components_reveal_review.ply'))
    vp=np.vstack([bp,np.asarray(display.points)[len(original.points):]]);vc=np.vstack([bc,np.asarray(display.colors)[len(original.points):]])
    for name in ['shelf2','bed2']:
        cloud=o.io.read_point_cloud(str(root/name/'observed_additions.ply'));p,c=np.asarray(cloud.points),np.asarray(cloud.colors)
        distance,ix=cKDTree(bp).query(p)
        if name=='bed2':
            neighbors=bp[np.unique(ix)];pc=o.geometry.PointCloud(o.utility.Vector3dVector(neighbors));plane,ids=pc.segment_plane(.015,3,2000);res=p@np.array(plane[:3])+plane[3]
            (root/name/'floor_plane_check.json').write_text(json.dumps({'baseline_plane':np.asarray(plane).tolist(),'baseline_support':len(ids),'candidate_signed_distance_m':np.quantile(res,[.05,.5,.95]).tolist()},indent=2))
        for label,(K,r,center) in VIEWS.items():
            with np.errstate(divide='ignore',invalid='ignore'):
                before=render(vp,vc,K,r,center);after=render(np.vstack([vp,p]),np.vstack([vc,c]),K,r,center)
                render(np.vstack([vp,p]),np.vstack([vc*.3,np.tile([1.,0.,1.],(len(p),1))]),K,r,center).save(root/f'{name}_added_{label}.png')
            before.save(root/f'baseline_{label}.png');after.save(root/f'{name}_raw_{label}.png')
    print('new2 component review rendered',flush=True)
def new2_finalize():
    root=D/'new2';original=POST/'local_gaps_20260912.ply'
    base=o.io.read_point_cloud(str(original));bp,bc=np.asarray(base.points),np.asarray(base.colors)
    display=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'))
    prior=o.io.read_point_cloud(str(POST/'window_components_reveal_review.ply'))
    vp=np.vstack([bp,np.asarray(display.points)[len(prior.points):]]);vc=np.vstack([bc,np.asarray(display.colors)[len(prior.points):]])
    additions=[];colors=[];audit={}
    buffers={name:depths(vp,v) for name,v in VIEWS.items()}
    for name in ['shelf2','bed2']:
        cloud=o.io.read_point_cloud(str(root/name/'observed_additions.ply'));p,c=np.asarray(cloud.points),np.asarray(cloud.colors)
        gaps=np.zeros(len(p),int);covers=np.zeros(len(p),bool)
        for label,v in VIEWS.items():
            xy,z,valid=project(p,v);ids=np.flatnonzero(valid);xx,yy=xy[ids].T;buf=buffers[label]
            gaps[ids]+=~np.isfinite(buf[yy,xx])
            for dy in range(-2,3):
                for dx in range(-2,3):
                    old=buf[yy+dy,xx+dx];covers[ids]|=np.isfinite(old)&(z[ids]<=old)
        keep=(gaps>0)&~covers
        audit[name]={'observed_candidates':len(p),'visible_gap_candidates':int((gaps>0).sum()),'protected_gap_additions':int(keep.sum())}
        additions.append(p[keep]);colors.append(c[keep])
    p,c=np.vstack(additions),np.vstack(colors)
    target=root/'local_gaps_new2_candidate.ply'
    result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,p])));result.colors=o.utility.Vector3dVector(np.vstack([bc,c]));o.io.write_point_cloud(str(target),result)
    check=o.io.read_point_cloud(str(target));assert np.array_equal(np.asarray(check.points)[:len(bp)],bp);assert np.array_equal(np.asarray(check.colors)[:len(bp)],bc)
    visual=json.loads(original.with_suffix('.visual.json').read_text());visual['pointcloud']=target.name;target.with_suffix('.visual.json').write_text(json.dumps(visual))
    for label,(K,r,center) in VIEWS.items():
        with np.errstate(divide='ignore',invalid='ignore'):
            before=render(vp,vc,K,r,center);after=render(np.vstack([vp,p]),np.vstack([vc,c]),K,r,center)
        after.save(root/f'final_{label}.png');combo=Image.new('RGB',(1792,930));combo.paste(before,(0,34));combo.paste(after,(896,34));draw=ImageDraw.Draw(combo);draw.text((12,10),'BASELINE',fill='white');draw.text((908,10),'PROTECTED LOCAL ADDITIONS',fill='white');combo.save(root/f'comparison_{label}.png')
        a,b=np.array(before),np.array(after);hole=(a==[15,19,26]).all(-1);newhole=(b==[15,19,26]).all(-1)
        audit[label]={'filled_black_pixels':int((hole&~newhole).sum()),'changed_existing_pixels':int(((a!=b).any(-1)&~hole).sum()),'new_black_pixels':int((~hole&newhole).sum())}
        assert audit[label]['changed_existing_pixels']==0 and audit[label]['new_black_pixels']==0
    audit.update({'baseline_points_preserved':len(bp),'added_points':len(p),'window2':'rejected: held-out p90 0.04118m exceeds 0.035m; baseline retained','status':'candidate only; formal selection unchanged'})
    (root/'final_audit.json').write_text(json.dumps(audit,indent=2));print(json.dumps(audit),flush=True)

def main():
    original=POST/'window_components_reveal_review.ply';base=o.io.read_point_cloud(str(original));bp=np.asarray(base.points);bc=np.asarray(base.colors)
    shelf=o.io.read_point_cloud(str(D/'shelf_gap_candidate.ply'));sp=np.asarray(shelf.points)[len(bp):];sc=np.asarray(shelf.colors)[len(bp):]
    display=o.io.read_point_cloud(str(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply'));vp=np.asarray(display.points);vc=np.asarray(display.colors)
    window=o.io.read_point_cloud(str(D/'window_bridge/additions.ply'));wp=np.asarray(window.points);wc=np.asarray(window.colors)
    additions=np.vstack([sp,wp]);gaps=np.zeros(len(additions),int);occludes=np.zeros(len(additions),bool)
    for label,v in VIEWS.items():
        zbuf=depths(vp,v);xy,z,valid=project(additions,v);ids=np.flatnonzero(valid);xx,yy=xy[ids].T;old=zbuf[yy,xx]
        gaps[ids]+=~np.isfinite(old)
        for dy in range(-2,3):
            for dx in range(-2,3):
                old=zbuf[yy+dy,xx+dx]
                occludes[ids]|=np.isfinite(old)&(z[ids]<=old)
    keep=(gaps>=1)&~occludes
    n=len(sp);sp,sc=sp[keep[:n]],sc[keep[:n]];wp,wc=wp[keep[n:]],wc[keep[n:]]
    clusters=o.geometry.PointCloud(o.utility.Vector3dVector(wp));labels=np.array(clusters.cluster_dbscan(eps=.015,min_points=5))
    ids,counts=np.unique(labels,return_counts=True);keep=np.isin(labels,ids[(ids>=0)&(counts>=100)]);wp,wc=wp[keep],wc[keep]
    result=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,sp,wp])));result.colors=o.utility.Vector3dVector(np.vstack([bc,sc,wc]));target=D/'local_gaps_20260912.ply';o.io.write_point_cloud(str(target),result)
    check=o.io.read_point_cloud(str(target));assert np.array_equal(np.asarray(check.points)[:len(bp)],bp);assert np.array_equal(np.asarray(check.colors)[:len(bp)],bc)
    visual=json.loads(original.with_suffix('.visual.json').read_text());visual['pointcloud']=target.name;target.with_suffix('.visual.json').write_text(json.dumps(visual))
    cp,cc=np.vstack([vp,sp,wp]),np.vstack([vc,sc,wc]);stats={}
    for label,v in VIEWS.items():
        K,r,center=v
        with np.errstate(divide='ignore',invalid='ignore'):
            before=render(vp,vc,K,r,center);after=render(cp,cc,K,r,center)
        before.save(D/f'final_before_{label}.png');after.save(D/f'final_after_{label}.png')
        combo=Image.new('RGB',(1792,930));combo.paste(before,(0,34));combo.paste(after,(896,34));draw=ImageDraw.Draw(combo);draw.text((12,10),'BASELINE',fill='white');draw.text((908,10),'LOCAL GAP CANDIDATE',fill='white');combo.save(D/f'comparison_{label}.png')
        a=np.array(before);b=np.array(after);bg=np.array([15,19,26]);hole=(a==bg).all(-1);newhole=(b==bg).all(-1)
        stats[label]={'filled_black_pixels':int((hole&~newhole).sum()),'new_black_pixels':int((~hole&newhole).sum()),'changed_existing_pixels':int(((a!=b).any(-1)&~hole).sum())}
        assert stats[label]['new_black_pixels']==0 and stats[label]['changed_existing_pixels']==0
    report={'baseline_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'baseline_points_preserved':len(bp),'shelf_added':len(sp),'window_added':len(wp),'views':stats,'status':'candidate'}
    (D/'final_audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
if __name__=='__main__':
    import sys
    if '--new2-review' in sys.argv:new2_review()
    elif '--new2-finalize' in sys.argv:new2_finalize()
    else:main()
