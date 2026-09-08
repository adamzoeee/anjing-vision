"""Loopback-only read-only harness using the unchanged production PLY viewer."""
import argparse,json,re
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'app/static/preview'
DEST=ROOT/'data/work/46/window_local_sequence_20260907'

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed=urlparse(self.path);path=parsed.path
        if path=='/api/preview/46/manifest.json':
            data=json.dumps({'scan_id':46,'name':'扫描46 · 窗户局部检查','ply':'/candidate.ply',
                'alignment':json.loads((ROOT/'data/work/46/postprocess/alignment.json').read_text())}).encode()
            kind='application/json'
        elif path=='/candidate.ply':
            data=self.server.candidate.read_bytes();kind='application/octet-stream'
        elif path=='/preview/46':
            data=(STATIC/'index.html').read_bytes();kind='text/html; charset=utf-8'
        elif path.startswith('/preview-static/'):
            filename=path.rsplit('/',1)[-1]
            if filename not in {p.name for p in STATIC.iterdir() if p.is_file()}:
                self.send_error(404);return
            data=(STATIC/filename).read_bytes();kind='text/javascript'
            if filename=='viewer.js':
                js=data.decode('utf-8')
                # Fixed inspection camera only in this HTTP response, never on disk.
                insert="""
    var qaView = new URLSearchParams(location.search).get('view') || 'front';
    if (qaView !== 'room') {
      camera.up.set(0,0,1);
      controls.target.set(.95,-.22,.08);
      if (qaView === 'oblique') camera.position.set(1.65,1.8,.12);
      else camera.position.set(.95,2.0,.08);
      camera.fov=69; camera.updateProjectionMatrix();
    }
"""
                start=js.index('  function resetView() {');end=js.index('  /* ---------------- 主流程',start)
                section=js[start:end];assert section.count('    controls.update();')==1
                js=js[:start]+section.replace('    controls.update();',insert+'    controls.update();')+js[end:]
                data=js.encode()
        else:
            self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers()
        self.wfile.write(data)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('candidate');args=parser.parse_args()
    candidate=(DEST/args.candidate).resolve()
    assert candidate.is_relative_to(DEST.resolve()) and candidate.is_file() and candidate.suffix=='.ply'
    server=ThreadingHTTPServer(('127.0.0.1',8001),Handler);server.candidate=candidate
    print('Read-only viewer on 127.0.0.1:8001',flush=True);server.serve_forever()
