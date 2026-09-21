"""Original RGB / registered-map localization audit. Never writes formal products."""
import json
import argparse
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageOps,ImageDraw
from localize_r2_map import OUT,DEST as QUERY,KEYS,query_regions
from audit_scan46_observation_planes import load_inputs,ROOT
DEST=OUT/'raw_visual_map'

def consistent_identity(rows):
    if len({r['old_frame'] for r in rows})<2:return None
    xyz=np.array([r['xyz'] for r in rows]);d=np.linalg.norm(xyz[:,None]-xyz[None,:],axis=-1)
    # Require the whole identity, not a convenient small cluster of repeated texture.
    if d.max()>.02:return None
    best=min(rows,key=lambda r:r['distance']).copy()
    best['support_frames']=sorted({r['old_frame'] for r in rows})
    best['spread_m']=float(d.max())
    return best

def sample_identity(points,conf,xy):
    x,y=xy;u,v=int(np.floor(x)),int(np.floor(y))
    if u<1 or v<1 or u+2>=points.shape[1] or v+2>=points.shape[0]:return None
    patch=points[v-1:v+3,u-1:u+3];quality=conf[v:v+2,u:u+2]
    if not np.isfinite(patch).all() or not np.isfinite(quality).all() or quality.min()<12:return None
    if max(np.linalg.norm(np.diff(patch,axis=0),axis=-1).max(),np.linalg.norm(np.diff(patch,axis=1),axis=-1).max())>.03:return None
    a,b=x-u,y-v
    return (1-b)*((1-a)*points[v,u]+a*points[v,u+1])+b*((1-a)*points[v+1,u]+a*points[v+1,u+1])

def original(frame):
    im=ImageOps.exif_transpose(Image.open(ROOT/f'data/work/45/frames/frame_{frame+1:05d}.jpg')).convert('RGB')
    w,h=im.size;long=round(224*max(w/h,h/w));nw,nh=round(w*long/max(w,h)),round(h*long/max(w,h))
    resize=im.resize((nw,nh),Image.Resampling.LANCZOS if max(w,h)>long else Image.Resampling.BICUBIC)
    cx,cy=nw//2,nh//2;half=min(cx,cy);box=(cx-half,cy-half,cx+half,cy+half)
    return np.array(resize.crop(box)),im,(nw/w,nh/h,box[0],box[1])

def run():
    cv2.setNumThreads(4);DEST.mkdir(parents=True,exist_ok=True)
    maps,imgs,rotation,shift=load_inputs()
    conf=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
    T=np.array(json.loads((ROOT/'data/work/46/coordinate_repair_20260905/registration_contract.json').read_text())['transform'])
    sift=cv2.SIFT_create(nfeatures=5000,contrastThreshold=.008,edgeThreshold=12)
    matcher=cv2.BFMatcher();queries={};ranks={i:[] for i in KEYS};audit=[]
    for i in KEYS:
        im=np.array(Image.open(QUERY/f'query_{i:02d}.png'));mask=query_regions(i)
        kp,d=sift.detectAndCompute(cv2.cvtColor(im,cv2.COLOR_RGB2GRAY),(mask>0).astype('uint8')*255)
        queries[i]=(kp,d,mask)
    for j in range(len(imgs)):
        cached=np.rint(imgs[j]).clip(0,255).astype('uint8')
        kp,d=sift.detectAndCompute(cv2.cvtColor(cv2.resize(cached,(672,672)),cv2.COLOR_RGB2GRAY),None)
        if d is None or len(d)<2:continue
        for i,(q,qd,_) in queries.items():
            pairs=matcher.knnMatch(qd,d,k=2)
            good=[a for a,b in pairs if a.distance<.72*b.distance]
            ranks[i].append((len(good),j))
        if j%100==0:print('retrieved',j,flush=True)
    (DEST/'retrieval.json').write_text(json.dumps({i:sorted(r,reverse=True)[:20] for i,r in ranks.items()},indent=2))
    selected=sorted({j for r in ranks.values() for _,j in sorted(r,reverse=True)[:20]})
    refs={}
    for j in selected:
        cached,im,affine=original(j);error=float(np.abs(cached.astype(float)-imgs[j]).max())
        audit.append(dict(frame=j,preprocess_max_error=error,affine=affine))
        if error>1.01:raise RuntimeError(f'RGB/map preprocessing mismatch {j}: {error}')
        sx,sy,ox,oy=affine
        # Match high-resolution original RGB, but restrict to observed map extent.
        gray=cv2.cvtColor(np.array(im),cv2.COLOR_RGB2GRAY);valid=np.zeros_like(gray)
        valid[int(oy/sy)+4:int((oy+224)/sy)-4,int(ox/sx)+4:int((ox+224)/sx)-4]=255
        k,d=sift.detectAndCompute(gray,valid);refs[j]=(k,d,affine)
    (DEST/'pixel_contract.json').write_text(json.dumps(audit,indent=2))
    rows=[]
    for i,(q,qd,mask) in queries.items():
        for _,j in sorted(ranks[i],reverse=True)[:20]:
            k,d,(sx,sy,ox,oy)=refs[j]
            reverse={a.queryIdx:a.trainIdx for a,b in matcher.knnMatch(d,qd,k=2) if a.distance<.72*b.distance}
            metric=(np.asarray(maps[j],float)@rotation.T+shift)@T[:3,:3].T+T[:3,3]
            for a,b in matcher.knnMatch(qd,d,k=2):
                if a.distance>=.72*b.distance or reverse.get(a.trainIdx)!=a.queryIdx:continue
                uv=np.array(q[a.queryIdx].pt);old=np.array(k[a.trainIdx].pt)
                pixel=(old+.5)*[sx,sy]-.5-[ox,oy]
                xyz=sample_identity(metric,conf[j],pixel)
                if xyz is None:continue
                rows.append(dict(frame=i,query_id=a.queryIdx,query_xy=uv.tolist(),old_frame=j,old_xy=old.tolist(),map_xy=pixel.tolist(),xyz=xyz.tolist(),region=int(mask[int(uv[1]),int(uv[0])]),distance=float(a.distance)))
    accepted=[]
    for i in KEYS:
        group={}
        for r in rows:
            if r['frame']==i:group.setdefault(r['query_id'],[]).append(r)
        accepted.extend(c for g in group.values() if (c:=consistent_identity(g)) is not None)
    (DEST/'matches.json').write_text(json.dumps(rows,indent=2))
    (DEST/'identities.json').write_text(json.dumps(accepted,indent=2))
    print('raw',len(rows),'consistent',len(accepted),flush=True)
    for i in KEYS:print(i,{g:sum(r['frame']==i and r['region']==g for r in accepted) for g in [2,3]},flush=True)

