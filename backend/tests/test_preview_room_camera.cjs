const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const THREE = require('../app/static/preview/three.min.js');
const layers = require('../app/static/preview/visual_layers.js');
class Surface {
  constructor() { this.handlers = new Map(); this.ownerDocument = this; this.clientWidth = 860; this.clientHeight = 778; }
  addEventListener(type, fn) { if (!this.handlers.has(type)) this.handlers.set(type, new Set()); this.handlers.get(type).add(fn); }
  removeEventListener(type, fn) { this.handlers.get(type)?.delete(fn); }
  dispatch(type, data = {}) { for (const fn of [...(this.handlers.get(type) || [])]) fn({preventDefault(){}, pointerType:'mouse', ...data}); }
  getRootNode() { return this; }
  focus() {}
}
const surface = new Surface();
vm.runInNewContext(fs.readFileSync(require.resolve('../app/static/preview/OrbitControls.js'),'utf8'), {THREE, document:{}, console});
const group = new THREE.Group();
group.rotation.x = -Math.PI/2;
const geometry = new THREE.BufferGeometry();
// Source -Y is up for the accepted room; the scene rotation makes display +Z up.
geometry.setAttribute('position',new THREE.Float32BufferAttribute([-.54,-.94,-.53,2.09,1.49,2.44],3));
group.add(new THREE.Points(geometry));
const original = Array.from(geometry.attributes.position.array);
const camera = new THREE.PerspectiveCamera(55,860/778,.01,500);
assert.equal(typeof layers.frameRoom,'function','interactive framing must use actual room bounds, not a source-camera optical target');
const frame = layers.frameRoom(THREE,camera,group,new THREE.Vector3(0,0,1));
const bounds = new THREE.Box3().setFromObject(group);
const center = bounds.getCenter(new THREE.Vector3());
assert(frame.target.distanceTo(center)<1e-8,'orbit center is inside the displayed room');
assert(frame.position.z > bounds.max.z,'initial camera is above the room');
assert(frame.position.x > bounds.min.x && frame.position.x < bounds.max.x);
assert(frame.position.y > bounds.min.y && frame.position.y < bounds.max.y,'not outside the window wall');
let controls = layers.roomControls(THREE,camera,surface,frame);
assert(controls.target.distanceTo(center)<1e-8);
assert(camera.up.equals(new THREE.Vector3(0,0,1)));
camera.updateMatrixWorld();
for (const x of [bounds.min.x,bounds.max.x]) for (const y of [bounds.min.y,bounds.max.y]) for (const z of [bounds.min.z,bounds.max.z]) {
  const p = new THREE.Vector3(x,y,z).project(camera);
  assert(Math.abs(p.x)<1 && Math.abs(p.y)<1 && p.z>-1 && p.z<1,'entire room fits initial viewport');
}
const distance = camera.position.distanceTo(center), height = camera.position.z;
const initialPosition = camera.position.clone();
// Exercise the actual shipped OrbitControls, not a mock of its update.
surface.dispatch('pointerdown',{button:0,clientX:420,clientY:390});
surface.dispatch('pointermove',{clientX:620,clientY:390});
surface.dispatch('pointerup');
for(let i=0;i<240;i++) controls.update();
assert(camera.position.distanceTo(initialPosition)>.1,'drag must rotate');
assert(Math.abs(camera.position.z-height)<1e-8,'horizontal drag rotates around real vertical axis');
assert(Math.abs(camera.position.distanceTo(center)-distance)<1e-8);
assert(controls.target.distanceTo(center)<1e-8,'rotation cannot orbit a black-space optical target');
surface.dispatch('wheel',{deltaY:-100});
assert(camera.position.distanceTo(center)<distance,'zoom can approach the room');
surface.dispatch('pointerdown',{button:0,clientX:420,clientY:390});
surface.dispatch('pointermove',{clientX:420,clientY:210});
surface.dispatch('pointerup');
for(let i=0;i<240;i++) controls.update();
assert(camera.position.z >= center.z,'vertical drag cannot flip under the room');
assert(controls.getPolarAngle()<=Math.PI/6+1e-8,'orbit stays above the room rather than behind its walls');
// Reset recreates the controller so pending damping/panning cannot move it away.
surface.dispatch('pointerdown',{button:2,clientX:420,clientY:390});
surface.dispatch('pointermove',{clientX:450,clientY:420});
surface.dispatch('pointerup');
controls.dispose();
controls = layers.roomControls(THREE,camera,surface,frame);
for(let i=0;i<120;i++) controls.update();
assert(controls.target.distanceTo(center)<1e-8);
assert(camera.position.distanceTo(initialPosition)<1e-8);
assert.equal(surface.handlers.get('pointerdown').size,1,'reset must not duplicate interaction listeners');
assert.deepEqual(Array.from(geometry.attributes.position.array),original,'navigation cannot modify points');
controls.dispose();
// Legacy z-up scans retain their existing displayed Y-up convention.
const legacy = layers.frameRoom(THREE,camera,group,new THREE.Vector3(0,1,0));
assert(legacy.up.equals(new THREE.Vector3(0,1,0)));
console.log('PASS: room-centered overhead framing, real drag/zoom, correct orbit axis, clean reset and unchanged point geometry');
