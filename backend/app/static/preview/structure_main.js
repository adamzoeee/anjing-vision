/* 独立 2.5D 空间结构视图：只消费 structure.json，不读取或修改稠密点云。 */
(function () {
  'use strict';
  window.__PREVIEW_MODE__ = 'gaussian';
  window.__startStructureViewer__ = async function (manifest, token) {
    var container = document.getElementById('gcontainer');
    container.style.display = 'block';
    document.getElementById('pcanvas-wrap').style.display = 'none';
    document.getElementById('row-point-size').style.display = 'none';
    document.getElementById('btn-mode-points').style.display = 'inline-block';
    document.getElementById('btn-mode-gaussian').style.display = 'none';
    var response = await fetch(manifest.structure, { headers: token ? { Authorization: 'Bearer ' + token } : {} });
    if (!response.ok) throw new Error('空间结构加载失败 HTTP ' + response.status);
    var data = await response.json();
    var measurements = null;
    if (manifest.measurements) {
      var measurementResponse = await fetch(manifest.measurements, { headers: token ? { Authorization: 'Bearer ' + token } : {} });
      if (measurementResponse.ok) measurements = await measurementResponse.json();
    }
    var THREE = window.THREE;
    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0x10141b);
    var renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(container.clientWidth || window.innerWidth, container.clientHeight || window.innerHeight, false);
    container.replaceChildren(renderer.domElement);
    renderer.domElement.style.width = '100%'; renderer.domElement.style.height = '100%';
    var room = data.room || { bounds_xy: { min: [-2, -2], max: [2, 2] }, height_m: 2.6 };
    var min = room.bounds_xy.min, max = room.bounds_xy.max;
    var cx = (min[0] + max[0]) / 2, cy = (min[1] + max[1]) / 2;
    var diag = Math.max(max[0] - min[0], max[1] - min[1], 1);
    var camera = new THREE.PerspectiveCamera(50, 1, 0.01, 100);
    camera.position.set(cx + diag * 0.9, cy - diag * 1.0, room.height_m * 1.15);
    camera.up.set(0, 0, 1); camera.lookAt(cx, cy, room.height_m * 0.4);
    var controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(cx, cy, room.height_m * 0.35); controls.enableDamping = true;
    var group = new THREE.Group(); scene.add(group);
    var groups = { walls: new THREE.Group(), doors: new THREE.Group(), windows: new THREE.Group(), objects: new THREE.Group(), obstacles: new THREE.Group() };
    Object.keys(groups).forEach(function (key) { group.add(groups[key]); });
    var measuredById = {};
    if (measurements) (measurements.objects || []).forEach(function (item) { measuredById[item.id] = item; });
    function box(item, color, fill) {
      // 优先用测量(审计后)的真实尺寸画盒子；结构文件里的旧尺寸只作回退
      var size = item.size || [1, 1, 1];
      var measured = measuredById[item.instance_id];
      if (measured && measured.length_m != null && measured.width_m != null && measured.height_m != null) {
        size = [measured.length_m, measured.width_m, measured.height_m];
      }
      var geo = new THREE.BoxGeometry(size[0], size[1], size[2]);
      var mat = new THREE.MeshBasicMaterial({ color: color, transparent: true, opacity: fill ? 0.18 : 0.0, depthWrite: false });
      var mesh = new THREE.Mesh(geo, mat); mesh.position.fromArray(item.center || [0, 0, 0]);
      mesh.rotation.z = THREE.MathUtils.degToRad(item.rotation_z_deg || 0);
      var edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: color }));
      mesh.add(edges); return mesh;
    }
    function dimensionText(item) {
      var m = measuredById[item.instance_id];
      if (!m || m.length_m == null) return '';
      return ' ' + m.length_m.toFixed(2) + '×' + m.width_m.toFixed(2) + '×' + m.height_m.toFixed(2) + 'm';
    }
    function label(item) {
      var canvas = document.createElement('canvas'); canvas.width = 256; canvas.height = 64;
      var ctx = canvas.getContext('2d');
      var m = measuredById[item.instance_id];
      var name = item.label || item.category || (m && m.type) || 'object';
      var text = name + dimensionText(item);
      ctx.font = 'bold 27px "Microsoft YaHei",sans-serif'; ctx.fillStyle = 'rgba(15,23,42,.88)';
      ctx.fillRect(2, 4, Math.min(250, ctx.measureText(text).width + 24), 54); ctx.fillStyle = '#ffd6de'; ctx.fillText(text, 14, 41);
      var sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), transparent: true, depthTest: false }));
      sprite.scale.set(0.85, 0.21, 1); sprite.position.fromArray(item.center || [0, 0, 0]); sprite.position.z += (item.size || [0, 0, 0])[2] / 2 + 0.08;
      return sprite;
    }
    var floorGeo = new THREE.PlaneGeometry(max[0] - min[0], max[1] - min[1]);
    var floor = new THREE.Mesh(floorGeo, new THREE.MeshBasicMaterial({ color: 0x334155, transparent: true, opacity: 0.32, side: THREE.DoubleSide }));
    floor.position.set(cx, cy, 0); group.add(floor);
    (data.walls || []).forEach(function (x) { groups.walls.add(box(x, 0x4f9cf9, true)); });
    (data.doors || []).forEach(function (x) { groups.doors.add(box(x, 0xffb703, true)); });
    (data.windows || []).forEach(function (x) { groups.windows.add(box(x, 0x5ce98a, true)); });
    // 家具优先画“semantic_instances”（审计/训练后的最终尺寸，与2D结构图一致），
    // 旧字段 objects 只作回退——之前画错了数据源，导致3D视图显示审计前的旧盒子。
    var structureObjects = (data.semantic_instances && data.semantic_instances.length)
      ? data.semantic_instances
      : (data.objects || []);
    structureObjects.forEach(function (x) { groups.objects.add(box(x, 0xff8fa3, true)); groups.objects.add(label(x)); });
    (data.geometric_obstacles || []).forEach(function (x) { groups.obstacles.add(box(x, 0xff7a00, true)); groups.obstacles.add(label(x)); });
    function bind(id, key) { var el = document.getElementById(id); if (el) el.onchange = function () { groups[key].visible = el.checked; }; }
    bind('ck-walls', 'walls'); bind('ck-doors', 'doors'); bind('ck-windows', 'windows'); bind('ck-objects', 'objects');
    var objectToggle = document.getElementById('ck-objects'); if (objectToggle) objectToggle.addEventListener('change', function () { groups.obstacles.visible = objectToggle.checked; });
    var labels = document.getElementById('ck-labels'); if (labels) labels.onchange = function () {
      groups.objects.children.forEach(function (child) { if (child.isSprite) child.visible = labels.checked; });
    };
    var scaleText = measurements && measurements.scale && measurements.scale.scale ? ' · 换算比例 ' + measurements.scale.scale.toFixed(4) + 'm/单位' : ' · 未完成米制标定';
    document.getElementById('scene-sub').textContent = '空间结构 · 墙 ' + (data.counts.walls || 0) + ' · 门 ' + (data.counts.doors || 0) + ' · 窗 ' + (data.counts.windows || 0) + ' · 家具 ' + (data.counts.objects || 0) + ' · 未知障碍 ' + (data.counts.geometric_obstacles || 0) + scaleText;
    if (measurements) {
      var rm = measurements.room || {};
      var validation = ((((measurements || {}).quality || {}).validation) || [])[0];
      document.getElementById('stats').textContent = '房间 ' + [rm.length_m, rm.width_m, rm.height_m].map(function(v){ return v == null ? '?' : v.toFixed(2); }).join(' × ') + 'm' + (validation ? ' | 书桌宽核验 ' + (validation.predicted_m == null ? '未识别' : validation.predicted_m.toFixed(2) + 'm / 实测 ' + validation.meters.toFixed(2) + 'm') : '');
    }
    document.getElementById('overlay').style.display = 'none';
    function resize() { var w = container.clientWidth || innerWidth, h = container.clientHeight || innerHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
    addEventListener('resize', resize); resize();
    (function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
    document.getElementById('btn-reset').onclick = function () { camera.position.set(cx + diag * 0.9, cy - diag * 1.0, room.height_m * 1.15); controls.target.set(cx, cy, room.height_m * 0.35); controls.update(); };
  };
})();
