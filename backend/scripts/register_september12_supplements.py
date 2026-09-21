"""Audit static matches for new clips against actual accepted geometry."""
import argparse,json
from pathlib import Path
import cv2,numpy as np,open3d as o
from PIL import Image,ImageDraw
from scipy.spatial import cKDTree
from audit_scan46_observation_planes import load_inputs,ROOT,POST,OUT
from fuse_anchored_supplements import fit_similarity

DEST=ROOT/'data/work/46/local_gaps_20260912'
sift=cv2.SIFT_create(nfeatures=3000)
def photo(path):
    im=Image.open(path);w,h=im.size;s=min(w,h)
    return np.array(im.crop(((w-s)//2,(h-s)//2,(w+s)//2,(h+s)//2)).resize((672,672)))
def feats(im):return sift.detectAndCompute(cv2.cvtColor(np.uint8(im),cv2.COLOR_RGB2GRAY),None)
def matches(a,b,ratio=.7,minimum=8):
    if a[1] is None or b[1] is None:return [],[]
    ms=cv2.BFMatcher().knnMatch(a[1],b[1],k=2)
    ms=[m for pair in ms if len(pair)==2 for m,n in [pair] if m.distance<ratio*n.distance]
    if len(ms)<minimum:return [],[]
    x=np.float32([a[0][m.queryIdx].pt for m in ms]);y=np.float32([b[0][m.trainIdx].pt for m in ms])
    _,mask=cv2.findHomography(x,y,cv2.RANSAC,3.)
    if mask is None:return [],[]
    return x[mask.ravel()>0],y[mask.ravel()>0]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('clip');args=parser.parse_args()
    def source_feats(im):
        if args.clip!='white_shelf_side':return feats(im)
        mask=np.zeros(im.shape[:2],np.uint8);mask[:,int(im.shape[1]*.55):]=255
        return sift.detectAndCompute(cv2.cvtColor(np.uint8(im),cv2.COLOR_RGB2GRAY),mask)
    folder=DEST/args.clip;pred=folder/'slam3r/scene/preds'
    new=np.load(pred/'registered_pcds.npy',mmap_mode='r');imgs=np.load(pred/'input_imgs.npy',mmap_mode='r')
    maps,images,R,t=load_inputs();T=np.array(json.loads((OUT/'registration_contract.json').read_text())['transform'])
    cloud=o.io.read_point_cloud(str(POST/'window_components_reveal_review.ply'));bp=np.array(cloud.points);bc=np.array(cloud.colors);tree=cKDTree(bp)
    refs=list(range(0,len(imgs),4 if args.clip=='shelf' else 10))
    nf={i:source_feats(np.asarray(imgs[i])) for i in refs};ranks=[]
    for j in range(0,len(images),8):
        f=feats(np.asarray(images[j]))
        for i in refs:
            x,y=matches(nf[i],f)
            if len(x)>=8:ranks.append((len(x),i,j))
    if args.clip in ('window','white_shelf_side') and not ranks:
        high={i:source_feats(photo(folder/f'frames/frame_{i+1:05d}.jpg')) for i in refs}
        search=(list(range(75,125,3))+list(range(710,800,3))) if args.clip=='white_shelf_side' else list(range(135,171,5))+list(range(680,706,5))+list(range(870,881,5))
        for j in search:
            f=feats(photo(ROOT/f'data/work/45/frames/frame_{j+1:05d}.jpg'))
            for i in refs:
                x,y=matches(high[i],f)
                if len(x)>=8:ranks.append((len(x),i,j))
    ranks=sorted(ranks,reverse=True)[:24];print('retrieval',ranks,flush=True)
    if args.clip=='window':
        # Mask out exterior scenery: static interior objects must drive alignment.
        ranks=[(0,i,j) for i in [32,36,40,44,48,52] for j in [150,155,160]]
    src=[];dst=[];pairs=[];reports=[]
    for _,i,j in ranks:
        a=photo(folder/f'frames/frame_{i+1:05d}.jpg');b=photo(ROOT/f'data/work/45/frames/frame_{j+1:05d}.jpg')
        if args.clip=='window':
            def interior(im,bottom):
                mask=np.zeros(im.shape[:2],np.uint8);mask[bottom:]=255
                return sift.detectAndCompute(cv2.cvtColor(im,cv2.COLOR_RGB2GRAY),mask)
            x,y=matches(interior(a,200),interior(b,470 if j==150 else 360 if j==155 else 260),.8,4)
        else:x,y=matches(source_feats(a),feats(b))
        if len(x)<(4 if args.clip=='window' else 8):continue
        x,y=x/3,y/3
        p=cv2.remap(np.asarray(new[i]),x[:,0,None],x[:,1,None],cv2.INTER_LINEAR).reshape(-1,3)
        q=cv2.remap(np.asarray(maps[j]),y[:,0,None],y[:,1,None],cv2.INTER_LINEAR).reshape(-1,3)
        if args.clip=='window':
            primary=ROOT/'data/work/46/window_local_sequence_20260907'
            pm=np.load(primary/'slam3r/window/preds/registered_pcds.npy',mmap_mode='r')
            pt=json.loads((primary/'integration_components_extent.json').read_text())['transform']
            q=cv2.remap(np.asarray(pm[j-125]),y[:,0,None],y[:,1,None],cv2.INTER_LINEAR).reshape(-1,3)
            q=pt['scale']*(q@np.array(pt['rotation']).T)+pt['translation']
        else:q=(q@R.T+t)@T[:3,:3].T+T[:3,3]
        d,ix=tree.query(q);rgb=cv2.remap(np.asarray(images[j]),y[:,0,None],y[:,1,None],cv2.INTER_LINEAR).reshape(-1,3)/255
        good=np.isfinite(p).all(1)&(d<.035)&(np.max(abs(rgb-bc[ix]),axis=1)<.2)
        src.extend(p[good]);dst.extend(bp[ix[good]]);pairs.extend([[i,j]]*int(good.sum()))
        reports.append([i,j,len(x),int(good.sum())])
        if len(reports)<=6:
            canvas=np.concatenate([a,b],axis=1)
            for xx,yy in zip(x[good]*3,y[good]*3):cv2.line(canvas,tuple(xx.astype(int)),tuple((yy+[672,0]).astype(int)),(0,255,0),1)
            Image.fromarray(canvas).save(folder/f'matches_{i}_{j}.png')
    p,q=np.array(src),np.array(dst);np.savez(folder/'correspondences.npz',source=p,target=q,pairs=pairs)
    if len(p)<15:raise RuntimeError(f'Insufficient static correspondence: {reports}')
    rng=np.random.default_rng(4612);best=np.zeros(len(p),bool)
    for _ in range(2000):
        ids=rng.choice(len(p),4,replace=False)
        s,r,t=fit_similarity(p[ids],q[ids]);res=np.linalg.norm(s*(p@r.T)+t-q,axis=1)
        good=res<.04
        if good.sum()>best.sum():best=good
    s,r,t=fit_similarity(p[best],q[best]);res=np.linalg.norm(s*(p@r.T)+t-q,axis=1)
    report={'pairs':reports,'anchors':len(p),'inliers':int(best.sum()),'inlier_fraction':float(best.mean()),'residual_inlier_m':np.quantile(res[best],[.5,.9,.95]).tolist(),'scale':s,'rotation':r.tolist(),'translation':t.tolist(),'inlier_pairs':np.unique(np.array(pairs)[best],axis=0).tolist()}
    (folder/'registration.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

if __name__=='__main__':main()
