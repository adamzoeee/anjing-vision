"""Local-only copy of the production Viewer; formal data is read-only.

Run this script, then open http://127.0.0.1:8766/?scan=46 .
The injected controls use the production PLY parser/material and actual WebGL.
"""
import json
import hashlib
import shutil
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import numpy as np
from PIL import Image
from verify_september12_candidate import VIEWS

ROOT = Path(__file__).resolve().parents[1]
POST = ROOT / 'data/work/46/postprocess'
OUT = ROOT / 'data/work/46/local_gaps_20260912/viewer_diagnostic'

def prepare():
    OUT.mkdir(exist_ok=True)
    static = ROOT / 'app/static/preview'
    for name in ['three.min.js', 'OrbitControls.js']:
        shutil.copyfile(static/name, OUT/name)
    page = (static/'index.html').read_text(encoding='utf-8')
    page = page.replace('/preview-static/', '/')
    page = '\n'.join(line for line in page.splitlines() if 'structure_main.js' not in line and 'gaussian_main.js' not in line)
    (OUT/'index.html').write_text(page, encoding='utf-8')
    source = (static/'viewer.js').read_text(encoding='utf-8')
    hook = (ROOT/'scripts/viewer_gap_diagnostic.js').read_text(encoding='utf-8')
    source = source.replace('        resetView();\n      })', '        resetView();\n        startGapDiagnostic();\n      })')
    source = source.replace("  window.__startPointsViewer__ = main;", hook + '\n  window.__startPointsViewer__ = main;')
    (OUT/'viewer.js').write_text(source, encoding='utf-8')
    candidate = OUT.parent/'independent_objects_verified'
    mesh = np.load(candidate/'observed_surface.npz')
    (OUT/'candidate.json').write_text(json.dumps({k: mesh[k].tolist() for k in mesh.files}),encoding='utf-8')
    shutil.copyfile(candidate/'source_texture.png',OUT/'source_texture.png')
    import open3d as o
    bp=np.asarray(o.io.read_point_cloud(str(POST/'local_gaps_20260912.ply')).points)
    ids=np.load(candidate/'seam_identity_hypothesis.npz')['baseline_ids']
    assert (ids>=1028147).all(), 'Only September12 window additions may be hidden'
    lineage=np.load(ROOT/'data/work/46/window_local_sequence_20260907/window_components_reveal_review.lineage.npz')
    inherited=np.flatnonzero(lineage['baseline_indices']>=507637)
    traced=np.load(OUT/'curtain_exact_lineage.npz')
    uv=traced['source_uv']
    # Source-frame 140: the pale window jamb slopes from u=88.5 at v=25
    # to u=84 at v=210. It is not cloth. Isolate only its interior, leaving
    # the ambiguous one-pixel edge and all actual curtain pixels untouched.
    jamb_right=88.5-(uv[:,1]-25)*4.5/185
    jamb=(uv[:,1]>=25)&(uv[:,1]<=190)&(uv[:,0]<jamb_right-.5)
    wrong_identity=traced['baseline_ids'][jamb & traced['exact']]
    (OUT/'isolation.json').write_text(json.dumps({'strip_ids':ids.tolist(),'historical_supplement_ids':inherited.tolist(),'jamb_in_curtain_ids':wrong_identity.tolist()}))
    old=o.io.read_point_cloud(str(OUT.parent/'new2/ownership/cushion_candidate.ply'))
    assert np.array_equal(np.asarray(old.points)[:len(bp)],bp)
    extra=np.c_[np.asarray(old.points)[len(bp):],np.asarray(old.colors)[len(bp):]]
    extra.astype('<f4').tofile(OUT/'supported_r2.bin')
    if (OUT/'front_formal_depth.png').exists():
        from summarize_viewer_gap import depth
        from verify_september12_candidate import project
        def nonoccluding(p):
            keep=np.ones(len(p),bool)
            for view in ['front','oblique','overview','overhead']:
                xy,z,valid=project(p,VIEWS[view]);ids=np.flatnonzero(valid)
                old=depth(view+'_formal')[xy[ids,1],xy[ids,0]]
                keep[ids[np.isfinite(old)&(z[ids]<old-.015)]]=False
            return keep
        keep=nonoccluding(extra[:,:3]);extra[keep].astype('<f4').tofile(OUT/'safe_r2.bin')
        good=nonoccluding(mesh['positions']);faces=mesh['faces'][good[mesh['faces']].all(1)]
        (OUT/'safe_faces.json').write_text(json.dumps(faces.tolist()))
        (OUT/'r2_visibility_guard.json').write_text(json.dumps({'before_points':len(extra),'retained_points':int(keep.sum()),'before_faces':len(mesh['faces']),'retained_faces':len(faces),'max_foreground_difference_m':.015}))
    (OUT/'views.json').write_text(json.dumps({n:{'K':k.tolist(),'R':r.tolist(),'C':c.tolist()} for n,(k,r,c) in VIEWS.items()}))
    lock=json.loads((OUT.parent/'new2/baseline_lock.json').read_text())
    assert all(hashlib.sha256((POST/n).read_bytes()).hexdigest()==h for n,h in lock.items())
    (OUT/'input_lock.json').write_text(json.dumps(lock,indent=2))

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(OUT),**kwargs)
    def do_GET(self):
        path=self.path.split('?')[0]
        selected=json.loads((POST/'preview_selection.json').read_text())['accepted_file']
        if path=='/api/preview/46/manifest.json':
            payload=json.dumps({'ply':'/scene.ply','window_visual':'/visual.json','alignment':None}).encode()
        elif path=='/scene.ply':payload=(POST/selected).read_bytes()
        elif path=='/visual.json':payload=(POST/selected).with_suffix('.visual.json').read_bytes()
        else:return super().do_GET()
        self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(payload)
    def do_POST(self):
        # Only diagnostic artifacts, never arbitrary paths or formal files.
        import re
        name=self.path.removeprefix('/capture/')
        if not self.path.startswith('/capture/') or not re.fullmatch(r'[a-z0-9_-]+\.(png|json)',name):
            self.send_error(400);return
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<20_000_000:self.send_error(413);return
        (OUT/name).write_bytes(self.rfile.read(length))
        self.send_response(204);self.end_headers()

if __name__=='__main__':
    prepare()
    print('Diagnostic copy only: http://127.0.0.1:8766/?scan=46',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
