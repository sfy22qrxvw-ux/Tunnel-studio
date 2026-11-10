'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const playwright=require(process.env.TUNNEL_PLAYWRIGHT_MODULE||'playwright');
const fixtureSource="\"\"\"Isolated synthetic state for production renderer browser regressions.\"\"\"\nimport copy,json,sys\nfrom pathlib import Path\nimport numpy as np\n\nsys.path.insert(0,sys.argv[1])\nimport server as s\nfrom core import Alignment\nfrom test_registration_virtual_0176 import add_scan\ns.STATE.update(job_items=[],scan_links=[],survey_controls={})\n\npoints=np.array([[-1.,5.,-1.],[1.,5.,-1.],[-1.,5.,1.],[1.,5.,1.]])\ncolours=np.array([[1.,0.,0.],[0.,1.,0.],[0.,0.,1.],[1.,1.,0.]])\nfor name,offset in [('A',0.),('B',100.)]:\n    shift=np.array([offset,0.,0.]);item=add_scan(s,name,shift)\n    q=points+shift;q.astype('<f8').tofile(item['meta']['file'])\n    np.column_stack((np.ones(4),colours)).astype('<f4').tofile(item['meta']['attributes_file'])\n    item['meta'].update(minimum=q.min(0).tolist(),maximum=q.max(0).tolist(),has_scanner_pose=True,source_format='e57',invalid_points_skipped=0)\n    item['name']='Setup '+name;item['registration_state'].update(rows=[],candidates=[],cloud=copy.deepcopy(item['meta']))\n\ns.STATE.update(axis=Alignment([dict(chainage=0,e=0,n=0,h=0,radius=3),dict(chainage=20,e=0,n=20,h=0,radius=3)],'Synthetic axis'),cloud=copy.deepcopy(s.STATE['job_items'][0]['meta']),active_job_scan='A',valid_points=4)\nderived=s.WORK/'fixture.analysis64';np.array([[5.,0.,0.,.001]]*4,dtype='<f8').tofile(derived);s.STATE['derived']=str(derived)\ns.save()\nclass QuietHandler(s.Handler):\n    def log_message(self,*args):pass\nhttp=s.ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)\nprint(json.dumps({'url':f'http://127.0.0.1:{http.server_port}','token':s.TOKEN}),flush=True)\nhttp.serve_forever()\n";
const root=path.resolve(__dirname,'../..'),evidence=process.env.TUNNEL_TEST_EVIDENCE||path.join(root,'validation/render_0186');
const colours=[[1,0,0],[0,1,0],[0,0,1],[1,1,0]],coords=[[-1,5,-1],[1,5,-1],[-1,5,1],[1,5,1]];
async function until(check,label,timeout=5000){const end=Date.now()+timeout;while(!check()){if(Date.now()>end)throw Error('Timed out: '+label);await new Promise(resolve=>setTimeout(resolve,10));}}
function pointsBuffer(id,quick=false){
 const ids=id?[id]:['A','B'],rows=[],owners=[];
 for(const name of ids){const start=rows.length,n=quick?2:4;for(let i=0;i<n;i++)rows.push([coords[i][0]+(name==='B'?100:0),coords[i][1],coords[i][2],...colours[i]]);owners.push([start,rows.length,name]);}
 const metadata=Buffer.from(JSON.stringify(owners)),buffer=Buffer.alloc(56+rows.length*24+metadata.length);
 buffer.write('TSPV');buffer.writeUInt32LE(2,4);buffer.writeDoubleLE(rows.length,32);buffer.writeDoubleLE(ids.length*4,40);
 rows.flat().forEach((v,i)=>buffer.writeFloatLE(v,56+i*4));metadata.copy(buffer,56+rows.length*24);return buffer;
}
function displayBuffer(){
 const rows=coords.map((q,i)=>[...q,...colours[i]]),metadata={origin:[0,0,0],sample_count:4,used:4,source_id:'A',result_id:'analysis-fixture',signed:true,tolerance_mm:10,colour_mode:'ramp'},json=Buffer.from(JSON.stringify(metadata)),offset=24+Math.ceil(json.length/8)*8,b=Buffer.alloc(offset+rows.length*24+rows.length*8);
 b.write('TSDP');b.writeUInt32LE(1,4);b.writeUInt32LE(4,8);b.writeUInt32LE(json.length,12);b.writeUInt32LE(1,16);json.copy(b,24);
 rows.flat().forEach((v,i)=>b.writeFloatLE(v,offset+i*4));[-20,-5,0,20].forEach((v,i)=>b.writeDoubleLE(v,offset+rows.length*24+i*8));return {b,rows,metadata,deviations:[-20,-5,0,20]};
}
async function main(){
 fs.mkdirSync(evidence,{recursive:true});const work=fs.mkdtempSync(path.join(os.tmpdir(),'tunnel-render-0186-'));const fixturePath=path.join(work,'browser_server_fixture.py');fs.writeFileSync(fixturePath,fixtureSource);
 const child=spawn(process.env.TUNNEL_TEST_PYTHON||path.join(root,'validation/venv/bin/python'),[fixturePath,__dirname],{env:{...process.env,TUNNEL_STUDIO_DATA:work},stdio:['ignore','pipe','pipe']});
 let stderr='';child.stderr.on('data',b=>stderr+=b);
 const endpoint=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture server timed out: '+stderr)),30000);child.on('exit',code=>{clearTimeout(timer);reject(Error('Fixture server exited '+code+': '+stderr));});readline.createInterface({input:child.stdout}).on('line',line=>{try{const v=JSON.parse(line);if(v.url){clearTimeout(timer);resolve(v);}}catch{}});});
 const browser=await playwright.chromium.launch({headless:true,executablePath:process.env.TUNNEL_CHROMIUM||undefined,args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],requests=[],pending=[];let holdSetup=null,holdNormal=false,holdDelete=false,deleteRoute=null,inventoryPending=false;
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
 await page.addInitScript(()=>{
  window.pointUploads=[];window.allBufferUploads=[];
  for(const proto of [WebGLRenderingContext.prototype,WebGL2RenderingContext.prototype]){const original=proto.bufferData;
   proto.bufferData=function(target,data,usage){const buffer=this.getParameter(this.ARRAY_BUFFER_BINDING);window.allBufferUploads.push(data?.byteLength||0);if(window.JobUI?.cadScene?.().buffer===buffer)window.pointUploads.push(data?.byteLength||0);return original.call(this,target,data,usage);};}
  window.pixelNearPoint=world=>{const scene=window.JobUI.cadScene(),p=window.JobUI.projectWorld(world),c=scene.c,gl=scene.gl;scene.draw();const x=Math.round(p[0]/c.clientWidth*c.width),y=Math.round(c.height-p[1]/c.clientHeight*c.height),pixels=new Uint8Array(7*7*4);gl.readPixels(x-3,y-3,7,7,gl.RGBA,gl.UNSIGNED_BYTE,pixels);let brightest=[0,0,0];for(let i=0;i<pixels.length;i+=4)if(pixels[i]+pixels[i+1]+pixels[i+2]>brightest[0]+brightest[1]+brightest[2])brightest=[pixels[i],pixels[i+1],pixels[i+2]];return brightest;};
 });
 await page.route('**/api/job-preview-bin?*',async route=>{
  const url=new URL(route.request().url()),id=url.searchParams.get('setup_id'),quick=url.searchParams.get('quick')==='1';
  const record={id,quick,view:url.searchParams.get('view'),url:url.toString(),route};
  if(!quick&&((holdSetup&&id===holdSetup)||(!id&&holdNormal))){pending.push(record);return;}
  await route.fulfill({status:200,contentType:'application/octet-stream',body:pointsBuffer(id,quick)});
 });
 await page.route('**/api/job-action',async route=>{const body=route.request().postDataJSON();if(holdDelete&&body?.action==='delete_selection_many'){deleteRoute=route;return;}await route.continue();});
 // Controlled legacy count metadata: the displayed four retained rows belong
 // to a larger edited source. Changing count completeness is not a geometry edit.
 await page.route('**/api/job',async route=>{
  const response=await route.fetch(),body=await response.json(),item=body.items.find(x=>x.id==='A');
  if(item)Object.assign(item,{original_count:400,count:inventoryPending?400:4,count_exact:!inventoryPending,bounds_exact:false,metadata_pending:inventoryPending,metadata_error:null,excluded_count:inventoryPending?null:396});
  await route.fulfill({response,json:body});
 });
 const report={status:'running',scenarios:[],browser:'Chromium 141, Linux, software WebGL',fixture:'Two synthetic four-point setups; delayed binary responses are controlled. This is not a large-cloud throughput benchmark.'};
 try{
  await page.goto(endpoint.url,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>window.JobUI?.cadScene().pointCount===8,null,{timeout:20000});
  await page.waitForFunction(()=>window.SetupCameraUI&&window.TargetUI);
  await page.evaluate(()=>poll());
  assert.equal(requests.some(u=>/\/api\/(overview|section)(\?|$)/.test(u)),false,'Hidden legacy survey previews must not download in the main workspace');
  assert.equal(requests.some(u=>u.includes('/api/reg-preview')),false,'Hidden registration preview must not download');
  report.scenarios.push('No hidden overview, section or registration preview requests');

  await page.route('**/api/setup-camera',async route=>{
   if(route.request().postDataJSON()?.action!=='open')return route.continue();
   const response=await route.fetch(),frame=await response.json();
   await route.fulfill({response,json:{...frame,has_setup_photos:true}});
  });
  const beforeSetup=requests.length,project=path.join(work,'project.json'),beforeOpen=fs.readFileSync(project);
  holdSetup='A';
  let activationRoute;
  await page.route('**/api/scan-registration',async route=>{
   if(route.request().method()==='POST'&&route.request().postDataJSON()?.action==='activate_setup'){activationRoute=route;return;}
   await route.continue();
  });
  await page.evaluate(()=>{window.setupOpened=false;window.setupOpening=window.SetupCameraUI.open('A').then(value=>{window.setupOpened=value;});});
  await until(()=>activationRoute,'delayed target panel activation');
  await page.waitForFunction(()=>window.JobUI.setupCamera()?.id==='A'&&window.JobUI.cadScene().pointCount===2);
  assert.equal(await page.evaluate(()=>window.setupOpened),false,'The view can draw before target activation finishes');
  await activationRoute.continue();
  await page.unroute('**/api/scan-registration');
  await page.waitForFunction(()=>window.setupOpened===true,null,{timeout:10000});
  await until(()=>pending.some(x=>x.id==='A'),'fine setup request after coarse frame');
  assert(pending.some(x=>x.id==='A'),'Fine setup response must still be held');
  assert.equal(await page.evaluate(()=>window.JobUI.cadScene().pointCount),2);
  assert.equal(await page.locator('#setup-camera-pick').isEnabled(),true);
  assert.equal(await page.locator('#setup-camera-display').inputValue(),'points');
  assert.equal(await page.locator('#setup-camera-display option[value=photo]').isDisabled(),false);
  assert(!requests.slice(beforeSetup).some(u=>/\/api\/(job$|setup-photos|setup-photo-face)/.test(u)),
   'Opening point setup should reuse inventory and defer photographs');
  assert.deepEqual(fs.readFileSync(project),beforeOpen,'Opening a setup must not save project data');
  await page.unroute('**/api/setup-camera');
  const coarse=await page.evaluate(()=>Array.from(window.JobUI.cadScene().pointValues));
  assert.deepEqual(coarse,coords.slice(0,2).flatMap((q,i)=>[...q,...colours[i]]));
  const redPixel=await page.evaluate(world=>window.pixelNearPoint(world),coords[0]);assert(redPixel[0]>200&&redPixel[1]<80&&redPixel[2]<80,'Coarse target points must actually render their RGB colour');
  assert.equal(JSON.parse(pending.at(-1).view).projection,'perspective');
  // Rendering/picking remain usable while the full response is pending.
  assert.equal(await page.evaluate(()=>window.JobUI.armPointPick(()=>{})),true);
  await page.evaluate(()=>window.JobUI.setMode('orbit'));
  const toggle=await page.evaluate(async()=>await Promise.race([window.JobUI.setSetupDisplay('points').then(()=>true),new Promise(resolve=>setTimeout(()=>resolve(false),100))]));
  assert.equal(toggle,true,'Existing coarse points must allow a display-mode switch without waiting for fine');
  report.scenarios.push('Setup opens on coloured coarse perspective points while fine response is held');

  // A failed fine request must be retryable without moving the current view.
  for(const item of pending.splice(0))await item.route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Controlled detail failure'})});
  await page.waitForFunction(()=>document.getElementById('job-progress').textContent.includes('Controlled detail failure'));
  await page.locator('#message-close').click();
  await page.locator('#setup-camera-detail').click();
  await until(()=>pending.some(x=>x.id==='A'),'manual retry of unchanged setup view');
  assert.equal(await page.evaluate(()=>window.JobUI.cadScene().pointCount),2);
  report.scenarios.push('Refresh point detail retries a failed response without requiring camera movement');

  holdSetup=null;for(const item of pending.splice(0))await item.route.fulfill({status:200,contentType:'application/octet-stream',body:pointsBuffer(item.id,false)}).catch(()=>{});
  await page.waitForFunction(()=>window.JobUI.cadScene().pointCount===4);
  assert.deepEqual(await page.evaluate(()=>Array.from(window.JobUI.cadScene().pointValues)),coords.flatMap((q,i)=>[...q,...colours[i]]));
  report.scenarios.push('Fine response preserves measured coordinates and RGB colours');

  const beforePan=requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length;
  const box=await page.locator('#job-canvas').boundingBox();
  await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);await page.mouse.down();await page.mouse.move(box.x+box.width*.65,box.y+box.height*.5,{steps:8});await page.mouse.up();
  await until(()=>requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length>=beforePan+1,'automatic detail after drag');
  await page.waitForFunction(()=>document.getElementById('job-canvas').getAttribute('aria-busy')==='false');
  await page.waitForTimeout(100);
  const afterPan=requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length;
  assert.equal(afterPan-beforePan,1,'A drag must reuse displayed overview points and request fine detail once');
  report.scenarios.push('Drag reuses displayed context and automatically requests fine detail once');

  const beforeWheel=requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length;
  for(let i=0;i<5;i++)await page.mouse.wheel(0,-60);
  await page.waitForTimeout(500);
  const afterWheel=requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length;
  // Zooming in can reuse a covering loaded frustum; zooming out must fetch.
  assert(afterWheel-beforeWheel<=1,'Wheel events must be coalesced or reuse existing detail');
  const beforeOut=afterWheel;await page.mouse.wheel(0,1000);
  await until(()=>requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length>=beforeOut+1,'automatic detail after wider zoom');
  await page.waitForFunction(()=>document.getElementById('job-canvas').getAttribute('aria-busy')==='false');
  assert.equal(requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length-beforeOut,1);
  report.scenarios.push('Wheel reuses covering detail and automatically loads a wider view');

  const beforeResize=requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length;
  await page.setViewportSize({width:1750,height:1000});
  await until(()=>requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length>=beforeResize+1,'automatic detail after wider viewport');
  await page.waitForFunction(()=>document.getElementById('job-canvas').getAttribute('aria-busy')==='false');
  assert.equal(requests.filter(u=>u.includes('/api/job-preview-bin')&&u.includes('setup_id=A')).length-beforeResize,1,'Wider canvas needs a new horizontal frustum');
  report.scenarios.push('Wider viewport automatically refreshes horizontal perspective detail');

  // A late A response cannot replace B after switching setups.
  holdSetup='A';await page.evaluate(()=>{const scene=window.JobUI.cadScene();scene.yaw+=.3;scene.onNavigate();});
  await page.waitForTimeout(500);assert(pending.some(x=>x.id==='A'));
  await page.evaluate(()=>window.SetupCameraUI.open('B'));
  await page.waitForFunction(()=>window.JobUI.setupCamera()?.id==='B'&&window.JobUI.cadScene().pointCount===4);
  holdSetup=null;for(const item of pending.splice(0))await item.route.fulfill({status:200,contentType:'application/octet-stream',body:pointsBuffer(item.id,false)}).catch(()=>{});
  await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>window.JobUI.setupCamera()?.id),'B');
  const bpoints=await page.evaluate(()=>Array.from(window.JobUI.cadScene().pointValues));assert(bpoints[0]>90);
  report.scenarios.push('Late response from previous setup cannot overwrite current setup');

  await page.evaluate(()=>window.SetupCameraUI.exit());
  await page.waitForFunction(()=>window.JobUI.cadScene().pointCount===8&&!window.JobUI.setupCamera());
  const uploads=await page.evaluate(()=>{const before=window.pointUploads.length;window.JobUI.clearSelection();window.JobUI.clearSelection();return window.pointUploads.length-before;});
  assert.equal(uploads,0,'Clearing an already-clear selection must reuse the GPU point buffer');
  report.scenarios.push('Repeated selection clear performs zero point-buffer uploads');

  await page.evaluate(()=>window.JobUI.setAutoDetail(false));
  const beforeCountRequests=requests.filter(u=>u.includes('/api/job-preview-bin')).length,beforeCountUploads=await page.evaluate(()=>window.pointUploads.length);
  inventoryPending=true;await page.evaluate(()=>window.JobUI.sync({inventoryOnly:true}));
  assert.match(await page.locator('#job-preview-info').textContent(),/checking remaining count/);
  assert.match(await page.locator('#job-list').textContent(),/Up to 400 points/);
  inventoryPending=false;
  await page.waitForFunction(()=>!document.getElementById('job-preview-info').textContent.includes('checking remaining count'),null,{timeout:3000});
  assert.equal(requests.filter(u=>u.includes('/api/job-preview-bin')).length,beforeCountRequests,'Count completion must not reload scan geometry');
  assert.equal(await page.evaluate(()=>window.pointUploads.length),beforeCountUploads,'Count completion must not upload a point buffer');
  report.scenarios.push('Legacy remaining count refreshes automatically without geometry requests or GPU uploads');

  const fixture=displayBuffer();
  const comparison=await page.evaluate(({bytes,rows,metadata,deviations})=>{
   const decode=window.TunnelDisplayBuffers.decode(Uint8Array.from(bytes).buffer),json={...metadata,points:rows,deviations_mm:deviations};
   const oldFlat=Array.prototype.flat;Array.prototype.flat=function(){throw Error('Display path flattened a point list');};
   try{
    const typed=window.TunnelDisplayBuffers.recolour(decode,10,true),legacy=window.TunnelDisplayBuffers.recolour(json,10,true);
    const before=window.pointUploads.length;window.JobUI.analysisPreview(typed,'inspection');const firstUploads=window.pointUploads.length-before;
    const beforeAgain=window.pointUploads.length;window.JobUI.analysisPreview(window.TunnelDisplayBuffers.recolour(decode,20,false),'inspection');
    return {typed:Array.from(typed.packed),legacy:Array.from(legacy.packed),firstUploads,recolourUploads:window.pointUploads.length-beforeAgain,source:Array.from(decode.packed),owner:window.JobUI.previewOwner()};
   }finally{Array.prototype.flat=oldFlat;}
  },{bytes:[...fixture.b],rows:fixture.rows,metadata:fixture.metadata,deviations:fixture.deviations});
  assert.deepEqual(comparison.typed,comparison.legacy);assert.deepEqual(comparison.source,fixture.rows.flat());assert.equal(comparison.owner,'inspection');
  assert.equal(comparison.firstUploads,1);assert.equal(comparison.recolourUploads,1,'Recolour must not restore/re-upload the source scan first');
  report.scenarios.push('Binary analysis equals JSON colours, avoids flattening, uploads once per recolour');

  const analysisPoints=await page.evaluate(()=>Array.from(window.JobUI.cadScene().pointValues)),beforeAnalysisCountUploads=await page.evaluate(()=>window.pointUploads.length);
  inventoryPending=true;await page.evaluate(()=>window.JobUI.sync({inventoryOnly:true}));inventoryPending=false;
  await page.waitForFunction(()=>!document.getElementById('job-list').textContent.includes('counting…'),null,{timeout:3000});
  assert.equal(await page.evaluate(()=>window.JobUI.previewOwner()),'inspection');
  assert.deepEqual(await page.evaluate(()=>Array.from(window.JobUI.cadScene().pointValues)),analysisPoints);
  assert.equal(await page.evaluate(()=>window.pointUploads.length),beforeAnalysisCountUploads);
  report.scenarios.push('Background count completion preserves the active analysis layer and GPU buffer');

  // An analysis overlay wins over a late fine response, and Restore resumes detail.
  await page.evaluate(()=>window.JobUI.clearCleaningPreview());
  holdNormal=true;await page.evaluate(()=>{const scene=window.JobUI.cadScene();scene.scale*=.6;window.JobUI.loadViewDetail(true);});
  await until(()=>pending.some(x=>!x.id),'normal fine request held after quick frame');
  assert.equal(await page.evaluate(()=>window.JobUI.cadScene().pointCount),4);
  await page.evaluate(bytes=>{const data=window.TunnelDisplayBuffers.decode(Uint8Array.from(bytes).buffer);window.JobUI.analysisPreview(window.TunnelDisplayBuffers.recolour(data,10,true),'inspection');},[...fixture.b]);
  const overlay=await page.evaluate(()=>Array.from(window.JobUI.cadScene().pointValues));
  holdNormal=false;for(const item of pending.splice(0))await item.route.fulfill({status:200,contentType:'application/octet-stream',body:pointsBuffer(item.id,false)}).catch(()=>{});
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(()=>window.JobUI.previewOwner()),'inspection');
  assert.deepEqual(await page.evaluate(()=>Array.from(window.JobUI.cadScene().pointValues)),overlay,'Late source response must not replace analysis');
  report.scenarios.push('Late normal scan response cannot replace an active analysis overlay');
  await page.evaluate(()=>window.JobUI.clearCleaningPreview());
  await page.waitForFunction(()=>window.JobUI.cadScene().pointCount===8,null,{timeout:3000});
  report.scenarios.push('Restore from analysis resumes missing fine detail after aborted load');

  // The GPU hide mask remains active when selection controls are cleared mid-delete.
  await page.evaluate(points=>{window.JobUI.setAutoDetail(false);window.JobUI.focusWorld(points);window.JobUI.setMode('box');},coords);
  const pick=await page.evaluate(world=>{const p=window.JobUI.projectWorld(world),r=document.getElementById('job-canvas').getBoundingClientRect();return {x:p[0]+r.left,y:p[1]+r.top};},coords[0]);
  await page.mouse.move(pick.x-12,pick.y-12);await page.mouse.down();await page.mouse.move(pick.x+12,pick.y+12,{steps:3});await page.mouse.up();
  await page.waitForFunction(()=>window.JobUI.cadScene().deletionFlagCount>0);
  const owners=await page.evaluate(()=>window.JobUI.cadScene().pointOwnerRanges);assert.deepEqual(owners,[[0,4,'A'],[4,8,'B']]);
  const beforeDelete=await page.evaluate(world=>window.pixelNearPoint(world),coords[0]);assert(beforeDelete[0]>200&&beforeDelete[1]>150);
  holdDelete=true;await page.evaluate(()=>{window.deleteFinished=false;window.JobUI.selAction('delete_selection').then(()=>window.deleteFinished=true);});
  await until(()=>deleteRoute,'point-deletion request');
  assert.equal(await page.evaluate(()=>window.JobUI.cadScene().pendingDelete),true);
  await page.evaluate(()=>window.JobUI.clearSelection());
  const hidden=await page.evaluate(world=>window.pixelNearPoint(world),coords[0]);assert(hidden[0]<180&&hidden[1]<150,'Pending deleted point must stay hidden after selection clear');
  await deleteRoute.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Controlled fixture cancellation'})});holdDelete=false;
  await page.waitForFunction(()=>window.deleteFinished===true);
  assert.equal(await page.evaluate(()=>window.JobUI.cadScene().pendingDelete),false);
  const restored=await page.evaluate(world=>window.pixelNearPoint(world),coords[0]);assert(restored[0]>200&&restored[1]<80);
  report.scenarios.push('Pending deletion stays hidden through selection clear; failure restores source display');

  assert.deepEqual(errors,[]);report.status='passed';
 }catch(error){report.status='failed';report.error=error.message;throw error;
 }finally{
  fs.writeFileSync(path.join(evidence,'browser_renderer_result.json'),JSON.stringify({...report,page_errors:errors,request_counts:{point_previews:requests.filter(u=>u.includes('/api/job-preview-bin')).length,legacy_overview:requests.filter(u=>u.includes('/api/overview')).length,legacy_section:requests.filter(u=>u.includes('/api/section?')).length,hidden_registration:requests.filter(u=>u.includes('/api/reg-preview')).length}},null,2));
  await browser.close();child.kill('SIGTERM');
 }
 console.log(JSON.stringify(report,null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
