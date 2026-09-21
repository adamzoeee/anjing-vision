"""Independent RGB surface experiments. Never writes formal scene data."""
import json,hashlib,sys
import numpy as np
from PIL import Image,ImageDraw
from scipy.spatial import Delaunay,cKDTree
from audit_screen_gaps import baseline
from build_window_observed_components import arrays,transform,DEST as SOURCE
from audit_scan46_window_camera import camera
from audit_scan46_observation_planes import ROOT,POST
from verify_september12_candidate import VIEWS,depths
from review_window_camera import render

DEST=ROOT/'data/work/46/local_gaps_20260912/visual_only_probe'

def supported_mask(mask,pixels,votes):
    result=mask.copy()
    result.flat[np.asarray(pixels)[np.asarray(votes)<3]]=False
    return result

def r2_labels():
    """Conservative object interiors traced in source frame 127 (896px crop)."""
    out=Image.new('L',(224,224));draw=ImageDraw.Draw(out)
    polygons=[[(535,602),(609,567),(684,582),(745,618),(699,687),(639,746),(584,705),(539,677)],
              [(782,604),(791,671),(776,843),(721,868),(638,847),(665,757),(712,699)]]
    for label,poly in enumerate(polygons,1):
        draw.polygon([(round(x/4),round(y/4)) for x,y in poly],fill=label)
    return np.array(out)

def grid_faces(index,labels):
    a,b,d,e=index[:-1,:-1],index[:-1,1:],index[1:,:-1],index[1:,1:]
    faces=np.concatenate([np.stack([a,b,d],-1).reshape(-1,3),np.stack([b,e,d],-1).reshape(-1,3)])
    faces=faces[(faces>=0).all(1)]
    vertex_labels=labels[index>=0]
    kinds=vertex_labels[faces]
    return faces[(kinds[:,0]>0)&(kinds==kinds[:,:1]).all(1)]

def safe_faces(p,uv,faces,mask,max_edge=.06):
    tri=p[faces];edge=np.max(np.linalg.norm(tri-np.roll(tri,1,axis=1),axis=2),axis=1)
    samples=np.concatenate([uv[faces],uv[faces].mean(1)[:,None]],axis=1)
    xy=np.rint(samples).astype(int);h,w=mask.shape
    valid=((xy[:,:,0]>=0)&(xy[:,:,0]<w)&(xy[:,:,1]>=0)&(xy[:,:,1]<h)).all(1)
    xy[:,:,0]=np.clip(xy[:,:,0],0,w-1);xy[:,:,1]=np.clip(xy[:,:,1],0,h-1)
    return faces[valid&(edge<=max_edge)&mask[xy[:,:,1],xy[:,:,0]].all(1)]

def overlay(image,zbuf,p,uv,faces,texture,view):
    k,r,c=view;local=(p-c)@r.T;hom=local@k.T;xy=hom[:,:2]/hom[:,2:]*4
    output=np.array(image).copy();zbuf=zbuf.copy()
    for face in faces:
        z=local[face,2]
        if (z<=.03).any():continue
        v=xy[face];lo=np.maximum(np.floor(v.min(0)).astype(int),0);hi=np.minimum(np.ceil(v.max(0)).astype(int),895)
        if (hi<lo).any():continue
        mat=np.vstack([v.T,np.ones(3)])
        if abs(np.linalg.det(mat))<1e-6:continue
        yy,xx=np.mgrid[lo[1]:hi[1]+1,lo[0]:hi[0]+1];b=np.linalg.inv(mat)@np.stack([xx.ravel(),yy.ravel(),np.ones(xx.size)])
        inside=(b>=-1e-7).all(0);d=1/(b/z[:,None]).sum(0)
        good=inside&(d<=zbuf[yy.ravel(),xx.ravel()]+.0001)
        if not good.any():continue
        pix=((b[:,good]/z[:,None])*d[good]) .T@uv[face]
        tx=np.clip(np.rint(pix[:,0]/223*(texture.shape[1]-1)).astype(int),0,texture.shape[1]-1)
        ty=np.clip(np.rint(pix[:,1]/223*(texture.shape[0]-1)).astype(int),0,texture.shape[0]-1)
        y,x=yy.ravel()[good],xx.ravel()[good];output[y,x]=texture[ty,tx];zbuf[y,x]=d[good]
    return Image.fromarray(output)

