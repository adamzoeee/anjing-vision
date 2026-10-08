/* Accepted visual-only surfaces. Coordinates and GPU UVs are already baked;
 * no second alignment, smoothing or reconstruction occurs in the frontend. */
(function (root) {
  'use strict';
  function descriptors(layer) {
    if (layer.purpose !== 'visual-only') throw new Error('Expected visual-only layer');
    if (Array.isArray(layer.meshes) && layer.meshes.length) return layer.meshes;
    if (!layer.vertices || layer.vertices.length !== 4 || !layer.uv || layer.uv.length !== 4)
      throw new Error('Invalid legacy window layer');
    return [{name:'window',positions:layer.vertices,uv:layer.uv.map(function(p){return [p[0],1-p[1]];}),
      faces:[[0,1,2],[0,2,3]],texture:layer.texture}];
  }
  function buildMesh(THREE, data, texture) {
    var count = data.positions && data.positions.length;
    function attribute(rows, size, name) {
      if (!rows || rows.length !== count || !rows.every(function(p){
        return p.length === size && p.every(Number.isFinite);
      })) throw new Error('Invalid ' + name + ' attribute');
      return new THREE.Float32BufferAttribute(rows.flat(),size);
    }
    if (!count) throw new Error('Invalid position attribute');
    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', attribute(data.positions,3,'position'));
    if (data.faces) {
      var indices = data.faces.flat();
      if (indices.length % 3 || !indices.every(function(i){return Number.isInteger(i) && i>=0 && i<count;}))
        throw new Error('Invalid triangle index');
      geometry.setIndex(indices);
    } else if (count % 3) throw new Error('Invalid unindexed triangles');
    if (data.uv) geometry.setAttribute('uv',attribute(data.uv,2,'uv'));
    if (data.colors) geometry.setAttribute('color',attribute(data.colors,3,'color'));
    var material = new THREE.MeshBasicMaterial({map:texture || null,vertexColors:!!data.colors,side:THREE.DoubleSide});
    if (data.color_delta) {
      geometry.setAttribute('colorDelta',attribute(data.color_delta,3,'colorDelta'));
      material.onBeforeCompile = function(shader) {
        shader.vertexShader='attribute vec3 colorDelta; varying vec3 vColorDelta;\n'+shader.vertexShader;
        shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\nvColorDelta=colorDelta;');
        shader.fragmentShader='varying vec3 vColorDelta;\n'+shader.fragmentShader;
        shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>','#include <map_fragment>\ndiffuseColor.rgb=clamp(diffuseColor.rgb+vColorDelta,0.0,1.0);');
      };
    }
    var mesh = new THREE.Mesh(geometry,material);
    mesh.name=data.name || 'accepted-visual-surface';
    mesh.userData.purpose='visual-only';
    return mesh;
  }
  function loadLayer(THREE, layer, group) {
    var items=descriptors(layer),cache={};
    function textureFor(data) {
      if (!data.texture) return Promise.resolve(null);
      var url=(layer.textures || {})[data.texture] || data.texture;
      if (!/^data:image\/(png|jpeg);base64,/.test(url)) throw new Error('Invalid embedded texture');
      if (!cache[url]) cache[url]=new Promise(function(resolve,reject){
        new THREE.TextureLoader().load(url,resolve,undefined,reject);
      });
      return cache[url];
    }
    return Promise.all(items.map(function(data){return textureFor(data).then(function(t){return buildMesh(THREE,data,t);});}))
      .then(function(meshes){
        meshes.forEach(function(mesh){group.add(mesh);});
        return true;
      });
  }
  // Source-camera poses are useful for image comparisons, not interactive orbit
  // targets. Frame the displayed room itself, after its scene transform.
  function frameRoom(THREE, camera, group, up) {
    group.updateWorldMatrix(true,true);
    var bounds=new THREE.Box3().setFromObject(group);
    if (bounds.isEmpty()) throw new Error('无法确定房间视角：场景为空');
    up=up.clone().normalize();
    var target=bounds.getCenter(new THREE.Vector3());
    var direction=new THREE.Vector3(.15,1,-.25).normalize()
      .applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),up));
    var right=new THREE.Vector3().crossVectors(up,direction).normalize();
    var screenUp=new THREE.Vector3().crossVectors(direction,right);
    var tanV=Math.tan(THREE.MathUtils.degToRad(55/2)),tanH=tanV*camera.aspect,distance=.5;
    [bounds.min.x,bounds.max.x].forEach(function(x){
      [bounds.min.y,bounds.max.y].forEach(function(y){
        [bounds.min.z,bounds.max.z].forEach(function(z){
          var p=new THREE.Vector3(x,y,z).sub(target),depth=p.dot(direction);
          distance=Math.max(distance,depth+Math.abs(p.dot(right))/tanH,
            depth+Math.abs(p.dot(screenUp))/tanV);
        });
      });
    });
    distance*=1.12;
    return {target:target,position:target.clone().addScaledVector(direction,distance),up:up,
      distance:distance,fov:55};
  }
  function roomControls(THREE, camera, element, frame) {
    // OrbitControls caches the up-axis quaternion in its constructor. Set it
    // first; a fresh instance also clears pending inertia when resetting.
    camera.up.copy(frame.up);
    camera.position.copy(frame.position);
    camera.fov=frame.fov;
    camera.updateProjectionMatrix();
    var controls=new THREE.OrbitControls(camera,element);
    controls.target.copy(frame.target);
    controls.enableDamping=true;
    controls.dampingFactor=.08;
    controls.autoRotate=false;
    controls.autoRotateSpeed=.9;
    controls.minDistance=.15;
    controls.maxDistance=frame.distance*4;
    // This is an open-top room preview, not a walk-through. Keep orbit above
    // the room so vertical dragging cannot land behind opaque exterior walls.
    controls.maxPolarAngle=Math.PI/6;
    controls.update();
    controls.saveState();
    return controls;
  }
  var api={descriptors:descriptors,buildMesh:buildMesh,loadLayer:loadLayer,
    frameRoom:frameRoom,roomControls:roomControls};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else root.PreviewVisualLayers=api;
})(typeof window !== 'undefined' ? window : globalThis);
