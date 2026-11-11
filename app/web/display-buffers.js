/* Copyright (c) 2026 Damian Gulczynski. */
/* Typed display arrays avoid large JSON point lists and repeated flattening. */
(()=>{
 'use strict';
 const MAX_POINTS=1000000,MAX_METADATA=1024*1024;
 const green=[.2,.82,.48],red=[1,.22,.12],blue=[.16,.45,1];
 function decode(buffer){
  if(!(buffer instanceof ArrayBuffer)||buffer.byteLength<24)throw Error('Incomplete display buffer.');
  const head=new DataView(buffer);
  if(head.getUint32(0,true)!==0x50445354||head.getUint32(4,true)!==1)throw Error('Unknown display buffer format.');
  const count=head.getUint32(8,true),length=head.getUint32(12,true),flags=head.getUint32(16,true);
  const offset=24+Math.ceil(length/8)*8,end=offset+count*24,expected=end+(flags&1?count*8:0);
  if(count>MAX_POINTS||length>MAX_METADATA||flags>1||head.getUint32(20,true)!==0||buffer.byteLength!==expected)throw Error('Invalid display buffer length.');
  const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(new Uint8Array(buffer,24,length)));
  if(!data||typeof data!=='object'||data.sample_count!==count||!Array.isArray(data.origin)||data.origin.length!==3||!data.origin.every(Number.isFinite))throw Error('Invalid display metadata.');
  const packed=new Float32Array(buffer,offset,count*6);
  for(let i=0;i<packed.length;i++)if(!Number.isFinite(packed[i]))throw Error('Invalid displayed coordinate or colour.');
  const deviations=flags&1?new Float64Array(buffer,end,count):null;
  if(deviations)for(let i=0;i<deviations.length;i++)if(!Number.isFinite(deviations[i]))throw Error('Invalid display deviation.');
  return {...data,packed,deviations_mm:deviations};
 }
 async function load(url,options={}){
  const separator=url.includes('?')?'&':'?',response=await fetch(url+separator+'format=binary',options);
  if(!response.ok){let error={};try{error=await response.json();}catch(_){}throw Error(error.error||'The display result could not load.');}
  return decode(await response.arrayBuffer());
 }
 function packed(data){
  if(data?.packed instanceof Float32Array){if(data.packed.length%6)throw Error('Invalid display point layout.');return data.packed;}
  const rows=data?.points;if(!Array.isArray(rows)||rows.length>MAX_POINTS)throw Error('The preview contains no display points.');
  const out=new Float32Array(rows.length*6);
  for(let i=0;i<rows.length;i++){if(!Array.isArray(rows[i])||rows[i].length!==6)throw Error('Invalid display point layout.');out.set(rows[i],i*6);}
  return out;
 }
 function recolour(data,scale,ramp){
  if(!(scale>0&&Number.isFinite(scale)))throw Error('Enter a positive colour scale in millimetres.');
  const source=packed(data),distances=data.deviations_mm,count=source.length/6;
  if(!distances||distances.length!==count)throw Error('This preview has no matching distance values.');
  const out=new Float32Array(source);
  for(let i=0;i<count;i++){
   const d=distances[i];if(!Number.isFinite(d))continue;
   const end=d<0?blue:red,t=ramp?Math.min(1,Math.abs(d)/scale):Math.abs(d)>scale?1:0;
   for(let j=0;j<3;j++)out[i*6+3+j]=green[j]+(end[j]-green[j])*t;
  }
  return {...data,points:undefined,packed:out,tolerance_mm:scale,colour_mode:ramp?'ramp':'tolerance'};
 }
 function recolourKeepBand(data,inward,outward){
  const source=packed(data),distances=data.deviations_mm,count=source.length/6;
  if(!distances||distances.length!==count)throw Error('This preview has no matching distance values.');
  const out=new Float32Array(source);
  for(let i=0;i<count;i++){
   const d=distances[i];
   out.set(inward!==null&&d < -inward?blue:outward!==null&&d>outward?red:green,i*6+3);
  }
  return {...data,points:undefined,packed:out,colour_mode:'keep-band'};
 }
 // All indices refer to the uploaded scan vertex stream. Sorting an index buffer
 // instead of the scan itself keeps source ownership, colours and edit flags intact.
 const PAGE_VERTICES=65536, COARSE_BIT=0x80000000, CELL_MASK=0x7fffffff;
 const REFERENCE_TAN=Math.tan(35*Math.PI/180), FAR_METRES=50;
 async function buildPointGrid(values,{signal,onProgress=()=>{},wide=true}={}){
  if(!(values instanceof Float32Array)||values.length%6||values.length/6>MAX_POINTS)
   throw Error('Invalid point stream for display indexing.');
  const n=values.length/6;
  const checkpoint=()=>{if(signal?.aborted)throw new DOMException('Display indexing cancelled.','AbortError');};
  let deadline=performance.now()+6,previous=-1;
  async function pass(phase,visit){
   for(let start=0;start<n;start+=2048){
    checkpoint();
    const stop=Math.min(start+2048,n);
    for(let row=start;row<stop;row++)visit(row);
    if(performance.now()>=deadline&&n>4096){
     const pct=Math.floor((phase+stop/n)*100/3);
     if(pct!==previous){onProgress(pct);previous=pct;}
     // Release the main thread for input, paint, or a superseding preview.
     await new Promise(resolve=>setTimeout(resolve,0));
     deadline=performance.now()+6;
    }
   }
   checkpoint();
  }
  const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];
  await pass(0,row=>{
   const at=row*6;
   for(let j=0;j<3;j++){
    const v=values[at+j];if(!Number.isFinite(v))throw Error('Non-finite displayed coordinate.');
    if(v<lo[j])lo[j]=v;if(v>hi[j])hi[j]=v;
   }
  });
  const extent=hi.map((v,j)=>n?v-lo[j]:0);
  const budget=Math.min(1024,Math.max(1,Math.ceil(n/384)));
  // Bound the number of cells, while allowing long, thin alignments to devote
  // almost all of the bins to their long direction.
  let left=0,right=Math.max(1,...extent);
  for(let k=0;k<24;k++){
   const mid=(left+right)*0.5;
   const bins=extent.reduce((prod,width)=>prod*Math.max(1,Math.ceil(width/mid)),1);
   if(bins>budget)left=mid;else right=mid;
  }
  const dims=extent.map(width=>Math.max(1,Math.ceil(width/right)));
  const binCount=dims[0]*dims[1]*dims[2];
  const membership=new Uint32Array(n),groups=new Map(),cells=[];
  function bin(v,axis){return Math.min(dims[axis]-1,Math.floor((v-lo[axis])/right));}
  await pass(1,row=>{
   const at=6*row,x=values[at],y=values[at+1],z=values[at+2];
   const page=wide?0:Math.floor(row/PAGE_VERTICES);
   const key=page*binCount+bin(x,0)+dims[0]*(bin(y,1)+dims[1]*bin(z,2));
   let cell=groups.get(key);
   if(!cell){
    cell={key,id:cells.length,page,count:0,coarseCount:0,lo:[x,y,z],hi:[x,y,z]};
    groups.set(key,cell);cells.push(cell);
   }
   const ordinal=cell.count++;
   // Retain at least one sample even in a sparsely populated or isolated tile.
   // Change the chosen offset between ten-point groups to avoid a fixed scan-line phase.
   const block=Math.floor(ordinal/10);
   const slot=block===0?0:((Math.imul(block^key,2654435761)>>>0)%10);
   const coarse=ordinal%10===slot;
   if(coarse)cell.coarseCount++;
   membership[row]=cell.id|(coarse?COARSE_BIT:0);
   for(let j=0;j<3;j++){
    const v=values[at+j];if(v<cell.lo[j])cell.lo[j]=v;if(v>cell.hi[j])cell.hi[j]=v;
   }
  });
  // Separate contiguous runs for full and coarse detail allow neighbouring
  // tiles at the same detail level to share one GPU draw.
  const ordered=cells.slice().sort((a,b)=>a.key-b.key);
  let full=0,coarse=n;
  for(const c of ordered){
   c.start=c.write=full;full+=c.count;
   c.coarseStart=c.coarseWrite=coarse;coarse+=c.coarseCount;
   c.centre=c.lo.map((v,i)=>(v+c.hi[i])/2);c.half=c.lo.map((v,i)=>(c.hi[i]-v)/2);
  }
  const indices=wide?new Uint32Array(coarse):new Uint16Array(coarse);
  await pass(2,row=>{
   const packed=membership[row],c=cells[packed&CELL_MASK],index=row-c.page*PAGE_VERTICES;
   indices[c.write++]=index;if(packed&COARSE_BIT)indices[c.coarseWrite++]=index;
  });
  checkpoint();onProgress(100);
  return {ordered,membership,indices,count:n,modes:new Uint8Array(cells.length)};
 }
 function viewFrame(basis,target,scale,aspect,camera,depthRange){
  // Orthographic projection has no physical eye. An equivalent 70-degree
  // vertical field of view supplies an orbit distance that follows zoom.
  const tanHalf=camera?Math.tan(camera.fov*Math.PI/360):REFERENCE_TAN;
  const eye=camera?target:target.map((v,j)=>v+basis.forward[j]*scale/(aspect*tanHalf));
  const plane=(v,sign,w)=>[sign*v[0],sign*v[1],sign*v[2],w];
  const side=(axis,sign,spread)=>axis.map((v,j)=>sign*v+basis.forward[j]*spread).concat(0);
  const r=basis.right,u=basis.up,f=basis.forward;
  let planes;
  if(camera){
   planes=[side(r,1,tanHalf*aspect),side(r,-1,tanHalf*aspect),side(u,1,tanHalf),side(u,-1,tanHalf),
    plane(f,1,-camera.near),plane(f,-1,camera.far)];
  }else{
   planes=[plane(r,1,scale),plane(r,-1,scale),plane(u,1,scale/aspect),plane(u,-1,scale/aspect),
    plane(f,1,depthRange),plane(f,-1,depthRange)];
  }
  return {target,eye,planes,distanceScale:tanHalf/REFERENCE_TAN};
 }
 function classifyCell(c,frame,forceFull){
  const dx=c.centre[0]-frame.target[0],dy=c.centre[1]-frame.target[1],dz=c.centre[2]-frame.target[2];
  for(const [a,b,d,w] of frame.planes){
   const support=Math.abs(a)*c.half[0]+Math.abs(b)*c.half[1]+Math.abs(d)*c.half[2];
   if(a*dx+b*dy+d*dz+w < -support-1e-5)return 0;
  }
  if(forceFull)return 2;
  // Nearest distance to the box is conservative at boundaries and at the near plane.
  let squared=0;
  for(let j=0;j<3;j++){
   const gap=Math.max(0,c.lo[j]-frame.eye[j],frame.eye[j]-c.hi[j]);squared+=gap*gap;
  }
  return squared*frame.distanceScale**2>FAR_METRES**2?1:2;
 }
 class SpatialPointDraw{
  constructor(gl,redraw,progress){
   this.gl=gl;this.redraw=redraw;this.progress=progress;this.revision=0;
   this.uint32=!!gl.getExtension('OES_element_index_uint');
  }
  reset(){
   this.controller?.abort();this.controller=null;
   if(this.buffer)this.gl.deleteBuffer(this.buffer);
   this.buffer=null;this.grid=null;this.submitted=0;this.revision++;
   this.progress(null);
  }
  async prepare(values){
   this.reset();if(!values.length)return;
   const controller=new AbortController();this.controller=controller;
   const onProgress=p=>{if(values.length>=24576&&!controller.signal.aborted)this.progress(p);};
   let buffer=null;
   try{
    onProgress(0);
    const wide=values.length/6>PAGE_VERTICES&&this.uint32;
    const grid=await buildPointGrid(values,{wide,signal:controller.signal,onProgress});
    if(controller.signal.aborted||this.gl.isContextLost())return;
    buffer=this.gl.createBuffer();if(!buffer)throw Error('No WebGL index buffer available.');
    this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER,buffer);
    this.gl.bufferData(this.gl.ELEMENT_ARRAY_BUFFER,grid.indices,this.gl.STATIC_DRAW);
    if(this.gl.getBufferParameter(this.gl.ELEMENT_ARRAY_BUFFER,this.gl.BUFFER_SIZE)!==grid.indices.byteLength)
     throw Error('Insufficient memory for display index.');
    this.bytes=grid.indices.BYTES_PER_ELEMENT;this.type=wide?this.gl.UNSIGNED_INT:this.gl.UNSIGNED_SHORT;
    // The copy resident on the graphics device is sufficient for every camera view.
    delete grid.indices;this.grid=grid;this.buffer=buffer;buffer=null;this.revision++;
    this.redraw();
   }catch(err){
    if(!controller.signal.aborted)this.progress(null,err?.message||'Display index unavailable.');
   }finally{
    if(buffer)this.gl.deleteBuffer(buffer);
    if(this.controller===controller){this.controller=null;this.progress(null);}
   }
  }
  contains(row){
   if(!this.grid)return true;
   const value=this.grid.membership[row],mode=this.grid.modes[value&CELL_MASK];
   return mode===2||(mode===1&&!!(value&COARSE_BIT));
  }
  draw(frame,forceFull,bindPage){
   const {grid,gl}=this;if(!grid)return false;
   gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,this.buffer);
   let page=-1,start=0,length=0,submitted=0,changed=false;
   const flush=()=>{if(length){gl.drawElements(gl.POINTS,length,this.type,start*this.bytes);length=0;}};
   for(const c of grid.ordered){
    const mode=classifyCell(c,frame,forceFull);
    if(mode!==grid.modes[c.id]){changed=true;grid.modes[c.id]=mode;}
    if(!mode)continue;
    const begin=mode===2?c.start:c.coarseStart;
    const count=mode===2?c.count:c.coarseCount;
    if(!count)continue;
    if(c.page!==page||begin!==start+length)flush();
    if(c.page!==page){page=c.page;bindPage(page*PAGE_VERTICES);}
    if(!length)start=begin;
    length+=count;submitted+=count;
   }
   flush();if(changed)this.revision++;
   this.submitted=submitted;return true;
  }
 }
 window.TunnelDisplayBuffers={decode,load,packed,recolour,recolourKeepBand,buildPointGrid,viewFrame,classifyCell,SpatialPointDraw};
})();
