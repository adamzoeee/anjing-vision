/* Standalone GLB preview. No changes to the pointcloud demo or production app. */
(async()=>{
 const names={overview:'整屋总览',living_room:'客厅',bedroom:'卧室',kitchen:'厨房',bathroom:'卫生间',corridor:'走廊'};
 const status=document.querySelector('#status'),view=document.querySelector('#view');
 try{
  const spec=await fetch('render_spec.json').then(r=>{if(!r.ok)throw Error(r.status);return r.json()});
  const scene=new THREE.Scene();scene.background=new THREE.Color('#26343b');
  const renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.outputEncoding=THREE.sRGBEncoding;renderer.toneMapping=THREE.ACESFilmicToneMapping;view.append(renderer.domElement);
  const cam=new THREE.PerspectiveCamera(42,1,.05,100);const controls=new THREE.OrbitControls(cam,renderer.domElement);controls.enableDamping=true;
  scene.add(new THREE.HemisphereLight(0xfff4df,0x586676,1.1));const sun=new THREE.DirectionalLight(0xfff5e4,1.6);sun.position.set(3,12,5);scene.add(sun);
  const gltf=await new THREE.GLTFLoader().loadAsync('house.glb');scene.add(gltf.scene);
  // The offline render uses source lights; web preview uses a bounded lighting setup.
  gltf.scene.traverse(o=>{if(o.isLight)o.visible=false});
  function belongs(o,rid){for(let p=o;p;p=p.parent)if(p.name===rid)return true;return false}
  function show(){
   const key=document.querySelector('#room').value,cut=document.querySelector('#cutaway').checked,ceil=document.querySelector('#ceiling').checked;
   const room=spec.rooms.find(r=>r.id===key);
   gltf.scene.traverse(o=>{
    if(!o.isMesh)return;
    const wall=o.name.startsWith('wall_');
    const owners=(o.userData.rooms||'').split(',');
    const frame=o.parent&&o.parent.name.startsWith('door_frame_')?o.parent:null;
    o.visible=Boolean(key==='overview'||belongs(o,key)||(wall&&owners.includes(key))||(frame&&(frame.userData.rooms||'').split(',').includes(key)));
    if(o.name.startsWith('ceiling_')&&!ceil)o.visible=false;
    if(wall&&cut){
     const i=Number(o.name.match(/_(\d+)$/)?.[1]);const w=spec.walls[i];
     if(w){const near=room?(w.axis===1&&Math.abs(w.plane-room.origin[1])<.01)||(w.axis===0&&Math.abs(w.plane-room.origin[0]-room.size[0])<.01):w.axis===1&&w.plane===0;if(near)o.visible=false;}
    }
   });
   if(room){const [x,y]=room.origin,[l,w]=room.size;controls.target.set(x+l/2,.6,-y-w/2);cam.position.set(x+l*1.6,Math.max(l,w)*1.4,-y+w*.8)}
   else{controls.target.set(5.2,.5,-3.7);cam.position.set(17,17,14)}
   controls.update();status.textContent=names[key]+'\n'+spec.objects.length+' 件输入物体 · 来源：'+spec.furniture_source+'\n程序化实体模型 · 非实拍纹理';
  }
  status.textContent='';for(const id of ['room','ceiling','cutaway'])document.getElementById(id).addEventListener('change',show);show();
  function resize(){renderer.setSize(view.clientWidth,view.clientHeight);cam.aspect=view.clientWidth/view.clientHeight;cam.updateProjectionMatrix()}
  new ResizeObserver(resize).observe(view);resize();
  renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,cam)});
  for(const [key,name] of Object.entries(names)){const f=document.createElement('figure'),img=document.createElement('img'),cap=document.createElement('figcaption');img.src=key+'.png';img.alt=name+'离线渲染';cap.textContent=name+' · Blender CPU 渲染';f.append(img,cap);document.querySelector('#gallery').append(f)}
  window.__houseRenderReady=true;
 }catch(e){status.textContent='加载失败：'+e.message;console.error(e)}
})();
