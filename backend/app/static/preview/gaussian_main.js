/* 安龄智境 - Gaussian 连续场景预览（默认）+ 几何点云调试模式切换
 * Gaussian 场景：GS3D 渲染 gaussian.ply（z-up 米制，与 SpatialLM 框同坐标系）
 * 结构框：独立透明 overlay canvas 同步 GS3D 相机，叠加墙/门/窗/家具框与标签 */
(function () {
  'use strict';
  // 先于 viewer.js 声明模式，防止点云查看器自动启动造成双加载
  window.__PREVIEW_MODE__ = 'gaussian'; // 仅用于阻止点云脚本抢先自启动；正式默认是 structure。

  var params = new URLSearchParams(location.search);
  var scanId = params.get('scan');
  var requestedMode = params.get('mode') || '';
  var token = params.get('token') || '';
  var manifestUrl = scanId ? '/api/preview/' + encodeURIComponent(scanId) + '/manifest.json' : null;
  var authHeaders = token ? { Authorization: 'Bearer ' + token } : {};

  var gaussianViewer = null;
  var overlayState = null;
  var started = false;

  function $(id) { return document.getElementById(id); }
  function setStatus(text, frac) {
    var el = $('overlay-status');
    if (el) el.textContent = text;
    if (typeof frac === 'number') $('bar').style.width = Math.round(frac * 100) + '%';
  }
  function showOverlay() { $('overlay').style.display = 'flex'; }
  function hideOverlay() { $('overlay').style.display = 'none'; }
  function fail(message) {
    $('overlay-status').style.display = 'none';
    $('error').style.display = 'block';
    $('error').textContent = message;
    $('bar-outer').style.display = 'none';
  }

  /* ---------------- 结构框 overlay（z-up，与数据同坐标系） ---------------- */
  var KIND_STYLE = {
    wall: 0x4f9cf9, door: 0xffb703, window: 0x5ce98a, object: 0xff8fa3,
  };
  function makeBox(item, kind) {
    var style = KIND_STYLE[kind] || 0xffffff;
    var size = new THREE.Vector3(item.size[0], item.size[1], item.size[2]);
    var geometry = new THREE.BoxGeometry(size.x, size.y, size.z);
    var edges = new THREE.EdgesGeometry(geometry);
    var line = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: style, transparent: true, opacity: 0.95 }));
    line.position.set(item.center[0], item.center[1], item.center[2]);
    line.rotation.z = THREE.MathUtils.degToRad(item.rotation_z_deg || 0);
    return line;
  }
  function makeLabel(item) {
    var canvas = document.createElement('canvas');
    canvas.width = 256; canvas.height = 64;
    var ctx = canvas.getContext('2d');
    ctx.font = 'bold 28px "Microsoft YaHei", sans-serif';
    ctx.fillStyle = 'rgba(20,24,32,0.85)';
    var text = item.category || 'object';
    var width = ctx.measureText(text).width + 24;
    if (ctx.roundRect) ctx.roundRect(4, 4, width, 56, 10); else ctx.fillRect(4, 4, width, 56);
    ctx.fill();
    ctx.fillStyle = '#ffd6de';
    ctx.fillText(text, 16, 42);
    var texture = new THREE.CanvasTexture(canvas);
    var sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true }));
    sprite.scale.set(0.9, 0.225, 1);
    sprite.position.set(item.center[0], item.center[1], item.center[2] + item.size[2] / 2 + 0.08);
    return sprite;
  }

  function initOverlay(layout) {
    var container = $('gcontainer');
    var mainCanvas = container.querySelector('canvas');
    var ovCanvas = document.createElement('canvas');
    ovCanvas.style.position = 'absolute';
    ovCanvas.style.inset = '0';
    ovCanvas.style.pointerEvents = 'none';
    container.appendChild(ovCanvas);
    var renderer = new THREE.WebGLRenderer({ canvas: ovCanvas, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    function resize() {
      var w = container.clientWidth || window.innerWidth;
      var h = container.clientHeight || window.innerHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    var camera = new THREE.PerspectiveCamera(55, 1, 0.01, 500);
    var scene = new THREE.Scene();
    var groups = { walls: new THREE.Group(), doors: new THREE.Group(), windows: new THREE.Group(), objects: new THREE.Group() };
    var labelGroup = new THREE.Group();
    (layout.walls || []).forEach(function (w) { groups.walls.add(makeBox(w, 'wall')); });
    (layout.doors || []).forEach(function (d) { groups.doors.add(makeBox(d, 'door')); });
    (layout.windows || []).forEach(function (w) { groups.windows.add(makeBox(w, 'window')); });
    (layout.objects || []).forEach(function (o) { groups.objects.add(makeBox(o, 'object')); labelGroup.add(makeLabel(o)); });
    Object.keys(groups).forEach(function (k) { scene.add(groups[k]); });
    scene.add(labelGroup);
    window.addEventListener('resize', resize);
    resize();

    function syncLoop() {
      requestAnimationFrame(syncLoop);
      if (!gaussianViewer || !gaussianViewer.camera) return;
      var cam = gaussianViewer.camera;
      camera.position.copy(cam.position);
      camera.quaternion.copy(cam.quaternion);
      camera.projectionMatrix.copy(cam.projectionMatrix);
      camera.matrixWorld.copy(cam.matrixWorld);
      camera.matrixWorldInverse.copy(cam.matrixWorldInverse);
      renderer.render(scene, camera);
    }
    syncLoop();

    var layerState = { walls: true, doors: true, windows: true, objects: true, labels: true };
    function bind(key, ckId) {
      var el = $(ckId);
      if (el) el.addEventListener('change', function () {
        layerState[key] = el.checked;
        if (groups[key]) groups[key].visible = el.checked;
        if (key === 'labels') labelGroup.visible = el.checked;
      });
    }
    bind('walls', 'ck-walls'); bind('doors', 'ck-doors'); bind('windows', 'ck-windows');
    bind('objects', 'ck-objects'); bind('labels', 'ck-labels');
    overlayState = { groups: groups, labelGroup: labelGroup, camera: camera, renderer: renderer };
  }

  /* ---------------- Gaussian 模式 ---------------- */
  async function startGaussian(manifest) {
    setStatus('初始化 Gaussian 渲染器', 0.1);
    var GS3D = await import('/preview-static/gs3d.module.js');
    var ext = (manifest.alignment && manifest.alignment.extents_m) || { x: [-2, 2], y: [-2, 2], z: [0, 3] };
    var cx = (ext.x[0] + ext.x[1]) / 2, cy = (ext.y[0] + ext.y[1]) / 2, cz = (ext.z[0] + ext.z[1]) / 2;
    var diag = Math.max(ext.x[1] - ext.x[0], ext.y[1] - ext.y[0], 0.5);
    var eye = [cx + diag * 0.55, cy + diag * 0.75, cz + diag * 0.85];
    var target = [cx, cy, cz];

    var container = $('gcontainer');
    container.style.display = 'block';
    $('canvas').parentElement.style.display = 'none';
    $('pcanvas-wrap').style.display = 'none';
    $('row-point-size').style.display = 'none';
    $('btn-mode-gaussian').style.display = 'none';
    $('btn-mode-points').style.display = 'inline-block';
    $('btn-reset').addEventListener('click', function () {
      if (gaussianViewer) {
        gaussianViewer.camera.position.set(eye[0], eye[1], eye[2]);
        gaussianViewer.camera.lookAt(target[0], target[1], target[2]);
        gaussianViewer.render();
      }
    });

    setStatus('加载 Gaussian 场景', 0.15);
    gaussianViewer = new GS3D.Viewer({
      rootElement: container,
      cameraUp: [0, 0, 1],
      initialCameraPosition: eye,
      initialCameraLookAt: target,
      selfDrivenMode: true,
      renderMode: GS3D.RenderMode.OnChange,
      // localhost 页面没有 COOP/COEP，不能让排序 Worker 使用 SharedArrayBuffer。
      // 显式使用可移植的普通 ArrayBuffer 通信，避免 Worker 永远不 ready。
      sharedMemoryForWorkers: false,
      enableSIMDInSort: false,
      freeIntermediateSplatData: true,
    });
    var url = manifest.gaussian_ply + (token ? '' : '');
    await gaussianViewer.addSplatScene(url, {
      format: url.toLowerCase().endsWith('.splat') ? GS3D.SceneFormat.Splat : GS3D.SceneFormat.Ply,
      progressiveLoad: true,
      splatAlphaRemovalThreshold: 20,
      showLoadingUI: false,
      headers: authHeaders,
      onProgress: function (percent, label, loaderStatus) {
        if (loaderStatus === 0) {
          setStatus('下载 Gaussian 场景 ' + (label || Math.round(percent) + '%'), 0.15 + percent * 0.0045);
        } else if (loaderStatus === 1) {
          setStatus('解析 Gaussian 场景', 0.62);
        }
      },
    });
    setStatus('渲染 Gaussian 场景', 0.7);
    gaussianViewer.start();
    started = true;

    if (manifest.layout) {
      setStatus('加载空间结构框', 0.85);
      var resp = await fetch(manifest.layout, { headers: authHeaders });
      if (resp.ok) {
        var layout = await resp.json();
        initOverlay(layout);
        var counts = layout.counts || {};
        $('scene-sub').textContent = 'Gaussian 场景 · 墙 ' + (counts.walls || 0) + ' · 门 ' + (counts.doors || 0) +
          ' · 窗 ' + (counts.windows || 0) + ' · 家具 ' + (counts.objects || 0);
      }
    }
    hideOverlay();
  }

  /* ---------------- 点云调试模式 ---------------- */
  function startPoints() {
    $('gcontainer').style.display = 'none';
    if (gaussianViewer) {
      try { gaussianViewer.stop(); } catch (e) { /* noop */ }
    }
    $('canvas').parentElement.style.display = 'block';
    $('pcanvas-wrap').style.display = 'block';
    $('row-point-size').style.display = 'flex';
    $('btn-mode-gaussian').style.display = 'inline-block';
    $('btn-mode-points').style.display = 'none';
    window.__PREVIEW_MODE__ = 'points';
    showOverlay();
    setStatus('加载真实场景（稠密点云）', 0.05);
    window.__startPointsViewer__();
  }

  /* ---------------- 主流程 ---------------- */
  async function main() {
    if (!manifestUrl) { fail('缺少 scan 参数'); return; }
    $('btn-mode-points').addEventListener('click', startPoints);
    $('btn-mode-gaussian').addEventListener('click', function () {
      // 强制带时间戳重载：同 URL 直接赋值在浏览器里是空操作，会导致“点了没反应”
      var next = new URL(location.href);
      next.searchParams.delete('mode');
      next.searchParams.set('_t', String(Date.now()));
      location.href = next.toString();
    });
    if (requestedMode === 'points') {
      startPoints();
      return;
    }
    try {
      setStatus('获取场景清单', 0.02);
      var resp = await fetch(manifestUrl, { headers: authHeaders });
      if (!resp.ok) throw new Error('清单加载失败 HTTP ' + resp.status + '（请确认已登录）');
      var manifest = await resp.json();
      $('scene-title').textContent = manifest.name || ('扫描 #' + scanId);
      if (manifest.structure && window.__startStructureViewer__) {
        setStatus('加载空间结构', 0.35);
        await window.__startStructureViewer__(manifest, token);
        return;
      }
      if (manifest.gaussian_ply) {
        await startGaussian(manifest);
      } else {
        startPoints();
      }
    } catch (error) {
      fail(error && error.message ? error.message : String(error));
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', main);
  } else {
    main();
  }
})();
