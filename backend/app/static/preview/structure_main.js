/* 纯 2.5D 空间结构视图：只读取 structure.json / measurements.json。
 * 不读取点云，不导入 GS3D，不加载 Gaussian。 */
(function () {
  'use strict';
  var renderer, controls, rafId;
  function $(id) { return document.getElementById(id); }
  function dispose() {
    if (rafId) cancelAnimationFrame(rafId);
    if (controls) controls.dispose();
    if (renderer) {
      renderer.dispose();
      if (renderer.forceContextLoss) renderer.forceContextLoss();
    }
  }
  window.__startStructureViewer__ = async function (manifest, token) {
    var headers = token ? { Authorization: 'Bearer ' + token } : {};
    var response = await fetch(manifest.structure, { headers: headers });
    if (!response.ok) throw new Error('空间结构加载失败 HTTP ' + response.status);
    var data = await response.json();
    var measurements = null;
    if (manifest.measurements) {
      var mResponse = await fetch(manifest.measurements, { headers: headers });
      if (mResponse.ok) measurements = await mResponse.json();
    }

    $('pcanvas-wrap').style.display = 'none';
    var container = $('gcontainer');
    container.style.display = 'block';
    var THREE = window.THREE;
    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0x10141b);
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    container.replaceChildren(renderer.domElement);

    var room = data.room || {};
    var bounds = room.bounds_xy || { min: [-2, -2], max: [2, 2] };
    var min = bounds.min || [-2, -2], max = bounds.max || [2, 2];
    var roomHeight = Number(room.height_m) || 2.6;
    var cx = (min[0] + max[0]) / 2, cy = (min[1] + max[1]) / 2;
    var diag = Math.max(max[0] - min[0], max[1] - min[1], 1);
    var camera = new THREE.PerspectiveCamera(50, 1, 0.01, Math.max(100, diag * 20));
    camera.up.set(0, 0, 1);
    camera.position.set(cx + diag * 0.9, cy - diag, roomHeight * 1.25);
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(cx, cy, roomHeight * 0.35);
    controls.enableDamping = true;

    var groups = {
      walls: new THREE.Group(), doors: new THREE.Group(), windows: new THREE.Group(),
      objects: new THREE.Group(), obstacles: new THREE.Group(), labels: new THREE.Group()
    };
    Object.keys(groups).forEach(function (key) { scene.add(groups[key]); });

    function addBox(item, color, opacity) {
      var size = item.size || [1, 1, 1];
      if (size.length !== 3 || size.some(function (v) { return !isFinite(v) || v <= 0; })) return null;
      var geometry = new THREE.BoxGeometry(size[0], size[1], size[2]);
      var mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
        color: color, transparent: true, opacity: opacity, depthWrite: false, side: THREE.DoubleSide
      }));
      mesh.position.fromArray(item.center || [0, 0, 0]);
      mesh.rotation.z = THREE.MathUtils.degToRad(item.rotation_z_deg || 0);
      mesh.add(new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry),
        new THREE.LineBasicMaterial({ color: color, transparent: true, opacity: 0.95 })
      ));
      return mesh;
    }
    function displayName(item) {
      var names = { bed: '床', desk: '书桌', table: '桌子', wardrobe: '衣柜', cabinet: '柜子',
        bookshelf: '书架', chair: '椅子', stool: '凳子', sofa: '沙发', box: '箱子',
        storage_rack: '小收纳架' };
      var key = item.normalized_label || item.label || item.category;
      return names[key] || key || '物体';
    }
    function dimensionText(item) {
      if (!measurements || !measurements.metric_scale_available) return '';
      var size = item.size || [];
      if (size.length !== 3) return '';
      var horizontal = [Number(size[0]), Number(size[1])].sort(function (a, b) { return b - a; });
      return ' ' + horizontal[0].toFixed(2) + '×' + horizontal[1].toFixed(2) + '×' + Number(size[2]).toFixed(2) + 'm';
    }
    function addLabel(item, color) {
      var canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 72;
      var ctx = canvas.getContext('2d'); var text = displayName(item) + dimensionText(item);
      ctx.font = 'bold 28px "Microsoft YaHei",sans-serif';
      ctx.fillStyle = 'rgba(15,23,42,.90)'; ctx.fillRect(2, 5, Math.min(506, ctx.measureText(text).width + 28), 60);
      ctx.fillStyle = color; ctx.fillText(text, 15, 45);
      var sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), transparent: true, depthTest: false }));
      sprite.scale.set(1.55, 0.22, 1); sprite.position.fromArray(item.center || [0, 0, 0]);
      sprite.position.z += Number((item.size || [0, 0, 0])[2] || 0) / 2 + 0.08;
      groups.labels.add(sprite);
    }

    var floor = new THREE.Mesh(
      new THREE.PlaneGeometry(Math.max(max[0] - min[0], 0.1), Math.max(max[1] - min[1], 0.1)),
      new THREE.MeshBasicMaterial({ color: 0x334155, transparent: true, opacity: 0.38, side: THREE.DoubleSide })
    );
    floor.position.set(cx, cy, 0); scene.add(floor);
    (data.walls || []).forEach(function (item) { var x = addBox(item, 0x4f9cf9, 0.12); if (x) groups.walls.add(x); });
    (data.doors || []).forEach(function (item) { var x = addBox(item, 0xffb703, 0.25); if (x) groups.doors.add(x); });
    (data.windows || []).forEach(function (item) { var x = addBox(item, 0x5ce98a, 0.22); if (x) groups.windows.add(x); });
    // 正式结构图只展示“多视角语义实例 + 可信几何”。SpatialLM 单独给出的
    // 候选框不能因为点数多就冒充已验证家具。
    var formalObjects = (data.semantic_instances || []).filter(function (item) {
      var semanticReliable = ['high', 'medium'].includes(item.semantic_confidence) ||
        (item.status === 'stable' && item.semantic_label);
      return item.status === 'stable' && item.geometry_status === 'verified' &&
        item.measurement_ready === true && semanticReliable;
    });
    // 新实例管线存在时绝不能再叠加旧 SpatialLM 对象。两套管线的 instance_id
    // 命名不同（bed_001 / bed_01），按 id 去重会把同一张床画两遍。
    // 只有旧扫描完全没有 semantic_instances 字段时才允许兼容旧对象。
    if (!Array.isArray(data.semantic_instances)) {
      formalObjects = (data.objects || []).filter(function (item) {
        return item.geometry_status === 'verified' &&
          ['high', 'medium'].includes(String(item.semantic_confidence || '').toLowerCase());
      });
    }
    formalObjects.forEach(function (item) { var x = addBox(item, 0xff8fa3, 0.22); if (x) groups.objects.add(x); addLabel(item, '#ffd6de'); });
    function footprintOverlap(a, b) {
      var ac = a.center || [], as = a.size || [], bc = b.center || [], bs = b.size || [];
      if (ac.length < 2 || as.length < 2 || bc.length < 2 || bs.length < 2) return 0;
      var ix = Math.max(0, Math.min(ac[0] + as[0] / 2, bc[0] + bs[0] / 2) -
        Math.max(ac[0] - as[0] / 2, bc[0] - bs[0] / 2));
      var iy = Math.max(0, Math.min(ac[1] + as[1] / 2, bc[1] + bs[1] / 2) -
        Math.max(ac[1] - as[1] / 2, bc[1] - bs[1] / 2));
      return (ix * iy) / Math.max(Math.min(as[0] * as[1], bs[0] * bs[1]), 1e-6);
    }
    // 未知障碍只保留真正从地面开始、且不与已知家具/门洞重叠的占用体。
    // 离地墙片、家具残片不能作为通道障碍显示。
    var displayedObstacles = (data.geometric_obstacles || []).filter(function (item) {
      var size = item.size || [], range = item.height_range_m || [];
      var bottom = range.length >= 2 ? Number(range[0]) : Number((item.center || [0, 0, 0])[2]) - Number(size[2] || 0) / 2;
      var sane = size.length === 3 && size.every(function (v) { return isFinite(v) && v > 0; }) &&
        bottom <= 0.18 && size[2] <= 1.20 && size[0] * size[1] <= 1.50;
      return sane && !formalObjects.some(function (known) { return footprintOverlap(item, known) > 0.20; }) &&
        !(data.doors || []).some(function (opening) { return footprintOverlap(item, opening) > 0.10; });
    });
    displayedObstacles.forEach(function (item) { var x = addBox(item, 0xff7a00, 0.28); if (x) groups.obstacles.add(x); addLabel(item, '#ffd199'); });

    function bind(id, key) { var el = $(id); if (el) el.onchange = function () { groups[key].visible = el.checked; }; }
    bind('ck-walls', 'walls'); bind('ck-doors', 'doors'); bind('ck-windows', 'windows');
    bind('ck-objects', 'objects'); bind('ck-obstacles', 'obstacles'); bind('ck-labels', 'labels');
    $('btn-reset').onclick = function () {
      camera.position.set(cx + diag * 0.9, cy - diag, roomHeight * 1.25);
      controls.target.set(cx, cy, roomHeight * 0.35); controls.update();
    };
    $('btn-shot').onclick = function () {
      renderer.render(scene, camera);
      var link = document.createElement('a'); link.download = 'structure.png';
      link.href = renderer.domElement.toDataURL('image/png'); link.click();
    };
    var counts = data.counts || {};
    $('scene-sub').textContent = '空间结构（无 Gaussian） · 墙 ' + (counts.walls || 0) + ' · 门 ' + (counts.doors || 0) +
      ' · 窗 ' + (data.windows || []).length + ' · 家具 ' + formalObjects.length + ' · 障碍 ' + displayedObstacles.length;
    var scale = measurements && measurements.scale || {};
    $('stats').textContent = measurements && measurements.metric_scale_available
      ? (scale.forced_estimate ? '尺寸：单参考估算（低置信度）' : '尺寸：多参考米制标定')
      : '尺寸：当前未完成米制标定';
    $('overlay').style.display = 'none';

    function resize() {
      var w = container.clientWidth || innerWidth, h = container.clientHeight || innerHeight;
      renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    }
    addEventListener('resize', resize); resize();
    (function loop() { rafId = requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
  };
  addEventListener('pagehide', dispose);
  addEventListener('beforeunload', dispose);
})();
