const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/preview/viewer.js'),'utf8');
const start=source.indexOf('function addPointChunks(');
const end=source.indexOf('/* ---------------- 开顶',start);
const chunks=[];
const context={CHUNK:2, Float32Array, DataView, pointMaterial:{},
 pcdGroup:{add:p=>chunks.push(p)},setTimeout:f=>f(),
 THREE:{BufferGeometry:class {constructor(){this.attributes={}}setAttribute(k,v){this.attributes[k]=v}},
 BufferAttribute:class {constructor(array,itemSize){this.array=array;this.itemSize=itemSize}},
 Points:class {constructor(geometry){this.geometry=geometry}}}};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const bytes=new Uint8Array(7*15);const view=new DataView(bytes.buffer);
for(let i=0;i<7;i++){for(let j=0;j<3;j++)view.setFloat32(i*15+j*4,i*10+j,true);bytes[i*15+12]=i+1;bytes[i*15+13]=i+2;bytes[i*15+14]=i+3;}
context.addPointChunks({vertexCount:7,stride:15,properties:[
 ...['x','y','z'].map((name,i)=>({name,type:'float',offset:i*4})),
 ...['red','green','blue'].map((name,i)=>({name,type:'uchar',offset:12+i}))]},bytes,()=>{});
assert.equal(new Set(chunks.map(p=>p.geometry.attributes.position.array)).size,4);
assert.deepEqual(chunks.flatMap(p=>Array.from(p.geometry.attributes.position.array)),Array.from({length:21},(_,i)=>Math.floor(i/3)*10+i%3));
assert(Math.abs(chunks[0].geometry.attributes.color.array[0]-1/255)<1e-8);
console.log('PASS: four chunks retain every position and RGB independently');
