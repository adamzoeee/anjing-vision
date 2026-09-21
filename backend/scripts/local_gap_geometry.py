"""Small geometry helpers for observed, baseline-constrained gap recovery."""
import numpy as np
import cv2

def replace_local(points, colors, remove, allowed, additions, addition_colors):
    remove=np.asarray(remove,bool);allowed=np.asarray(allowed,bool)
    if np.any(remove&~allowed):raise ValueError('removal outside authorized seam')
    return np.vstack([points[~remove],additions]),np.vstack([colors[~remove],addition_colors])

def largest_surface_hole(occupied, roi, sampling_kernel):
    """Diagnostic only: bridge point-spacing gaps, never change rendered geometry."""
    surface=cv2.morphologyEx(np.asarray(occupied,np.uint8),cv2.MORPH_CLOSE,
                             np.ones((sampling_kernel,sampling_kernel),np.uint8))
    holes=(np.asarray(roi,bool)&~surface.astype(bool)).astype(np.uint8)
    _,_,stats,_=cv2.connectedComponentsWithStats(holes,8)
    return int(stats[1:,cv2.CC_STAT_AREA].max()) if len(stats)>1 else 0

def fuse_multiview(points, colors, frame_ids, voxel, min_views):
    cells=np.floor(np.asarray(points)/voxel).astype(np.int64)
    _,inverse=np.unique(cells,axis=0,return_inverse=True)
    views=np.unique(np.c_[inverse,frame_ids],axis=0)
    support=np.bincount(views[:,0],minlength=inverse.max()+1)
    counts=np.bincount(inverse);p=np.zeros((len(counts),3));c=np.zeros_like(p)
    np.add.at(p,inverse,points);np.add.at(c,inverse,colors)
    selected=support>=min_views
    return (p/counts[:,None])[selected],(c/counts[:,None])[selected]

def missing_voxels(points, baseline, voxel):
    occupied={tuple(row) for row in np.floor(np.asarray(baseline)/voxel).astype(np.int64)}
    return np.array([tuple(row) not in occupied for row in np.floor(np.asarray(points)/voxel).astype(np.int64)])

def hole_mask(depth, polygon):
    selected=np.zeros(depth.shape,np.uint8)
    cv2.fillPoly(selected,[np.asarray(polygon,np.int32)],1)
    return selected.astype(bool)&np.isinf(depth)

def screen_hits(points, intrinsic, rotation, center, mask):
    camera=(np.asarray(points)-center)@rotation.T;uv=camera@intrinsic.T
    xy=np.divide(uv[:,:2],uv[:,2:],out=np.full((len(points),2),-1.),where=abs(uv[:,2:])>1e-9)
    xy=np.rint(xy).astype(int);x,y=xy.T
    valid=(camera[:,2]>0)&(x>=0)&(y>=0)&(x<mask.shape[1])&(y<mask.shape[0])
    ids=np.flatnonzero(valid);valid[ids]=mask[y[ids],x[ids]]
    return valid

def intersect_plane(uv, intrinsic, rotation, center, plane):
    rays=np.c_[uv,np.ones(len(uv))]@np.linalg.inv(intrinsic).T@rotation
    denominator=rays@plane[:3]
    distance=np.divide(-(np.asarray(center)@plane[:3]+plane[3]),denominator,out=np.full(len(uv),np.nan),where=abs(denominator)>1e-9)
    valid=np.isfinite(distance)&(distance>0)
    result=np.asarray(center)+rays*distance[:,None]
    result[~valid]=np.nan
    return result,valid

