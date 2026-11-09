'use strict';
/* Actual production WebGL renderer; generated preview points in an isolated project. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require(process.env.TUNNEL_PLAYWRIGHT_MODULE||'playwright');
const {fixture,setupPage}=require('./test_point_delete_ui_0187');

async function main(){
 const server=await fixture(),browser=await chromium.launch({headless:true,executablePath:process.env.TUNNEL_CHROMIUM,
  args:['--no-sandbox','--no-zygote','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-dev-shm-usage']});
 try{
  const {page,errors}=await setupPage(browser,server);
  await page.evaluate(()=>JobUI.sync({retainDisplay:true}));
  const results=await page.evaluate(async()=>{
   const scene=JobUI.cadScene(),gl=scene.gl,n=100000,rows=new Float32Array(n*6);
   for(let i=0;i<n;i++){
    // Near, distant, far to the side, and behind the scanner. Interleave each
    // group to defeat index-order bounding boxes and exercise actual spatial bins.
    const g=i%4,at=i*6,t=(Math.floor(i/4)%100)/100;
    rows.set([g===2?251+t:t,g===0?10:g===1?100:g===2?10:-80,t,1,0,0],at);
   }
   async function ready(){while(scene.spatialPoints.controller)await new Promise(resolve=>setTimeout(resolve,5));}
   JobUI.analysisPreview({packed:rows,sample_count:n,used:n,origin:[0,0,0],result_id:'lod-fixture',tolerance_mm:5},'inspection');await ready();if(scene.spatialPoints.grid?.count!==n)throw Error('Initial preview did not finish before the synthetic camera fixture');
   scene.meshCount=0;scene.meshWireCount=0;scene.target=[0,0,0];scene.yaw=0;scene.pitch=0;
   scene.setupCamera={orientation:[[1,0,0],[0,1,0],[0,0,1]],near:.1,far:500,fov:70};
   const before=window.pointGeometryUploads.length;
   const draw=()=>{scene.draw();return scene.spatialPoints.submitted;};
   const base=draw(),visibility=[0,0,0,0];
   for(let i=0;i<n;i++)if(scene.visiblePreviewRow(i))visibility[i%4]++;
   // The view change reuses the GPU point and index buffers.
   scene.setupCamera.fov=12;const telephoto=draw();
   scene.yaw=Math.PI;scene.setupCamera.fov=70;const reversed=draw();
   scene.setupCamera=null;scene.yaw=0;scene.scale=4;
   const orthoNear=draw();scene.scale=1000;const orthoFar=draw();
   const uploadDelta=window.pointGeometryUploads.length-before;
   // Simulate a graphics driver without uint32 draw indices. The last half
   // of the stream must index the correct position AND flag streams.
   scene.spatialPoints.uint32=false;scene.upload(scene.buffer,rows);await ready();
   scene.target=[0,0,0];scene.setupCamera={orientation:[[1,0,0],[0,1,0],[0,0,1]],near:.1,far:500,fov:70};
   const fallback=draw(),pageBases=new Set();
   scene.spatialPoints.draw(TunnelDisplayBuffers.viewFrame(scene.basis(),scene.target,scene.scale,scene.c.width/scene.c.height,scene.setupCamera,scene.depthRange()),false,
    first=>{pageBases.add(first);gl.bindBuffer(gl.ARRAY_BUFFER,scene.buffer);
     for(const [key,off] of [['pos',0],['colour',12]])gl.vertexAttribPointer(scene.attributeLocations[key],3,gl.FLOAT,false,24,first*24+off);
     gl.bindBuffer(gl.ARRAY_BUFFER,scene.deletionBuffer);gl.vertexAttribPointer(scene.attributeLocations.deletionFlag,1,gl.UNSIGNED_BYTE,false,1,first);});
   const glError=gl.getError();
   // Real framebuffer footprint for a red near sample and a green distant
   // sample. Force full detail during this measurement using separate tiles.
   const sizes=new Float32Array([-1,5,0,1,0,0, 25,125,0,0,1,0]);
   scene.setPacked(sizes,[0,0,0]);await ready();
   const oldSize=document.getElementById('job-point-size').value;
   document.getElementById('job-point-size').value='6';
   scene.draw();
   function pixels(point,channel){
    const [x,y]=scene.projectLocal(point),block=new Uint8Array(21*21*4);
    gl.readPixels(Math.round((x+1)*scene.c.width/2)-10,Math.round((y+1)*scene.c.height/2)-10,21,21,gl.RGBA,gl.UNSIGNED_BYTE,block);
    let sum=0;for(let i=0;i<block.length;i+=4)if(block[i+channel]>180&&block[i+1-channel]<60)sum++;return sum;
   }
   const nearPixels=pixels([-1,5,0],0),farPixels=pixels([25,125,0],1);
   document.getElementById('job-point-size').value=oldSize;
   // Original indices still match owner ranges when the CPU preview contains
   // non-rendered mesh vertices between scan ranges.
   scene.setPacked(new Float32Array([0,5,0,1,0,0,1,5,0,1,0,1,2,5,0,0,1,0]),[0,0,0],[[0,1,'A'],[1,2,'M'],[2,3,'B']]);
   await ready();const ids=[0,1,2].map(i=>scene.gpuRowForPreview(i));
   // Supersession, user cancellation, and a new successful retry. Nothing
   // stale may take the place of the most recently supplied geometry.
   const progress=[];let stopped=false;const original=scene.onPointIndexProgress;
   scene.onPointIndexProgress=(pct,msg)=>{original(pct,msg);if(pct!==null)progress.push(pct);
    if(pct>0&&pct<100&&!stopped){stopped=true;scene.spatialPoints.reset();}};
   await scene.spatialPoints.prepare(new Float32Array(1000000*6));
   const cancelled=stopped&&!scene.spatialPoints.grid&&!scene.spatialPoints.controller;
   scene.onPointIndexProgress=original;
   const stale=scene.spatialPoints.prepare(rows),replacement=scene.spatialPoints.prepare(sizes);
   await Promise.all([stale,replacement]);
   const latest=scene.spatialPoints.grid?.count;
   return {base,visibility,telephoto,reversed,orthoNear,orthoFar,uploadDelta,fallback,pageBases:[...pageBases],glError,nearPixels,farPixels,ids,cancelled,progress,latest};
  });
  assert.deepEqual(errors,[]);
  if(process.env.TUNNEL_LOD_DEBUG)console.log(results);
  assert.equal(results.visibility[0],25000);
  assert(results.visibility[1]>=2400&&results.visibility[1]<=2700);
  assert.deepEqual(results.visibility.slice(2),[0,0]);
  assert.equal(results.base,results.visibility[0]+results.visibility[1]);
  assert.equal(results.telephoto,50000);
  assert(results.reversed>2000&&results.reversed<3000);
  assert(results.orthoNear>results.orthoFar);
  assert.equal(results.uploadDelta,0);
  assert(Math.abs(results.fallback-results.base)<100);
  assert.deepEqual(results.pageBases,[0,65536]);
  assert.equal(results.glError,0);assert(results.nearPixels>results.farPixels&&results.farPixels>0);
  assert.deepEqual(results.ids,[0,-1,1]);
  assert(results.cancelled);assert.equal(results.latest,2);
  const report={status:'passed',browser:await browser.version(),platform:'Linux Chromium, software WebGL',results};
  if(process.env.TUNNEL_LOD_REPORT)fs.writeFileSync(process.env.TUNNEL_LOD_REPORT,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report));
 }finally{await browser.close();server.child.kill('SIGTERM');}
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
