"""Read-only localization against rendered frozen map IDs, not learned-map NN."""
import argparse
import json
import hashlib
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageDraw
from local_gap_geometry import render_identity,map_surface_point
from reconstruct_target_r2 import OUT,SOURCE,ROOT
from audit_screen_gaps import baseline as load_baseline

DEST=OUT/'map_localization'
KEYS=[0,2,6,10,12,16,20]


def baseline():
    path=ROOT/'data/work/46/postprocess/local_gaps_20260912.ply'
    lock=json.loads((OUT.parent/'new2/baseline_lock.json').read_text())
    with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=lock[path.name]:raise RuntimeError('Frozen baseline changed; cached point IDs cannot be reused')
    return load_baseline()


def select_map_matches(rows,points):
    groups={}
    for row in rows:
        key=tuple(np.rint(np.array(row['query_xy'])/3).astype(int))
        groups.setdefault(key,[]).append(row)
    chosen=[]
    for group in groups.values():
        xyz=np.array([r.get('map_xyz',points[r['point_id']]) for r in group])
        agree=np.linalg.norm(xyz[:,None]-xyz[None,:],axis=2)<.02
        best=int(np.argmax(agree.sum(1)));support=agree[best]
        refs={r['reference'] for r,good in zip(group,support) if good}
        if len(refs)<2 or support.sum()<len(group)/2:continue
        kept=min([r for r,good in zip(group,support) if good],key=lambda r:r['distance']).copy()
        kept['support_reference_count']=len(refs)
        chosen.append(kept)
    return chosen


