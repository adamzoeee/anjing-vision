// Local browser integration check; instrumentation is test-only, never served.
const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/吕昊东/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true});
  const page = await browser.newPage({viewport:{width:1480,height:1347}});
  const errors=[];page.on('pageerror', e=>errors.push(String(e)));
  if(process.env.SCAN46_AUDIT_PLY) await page.route('**/scene.ply?*',route=>route.fulfill({contentType:'application/octet-stream',body:fs.readFileSync(process.env.SCAN46_AUDIT_PLY)}));
  await page.route('**/viewer.js?*', async route => {
    let source=fs.readFileSync(path.join(__dirname,'../app/static/preview/viewer.js'),'utf8');
    source=source.replace('window.__startPointsViewer__ = main;', `
      window.__audit = {
        state: () => ({counts:pcdGroup.children.map(p=>p.geometry.attributes.position.count),
          distinct:new Set(pcdGroup.children.map(p=>p.geometry.attributes.position.array)).size,
          overlays:worldGroup.children.filter(g=>g!==pcdGroup&&g.visible).length}),
        view: (eye,target,upright=false) => {controls.autoRotate=false;controls.enableDamping=false;
          camera.up.set(0,upright?0:1,upright?1:0);
          camera.position.set(eye[0],eye[2],-eye[1]);controls.target.set(target[0],target[2],-target[1]);
          controls.update();renderer.render(scene,camera);}
      }; window.__startPointsViewer__ = main;`);
    await route.fulfill({contentType:'application/javascript',body:source});
  });
  await page.goto(process.env.SCAN46_REVIEW_URL);
  await page.waitForFunction(()=>{try{return window.__audit && window.__audit.state().counts.length===4}catch(_){return false}});
  await page.waitForFunction(()=>document.getElementById('overlay').style.display==='none');
  const state=await page.evaluate(()=>window.__audit.state());
  if(state.distinct!==state.counts.length || state.overlays!==0 || errors.length) throw Error(JSON.stringify({state,errors}));
  const out=path.join(__dirname,'../data/work/46/coordinate_repair_20260905');
  for (const [name,eye] of [['top',[.7,-3.5,1.3]],['oblique',[-1,-3.2,3.3]]]) {
    await page.evaluate(eye=>window.__audit.view(eye,[.7,.7,1.3]),eye);
    await page.screenshot({path:path.join(out,(process.env.SCAN46_SHOT_PREFIX||'pure_')+name+'.png')});
  }
  console.log(JSON.stringify({state,errors}));
  await page.evaluate(()=>window.__audit.view([.7,.1,1.7],[.7,.1,-.15],true));
  await page.screenshot({path:path.join(out,(process.env.SCAN46_SHOT_PREFIX||'pure_')+'window.png')});
  await page.click('#btn-mode-gaussian');
  await page.waitForFunction(()=>document.getElementById('scene-sub').textContent.startsWith('空间结构 ·'));
  console.log('Structure page preserved: '+await page.locator('#scene-sub').innerText());
  if(errors.length) throw Error(errors.join('\n'));
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