def estimate_pose(points, pixels, intrinsic):
    points=np.asarray(points,np.float64);pixels=np.asarray(pixels,np.float64)
    if len(points)<6:return None
    cv2.setRNGSeed(46)
    ok,rvec,tvec,ids=cv2.solvePnPRansac(points,pixels,intrinsic,None,iterationsCount=2000,reprojectionError=2.,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
    if not ok or ids is None or len(ids)<6:return None
    ids=ids.ravel()
    rvec,tvec=cv2.solvePnPRefineLM(points[ids],pixels[ids],intrinsic,None,rvec,tvec)
    rotation=cv2.Rodrigues(rvec)[0]
    projected=cv2.projectPoints(points,rvec,tvec,intrinsic,None)[0].reshape(-1,2)
    return {'rotation':rotation,'center':-rotation.T@tvec.ravel(),'inliers':ids,'residual_px':np.linalg.norm(projected-pixels,axis=1)}


def refine_ray_depth(pixels, initial_depth, tracks, visible, cameras, reference, max_change=.2):
    """Optimize depth on fixed reference rays; reject unobservable/invalid updates."""
    k,r,center=cameras[reference]
    pixels=np.asarray(pixels,float);initial_depth=np.asarray(initial_depth,float)
    ray=np.c_[pixels,np.ones(len(pixels))]@np.linalg.inv(k).T@r
    visible=np.asarray(visible,bool)&np.isfinite(tracks).all(-1)
    aa=[];bb=[]
    for j,(kj,rj,cj) in enumerate(cameras):
        uv=np.c_[tracks[j],np.ones(len(pixels))]@np.linalg.inv(kj).T
        direction=ray@rj.T;offset=(center-cj)@rj.T
        a=direction[:,:2]-uv[:,:2]*direction[:,2:]
        b=offset[:2]-uv[:,:2]*offset[2]
        aa.append(np.where(visible[j,:,None],a,0));bb.append(np.where(visible[j,:,None],b,0))
    a=np.array(aa);b=np.array(bb)
    denominator=np.sum(a*a,axis=(0,2))
    depth=np.divide(-np.sum(a*b,axis=(0,2)),denominator,
                    out=initial_depth.copy(),where=denominator>1e-10)
    points=center+ray*depth[:,None]
    errors=[];angles=[];positive=np.ones(len(pixels),bool)
    reference_unit=ray/np.linalg.norm(ray,axis=1)[:,None]
    for j,(kj,rj,cj) in enumerate(cameras):
        local=(points-cj)@rj.T;h=local@kj.T
        uv=np.divide(h[:,:2],h[:,2:],out=np.full((len(points),2),np.nan),where=abs(h[:,2:])>1e-10)
        errors.append(np.where(visible[j],np.linalg.norm(uv-tracks[j],axis=1),np.nan))
        direction=points-cj;unit=direction/np.maximum(np.linalg.norm(direction,axis=1)[:,None],1e-12)
        angle=np.degrees(np.arccos(np.clip(np.sum(unit*reference_unit,axis=1),-1,1)))
        angles.append(np.where(visible[j],angle,0))
        positive&=(~visible[j])|(local[:,2]>0)
    errors=np.array(errors)
    error=np.nanmedian(errors,axis=0);parallax=np.max(angles,axis=0)
    # The reference reprojection is zero by construction, not independent
    # evidence. Every supporting neighbor must agree with the solved surface.
    neighbors=visible.copy();neighbors[reference]=False
    neighbor_max=np.max(np.where(neighbors,errors,0.),axis=0)
    accepted=(visible.sum(0)>=3)&(denominator>1e-10)&positive&(depth>0)&(parallax>.5)&(error<1.)&(neighbor_max<1.)&(
        abs(depth-initial_depth)<=max_change)
    return dict(points=points,depth=depth,accepted=accepted,reprojection_median_px=error,
                neighbor_reprojection_max_px=neighbor_max,parallax_deg=parallax)


def transfer_crop_intrinsics(intrinsic, source_crop, target_crop, size):
    """Express one calibrated camera in another crop; do not invent a new center."""
    sx,sy,sw,sh=source_crop;tx,ty,tw,th=target_crop
    if min(sw,sh,tw,th,size)<=0:raise ValueError('positive crop sizes required')
    affine=np.array([[sw/tw,0,(sx-tx)*size/tw],
                     [0,sh/th,(sy-ty)*size/th],[0,0,1.]])
    return affine@np.asarray(intrinsic,float)


def render_identity(points, colors, intrinsic, rotation, center, shape, radius=2):
    """Z-buffer splats with exact, unmodified baseline point IDs; no hole fill."""
    height,width=shape
    local=(np.asarray(points)-center)@rotation.T
    original=np.flatnonzero(np.isfinite(local).all(1)&(local[:,2]>.03))
    h=local[original]@intrinsic.T
    xy=np.rint(h[:,:2]/h[:,2:]).astype(np.int64)
    z=local[original,2]
    valid=(xy[:,0]>=-radius)&(xy[:,0]<width+radius)&(xy[:,1]>=-radius)&(xy[:,1]<height+radius)
    xy,z,original=xy[valid],z[valid],original[valid]
    depth=np.full(height*width,np.inf)
    ids=np.full(height*width,len(points),np.int64)
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            x,y=xy[:,0]+dx,xy[:,1]+dy
            keep=(x>=0)&(x<width)&(y>=0)&(y<height)
            np.minimum.at(depth,y[keep]*width+x[keep],z[keep])
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            x,y=xy[:,0]+dx,xy[:,1]+dy
            keep=(x>=0)&(x<width)&(y>=0)&(y<height)
            index=y[keep]*width+x[keep]
            front=z[keep]==depth[index]
            np.minimum.at(ids,index[front],original[keep][front])
    visible=ids<len(points)
    rgb=np.full((height*width,3),[15,19,26],np.uint8)
    rgb[visible]=np.clip(np.asarray(colors)[ids[visible]]*255,0,255).astype(np.uint8)
    ids[~visible]=-1
    return rgb.reshape(height,width,3),depth.reshape(height,width),ids.reshape(height,width)


def map_surface_point(pixel, point_ids, points, intrinsic, rotation, center):
    """Subpixel XYZ on a locally observed map surfel, with source IDs retained.

    A rendered splat's center is not the surface location of every covered pixel.
    Fit only observed nearby map points; no target depth or map NN is consulted.
    """
    x,y=np.rint(pixel).astype(int);height,width=point_ids.shape
    if not (0<=x<width and 0<=y<height) or point_ids[y,x]<0:return None
    seed=int(point_ids[y,x])
    ids=np.unique(point_ids[max(0,y-10):min(height,y+11),max(0,x-10):min(width,x+11)])
    ids=ids[ids>=0]
    ids=ids[np.linalg.norm(points[ids]-points[seed],axis=1)<.035]
    if len(ids)<6:return None
    q=points[ids];origin=q.mean(0)
    _,singular,axes=np.linalg.svd(q-origin,full_matrices=False)
    if singular[1]<.001:return None
    normal=axes[-1];error=float(np.quantile(abs((q-origin)@normal),.9))
    if error>.003:return None
    ray=np.r_[pixel,1.]@np.linalg.inv(intrinsic).T@rotation
    denominator=float(ray@normal)
    if abs(denominator)<1e-6:return None
    depth=float((origin-center)@normal/denominator)
    xyz=center+depth*ray
    if depth<=0 or np.linalg.norm(xyz-points[seed])>.02:return None
    return dict(xyz=xyz,point_ids=ids,plane_p90_m=error,splat_center_id=seed)