def solve(surface=False):
    bp,_,_,_=baseline()
    rows=json.loads((DEST/('surface_correspondences.json' if surface else 'map_correspondences.json')).read_text());audit=[]
    cv2.setRNGSeed(4615)
    for frame in KEYS:
        selected=select_map_matches([r for r in rows if r['frame']==frame],bp)
        report=dict(frame=frame,matches=selected,accepted=False)
        q=np.array([r.get('map_xyz',bp[r['point_id']]) for r in selected],np.float64).reshape(-1,3)
        uv=np.array([r['query_xy'] for r in selected],np.float64).reshape(-1,2)
        train=np.array([r['region']==3 for r in selected],bool);hold=~train
        report.update(training_shelf=int(train.sum()),heldout_floor=int(hold.sum()))
        attempts=[]
        if train.sum()>=8:
            for focal in [1200.,1400.,1600.,1800.,2000.]:
                k=np.array([[focal,0,540],[0,focal,960],[0,0,1.]])
                ok,r,t,ids=cv2.solvePnPRansac(q[train],uv[train],k,None,iterationsCount=5000,
                    reprojectionError=4.,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
                if not ok or ids is None or len(ids)<6:
                    attempts.append(dict(focal=focal,pnp_returned_pose=bool(ok),inliers=0 if ids is None else len(ids),
                                         passed_training_gate=False))
                    continue
                r,t=cv2.solvePnPRefineLM(q[train][ids.ravel()],uv[train][ids.ravel()],k,None,r,t)
                projection=cv2.projectPoints(q,r,t,k,None)[0].reshape(-1,2)
                residual=np.linalg.norm(projection-uv,axis=1)
                attempts.append(dict(focal=focal,inliers=len(ids),passed_training_gate=True,training_p90_px=float(np.quantile(residual[train],.9)),
                    heldout_floor_median_p90_px=np.quantile(residual[hold],[.5,.9]).tolist() if hold.any() else None,
                    K=k.tolist(),rotation=cv2.Rodrigues(r)[0].tolist(),center=(-cv2.Rodrigues(r)[0].T@t.ravel()).tolist()))
        report['pose_trials']=attempts
        report['reason']='No spatially independent map localization certificate; do not use these pose trials for R2.'
        audit.append(report)
        print(frame,'shelf',report['training_shelf'],'floor',report['heldout_floor'],'trials',attempts,flush=True)
    (DEST/('surface_localization_audit.json' if surface else 'localization_audit.json')).write_text(json.dumps(audit,indent=2),encoding='utf-8')


def surface_matches():
    bp,_,_,_=baseline()
    refs={r['name']:r for r in json.loads((DEST/'references.json').read_text())}
    rows=json.loads((DEST/'map_correspondences.json').read_text());kept=[];cache={}
    for row in rows:
        name=row['reference'];view=refs[name]
        if name not in cache:cache[name]=np.load(DEST/f'{name}_dense.npz')['point_ids']
        surface=map_surface_point(row['reference_xy'],cache[name],bp,np.array(view['K']),
                                  np.array(view['rotation']),np.array(view['center']))
        if surface is None:continue
        row=row.copy();row['map_xyz']=surface['xyz'].tolist()
        row['support_baseline_ids']=surface['point_ids'].tolist();row['plane_p90_m']=surface['plane_p90_m']
        kept.append(row)
    (DEST/'surface_correspondences.json').write_text(json.dumps(kept,indent=2),encoding='utf-8')
    print('surface-constrained correspondences',len(kept),'from',len(rows),flush=True)


def manual_review():
    # These are candidate landmarks inspected in the source, not invented 3D pairs.
    candidates=[
        ('floor_cross',[692,1288],'full-RGB tile intersection is visible; no unambiguous same intersection in rendered map'),
        ('floor_vertical_line',[685,1100],'line interior has aperture ambiguity, not a uniquely identified point'),
        ('radiator_left_seam',[58,600],'source seam visible; no identifiable corresponding radiator seam in map views'),
        ('radiator_right_seam',[180,600],'source seam visible; no identifiable corresponding radiator seam in map views'),
        ('shelf_upper_corner',[950,505],'candidate shelf junction; rendered missing/rounded edge prevents exact vertex identity'),
        ('shelf_middle_corner',[978,1005],'candidate shelf junction; repeated levels do not establish vertex identity'),
        ('shelf_lower_edge',[1027,1390],'partial clipped shelf edge, not an independent confirmed corner'),
        ('shelf_upper_inner',[970,155],'candidate upper shelf junction; source and map shared extent insufficient for confirmation')]
    image=np.array(Image.open(DEST/'query_10.png'));rows=[]
    for index,(name,xy,reason) in enumerate(candidates,1):
        cv2.circle(image,tuple(xy),13,(255,180,0),3)
        cv2.putText(image,str(index),(xy[0]-30,xy[1]-18),cv2.FONT_HERSHEY_SIMPLEX,.8,(255,180,0),2)
        rows.append(dict(index=index,source_frame=10,name=name,query_xy=xy,confirmed=False,
                         baseline_point_id=None,reason=reason))
    Image.fromarray(image).save(DEST/'manual_landmark_candidates.png')
    (DEST/'manual_landmark_audit.json').write_text(json.dumps(dict(landmarks=rows,
        note='No candidate with uncertain identity is promoted to a PnP anchor. No new geometry generated.'),indent=2),encoding='utf-8')


def query_regions(frame):
    # Full-resolution masks traced on full_rgb_keys.png; uncertain boundaries excluded.
    floors={0:[(145,265),(242,265),(242,415),(229,466),(126,466),(116,432)],
            2:[(148,270),(241,270),(241,410),(235,468),(128,468),(118,438)],
            6:[(157,276),(248,276),(248,465),(139,468)],
            10:[(155,275),(243,275),(252,470),(140,470)],
            12:[(165,290),(244,290),(254,473),(146,473)],
            16:[(180,312),(248,312),(259,472),(162,472)],
            20:[(188,351),(254,351),(261,474),(174,474)]}
    labels=np.zeros((1920,1080),np.uint8)
    cv2.fillPoly(labels,[np.array(floors[frame],np.int32)*4],2)
    # Trace the visible shelf faces, not just the last six pixels at image edge.
    # These polygons stay to the right of curtains and bags in the full RGB.
    shelf={0:[(254,12),(269,12),(269,320),(258,320),(255,224),(256,150)],
           2:[(256,15),(269,15),(269,320),(261,320),(255,216),(254,149)],
           6:[(258,15),(269,15),(269,468),(267,468),(258,330),(250,244),(255,158)],
           10:[(248,20),(269,20),(269,465),(262,380),(250,285),(248,245),(253,172),(246,126)],
           12:[(255,25),(269,25),(269,465),(264,370),(256,280),(256,210)],
           16:[(262,25),(269,25),(269,465),(264,395),(259,322),(262,222)],
           20:[(263,45),(269,45),(269,469),(265,390),(262,285)]}
    cv2.fillPoly(labels,[np.array(shelf[frame],np.int32)*4],3)
    return labels


def match():
    bp,bc,_,_=baseline()
    refs=json.loads((DEST/'references.json').read_text())
    sift=cv2.SIFT_create(nfeatures=9000,contrastThreshold=.008,edgeThreshold=12)
    query={};counts={};pairs=[];correspondences=[]
    for i in KEYS:
        rgb=np.array(Image.open(DEST/f'query_{i:02d}.png'));labels=query_regions(i)
        k,d=sift.detectAndCompute(cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY),(labels>0).astype(np.uint8)*255)
        query[i]=(k,d,labels)
        canvas=rgb.copy();canvas[labels>0]=(canvas[labels>0]*.5+np.array([0,220,100])*.5).astype(np.uint8)
        for key in k:cv2.circle(canvas,tuple(np.rint(key.pt).astype(int)),6,(255,0,0),1)
        Image.fromarray(canvas).save(DEST/f'query_{i:02d}_rigid.png')
        counts[i]=len(k)
    for v in refs:
        name=v['name']
        if (DEST/f'{name}_dense.npz').exists():
            rgb=np.array(Image.open(DEST/f'{name}_dense.png'))
            ids=np.load(DEST/f'{name}_dense.npz')['point_ids']
        else:
            rgb,depth,ids=render_identity(bp,bc,np.array(v['K']),np.array(v['rotation']),np.array(v['center']),(1280,720),5)
            Image.fromarray(rgb).save(DEST/f'{name}_dense.png')
            np.savez_compressed(DEST/f'{name}_dense.npz',depth=depth.astype(np.float32),point_ids=ids.astype(np.int32))
        visible=ids>=0;xyz=bp[np.maximum(ids,0)]
        region=np.zeros(ids.shape,np.uint8)
        region[visible&(xyz[:,:,1]>1.24)&(xyz[:,:,1]<1.32)&(xyz[:,:,2]>.15)]=2
        region[visible&(xyz[:,:,0]<-.16)&(xyz[:,:,0]>-.3)&(xyz[:,:,1]>.3)&(xyz[:,:,1]<1.24)]=3
        # Leave a border at depth/visibility edges; no descriptor should describe a hole.
        support=cv2.erode((region>0).astype(np.uint8),np.ones((9,9),np.uint8))*255
        kr,dr=sift.detectAndCompute(cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY),support)
        for i,(kq,dq,labels) in query.items():
            if dr is None or dq is None or len(dr)<2 or len(dq)<2:continue
            forward=cv2.BFMatcher().knnMatch(dq,dr,k=2)
            backward=cv2.BFMatcher().match(dr,dq)
            accepted=[]
            for pair in forward:
                if len(pair)!=2:continue
                a,b=pair
                if a.distance>=.72*b.distance or backward[a.trainIdx].trainIdx!=a.queryIdx:continue
                u=np.array(kq[a.queryIdx].pt);vxy=np.array(kr[a.trainIdx].pt)
                x,y=np.rint(vxy).astype(int);qx,qy=np.rint(u).astype(int)
                if region[y,x]!=labels[qy,qx] or ids[y,x]<0:continue
                accepted.append(a)
                correspondences.append(dict(frame=i,reference=name,query_feature=a.queryIdx,query_xy=u.tolist(),
                    reference_xy=vxy.tolist(),point_id=int(ids[y,x]),region=int(region[y,x]),distance=float(a.distance)))
            pairs.append(dict(frame=i,reference=name,reference_features=len(kr),matches=len(accepted)))
            if accepted:
                canvas=cv2.drawMatches(np.array(Image.open(DEST/f'query_{i:02d}.png')),kq,rgb,kr,accepted,None,
                    flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
                Image.fromarray(canvas).save(DEST/f'matches_{i:02d}_{name}.jpg')
        print(name,'rigid_features',len(kr),'matched',sum(p['matches'] for p in pairs if p['reference']==name),flush=True)
    (DEST/'map_correspondences.json').write_text(json.dumps(correspondences,indent=2),encoding='utf-8')
    (DEST/'matching_audit.json').write_text(json.dumps(dict(query_features=counts,pairs=pairs,
        correspondences=len(correspondences),camera_accepted=False),indent=2),encoding='utf-8')
    print('total',len(correspondences),flush=True)


def prepare():
    DEST.mkdir(exist_ok=True)
    capture=cv2.VideoCapture(str(SOURCE))
    sheet=Image.new('RGB',(7*270,504),'#161b22');draw=ImageDraw.Draw(sheet)
    for i in range(21):
        capture.set(cv2.CAP_PROP_POS_MSEC,(2+i/4)*1000);ok,bgr=capture.read()
        if not ok:raise RuntimeError(f'Missing frame {i}')
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
        Image.fromarray(rgb).save(DEST/f'query_{i:02d}.png')
        if i in KEYS:
            x=KEYS.index(i)*270
            sheet.paste(Image.fromarray(rgb).resize((270,480)),(x,24))
            draw.text((x,2),f'frame {i} / {2+i/4:.2f}s',fill='white')
    capture.release();sheet.save(DEST/'full_rgb_keys.png')
    bp,bc,_,_=baseline()
    cameras=json.loads((OUT/'rigid_pose/bundle_cameras.json').read_text())['cameras']
    records=[];sheet=Image.new('RGB',(7*270,504),'#161b22');draw=ImageDraw.Draw(sheet)
    for i in KEYS:
        camera=cameras[i]
        k=np.array(camera['K']);r=np.array(camera['rotation']);c=np.array(camera['center'])
        # Guessed poses only select reference views. They are NOT localization output.
        full=np.array([[1080/224,0,0],[0,1080/224,200],[0,0,1.]])@k
        display=np.diag([2/3,2/3,1.])@full
        for name,offset in [('seed',[0,0,0]),('left',[-.08,0,0]),('right',[.08,0,0]),('near',[0,0,.08]),('far',[0,0,-.08])]:
            center=c+np.array(offset)@r
            rgb,depth,ids=render_identity(bp,bc,display,r,center,(1280,720),2)
            stem=f'ref_{i:02d}_{name}'
            Image.fromarray(rgb).save(DEST/f'{stem}.png')
            np.savez_compressed(DEST/f'{stem}.npz',depth=depth.astype(np.float32),point_ids=ids.astype(np.int32))
            records.append(dict(name=stem,K=display.tolist(),rotation=r.tolist(),center=center.tolist(),seed_frame=i))
            if name=='seed':
                x=KEYS.index(i)*270;sheet.paste(Image.fromarray(rgb).resize((270,480)),(x,24))
                draw.text((x,2),f'map view near {i}',fill='white')
            print(stem,flush=True)
    (DEST/'references.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    sheet.save(DEST/'map_reference_keys.png')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','match','solve','manual-review','surface-matches','solve-surface'])
    args=parser.parse_args();{'prepare':prepare,'match':match,'solve':solve,'manual-review':manual_review,'surface-matches':surface_matches,'solve-surface':lambda:solve(True)}[args.stage]()
