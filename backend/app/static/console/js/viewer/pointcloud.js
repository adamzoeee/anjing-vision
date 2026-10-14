/* 安龄智境 · 点云 3D 引擎（大屏版）
 * 渐进式分块加载 + SpatialLM 结构框叠加 + 电影感镜头。
 * 依赖 /preview-static/three.min.js 与 OrbitControls.js（全局 THREE）。 */

import { getToken } from '../api.js?v=house-scan-1';
import { typeName, measuredSizeMap, normalizeInstanceId } from '../ui.js';
import { packUpAxis, resolveFrame, resolveCalibration, framePoint, framePointInto, percentile, envelope, modalPlane } from './frame.mjs';
import { normalizeStructure } from './structure.mjs';
import { layerVisibility } from './mode.mjs?v=house-scan-1';

const PLY_TYPE_SIZES = {
  char: 1, uchar: 1, short: 2, ushort: 2,
  int: 4, uint: 4, float: 4, double: 8,
};
const CHUNK = 200000;

const LAYER_STYLE = {
  wall: { color: 0x9b8cff, fill: 0.05 },
  door: { color: 0xffc24b, fill: 0.1 },
  window: { color: 0x6ee7b7, fill: 0.1 },
  object: { color: 0xff9db1, fill: 0.04 },
};

function reducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function parsePlyHeader(headerText) {
  const lines = headerText.split('\n');
  if (lines[0].indexOf('ply') !== 0) throw new Error('不是 PLY 文件');
  let format = null;
  let vertexCount = 0;
  const properties = [];
  let currentElement = null;
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const parts = line.split(/\s+/);
    if (parts[0] === 'format') format = parts[1];
    else if (parts[0] === 'element') {
      currentElement = parts[1];
      if (parts[1] === 'vertex') vertexCount = parseInt(parts[2], 10);
    } else if (parts[0] === 'property' && currentElement === 'vertex') {
      const size = PLY_TYPE_SIZES[parts[1]];
      properties.push({
        type: parts[1],
        name: parts[2],
        size: typeof size === 'number' ? size : 4,
        offset: properties.reduce((sum, prop) => sum + prop.size, 0),
      });
    } else if (parts[0] === 'end_header') break;
  }
  const stride = properties.reduce((sum, prop) => sum + prop.size, 0);
  return { format, vertexCount, properties, stride };
}

function findHeaderEnd(buf) {
  const needle = new TextEncoder().encode('end_header');
  outer: for (let i = 0; i <= buf.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (buf[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

async function fetchPly(url, onProgress, signal) {
  const token = getToken();
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  });
  if (!response.ok) throw new Error(`点云下载失败（HTTP ${response.status}）`);
  const reader = response.body.getReader();
  const chunks = [];
  let meta = null;
  let headerBytes = null;
  let loaded = 0;
  let held = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    loaded += value.byteLength;
    if (meta === null) {
      chunks.push(value);
      const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
      const buf = new Uint8Array(total);
      let offset = 0;
      chunks.forEach((chunk) => {
        buf.set(new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength), offset);
        offset += chunk.byteLength;
      });
      const endIdx = findHeaderEnd(buf);
      if (endIdx >= 0) {
        meta = parsePlyHeader(new TextDecoder().decode(buf.slice(0, endIdx)));
        if (meta.format !== 'binary_little_endian') throw new Error('仅支持二进制 PLY 点云');
        headerBytes = new Uint8Array(buf.slice(endIdx + 'end_header\n'.length));
        held = headerBytes.byteLength;
      }
    } else {
      headerBytes = appendBytes(headerBytes, value);
      held += value.byteLength;
    }
    if (meta && onProgress) {
      const total = meta.vertexCount * meta.stride;
      onProgress(Math.min(1, held / total));
    }
  }
  if (!meta || !headerBytes) throw new Error('PLY 头解析失败');
  const need = meta.vertexCount * meta.stride;
  const data = headerBytes.byteLength > need ? headerBytes.slice(0, need) : headerBytes;
  return { meta, data };
}

function appendBytes(buf, value) {
  const merged = new Uint8Array(buf.byteLength + value.byteLength);
  merged.set(buf, 0);
  merged.set(value, buf.byteLength);
  return merged;
}

