"""Register a new window visit with exact existing window keyframes, in isolation."""
import os,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pipeline.slam3r_runner import run_reconstruction
DEST=ROOT/'data/work/46/local_gaps_20260912/window_bridge'
if DEST.exists():raise RuntimeError('Candidate directory already exists')
frames=DEST/'frames';frames.mkdir(parents=True);mapping=[]
sources=[('anchor',i,ROOT/f'data/work/45/frames/frame_{i+1:05d}.jpg') for i in range(141,162)]
sources += [('supplement',i,p) for i,p in enumerate(sorted((DEST.parent/'window/frames').glob('frame_*.jpg')))]
for kind,i,source in sources:
    output=len(mapping);os.link(source,frames/f'frame_{output+1:05d}.jpg');mapping.append({'kind':kind,'original':i,'output':output})
(DEST/'mapping.json').write_text(json.dumps(mapping))
result=run_reconstruction(frames,DEST/'slam3r',test_name='window',keyframe_stride=2,win_r=5,num_scene_frame=10,num_points_save=1000000,timeout_s=1200)
print(str(result['ply']),flush=True)
