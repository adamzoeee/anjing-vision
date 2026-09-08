"""Short window-only sequence with exact primary anchors and one clear visit."""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pipeline.slam3r_runner import run_reconstruction
dest=ROOT/'data/work/46/window_supplement_bridge_20260908'
if dest.exists():raise RuntimeError('Refuse overwriting a reconstruction')
frames=dest/'frames';frames.mkdir(parents=True)
mapping=[]
for i in range(141,162):
    source=ROOT/f'data/work/45/frames/frame_{i+1:05d}.jpg'
    output=len(mapping);os.link(source,frames/f'frame_{output+1:05d}.jpg')
    mapping.append({'output':output,'kind':'primary','original':i})
for i in range(15,31):
    source=ROOT/f'data/work/46/supplement_buchong2_clip2/frames/frame_{i+1:05d}.jpg'
    output=len(mapping);os.link(source,frames/f'frame_{output+1:05d}.jpg')
    mapping.append({'output':output,'kind':'supplement','original':i})
(dest/'mapping.json').write_text(json.dumps(mapping))
result=run_reconstruction(frames,dest/'slam3r',test_name='window',keyframe_stride=2,win_r=5,
    num_scene_frame=10,num_points_save=1000000,timeout_s=1200,
    progress_callback=lambda p:print(round(p,3),flush=True))
print(str(result['ply']),flush=True)
