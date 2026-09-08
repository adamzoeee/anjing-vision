"""Estimate image-space camera drift from opaque same-feature correspondences."""
import cv2
import numpy as np
from audit_scan46_window_camera import camera

def correct_intrinsics(images,world_ref,world,plane,frame):
    K,r,center,error=camera(world)
    if frame==145:return K,r,center,error,{'matches':0,'shift':[0.,0.]}
    k0,r0,c0,e0=camera(world_ref)
    gray=cv2.cvtColor(np.asarray(images[145],np.uint8),cv2.COLOR_RGB2GRAY)
    other=cv2.cvtColor(np.asarray(images[frame],np.uint8),cv2.COLOR_RGB2GRAY)
    mask=np.zeros((224,224),np.uint8)
    for a,b,c,d in [(30,48,19,133),(35,213,21,34),(32,219,117,132)]:mask[a:b,c:d]=255
    p=cv2.goodFeaturesToTrack(gray,500,.001,2,mask=mask)
    q,s,_=cv2.calcOpticalFlowPyrLK(gray,other,p,None,winSize=(31,31),maxLevel=4)
    back,bs,_=cv2.calcOpticalFlowPyrLK(other,gray,q,None,winSize=(31,31),maxLevel=4)
    keep=(s.ravel()>0)&(bs.ravel()>0)&(np.linalg.norm(p[:,0]-back[:,0],axis=1)<.6)
    p,q=p[keep,0],q[keep,0]
    rays=np.c_[p,np.ones(len(p))]@np.linalg.inv(k0).T@r0
    distance=-(c0@plane[:3]+plane[3])/(rays@plane[:3])
    xyz=c0+rays*distance[:,None]
    proj=(xyz-center)@r.T@K.T;pred=proj[:,:2]/proj[:,2:]
    delta=q-pred;shift=np.median(delta,axis=0)
    good=np.linalg.norm(delta-shift,axis=1)<2.5
    if good.sum()<15:raise RuntimeError(f'{frame}: insufficient drift matches {good.sum()}')
    shift=np.median(delta[good],axis=0);res=np.linalg.norm(delta[good]-shift,axis=1)
    K[:2,2]+=shift
    return K,r,center,error,{'matches':int(good.sum()),'shift':shift.tolist(),'residual_p90_px':float(np.quantile(res,.9))}
