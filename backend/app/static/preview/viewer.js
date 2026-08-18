/* 安龄智境 - 高密度稠密点云 3D 预览
 * 渐进式分块加载（连续化显示）：大 PLY 按 20 万点/块流式解析并逐块上屏，
 * 配合 SpatialLM 的墙/门/窗/家具 3D 框叠加显示。 */
(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var scanId = params.get('scan');
  // 兼容旧 Flutter Web 构建使用的 /preview/?ply=/static/<id>/... 链接。
  // 历史版本还可能把 /static/... 与 /api/preview/... 错误拼接；只从中
  // 提取 scan id，实际数据统一通过当前受鉴权的 manifest API 加载。
  if (!scanId) {
    var legacyPly = params.get('ply') || '';
    var legacyMatch = legacyPly.match(/(?:\/api\/preview\/|\/static\/)(\d+)(?:\/|$)/);
    if (legacyMatch) scanId = legacyMatch[1];
  }
  var token = params.get('token') || '';
  var manifestUrl = scanId ? '/api/preview/' + encodeURIComponent(scanId) + '/manifest.json' : null;

  var scene, camera, renderer, controls, worldGroup;
  var pcdGroup, boxesGroup;
  var layerState = { pcd: true, walls: true, doors: true, windows: true, objects: true, labels: true };
  var boxGroups = { walls: null, doors: null, windows: null, objects: null };
  var labelGroup = null;
  var pointMaterial = null;
  var rafId = null;
  var resizeHandler = null;
  var sceneDisposed = false;

  function $(id) { return document.getElementById(id); }
  function setProgress(fraction, text) {
    $('bar').style.width = Math.round(fraction * 100) + '%';
    if (text) $('overlay-status').textContent = text;
  }
  function fail(message) {
    $('overlay-status').style.display = 'none';
    $('error').style.display = 'block';
    $('error').textContent = message;
    $('bar-outer').style.display = 'none';
    $('btn-retry').style.display = 'block';
  }

  function createRenderer() {
    // 只创建一个 canvas/context。连续 new WebGLRenderer 会额外消耗浏览器的
    // WebGL context 配额，多次打开预览后可能直接得到“Error creating WebGL context”。
    var canvas = document.createElement('canvas');
    var attributes = {
      alpha: false,
      antialias: false,
      depth: true,
      stencil: false,
      powerPreference: 'high-performance',
      failIfMajorPerformanceCaveat: false,
      preserveDrawingBuffer: false,
    };
    var context = canvas.getContext('webgl2', attributes) ||
      canvas.getContext('webgl', attributes) ||
      canvas.getContext('experimental-webgl', attributes);
    if (!context) {
      throw new Error('浏览器未能创建 WebGL 上下文。请关闭旧的 3D 预览标签页后重试；若仍失败，请在 Edge/Chrome 设置中开启图形加速并重启浏览器');
    }
    return new THREE.WebGLRenderer({
      canvas: canvas,
      context: context,
      antialias: false,
      powerPreference: 'high-performance',
    });
  }

  function disposeMaterial(material) {
    if (!material) return;
    Object.keys(material).forEach(function (key) {
      var value = material[key];
      if (value && value.isTexture && typeof value.dispose === 'function') value.dispose();
    });
    if (typeof material.dispose === 'function') material.dispose();
  }

  function disposeScene() {
    if (sceneDisposed) return;
    sceneDisposed = true;
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (resizeHandler) window.removeEventListener('resize', resizeHandler);
    if (controls && typeof controls.dispose === 'function') controls.dispose();
    if (scene && typeof scene.traverse === 'function') {
      scene.traverse(function (object) {
        if (object.geometry && typeof object.geometry.dispose === 'function') object.geometry.dispose();
        if (Array.isArray(object.material)) object.material.forEach(disposeMaterial);
        else disposeMaterial(object.material);
      });
    }
    if (renderer) {
      var canvas = renderer.domElement;
      renderer.dispose();
      // 主动释放 GPU context，避免刷新/返回/重复打开预览后耗尽配额。
      if (typeof renderer.forceContextLoss === 'function') renderer.forceContextLoss();
      if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
    }
  }

  function initScene(alignment) {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x10141b);
    camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.01, 500);
    renderer = createRenderer();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setSize(window.innerWidth, window.innerHeight);
    $('canvas').appendChild(renderer.domElement);
    renderer.domElement.addEventListener('webglcontextlost', function (event) {
      event.preventDefault();
      if (rafId !== null) cancelAnimationFrame(rafId);
      rafId = null;
      fail('3D 图形上下文已被浏览器回收。请关闭其他 3D 预览标签页，然后刷新本页重试');
    }, false);
    renderer.domElement.addEventListener('webglcontextrestored', function () {
      location.reload();
    }, false);

    // 数据为 z-up 米制；three.js 是 y-up，整组绕 X 转 -90°
    worldGroup = new THREE.Group();
    worldGroup.rotation.x = -Math.PI / 2;
    scene.add(worldGroup);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.9;
    controls.target.set(0, 0, 0);

    var grid = new THREE.GridHelper(20, 40, 0x2f3b52, 0x222b3a);
    worldGroup.add(grid);
    var axes = new THREE.AxesHelper(1.2);
    worldGroup.add(axes);

    pcdGroup = new THREE.Group();
    boxesGroup = new THREE.Group();
    labelGroup = new THREE.Group();
    worldGroup.add(pcdGroup);
    worldGroup.add(boxesGroup);
    worldGroup.add(labelGroup);

    resizeHandler = function () {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    };
    window.addEventListener('resize', resizeHandler);
    animate();
  }

  function animate() {
    if (sceneDisposed || !renderer || !controls) return;
    rafId = requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }

  /* ---------------- PLY 流式解析 ---------------- */
  var PLY_TYPE_SIZES = {
    char: 1, uchar: 1, short: 2, ushort: 2,
    int: 4, uint: 4, float: 4, double: 8,
  };
  function parsePlyHeader(headerText) {
    var lines = headerText.split('\n');
    if (lines[0].indexOf('ply') !== 0) throw new Error('不是 PLY 文件');
    var format = null, vertexCount = 0, properties = [];
    var currentElement = null;
    for (var i = 1; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      var parts = line.split(/\s+/);
      if (parts[0] === 'format') format = parts[1];
      else if (parts[0] === 'element') {
        currentElement = parts[1];
        if (parts[1] === 'vertex') vertexCount = parseInt(parts[2], 10);
      }
      else if (parts[0] === 'property' && currentElement === 'vertex') {
        // Open3D 会输出 property double x/y/z（8 字节）；旧解析只认 float 会导致
        // 步长错位、全部坐标变成垃圾（星空/放射线症状）。按真实类型定步长。
        var size = PLY_TYPE_SIZES[parts[1]];
        properties.push({
          type: parts[1],
          name: parts[2],
          size: typeof size === 'number' ? size : 4,
          offset: properties.reduce(function (s, p) { return s + p.size; }, 0),
        });
      }
      else if (parts[0] === 'end_header') break;
    }
    var stride = properties.reduce(function (s, p) { return s + p.size; }, 0);
    return { format: format, vertexCount: vertexCount, properties: properties, stride: stride };
  }

  var CHUNK = 200000;

  function loadPly(url, onProgress) {
    return fetch(url, { headers: token ? { Authorization: 'Bearer ' + token } : {} }).then(function (response) {
      if (!response.ok) throw new Error('PLY 下载失败 HTTP ' + response.status);
      var total = parseInt(response.headers.get('content-length') || '0', 10);
      var received = 0;
      var reader = response.body.getReader();
      var chunks = [], headerBytes = null, headerText = null, vertexBytes = [];
      var meta = null;
      var loaded = 0;

      function consume() {
        return reader.read().then(function (result) {
          if (result.done) { return finish(); }
          received += result.value.byteLength;
          if (headerText === null) {
            chunks.push(result.value);
            var buf = new Uint8Array(chunks.reduce(function (s, c) { return s + c.byteLength; }, 0));
            var off = 0;
            chunks.forEach(function (c) { buf.set(new Uint8Array(c.buffer, c.byteOffset, c.byteLength), off); off += c.byteLength; });
            var endIdx = indexOfEndHeader(buf);
            if (endIdx >= 0) {
              headerText = new TextDecoder().decode(buf.slice(0, endIdx));
              meta = parsePlyHeader(headerText);
              if (meta.format !== 'binary_little_endian') throw new Error('仅支持二进制 PLY');
              var bodyStart = endIdx + 'end_header\n'.length;
              var body = buf.slice(bodyStart);
              vertexBytes.push(body);
              loaded += body.byteLength;
              onProgress(Math.min(1, loaded / (meta.vertexCount * meta.stride)));
            }
          } else {
            vertexBytes.push(result.value);
            loaded += result.value.byteLength;
            var frac = meta && meta.vertexCount ? Math.min(1, loaded / (meta.vertexCount * meta.stride)) : 0;
            onProgress(frac);
          }
          return consume();
        });
      }
      function finish() {
        if (!meta) throw new Error('PLY 头解析失败');
        var full = new Uint8Array(meta.vertexCount * meta.stride);
        var off = 0;
        vertexBytes.forEach(function (part) {
          if (off + part.byteLength > full.byteLength) part = part.slice(0, full.byteLength - off);
          full.set(part, off); off += part.byteLength;
        });
        return { meta: meta, data: full };
      }
      return consume();
    });
  }

  function indexOfEndHeader(buf) {
    var needle = new TextEncoder().encode('end_header');
    outer: for (var i = 0; i <= buf.length - needle.length; i++) {
      for (var j = 0; j < needle.length; j++) if (buf[i + j] !== needle[j]) continue outer;
      return i;
    }
    return -1;
  }

  function addPointChunks(meta, data, onProgress) {
    var props = meta.properties;
    var getOffset = function (name) { for (var i = 0; i < props.length; i++) if (props[i].name === name) return props[i]; return null; };
    var px = getOffset('x'), py = getOffset('y'), pz = getOffset('z');
    var pr = getOffset('red'), pg = getOffset('green'), pb = getOffset('blue');
    var dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    var stride = meta.stride;
    var total = meta.vertexCount;
    var positions = new Float32Array(CHUNK * 3);
    var colors = new Float32Array(CHUNK * 3);
    var added = 0;

    function readProp(prop, base) {
      if (!prop) return 0;
      switch (prop.type) {
        case 'double': return dv.getFloat64(base + prop.offset, true);
        case 'float': return dv.getFloat32(base + prop.offset, true);
        case 'uchar': return dv.getUint8(base + prop.offset);
        case 'char': return dv.getInt8(base + prop.offset);
        case 'ushort': return dv.getUint16(base + prop.offset, true);
        case 'short': return dv.getInt16(base + prop.offset, true);
        case 'uint': return dv.getUint32(base + prop.offset, true);
        case 'int': return dv.getInt32(base + prop.offset, true);
        default: return 0;
      }
    }

    function feedChunk() {
      var start = added;
      var end = Math.min(added + CHUNK, total);
      var count = end - start;
      var posArr = count === CHUNK ? positions : new Float32Array(count * 3);
      var colArr = count === CHUNK ? colors : new Float32Array(count * 3);
      // uchar 颜色是 0..255，需要 /255；float/double 颜色已经是 0..1
      var colorScale = function (prop) {
        return (prop && prop.type !== 'float' && prop.type !== 'double') ? 1 / 255 : 1;
      };
      for (var i = 0; i < count; i++) {
        var base = (start + i) * stride;
        posArr[i * 3] = readProp(px, base);
        posArr[i * 3 + 1] = readProp(py, base);
        posArr[i * 3 + 2] = readProp(pz, base);
        colArr[i * 3] = (pr ? readProp(pr, base) : 200) * colorScale(pr);
        colArr[i * 3 + 1] = (pg ? readProp(pg, base) : 200) * colorScale(pg);
        colArr[i * 3 + 2] = (pb ? readProp(pb, base) : 200) * colorScale(pb);
      }
      var geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(colArr, 3));
      var points = new THREE.Points(geometry, pointMaterial);
      points.frustumCulled = false;
      pcdGroup.add(points);
      added = end;
      onProgress(added / total);
      if (added < total) {
        setTimeout(feedChunk, 0); // 连续化：让出主线程逐块上屏
      }
    }
    feedChunk();
  }

  /* ---------------- 3D 框叠加 ---------------- */
  var KIND_STYLE = {
    wall: { color: 0x4f9cf9, label: false },
    door: { color: 0xffb703, label: false },
    window: { color: 0x5ce98a, label: false },
    object: { color: 0xff8fa3, label: true }
  };

  function addBoxes(layout) {
    var groups = { walls: new THREE.Group(), doors: new THREE.Group(), windows: new THREE.Group(), objects: new THREE.Group() };
    ['walls', 'doors', 'windows'].forEach(function (key) {
      (layout[key] || []).forEach(function (item) {
        groups[key].add(makeBox(item, key === 'walls' ? 'wall' : key.slice(0, -1)));
      });
    });
    (layout.objects || []).forEach(function (item) {
      groups.objects.add(makeBox(item, 'object'));
      if (labelGroup) labelGroup.add(makeLabel(item));
    });
    Object.keys(groups).forEach(function (key) {
      boxGroups[key] = groups[key];
      boxesGroup.add(groups[key]);
    });
  }

  function makeBox(item, kind) {
    var style = KIND_STYLE[kind];
    var size = new THREE.Vector3(item.size[0], item.size[1], item.size[2]);
    var geometry = new THREE.BoxGeometry(size.x, size.y, size.z);
    var edges = new THREE.EdgesGeometry(geometry);
    var line = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: style.color, transparent: true, opacity: 0.95 }));
    line.position.set(item.center[0], item.center[1], item.center[2]);
    line.rotation.z = THREE.MathUtils.degToRad(item.rotation_z_deg || 0);
    line.userData = item;
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
    ctx.roundRect ? ctx.roundRect(4, 4, width, 56, 10) : ctx.fillRect(4, 4, width, 56);
    ctx.fill();
    ctx.fillStyle = '#ffd6de';
    ctx.fillText(text, 16, 42);
    var texture = new THREE.CanvasTexture(canvas);
    var sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true }));
    sprite.scale.set(0.9, 0.225, 1);
    sprite.position.set(item.center[0], item.center[1], item.center[2] + item.size[2] / 2 + 0.08);
    return sprite;
  }

  /* ---------------- UI ---------------- */
  function bindUI(alignment) {
    pointMaterial = new THREE.PointsMaterial({
      size: 0.02, vertexColors: true, sizeAttenuation: true
    });
    $('point-size').addEventListener('input', function () {
      if (pointMaterial) pointMaterial.size = parseFloat(this.value) * 0.012;
    });
    $('ck-pcd').addEventListener('change', function () { layerState.pcd = this.checked; pcdGroup.visible = this.checked; });
    $('ck-walls').addEventListener('change', function () { layerState.walls = this.checked; boxGroups.walls.visible = this.checked; });
    $('ck-doors').addEventListener('change', function () { layerState.doors = this.checked; boxGroups.doors.visible = this.checked; });
    $('ck-windows').addEventListener('change', function () { layerState.windows = this.checked; boxGroups.windows.visible = this.checked; });
    $('ck-objects').addEventListener('change', function () { layerState.objects = this.checked; boxGroups.objects.visible = this.checked; });
    $('ck-labels').addEventListener('change', function () { layerState.labels = this.checked; labelGroup.visible = this.checked; });
    $('ck-rotate').addEventListener('change', function () { controls.autoRotate = this.checked; });
    $('btn-reset').addEventListener('click', resetView);
    $('btn-shot').addEventListener('click', function () {
      renderer.render(scene, camera);
      var link = document.createElement('a');
      link.download = 'scene_preview.png';
      link.href = renderer.domElement.toDataURL('image/png');
      link.click();
    });
  }

  function resetView() {
    var extent = window.__extent || { x: [-2, 2], y: [-2, 2], z: [0, 3] };
    var cx = (extent.x[0] + extent.x[1]) / 2;
    var cy = (extent.y[0] + extent.y[1]) / 2;
    var cz = (extent.z[0] + extent.z[1]) / 2;
    var diagonal = Math.max(extent.x[1] - extent.x[0], extent.y[1] - extent.y[0], 0.5);
    controls.target.set(cx, cz, -cy);
    camera.position.set(cx + diagonal * 0.55, cz + diagonal * 0.75, -cy - diagonal * 0.9);
    controls.update();
  }

  /* ---------------- 主流程 ---------------- */
  function main() {
    $('btn-retry').addEventListener('click', function () { location.reload(); });
    if (!manifestUrl) { fail('缺少 scan 参数：请在 URL 中带上 ?scan=<扫描ID>'); return; }
    setProgress(0.02, '获取场景清单');
    fetch(manifestUrl, { headers: token ? { Authorization: 'Bearer ' + token } : {} })
      .then(function (response) {
        if (!response.ok) throw new Error('清单加载失败 HTTP ' + response.status + '（请确认已登录）');
        return response.json();
      })
      .then(function (manifest) {
        $('scene-title').textContent = manifest.name || ('扫描 #' + scanId);
        initScene(manifest.alignment || {});
        bindUI(manifest.alignment || {});
        if (manifest.alignment && manifest.alignment.extents_m) {
          window.__extent = manifest.alignment.extents_m;
        }
        setProgress(0.05, '加载稠密点云');
        return loadPly(manifest.ply, function (frac) { setProgress(0.05 + 0.6 * frac, '加载稠密点云 ' + Math.round(frac * 100) + '%'); })
          .then(function (plyResult) {
            setProgress(0.65, '渲染点云');
            addPointChunks(plyResult.meta, plyResult.data, function (frac) {
              setProgress(0.65 + 0.2 * frac, '渲染点云 ' + Math.round(frac * 100) + '%');
            });
            return manifest;
          });
      })
      .then(function (manifest) {
        if (!manifest.layout) return;
        setProgress(0.9, '加载空间结构识别结果');
        return fetch(manifest.layout, { headers: token ? { Authorization: 'Bearer ' + token } : {} })
          .then(function (response) {
            if (!response.ok) throw new Error('结构结果加载失败 HTTP ' + response.status);
            return response.json();
          })
          .then(function (layout) {
            addBoxes(layout);
            setProgress(0.98, '完成');
            var counts = layout.counts || {};
            $('scene-sub').textContent = '点云已加载 · 墙 ' + (counts.walls || 0) + ' · 门 ' + (counts.doors || 0) +
              ' · 窗 ' + (counts.windows || 0) + ' · 家具 ' + (counts.objects || 0);
            $('stats').textContent = JSON.stringify(manifest.alignment ? {
              points: manifest.alignment.points_preview,
              scale: manifest.alignment.scale,
              unit: manifest.alignment.coordinate_unit
            } : {}, null, 0);
            setTimeout(function () { $('overlay').style.display = 'none'; }, 250);
            resetView();
          });
      })
      .catch(function (error) { fail(error && error.message ? error.message : String(error)); });
  }

  // 几何点云模式由 gaussian_main.js 编排：Gaussian 模式时不自启动，
  // 切换“几何/调试模式”时通过 window.__startPointsViewer__ 手动拉起。
  window.__startPointsViewer__ = main;
  if (window.__PREVIEW_MODE__ === 'gaussian') {
    /* 等待用户切换 */
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', main);
  } else {
    main();
  }
  window.addEventListener('pagehide', disposeScene);
  window.addEventListener('beforeunload', disposeScene);
})();
