"""High-resolution local stereo of the opaque window wall; no whole-room run."""
import json
import cv2
import numpy as np
import open3d as o
from PIL import Image
from audit_scan46_observation_planes import load_inputs,ROOT,OUT,POST
from audit_scan46_window_camera import camera

DEST=ROOT/'data/work/46/window_components_20260907'
def main():
    maps,images,R,t=load_inputs()
    T=np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
    def metric(p):return (np.asarray(p,float)@R.T+t)@T[:3,:3].T+T[:3,3]
    def load(i):
        im=Image.open(ROOT/f'data/work/45/frames/frame_{i+1:05d}.jpg').convert('RGB')
        w,h=im.size;size=min(w,h);im=im.crop(((w-size)//2,(h-size)//2,(w+size)//2,(h+size)//2))
        return np.asarray(im.resize((672,672),Image.Resampling.LANCZOS))
    points=[];colours=[];reports=[]
    for a,b in [(150,145),(150,155),(160,155)]:
        ia,ib=load(a),load(b)
        ka,ra,ca,ea=camera(metric(maps[a]));kb,rb,cb,eb=camera(metric(maps[b]))
        ka[:2]*=3;kb[:2]*=3
        rel=rb@ra.T;trans=rb@(ca-cb)
        ar,br,ap,bp,Q,_,_=cv2.stereoRectify(ka,np.zeros(5),kb,np.zeros(5),(672,672),np.ascontiguousarray(rel),trans.reshape(3,1),flags=cv2.CALIB_ZERO_DISPARITY,alpha=.25)
        if abs(bp[1,3])>abs(bp[0,3]):
            reports.append(dict(a=a,b=b,rejected='vertical rectification'));continue
        ax,ay=cv2.initUndistortRectifyMap(ka,None,ar,ap,(672,672),cv2.CV_32FC1)
        bx,by=cv2.initUndistortRectifyMap(kb,None,br,bp,(672,672),cv2.CV_32FC1)
        left=cv2.remap(ia,ax,ay,cv2.INTER_LINEAR);right=cv2.remap(ib,bx,by,cv2.INTER_LINEAR)
        # Negative disparity is required if the second camera is on the other side.
        min_d=0 if bp[0,3]<0 else -128
        matcher=cv2.StereoSGBM_create(minDisparity=min_d,numDisparities=128,blockSize=5,
            P1=8*3*25,P2=32*3*25,disp12MaxDiff=1,uniquenessRatio=12,speckleWindowSize=80,speckleRange=2,
            mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
        disparity=matcher.compute(left,right).astype(float)/16
        local=cv2.reprojectImageTo3D(disparity.astype(np.float32),Q)
        world=local@ar@ra+ca
        # Semantic mask defined on actual source pixels; glass and plants excluded.
        mask=np.zeros((224,224),np.uint8)
        rects=[(0,36,0,224),(36,190,0,59),(36,224,158,224),(207,224,0,158)] if a==150 else [(120,139,60,165),(137,150,55,165),(0,145,0,30)]
        for y0,y1,x0,x1 in rects:mask[y0:y1,x0:x1]=1
        m=cv2.remap(cv2.resize(mask,(672,672),interpolation=cv2.INTER_NEAREST),ax,ay,cv2.INTER_NEAREST)>0
        keep=m&np.isfinite(world).all(2)&(disparity>min_d)&(local[:,:,2]>.15)&(local[:,:,2]<4)
        keep&=(world[:,:,2]>-.65)&(world[:,:,2]<.2)&(world[:,:,1]>-1)&(world[:,:,1]<.85)
        p,c=world[keep],left[keep]/255.
        if len(p):points.append(p);colours.append(c)
        cv2.imwrite(str(DEST/f'stereo_{a}_{b}.png'),cv2.cvtColor(np.hstack([left,right]),cv2.COLOR_RGB2BGR))
        reports.append(dict(a=a,b=b,baseline_m=float(np.linalg.norm(trans)),accepted=len(p)))
    if points:
        p=np.vstack(points);c=np.vstack(colours)
        cloud=o.geometry.PointCloud(o.utility.Vector3dVector(p));cloud.colors=o.utility.Vector3dVector(c)
        assert o.io.write_point_cloud(str(DEST/'stereo_window_full.ply'),cloud)
    (DEST/'stereo_report.json').write_text(json.dumps(reports,indent=2));print(json.dumps(reports))
if __name__=='__main__':main()
