/* 方案三维框架图 + 图层归一（console 方案可视化页专用）
 *
 * 数据源：GET /api/assistant/scans/{id}/schemes/{sid} 返回的 layout_before / layout_after
 * （与 preview layout.json 同形状：米、z-up；objects 带 kind=object|obstacle 与 assumed）。
 * 纯函数（layoutLayers / diffLayout）由 node 单测直接消费；THREE 只在 createSchemeViewer
 * 被调用时访问全局（console 的 index.html 已全局加载 three.min.js + OrbitControls）。 */

export const SCHEME3D_STYLE = {
  wall: { color: 0x9b8cff, fill: 0.05 },
  door: { color: 0xffc24b, fill: 0.12 },
  window: { color: 0x6ee7b7, fill: 0.12 },
  object: { color: 0xff9db1, fill: 0.05 },
  obstacle: { color: 0xffd6a5, fill: 0.06 },
};
/* 方案可视化专用语义色：前后对照时一眼看出“动了什么 / 哪来的 / 挪去哪”。 */
export const SCHEME3D_MARK = {
  changed: { color: 0x64ffda, fill: 0.10 },   // 本方案改动过（移动/改尺寸/旋转）
  added: { color: 0xffd166, fill: 0.12 },     // 本方案新增（假设放置）
  removed: { color: 0xff7b7b, fill: 0.08 },   // 对照“改造前”时：将不再存在
};

const EPS = 1e-6;
const number = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);

function normalizeItem(item, bucket, fallbackKind) {
  if (!item || !Array.isArray(item.center) || !Array.isArray(item.size)) return null;
  return {
    id: String(item.id ?? item.instance_id ?? ''),
    bucket,
    kind: String(item.kind || fallbackKind),
    label: String(item.label || ''),
    assumed: !!item.assumed,
    center: [number(item.center[0]), number(item.center[1]), number(item.center[2])],
    size: [number(item.size[0]), number(item.size[1]), number(item.size[2])],
    rotation_z_deg: number(item.rotation_z_deg),
  };
}

export function layoutLayers(layout) {
  const take = (items, bucket, fallbackKind) => (Array.isArray(items) ? items : [])
    .map((item) => normalizeItem(item, bucket, fallbackKind))
    .filter(Boolean);
  return {
    walls: take(layout?.walls, 'walls', 'wall'),
    doors: take(layout?.doors, 'doors', 'door'),
    windows: take(layout?.windows, 'windows', 'window'),
    objects: take(layout?.objects, 'objects', 'object'),
  };
}

function itemChanged(before, after) {
  return before.center.some((value, index) => Math.abs(value - after.center[index]) > EPS)
    || before.size.some((value, index) => Math.abs(value - after.size[index]) > EPS)
    || Math.abs(before.rotation_z_deg - after.rotation_z_deg) > 1e-4;
}

/* 前后差异（按对象 id 比较）：changed=动了/改尺寸/旋转；added=新增；removed=移除。 */
export function diffLayout(before, after) {
  const oldItems = layoutLayers(before).objects;
  const newItems = layoutLayers(after).objects;
  const oldMap = new Map(oldItems.map((item) => [item.id, item]));
  const newMap = new Map(newItems.map((item) => [item.id, item]));
  const changed = [];
  const added = [];
  const removed = [];
  newItems.forEach((item) => {
    const old = oldMap.get(item.id);
    if (!old) added.push(item.id);
    else if (itemChanged(old, item)) changed.push(item.id);
  });
  oldItems.forEach((item) => {
    if (!newMap.has(item.id)) removed.push(item.id);
  });
  return { changed: changed.sort(), added: added.sort(), removed: removed.sort() };
}

function markSet(diff, keys) {
  const set = new Set();
  keys.forEach((key) => (diff?.[key] || []).forEach((id) => set.add(id)));
  return set;
}

