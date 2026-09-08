"""Fixed camera comparison, independent of the running application's selection."""
from pathlib import Path
import sys
import numpy as np
import open3d as o
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/work/46/window_components_20260907'
def render(p,c,angle):
    a=np.deg2rad(angle)
    # Window faces negative Z; physical up is negative Y in this dataset.
    right=np.array([-np.cos(a),0,np.sin(a)])
    depth=np.array([np.sin(a),0,np.cos(a)])
    x=p@right;y=p[:,1];d=p@depth
    xx=((x+2.8)*300).astype(int);yy=((y+1.4)*300).astype(int)
    valid=(xx>=0)&(xx<1100)&(yy>=0)&(yy<900)
    xx,yy,d,col=xx[valid],yy[valid],d[valid],np.uint8(np.clip(c[valid]*255,0,255))
    order=np.argsort(d)[::-1]
    canvas=np.full((900,1100,3),[15,19,26],np.uint8)
    for dx,dy in [(a,b) for a in range(-2,3) for b in range(-2,3)]:
        canvas[np.clip(yy[order]+dy,0,899),np.clip(xx[order]+dx,0,1099)]=col[order]
    return Image.fromarray(canvas)
for angle in [0,25]:
    panels=[]
    for label,path in [('current',ROOT/'data/work/46/postprocess/scene_preview_window_rim_opaque.ply'),('candidate',OUT/(sys.argv[1] if len(sys.argv)>1 else 'scene_window_components_multiview.ply'))]:
        cloud=o.io.read_point_cloud(str(path)); im=render(np.asarray(cloud.points),np.asarray(cloud.colors),angle)
        ImageDraw.Draw(im).text((20,20),label,fill='white');panels.append(im)
    joined=Image.new('RGB',(2200,900));joined.paste(panels[0],(0,0));joined.paste(panels[1],(1100,0))
    joined.save(OUT/f'comparison_{Path(sys.argv[1]).stem if len(sys.argv)>1 else "multiview"}_{angle}.png')