def main():
    global DEST
    r2='--r2' in sys.argv
    independent='--independent-objects' in sys.argv
    if independent:r2=True
    envelope='--display-envelope' in sys.argv
    if envelope:
        raise RuntimeError('Rejected display deformation: 19-22cm boundary shifts distort the cushion. See visual_only_r2_envelope/review.json. Formal scene is unchanged.')
    if r2:DEST=DEST.parent/'visual_only_r2_objects'
    if independent:DEST=DEST.parent/'independent_objects_verified'
    if envelope:DEST=DEST.parent/'visual_only_r2_envelope'
    DEST.mkdir(parents=True,exist_ok=True)
    lock=json.loads((DEST.parent/'new2/baseline_lock.json').read_text())
    def frozen():return {n:hashlib.sha256((POST/n).read_bytes()).hexdigest()==h for n,h in lock.items()}
    assert all(frozen().values())
    bp,bc,vp,vc=baseline();maps,images,conf=arrays(SOURCE)
    t=json.loads((SOURCE/'integration_components_extent.json').read_text())['transform']
    frame=2 if r2 else 15
    world=transform(maps[frame],t);k,r,c,error=camera(world)
    if error>1.5 and not r2:raise RuntimeError('Existing source camera not usable')
    # Observed curtain only. No unknown side wall, radiator, cushion or exterior.
    mask=np.zeros((224,224),bool);mask[110:219,105:196]=True
    local=(bp-c)@r.T;h=local@k.T
    uv=np.divide(h[:,:2],h[:,2:],out=np.full((len(h),2),-999.),where=abs(h[:,2:])>1e-8)
    xy=np.rint(uv).astype(int);valid=(local[:,2]>0)&(xy[:,0]>=0)&(xy[:,0]<224)&(xy[:,1]>=0)&(xy[:,1]<224)
    ids=np.flatnonzero(valid);x,y=xy[ids].T
    support=mask[y,x]&(conf[15,y,x]>=3)&(np.linalg.norm(bp[ids]-world[y,x],axis=1)<.02)&(np.max(abs(bc[ids]-images[15,y,x]/255),axis=1)<.12)
    ids=ids[support]
    # One unchanged baseline vertex per source pixel; never generate new positions.
    _,first=np.unique(xy[ids],axis=0,return_index=True);ids=ids[first];p=bp[ids];uv=uv[ids]
    faces=safe_faces(p,uv,Delaunay(uv).simplices,mask) if not r2 else None
    if r2:
        # A single observed state, with neighbouring frames used only to check
        # depth. No new-camera solution or unverified R2 transform is used.
        yy,xx=np.mgrid[:224,:224]
        labels=r2_labels()
        if independent:
            shape=Image.new('L',(224,224));dr=ImageDraw.Draw(shape)
            # Three distinct observed surfaces, never stretched to each other.
            dr.polygon([(103,103),(200,103),(202,140),(191,141),(177,139),
                        (165,136),(153,138),(140,135),(124,138),(103,140)],fill=1)
            dr.polygon([(128,153),(140,145),(151,141),(161,141),(173,145),
                        (189,154),(179,171),(162,191),(153,191),(137,182),(128,170)],fill=2)
            dr.polygon([(195,145),(201,145),(198,187),(195,214),(178,219),
                        (157,214),(156,207),(168,185),(178,175)],fill=3)
            labels=np.array(shape)
        if envelope:
            # Approximate display geometry from a single observed state, NOT
            # validated reconstruction. Include object boundaries, not interiors
            # only; the image contour keeps bed and shelf outside the surface.
            shape=Image.new('L',(224,224));dr=ImageDraw.Draw(shape)
            dr.polygon([(103,142),(120,138),(137,137),(150,140),(163,138),(176,142),(190,143),(201,145),(201,157),(199,180),
                        (196,216),(158,216),(127,215),(128,170),(127,155),
                        (117,150),(103,147)],fill=1)
            dr.polygon([(202,35),(213,35),(213,142),(202,144),(198,132)],fill=2)
            labels=np.array(shape)
        mask=(labels>0)&(conf[frame]>5)&np.isfinite(world).all(-1)
        mask &= (images[frame,:,:,2].astype(float)-images[frame,:,:,0]<23)
        pixels=np.flatnonzero(mask);points=world.reshape(-1,3)[pixels]
        cols=images[frame].reshape(-1,3)[pixels]/255.;support=np.zeros(len(points),int)
        for j in [0,1,3,4,5]:
            good=(conf[j]>5)&np.isfinite(maps[j]).all(-1)
            q=transform(maps[j],t)[good];dist,idx=cKDTree(q).query(points)
            support+=(dist<.02)&(np.max(abs(images[j][good][idx]/255.-cols),axis=1)<.12)
        evidence={str(label):{'source_pixels':int((labels.ravel()[pixels]==label).sum()),
                              'supported_pixels':int(((labels.ravel()[pixels]==label)&(support>=3)).sum())}
                  for label in np.unique(labels) if label>0}
        mask=supported_mask(mask,pixels,support)
        uv=np.column_stack([xx[mask],yy[mask]]).astype(float);p=world[mask]
        if envelope:
            # Explicit approximate-display boundary deformation. The existing
            # curtain itself is excluded. Its visible lower edge constrains only
            # the top of the observed wall/cushion patch, fading to zero below.
            vk,vr,vcam=VIEWS['front'];local_base=(bp-vcam)@vr.T
            hp=local_base@vk.T
            proj=np.divide(hp[:,:2],hp[:,2:],out=np.full((len(bp),2),-999.),where=abs(hp[:,2:])>1e-8)*4
            controls=[]
            for sx in [140,150,160,170,180]:
                source=world[139,sx];h0=((source-vcam)@vr.T)@vk.T;screen=h0[:2]/h0[2]*4
                selected=(local_base[:,2]>0)&(abs(proj[:,0]-screen[0])<5)&(proj[:,1]>648)&(proj[:,1]<670)&(bc.mean(1)>.5)&(bp[:,2]<0)
                if selected.sum()<10:raise RuntimeError('Curtain boundary control missing')
                target=np.median(bp[selected],axis=0);controls.append(target-source)
            controls=np.array(controls)
            if np.linalg.norm(controls,axis=1).max()>.25:raise RuntimeError('Display correction exceeds local limit')
            displacement=np.column_stack([np.interp(uv[:,0],[140,150,160,170,180],controls[:,axis]) for axis in range(3)])
            weight=np.clip((151-uv[:,1])/12,0,1);weight=weight*weight*(3-2*weight)
            weight[labels[mask]!=1]=0
            p=p+displacement*weight[:,None]
            correction={'source_edge_row':139,'control_columns':[140,150,160,170,180],
                        'displacements_m':controls.tolist(),'max_shift_m':float(np.linalg.norm(displacement*weight[:,None],axis=1).max()),
                        'unchanged_below_source_row':151,'kind':'approximate_display_only_not_reconstruction'}
        # Grid triangles only: never span a missing pixel or hidden surface.
        index=np.full(mask.shape,-1,int);index[mask]=np.arange(mask.sum())
        # Validate adjacency in the observed surface before the explicitly
        # recorded display deformation; do not punch holes into its stretched
        # boundary by applying the reconstruction sampling limit afterwards.
        faces=safe_faces(world[mask] if envelope else p,uv,grid_faces(index,labels),mask,max_edge=.03)
    # Exact source preprocessing geometry, at 4x texture resolution.
    original=Image.open(ROOT/f'data/work/45/frames/frame_{126+frame:05d}.jpg').convert('RGB')
    w,h=original.size;s=round(224*max(w/h,h/w));nw,nh=round(w*s/max(w,h)),round(h*s/max(w,h))
    ox,oy=nw//2-112,nh//2-112
    texture=np.array(original.resize((nw*4,nh*4),Image.Resampling.LANCZOS).crop((ox*4,oy*4,(ox+224)*4,(oy+224)*4)))
    Image.fromarray(texture).save(DEST/'source_texture.png')
    if r2:
        proof=Image.fromarray(texture).copy();draw=ImageDraw.Draw(proof)
        for y,x in zip(*np.where(mask)):
            draw.point((int(x*4),int(y*4)),fill=(255,0,255))
        proof.save(DEST/'source_evidence.png')
    # Portable visual-only OBJ; never appended to the formal PLY or measurements.
    obj=['mtllib curtain.mtl','usemtl real_rgb']
    obj += ['v '+' '.join(map(str,v)) for v in p]
    obj += [f'vt {v[0]/223} {1-v[1]/223}' for v in uv]
    obj += ['f '+' '.join(f'{i+1}/{i+1}' for i in face) for face in faces]
    (DEST/'curtain.obj').write_text('\n'.join(obj))
    (DEST/'curtain.mtl').write_text('newmtl real_rgb\nKd 1 1 1\nmap_Kd source_texture.png\n')
    if not r2:np.save(DEST/'baseline_vertex_ids.npy',ids)
    report=dict(purpose='visual-only',status='unreviewed_candidate',excluded_from_measurements=True,source_frame=125+frame,
                vertices=len(p),triangles=len(faces),all_vertices_unchanged_baseline=not r2,
                camera_fit_px=error,camera_fit_used_for_geometry=not r2,
                geometry_source='original point-map pixel coordinates' if r2 else 'baseline vertices',views={})
    report['approximate_display_geometry']=envelope
    report['independent_observed_surfaces']=independent
    report['surface_positions_deformed']=envelope
    if independent:
        # Audit the bright baseline strip against actual frame-127 observations.
        # Candidate-only colour repair: no baseline position is deleted/moved.
        vk,vr,cc=VIEWS['front'];lc=(bp-cc)@vr.T;hh=lc@vk.T
        xy=np.divide(hh[:,:2],hh[:,2:],out=np.full((len(bp),2),-999.),where=abs(hh[:,2:])>1e-8)*4
        ids=np.flatnonzero((lc[:,2]>0)&(xy[:,0]>615)&(xy[:,0]<780)&(xy[:,1]>640)&(xy[:,1]<680)&(bc.mean(1)>.5)&(bp[:,2]<0))
        dd,ii=cKDTree(world.reshape(-1,3)).query(bp[ids]);sy,sx=np.unravel_index(ii,(224,224))
        observed=images[frame,sy,sx]/255.
        good=(dd<.025)&(sy>=99)&(sy<=115)&(sx>=103)&(sx<200)&(conf[frame,sy,sx]>5)&(observed.mean(1)<.42)
        ids=ids[good];replacement=observed[good]
        # A nearest neighbour is not a semantic identity proof. Keep this as
        # evidence only, not an automatic edit of the old curtain seam.
        cv=vc.copy()
        np.savez_compressed(DEST/'seam_identity_hypothesis.npz',baseline_ids=ids,observed_rgb=replacement)
        report['candidate_only_colour_changes']=0
        report['seam_identity_status']='hypothesis_only_no_baseline_edits'
        report['objects']=evidence
        report['source_sequence']='scan45_original_window_sequence_125_to_130'
        report['support_note']='Neighbour-frame consistency, not independent absolute-depth certification'
        report['old_edge_source_rows']=np.quantile(sy[good],[0,.5,1]).tolist()
        report['old_edge_source_distance_m']=np.quantile(dd[good],[.5,.9]).tolist()
        report['actual_source_hem_row_approx']=139
        report['three_dimensional_position_certified']=False
        assert np.array_equal(p,world[mask]), 'Independent objects must not be stretched or moved'
        face_labels=labels[mask][faces]
        assert (face_labels==face_labels[:,:1]).all(), 'Do not join different objects'
        report['source_coordinates_exactly_preserved']=True
        report['cross_object_triangles']=0
        np.savez_compressed(DEST/'observed_surface.npz',positions=p,uv=uv,faces=faces,labels=labels[mask])
        proof=Image.fromarray(texture).copy();draw=ImageDraw.Draw(proof)
        draw.line([(103*4,108*4),(200*4,108*4)],fill=(255,30,180),width=4)
        draw.text((420,408),'OLD EDGE MAPS HERE: CURTAIN BODY',fill=(255,30,180))
        draw.line([(103*4,139*4),(200*4,139*4)],fill=(30,255,150),width=4)
        draw.text((420,572),'ACTUAL HEM / SEPARATE OBJECTS BELOW',fill=(30,255,150))
        proof.save(DEST/'edge_identity_evidence.png')
    if envelope:report['boundary_correction']=correction
    report['not_validated_for_measurement']=True
    for label in ['front','oblique','overview']:
        view=VIEWS[label];before=render(vp,vc,*view)
        display_base=render(vp,cv,*view) if independent else before
        after=overlay(display_base,depths(vp,view),p,uv,faces,texture,view)
        before.save(DEST/f'before_{label}.png');after.save(DEST/f'after_{label}.png')
        canvas=Image.new('RGB',(1792,930),(15,19,26));canvas.paste(before,(0,34));canvas.paste(after,(896,34))
        d=ImageDraw.Draw(canvas);d.text((10,10),'FROZEN BASELINE',fill='white');d.text((906,10),'VISUAL-ONLY / OBSERVED LOCAL SURFACE',fill='white');canvas.save(DEST/f'comparison_{label}.png')
        a,b=np.array(before),np.array(after);black=(a==[15,19,26]).all(-1);black2=(b==[15,19,26]).all(-1)
        report['views'][label]=dict(filled_black_pixels=int((black&~black2).sum()),changed_pixels=int((a!=b).any(-1).sum()),new_black_pixels=int((~black&black2).sum()))
    report['frozen_files']=frozen();assert all(report['frozen_files'].values())
    (DEST/'audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':main()
