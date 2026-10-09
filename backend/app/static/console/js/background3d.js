/* 安龄智境 · 界面背后的悬浮 3D 建模层
 * 固定在全屏最底层：缓慢旋转的房间结构线框 + 漂浮尘点 + 鼠标视差。
 * 数据来源：最近一次扫描的 layout.json（取不到时用程序化房间兜底）。 */

import { getToken } from './api.js';

let instance = null;

export function getBackground3D() {
  if (!instance) instance = createBackground3D();
  return instance;
}

function cssColor(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function createBackground3D() {
  const canvas = document.createElement('canvas');
  canvas.id = 'bg3d';
  document.body.appendChild(canvas);

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  } catch {
    return { setScan() {}, setSubtle() {}, applySkin() {}, dispose() {} };
  }
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 300);
  const world = new THREE.Group();
  world.rotation.x = -Math.PI / 2; // 数据为 z-up
  scene.add(world);

  const modelGroup = new THREE.Group();
  const gridGroup = new THREE.Group();
  world.add(modelGroup, gridGroup);

  let edgeColor = cssColor('--accent', '#ff8e6b');
  let fillColor = cssColor('--accent-3', '#a78bfa');
  let fillMaterials = [];

  /* —— 尘点：缓慢漂浮 —— */
  const DUST = 260;
  const dustPos = new Float32Array(DUST * 3);
  const dustSeed = new Float32Array(DUST);
  for (let i = 0; i < DUST; i++) {
    dustPos[i * 3] = (Math.random() - 0.5) * 26;
    dustPos[i * 3 + 1] = (Math.random() - 0.5) * 16;
    dustPos[i * 3 + 2] = Math.random() * 9;
    dustSeed[i] = Math.random() * Math.PI * 2;
  }
  const dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
  const dustMat = new THREE.PointsMaterial({
    color: new THREE.Color(edgeColor),
    size: 0.06,
    transparent: true,
    opacity: 0.5,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const dust = new THREE.Points(dustGeo, dustMat);
  scene.add(dust);

  /* —— 程序化兜底房间 —— */
  function proceduralLayout() {
    const walls = [
      { center: [0, -1.9, 1.35], size: [3.8, 0.08, 2.7], rotation_z_deg: 0 },
      { center: [0, 1.9, 1.35], size: [3.8, 0.08, 2.7], rotation_z_deg: 0 },
      { center: [-1.9, 0, 1.35], size: [0.08, 3.8, 2.7], rotation_z_deg: 0 },
      { center: [1.9, 0, 1.35], size: [0.08, 3.8, 2.7], rotation_z_deg: 0 },
    ];
    const objects = [
      { center: [-0.7, 0.9, 0.3], size: [2.0, 1.5, 0.6], rotation_z_deg: 0, label: 'bed' },
      { center: [1.2, -0.9, 0.36], size: [0.7, 1.2, 0.72], rotation_z_deg: 12, label: 'table' },
      { center: [1.4, 1.1, 0.9], size: [0.6, 0.5, 1.8], rotation_z_deg: -8, label: 'cabinet' },
    ];
    return { walls, doors: [], windows: [], objects };
  }

  function clearGroup(group) {
    while (group.children.length) {
      const child = group.children.pop();
      child.traverse?.((node) => {
        node.geometry?.dispose?.();
        if (Array.isArray(node.material)) node.material.forEach((m) => m.dispose?.());
        else node.material?.dispose?.();
      });
    }
  }

  function applySkin() {
    edgeColor = cssColor('--accent', '#ff8e6b');
    fillColor = cssColor('--accent-3', '#a78bfa');
    const ivory = document.documentElement.dataset.skin === 'ivory';
    const blending = ivory ? THREE.NormalBlending : THREE.AdditiveBlending;
    dustMat.color = new THREE.Color(edgeColor);
    dustMat.blending = blending;
    dustMat.opacity = ivory ? 0.65 : 0.5;
    modelGroup.traverse((node) => {
      if (!node.material) return;
      node.material.blending = blending;
      if (node.userData.role === 'edge') node.material.color = new THREE.Color(edgeColor);
      if (node.userData.role === 'fill') node.material.color = new THREE.Color(fillColor);
    });
    gridGroup.traverse((node) => {
      if (node.material) node.material.blending = blending;
    });
  }

  function setLayout(layout) {
    clearGroup(modelGroup);
    fillMaterials = [];
    const data = layout && (layout.walls || layout.objects) ? layout : proceduralLayout();
    const kinds = [
      { key: 'walls', fill: 0.045, edge: 0.4 },
      { key: 'doors', fill: 0.09, edge: 0.5 },
      { key: 'windows', fill: 0.09, edge: 0.5 },
      { key: 'objects', fill: 0.035, edge: 0.32 },
    ];
    const box = new THREE.Box3();
    kinds.forEach(({ key, fill, edge }) => {
      (data[key] || []).forEach((item) => {
        if (!item.size || !item.center) return;
        const geometry = new THREE.BoxGeometry(
          Math.max(item.size[0], 0.02),
          Math.max(item.size[1], 0.02),
          Math.max(item.size[2], 0.02),
        );
        const fillMat = new THREE.MeshBasicMaterial({
          color: new THREE.Color(fillColor),
          transparent: true,
          opacity: fill,
          depthWrite: false,
          blending: document.documentElement.dataset.skin === 'ivory' ? THREE.NormalBlending : THREE.AdditiveBlending,
        });
        const mesh = new THREE.Mesh(geometry, fillMat);
        mesh.userData.role = 'fill';
        fillMaterials.push(fillMat);
        const edgeMat = new THREE.LineBasicMaterial({
          color: new THREE.Color(edgeColor),
          transparent: true,
          opacity: edge,
          blending: document.documentElement.dataset.skin === 'ivory' ? THREE.NormalBlending : THREE.AdditiveBlending,
        });
        const lines = new THREE.LineSegments(new THREE.EdgesGeometry(geometry), edgeMat);
        lines.userData.role = 'edge';
        const group = new THREE.Group();
        group.add(mesh, lines);
        group.position.set(item.center[0], item.center[1], item.center[2]);
        group.rotation.z = THREE.MathUtils.degToRad(item.rotation_z_deg || 0);
        modelGroup.add(group);
        box.expandByPoint(new THREE.Vector3(
          item.center[0] - item.size[0] / 2,
          item.center[1] - item.size[1] / 2,
          item.center[2] - item.size[2] / 2,
        ));
        box.expandByPoint(new THREE.Vector3(
          item.center[0] + item.size[0] / 2,
          item.center[1] + item.size[1] / 2,
          item.center[2] + item.size[2] / 2,
        ));
      });
    });

    // 地面网格
    clearGroup(gridGroup);
    if (!box.isEmpty()) {
      const size = Math.max(box.max.x - box.min.x, box.max.y - box.min.y, 2) * 1.6;
      const grid = new THREE.GridHelper(size, 12, new THREE.Color(edgeColor), new THREE.Color(edgeColor));
      grid.material.transparent = true;
      grid.material.opacity = 0.12;
      grid.material.blending = THREE.AdditiveBlending;
      grid.rotation.x = Math.PI / 2;
      grid.position.set((box.min.x + box.max.x) / 2, (box.min.y + box.max.y) / 2, -0.02);
      gridGroup.add(grid);
      gridGroup.visible = false;
    }

    // 相机取景
    const center = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
    const dims = box.isEmpty() ? new THREE.Vector3(3, 3, 2.6) : box.getSize(new THREE.Vector3());
    const span = Math.max(dims.x, dims.y, dims.z, 2);
    const target = new THREE.Vector3(center.x, center.z, -center.y);
    const distance = span * 1.55;
    camera.position.set(
      center.x + distance * 0.72,
      center.z + distance * 0.42,
      -center.y - distance * 1.05,
    );
    camera.lookAt(target);
    camera.userData.target = target;
  }

  let pointerX = 0;
  let pointerY = 0;
  let smoothX = 0;
  let smoothY = 0;
  const onPointerMove = (event) => {
    pointerX = (event.clientX / window.innerWidth - 0.5) * 2;
    pointerY = (event.clientY / window.innerHeight - 0.5) * 2;
  };
  window.addEventListener('pointermove', onPointerMove, { passive: true });

  function resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  resize();
  window.addEventListener('resize', resize);

  let raf = null;
  let time = 0;
  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
    && document.documentElement.dataset.motion !== 'full';

  function frame() {
    raf = requestAnimationFrame(frame);
    if (document.hidden) return;
    time += 1 / 60;
    const motion = !reduced();
    smoothX += (pointerX - smoothX) * 0.03;
    smoothY += (pointerY - smoothY) * 0.03;

    if (motion) {
      modelGroup.rotation.z = time * 0.045;
      modelGroup.position.z = Math.sin(time * 0.32) * 0.12;
      gridGroup.rotation.z = time * 0.045;
      dust.rotation.z = time * 0.012;
      const pos = dustGeo.getAttribute('position');
      for (let i = 0; i < DUST; i++) {
        pos.array[i * 3 + 2] = (dustSeed[i] + time * 0.35) % 9;
      }
      pos.needsUpdate = true;
    }
    const target = camera.userData.target || new THREE.Vector3();
    camera.position.x += ((target.x + (camera.userData.baseX ?? camera.position.x)) - camera.position.x) * 0;
    camera.lookAt(
      target.x + smoothX * 0.35,
      target.y + smoothY * 0.25,
      target.z,
    );
    renderer.render(scene, camera);
  }
  frame();

  let currentScan = null;
  async function setScan(scanId) {
    if (!scanId || scanId === currentScan) return;
    currentScan = scanId;
    try {
      const token = getToken();
      const response = await fetch(`/api/preview/${scanId}/layout.json`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new Error('layout unavailable');
      setLayout(await response.json());
    } catch {
      setLayout(null);
    }
  }

  setLayout(null);

  return {
    setScan,
    applySkin,
    setSubtle(on) { canvas.classList.toggle('subtle', !!on); },
    setGrid(on) { gridGroup.visible = !!on; },
    dispose() {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onPointerMove);
      renderer.dispose();
      canvas.remove();
      instance = null;
    },
  };
}