export function createSchemeViewer(host, options = {}) {
  const { autoRotate = false } = options;
  if (typeof THREE === 'undefined') throw new Error('three.js 未加载，无法渲染方案框架图');

  let scene = null;
  let camera = null;
  let renderer = null;
  let controls = null;
  let world = null;
  let boxGroup = null;
  let gridGroup = null;
  let rafId = null;
  let disposed = false;
  let extent = null;
  let fitted = false;
  let resizeObserver = null;

  const disposables = [];
  const track = (resource) => {
    if (resource && resource.dispose) disposables.push(resource);
    return resource;
  };

  function createRenderer() {
    const attempts = [
      { type: 'webgl2', powerPreference: 'high-performance' },
      { type: 'webgl2', powerPreference: 'default' },
      { type: 'webgl', powerPreference: 'default' },
    ];
    let canvas = null;
    let context = null;
    for (let i = 0; i < attempts.length && !context; i += 1) {
      canvas = document.createElement('canvas');
      try {
        context = canvas.getContext(attempts[i].type, {
          alpha: true,
          antialias: false,
          depth: true,
          stencil: false,
          powerPreference: attempts[i].powerPreference,
          failIfMajorPerformanceCaveat: false,
        });
      } catch {
        context = null;
      }
    }
    if (!context || !canvas) throw new Error('浏览器未能创建 WebGL 上下文，请开启硬件加速后重试');
    return new THREE.WebGLRenderer({ canvas, context, antialias: false, alpha: true });
  }

  function init() {
    scene = new THREE.Scene();
    const width = host.clientWidth || 800;
    const height = host.clientHeight || 520;
    camera = new THREE.PerspectiveCamera(52, width / height, 0.01, 300);
    renderer = createRenderer();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.setSize(width, height);
    renderer.setClearColor(0x000000, 0);
    host.appendChild(renderer.domElement);

    world = new THREE.Group();
    world.rotation.x = -Math.PI / 2;   // 业务 z-up → three y-up
    scene.add(world);
    boxGroup = new THREE.Group();
    gridGroup = new THREE.Group();
    world.add(boxGroup, gridGroup);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.7;
    controls.zoomSpeed = 0.8;
    controls.panSpeed = 0.7;
    controls.minDistance = 0.4;
    controls.maxDistance = 60;
    controls.autoRotate = autoRotate;
    controls.autoRotateSpeed = 0.6;
    controls.target.set(0, 0, 0);

    resizeObserver = new ResizeObserver(() => {
      if (disposed) return;
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
      controls.update();
      renderer.render(scene, camera);
    };
    animate();
  }

  function clearGroup(group) {
    group.children.slice().forEach((child) => {
      child.traverse((node) => {
        if (node.geometry) node.geometry.dispose();
        const materials = Array.isArray(node.material) ? node.material : [node.material];
        materials.forEach((material) => material?.dispose && material.dispose());
      });
      group.remove(child);
    });
  }

  function makeBox(item, style) {
    const size = item.size.map((value) => Math.max(value, 0.01));
    const geometry = new THREE.BoxGeometry(size[0], size[1], size[2]);
    const fill = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
      color: style.color,
      transparent: true,
      opacity: style.fill,
      depthWrite: false,
    }));
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry),
      new THREE.LineBasicMaterial({ color: style.color, transparent: true, opacity: 0.9 }),
    );
    const group = new THREE.Group();
    group.add(fill, edges);
    group.position.set(item.center[0], item.center[1], item.center[2]);
    group.rotation.z = (item.rotation_z_deg * Math.PI) / 180;
    return group;
  }

  function styleFor(item, marks) {
    if (marks.removed.has(item.id)) return SCHEME3D_MARK.removed;
    if (marks.changed.has(item.id)) return SCHEME3D_MARK.changed;
    if (marks.added.has(item.id) || item.assumed) return SCHEME3D_MARK.added;
    return SCHEME3D_STYLE[item.kind] || SCHEME3D_STYLE.object;
  }

  function fitCamera(bounds) {
    const cx = (bounds.min[0] + bounds.max[0]) / 2;
    const cy = (bounds.min[1] + bounds.max[1]) / 2;
    const cz = (bounds.min[2] + bounds.max[2]) / 2;
    const span = Math.max(
      bounds.max[0] - bounds.min[0],
      bounds.max[1] - bounds.min[1],
      bounds.max[2] - bounds.min[2],
      0.8,
    );
    controls.target.set(cx, cz, -cy);
    camera.position.set(cx + span * 0.44, cz + span * 0.53, -cy - span * 0.7);
    controls.update();
  }

  function ensureGrid(bounds) {
    clearGroup(gridGroup);
    const spanX = bounds.max[0] - bounds.min[0];
    const spanY = bounds.max[1] - bounds.min[1];
    const size = Math.max(spanX, spanY, 2) * 1.8;
    const grid = new THREE.GridHelper(size, Math.max(8, Math.round(size / 0.5)), 0x6b7db8, 0x3a476e);
    grid.material.transparent = true;
    grid.material.opacity = 0.22;
    grid.material.depthWrite = false;
    grid.rotation.x = Math.PI / 2;
    const cx = (bounds.min[0] + bounds.max[0]) / 2;
    const cy = (bounds.min[1] + bounds.max[1]) / 2;
    grid.position.set(cx, cy, Math.min(0, bounds.min[2]) - 0.01);
    gridGroup.add(grid);
  }

  /* layout：方案接口的 layout_before / layout_after；
   * mark：{ changed?, added?, removed? }（diffLayout 的输出，直接传整个对象即可）。 */
  function setLayout(layout, mark = {}) {
    const layers = layoutLayers(layout);
    const marks = {
      changed: markSet(mark, ['changed']),
      added: markSet(mark, ['added']),
      removed: markSet(mark, ['removed']),
    };
    clearGroup(boxGroup);
    const bounds = {
      min: [Infinity, Infinity, Infinity],
      max: [-Infinity, -Infinity, -Infinity],
    };
    const add = (items) => items.forEach((item) => {
      const group = makeBox(item, styleFor(item, marks));
      boxGroup.add(group);
      for (let axis = 0; axis < 3; axis += 1) {
        bounds.min[axis] = Math.min(bounds.min[axis], item.center[axis] - item.size[axis] / 2);
        bounds.max[axis] = Math.max(bounds.max[axis], item.center[axis] + item.size[axis] / 2);
      }
    });
    add(layers.walls);
    add(layers.doors);
    add(layers.windows);
    add(layers.objects);
    if (!Number.isFinite(bounds.min[0])) {
      bounds.min = [-1, -1, 0];
      bounds.max = [1, 1, 1];
    }
    extent = JSON.parse(JSON.stringify(bounds));
    ensureGrid(bounds);
    // 只在第一次布局时取景；前后切换保留用户已经转好的视角（重置按钮可回到默认）。
    if (!fitted) {
      fitCamera(bounds);
      fitted = true;
    }
    return { counts: layers };
  }

  function resetView() {
    if (extent) {
      fitCamera(extent);
      fitted = true;
    }
  }

  function setAutoRotate(on) {
    if (controls) controls.autoRotate = !!on;
  }

  function dispose() {
    disposed = true;
    if (rafId !== null) cancelAnimationFrame(rafId);
    rafId = null;
    resizeObserver?.disconnect();
    if (boxGroup) clearGroup(boxGroup);
    if (gridGroup) clearGroup(gridGroup);
    disposables.forEach((resource) => {
      try { resource.dispose(); } catch { /* ignore */ }
    });
    if (renderer) {
      const canvas = renderer.domElement;
      renderer.dispose();
      if (renderer.forceContextLoss) renderer.forceContextLoss();
      canvas?.parentNode?.removeChild(canvas);
    }
  }

  init();
  return { setLayout, resetView, setAutoRotate, dispose };
}
