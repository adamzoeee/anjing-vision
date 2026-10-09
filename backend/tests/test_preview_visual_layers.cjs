const assert = require('node:assert/strict');
const THREE = require('../app/static/preview/three.min.js');
const layers = require('../app/static/preview/visual_layers.js');
const positions = [[1,2,3],[4,5,6],[7,8,9]];
const texture = new THREE.Texture();
const mesh = layers.buildMesh(THREE, {name:'curtain',positions,uv:[[0,1],[1,1],[0,0]],
  color_delta:[[.1,0,0],[0,0,0],[0,0,0]]}, texture);
assert.deepEqual(Array.from(mesh.geometry.attributes.position.array), positions.flat());
assert.deepEqual(Array.from(mesh.geometry.attributes.uv.array), [0,1,1,1,0,0]);
assert.equal(mesh.geometry.index, null); // already-expanded observed triangles
assert.equal(mesh.material.map, texture);
assert.equal(mesh.userData.purpose, 'visual-only');
const shader={vertexShader:'#include <begin_vertex>',fragmentShader:'#include <map_fragment>'};
mesh.material.onBeforeCompile(shader);
assert(shader.fragmentShader.includes('diffuseColor.rgb+vColorDelta'));
const legacy=layers.descriptors({purpose:'visual-only',vertices:[...positions,[10,11,12]],
  uv:[[0,0],[1,0],[1,1],[0,1]],texture:'data:image/png;base64,AA=='});
assert.deepEqual(legacy[0].uv,[[0,1],[1,1],[1,0],[0,0]]);
assert.deepEqual(legacy[0].faces,[[0,1,2],[0,2,3]]);
assert.equal(layers.descriptors({purpose:'visual-only',meshes:[{positions}]}).length,1);
assert.throws(()=>layers.descriptors({purpose:'measurement',meshes:[]}),/visual-only/);
assert.throws(()=>layers.buildMesh(THREE,{positions,faces:[[0,1,3]]}),/index/);
assert.throws(()=>layers.buildMesh(THREE,{positions:[[NaN,0,0]]}),/position/);
const color=layers.buildMesh(THREE,{positions,colors:[[1,0,0],[0,1,0],[0,0,1]],faces:[[0,1,2]]});
assert.equal(color.material.vertexColors,true);
assert.deepEqual(Array.from(color.geometry.index.array),[0,1,2]);
console.log('PASS: original legacy layer and accepted multi-surface geometry, UV, color and validation');
