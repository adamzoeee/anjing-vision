/* Synthetic demo only. No scan API, credentials or production viewer mutations. */
(async function () {
  'use strict';
  const el = id => document.getElementById(id);
  const get = async path => { const response = await fetch(path, {cache: 'no-store'}); if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`); return response; };
  try {
    const manifest = await (await get('manifest.json')).json();
    const truth = await (await get('ground_truth.json')).json();
    let predictions = await (await get(manifest.predictions)).json();
    async function optionalAnalysis(path) {
      if(!path) return null;
      const response=await fetch(path,{cache:'no-store'});
      if(response.status===404) return null;
      if(!response.ok) throw new Error('布局分析加载失败');
      return response.json();
    }
    const layoutTruth=await optionalAnalysis(manifest.layout_analysis);
    let layoutModel=await optionalAnalysis(manifest.model_layout_analysis);
    el('assistant-panel').hidden=!manifest.layout_analysis;
    el('status').after(el('assistant-panel'));
    const scene = new THREE.Scene(); scene.background = new THREE.Color(0x101823);
    const host = el('view');
    const renderer = new THREE.WebGLRenderer({antialias: true});
    renderer.localClippingEnabled = true;
    const wallClip = new THREE.Plane(new THREE.Vector3(0,0,-1),1.05);
    renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5)); host.appendChild(renderer.domElement);
    const camera = new THREE.PerspectiveCamera(48, 1, 0.05, 100);
    camera.up.set(0, 0, 1);
    const controls = new THREE.OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
    const roomGroups = new Map(); const overlay = new THREE.Group(); scene.add(overlay);
    const routeOverlay=new THREE.Group();scene.add(routeOverlay);
    const roomLabels = new Map(), pickTargets = [];
    const names = {table:'桌子',coffee_table:'茶几',dining_table:'餐桌',chair:'椅子',dining_chair:'餐椅',sofa:'沙发',bed:'床',cabinet:'柜子',cupboard:'橱柜',tv:'电视',television:'电视',wardrobe:'衣柜',nightstand:'床头柜',desk:'书桌',window:'窗户',curtain:'窗帘',air_conditioner:'空调',rug:'地毯',plant:'绿植',lamp:'台灯',sink:'水槽',hand_sink:'洗手台',fridge:'冰箱',refrigerator:'冰箱',stove:'灶台',range_hood:'抽油烟机',toilet:'马桶',vanity:'洗手台',mirror:'镜子',shower:'淋浴区域',shower_room:'淋浴区域',shower_head:'花洒',towel_rack:'毛巾架',shoe_cabinet:'鞋柜',wall_art:'墙面装饰',ceiling_light:'顶灯',tv_stand:'电视柜'};
    Object.assign(names, {carpet:'地毯',painting:'挂画',side_table:'边桌'});
    Object.assign(names, {book:'书本',cup:'杯子',countertop:'操作台',washing_machine:'洗衣机',storage_cabinet:'储物柜',coat_rack:'衣帽架',umbrella_stand:'伞架',storage_box:'收纳箱'});
    Object.assign(names, {clothes_rack:'衣帽架'});
    for (const room of manifest.rooms) {
      const option = document.createElement('option'); option.value = room.id; option.textContent = room.label; el('room').appendChild(option);
      const data = new Float32Array(await (await get(room.buffer)).arrayBuffer());
      const layers = new Uint8Array(await (await get(room.layers)).arrayBuffer());
      if (data.length !== room.points * 6 || layers.length !== room.points) throw new Error(`点云长度异常: ${room.id}`);
      const group = new THREE.Group(); scene.add(group);
      const objectColors = new Float32Array(room.points * 3);
      for (let i=0; i<room.points; i++) objectColors.set(data.subarray(i*6+3,i*6+6), i*3);
      truth.objects.filter(item => item.room_id === room.id && item.point_range).forEach(item => {
        let hash = 0; for (const char of item.object) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
        const color = new THREE.Color().setHSL((hash % 360)/360,.5,.6);
        const [start,end] = item.point_range;
        if (start < 0 || end > room.points || start > end) throw new Error('物体点索引异常');
        for (let i=start;i<end;i++) objectColors.set([color.r,color.g,color.b],i*3);
      });
      for (let layer = 0; layer < 3; layer++) {
        const pos = [], rgb = [], tint = [], semantic = [];
        for (let i = 0; i < room.points; i++) {
          if (layers[i] !== layer) continue;
          pos.push(...data.subarray(i * 6, i * 6 + 3)); rgb.push(...data.subarray(i * 6 + 3, i * 6 + 6));
          tint.push(...(layer === 2 ? data.subarray(i*6+3,i*6+6) : room.color));
          semantic.push(...objectColors.subarray(i*3,i*3+3));
        }
        const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
        geo.setAttribute('color', new THREE.Float32BufferAttribute(tint, 3));
        const points = new THREE.Points(geo, new THREE.PointsMaterial({size: manifest.spacing_m * 0.7, vertexColors: true}));
        points.userData = {layer, rgb, tint, semantic}; group.add(points);
      }
      roomGroups.set(room.id, group);
      const canvas = document.createElement('canvas'); canvas.width=256; canvas.height=64;
      const context = canvas.getContext('2d'); context.fillStyle='#172a39'; context.fillRect(0,0,256,64);
      context.fillStyle='#f0e7c8';context.font='32px sans-serif';context.textAlign='center';context.fillText(room.label,128,44);
      const name = new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas),depthTest:false}));
      name.position.set(room.origin[0]+room.size[0]/2,room.origin[1]+room.size[1]/2,room.size[2]+.25);
      name.scale.set(1.4,.35,1); name.userData.roomId=room.id;scene.add(name);roomLabels.set(room.id,name);pickTargets.push(name);
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(room.size[0],room.size[1]),new THREE.MeshBasicMaterial({transparent:true,opacity:0,depthWrite:false,side:THREE.DoubleSide}));
      floor.position.set(room.origin[0]+room.size[0]/2,room.origin[1]+room.size[1]/2,.002);floor.userData.roomId=room.id;scene.add(floor);pickTargets.push(floor);
    }
    function reset() {
      const room = manifest.rooms.find(r => r.id === el('room').value);
      const center = room ? [room.origin[0] + room.size[0] / 2, room.origin[1] + room.size[1] / 2, 0.8] : [5.25, 3.75, 0.8];
      const span = room ? Math.max(...room.size) : 11;
      controls.target.fromArray(center);
      if(room) {
        const radius=Math.hypot(...room.size)/2;
        const distance=radius/Math.sin(camera.fov*Math.PI/360)*1.12;
        const direction=new THREE.Vector3(.8,-1,1.25).normalize();
        camera.position.copy(controls.target).addScaledVector(direction,distance);
      } else camera.position.set(center[0] + span * .8, center[1] - span, span * 1.35);
      controls.update();
    }
    function clearOverlay() {
      overlay.traverse(child => {
        if (child.geometry) child.geometry.dispose();
        if (child.material) { if (child.material.map) child.material.map.dispose(); child.material.dispose(); }
      });
      while (overlay.children.length) overlay.remove(overlay.children[0]);
    }
    function drawObject(item, model) {
      const box = new THREE.BoxGeometry(...item.size);
      const edge = new THREE.EdgesGeometry(box); box.dispose();
      const wire = new THREE.LineSegments(edge, new THREE.LineBasicMaterial({color: model ? 0x5bffba : 0xffcb66}));
      wire.position.fromArray(item.position); wire.rotation.z = item.rotation * Math.PI / 180; overlay.add(wire);
      const canvas = document.createElement('canvas'); canvas.width = 384; canvas.height = 64;
      const ctx = canvas.getContext('2d'); ctx.fillStyle = 'rgba(12,22,30,0.9)'; ctx.fillRect(0,0,384,64);
      ctx.fillStyle = model ? '#8dffce' : '#ffcb66'; ctx.font = '30px sans-serif';
      ctx.fillText(`${model ? '识别' : '真值'} · ${item.label || names[item.object] || item.object}`, 10, 43);
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas), depthTest:false}));
      sprite.position.set(item.position[0],item.position[1],item.position[2]+item.size[2]/2+.2); sprite.scale.set(1.6,.25,1); overlay.add(sprite);
    }
    function update() {
      const selected = el('room').value, mode = el('mode').value;
      roomGroups.forEach((group, id) => {
        group.visible = selected === 'all' || selected === id;
        roomLabels.get(id).visible = group.visible;
        group.children.forEach(points => {
          points.visible = points.userData.layer === 2 || (points.userData.layer === 1 ? el('ceiling').checked : el('walls').checked);
          points.material.clippingPlanes = points.userData.layer === 0 && el('cutaway').checked ? [wallClip] : [];
          points.geometry.attributes.color.array.set(el('object-colors').checked && points.userData.layer === 2 ? points.userData.semantic : el('colors').checked ? points.userData.tint : points.userData.rgb);
          points.geometry.attributes.color.needsUpdate = true;
        });
      });
      clearOverlay();
      const selectedRooms = manifest.rooms.filter(r => selected === 'all' || selected === r.id);
      const labels = {not_run:'尚未运行',running:'正在推理',done:'推理完成',failed:'推理失败'};
      el('status').textContent = selectedRooms.map(r => {
        const result = predictions.rooms[r.id] || {status:'not_run',objects:[]};
        return `${r.label}: ${labels[result.status] || result.status} / ${result.objects.length} 个模型物体` + (result.error ? `\n${result.error}` : '');
      }).join('\n');
      const items = mode === 'truth' ? truth.objects : mode === 'model' ? selectedRooms.flatMap(r => (predictions.rooms[r.id] || {}).objects || []) : [];
      const visible = items.filter(item => selected === 'all' || item.room_id === selected);
      visible.forEach(item => drawObject(item, mode === 'model'));
      el('objects').textContent = visible.map(item => `${mode === 'model' ? '识别' : '真值'} ${names[item.object] || item.object}\n中心 (${item.position.map(v => v.toFixed(2)).join(', ')}) m\n尺寸 ${item.size.map(v => v.toFixed(2)).join(' × ')} m`).join('\n\n');
      if (mode === 'truth') el('status').textContent += '\n黄色标签为预设真值，仅用于核对，非模型输出。';
      routeOverlay.children.slice().forEach(line=>{line.geometry.dispose();line.material.dispose();routeOverlay.remove(line);});
      const analysis=el('assistant-source').value==='truth'?layoutTruth:layoutModel;
      el('assistant').textContent=selectedRooms.map(room=>{
        const entry=analysis && analysis.rooms[room.id];
        if(!entry || entry.status==='not_evaluable') return room.label+'：证据不足，暂不能分析；不代表安全。';
        const source=analysis.source==='synthetic_ground_truth'?'生成几何':'模型结果（可能漏检）';
        const widths=entry.routes.map(r=>r.minimum_width_m==null?'不可评估':r.minimum_width_m.toFixed(2)+' m').join(' / ');
        const occupied=entry.doorways.reduce((sum,d)=>sum+d.occupied_area_m2,0);
        entry.routes.forEach(route=>{
          if(route.path_xy.length<2) return;
          const geometry=new THREE.BufferGeometry().setFromPoints(route.path_xy.map(p=>new THREE.Vector3(p[0],p[1],.06)));
          routeOverlay.add(new THREE.Line(geometry,new THREE.LineBasicMaterial({color:analysis.source==='synthetic_ground_truth'?0xffcb66:0x5bffba})));
        });
        return `${room.label} · ${source}\n路线局部净空：${widths}\n门口检查区占用：${occupied.toFixed(2)} m²\n`+
          (entry.advice.length?entry.advice.map(a=>'• '+a.text).join('\n'):'暂无命中演示规则的建议，不代表已评估为安全。');
      }).join('\n\n');
    }
    ['colors','object-colors','ceiling','walls','cutaway','labels','mode','assistant-source'].forEach(id => el(id).addEventListener('change',update));
    el('room').addEventListener('change',() => {update();reset();}); el('reset').onclick = reset;
    el('refresh').onclick = async () => {try {predictions = await (await get(manifest.predictions)).json();layoutModel=await optionalAnalysis(manifest.model_layout_analysis); el('error').textContent = ''; update();} catch(e) {el('error').textContent = e.message;}};
    const raycaster = new THREE.Raycaster(); let down = null;
    renderer.domElement.addEventListener('pointerdown', event => {if(event.button===0) down=[event.clientX,event.clientY];});
    renderer.domElement.addEventListener('pointerup', event => {
      if(event.button!==0 || !down) return;
      const moved=Math.hypot(event.clientX-down[0],event.clientY-down[1]);down=null;if(moved>5) return;
      const rect=renderer.domElement.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),camera);
      const selected=el('room').value;
      const hits=raycaster.intersectObjects(pickTargets.filter(target=>selected==='all'||target.userData.roomId===selected));
      if(hits.length){el('room').value=hits[0].object.userData.roomId;update();reset();}
    });
    const resize = () => {renderer.setSize(host.clientWidth,host.clientHeight); camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();};
    window.addEventListener('resize',resize); resize(); reset(); update();
    renderer.setAnimationLoop(() => {
      controls.update();camera.updateMatrixWorld();
      const placed=[];
      overlay.children.filter(child=>child.isSprite).forEach(label=>{
        const depth=-label.position.clone().applyMatrix4(camera.matrixWorldInverse).z;
        label.scale.set(depth*.12,depth*.02,1);
        const p=label.position.clone().project(camera);
        const x=(p.x+1)*host.clientWidth/2, y=(1-p.y)*host.clientHeight/2;
        label.visible=el('labels').checked && depth>0 && Math.abs(p.x)<1 && Math.abs(p.y)<1 && !placed.some(q=>Math.abs(x-q[0])<145 && Math.abs(y-q[1])<28);
        if(label.visible) placed.push([x,y]);
      });
      renderer.render(scene,camera);
    });
    window.__houseDemoReady = true;
  } catch(error) {el('error').textContent = `展示加载失败：${error.message}`; el('status').textContent = '未就绪';}
})();
