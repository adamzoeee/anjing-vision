"""Fit a pinhole camera to cached pixel/point pairs; reject inaccurate rays."""
import cv2
import numpy as np
from audit_scan46_observation_planes import load_inputs

def camera(points):
    y,x=np.mgrid[8:216:6,8:216:6]
    p=points[y,x].reshape(-1,3).astype(np.float32)
    uv=np.c_[x.ravel(),y.ravel()].astype(np.float32)
    valid=np.isfinite(p).all(1)
    K=np.array([[180.,0,111.5],[0,180.,111.5],[0,0,1]])
    flags=(cv2.CALIB_USE_INTRINSIC_GUESS|cv2.CALIB_FIX_ASPECT_RATIO|cv2.CALIB_FIX_PRINCIPAL_POINT|
           cv2.CALIB_ZERO_TANGENT_DIST|cv2.CALIB_FIX_K1|cv2.CALIB_FIX_K2|cv2.CALIB_FIX_K3)
    err,K,d,rs,ts=cv2.calibrateCamera([p[valid]],[uv[valid]],(224,224),K,None,flags=flags)
    rot=cv2.Rodrigues(rs[0])[0];center=-rot.T@ts[0].ravel()
    return K,rot,center,float(err)

if __name__=='__main__':
    maps,images,R,t=load_inputs()
    for frame in [141,145,149,687]:
        K,r,c,e=camera(np.asarray(maps[frame],float)@R.T+t)
        print(frame,'rms_px',e,'focal_px',K[0,0],'center',c)
