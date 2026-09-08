"""Read-only, pixel-selected window filter audit for the presentation pass."""
import json
import numpy as np
from audit_scan46_observation_planes import load_inputs, OUT, ROOT

maps, images, R, t = load_inputs()
T = np.array(json.loads((OUT/'registration_contract.json').read_text())['transform'])
plane = np.array(json.loads((OUT/'verified_repair_v3.json').read_text())['window']['baseline_plane'])
conf = np.load(ROOT/'data/work/45/slam3r/scene/preds/registered_confs.npy', mmap_mode='r')
# Image 145: opaque top, left/right jambs; exclude glass and exterior pixels.
mask = np.zeros((224,224),bool)
for a,b,c,d in [(32,46,23,120),(47,190,22,32),(45,200,116,129)]:
    mask[a:b,c:d] = True
p = (np.asarray(maps[145],float)@R.T+t)@T[:3,:3].T+T[:3,3]
distance = abs(p@plane[:3]+plane[3])[mask]
c = np.asarray(conf[145])[mask]
print(json.dumps({'frame':145, 'opaque_frame_pixels':int(mask.sum()),
 'confidence_ge3':int((c>=3).sum()),
 'plane_25mm':int((distance<.025).sum()),
 'confidence_and_plane':int(((c>=3)&(distance<.025)).sum()),
 'distance_quantiles_m':np.quantile(distance,[.1,.5,.9]).tolist()},indent=2))
