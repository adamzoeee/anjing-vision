"""Read-only geometry audit of image-selected opaque window components."""
import json
import numpy as np
from audit_scan46_observation_planes import load_inputs, OUT, ROOT

COMPONENTS = {
    'upper_wall': (150, [(0,18,20,150)]),
    'left_wall': (150, [(25,175,2,30)]),
    'right_curtain': (150, [(5,200,162,218)]),
    'sill': (160, [(120,134,66,166)]),
    'lower_wall': (160, [(141,151,65,160)]),
    'left_curtain': (160, [(3,140,2,28)]),
}

def main():
    maps, images, R, t = load_inputs()
    T = np.asarray(json.loads((OUT/'registration_contract.json').read_text())['transform'])
    plane = np.asarray(json.loads((OUT/'scene_preview_window_rim_opaque.json').read_text())['wall_plane'])
    conf = np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy', mmap_mode='r')
    for name, (frame, rects) in COMPONENTS.items():
        mask = np.zeros((224,224), bool)
        for a,b,c,d in rects: mask[a:b,c:d] = True
        p = (np.asarray(maps[frame],float)@R.T+t)@T[:3,:3].T+T[:3,3]
        q = p[mask]
        print(json.dumps(dict(component=name,frame=frame,candidates=len(q),
            confidence_pass=int((conf[frame][mask]>=3).sum()),
            wall_distance_quantiles=np.quantile(q@plane[:3]+plane[3],[0,.1,.5,.9,1]).tolist(),
            bounds=np.quantile(q,[.05,.95],axis=0).tolist())))

if __name__ == '__main__': main()
