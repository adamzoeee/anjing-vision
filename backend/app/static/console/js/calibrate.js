/* 大屏"框架校准"页：点云保持原始尺寸，手动调整整个房间框架（房间轮廓 + 墙 +
 * 门窗 + 全部物品框）的旋转 / 缩放 / 平移，直到框贴住点云里的真实物体。
 * 参数含义：p_display = T · S · Rz(yaw) · p_business，S 含镜像符号；
 * 显示系 = 交付包原生系 (pack_x, pack_z, floor - pack_y)。 */
(function () {
  'use strict';
  var SESSION_KEY = 'anjing.console.session';
  var params = new URLSearchParams(location.search);
  var scanId = params.get('scan') || '46';
  var statusEl = document.getElementById('status');
  var logEl = document.getElementById('log');
  var lines = [];
  function log(text) {
    var stamp = new Date().toTimeString().slice(0, 8);
    lines.push('[' + stamp + '] ' + text);
    if (lines.length > 40) lines.shift();
    if (logEl) logEl.value = lines.join('\n');
  }
  function say(text) {
    statusEl.style.display = text === '' ? 'none' : '';
    statusEl.innerHTML = text || '';
    if (text) log(text);
  }
  function fatal(error) {
    var message = (error && error.message) ? error.message : String(error);
    lines.push('[fatal] ' + message);
    if (logEl) logEl.value = lines.join('\n');
    statusEl.style.display = '';
    statusEl.innerHTML = '<b style="color:#ff8080">出错了：</b><br>' + message
      + '<br><br><span style="color:#8ba0c9;font-size:12px">请把左下角"诊断日志"复制给我，或截这张图。</span>';
  }
  window.addEventListener('error', function (event) { fatal(event.message); });
  window.addEventListener('unhandledrejection', function (event) {
    fatal(event.reason && event.reason.message ? event.reason.message : event.reason);
  });
  function token() {
    try { return (JSON.parse(localStorage.getItem(SESSION_KEY) || '{}').token) || ''; } catch (e) { return ''; }
  }
  function headers() { var t = token(); return t ? { Authorization: 'Bearer ' + t } : {}; }
  /* 没有控制台会话时自动走演示登录（本机 DEMO_LOGIN=true），避免卡在登录态上 */
  function ensureSession() {
    var existing = token();
    if (existing) return Promise.resolve(existing);
    log('没有本地会话，尝试演示登录…');
    return fetch('/api/auth/demo-login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(function (res) { return res.ok ? res.json() : Promise.reject(new Error('演示登录 HTTP ' + res.status)); })
      .then(function (auth) {
        localStorage.setItem(SESSION_KEY, JSON.stringify({
          token: auth.token, user: auth.user, expiresAt: auth.expires_at || null, savedAt: Date.now(),
        }));
        log('演示登录成功');
        return auth.token;
      });
  }

  /* ---------- 参数 ---------- */
  var P = { yaw: 180, sx: 0.845, sy: 0.644, sz: 0.9, tx: 1.553, ty: 2.117, tz: 0, mirrorX: false, mirrorY: false };
  function link(key) {
    var slider = document.getElementById(key);
    var num = document.getElementById(key + '-n');
    var apply = function (value) {
      var v = Number(value);
      if (!isFinite(v)) return;
      P[key] = v; slider.value = v; num.value = v.toFixed(3);
      updateMatrix(); scheduleScore();
    };
    slider.addEventListener('input', function () { apply(slider.value); });
    num.addEventListener('change', function () { apply(num.value); });
  }
  ['yaw', 'sx', 'sy', 'sz', 'tx', 'ty', 'tz'].forEach(link);
  [['mirror-x', 'mirrorX'], ['mirror-y', 'mirrorY']].forEach(function (pair) {
    document.getElementById(pair[0]).addEventListener('change', function () {
      P[pair[1]] = this.checked; updateMatrix(); scheduleScore();
    });
  });
  function refreshUI() {
    ['yaw', 'sx', 'sy', 'sz', 'tx', 'ty', 'tz'].forEach(function (key) {
      document.getElementById(key).value = P[key];
      document.getElementById(key + '-n').value = Number(P[key]).toFixed(3);
    });
    document.getElementById('mirror-x').checked = P.mirrorX;
    document.getElementById('mirror-y').checked = P.mirrorY;
    updateMatrix(); scheduleScore();
  }

  /* ---------- three 场景 ---------- */
  var host = document.getElementById('view');
  function createRenderer() {
    // 与大屏查看器同样的兼容策略：逐个尝试上下文，避免个别显卡/嵌入式浏览器拿不到 WebGL
    var attempts = [
      { type: 'webgl2', powerPreference: 'high-performance' },
      { type: 'webgl2', powerPreference: 'default' },
      { type: 'webgl', powerPreference: 'default' },
      { type: 'webgl', powerPreference: 'low-power' },
      { type: 'experimental-webgl', powerPreference: 'default' },
    ];
    var canvas = null, context = null;
    for (var i = 0; i < attempts.length && !context; i++) {
      canvas = document.createElement('canvas');
      try {
        context = canvas.getContext(attempts[i].type, {
          alpha: true, antialias: false, depth: true, stencil: false,
          powerPreference: attempts[i].powerPreference,
          failIfMajorPerformanceCaveat: false,
        });
      } catch (error) { context = null; }
    }
    if (!context) throw new Error('浏览器没能创建 WebGL 上下文（请开启硬件加速后重试）');
    return new THREE.WebGLRenderer({ canvas: canvas, context: context, antialias: false, alpha: true });
  }
  var renderer = createRenderer();
  log('WebGL 上下文已创建');
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.setSize(host.clientWidth || 800, host.clientHeight || 600);
  host.appendChild(renderer.domElement);
  var scene = new THREE.Scene();
  scene.background = new THREE.Color(0x05070d);
  var camera = new THREE.PerspectiveCamera(55, (host.clientWidth || 800) / (host.clientHeight || 600), 0.02, 300);
  var controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  camera.position.set(4, 4, 5);
  var world = new THREE.Group();
  world.rotation.x = -Math.PI / 2;      /* 显示系 z-up → three y-up */
  scene.add(world);
  var cloudGroup = new THREE.Group();
  var visualGroup = new THREE.Group();
  var frameGroup = new THREE.Group();
  world.add(cloudGroup, visualGroup, frameGroup);
  var grid = new THREE.GridHelper(9, 36, 0x2b3f66, 0x16213a);
  grid.rotation.x = Math.PI / 2;
  grid.position.y = 0.001;
  world.add(grid);
  function resize() {
    var w = host.clientWidth || 1, h = host.clientHeight || 1;
    camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h);
  }
  new ResizeObserver(resize).observe(host);
  (function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();

  /* ---------- 结构框 ---------- */
  var STYLE = {
    wall: { color: 0x9b8cff, op: 0.06 },
    door: { color: 0xffc24b, op: 0.16 },
    window: { color: 0x6ee7b7, op: 0.16 },
    object: { color: 0xff9db1, op: 0.06 },
    room: { color: 0x64ffda, op: 0.015 },
  };
  var bucket = { walls: new THREE.Group(), doors: new THREE.Group(), windows: new THREE.Group(), objects: new THREE.Group() };
  Object.keys(bucket).forEach(function (key) { frameGroup.add(bucket[key]); });
  var labelGroup = new THREE.Group();
  frameGroup.add(labelGroup);
  var boxes = [];
  function kindBucket(kind) {
    return kind === 'wall' ? 'walls' : kind === 'door' ? 'doors' : kind === 'window' ? 'windows' : 'objects';
  }
  function makeBox(item, kind) {
    var size = [Math.max(item.size[0], 0.01), Math.max(item.size[1], 0.01), Math.max(item.size[2], 0.01)];
    var geo = new THREE.BoxGeometry(size[0], size[1], size[2]);
    var fill = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color: STYLE[kind].color, transparent: true, opacity: STYLE[kind].op, depthWrite: false,
    }));
    var edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({
      color: STYLE[kind].color, transparent: true, opacity: kind === 'room' ? 1 : 0.92,
    }));
    var holder = new THREE.Group();
    holder.add(fill, edges);
    holder.position.set(item.center[0], item.center[1], item.center[2]);
    holder.rotation.z = THREE.MathUtils.degToRad(item.rotation_z_deg || 0);
    holder.userData.item = item;
    bucket[kindBucket(kind)].add(holder);
    boxes.push({ holder: holder, item: item, kind: kind });
  }
  function makeLabel(item, text) {
    var canvas = document.createElement('canvas');
    canvas.width = 256; canvas.height = 64;
    var ctx = canvas.getContext('2d');
    ctx.font = '600 26px "Microsoft YaHei", sans-serif';
    var width = Math.min(244, ctx.measureText(text).width + 26);
    ctx.fillStyle = 'rgba(10,15,30,0.82)';
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(2, 4, width, 54, 12); ctx.fill(); } else { ctx.fillRect(2, 4, width, 54); }
    ctx.strokeStyle = 'rgba(255,143,163,0.55)'; ctx.lineWidth = 1.5;
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(2, 4, width, 54, 12); ctx.stroke(); }
    ctx.fillStyle = '#ffd6de'; ctx.fillText(text, 14, 41);
    var sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false, transparent: true }));
    sprite.scale.set(0.8, 0.2, 1);
    sprite.position.set(item.center[0], item.center[1], item.center[2] + item.size[2] / 2 + 0.09);
    labelGroup.add(sprite);
  }
  function addStructure(source) {
    (source.walls || []).forEach(function (w) { makeBox(w, 'wall'); });
    (source.doors || []).forEach(function (w) { makeBox(w, 'door'); });
    (source.windows || []).forEach(function (w) { makeBox(w, 'window'); });
    var list = Array.isArray(source.semantic_instances) && source.semantic_instances.length
      ? source.semantic_instances
      : (Array.isArray(source.objects) ? source.objects : []);
    list.forEach(function (o) {
      var item = { center: o.center, size: o.size, rotation_z_deg: o.rotation_z_deg || 0 };
      makeBox(item, 'object');
      makeLabel(item, String(o.instance_id || o.category || 'object'));
    });
    var room = source.room || {};
    var lo = (room.bounds_xy || {}).min, hi = (room.bounds_xy || {}).max;
    if (Array.isArray(lo) && Array.isArray(hi)) {
      makeBox({
        center: [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (room.height_m || 2.7) / 2],
        size: [hi[0] - lo[0], hi[1] - lo[1], room.height_m || 2.7],
        rotation_z_deg: 0,
      }, 'room');
    }
  }

  /* ---------- 整体变换 ---------- */
  var mtx = new THREE.Matrix4();
  var invMtx = new THREE.Matrix4();
  function updateMatrix() {
    var rot = new THREE.Matrix4().makeRotationZ(THREE.MathUtils.degToRad(P.yaw));
    var scl = new THREE.Matrix4().makeScale((P.mirrorX ? -1 : 1) * P.sx, (P.mirrorY ? -1 : 1) * P.sy, P.sz);
    var trn = new THREE.Matrix4().makeTranslation(P.tx, P.ty, P.tz);
    mtx.copy(trn).multiply(scl).multiply(rot);
    invMtx.copy(mtx).invert();
    frameGroup.matrixAutoUpdate = false;
    frameGroup.matrix.copy(mtx);
    frameGroup.matrixWorldNeedsUpdate = true;
  }

  /* ---------- 点云 ---------- */
  var cloudPoints = null, cloudXYZ = null, cloudStats = null;
  function parseHeader(buffer) {
    var bytes = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 262144));
    var text = '';
    for (var i = 0; i < bytes.length; i++) {
      text += String.fromCharCode(bytes[i]);
      if (text.length > 10 && text.slice(-10) === 'end_header') break;
    }
    var lines = text.split('\n');
    var count = 0, props = [], element = null;
    lines.forEach(function (line) {
      var parts = line.trim().split(/\s+/);
      if (parts[0] === 'element') {
        element = parts[1];
        if (element === 'vertex') count = parseInt(parts[2], 10);
      } else if (parts[0] === 'property' && element === 'vertex') {
        props.push({ type: parts[1], name: parts[2] });
      }
    });
    if (!count || !props.length) throw new Error('PLY 头解析失败');
    var sizes = { float: 4, double: 8, uchar: 1, char: 1, short: 2, ushort: 2, int: 4, uint: 4 };
    var stride = 0;
    props.forEach(function (p) { p.offset = stride; stride += sizes[p.type] || 4; });
    return { count: count, props: props, stride: stride, dataStart: text.length + 1 };
  }
  function buildCloud(buffer) {
    var meta = parseHeader(buffer);
    var view = new DataView(buffer, 0, buffer.byteLength);
    var find = function (name) { return meta.props.find(function (p) { return p.name === name; }); };
    var px = find('x'), py = find('y'), pz = find('z'), pr = find('red'), pg = find('green'), pb = find('blue');
    var read = function (prop, base) {
      if (!prop) return 0;
      switch (prop.type) {
        case 'double': return view.getFloat64(base + prop.offset, true);
        case 'float': return view.getFloat32(base + prop.offset, true);
        case 'uchar': return view.getUint8(base + prop.offset);
        case 'char': return view.getInt8(base + prop.offset);
        default: return 0;
      }
    };
    var step = Math.max(1, Math.floor(meta.count / 420000));
    var n = Math.floor(meta.count / step);
    var pos = new Float32Array(n * 3);
    var col = new Float32Array(n * 3);
    var hist = new Uint32Array(320);
    var yMin = Infinity, yMax = -Infinity, i;
    for (i = 0; i < meta.count; i += step) {
      var y = read(py, meta.dataStart + i * meta.stride);
      if (y < yMin) yMin = y;
      if (y > yMax) yMax = y;
    }
    var width = (yMax - yMin) / 320 || 1;
    for (i = 0; i < meta.count; i += step) {
      var yv = read(py, meta.dataStart + i * meta.stride);
      hist[Math.min(319, Math.max(0, Math.floor((yv - yMin) / width)))] += 1;
    }
    var best = 288;
    for (var s = 208; s < 320; s++) if (hist[s] > hist[best]) best = s;
    cloudStats = { floor: yMin + (best + 0.5) * width, sampled: n, total: meta.count };
    var scale = function (prop) { return prop && prop.type !== 'float' && prop.type !== 'double' ? 1 / 255 : 1; };
    for (i = 0; i < n; i++) {
      var base = meta.dataStart + (i * step) * meta.stride;
      pos[i * 3] = read(px, base);
      pos[i * 3 + 1] = read(pz, base);
      pos[i * 3 + 2] = cloudStats.floor - read(py, base);
      col[i * 3] = (pr ? read(pr, base) * scale(pr) : 0.8);
      col[i * 3 + 1] = (pg ? read(pg, base) * scale(pg) : 0.82);
      col[i * 3 + 2] = (pb ? read(pb, base) * scale(pb) : 0.86);
    }
    cloudXYZ = pos;
    log('点云解析完成：抽样 ' + n + ' / ' + meta.count + ' 点，地板面 y=' + cloudStats.floor.toFixed(3));
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    cloudPoints = new THREE.Points(geo, new THREE.PointsMaterial({ size: 0.0175, vertexColors: true, sizeAttenuation: true }));
    cloudPoints.frustumCulled = false;
    cloudGroup.add(cloudPoints);
  }
  function buildVisual(layer) {
    var meshes = (layer && Array.isArray(layer.meshes)) ? layer.meshes : [];
    meshes.forEach(function (mesh) {
      var rows = mesh.positions || [];
      if (!rows.length) return;
      var arr = new Float32Array(rows.length * 3);
      for (var i = 0; i < rows.length; i++) {
        arr[i * 3] = rows[i][0];
        arr[i * 3 + 1] = rows[i][2];
        arr[i * 3 + 2] = (cloudStats ? cloudStats.floor : 1.345) - rows[i][1];
      }
      var geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(arr, 3));
      if (Array.isArray(mesh.faces) && mesh.faces.length) {
        var idx = [];
        mesh.faces.forEach(function (f) { idx.push(f[0], f[1], f[2]); });
        geo.setIndex(idx);
      }
      var colour = mesh.name === 'window' ? 0xcfe8ff : /curtain/.test(mesh.name || '') ? 0x8fd0a6 : 0xffc2d1;
      visualGroup.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
        color: colour, side: THREE.DoubleSide, transparent: true, opacity: 0.85,
      })));
    });
  }

  /* ---------- 贴合检查 ---------- */
  var scoreEl = document.getElementById('score');
  var scoreBody = document.querySelector('#score-table tbody');
  var scoreTimer = null;
  function scheduleScore() {
    if (scoreTimer) clearTimeout(scoreTimer);
    scoreTimer = setTimeout(runScore, 220);
  }
  function runScore() {
    if (!cloudXYZ || !boxes.length) return;
    var furniture = boxes.filter(function (b) { return b.kind === 'object'; });
    var point = new THREE.Vector3();
    var total = 0;
    var rows = '';
    var n = cloudXYZ.length / 3;
    furniture.forEach(function (b) {
      var item = b.item, size = item.size;
      var rot = THREE.MathUtils.degToRad(item.rotation_z_deg || 0);
      var cos = Math.cos(-rot), sin = Math.sin(-rot);
      var hits = 0;
      for (var i = 0; i < n; i += 2) {
        point.set(cloudXYZ[i * 3], cloudXYZ[i * 3 + 1], cloudXYZ[i * 3 + 2]).applyMatrix4(invMtx);
        var dx = point.x - item.center[0];
        var dy = point.y - item.center[1];
        var u = dx * cos - dy * sin;
        var v = dx * sin + dy * cos;
        if (Math.abs(u) <= size[0] / 2 + 0.05 && Math.abs(v) <= size[1] / 2 + 0.05
          && Math.abs(point.z - item.center[2]) <= size[2] / 2 + 0.05) hits += 2;
      }
      total += hits;
      rows += '<tr><td>' + String(item.instance_id || item.category || '') + '</td><td class="num">' + hits + '</td></tr>';
    });
    scoreBody.innerHTML = rows;
    scoreEl.textContent = String(total);
  }

  /* ---------- 预设 ---------- */
  var roomBox = null, cloudBox = null;
  function computeBoxes() {
    if (!cloudXYZ) return;
    var mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    for (var i = 0; i < cloudXYZ.length; i += 3) {
      for (var a = 0; a < 3; a++) {
        var value = cloudXYZ[i + a];
        if (value < mn[a]) mn[a] = value;
        if (value > mx[a]) mx[a] = value;
      }
    }
    cloudBox = { min: mn, max: mx };
    boxes.filter(function (b) { return b.kind === 'room'; }).forEach(function (b) {
      var c = b.item.center, s = b.item.size;
      roomBox = { min: [c[0] - s[0] / 2, c[1] - s[1] / 2, 0], max: [c[0] + s[0] / 2, c[1] + s[1] / 2, s[2]] };
    });
  }
  /* 点云原生系里的墙位（“墙对齐”预设用）。scan46 实测：
   * x 两面墙 ≈ -0.483 / 1.962；窗墙内侧 ≈ -0.265；门墙 ≈ 2.005。 */
  var wallRef = null;
  function measureWalls() {
    if (!cloudXYZ || wallRef) return wallRef;
    var n = cloudXYZ.length / 3;
    var xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
    for (var i = 0; i < n; i++) {
      var x = cloudXYZ[i * 3], y = cloudXYZ[i * 3 + 1];
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
      if (y < yMin) yMin = y;
      if (y > yMax) yMax = y;
    }
    var strongest = function (axis, from, to, bins) {
      var base = axis === 0 ? xMin : yMin;
      var span = (axis === 0 ? xMax - xMin : yMax - yMin) || 1;
      var lo = base + span * from, hi = base + span * to;
      var width = (hi - lo) / bins || 1;
      var hist = new Uint32Array(bins);
      for (var j = 0; j < n; j++) {
        var v = cloudXYZ[j * 3 + axis];
        if (v < lo || v > hi) continue;
        hist[Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / width)))] += 1;
      }
      var best = 0;
      for (var b = 1; b < bins; b++) if (hist[b] > hist[best]) best = b;
      return lo + (best + 0.5) * width;
    };
    wallRef = {
      xLo: strongest(0, 0, 0.25, 120),
      xHi: strongest(0, 0.75, 1, 120),
      yWindow: strongest(1, 0, 0.3, 160),
      yDoor: strongest(1, 0.7, 1, 160),
    };
    return wallRef;
  }
  function applyPreset(kind) {
    computeBoxes();
    if (!roomBox || !cloudBox) return;
    var note = document.getElementById('preset-note');
    var spanX = cloudBox.max[0] - cloudBox.min[0];
    var spanY = cloudBox.max[1] - cloudBox.min[1];
    var roomX = roomBox.max[0] - roomBox.min[0];
    var roomY = roomBox.max[1] - roomBox.min[1];
    P.yaw = 180; P.mirrorX = false; P.mirrorY = false; P.tz = 0;
    P.sx = spanX / roomX;
    P.sy = spanY / roomY;
    if (kind === 'native') {
      P.sx = 1; P.sy = 1; P.sz = 1;
      note.textContent = '原生 1.0：点云原始尺寸，只做 180° 摆放（框会偏大或偏小，作为对照）。';
    } else if (kind === 'bbox') {
      P.sz = 0.9;
      note.textContent = '等比例：房间外框缩到点云外接框（≈ 大屏"之前"的口径）。';
    } else {
      var w = measureWalls();
      /* x：房间西墙 ↔ 点云 x 大端墙；y：房间南墙（门墙）↔ 点云门墙平面 */
      P.sx = (w.xHi - w.xLo) / roomX;
      P.sy = (w.yDoor - w.yWindow) / roomY;
      P.sz = 0.9;
      P.tx = w.xHi + P.sx * roomBox.min[0];
      P.ty = w.yDoor + P.sy * roomBox.min[1];
      note.textContent = '墙对齐：X 按两面墙间距、Y 按门窗墙间距缩放（scan46 约 0.85 / 0.69）。先从这里微调。';
      refreshUI();
      return;
    }
    /* 180° 摆放：房间最小角 → 点云最大角（display = T + S·Rz(180°)·business） */
    P.tx = cloudBox.max[0] + P.sx * roomBox.min[0];
    P.ty = cloudBox.max[1] + P.sy * roomBox.min[1];
    refreshUI();
  }
  document.getElementById('preset-walls').onclick = function () { applyPreset('walls'); };
  document.getElementById('preset-bbox').onclick = function () { applyPreset('bbox'); };
  document.getElementById('preset-native').onclick = function () { applyPreset('native'); };

  /* ---------- 视角 ---------- */
  function fittedTarget() {
    var c = roomBox
      ? [(roomBox.min[0] + roomBox.max[0]) / 2, (roomBox.min[1] + roomBox.max[1]) / 2]
      : [1, 1];
    return new THREE.Vector3(c[0], roomBox ? roomBox.max[2] * 0.45 : 1.2, -c[1]);
  }
  /* OrbitControls 会在构造时缓存 up 轴，切换俯视/正视必须重建控制器，否则视角不生效 */
  function applyView(up, position, target) {
    if (controls && controls.dispose) controls.dispose();
    camera.up.copy(up);
    camera.position.copy(position);
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.copy(target);
    controls.update();
  }
  function fitView() {
    var target = fittedTarget();
    var span = 4.2;
    applyView(new THREE.Vector3(0, 1, 0),
      new THREE.Vector3(target.x + span * 0.62, target.y + span * 0.78, target.z - span * 1.05), target);
  }
  function topView() {
    var target = controls.target.clone();
    applyView(new THREE.Vector3(0, 0, -1),
      new THREE.Vector3(target.x, target.y + 7, target.z + 0.001), target);
  }
  function frontView() {
    var target = controls.target.clone();
    applyView(new THREE.Vector3(0, 1, 0),
      new THREE.Vector3(target.x, target.y + 1.3, target.z - 7), target);
  }
  document.getElementById('view-top').onclick = topView;
  document.getElementById('view-front').onclick = frontView;
  document.getElementById('view-iso').onclick = fitView;
  document.getElementById('view-reset').onclick = fitView;

  /* ---------- 显示开关 ---------- */
  document.getElementById('show-cloud').onchange = function () { cloudGroup.visible = this.checked; };
  document.getElementById('show-boxes').onchange = function () { frameGroup.visible = this.checked; };
  document.getElementById('show-labels').onchange = function () { labelGroup.visible = this.checked; };
  document.getElementById('show-visual').onchange = function () { visualGroup.visible = this.checked; };
  var psize = document.getElementById('psize');
  function applyPointSize() {
    if (cloudPoints) cloudPoints.material.size = 0.0175 * Number(psize.value);
    document.getElementById('psize-n').value = Number(psize.value).toFixed(2);
  }
  psize.addEventListener('input', applyPointSize);
  document.getElementById('psize-n').addEventListener('change', function () { psize.value = this.value; applyPointSize(); });

  /* ---------- 保存 ---------- */
  function payload() {
    return {
      source: 'scan46-frame-calibration',
      scan_id: Number(scanId),
      created_at: new Date().toISOString(),
      note: 'p_display = T · S · Rz(yaw) · p_business；显示系为交付包原生系 (pack_x, pack_z, floor - pack_y)',
      transform: {
        yaw_deg: P.yaw,
        mirror_x: !!P.mirrorX,
        mirror_y: !!P.mirrorY,
        scale_x: P.sx,
        scale_y: P.sy,
        scale_z: P.sz,
        translate_x: P.tx,
        translate_y: P.ty,
        translate_z: P.tz,
      },
      cloud_frame: {
        floor_y_pack: cloudStats ? cloudStats.floor : null,
        sampled_points: cloudStats ? cloudStats.sampled : null,
        source_points: cloudStats ? cloudStats.total : null,
      },
      room_box: roomBox,
      cloud_box: cloudBox,
      score: scoreEl.textContent,
    };
  }
  function refreshOut() { document.getElementById('out').value = JSON.stringify(payload(), null, 2); }
  setInterval(refreshOut, 600);
  document.getElementById('save').onclick = function () {
    refreshOut();
    var blob = new Blob([JSON.stringify(payload(), null, 2)], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'scan46-frame-calibration.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    say('已下载 scan46-frame-calibration.json，把它发给我即可。');
    setTimeout(function () { say(''); }, 4000);
  };
  document.getElementById('copy').onclick = function () {
    refreshOut();
    var area = document.getElementById('out');
    area.select();
    document.execCommand('copy');
    say('参数已复制到剪贴板。');
    setTimeout(function () { say(''); }, 2500);
  };
  document.getElementById('diag').onclick = function () {
    var text = '校准页诊断\n' + lines.join('\n');
    var area = document.getElementById('log');
    if (area) { area.value = text; area.style.display = ''; area.select(); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { say('诊断日志已复制。'); }, function () { say('日志在上面的文本框里，请手动复制。'); });
    } else {
      document.execCommand('copy');
      say('诊断日志已复制。');
    }
    setTimeout(function () { say(''); }, 2500);
  };
  document.getElementById('reload').onclick = function () { location.reload(); };

  /* ---------- 加载 ---------- */
  function load() {
    if (typeof THREE === 'undefined') { fatal(new Error('three.js 没有加载（/preview-static/three.min.js）')); return; }
    say('准备会话…');
    ensureSession()
      .then(function () {
        say('读取清单…');
        return fetch('/api/preview/' + scanId + '/manifest.json', { headers: headers() });
      })
      .then(function (res) {
        if (!res.ok) throw new Error('清单 HTTP ' + res.status);
        return res.json();
      })
      .then(function (manifest) {
        var structureUrl = manifest.structure || manifest.layout;
        say('读取结构…');
        return fetch(structureUrl, { headers: headers() })
          .then(function (res) { return res.json(); })
          .then(function (structure) {
            addStructure(structure);
            var jobs = [];
            if (manifest.ply) {
              say('下载点云…');
              jobs.push(fetch(manifest.ply, { headers: headers() })
                .then(function (res) { return res.arrayBuffer(); })
                .then(function (buf) {
                  say('解析点云…');
                  return new Promise(function (resolve) {
                    setTimeout(function () {
                      try { buildCloud(buf); resolve(); } catch (error) { fatal(error); }
                    }, 0);
                  });
                }));
            }
            if (manifest.window_visual) {
              jobs.push(fetch(manifest.window_visual, { headers: headers() })
                .then(function (res) { return res.ok ? res.json() : null; })
                .then(function (layer) { if (layer) buildVisual(layer); }));
            }
            return Promise.all(jobs);
          });
      })
      .then(function () {
        say('');
        applyPreset('walls');
        fitView();
        scheduleScore();
        setTimeout(topView, 900);
      })
      .catch(function (error) { say('加载失败：' + (error && error.message ? error.message : error)); });
  }
  load();

  /* 没登录时的兜底：本机开着演示登录，点一下就能拿到会话（与控制台同一个 localStorage key） */
  function showLoginHint() {
    statusEl.style.display = '';
    if (params.get('demo') === '1') {
      statusEl.innerHTML = '正在以演示账号登录…';
      demoLogin();
      return;
    }
    statusEl.innerHTML = '当前浏览器没有控制台登录态。<br>可以先 <a href="/console" style="color:#7dd3fc">打开控制台</a> 登录，'
      + '或直接 <button id="demo-login" class="primary" style="margin-top:8px">一键进入演示并继续</button>';
    var btn = document.getElementById('demo-login');
    btn.onclick = demoLogin;
  }
  function demoLogin() {
    fetch('/api/auth/demo-login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(function (res) { return res.ok ? res.json() : Promise.reject(new Error('HTTP ' + res.status)); })
      .then(function (auth) {
        localStorage.setItem(SESSION_KEY, JSON.stringify({
          token: auth.token, user: auth.user, expiresAt: auth.expires_at || null, savedAt: Date.now(),
        }));
        location.reload();
      })
      .catch(function (error) { fatal(error); });
  }
})();