def learned(audit_only=False):
    import sys
    sys.path[:0]=['D:/部署文件/output/LightGlue-r2-localization','D:/部署文件/output/r2-localization-deps']
    import torch
    from lightglue import ALIKED,LightGlue
    torch.set_grad_enabled(False);torch.set_num_threads(4)
    # This host's torchvision deform_conv2d is CPU-only; keep application packages intact.
    extractor=ALIKED(max_num_keypoints=8192).eval()
    matcher=LightGlue(features='aliked',depth_confidence=-1,width_confidence=-1).eval().cuda()
    maps,imgs,rotation,shift=load_inputs();conf=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
    T=np.array(json.loads((ROOT/'data/work/46/coordinate_repair_20260905/registration_contract.json').read_text())['transform'])
    def features(im):
        result=extractor.extract(torch.from_numpy(np.array(im).copy()).permute(2,0,1).float()/255,resize=1536)
        return {key:value.cuda() if isinstance(value,torch.Tensor) else value for key,value in result.items()}
    queries={}
    for i in ([0,10,20] if audit_only else KEYS):
        f=features(Image.open(QUERY/f'query_{i:02d}.png'));xy=f['keypoints'][0].cpu().numpy();mask=query_regions(i)
        # Keep image context for matching; soft points are removed before any 3D lookup.
        # Cropping descriptors to a narrow shelf destroys LightGlue's context.
        queries[i]=f
    rows=[];counts=[];contracts=[];before_depth=[]
    def match_frame(j):
        cached,im,(sx,sy,ox,oy)=original(j)
        error=float(np.abs(cached.astype(float)-imgs[j]).max())
        if error>1.01:raise RuntimeError('Original RGB contract broken')
        contracts.append(dict(frame=j,max_error=error));f=features(im)
        oldxy=f['keypoints'][0].cpu().numpy();pixel=(oldxy+.5)*[sx,sy]-.5-[ox,oy]
        metric=(np.asarray(maps[j],float)@rotation.T+shift)@T[:3,:3].T+T[:3,3]
        total=0
        for i,q in queries.items():
            result=matcher(dict(image0=q,image1=f));pairs=result['matches'][0].cpu().numpy();scores=result['scores'][0].cpu().numpy()
            qxy=q['keypoints'][0].cpu().numpy();labels=query_regions(i);count=0
            for (a,b),score in zip(pairs,scores):
                if score<.2:continue
                uv=qxy[a]
                if labels[int(uv[1]),int(uv[0])]==0:continue
                xyz=sample_identity(metric,conf[j],pixel[b])
                before_depth.append(dict(frame=i,query_xy=uv.tolist(),old_frame=j,old_xy=oldxy[b].tolist(),map_xy=pixel[b].tolist(),score=float(score),passed_depth=xyz is not None))
                if xyz is None:continue
                rows.append(dict(frame=i,query_id=int(a),query_xy=uv.tolist(),old_frame=j,old_xy=oldxy[b].tolist(),map_xy=pixel[b].tolist(),xyz=xyz.tolist(),region=int(labels[int(uv[1]),int(uv[0])]),distance=float(1-score)))
                count+=1
            counts.append(dict(query=i,old=j,count=count));total+=count
        return total
    sampled=([110,115,120,125,710,720,730,740,750,760,780] if audit_only else list(range(0,900,10)));totals=[]
    for j in sampled:
        totals.append((match_frame(j),j))
        if j%100==0:print('learned scan',j,totals[-1],flush=True)
    neighbors=[] if audit_only else sorted({x for count,j in sorted(totals,reverse=True)[:15] if count>0 for x in range(max(0,j-4),min(900,j+5))}-set(sampled))
    for n,j in enumerate(neighbors):
        match_frame(j)
        if n%20==0:print('learned neighbors',n,len(neighbors),flush=True)
    accepted=[]
    for i in KEYS:
        groups={}
        for r in rows:
            if r['frame']==i:groups.setdefault(r['query_id'],[]).append(r)
        accepted.extend(c for g in groups.values() if (c:=consistent_identity(g)) is not None)
    for name,value in [('context_matches',rows),('context_identities',accepted),('context_counts',counts),('context_contracts',contracts)]:
        (DEST/f'{"pair_audit_" if audit_only else ""}{name}.json').write_text(json.dumps(value,indent=2))
    (DEST/f'{"pair_audit_" if audit_only else ""}before_depth.json').write_text(json.dumps(before_depth,indent=2))
    print('learned raw',len(rows),'consistent',len(accepted),flush=True)
    for i in KEYS:print(i,{g:sum(r['frame']==i and r['region']==g for r in accepted) for g in [2,3]},flush=True)

