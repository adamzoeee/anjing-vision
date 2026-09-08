"""Cross-video static-object feature registration; exclude glass and curtains."""
import json,cv2
import numpy as np
from PIL import Image
from audit_scan46_observation_planes import ROOT
from fuse_scan46_buchong2_four_regions import robust_similarity

def main():
    dest=ROOT/'data/work/46/window_local_sequence_20260907'
    folder=ROOT/'data/work/46/window_supplement_bridge_20260908'
    am=np.load(dest/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r')
    bm=np.load(folder/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r')
    at=json.loads((dest/'integration_components_extent.json').read_text())['transform']
    bt=json.loads((dest/'window_local_supplement_curtain_components.json').read_text())
    def world(p,t):return t['scale']*(np.asarray(p,float)@np.asarray(t['rotation']).T)+t['translation']
    def photo(path):
        im=Image.open(path);w,h=im.size;s=min(w,h)
        return np.array(im.crop(((w-s)//2,(h-s)//2,(w+s)//2,(h+s)//2)).resize((672,672)))
    def features(im,rects):
        mask=np.zeros((672,672),np.uint8)
        for a,b,c,d in rects:mask[a*3:b*3,c*3:d*3]=255
        return cv2.SIFT_create(nfeatures=3000).detectAndCompute(cv2.cvtColor(im,cv2.COLOR_RGB2GRAY),mask)
    src=[];dst=[];rows=[];pairs=[]
    main_rects={150:[(174,205,48,83),(182,220,97,130),(205,223,33,144),(25,40,42,65)],
        155:[(135,176,68,105),(153,192,115,150),(178,197,45,160)],
        160:[(90,127,72,113),(105,145,120,155),(130,145,60,160)]}
    for i,rects in main_rects.items():
        ia=photo(ROOT/f'data/work/45/frames/frame_{i+1:05d}.jpg');ka,da=features(ia,rects)
        for j in [27,29,31,34]:
            ib=photo(folder/f'frames/frame_{j+1:05d}.jpg')
            kb,db=features(ib,[(168,223,30,145),(0,25,25,80)])
            matches=cv2.BFMatcher().knnMatch(da,db,k=2)
            matches=[m for m,n in matches if m.distance<.72*n.distance]
            reverse=cv2.BFMatcher().knnMatch(db,da,k=2)
            reciprocal={(m.queryIdx,m.trainIdx) for m,n in reverse if m.distance<.72*n.distance}
            matches=[m for m in matches if (m.trainIdx,m.queryIdx) in reciprocal]
            if len(matches)<5:continue
            ua=np.float32([ka[m.queryIdx].pt for m in matches])/3
            ub=np.float32([kb[m.trainIdx].pt for m in matches])/3
            _,inliers=cv2.findHomography(ua,ub,cv2.RANSAC,2.)
            if inliers is None:continue
            good=inliers.ravel()>0;ua,ub=ua[good],ub[good]
            a=cv2.remap(np.asarray(am[i-125]),ua[:,0,None],ua[:,1,None],cv2.INTER_LINEAR).reshape(-1,3)
            b=cv2.remap(np.asarray(bm[j]),ub[:,0,None],ub[:,1,None],cv2.INTER_LINEAR).reshape(-1,3)
            a,b=world(a,at),world(b,bt)
            good=(a[:,2]>-.65)&(a[:,2]<.2)&(b[:,2]>-.65)&(b[:,2]<.2)
            if i==150 and j==31:
                canvas=np.concatenate([ia,ib],axis=1).copy()
                for n,(u,v,pa,pb,ok) in enumerate(zip(ua,ub,a,b,good)):
                    if not ok:continue
                    color=(0,255,0) if np.linalg.norm(pa-pb)<.08 else (255,0,0)
                    x1,y1=np.rint(u*3).astype(int);x2,y2=np.rint(v*3).astype(int);x2+=672
                    cv2.line(canvas,(x1,y1),(x2,y2),color,1)
                    cv2.putText(canvas,str(n),(x1,y1),cv2.FONT_HERSHEY_SIMPLEX,.5,color,1)
                Image.fromarray(canvas).save(dest/'bridge_static_matches_150_31.png')
            pairs.extend([[i,j]]*int(good.sum()))
            src.extend(b[good]);dst.extend(a[good]);rows.append([i,j,int(good.sum())])
    p,q=np.asarray(src),np.asarray(dst);print('pairs',rows,'total',len(p),flush=True)
    np.savez(dest/'bridge_static_correspondences.npz',source=p,target=q,pairs=pairs)
    if len(p)<15:raise RuntimeError('Insufficient static feature anchors')
    train=np.arange(len(p))%3!=0;s,r,t,res=robust_similarity(p[train],q[train])
    err=np.linalg.norm(s*(p@r.T)+t-q,axis=1)
    report={'pairs':rows,'samples':len(p),'scale':s,'rotation':r.tolist(),'translation':t.tolist(),
        'heldout_m':np.quantile(err[~train],[.5,.9,.95]).tolist()}
    (dest/'bridge_static_feature_alignment.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
