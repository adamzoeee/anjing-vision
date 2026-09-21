  // Injected only into the isolated diagnostic copy, not the production Viewer.
  async function startGapDiagnostic() {
    controls.autoRotate = false;
    controls.enableDamping = false;
    var views = await (await fetch('/views.json')).json();
    var data = await (await fetch('/candidate.json')).json();
    var isolation=await (await fetch('/isolation.json')).json();
    var extra=new Float32Array(await (await fetch('/supported_r2.bin')).arrayBuffer());
    var ep=[],ec=[];for(var i=0;i<extra.length;i+=6){ep.push(...extra.slice(i,i+3));ec.push(...extra.slice(i+3,i+6));}
    var eg=new THREE.BufferGeometry();eg.setAttribute('position',new THREE.Float32BufferAttribute(ep,3));eg.setAttribute('color',new THREE.Float32BufferAttribute(ec,3));
    var observed=new THREE.Points(eg,pointMaterial);observed.visible=false;worldGroup.add(observed);
    var safeData=new Float32Array(await (await fetch('/safe_r2.bin')).arrayBuffer()),sp=[],sc=[];
    for(var i=0;i<safeData.length;i+=6){sp.push(...safeData.slice(i,i+3));sc.push(...safeData.slice(i+3,i+6));}
    var sg=new THREE.BufferGeometry();sg.setAttribute('position',new THREE.Float32BufferAttribute(sp,3));sg.setAttribute('color',new THREE.Float32BufferAttribute(sc,3));
    var safePoints=new THREE.Points(sg,pointMaterial);safePoints.visible=false;worldGroup.add(safePoints);
    var safeFaces=await (await fetch('/safe_faces.json')).json();
    var texture = await new Promise((resolve,reject)=>new THREE.TextureLoader().load('/source_texture.png',resolve,undefined,reject));
    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(data.positions.flat(),3));
    geometry.setAttribute('uv',new THREE.Float32BufferAttribute(data.uv.flatMap(p=>[p[0]/223,1-p[1]/223]),2));
    geometry.setIndex(data.faces.flat());
    var patch = new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({map:texture,side:THREE.DoubleSide}));
    patch.visible=false;worldGroup.add(patch);
    var r3data=await (await fetch('/r3_patch.json')).json();
    var r3texture=await new Promise((resolve,reject)=>new THREE.TextureLoader().load('/r3_texture.png',resolve,undefined,reject));
    var r3g=new THREE.BufferGeometry();r3g.setAttribute('position',new THREE.Float32BufferAttribute(r3data.positions.flat(),3));r3g.setAttribute('uv',new THREE.Float32BufferAttribute(r3data.uv.flatMap(p=>[p[0]/223,1-p[1]/223]),2));r3g.setIndex(r3data.faces.flat());
    var r3patch=new THREE.Mesh(r3g,new THREE.MeshBasicMaterial({map:r3texture,side:THREE.DoubleSide}));r3patch.visible=false;worldGroup.add(r3patch);
    var r1data=await (await fetch('/r1_patch.json')).json();
    var r1texture=await new Promise((resolve,reject)=>new THREE.TextureLoader().load('/r1_texture.png',resolve,undefined,reject));
    var r1g=new THREE.BufferGeometry();r1g.setAttribute('position',new THREE.Float32BufferAttribute(r1data.positions.flat(),3));r1g.setAttribute('uv',new THREE.Float32BufferAttribute(r1data.uv.flatMap(p=>[p[0]/223,1-p[1]/223]),2));r1g.setIndex(r1data.faces.flat());
    var r1patch=new THREE.Mesh(r1g,new THREE.MeshBasicMaterial({map:r1texture,side:THREE.DoubleSide}));r1patch.visible=false;worldGroup.add(r1patch);
    var r5data=await (await fetch('/r5_patch.json')).json();
    var r5g=new THREE.BufferGeometry();r5g.setAttribute('position',new THREE.Float32BufferAttribute(r5data.positions.flat(),3));r5g.setAttribute('uv',new THREE.Float32BufferAttribute(r5data.uv.flatMap(p=>[p[0]/223,1-p[1]/223]),2));r5g.setIndex(r5data.faces.flat());
    var r5patch=new THREE.Mesh(r5g,new THREE.MeshBasicMaterial({map:r3texture,side:THREE.DoubleSide}));r5patch.visible=false;worldGroup.add(r5patch);
    var points=pcdGroup.children.filter(x=>x.isPoints), visual=pcdGroup.children.filter(x=>x.isMesh);
    // Wait for all asynchronously parsed chunks, not just the first chunk.
    while(points.reduce((n,x)=>n+x.geometry.attributes.position.count,0)<1030396){
      await new Promise(r=>setTimeout(r,50));points=pcdGroup.children.filter(x=>x.isPoints);
    }
    var panel=$('panel');panel.innerHTML='<h2>独立诊断：正式数据未修改</h2>';
    var status=document.createElement('p');panel.appendChild(status);
    var toWorld=p=>new THREE.Vector3(p[0],p[2],-p[1]);
    function setView(name){
      var v=views[name];camera.position.copy(toWorld(v.C));
      camera.up.copy(toWorld(v.R[1].map(x=>-x)));
      controls.target.copy(toWorld(v.C.map((x,i)=>x+v.R[2][i])));
      camera.fov=2*Math.atan(112/v.K[1][1])*180/Math.PI;camera.aspect=1;
      camera.updateProjectionMatrix();controls.update();
    }
    var strip=new Set(isolation.strip_ids),historical=new Set(isolation.historical_supplement_ids);
    var identity=new Set(isolation.jamb_in_curtain_ids);
    var offset=0;points.forEach(x=>{var a=[],b=[];for(var i=0;i<x.geometry.attributes.position.count;i++){if(!strip.has(i+offset)&&!identity.has(i+offset))a.push(i);if(!historical.has(i+offset))b.push(i);}offset+=x.geometry.attributes.position.count;x.userData.clean=a;x.userData.observed=b;});
    offset=0;points.forEach(x=>{var kept=[];for(var i=0;i<x.geometry.attributes.position.count;i++)if(!identity.has(i+offset))kept.push(i);offset+=x.geometry.attributes.position.count;x.userData.identity=kept;});
    var modes={baseline:[true,false,false],formal:[true,true,false],r2:[false,false,true],combined:[true,true,true],observed_only:[true,false,false],supported_points:[false,false,false],revised:[true,true,true],r3_repair:[true,true,false],local_repair:[true,true,true]};
    modes.identity_only=[true,true,false];
    function mode(name){var flags=modes[name];points.forEach(x=>{x.visible=flags[0];x.geometry.setIndex(name==='revised'||name==='local_repair'?x.userData.clean:name==='observed_only'?x.userData.observed:null);});visual.forEach(x=>x.visible=flags[1]);patch.visible=flags[2];patch.geometry.setIndex((name==='local_repair'?safeFaces:data.faces).flat());observed.visible=name==='supported_points'||name==='revised';safePoints.visible=name==='local_repair';r3patch.visible=name==='r3_repair'||name==='local_repair';r1patch.visible=name==='local_repair';r5patch.visible=name==='local_repair';}
    var originalMode=mode;mode=function(name){originalMode(name);if(name==='identity_only')points.forEach(x=>x.geometry.setIndex(x.userData.identity));};
    var depthMaterials=[];
    function depthMaterial(isPoint){
      var m=new THREE.ShaderMaterial({side:THREE.DoubleSide,uniforms:{size:{value:.0175},scale:{value:448}},
        vertexShader:'varying float zd; uniform float size; uniform float scale; void main(){vec4 p=modelViewMatrix*vec4(position,1.);zd=-p.z;gl_Position=projectionMatrix*p;gl_PointSize=size*scale/max(.0001,-p.z);}',
        fragmentShader:'varying float zd; void main(){float n=floor(zd*10000.+.5);gl_FragColor=vec4(mod(floor(n/65536.),256.),mod(floor(n/256.),256.),mod(n,256.),255.)/255.;}'});
      depthMaterials.push(m);return m;
    }
    var pointDepth=depthMaterial(true),meshDepth=depthMaterial(false);
    var target=new THREE.WebGLRenderTarget(896,896);
    async function save(name,depth){
      var all=points.concat(visual,[patch,observed,r3patch,r1patch,r5patch,safePoints]),old=all.map(x=>x.material),bg=scene.background;
      if(depth){scene.background=new THREE.Color(0);all.forEach(x=>x.material=x.isPoints?pointDepth:meshDepth);}
      renderer.setRenderTarget(target);renderer.render(scene,camera);
      var raw=new Uint8Array(896*896*4);renderer.readRenderTargetPixels(target,0,0,896,896,raw);renderer.setRenderTarget(null);
      all.forEach((x,i)=>x.material=old[i]);scene.background=bg;
      var canvas=document.createElement('canvas');canvas.width=canvas.height=896;var ctx=canvas.getContext('2d'),pixels=ctx.createImageData(896,896);
      for(var y=0;y<896;y++)pixels.data.set(raw.subarray((895-y)*896*4,(896-y)*896*4),y*896*4);
      ctx.putImageData(pixels,0,0);
      var blob=await new Promise(r=>canvas.toBlob(r));await fetch('/capture/'+name+'.png',{method:'POST',body:blob});
    }
    for(var name of Object.keys(views)){
      var button=document.createElement('button');button.className='btn';button.textContent=name;button.onclick=()=>{};
      button.onclick=(n=>()=>{setView(n);renderer.setSize(896,896);})(name);panel.appendChild(button);
    }
    for(var name of Object.keys(modes)){
      var button=document.createElement('button');button.className='btn';button.textContent=name;
      button.onclick=(n=>()=>{mode(n);status.textContent=n;})(name);panel.appendChild(button);
    }
    var auditButton=document.createElement('button');auditButton.className='btn';auditButton.textContent='运行同视角 WebGL 隔离验收';panel.appendChild(auditButton);
    auditButton.onclick=async()=>{
      auditButton.disabled=true;renderer.setPixelRatio(1);renderer.setSize(896,896);
      for(var view of Object.keys(views)){
        setView(view);
        for(var name of Object.keys(modes)){
          mode(name);status.textContent=view+' / '+name;
          await save(view+'_'+name,false);await save(view+'_'+name+'_depth',true);
        }
        mode('formal');var prior=pointMaterial.size;pointMaterial.size=.021;pointDepth.uniforms.size.value=.021;
        await save(view+'_density120',false);await save(view+'_density120_depth',true);
        pointMaterial.size=prior;pointDepth.uniforms.size.value=prior;
      }
      await fetch('/capture/webgl_complete.json',{method:'POST',body:JSON.stringify({points:points.reduce((n,x)=>n+x.geometry.attributes.position.count,0),visualMeshes:visual.length,candidateVertices:data.positions.length,pointSize:pointMaterial.size,views:Object.keys(views)})});
      setView('front');mode('formal');status.textContent='隔离验收完成，当前为原正式显示';auditButton.disabled=false;
    };
    setView('front');renderer.setPixelRatio(1);renderer.setSize(896,896);mode('formal');
    status.textContent='准备完成：原正式显示';
  }