def review():
    rows=json.loads((DEST/'context_identities.json').read_text());results=[]
    sheet=Image.new('RGB',(800,max(1,len(rows))*170),'white');draw=ImageDraw.Draw(sheet)
    for n,row in enumerate(rows):
        q=Image.open(QUERY/f"query_{row['frame']:02d}.png");_,old,_=original(row['old_frame'])
        for col,im,xy in [(0,q,row['query_xy']),(1,old,row['old_xy'])]:
            x,y=xy;crop=im.crop((round(x)-100,round(y)-70,round(x)+100,round(y)+70))
            mark=ImageDraw.Draw(crop);mark.ellipse((96,66,104,74),outline='red',width=2)
            sheet.paste(crop,(col*220,n*170+25))
        draw.text((0,n*170+4),f"{n}: query {row['frame']}, old {row['old_frame']}, region {row['region']}, spread {row['spread_m']:.4f}m",fill='black')
    sheet.save(DEST/'identity_review.png')
    for i in KEYS:
        selected=[r for r in rows if r['frame']==i]
        shelf=[r for r in selected if r['region']==3];floor=[r for r in selected if r['region']==2]
        results.append(dict(frame=i,shelf=len(shelf),floor=len(floor),camera_accepted=False,
            reason='Requires verified old-image rigid identity, >=8 training and >=3 spatial holdout points; no pose fallback.'))
    (DEST/'camera_gate.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results,indent=2))

def track_original():
    """Verify actual source identities in consecutive old RGB, not XYZ neighbours."""
    rows=json.loads((DEST/'pair_audit_context_matches.json').read_text())
    labels=np.zeros((1080,1920),np.uint8)
    cv2.fillPoly(labels,[np.array([(1300,15),(1480,15),(1480,750),(1350,750),(1250,520),(1250,310)])],3)
    cv2.fillPoly(labels,[np.array([(895,535),(1180,535),(1290,1050),(785,1050),(850,750)])],2)
    rows=[r for r in rows if r['old_frame']==120 and labels[round(r['old_xy'][1]),round(r['old_xy'][0])]==r['region']]
    maps,imgs,rotation,shift=load_inputs();conf=np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy',mmap_mode='r')
    T=np.array(json.loads((ROOT/'data/work/46/coordinate_repair_20260905/registration_contract.json').read_text())['transform'])
    _,im,_=original(120);gray=cv2.cvtColor(np.array(im),cv2.COLOR_RGB2GRAY)
    xy=np.array([r['old_xy'] for r in rows],np.float32).reshape(-1,1,2);tracks=[[r] for r in rows]
    for j in [118,119,121,122]:
        _,im,(sx,sy,ox,oy)=original(j);other=cv2.cvtColor(np.array(im),cv2.COLOR_RGB2GRAY)
        dest,ok,_=cv2.calcOpticalFlowPyrLK(gray,other,xy,None,winSize=(25,25),maxLevel=4)
        back,ok2,_=cv2.calcOpticalFlowPyrLK(other,gray,dest,None,winSize=(25,25),maxLevel=4)
        valid=ok.ravel().astype(bool)&ok2.ravel().astype(bool)&(np.linalg.norm(back-xy,axis=2).ravel()<.5)
        metric=(np.asarray(maps[j],float)@rotation.T+shift)@T[:3,:3].T+T[:3,3]
        for n in np.flatnonzero(valid):
            point=(dest[n,0]+.5)*[sx,sy]-.5-[ox,oy];xyz=sample_identity(metric,conf[j],point)
            if xyz is None:continue
            r=rows[n].copy();r.update(old_frame=j,xyz=xyz.tolist(),old_xy=dest[n,0].tolist());tracks[n].append(r)
    accepted=[];audits=[]
    for row,group in zip(rows,tracks):
        identity=consistent_identity(group)
        if identity is not None and len(identity['support_frames'])>=3:accepted.append(identity)
        xyz=np.array([r['xyz'] for r in group]);spread=float(np.linalg.norm(xyz[:,None]-xyz[None,:],axis=-1).max())
        audits.append(dict(frame=row['frame'],query_xy=row['query_xy'],old_xy=row['old_xy'],region=row['region'],observations=len(group),spread_m=spread,passed=identity is not None and len(group)>=3))
    (DEST/'tracked_identities.json').write_text(json.dumps(accepted,indent=2))
    (DEST/'tracked_audit.json').write_text(json.dumps(audits,indent=2))
    print('source rigid',len(rows),'three-frame consistent',len(accepted),flush=True)
    for i in [0,10,20]:print(i,{g:sum(r['frame']==i and r['region']==g for r in accepted) for g in [2,3]},flush=True)

def minimal_pose_check():
    """Diagnostic only: four reviewed book marks, independent floor LINE check.

    Floor seam interiors have no along-line pixel identity. Never treat these
    as three independent holdout point correspondences.
    """
    rows=json.loads((DEST/'context_identities.json').read_text())
    good=[r for r in rows if r['frame']==10 and r['query_id'] in [447,792,1449,1670]]
    floor=[r for r in rows if r['frame']==10 and r['region']==2]
    xyz=np.array([r['xyz'] for r in good],float);uv=np.array([r['query_xy'] for r in good],float)
    fxyz=np.array([r['xyz'] for r in floor],float);fuv=np.array([r['query_xy'] for r in floor],float)
    _,_,vt=np.linalg.svd(np.c_[fuv,np.ones(len(fuv))]);line=vt[-1];line/=np.linalg.norm(line[:2])
    trials=[]
    for focal in [1200.,1400.,1600.,1800.,2000.]:
        k=np.array([[focal,0,540],[0,focal,960],[0,0,1.]])
        result=cv2.solvePnPGeneric(xyz,uv,k,None,flags=cv2.SOLVEPNP_AP3P)
        for r,t in zip(result[1],result[2]):
            r,t=cv2.solvePnPRefineLM(xyz,uv,k,None,r,t)
            projected=cv2.projectPoints(xyz,r,t,k,None)[0].reshape(-1,2)
            held=cv2.projectPoints(fxyz,r,t,k,None)[0].reshape(-1,2)
            trials.append(dict(focal=focal,training_errors_px=np.linalg.norm(projected-uv,axis=1).tolist(),
                floor_line_errors_px=np.abs(np.c_[held,np.ones(len(held))]@line).tolist(),
                all_in_front=bool(((xyz@cv2.Rodrigues(r)[0].T+t.ravel())[:,2]>0).all()),
                center=(-cv2.Rodrigues(r)[0].T@t.ravel()).tolist(),camera_accepted=False))
    record=dict(training_query_ids=[r['query_id'] for r in good],
        rejected_query_id_160='New black item matched an old green book: wrong physical identity.',
        floor_evidence='One repeated straight seam, not three independent point identities.',
        trials=trials,accepted=False)
    (DEST/'minimal_pose_diagnostic.json').write_text(json.dumps(record,indent=2))
    print(json.dumps(record,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--learned',action='store_true');parser.add_argument('--audit-pairs',action='store_true');parser.add_argument('--review',action='store_true');parser.add_argument('--track-original',action='store_true');parser.add_argument('--minimal-pose-check',action='store_true');args=parser.parse_args()
    if args.minimal_pose_check:minimal_pose_check()
    elif args.track_original:track_original()
    elif args.review:review()
    elif args.learned or args.audit_pairs:learned(args.audit_pairs)
    else:run()
