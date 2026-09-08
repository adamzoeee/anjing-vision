"""Read-only, depth-correct point rendering from measured video cameras."""
from pathlib import Path
import sys
import numpy as np
import open3d as o
from PIL import Image
from audit_scan46_observation_planes import load_inputs,OUT,ROOT
from audit_scan46_window_camera import camera
import json

def render(p,c,K,rot,center,size=896,radius=2):
    local=(p-center)@rot.T
    uv=local@K.T
    xy=np.rint(uv[:,:2]/uv[:,2:]*size/224).astype(int)
    good=(local[:,2]>.03)&np.all((xy>=radius)&(xy<size-radius),axis=1)
    xy,z,col=xy[good],local[good,2],np.uint8(np.clip(c[good]*255,0,255))
    depth=np.full(size*size,np.inf)
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            ids=(xy[:,1]+dy)*size+xy[:,0]+dx
            np.minimum.at(depth,ids,z)
    canvas=np.full((size*size,3),[15,19,26],np.uint8)
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            ids=(xy[:,1]+dy)*size+xy[:,0]+dx
            front=z==depth[ids]
            canvas[ids[front]]=col[front]
    return Image.fromarray(canvas.reshape(size,size,3))

if __name__=='__main__':
    path=Path(sys.argv[1]);cloud=o.io.read_point_cloud(str(path))
    p=np.asarray(cloud.points);c=np.asarray(cloud.colors)
    maps,images,R,t=load_inputs()
    T=np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
    for frame in [150,160]:
        world=(np.asarray(maps[frame],float)@R.T+t)@T[:3,:3].T+T[:3,3]
        K,rot,center,e=camera(world)
        render(p,c,K,rot,center).save(path.with_name(path.stem+f'_camera{frame}.png'))
        print(frame,center.tolist(),e)
    for label,center in [('front',[.95,-.08,2.0]),('oblique',[1.65,-.12,1.8])]:
        center=np.asarray(center);forward=np.array([.95,-.08,-.22])-center
        forward/=np.linalg.norm(forward)
        right=np.cross(forward,[0,-1,0]);right/=np.linalg.norm(right)
        down=np.cross(forward,right);rot=np.vstack([right,down,forward])
        K=np.array([[160.,0,111.5],[0,160.,111.5],[0,0,1]])
        render(p,c,K,rot,center).save(path.with_name(path.stem+f'_{label}.png'))
