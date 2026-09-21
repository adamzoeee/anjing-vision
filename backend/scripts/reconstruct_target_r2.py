"""Fresh target-centric R2 experiment. Never writes formal artifacts."""
import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'data/work/46/local_gaps_20260912/target_r2_20260914'
DEFAULT_SOURCE = ROOT / 'data/training_sources/scan46_buchong2/窗户边角2.mp4'
# Backward-compatible import used by the saved localization/audit scripts.
SOURCE = DEFAULT_SOURCE


def prepare(source: Path = DEFAULT_SOURCE):
    if not source.is_file():
        raise FileNotFoundError(f'补拍视频不存在: {source}')
    frames = OUT / 'frames'
    frames.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(source))
    rows = []
    sheet = Image.new('RGB', (1400, 7 * 230), '#161b22')
    draw = ImageDraw.Draw(sheet)
    for i, seconds in enumerate(np.linspace(2, 7, 21)):
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, bgr = cap.read()
        if not ok:
            raise RuntimeError(f'Cannot decode {seconds}')
        rgb = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        x, y, width, height = 0, 200, 1080, 1080
        crop = rgb.crop((x, y, x + width, y + height))
        crop.save(frames / f'frame_{i:05d}.jpg', quality=97)
        overlay = rgb.copy()
        ImageDraw.Draw(overlay).rectangle((x, y, x + width - 1, y + height), outline='red', width=12)
        col, row = i % 3, i // 3
        sheet.paste(overlay.resize((112, 200)), (col * 466, row * 230 + 25))
        sheet.paste(crop.resize((200, 200)), (col * 466 + 120, row * 230 + 25))
        draw.text((col * 466, row * 230 + 5), f'{i:02d}  {seconds:.2f}s | FULL -> MODEL CROP', fill='white')
        rows.append(dict(index=i, seconds=float(seconds), crop_xywh=[x, y, width, height], source='窗户边角2.mp4'))
    cap.release()
    sheet.save(OUT / 'input_contact_sheet.png')
    (OUT / 'inputs.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


def reconstruct():
    from pipeline.slam3r_runner import run_reconstruction
    result = run_reconstruction(OUT / 'frames', OUT / 'slam3r', test_name='r2', fps=4,
                                keyframe_stride=2, win_r=5, num_scene_frame=10,
                                num_points_save=1100000, timeout_s=1500,
                                progress_callback=lambda p: print(f'reconstruction {p}', flush=True))
    print(result['ply'], flush=True)


def align():
    from scipy.spatial import cKDTree
    import open3d as o
    from fuse_anchored_supplements import fit_similarity
    from audit_screen_gaps import baseline
    pred = OUT / 'slam3r/r2/preds'
    maps = np.load(pred / 'registered_pcds.npy')
    old = ROOT / 'data/work/46/local_gaps_20260912/new2/bed2'
    refs = np.load(old / 'slam3r/scene/preds/registered_pcds.npy', mmap_mode='r')
    reg = json.loads((old / 'validated_registration.json').read_text())
    bp, bc, _, _ = baseline()
    tree = cKDTree(bp)
    src, dst, ids = [], [], []
    # Same raw timestamps and pixels: old maps serve ONLY as rigid pose anchors,
    # never as the source of the new curtain/cushion surface.
    for i in range(0, 11, 2):
        sec = 2 + i / 4
        ref = 21 + int(sec * 2)
        xx, yy = np.meshgrid(np.arange(550, 850, 12), np.arange(1180, 1270, 12))
        uv = np.c_[xx.ravel(), yy.ravel()].astype(np.float32)
        a = (uv - [0, 200]).astype(np.float32) * 224 / 1080
        b = (uv - [0, 840]).astype(np.float32) * 224 / 1080
        p = cv2.remap(maps[i], a[:, 0, None], a[:, 1, None], cv2.INTER_LINEAR).reshape(-1, 3)
        q = cv2.remap(np.asarray(refs[ref]), b[:, 0, None], b[:, 1, None], cv2.INTER_LINEAR).reshape(-1, 3)
        q = reg['scale'] * (q @ np.array(reg['rotation']).T) + reg['translation']
        distance, nearest = tree.query(q)
        valid = np.isfinite(p).all(1) & (distance < .035)
        src.extend(p[valid]); dst.extend(q[valid]); ids.extend([i] * int(valid.sum()))
    p, q, ids = np.array(src), np.array(dst), np.array(ids)
    if len(p) < 40:
        raise RuntimeError('Insufficient rigid anchors')
    train = (ids // 2) % 2 == 0
    scale, rotation, translation = fit_similarity(p[train], q[train])
    aligned = scale * (p @ rotation.T) + translation
    error = np.linalg.norm(aligned - q, axis=1)
    report = dict(scale=scale, rotation=rotation.tolist(), translation=translation.tolist(),
                  training_points=int(train.sum()), heldout_points=int((~train).sum()),
                  heldout_m=np.quantile(error[~train], [.5, .9, .95]).tolist(),
                  source='Same-timestamp floor observations; no soft anchors',
                  anchor_singular_values=np.linalg.svd(p[train]-p[train].mean(0), compute_uv=False).tolist())
    np.savez_compressed(OUT / 'rigid_correspondences.npz', source=p, target=q, frame=ids, train=train)
    (OUT / 'registration.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


def patch():
    import open3d as o
    from scipy.spatial import cKDTree
    from audit_screen_gaps import baseline
    from local_gap_geometry import fuse_multiview
    from review_window_camera import render
    from verify_september12_candidate import VIEWS
    pred = OUT / 'slam3r/r2/preds'
    maps = np.load(pred / 'registered_pcds.npy')
    imgs = np.load(pred / 'input_imgs.npy')
    conf = np.load(pred / 'registered_confs.npy')
    reg = json.loads((OUT / 'registration.json').read_text())
    bp, bc, vp, vc = baseline()
    parts, colors, frames, counts = [], [], [], []
    sheet = Image.new('RGB', (5*224, 3*248), '#161b22')
    polygon = np.array([(94, 38), (176, 32), (198, 50), (208, 90),
                        (210, 145), (154, 150), (119, 135), (110, 101), (104, 80)], np.int32)
    for i in range(13):
        mask = np.zeros((224, 224), np.uint8)
        poly = polygon + [0, round(i*1.5)]
        cv2.fillPoly(mask, [poly], 1)
        world = reg['scale'] * (maps[i] @ np.array(reg['rotation']).T) + reg['translation']
        valid = mask.astype(bool) & np.isfinite(world).all(-1) & (conf[i] > 5)
        parts.append(world[valid]); colors.append(imgs[i][valid]/255); frames.extend([i]*int(valid.sum()))
        counts.append(dict(frame=i, mask=int(mask.sum()), confidence_retained=int(valid.sum())))
        im = imgs[i].astype('uint8').copy()
        cv2.polylines(im, [poly], True, (255, 0, 255), 1)
        sheet.paste(Image.fromarray(im), ((i%5)*224, (i//5)*248+24))
    sheet.save(OUT/'surface_masks.png')
    consensus_p, consensus_c = fuse_multiview(np.vstack(parts), np.vstack(colors), np.array(frames), .006, 3)
    # Keep the connected topology of one observed keyframe. Validate its surface
    # against the same continuous clip, rather than layering thirteen surfaces.
    from window_observed_surface import sample_cells
    world = reg['scale']*(maps[0]@np.array(reg['rotation']).T)+reg['translation']
    poly=np.array([(80,60),(98,51),(121,48),(149,40),(176,44),(195,59),
                   (205,89),(210,145),(149,151),(124,136),(116,104),(105,79)],np.int32)
    mask=np.zeros((224,224),np.uint8);cv2.fillPoly(mask,[poly],1)
    flat=world.reshape(-1,3);rgb=imgs[0].reshape(-1,3)/255
    votes=np.zeros(len(flat),np.int16)
    for i in range(1,13):
        q=reg['scale']*(maps[i]@np.array(reg['rotation']).T)+reg['translation']
        d,ix=cKDTree(q.reshape(-1,3)).query(flat)
        votes+=(d<.018)&(np.max(abs(imgs[i].reshape(-1,3)[ix]/255-rgb),axis=1)<.15)
    valid=mask.astype(bool)&(conf[0]>5)&(votes.reshape(224,224)>=3)
    p,c=sample_cells(world,imgs[0]/255,valid,subdivisions=3,max_edge=.025)
    evidence=imgs[0].astype('uint8').copy();cv2.polylines(evidence,[poly],True,(255,0,255),1)
    Image.fromarray(evidence).resize((672,672)).save(OUT/'connected_surface_source.png')
    np.savez_compressed(OUT/'surface_evidence.npz',frame=0,valid=valid,votes=votes.reshape(224,224))
    cloud = o.geometry.PointCloud(o.utility.Vector3dVector(p)); cloud.colors=o.utility.Vector3dVector(c)
    o.io.write_point_cloud(str(OUT/'independent_surface_patch.ply'), cloud)
    tree=cKDTree(bp); distance,_=tree.query(p)
    keep=distance>.012
    ap,ac=p[keep],c[keep]
    cloud=o.geometry.PointCloud(o.utility.Vector3dVector(np.vstack([bp,ap])))
    cloud.colors=o.utility.Vector3dVector(np.vstack([bc,ac]))
    o.io.write_point_cloud(str(OUT/'candidate.ply'),cloud)
    for name in ['front','oblique','overview','overhead']:
        k,r,center=VIEWS[name]
        before=render(vp,vc,k,r,center)
        after=render(np.vstack([vp,ap]),np.vstack([vc,ac]),k,r,center)
        canvas=Image.new('RGB',(1792,928),'#161b22');d=ImageDraw.Draw(canvas)
        canvas.paste(before,(0,32));canvas.paste(after,(896,32))
        d.text((10,8),'FROZEN BASELINE',fill='white');d.text((906,8),'FRESH R2 CANDIDATE / NOT PUBLISHED',fill='white')
        canvas.save(OUT/f'comparison_{name}.png')
        render(p,c,k,r,center).save(OUT/f'patch_{name}.png')
    report=dict(frames=counts, consensus_points=len(consensus_p), connected_surface_points=len(p), candidate_additions=len(ap),
                baseline_points_preserved=len(bp),accepted=False,
                note='Candidate only; requires multi-view inspection and independent rigid checks')
    (OUT/'patch_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


def refine():
    import open3d as o
    from scipy.spatial import cKDTree
    from audit_screen_gaps import baseline
    pred=OUT/'slam3r/r2/preds'
    maps=np.load(pred/'registered_pcds.npy');imgs=np.load(pred/'input_imgs.npy')
    reg=json.loads((OUT/'registration.json').read_text())
    bp,bc,_,_=baseline();tree=cKDTree(bp)
    train=[];held=[];references=[]
    for i in [0,2,4,6,8,10]:
        # White shelf vertical side only, never curtain or bedding.
        q=reg['scale']*(maps[i][115:205,218:223].reshape(-1,3)@np.array(reg['rotation']).T)+reg['translation']
        color=imgs[i][115:205,218:223].reshape(-1,3)/255
        d,ix=tree.query(q)
        valid=(d<.04)&(np.max(abs(bc[ix]-color),axis=1)<.12)
        if i%4==0:train.extend(q[valid])
        else:held.extend(q[valid]);references.extend(bp[ix[valid]])
    anchors=np.load(OUT/'rigid_correspondences.npz')
    q=reg['scale']*(anchors['source']@np.array(reg['rotation']).T)+reg['translation']
    train.extend(q[anchors['train']]);held.extend(q[~anchors['train']]);references.extend(anchors['target'][~anchors['train']])
    train=np.array(train);held=np.array(held);references=np.array(references)
    source=o.geometry.PointCloud(o.utility.Vector3dVector(train));source=source.voxel_down_sample(.007)
    ix=np.unique(tree.query(train,k=12)[1]);target=o.geometry.PointCloud(o.utility.Vector3dVector(bp[ix]))
    target.estimate_normals(o.geometry.KDTreeSearchParamHybrid(radius=.05,max_nn=30))
    fit=o.pipelines.registration.registration_icp(source,target,.04,np.eye(4),
        o.pipelines.registration.TransformationEstimationPointToPlane(o.pipelines.registration.TukeyLoss(k=.02)),
        o.pipelines.registration.ICPConvergenceCriteria(max_iteration=40))
    t=fit.transformation;after=held@t[:3,:3].T+t[:3,3]
    before=np.linalg.norm(held-references,axis=1);error=np.linalg.norm(after-references,axis=1)
    accepted=bool(np.quantile(error,.9)<np.quantile(before,.9) and np.quantile(np.linalg.norm(after-held,axis=1),.95)<.035)
    report=dict(before=np.quantile(before,[.5,.9,.95]).tolist(),after=np.quantile(error,[.5,.9,.95]).tolist(),
                correction=t.tolist(),accepted=accepted,train=len(train),heldout=len(held))
    (OUT/'icp_check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


def bias():
    """Test a two-parameter depth bias; never accept solely on training planes."""
    from audit_screen_gaps import baseline
    from audit_scan46_window_camera import camera
    from scipy.spatial import cKDTree
    import open3d as o
    maps=np.load(OUT/'slam3r/r2/preds/registered_pcds.npy')
    reg=json.loads((OUT/'registration.json').read_text())
    world=reg['scale']*(maps[0]@np.array(reg['rotation']).T)+reg['translation']
    k,r,center,rms=camera(world)
    bp,bc,_,_=baseline();tree=cKDTree(bp)
    rad=world[68:77,8:60].reshape(-1,3)
    _,ix=tree.query(rad,k=12)
    base=o.geometry.PointCloud(o.utility.Vector3dVector(bp[np.unique(ix)]))
    o.utility.random.seed(46)
    plane,inliers=base.segment_plane(.012,3,1000)
    plane=np.array(plane);normal=plane[:3]
    anchors=np.load(OUT/'rigid_correspondences.npz')
    mask=anchors['frame']==0
    floor=reg['scale']*(anchors['source'][mask]@np.array(reg['rotation']).T)+reg['translation']
    target=anchors['target'][mask]
    def terms(p):
        delta=p-center;z=(delta@r.T)[:,2]
        return delta,delta/z[:,None]
    fa,fb=terms(floor);ra,rb=terms(rad)
    # Equal aggregate weight for floor correspondences and radiator plane.
    a=np.vstack([np.c_[fa.ravel(),fb.ravel()]/np.sqrt(fa.size),
                 np.c_[ra@normal,rb@normal]/np.sqrt(len(rad))])
    y=np.r_[(target-center).ravel()/np.sqrt(fa.size),
            np.full(len(rad),-plane[3]-normal@center)/np.sqrt(len(rad))]
    coef=np.linalg.lstsq(a,y,rcond=None)[0]
    def correct(p):
        da,db=terms(p);return center+coef[0]*da+coef[1]*db
    held=anchors['frame']==2
    p=reg['scale']*(anchors['source'][held]@np.array(reg['rotation']).T)+reg['translation']
    q=anchors['target'][held]
    shelf=world[115:205,218:223].reshape(-1,3)
    before=np.linalg.norm(p-q,axis=1);after=np.linalg.norm(correct(p)-q,axis=1)
    sd=tree.query(shelf)[0];sad=tree.query(correct(shelf))[0]
    report=dict(depth_scale=float(coef[0]),depth_offset_m=float(coef[1]),camera_fit_rms_px=rms,
                radiator_plane=plane.tolist(),plane_inliers=len(inliers),
                heldout_floor_before=np.quantile(before,[.5,.9]).tolist(),
                heldout_floor_after=np.quantile(after,[.5,.9]).tolist(),
                heldout_shelf_before=np.quantile(sd,[.5,.9]).tolist(),
                heldout_shelf_after=np.quantile(sad,[.5,.9]).tolist(),
                radiator_before=float(np.median(abs(rad@normal+plane[3]))),
                radiator_after=float(np.median(abs(correct(rad)@normal+plane[3]))))
    report['accepted']=bool(np.quantile(after,.9)<.035 and np.quantile(sad,.9)<.035 and rms<2)
    (OUT/'depth_bias_check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


def poseaudit():
    """Find actual rigid 2D/3D correspondences, separate from target depth."""
    from register_september12_supplements import photo
    from audit_screen_gaps import baseline
    from scipy.spatial import cKDTree
    pred=OUT/'slam3r/r2/preds'
    imgs=np.load(pred/'input_imgs.npy')
    primary=ROOT/'data/work/46/window_local_sequence_20260907'
    refmaps=np.load(primary/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r')
    tr=json.loads((primary/'integration_components_extent.json').read_text())['transform']
    bp,bc,_,_=baseline();tree=cKDTree(bp)
    folder=OUT/'rigid_pose';folder.mkdir(exist_ok=True)
    sift=cv2.SIFT_create(nfeatures=7000,contrastThreshold=.015)
    source={};sheet=Image.new('RGB',(5*224,5*248),'#161b22')
    for i in range(21):
        mask=np.zeros((224,224),np.uint8)
        # Keep a margin from the visible curtain hem and bedding boundaries.
        y=int(np.interp(i,[0,10,14,18,20],[68,78,105,127,143]))
        mask[y:y+5,8:35]=1
        if i<=10:
            mask[190:220,140:190]=2
        mask[115:205,219:223]=3
        # Source image itself shows no sill/frame in this target crop.
        im=imgs[i].astype('uint8').copy()
        im[mask>0]=(im[mask>0]*.4+np.array([255,70,20])*.6).astype('uint8')
        sheet.paste(Image.fromarray(im),((i%5)*224,(i//5)*248+24))
        ImageDraw.Draw(sheet).text(((i%5)*224,(i//5)*248),str(i),fill='white')
        np.save(folder/f'mask_{i:02d}.npy',mask)
        high=photo(OUT/f'frames/frame_{i:05d}.jpg')
        highmask=cv2.resize((mask>0).astype('uint8')*255,(672,672),interpolation=cv2.INTER_NEAREST)
        source[i]=(high,*sift.detectAndCompute(cv2.cvtColor(high,cv2.COLOR_RGB2GRAY),highmask))
    sheet.save(folder/'masks_all_frames.png')
    srcpix=[];targets=[];frames=[];regions=[];records=[]
    for j in range(125,176,2):
        high=photo(ROOT/f'data/work/45/frames/frame_{j+1:05d}.jpg')
        rk,rd=sift.detectAndCompute(cv2.cvtColor(high,cv2.COLOR_RGB2GRAY),None)
        for i in [1,5,9,13,17,20]:
            image,sk,sd=source[i]
            if sd is None or rd is None:continue
            pairs=cv2.BFMatcher().knnMatch(sd,rd,k=2)
            ms=[a for pair in pairs if len(pair)==2 for a,b in [pair] if a.distance<.72*b.distance]
            if len(ms)<4:continue
            uv=np.array([sk[a.queryIdx].pt for a in ms],np.float32)/3
            rv=np.array([rk[a.trainIdx].pt for a in ms],np.float32)/3
            q=cv2.remap(np.asarray(refmaps[j-125]),rv[:,0,None],rv[:,1,None],cv2.INTER_LINEAR).reshape(-1,3)
            q=tr['scale']*(q@np.array(tr['rotation']).T)+tr['translation']
            d,ix=tree.query(q)
            rgb=cv2.remap(high,rv[:,0,None]*3,rv[:,1,None]*3,cv2.INTER_LINEAR).reshape(-1,3)/255
            good=(d<.035)&(np.max(abs(bc[ix]-rgb),axis=1)<.2)
            mask=np.load(folder/f'mask_{i:02d}.npy')
            xy=np.clip(np.rint(uv).astype(int),0,223)
            srcpix.extend(uv[good]);targets.extend(bp[ix[good]]);frames.extend([i]*int(good.sum()));regions.extend(mask[xy[good,1],xy[good,0]])
            if good.any():records.append([i,j,len(ms),int(good.sum())])
    np.savez_compressed(folder/'feature_correspondences.npz',pixels=np.array(srcpix).reshape(-1,2),
                        points=np.array(targets).reshape(-1,3),frame=np.array(frames),region=np.array(regions))
    (folder/'retrieval.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    print(json.dumps({'matches':len(srcpix),'pairs':records}),flush=True)


def rigidcameras():
    """Use full-RGB rigid observations outside the target crop to constrain poses."""
    from audit_screen_gaps import baseline
    from scipy.spatial import cKDTree
    from audit_scan46_window_camera import camera
    folder=OUT/'rigid_pose';folder.mkdir(exist_ok=True)
    old=ROOT/'data/work/46/local_gaps_20260912/new2/bed2'
    maps=np.load(old/'slam3r/scene/preds/registered_pcds.npy',mmap_mode='r')
    images=np.load(old/'slam3r/scene/preds/input_imgs.npy',mmap_mode='r')
    tr=json.loads((old/'validated_registration.json').read_text())
    bp,bc,_,_=baseline();tree=cKDTree(bp)
    objects=[];pixels=[];labels=[];records=[]
    for i in range(21):
        # Even frames share exact source timestamps with the old rigid map.
        # Odd frames are constrained below by tracked raw-image rigid pixels.
        ref=25+i//2
        yy,xx=np.mgrid[105:212:4,105:190:4]
        sy,sx=np.mgrid[90:205:3,211:223:1]
        uv=np.vstack([np.c_[xx.ravel(),yy.ravel()],np.c_[sx.ravel(),sy.ravel()]]).astype(np.float32)
        label=np.r_[np.full(xx.size,2),np.full(sx.size,3)]
        x,y=uv.astype(int).T
        q=tr['scale']*(maps[ref][y,x]@np.array(tr['rotation']).T)+tr['translation']
        color=images[ref][y,x]/255
        d,ix=tree.query(q)
        good=(d<.025)&(np.max(abs(bc[ix]-color),axis=1)<.10)
        uv=uv[good];q=bp[ix[good]];label=label[good]
        if i%2:
            cap=cv2.VideoCapture(str(SOURCE));raw=[]
            for sec in [2+(i-1)/4,2+i/4]:
                cap.set(cv2.CAP_PROP_POS_MSEC,sec*1000);ok,im=cap.read()
                if not ok:raise RuntimeError('raw frame unavailable')
                raw.append(cv2.resize(cv2.cvtColor(im[840:1920],cv2.COLOR_BGR2GRAY),(672,672)))
            cap.release()
            tracked,status,_=cv2.calcOpticalFlowPyrLK(raw[0],raw[1],(uv*3).reshape(-1,1,2),None,
                winSize=(21,21),maxLevel=3,criteria=(cv2.TERM_CRITERIA_COUNT|cv2.TERM_CRITERIA_EPS,40,.01))
            back,bs,_=cv2.calcOpticalFlowPyrLK(raw[1],raw[0],tracked,None,winSize=(21,21),maxLevel=3)
            good=status.ravel().astype(bool)&bs.ravel().astype(bool)&(np.linalg.norm(back.reshape(-1,2)-uv*3,axis=1)<.8)
            uv=tracked.reshape(-1,2)[good]/3;q=q[good];label=label[good]
        uv=uv+np.array([0,(840-200)*224/1080],np.float32)
        objects.append(q.astype(np.float32));pixels.append(uv.astype(np.float32));labels.append(label)
    from local_gap_geometry import transfer_crop_intrinsics
    source_k,_,_,_=camera(np.asarray(maps[30]))
    k=transfer_crop_intrinsics(source_k,[0,840,1080,1080],[0,200,1080,1080],224)
    flags=(cv2.CALIB_USE_INTRINSIC_GUESS|cv2.CALIB_FIX_ASPECT_RATIO|cv2.CALIB_FIX_PRINCIPAL_POINT|
           cv2.CALIB_ZERO_TANGENT_DIST|cv2.CALIB_FIX_K1|cv2.CALIB_FIX_K2|cv2.CALIB_FIX_K3)
    # Spatially distributed held-out points are never passed to calibration.
    train=[np.arange(len(q))%4!=0 for q in objects]
    rs=[];ts=[];training_errors=[]
    cv2.setRNGSeed(4614)
    for q,uv,t in zip(objects,pixels,train):
        ok,rv,tv,inliers=cv2.solvePnPRansac(q[t],uv[t],k,None,iterationsCount=3000,
            reprojectionError=2.,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
        if not ok or inliers is None or len(inliers)<8:
            raise RuntimeError('Rigid PnP has insufficient inliers')
        ids=inliers.ravel()
        rv,tv=cv2.solvePnPRefineLM(q[t][ids],uv[t][ids],k,None,rv,tv)
        rs.append(rv);ts.append(tv)
        e=cv2.projectPoints(q[t][ids],rv,tv,k,None)[0].reshape(-1,2)-uv[t][ids]
        training_errors.extend(np.linalg.norm(e,axis=1))
    error=float(np.sqrt(np.mean(np.square(training_errors))))
    new=np.load(OUT/'slam3r/r2/preds/registered_pcds.npy')
    rg=json.loads((OUT/'registration.json').read_text())
    all_records=[]
    for i,(q,uv,label,t,rv,tv) in enumerate(zip(objects,pixels,labels,train,rs,ts)):
        rot=cv2.Rodrigues(rv)[0];center=-rot.T@tv.ravel()
        projection=cv2.projectPoints(q,rv,tv,k,None)[0].reshape(-1,2)
        e=np.linalg.norm(projection-uv,axis=1)
        residual={}
        for region,name in [(2,'floor'),(3,'shelf')]:
            keep=(label==region)&~t
            residual[name]={'count':int(keep.sum()),'heldout_reprojection_px':np.quantile(e[keep],[.5,.9]).tolist() if keep.any() else None}
        w=rg['scale']*(new[i]@np.array(rg['rotation']).T)+rg['translation']
        _,_,_,model_error=camera(w)
        records.append(dict(frame=i,K=k.tolist(),rotation=rot.tolist(),center=center.tolist(),
                            rigid_checks=residual,pointmap_camera_rms_px=model_error))
        all_records.extend([[i,*point,*pix,int(lab),bool(tr)] for point,pix,lab,tr in zip(q,uv,label,t)])
    np.save(folder/'pose_correspondences.npy',np.array(all_records))
    (folder/'cameras.json').write_text(json.dumps(dict(bundle_rms_px=error,cameras=records),indent=2),encoding='utf-8')
    print(json.dumps(dict(bundle_rms_px=error,K=k.tolist(),checks=[r['rigid_checks'] for r in records])),flush=True)


def bundle(tracked=False):
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix
    folder=OUT/'rigid_pose'
    record=json.loads((folder/('tracked_cameras.json' if tracked else 'cameras.json')).read_text())
    data=np.load(folder/('tracked_correspondences.npy' if tracked else 'pose_correspondences.npy'))
    fid=data[:,0].astype(int);xyz=data[:,1:4];uv=data[:,4:6];region=data[:,6].astype(int);train=data[:,7].astype(bool)
    poses=[]
    for c in record['cameras']:
        r=np.array(c['rotation']);t=-r@np.array(c['center'])
        poses.append(np.r_[cv2.Rodrigues(r)[0].ravel(),t])
    initial_k=np.array(record['cameras'][10]['K'])
    initial_intrinsic=np.array([initial_k[0,0],initial_k[0,2],initial_k[1,2]])
    x0=np.r_[initial_intrinsic,np.array(poses).ravel()]
    weights=np.zeros(len(xyz))
    for i in range(21):
        for label in [2,3]:
            selected=(fid==i)&(region==label)&train
            if selected.sum()>=(6 if tracked else 20):weights[selected]=np.sqrt(100/selected.sum())
    active=np.flatnonzero(weights>0)
    def residual(x):
        k=np.array([[x[0],0,x[1]],[0,x[0],x[2]],[0,0,1.]])
        poses=x[3:].reshape(21,6);out=np.zeros((len(data),2))
        for i in range(21):
            selected=(fid==i)&(weights>0)
            if not selected.any():continue
            pr=cv2.projectPoints(xyz[selected],poses[i,:3],poses[i,3:],k,None)[0].reshape(-1,2)
            out[selected]=(pr-uv[selected])*weights[selected,None]
        # Soft trajectory regularization, never moving baseline geometry.
        smooth=(poses[:-2]-2*poses[1:-1]+poses[2:])*np.array([8,8,8,8,8,8])
        prior=(x[:3]-initial_intrinsic)/np.array([100,30,60])
        return np.r_[out[active].ravel(),smooth.ravel(),prior]
    sparsity=lil_matrix((len(active)*2+19*6+3,len(x0)))
    for row,index in enumerate(active):
        sparsity[row*2:row*2+2,:3]=1
        start=3+fid[index]*6;sparsity[row*2:row*2+2,start:start+6]=1
    offset=len(active)*2
    for i in range(19):sparsity[offset+i*6:offset+(i+1)*6,3+i*6:3+(i+3)*6]=1
    sparsity[-3:,:3]=1
    lower=np.r_[180,50,50,np.full(126,-np.inf)];upper=np.r_[500,175,350,np.full(126,np.inf)]
    fit=least_squares(residual,x0,bounds=(lower,upper),loss='soft_l1',f_scale=1.,
        jac_sparsity=sparsity.tocsr(),max_nfev=120,ftol=1e-5,xtol=1e-5)
    x=fit.x;k=np.array([[x[0],0,x[1]],[0,x[0],x[2]],[0,0,1.]])
    for i,c in enumerate(record['cameras']):
        pose=x[3:].reshape(21,6)[i];r=cv2.Rodrigues(pose[:3])[0]
        c.update(K=k.tolist(),rotation=r.tolist(),center=(-r.T@pose[3:]).tolist())
        selected=fid==i
        if not selected.any():continue
        pr=cv2.projectPoints(xyz[selected],pose[:3],pose[3:],k,None)[0].reshape(-1,2)
        err=np.linalg.norm(pr-uv[selected],axis=1)
        for label,name in [(2,'floor'),(3,'shelf')]:
            keep=(region[selected]==label)&~train[selected]
            c['rigid_checks'][name]={'count':int(keep.sum()),'heldout_reprojection_px':np.quantile(err[keep],[.5,.9]).tolist() if keep.any() else None}
    record.update(method='rigid-only PnP then robust local bundle adjustment',cost=float(fit.cost),evaluations=fit.nfev,
                  optimizer_converged=bool(fit.success),optimizer_message=str(fit.message))
    (folder/('tracked_bundle_cameras.json' if tracked else 'bundle_cameras.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(dict(K=k.tolist(),cost=fit.cost,nfev=fit.nfev,checks=[c['rigid_checks'] for c in record['cameras']])),flush=True)


def depthopt(tracked=False):
    from register_september12_supplements import photo
    from local_gap_geometry import refine_ray_depth
    from audit_scan46_window_camera import camera
    from audit_screen_gaps import baseline
    from scipy.spatial import cKDTree
    import open3d as o
    folder=OUT/'rigid_pose'
    dst=OUT/('ray_depth_tracked' if tracked else 'ray_depth');dst.mkdir(exist_ok=True)
    record=json.loads((folder/('tracked_bundle_cameras.json' if tracked else 'bundle_cameras.json')).read_text())
    cameras=[(np.array(c['K']),np.array(c['rotation']),np.array(c['center'])) for c in record['cameras']]
    usable=[]
    for i,c in enumerate(record['cameras']):
        checks=c['rigid_checks']
        minimum={'floor':3,'shelf':5} if tracked else {'floor':20,'shelf':20}
        if all(checks[n]['count']>=minimum[n] and checks[n]['heldout_reprojection_px'][1]<3.5 for n in ['floor','shelf']):usable.append(i)
    reference=10
    if reference not in usable or len(usable)<3:raise RuntimeError('Insufficient independently checked rigid cameras')
    high=[photo(OUT/f'frames/frame_{i:05d}.jpg') for i in range(21)]
    gray=[cv2.cvtColor(im,cv2.COLOR_RGB2GRAY) for im in high]
    mask=np.zeros((224,224),np.uint8)
    poly=np.array([(86,73),(105,66),(130,58),(175,54),(192,76),(207,107),
                   (213,163),(156,165),(129,149),(121,117),(112,102),(95,91)],np.int32)
    cv2.fillPoly(mask,[poly],4)
    mask[78:83,8:35]=1;mask[190:218,140:190]=2;mask[115:205,219:223]=3
    yy,xx=np.mgrid[:224,:224];selected=(mask>0)&(xx%2==0)&(yy%2==0)
    pixels=np.c_[xx[selected],yy[selected]].astype(float);labels=mask[selected]
    initial=(pixels*3).astype(np.float32).reshape(-1,1,2)
    tracks=np.full((21,len(pixels),2),np.nan);valid=np.zeros((21,len(pixels)),bool)
    tracks[reference]=pixels;valid[reference]=True
    for i in usable:
        if i==reference:continue
        nxt,status,error=cv2.calcOpticalFlowPyrLK(gray[reference],gray[i],initial,None,
            winSize=(19,19),maxLevel=4,criteria=(cv2.TERM_CRITERIA_COUNT|cv2.TERM_CRITERIA_EPS,60,.005))
        back,bs,_=cv2.calcOpticalFlowPyrLK(gray[i],gray[reference],nxt,None,winSize=(19,19),maxLevel=4)
        fb=np.linalg.norm(back.reshape(-1,2)-initial.reshape(-1,2),axis=1)
        good=status.ravel().astype(bool)&bs.ravel().astype(bool)&(fb<.8)&(error.ravel()<15)
        tracks[i]=nxt.reshape(-1,2)/3;valid[i]=good
    maps=np.load(OUT/'slam3r/r2/preds/registered_pcds.npy')
    rg=json.loads((OUT/'registration.json').read_text())
    raw=rg['scale']*(maps[reference]@np.array(rg['rotation']).T)+rg['translation']
    # Convert the initializer to the independently solved camera coordinates.
    # Keeping the old camera's z after changing pose is not a depth initializer.
    _,reference_r,reference_c=cameras[reference]
    depth=((raw[selected]-reference_c)@reference_r.T)[:,2]
    result=refine_ray_depth(pixels,depth,tracks,valid,cameras,reference,max_change=.25)
    bp,bc,vp,vc=baseline();tree=cKDTree(bp)
    report={'usable_frames':usable,'reference':reference,'regions':{}}
    for value,name in [(1,'radiator'),(2,'floor'),(3,'shelf'),(4,'R2')]:
        take=labels==value;accepted=take&result['accepted']
        old_distance=tree.query(raw[selected][take])[0]
        same_old_distance=tree.query(raw[selected][accepted])[0]
        new_distance=tree.query(result['points'][accepted])[0]
        report['regions'][name]={'input':int(take.sum()),'tracks_3views':int((take&(valid.sum(0)>=3)).sum()),
            'accepted':int(accepted.sum()),'baseline_before_m':np.quantile(old_distance,[.5,.9]).tolist() if len(old_distance) else None,
            'baseline_before_same_accepted_subset_m':np.quantile(same_old_distance,[.5,.9]).tolist() if len(same_old_distance) else None,
            'baseline_after_m':np.quantile(new_distance,[.5,.9]).tolist() if len(new_distance) else None,
            'parallax_deg':np.quantile(result['parallax_deg'][take],[.1,.5,.9]).tolist() if take.any() else None,
            'change_m':np.quantile((result['depth']-depth)[take],[.1,.5,.9]).tolist() if take.any() else None}
    np.savez_compressed(dst/'optimization.npz',pixels=pixels,labels=labels,initial_depth=depth,tracks=tracks,valid=valid,**result)
    colors=cv2.remap(high[reference],(pixels[:,0]*3).astype(np.float32)[:,None],
        (pixels[:,1]*3).astype(np.float32)[:,None],cv2.INTER_LINEAR).reshape(-1,3)/255
    accepted=result['accepted']&(labels==4)
    cloud=o.geometry.PointCloud(o.utility.Vector3dVector(result['points'][accepted]));cloud.colors=o.utility.Vector3dVector(colors[accepted])
    o.io.write_point_cloud(str(dst/'diagnostic_surface.ply'),cloud)
    display=high[reference].copy()
    for point,ok,label in zip(pixels,result['accepted'],labels):
        cv2.circle(display,tuple((point*3).astype(int)),2,(0,255,0) if ok else (255,0,0),-1)
    Image.fromarray(display).save(dst/'support.png')
    report['accepted_candidate']=False
    report['status']='rigid_camera_constraints_not_certified'
    report['rejection_reasons']=[]
    for name in ['radiator','floor','shelf']:
        check=report['regions'][name]
        if check['accepted']<20:
            report['rejection_reasons'].append(f'{name}: insufficient independent depth support')
        if check['baseline_after_m'] and check['baseline_after_m'][1]>.03:
            report['rejection_reasons'].append(f'{name}: independent depth P90 exceeds 3 cm')
    report['interpretation']='These diagnostics do not establish correct cameras or irrecoverable R2 depth. No formal export is authorized.'
    (dst/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


def trackrigid():
    """Track the SAME rigid landmarks across frames to avoid point-map pose jitter."""
    from scipy.spatial import cKDTree
    from audit_screen_gaps import baseline
    folder=OUT/'rigid_pose';reference=10
    cap=cv2.VideoCapture(str(SOURCE));raw=[]
    for i in range(21):
        cap.set(cv2.CAP_PROP_POS_MSEC,(2+i/4)*1000);ok,im=cap.read()
        if not ok:raise RuntimeError('Missing source frame')
        raw.append(cv2.resize(cv2.cvtColor(im[840:1920],cv2.COLOR_BGR2RGB),(672,672)))
    cap.release();gray=[cv2.cvtColor(im,cv2.COLOR_RGB2GRAY) for im in raw]
    mask=np.zeros((224,224),np.uint8);mask[85:219,95:192]=2;mask[50:215,201:223]=3
    sm=cv2.resize((mask>0).astype(np.uint8)*255,(672,672),interpolation=cv2.INTER_NEAREST)
    points=cv2.goodFeaturesToTrack(gray[reference],maxCorners=600,qualityLevel=.006,minDistance=5,mask=sm)
    if points is None:raise RuntimeError('No rigid tracks')
    uv=points.reshape(-1,2)/3;xy=np.clip(np.rint(uv).astype(int),0,223)
    labels=mask[xy[:,1],xy[:,0]]
    old=ROOT/'data/work/46/local_gaps_20260912/new2/bed2'
    maps=np.load(old/'slam3r/scene/preds/registered_pcds.npy',mmap_mode='r')
    rg=json.loads((old/'validated_registration.json').read_text())
    q=cv2.remap(np.asarray(maps[30]),uv[:,0,None],uv[:,1,None],cv2.INTER_LINEAR).reshape(-1,3)
    q=rg['scale']*(q@np.array(rg['rotation']).T)+rg['translation']
    bp,bc,_,_=baseline();d,ix=cKDTree(bp).query(q)
    color=cv2.remap(raw[reference],points[:,0,0,None],points[:,0,1,None],cv2.INTER_LINEAR).reshape(-1,3)/255
    good=(d<.025)&(np.max(abs(bc[ix]-color),axis=1)<.14)
    points=points[good];labels=labels[good];q=bp[ix[good]]
    canvas=raw[reference].copy()
    for uv,label in zip(points[:,0],labels):cv2.circle(canvas,tuple(uv.astype(int)),3,(0,255,0) if label==2 else (255,120,0),-1)
    Image.fromarray(canvas).save(folder/'tracked_rigid_sources.png')
    from local_gap_geometry import transfer_crop_intrinsics
    from audit_scan46_window_camera import camera
    source_k,_,_,_=camera(np.asarray(maps[30]))
    k=transfer_crop_intrinsics(source_k,[0,840,1080,1080],[0,200,1080,1080],224)
    records=[];data=[]
    cv2.setRNGSeed(4614)
    positions={reference:points.copy()};visibility={reference:np.ones(len(points),bool)}
    for direction in [-1,1]:
        for i in range(reference+direction,-1 if direction<0 else 21,direction):
            previous=i-direction
            nxt,st,err=cv2.calcOpticalFlowPyrLK(gray[previous],gray[i],positions[previous],None,
                winSize=(21,21),maxLevel=3,criteria=(cv2.TERM_CRITERIA_COUNT|cv2.TERM_CRITERIA_EPS,50,.005))
            back,bs,_=cv2.calcOpticalFlowPyrLK(gray[i],gray[previous],nxt,None,winSize=(21,21),maxLevel=3)
            positions[i]=nxt
            visibility[i]=visibility[previous]&st.ravel().astype(bool)&bs.ravel().astype(bool)&(
                np.linalg.norm(back[:,0]-positions[previous][:,0],axis=1)<.7)&(err.ravel()<18)
    for i in range(21):
        target=positions[i];valid=visibility[i]
        qp=q[valid].astype(np.float32);pix=target[valid,0]/3+[0,(840-200)*224/1080]
        lab=labels[valid];ids=np.flatnonzero(valid);train=ids%4!=0
        print('tracked frame',i,'total',len(qp),'floor',int((lab==2).sum()),'shelf',int((lab==3).sum()),flush=True)
        if train.sum()<8:
            previous=json.loads((folder/'cameras.json').read_text())['cameras'][i]
            previous['rigid_checks']={name:{'count':0,'heldout_reprojection_px':None} for name in ['floor','shelf']}
            previous['not_observed_by_tracks']=True;records.append(previous)
            continue
        ok,rv,tv,inliers=cv2.solvePnPRansac(qp[train],pix[train].astype(np.float32),k,None,
            iterationsCount=3000,reprojectionError=2.,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
        if not ok or inliers is None or len(inliers)<8:raise RuntimeError(f'Rigid tracks PnP failed: frame {i}')
        inside=inliers.ravel();rv,tv=cv2.solvePnPRefineLM(qp[train][inside],pix[train][inside],k,None,rv,tv)
        r=cv2.Rodrigues(rv)[0];center=-r.T@tv.ravel()
        pr=cv2.projectPoints(qp,rv,tv,k,None)[0].reshape(-1,2);err=np.linalg.norm(pr-pix,axis=1)
        checks={}
        for value,name in [(2,'floor'),(3,'shelf')]:
            sel=(lab==value)&~train
            checks[name]={'count':int(sel.sum()),'heldout_reprojection_px':np.quantile(err[sel],[.5,.9]).tolist() if sel.any() else None}
        records.append(dict(frame=i,K=k.tolist(),rotation=r.tolist(),center=center.tolist(),rigid_checks=checks))
        data.extend([[i,*pt,*uv,int(label),bool(t)] for pt,uv,label,t in zip(qp,pix,lab,train)])
    np.save(folder/'tracked_correspondences.npy',np.array(data))
    (folder/'tracked_cameras.json').write_text(json.dumps(dict(method='same-rigid-landmark tracking',cameras=records),indent=2),encoding='utf-8')
    print(json.dumps(dict(landmarks=len(q),floor=int((labels==2).sum()),shelf=int((labels==3).sum()),checks=[c['rigid_checks'] for c in records])),flush=True)


def residualaudit():
    from audit_screen_gaps import baseline
    from scipy.spatial import cKDTree
    from audit_scan46_window_camera import camera
    folder=OUT/'rigid_pose';maps=np.load(OUT/'slam3r/r2/preds/registered_pcds.npy')
    rg=json.loads((OUT/'registration.json').read_text())
    bp,bc,_,_=baseline();tree=cKDTree(bp);records=[]
    for i in range(21):
        world=rg['scale']*(maps[i]@np.array(rg['rotation']).T)+rg['translation']
        k,r,c,rms=camera(world);mask=np.load(folder/f'mask_{i:02d}.npy')
        regions={}
        for value,name in [(1,'radiator'),(2,'floor'),(3,'shelf'),(5,'window_frame_sill')]:
            selected=mask==value
            if not selected.any():
                regions[name]={'visible_in_target_crop':False,'points':0};continue
            q=world[selected];d,ix=tree.query(q);reference=bp[ix]
            direction=q-c;direction/=np.linalg.norm(direction,axis=1)[:,None]
            radial=np.sum((reference-q)*direction,axis=1)
            def project(p):
                h=((p-c)@r.T)@k.T;return h[:,:2]/h[:,2:]
            reproj=np.linalg.norm(project(reference)-project(q),axis=1)
            regions[name]={'visible_in_target_crop':True,'points':len(q),
                'distance_median_p90_m':np.quantile(d,[.5,.9]).tolist(),
                'signed_depth_p10_median_p90_m':np.quantile(radial,[.1,.5,.9]).tolist(),
                'nearest_surface_reprojection_median_p90_px':np.quantile(reproj,[.5,.9]).tolist(),
                'median_world_residual_xyz_m':np.median(reference-q,axis=0).tolist()}
        records.append({'frame':i,'time_seconds':2+i/4,'pointmap_camera_rms_px':rms,'regions':regions})
    summary={}
    for name in ['floor','radiator','shelf']:
        values=[r['regions'][name]['signed_depth_p10_median_p90_m'][1] for r in records if r['regions'][name]['points']]
        summary[name]={'frame_median_depth_mean_m':float(np.mean(values)),'frame_median_depth_std_m':float(np.std(values))}
    report={'warning':'Nearest-surface distances are diagnostics, not ground-truth 2D/3D feature correspondence errors.',
            'per_frame':records,'temporal_summary':summary}
    (folder/'all_frame_residuals.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'reconstruct', 'align', 'patch', 'refine', 'bias', 'poseaudit', 'rigidcameras', 'bundle', 'depthopt', 'trackrigid', 'bundle-tracked', 'depthopt-tracked', 'residualaudit'])
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE, help='R2连续补拍视频路径')
    args = parser.parse_args()
    stages = {'prepare': lambda: prepare(args.source), 'reconstruct': reconstruct, 'align': align, 'patch': patch, 'refine': refine, 'bias': bias, 'poseaudit': poseaudit, 'rigidcameras': rigidcameras, 'bundle': bundle, 'depthopt': depthopt, 'trackrigid': trackrigid, 'bundle-tracked': lambda: bundle(True), 'depthopt-tracked': lambda: depthopt(True), 'residualaudit': residualaudit}
    stages[args.stage]()