export function createViewer(host, options = {}) {
  const {
    onStatus = () => {},
    onStats = () => {},
    cinematic = false,
    defaultAutoRotate = false,
    defaultPointScale = 1,
  } = options;

  let scene = null;
  let camera = null;
  let renderer = null;
  let controls = null;
  let worldGroup = null;
  let pcdGroup = null;
  let boxesGroup = null;
  let labelGroup = null;
  let boxGroups = { walls: null, doors: null, windows: null, objects: null };
  let pointMaterial = null;
  let rafId = null;
  let resizeObserver = null;
  let disposed = false;
  let abortController = null;
  let introProgress = -1;

  const state = {
    extent: null,
    points: 0,
    // labels 默认与界面 chip 的初始“开”状态一致；真实场景下标签同样不渲染（见 layerVisibility）。
    layers: { pcd: true, walls: true, doors: true, windows: true, objects: true, labels: true },
    pointScale: defaultPointScale,
    autoRotate: defaultAutoRotate && !reducedMotion(),
    ready: false,
    mode: 'scene',
    hasLayout: false,
  };
  let gridGroup = null;
  const structureFills = [];
  const structureEdges = [];

  function createRenderer() {
    const attempts = [
      { type: 'webgl2', powerPreference: 'high-performance' },
      { type: 'webgl2', powerPreference: 'default' },
      { type: 'webgl', powerPreference: 'default' },
      { type: 'webgl', powerPreference: 'low-power' },
      { type: 'experimental-webgl', powerPreference: 'default' },
    ];
    let canvas = null;
    let context = null;
    let selected = 'default';
    for (let i = 0; i < attempts.length && !context; i++) {
      canvas = document.createElement('canvas');
      const attempt = attempts[i];
      try {
        context = canvas.getContext(attempt.type, {
          alpha: true,
          antialias: false,
          depth: true,
          stencil: false,
          powerPreference: attempt.powerPreference,
          failIfMajorPerformanceCaveat: false,
          preserveDrawingBuffer: false,
        });
        if (context) selected = attempt.powerPreference;
      } catch {
        context = null;
      }
    }
    if (!context || !canvas) {
      throw new Error('浏览器未能创建 WebGL 上下文，请开启硬件加速后重试');
    }
    return new THREE.WebGLRenderer({
      canvas,
      context,
      antialias: false,
      alpha: true,
      powerPreference: selected,
    });
  }

  function initScene() {
    scene = new THREE.Scene();
    const width = host.clientWidth || 800;
    const height = host.clientHeight || 600;
    camera = new THREE.PerspectiveCamera(55, width / height, 0.01, 500);
    renderer = createRenderer();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.setSize(width, height);
    renderer.setClearColor(0x000000, 0);
    host.appendChild(renderer.domElement);
    renderer.domElement.addEventListener('webglcontextlost', (event) => {
      event.preventDefault();
      if (rafId !== null) cancelAnimationFrame(rafId);
      rafId = null;
      onStatus({ phase: 'error', message: '3D 图形上下文被浏览器回收，请刷新重试' });
    });
    renderer.domElement.addEventListener('webglcontextrestored', () => location.reload());

    worldGroup = new THREE.Group();
    worldGroup.rotation.x = -Math.PI / 2;
    scene.add(worldGroup);

    pcdGroup = new THREE.Group();
    boxesGroup = new THREE.Group();
    boxesGroup.visible = false;
    labelGroup = new THREE.Group();
    labelGroup.visible = false;
    worldGroup.add(pcdGroup, boxesGroup, labelGroup);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.055;
    controls.rotateSpeed = 0.7;
    controls.zoomSpeed = 0.75;
    controls.panSpeed = 0.7;
    controls.autoRotate = state.autoRotate;
    controls.autoRotateSpeed = 0.5;
    controls.minDistance = 0.05;
    controls.maxDistance = 150;
    controls.target.set(0, 0, 0);

    gridGroup = new THREE.Group();
    gridGroup.visible = false;
    worldGroup.add(gridGroup);

    pointMaterial = new THREE.PointsMaterial({
      size: 0.0175 * state.pointScale,
      vertexColors: true,
      sizeAttenuation: true,
      transparent: false,
    });

    resizeObserver = new ResizeObserver(() => {
      const w = host.clientWidth || 1;
      const h = host.clientHeight || 1;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    resizeObserver.observe(host);

    const animate = () => {
      if (disposed) return;
      rafId = requestAnimationFrame(animate);
      if (introProgress >= 0 && introProgress < 1) {
        introProgress = Math.min(1, introProgress + 1 / 220);
        applyIntro(introProgress);
      }
      controls.update();
      renderer.render(scene, camera);
    };
    animate();
  }

  /* —— 电影感入场：从远到近的镜头 —— */
  let intro = null;
  function startIntro() {
    if (!state.extent) return;
    if (reducedMotion()) { resetView(); return; }
    const fitted = fittedCamera(state.extent);
    const span = Math.max(
      state.extent.x[1] - state.extent.x[0],
      state.extent.y[1] - state.extent.y[0],
      state.extent.z[1] - state.extent.z[0],
      0.6,
    );
    const from = fitted.position.clone().multiplyScalar(1.85).add(fitted.target.clone().multiplyScalar(-0.85));
    const control = from.clone().lerp(fitted.position, 0.55).add(new THREE.Vector3(span * 0.4, span * 0.55, 0));
    intro = {
      from,
      control,
      to: fitted.position,
      target: fitted.target,
    };
    camera.position.copy(intro.from);
    controls.target.copy(intro.target);
    controls.enabled = false;
    introProgress = 0;
  }
  function applyIntro(t) {
    if (!intro) { introProgress = -1; return; }
    const ease = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    const inverse = 1 - ease;
    camera.position.set(
      inverse * inverse * intro.from.x + 2 * inverse * ease * intro.control.x + ease * ease * intro.to.x,
      inverse * inverse * intro.from.y + 2 * inverse * ease * intro.control.y + ease * ease * intro.to.y,
      inverse * inverse * intro.from.z + 2 * inverse * ease * intro.control.z + ease * ease * intro.to.z,
    );
    controls.target.lerp(intro.target, 0.05);
    if (t >= 1) {
      introProgress = -1;
      controls.enabled = true;
      controls.update();
    }
  }

  function fittedCamera(extent) {
    const cx = (extent.x[0] + extent.x[1]) / 2;
    const cy = (extent.y[0] + extent.y[1]) / 2;
    const cz = (extent.z[0] + extent.z[1]) / 2;
    const span = Math.max(
      extent.x[1] - extent.x[0],
      extent.y[1] - extent.y[0],
      extent.z[1] - extent.z[0],
      0.6,
    );
    const target = new THREE.Vector3(cx, cz, -cy);
    const position = new THREE.Vector3(
      cx + span * 0.62,
      cz + span * 0.78,
      -cy - span * 1.05,
    );
    return { target, position };
  }

  function resetView() {
    if (!state.extent) return;
    const fitted = fittedCamera(state.extent);
    controls.target.copy(fitted.target);
    camera.position.copy(fitted.position);
    controls.update();
  }

  function focusBounds(bounds) {
    if (!controls || !state.ready || !bounds?.min || !bounds?.max) return;
    const extent = {
      x: [bounds.min[0], bounds.max[0]],
      y: [bounds.min[1], bounds.max[1]],
      z: [bounds.min[2], bounds.max[2]],
    };
    const fitted = fittedCamera(extent);
    controls.target.copy(fitted.target);
    camera.position.copy(fitted.position);
    controls.update();
  }

  function highlightDoor(instanceId = null) {
    if (!boxGroups.doors) return;
    boxGroups.doors.children.forEach((group) => {
      const selected = instanceId && group.userData.item?.instance_id === instanceId;
      const edges = group.children.find((child) => child.isLineSegments);
      if (!edges) return;
      edges.material.color.setHex(selected ? 0xffffff : LAYER_STYLE.door.color);
      edges.renderOrder = selected ? 5 : 2;
    });
  }

  /* 交付包竖直轴不是 z 时，用地板面给出竖直锚点（up=-Y→地板在最大 y；up=+Y→最小 y）。
   * 取竖直方向直方图里地板一侧的高密度平面，而不是极端分位数：地板面是真平面，
   * 分位数会落在“平面之外那几个离群点”上，让整包点云抬高十几厘米。 */
  function sampleFloorAnchor(meta, data, upSign) {
    const py = meta.properties.find((prop) => prop.name === 'y');
    if (!py) return null;
    const total = meta.vertexCount;
    const stride = meta.stride;
    const step = Math.max(1, Math.floor(total / 60000));
    const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const readY = (base) => {
      switch (py.type) {
        case 'double': return dv.getFloat64(base + py.offset, true);
        case 'float': return dv.getFloat32(base + py.offset, true);
        default: return 0;
      }
    };
    const values = [];
    for (let i = 0; i < total; i += step) values.push(readY(i * stride));
    if (values.length < 16) return percentile(values, upSign < 0 ? 99.5 : 0.5);
    const plane = modalPlane(values, upSign);
    return plane === null ? percentile(values, upSign < 0 ? 99.5 : 0.5) : plane;
  }

  /* 交付包水平变换的输入：
   *  - 真实极值（必须扫全部点：穿窗、门口走廊的极值点很稀疏）；
   *  - 0.1%/99.9% 分位包络（兜底拟合用）；
   *  - 竖直中段的 x/z 剖面（首选：墙面平面不会被走廊/穿窗几何撑大）。 */
  function sampleHorizontalFrame(meta, data, floor, upSign) {
    const px = meta.properties.find((prop) => prop.name === 'x');
    const py = meta.properties.find((prop) => prop.name === 'y');
    const pz = meta.properties.find((prop) => prop.name === 'z');
    if (!px || !pz) return null;
    const total = meta.vertexCount;
    const stride = meta.stride;
    const step = Math.max(1, Math.floor(total / 120000));
    const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const read = (prop, base) => {
      switch (prop.type) {
        case 'double': return dv.getFloat64(base + prop.offset, true);
        case 'float': return dv.getFloat32(base + prop.offset, true);
        default: return 0;
      }
    };
    const xs = [];
    const zs = [];
    const hs = [];
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    // 极值点很稀疏（穿窗/门口走廊），必须扫全部点；分位包络抽样即可
    for (let i = 0; i < total; i += 1) {
      const base = i * stride;
      const x = read(px, base);
      const z = read(pz, base);
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (z < minZ) minZ = z;
      if (z > maxZ) maxZ = z;
      if (i % step === 0) {
        xs.push(x);
        zs.push(z);
        if (py) {
          const y = read(py, base);
          hs.push(upSign < 0 ? floor - y : y - floor);
        }
      }
    }
    if (!Number.isFinite(minX) || !Number.isFinite(minZ)) return null;
    const frame = {
      x: [minX, maxX],
      z: [minZ, maxZ],
      xBand: envelope(xs),
      zBand: envelope(zs),
    };
    if (py && hs.length >= 64) {
      let minH = Infinity;
      let maxH = -Infinity;
      hs.forEach((h) => {
        if (h < minH) minH = h;
        if (h > maxH) maxH = h;
      });
      const span = maxH - minH;
      if (span > 1) {
        const lo = minH + span * 0.2;
        const hi = minH + span * 0.8;
        const bandX = [];
        const bandZ = [];
        for (let i = 0; i < hs.length; i += 1) {
          if (hs[i] >= lo && hs[i] <= hi) {
            bandX.push(xs[i]);
            bandZ.push(zs[i]);
          }
        }
        if (bandX.length >= 64) frame.wallProfile = { x: bandX, z: bandZ };
      }
    }
    return frame;
  }

  /* 房间轮廓（业务 z-up 系）来自结构数据；缺失时不做水平拟合。 */
  function roomOutline(source) {
    const bounds = source?.room?.bounds_xy;
    if (bounds && Array.isArray(bounds.min) && Array.isArray(bounds.max)) return bounds;
    return null;
  }

  function addPointChunks(meta, data, frame, onProgress) {
    const props = meta.properties;
    const find = (name) => props.find((prop) => prop.name === name) || null;
    const px = find('x');
    const py = find('y');
    const pz = find('z');
    const pr = find('red');
    const pg = find('green');
    const pb = find('blue');
    const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const stride = meta.stride;
    const total = meta.vertexCount;
    let added = 0;

    const readProp = (prop, base) => {
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
    };
    const colorScale = (prop) => (prop && prop.type !== 'float' && prop.type !== 'double' ? 1 / 255 : 1);

    function feed() {
      if (disposed) return;
      const start = added;
      const end = Math.min(added + CHUNK, total);
      const count = end - start;
      const posArr = new Float32Array(count * 3);
      const colArr = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        const base = (start + i) * stride;
    // 与窗边展示层共用同一份坐标校正（竖直轴 + 水平面 180° + 逐轴尺度）；
        // 点云单独跳过水平那段会让家具框与点云整体错开（scan46 的回归点）。
        framePointInto(posArr, i * 3, readProp(px, base), readProp(py, base), readProp(pz, base), frame);
        colArr[i * 3] = (pr ? readProp(pr, base) : 205) * colorScale(pr);
        colArr[i * 3 + 1] = (pg ? readProp(pg, base) : 210) * colorScale(pg);
        colArr[i * 3 + 2] = (pb ? readProp(pb, base) : 220) * colorScale(pb);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(colArr, 3));
      const points = new THREE.Points(geometry, pointMaterial);
      points.frustumCulled = false;
      pcdGroup.add(points);
      state.points += count;
      added = end;
      onProgress(added / total);
      if (added < total) setTimeout(feed, 0);
    }
    feed();
  }

  /* —— 结构框 —— */
  function makeBox(item, kind) {
    const style = LAYER_STYLE[kind];
    const size = new THREE.Vector3(item.size[0], item.size[1], item.size[2]);
    const geometry = new THREE.BoxGeometry(Math.max(size.x, 0.01), Math.max(size.y, 0.01), Math.max(size.z, 0.01));
    const group = new THREE.Group();
    const fill = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
      color: style.color,
      transparent: true,
      opacity: style.fill,
      depthWrite: false,
    }));
    fill.material.userData.base = style.fill;
    structureFills.push(fill.material);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry),
      new THREE.LineBasicMaterial({ color: style.color, transparent: true, opacity: 0.92 }),
    );
    edges.renderOrder = 2;
    structureEdges.push(edges.material);
    group.add(fill, edges);
    group.position.set(item.center[0], item.center[1], item.center[2]);
    group.rotation.z = THREE.MathUtils.degToRad(item.rotation_z_deg || 0);
    group.userData.item = item;
    return group;
  }

  function makeLabel(item) {
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    ctx.font = '600 26px "Microsoft YaHei", sans-serif';
    const text = typeName(item.category || 'object');
    const width = Math.min(244, ctx.measureText(text).width + 28);
    ctx.fillStyle = 'rgba(10,15,30,0.82)';
    if (ctx.roundRect) {
      ctx.beginPath();
      ctx.roundRect(2, 4, width, 54, 12);
      ctx.fill();
    } else {
      ctx.fillRect(2, 4, width, 54);
    }
    ctx.strokeStyle = 'rgba(255,143,163,0.5)';
    ctx.lineWidth = 1.5;
    if (ctx.roundRect) {
      ctx.beginPath();
      ctx.roundRect(2, 4, width, 54, 12);
      ctx.stroke();
    }
    ctx.fillStyle = '#ffd6de';
    ctx.fillText(text, 16, 41);
    const texture = new THREE.CanvasTexture(canvas);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true }));
    sprite.scale.set(0.85, 0.215, 1);
    sprite.position.set(item.center[0], item.center[1], item.center[2] + item.size[2] / 2 + 0.09);
    return sprite;
  }

  function addLayout(layout, measuredMap) {
    const resolveSize = (item) => {
      if (measuredMap && item.instance_id && item.category !== 'wall') {
        const measured = measuredMap.get(normalizeInstanceId(item.instance_id));
        if (measured) return [measured.length_m, measured.width_m, measured.height_m];
      }
      return item.size;
    };
    const groups = { walls: new THREE.Group(), doors: new THREE.Group(), windows: new THREE.Group(), objects: new THREE.Group() };
    (layout.walls || []).forEach((item) => groups.walls.add(makeBox(item, 'wall')));
    (layout.doors || []).forEach((item) => groups.doors.add(makeBox(item, 'door')));
    (layout.windows || []).forEach((item) => groups.windows.add(makeBox(item, 'window')));
    (layout.objects || []).forEach((item) => {
      const resolved = { ...item, size: resolveSize(item) };
      groups.objects.add(makeBox(resolved, 'object'));
      labelGroup.add(makeLabel(resolved));
    });
    Object.entries(groups).forEach(([key, group]) => {
      boxGroups[key] = group;
      boxesGroup.add(group);
    });
    boxesGroup.visible = true;
    labelGroup.visible = state.layers.labels;
    state.hasLayout = true;
    ensureGrid();
    applyMode();
  }

  /* 结构框架整体套用“扫描级校准”（手调参数固化）：与校准页 frameGroup 完全同一个
   * 矩阵 T · S · Rz(yaw)（业务 z-up → 显示系）。只调整体大框架，不单独挪某个物品框；
   * 点云此时保持交付包原生尺寸，两者“刚好和现实重叠”。 */
  function applyStructureCalibration(rows) {
    if (!rows) return;
    const matrix = new THREE.Matrix4().set(...rows);   // 行主序参数
    [boxesGroup, labelGroup].forEach((group) => {
      group.matrixAutoUpdate = false;
      group.matrix.copy(matrix);
      group.matrixWorldNeedsUpdate = true;
    });
  }

  /* —— 已修正窗边展示层（visual-only，不参与几何/评分）
   * 兼容新版多层网格（meshes[] + 内嵌贴图/顶点色/colorDelta）与旧版单层贴图。 —— */
  function visualLayerItems(layer) {
    if (Array.isArray(layer.meshes) && layer.meshes.length) return layer.meshes;
    if (Array.isArray(layer.vertices) && layer.vertices.length === 4
      && Array.isArray(layer.uv) && layer.uv.length === 4) {
      return [{
        name: 'window',
        positions: layer.vertices,
        uv: layer.uv.map((point) => [point[0], 1 - point[1]]),
        faces: [[0, 1, 2], [0, 2, 3]],
        texture: layer.texture,
      }];
    }
    return [];
  }

  function buildVisualMesh(data, texture, frame) {
    const count = Array.isArray(data.positions) ? data.positions.length : 0;
    if (!count) throw new Error('展示层缺少顶点');
    const attribute = (rows, size) => new THREE.Float32BufferAttribute(rows.flat(), size);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      'position',
      attribute(data.positions.map((point) => framePoint(point[0], point[1], point[2], frame)), 3),
    );
    if (Array.isArray(data.faces) && data.faces.length) {
      const indices = data.faces.flat();
      if (indices.length % 3 || !indices.every((i) => Number.isInteger(i) && i >= 0 && i < count)) {
        throw new Error('展示层三角面索引非法');
      }
      geometry.setIndex(indices);
    } else if (count % 3) {
      throw new Error('展示层未索引顶点数非法');
    }
    const hasUv = Array.isArray(data.uv) && data.uv.length === count;
    const hasColors = Array.isArray(data.colors) && data.colors.length === count;
    if (hasUv) geometry.setAttribute('uv', attribute(data.uv, 2));
    if (hasColors) geometry.setAttribute('color', attribute(data.colors, 3));
    const material = new THREE.MeshBasicMaterial({ map: texture || null, vertexColors: hasColors, side: THREE.DoubleSide });
    if (Array.isArray(data.color_delta) && data.color_delta.length === count) {
      geometry.setAttribute('colorDelta', attribute(data.color_delta, 3));
      material.onBeforeCompile = (shader) => {
        shader.vertexShader = 'attribute vec3 colorDelta;\nvarying vec3 vColorDelta;\n' + shader.vertexShader;
        shader.vertexShader = shader.vertexShader.replace(
          '#include <begin_vertex>',
          '#include <begin_vertex>\nvColorDelta = colorDelta;',
        );
        shader.fragmentShader = 'varying vec3 vColorDelta;\n' + shader.fragmentShader;
        shader.fragmentShader = shader.fragmentShader.replace(
          '#include <map_fragment>',
          '#include <map_fragment>\ndiffuseColor.rgb = clamp(diffuseColor.rgb + vColorDelta, 0.0, 1.0);',
        );
      };
    }
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = data.name || 'accepted-visual-surface';
    mesh.userData.purpose = 'visual-only';
    return mesh;
  }

  async function fetchWindowVisual(url, headers) {
    const res = await fetch(url, { headers, signal: abortController.signal }).catch(() => null);
    if (!res || !res.ok) return null;
    const layer = await res.json().catch(() => null);
    return layer && layer.purpose === 'visual-only' ? layer : null;
  }

  async function addWindowVisual(layer, frame) {
    if (!layer) return;
    const items = visualLayerItems(layer);
    if (!items.length) return;
    const textureCache = new Map();
    const textureFor = (data) => {
      if (!data.texture) return Promise.resolve(null);
      const source = (layer.textures || {})[data.texture] || data.texture;
      if (typeof source !== 'string' || !/^data:image\/(png|jpeg);base64,/.test(source)) return Promise.resolve(null);
      if (!textureCache.has(source)) {
        textureCache.set(source, new Promise((resolve) => {
          new THREE.TextureLoader().load(source, resolve, undefined, () => resolve(null));
        }));
      }
      return textureCache.get(source);
    };
    const meshes = await Promise.all(items.map(async (data) => {
      try {
        return buildVisualMesh(data, await textureFor(data), frame);
      } catch (error) {
        return null;
      }
    }));
    meshes.filter(Boolean).forEach((mesh) => pcdGroup.add(mesh));
  }

  function ensureGrid() {
    if (!gridGroup || !state.extent || gridGroup.children.length) return;
    const ex = state.extent;
    const cx = (ex.x[0] + ex.x[1]) / 2;
    const cy = (ex.y[0] + ex.y[1]) / 2;
    const size = Math.max(ex.x[1] - ex.x[0], ex.y[1] - ex.y[0], 2) * 1.7;
    const grid = new THREE.GridHelper(size, 14, 0x9b8cff, 0x9b8cff);
    grid.material.transparent = true;
    grid.material.opacity = 0.16;
    grid.material.depthWrite = false;
    grid.rotation.x = Math.PI / 2;
    grid.position.set(cx, cy, ex.z[0] - 0.01);
    gridGroup.add(grid);
  }

  function applyMode() {
    const structure = state.mode === 'structure';
    const overlay = state.mode === 'overlay';
    const view = layerVisibility(state.mode, state.layers, state.hasLayout);
    if (structure || overlay) {
      ensureGrid();
      structureFills.forEach((material) => {
        material.opacity = Math.min(overlay ? 0.1 : 0.3, (material.userData.base || 0.05) * (overlay ? 1.4 : 3.4));
      });
      structureEdges.forEach((material) => {
        material.opacity = overlay ? 0.68 : 1;
        material.depthTest = !overlay;
      });
    } else {
      structureFills.forEach((material) => { material.opacity = material.userData.base || 0.05; });
      structureEdges.forEach((material) => { material.opacity = 0.92; material.depthTest = true; });
    }
    // 真实场景保持纯点云；整屋叠加模式才同时显示点云与框架。
    pcdGroup.visible = view.pcd;
    labelGroup.visible = view.labels;
    boxesGroup.visible = view.boxes;
    gridGroup.visible = view.grid && gridGroup.children.length > 0;
    ['walls', 'doors', 'windows', 'objects'].forEach((key) => {
      if (boxGroups[key]) boxGroups[key].visible = view[key];
    });
  }

  function setMode(mode) {
    state.mode = ['scene', 'structure', 'overlay'].includes(mode) ? mode : 'scene';
    applyMode();
  }

  /* —— 对外 API —— */
  async function load(scanId) {
    abortController = new AbortController();
    try {
      onStatus({ phase: 'manifest', message: '获取场景清单' });
      const token = getToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const manifestRes = await fetch(`/api/preview/${scanId}/manifest.json`, { headers, signal: abortController.signal });
      if (!manifestRes.ok) {
        throw new Error(manifestRes.status === 401 ? '登录已过期' : '场景清单加载失败');
      }
      const manifest = await manifestRes.json();
      if (manifest.alignment?.extents_m) state.extent = manifest.alignment.extents_m;
      initScene();

      const structurePromise = (async () => {
        let source = null;
        for (const url of [manifest.structure, manifest.layout]) {
          if (!url) continue;
          const res = await fetch(url, { headers, signal: abortController.signal }).catch(() => null);
          if (res && res.ok) {
            source = await res.json().catch(() => null);
            if (source) break;
          }
        }
        let measurements = null;
        if (manifest.measurements) {
          const res = await fetch(manifest.measurements, { headers, signal: abortController.signal }).catch(() => null);
          if (res && res.ok) measurements = await res.json().catch(() => null);
        }
        return { source, measurements };
      })();

      const windowVisualPromise = manifest.window_visual
        ? fetchWindowVisual(manifest.window_visual, headers)
        : Promise.resolve(null);

      /* 扫描级“整体框架校准”（校准页手调参数固化，可选）：点云保持原生尺寸，
       * 结构框架整体套矩阵，二者“刚好和现实重叠”。 */
      const calibrationPromise = manifest.frame_calibration
        ? fetch(manifest.frame_calibration, { headers, signal: abortController.signal })
          .then((res) => (res.ok ? res.json().catch(() => null) : null))
          .catch(() => null)
        : Promise.resolve(null);

      onStatus({ phase: 'points', message: '流式加载稠密点云', progress: 0 });
      const { meta, data } = await fetchPly(manifest.ply, (frac) => {
        onStatus({ phase: 'points', message: '流式加载稠密点云', progress: frac });
      }, abortController.signal);
      // 交付包可能导出自来源相机坐标系（竖直轴 Y）；按包内视角位姿判断并映射回 z-up 业务系，
      // 否则点云/展示层会相对结构框整体旋转 90°。
      const layer = await windowVisualPromise;
      // 结构/测量清单是小文件且与点云并发下载，这里已经就绪，用它拿房间轮廓做水平面对齐。
      const { source, measurements } = await structurePromise;
      const calibrationPayload = await calibrationPromise;
      const upAxis = packUpAxis(layer?.views || null);
      let calibration = null;
      if (calibrationPayload) {
        const recorded = Number(calibrationPayload?.cloud_frame?.floor_y_pack);
        const floorHint = Number.isFinite(recorded) ? recorded : sampleFloorAnchor(meta, data, -1);
        calibration = resolveCalibration(calibrationPayload, layer?.views || null, floorHint);
        if (!calibration) {
          onStatus({ phase: 'points', message: '扫描级校准与当前点云不匹配，已退回通用拟合' });
        }
      }
      // 有校准：点云/展示层只做竖直轴映射（原生尺寸）；无校准：按墙面剖面对齐房间轮廓。
      const frame = calibration
        ? calibration.frame
        : upAxis && Math.abs(upAxis[1]) > 0.9 && Math.abs(upAxis[2]) <= 0.9
          ? (() => {
            const floor = sampleFloorAnchor(meta, data, upAxis[1]);
            const horizontal = sampleHorizontalFrame(meta, data, floor, upAxis[1]);
            return resolveFrame(layer.views, floor, {
              pointExtent: horizontal,
              wallProfile: horizontal?.wallProfile || null,
              roomBounds: roomOutline(source),
            });
          })()
          : null;
      if (calibration) {
        applyStructureCalibration(calibration.rows);
        onStatus({
          phase: 'points',
          message: '点云保持交付包原生尺寸，结构框架整体套用手调校准（180°，X×0.84 / Y×0.71 / Z×0.9）',
        });
      } else if (frame) {
        onStatus({
          phase: 'points',
          message: frame.horizontal
            ? '已校正交付包坐标系（Y-竖直 → z-up，水平面逐轴对齐房间墙面）'
            : '已校正交付包坐标系（来源帧 Y-竖直 → 业务 z-up）',
        });
      } else if (upAxis) {
        // 静默退回“不做校正”是上一轮的坑：状态栏必须告诉用户原因。
        onStatus({ phase: 'points', message: '交付包坐标系未识别，点云按包内坐标直接渲染' });
      }
      await new Promise((resolve) => {
        addPointChunks(meta, data, frame, (frac) => {
          onStatus({ phase: 'render', message: '逐块渲染点云', progress: frac });
          if (frac >= 1) resolve();
        });
      });
      if (source) addLayout(normalizeStructure(source), measuredSizeMap(measurements));
      await addWindowVisual(layer, frame);
      state.ready = true;
      if (!state.extent || calibration) computeBounds();
      ensureGrid();
      applyMode();
      if (cinematic) startIntro(); else resetView();
      onStatus({ phase: 'ready', message: '场景就绪' });
      onStats({ name: manifest.name, points: state.points, extent: state.extent });
    } catch (error) {
      if (error?.name === 'AbortError') return;
      onStatus({ phase: 'error', message: error?.message || String(error) });
    }
  }

  function computeBounds() {
    const box = new THREE.Box3();
    pcdGroup.children.forEach((child) => {
      child.geometry.computeBoundingBox();
      box.union(child.geometry.boundingBox);
    });
    if (!box.isEmpty()) {
      state.extent = {
        x: [box.min.x, box.max.x],
        y: [box.min.y, box.max.y],
        z: [box.min.z, box.max.z],
      };
    }
  }

  function setLayer(name, on) {
    state.layers[name] = !!on;
    applyMode();
  }

  function setPointScale(scale) {
    state.pointScale = scale;
    if (pointMaterial) pointMaterial.size = 0.0175 * scale;
  }

  function setAutoRotate(on) {
    state.autoRotate = !!on;
    if (controls) controls.autoRotate = !!on;
  }

  function screenshot() {
    if (!renderer) return null;
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL('image/png');
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    abortController?.abort();
    if (rafId !== null) cancelAnimationFrame(rafId);
    resizeObserver?.disconnect();
    if (controls?.dispose) controls.dispose();
    if (scene?.traverse) {
      scene.traverse((object) => {
        if (object.geometry?.dispose) object.geometry.dispose();
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach((material) => {
          if (!material) return;
          Object.values(material).forEach((value) => {
            if (value?.isTexture && value.dispose) value.dispose();
          });
          if (material.dispose) material.dispose();
        });
      });
    }
    if (renderer) {
      const canvas = renderer.domElement;
      renderer.dispose();
      if (renderer.forceContextLoss) renderer.forceContextLoss();
      canvas?.parentNode?.removeChild(canvas);
    }
  }

  return {
    load,
    dispose,
    setMode,
    setLayer,
    setPointScale,
    setAutoRotate,
    resetView,
    focusBounds,
    highlightDoor,
    screenshot,
    state,
  };
}
