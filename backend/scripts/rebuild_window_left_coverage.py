"""Recover the left curtain lost by the model's central square input crop."""
import json
import sys
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pipeline.slam3r_runner import run_reconstruction

dest=ROOT/'data/work/46/window_left_coverage_20260907'
if dest.exists():raise RuntimeError('Refuse reuse of existing reconstruction')
frames=dest/'frames';frames.mkdir(parents=True)
mapping=[]
for output,i in enumerate(range(125,176)):
    source=ROOT/f'data/work/45/frames/frame_{i+1:05d}.jpg'
    with Image.open(source) as image:
        assert image.size==(1920,1080)
        image.crop((120,0,1200,1080)).save(frames/f'frame_{output+1:05d}.png')
    mapping.append({'output':output,'original':i,'crop':[120,0,1200,1080]})
(dest/'mapping.json').write_text(json.dumps(mapping))
result=run_reconstruction(frames,dest/'slam3r',test_name='window',keyframe_stride=2,win_r=5,
    num_scene_frame=10,num_points_save=1000000,timeout_s=1200,
    progress_callback=lambda p:print(round(p,3),flush=True))
print(str(result['ply']),flush=True)
