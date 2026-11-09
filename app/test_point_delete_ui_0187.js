'use strict';
// Actual browser + production API regression. The fixture owns synthetic files
// in a temporary data directory; no user project or installed application is used.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),readline=require('node:readline'),crypto=require('node:crypto');
const {chromium}=require(process.env.TUNNEL_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),evidence=path.join(root,'validation/delete_0187/ui');
const fixtureSource=String.raw`
import copy,hashlib,json,sys,time
from pathlib import Path
import numpy as np
sys.path.insert(0,sys.argv[1])
import server as s
from core import Alignment
count=int(sys.argv[2]);unknown=sys.argv[3]=='unknown'
s.STATE.update(job_items=[],scan_links=[],survey_controls={})
base=np.array([[-1.,5.,-1.],[1.,5.,-1.],[-1.,5.,1.],[1.,5.,1.]])
colours=np.array([[1.,0.,0.],[0.,1.,0.],[0.,0.,1.],[1.,1.,0.]])
if count>4:
    row=np.arange(count);side=int(np.sqrt(count))
    base=np.column_stack(((row%side)/(side-1)*20-10,np.full(count,5.),(row//side)/(side-1)*20-10))
for name,offset in ([('A',0.)] if count>4 else [('A',0.),('B',100.)]):
    shift=np.array([offset,0.,0.]);q=base+shift
    f=s.WORK/(name+'.xyz64');q.astype('<f8').tofile(f)
    a=s.WORK/(name+'.attr32');np.column_stack((np.ones(count),colours[np.arange(count)%4])).astype('<f4').tofile(a)
    meta=dict(name='Setup '+name,file=str(f),attributes_file=str(a),count=count,minimum=q.min(0).tolist(),maximum=q.max(0).tolist(),attribute_counts=[count]*4,has_scanner_pose=True,source_format='e57',invalid_points_skipped=0)
    item=dict(id=name,kind='scan',name='Setup '+name,meta=meta,visible=True,source_bytes=0,hidden_boxes=[],isolate_box=None,edit_history=[],redo_history=[],scan_origin=shift.tolist(),scan_orientation=np.eye(3).tolist())
    item['registration_state']=dict(cloud=copy.deepcopy(meta),rows=[],candidates=[],solution=None,transformed=None,revision='r')
    s.STATE['job_items'].append(item)
if count==4 and not unknown:
    quad=np.array([[-2.,6.,2.],[2.,6.,2.],[2.,6.,3.],[-2.,6.,3.]],dtype='<f8')
    vertices=np.concatenate([quad+np.array([4.*j,0.,0.]) for j in range(3)])
    faces=np.array([[4*j,4*j+1,4*j+2] for j in range(3)]+[[4*j,4*j+2,4*j+3] for j in range(3)],dtype='<u4')
    vf=s.WORK/'mesh.vertices64';ff=s.WORK/'mesh.faces32';vertices.tofile(vf);faces.tofile(ff)
    meta=dict(name='Reference triangle',vertices_file=str(vf),faces_file=str(ff),vertices=len(vertices),triangles=len(faces),units='metres',minimum=vertices.min(0).tolist(),maximum=vertices.max(0).tolist())
    s.STATE['job_items'].append(dict(id='M',kind='mesh',name='Reference triangle',meta=meta,visible=True,source_bytes=0,hidden_boxes=[],isolate_box=None,edit_history=[],redo_history=[]))
axis=Alignment([dict(chainage=0,e=0,n=0,h=0,radius=3),dict(chainage=20,e=0,n=20,h=0,radius=3)],'Synthetic axis')
derived=s.WORK/'fixture.analysis64';np.tile(np.array([5.,0.,0.,.001]),(count,1)).astype('<f8').tofile(derived)
s.STATE.update(axis=axis,cloud=copy.deepcopy(s.STATE['job_items'][0]['meta']),active_job_scan='A',derived=str(derived),valid_points=None if unknown else count)
s.save()
operation_timings=[];start_job=s.start_job
def timed_job(action,*args,**kwargs):
    def run():
        started=time.perf_counter()
        try:return action()
        finally:operation_timings.append(dict(seconds=time.perf_counter()-started,finished_utc=time.time()))
    return start_job(run,*args,**kwargs)
s.start_job=timed_job
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
class QuietHandler(s.Handler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path=='/api/test-state':
            items=[]
            for item in s.STATE['job_items']:
                if item['kind']!='scan':continue
                m=item['meta'];mask=np.fromfile(m['mask_file'],'u1') if m.get('mask_file') else np.zeros(m['count'],'u1');ids=np.flatnonzero(mask)
                items.append(dict(id=item['id'],excluded=ids.tolist() if count<=4 else None,excluded_count=int(len(ids)),mask_sha256=hashlib.sha256(mask.tobytes()).hexdigest(),mask_file=m.get('mask_file'),xyz_sha256=digest(m['file']),attributes_sha256=digest(m['attributes_file']),edits=len(item.get('edit_history',[])),redo=len(item.get('redo_history',[]))))
            return self.send(dict(items=items,busy=s.JOB['busy'],valid_points=s.STATE.get('valid_points'),derived=s.STATE.get('derived'),derived_sha256=digest(derived),active_job_scan=s.STATE.get('active_job_scan'),cloud_file=(s.STATE.get('cloud') or {}).get('file'),cloud_mask=(s.STATE.get('cloud') or {}).get('mask_file'),history=s.workspace_history.status(s),operation_timings=operation_timings))
        return super().do_GET()
http=s.ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
print(json.dumps({'url':f'http://127.0.0.1:{http.server_port}'}),flush=True)
http.serve_forever()
`;
async function until(check,label,timeout=10000){const end=Date.now()+timeout;while(!check()){if(Date.now()>end)throw Error('Timed out: '+label);await new Promise(resolve=>setTimeout(resolve,10));}}
async function fixture(count=4,unknown=false){
 const work=fs.mkdtempSync(path.join(os.tmpdir(),'tunnel-delete-ui-0187-'));fs.writeFileSync(path.join(work,'fixture.py'),fixtureSource);
 const child=spawn(process.env.TUNNEL_TEST_PYTHON||path.join(root,'validation/venv/bin/python'),[path.join(work,'fixture.py'),__dirname,String(count),unknown?'unknown':'known'],{env:{...process.env,TUNNEL_STUDIO_DATA:work},stdio:['ignore','pipe','pipe']});
 let stderr='';child.stderr.on('data',b=>stderr+=b);
 const endpoint=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timed out: '+stderr)),30000);child.on('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr));});readline.createInterface({input:child.stdout}).on('line',line=>{try{const d=JSON.parse(line);if(d.url){clearTimeout(timer);resolve(d);}}catch{}});});
 return {child,work,...endpoint};
}
async function setupPage(browser,server){
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],previews=[],posts=[],held=[];
 const control={holdPreview:false,holdPost:false};
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  window.deleteTimings=[];window.deleteActive=-1;
  const original=window.fetch;window.fetch=async(...args)=>{
   const url=String(args[0]),timing=window.deleteTimings[window.deleteActive];let poll;
   if(url==='/api/job-action'&&timing)timing.post_sent=performance.now();
   if(url.startsWith('/api/status')&&timing){poll={requested:performance.now()};(timing.polls??=[]).push(poll);}
   const response=await original(...args);if(url==='/api/job-action'&&timing)timing.post_ack=performance.now();
   if(poll){poll.response=performance.now();response.clone().json().then(s=>{poll.parsed=performance.now();poll.busy=s.job.busy;if(!s.job.busy&&!timing.server_ready)timing.server_ready=performance.now();});}
   return response;
  };
  document.addEventListener('pointerup',e=>{if(e.target.id==='job-canvas')window.lastSelectionPointerUp=performance.now();},true);
  window.pixelNearPoint=world=>{const scene=window.JobUI.cadScene(),p=window.JobUI.projectWorld(world),c=scene.c,gl=scene.gl;scene.draw();const x=Math.round(p[0]/c.clientWidth*c.width),y=Math.round(c.height-p[1]/c.clientHeight*c.height),pixels=new Uint8Array(7*7*4);gl.readPixels(x-3,y-3,7,7,gl.RGBA,gl.UNSIGNED_BYTE,pixels);let brightest=[0,0,0];for(let i=0;i<pixels.length;i+=4)if(pixels[i]+pixels[i+1]+pixels[i+2]>brightest[0]+brightest[1]+brightest[2])brightest=[pixels[i],pixels[i+1],pixels[i+2]];return brightest;};
 });
 await page.route('**/api/job-preview-bin?*',async route=>{
  const u=new URL(route.request().url()),item={route,url:u.toString(),view:u.searchParams.get('view'),setup:u.searchParams.get('setup_id'),released:false};previews.push(item);
  if(control.holdPreview){held.push(item);return;}
  await route.continue();
 });
 await page.route('**/api/job-action',async route=>{const body=route.request().postDataJSON();if(control.holdPost&&body.action==='delete_selection_many'){posts.push({route,body});return;}await route.continue();});
 await page.goto(server.url,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>window.JobUI?.cadScene().pointCount>0,null,{timeout:30000});
 await page.evaluate(()=>{window.pointGeometryUploads=[];const scene=window.JobUI.cadScene(),upload=scene.upload;scene.upload=function(buffer,values){if(buffer===this.buffer)window.pointGeometryUploads.push(values.byteLength);return upload.call(this,buffer,values);};});
 await page.evaluate(()=>window.JobUI.setAutoDetail(false));
 const snapshot=async()=>await (await page.request.get(server.url+'/api/test-state')).json();
 const release=async(item,body=null)=>{item.released=true;if(body)await item.route.fulfill({status:200,contentType:'application/octet-stream',body}).catch(()=>{});else await item.route.continue().catch(()=>{});};
 return {page,errors,previews,posts,held,control,snapshot,release};
}
async function selectPoint(page,world){
 await page.evaluate(()=>window.JobUI.setMode('box'));
 const p=await page.evaluate(world=>{const p=window.JobUI.projectWorld(world),r=document.getElementById('job-canvas').getBoundingClientRect();return {x:r.left+p[0],y:r.top+p[1]};},world);
 await page.mouse.move(p.x-9,p.y-9);await page.mouse.down();await page.mouse.move(p.x+9,p.y+9,{steps:3});await page.mouse.up();
 await page.waitForFunction(()=>window.JobUI.cadScene().deletionFlagCount>0);
 return await page.evaluate(()=>{const s=window.JobUI.cadScene();return {flags:s.deletionFlagCount,values:Array.from(s.pointValues),mask:Array.from(s.deletionFlags),owners:s.pointOwnerRanges,selection:window.JobUI.currentSelection(),camera:{target:s.target.slice(),origin:window.JobUI.cadOrigin().slice(),yaw:s.yaw,pitch:s.pitch,scale:s.scale,canvas_size:[s.c.width,s.c.height]},selection_ms:performance.now()-window.lastSelectionPointerUp};});
}
async function beginDelete(page){return await page.evaluate(()=>{
 const index=window.deleteTimings.length,timing={started:performance.now()};window.deleteTimings.push(timing);window.deleteActive=index;
 window.deletePromises??=[];window.deletePromises[index]=window.JobUI.selAction('delete_selection').then(result=>{timing.finished=performance.now();timing.result=result;window.deleteActive=-1;});
 requestAnimationFrame(()=>{timing.first_frame=performance.now();timing.hidden_on_first_frame=!!window.JobUI.cadScene().pendingDelete;});return index;
});}
async function completed(page,index){await page.waitForFunction(index=>window.deleteTimings[index].finished!=null,index,{timeout:10000});return await page.evaluate(index=>window.deleteTimings[index],index);}
const keptValues=selection=>selection.values.filter((_v,i)=>!selection.mask[Math.floor(i/6)]);
async function viewState(page){return await page.evaluate(()=>{const s=window.JobUI.cadScene(),meshRange=s.renderOwners?.find(x=>x[2]==='M');return {busy,points:s.pointCount,values:Array.from(s.pointValues),owners:s.pointOwnerRanges,allOwners:s.renderOwners,mesh:Array.from(s.meshValues||[]),meshSamples:meshRange?Array.from(s.original.subarray(meshRange[0]*6,meshRange[1]*6)):[],geometryUploads:window.pointGeometryUploads.length,pending:!!s.pendingDelete,selected:window.JobUI.currentSelection(),camera:{target:s.target.slice(),origin:window.JobUI.cadOrigin().slice(),yaw:s.yaw,pitch:s.pitch,scale:s.scale,canvas_size:[s.c.width,s.c.height]},inventory:window.JobUI.inventory().items.map(x=>({id:x.id,count:x.count,edits:x.edit_count})),status:document.getElementById('work-status')?.textContent};});}
function unchangedSources(before,after){for(const item of before.items){const current=after.items.find(x=>x.id===item.id);assert.equal(current.xyz_sha256,item.xyz_sha256);assert.equal(current.attributes_sha256,item.attributes_sha256);}assert.equal(after.derived_sha256,before.derived_sha256);assert.equal(after.derived,before.derived);assert.equal(after.cloud_file,before.cloud_file);assert.equal(after.active_job_scan,before.active_job_scan);}
async function smallRegression(browser,report){
 const server=await fixture(),p=await setupPage(browser,server),{page,control,held,posts,snapshot,release,previews}=p;
 try{
  await page.waitForFunction(()=>window.JobUI.cadScene().pointCount===8&&window.JobUI.cadScene().meshCount===18);
  await page.evaluate(()=>{const s=window.JobUI.cadScene();s.yaw=0;s.pitch=0;window.JobUI.focusWorld([[-1,5,-1],[1,5,-1],[-1,5,1],[1,5,1]]);window.JobUI.setAutoDetail(true);window.JobUI.loadViewDetail(true);});
  await page.waitForFunction(()=>document.getElementById('job-canvas').getAttribute('aria-busy')==='false');
  const before=await snapshot(),selection=await selectPoint(page,[-1,5,-1]),initialView=previews.at(-1).view,beforeView=await viewState(page);
  assert(initialView,'Fixture must exercise an existing local detail view');
  const highlight=await page.evaluate(()=>window.pixelNearPoint([-1,5,-1]));assert(highlight[0]>200&&highlight[1]>150,'GPU flag must highlight the selected measured point');
  control.holdPreview=true;control.holdPost=true;
  const index=await beginDelete(page);await until(()=>posts.length===1,'held delete request');
  await page.waitForFunction(()=>window.deleteTimings[0].first_frame!=null);
  const pending=await viewState(page);assert(pending.pending);assert(pending.busy);
  const hidden=await page.evaluate(()=>window.pixelNearPoint([-1,5,-1]));assert(hidden[0]<180&&hidden[1]<150,'Pending deletion must actually hide the rendered point');
  assert.deepEqual((await snapshot()).items.map(x=>x.excluded),[[],[]]);
  assert.equal(await page.evaluate(()=>window.JobUI.selAction('delete_selection')),false,'A repeated Delete while saving must not replace the first operation');
  assert.equal((await viewState(page)).pending,true,'Rejected repeated Delete must not release the first GPU hide');
  assert.equal(await page.evaluate(()=>window.deleteTimings[0].finished),undefined);
  control.holdPost=false;await posts.shift().route.continue();
  const timing=await completed(page,index),committed=await snapshot(),after=await viewState(page);
  assert.equal(timing.result,true);assert.equal(after.busy,false);assert.equal(after.pending,false);
  assert.deepEqual(committed.items.map(x=>x.excluded),[[0],[]]);assert.equal(committed.valid_points,3);
  assert.deepEqual(after.values,keptValues(selection),'Committed display compaction must preserve every unselected XYZ/RGB row');
  assert.equal(after.geometryUploads,beforeView.geometryUploads,'Deletion must not upload the point geometry again');
  const committedPixel=await page.evaluate(()=>window.pixelNearPoint([-1,5,-1]));assert(committedPixel[0]<180&&committedPixel[1]<150,'Committed exclusion must remain rendered after pendingDelete is cleared');
  assert.deepEqual(after.mesh,beforeView.mesh);assert.deepEqual(after.meshSamples,beforeView.meshSamples,'Mixed scan/mesh ownership must preserve all mesh rows during compaction');
  assert.deepEqual(after.camera,selection.camera);assert.equal(after.selected,null);assert.equal(after.inventory.find(x=>x.id==='A').count,3);
  await until(()=>held.length>0,'separate post-delete refinement');assert.equal(held[0].view,initialView,'Deletion must preserve local view rather than requesting whole-cloud detail');
  // Deliberately hold the geometry response for three seconds AFTER the actual
  // server commit. Completion and a second selection must already be usable.
  const second=await selectPoint(page,[1,5,-1]);
  assert.equal((await viewState(page)).geometryUploads,after.geometryUploads,'A second selection must reuse GPU point geometry');
  assert.equal(await page.locator('#job-delete-selection').isEnabled(),true);
  const firstHeld=held[0],staleBody=await (await page.request.get(firstHeld.url)).body();
  await page.waitForTimeout(3000);
  assert.equal((await viewState(page)).busy,false);assert.equal((await viewState(page)).points,after.points);
  report.scenarios.push('Exact commit, retained buffer and next selection finish while post-delete detail is deliberately withheld for 3 seconds');
  report.small_delete={...timing,hide_ms:timing.first_frame-timing.started,completion_ms:timing.finished-timing.started,selection_ms:selection.selection_ms,retained_display_points:after.points};
  const secondIndex=await beginDelete(page),secondTiming=await completed(page,secondIndex),secondState=await snapshot(),secondView=await viewState(page);
  assert.equal(secondTiming.result,true);assert.deepEqual(secondState.items.map(x=>x.excluded),[[0,1],[]]);assert.equal(secondState.valid_points,2);
  assert.deepEqual(secondView.values,keptValues(second));assert.deepEqual(secondView.camera,second.camera);
  await release(firstHeld,staleBody);await page.waitForTimeout(200);
  assert.deepEqual((await viewState(page)).values,secondView.values,'A response for the prior edit must not resurrect deleted points');
  await until(()=>held.some(x=>x!==firstHeld),'second asynchronous refinement');
  const staleUndo=held.find(x=>x!==firstHeld),undoBody=await (await page.request.get(staleUndo.url)).body();
  control.holdPreview=false;
  assert.equal(await page.evaluate(()=>window.HistoryUI.apply(false)),true);
  assert.deepEqual((await snapshot()).items.map(x=>x.excluded),[[0],[]]);assert.equal((await snapshot()).valid_points,3);
  const undoView=await viewState(page);await release(staleUndo,undoBody);await page.waitForTimeout(200);
  assert.deepEqual((await viewState(page)).values,undoView.values,'Old refinement must not overwrite Undo');
  assert.equal(await page.evaluate(()=>window.HistoryUI.apply(false)),true);assert.deepEqual((await snapshot()).items.map(x=>x.excluded),[[],[]]);
  assert.equal(await page.evaluate(()=>window.HistoryUI.apply(true)),true);assert.deepEqual((await snapshot()).items.map(x=>x.excluded),[[0],[]]);
  assert.equal(await page.evaluate(()=>window.HistoryUI.apply(true)),true);assert.deepEqual((await snapshot()).items.map(x=>x.excluded),[[0,1],[]]);
  report.scenarios.push('Rapid successive edits preserve exact source IDs, mixed scan/mesh ownership, triangle geometry, current camera, and grouped Undo/Redo; old responses cannot undo either change');
  const failureSelection=await selectPoint(page,[-1,5,1]),beforeFailure=await snapshot();control.holdPost=true;
  const failureIndex=await beginDelete(page);await until(()=>posts.length===1,'held failing request');assert.equal((await viewState(page)).pending,true);
  await posts.shift().route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Controlled delete failure'})});control.holdPost=false;
  assert.equal((await completed(page,failureIndex)).result,false);const failureView=await viewState(page);
  assert.equal(failureView.pending,false);assert.equal(failureView.busy,false);assert.deepEqual(failureView.values,failureSelection.values);assert.deepEqual(failureView.camera,failureSelection.camera);
  assert.deepEqual((await snapshot()).items,beforeFailure.items);await page.locator('#message-close').click();
  report.scenarios.push('Failed exact edit restores the temporary hide and preserves source masks, selection and camera');
  unchangedSources(before,await snapshot());assert.deepEqual(p.errors,[]);report.small_page_errors=p.errors;
 }finally{await page.close();server.child.kill('SIGTERM');}
}
async function unknownAnalysisRegression(browser,report){
 const server=await fixture(4,true),p=await setupPage(browser,server),{page,control,posts,snapshot}=p;
 try{
  await page.evaluate(()=>{const s=window.JobUI.cadScene();s.yaw=0;s.pitch=0;window.JobUI.focusWorld([[-1,5,-1],[1,5,-1],[-1,5,1],[1,5,1]]);});
  const before=await snapshot();await selectPoint(page,[-1,5,-1]);control.holdPost=true;control.holdPreview=true;
  const index=await beginDelete(page);await until(()=>posts.length===1,'overlay race delete');
  // A previously running analysis can publish while the source edit is saving.
  // Its buffer must survive; the saved normal view must also lose deleted rows.
  await page.evaluate(()=>window.JobUI.analysisPreview({origin:[0,0,0],points:[[900,900,900,1,0,0]],source_id:'B',result_id:'unrelated-analysis',sample_count:1,used:1,signed:true,tolerance_mm:10},'inspection'));
  const overlay=await viewState(page);control.holdPost=false;await posts.shift().route.continue();
  assert.equal((await completed(page,index)).result,true);const after=await snapshot();
  unchangedSources(before,after);assert.equal(after.valid_points,null);assert.deepEqual(after.items.map(x=>x.excluded),[[0],[]]);
  assert.deepEqual((await viewState(page)).values,overlay.values);assert.equal(await page.evaluate(()=>window.JobUI.previewOwner()),'inspection');
  await page.evaluate(()=>window.JobUI.clearCleaningPreview('inspection'));
  const restored=await viewState(page),origin=restored.camera.origin;
  for(let i=0;i<restored.values.length;i+=6)assert(!(restored.values[i]+origin[0]===-1&&restored.values[i+1]+origin[1]===5&&restored.values[i+2]+origin[2]===-1),'Restoring normal view must not resurrect a deletion committed beneath an unrelated analysis');
  assert.equal(restored.pending,false);
  await page.evaluate(()=>{const s=window.JobUI.cadScene();s.yaw=0;s.pitch=0;window.JobUI.focusWorld([[-1,5,-1],[1,5,-1],[-1,5,1],[1,5,1]]);});
  const nextSelection=await selectPoint(page,[1,5,-1]),nextIndex=await beginDelete(page);
  assert.equal((await completed(page,nextIndex)).result,true);
  assert.deepEqual((await snapshot()).items.map(x=>x.excluded),[[0,1],[]]);
  const nextView=await viewState(page);assert.equal(nextView.pending,false);assert.deepEqual(nextView.values,keptValues(nextSelection),'Restored normal ownership must support another committed deletion before detail arrives');
  unchangedSources(before,await snapshot());assert.deepEqual(p.errors,[]);
  report.scenarios.push('Unknown derived count preserves active cloud and analysis files; an unrelated analysis stays visible, and returning to normal view permits another exact edit before refinement');
 }finally{await page.close();server.child.kill('SIGTERM');}
}
function expectedGridMask(spec,count){
 const side=Math.sqrt(count),mask=Buffer.alloc(count);let selected=0;
 for(let row=0;row<count;row++){
  const q=[(row%side)/(side-1)*20-10-spec.target[0],5-spec.target[1],Math.floor(row/side)/(side-1)*20-10-spec.target[2]];
  const x=q.reduce((n,v,i)=>n+v*spec.right[i],0)/spec.scale,y=q.reduce((n,v,i)=>n+v*spec.up[i],0)*spec.aspect/spec.scale;
  let inside=false,j=spec.polygon.length-1;for(let i=0;i<spec.polygon.length;i++){const [xi,yi]=spec.polygon[i],[xj,yj]=spec.polygon[j];if((yi>y)!==(yj>y)&&x<(xj-xi)*(y-yi)/(yj-yi)+xi)inside=!inside;j=i;}
  if(inside){mask[row]=1;selected++;}
 }
 return {selected,sha256:crypto.createHash('sha256').update(mask).digest('hex')};
}
async function largeRegression(browser,report){
 const count=1000000,server=await fixture(count),p=await setupPage(browser,server),{page,control,held,snapshot}=p;
 try{
  await page.evaluate(()=>{document.getElementById('job-density').value=100;document.getElementById('job-density').oninput();});
  await page.waitForFunction(()=>window.JobUI.cadScene().pointCount===1000000,null,{timeout:60000});
  await page.evaluate(()=>{const s=window.JobUI.cadScene();s.yaw=0;s.pitch=0;s.target=[0,0,0];s.scale=24;s.draw();window.JobUI.setMode('box');});
  const r=await page.locator('#job-canvas').boundingBox(),cx=r.x+r.width/2,cy=r.y+r.height/2;
  const half=r.width*2.5/48;
  await page.mouse.move(cx-half,cy-half);await page.mouse.down();await page.mouse.move(cx+half,cy+half,{steps:3});await page.mouse.up();
  await page.waitForFunction(()=>window.JobUI.cadScene().deletionFlagCount>0);
  const selection=await page.evaluate(()=>({spec:window.JobUI.currentSelection(),flags:window.JobUI.cadScene().deletionFlagCount,selection_ms:performance.now()-window.lastSelectionPointerUp,uploads:window.pointGeometryUploads.length}));
  const expected=expectedGridMask(selection.spec,count);assert.equal(selection.flags,expected.selected);
  control.holdPreview=true;const index=await beginDelete(page),timing=await completed(page,index),after=await snapshot();
  assert.equal(timing.result,true);assert.equal(after.items[0].mask_sha256,expected.sha256);assert.equal(after.items[0].excluded_count,expected.selected);assert.equal(after.valid_points,count-expected.selected);
  assert.equal(await page.evaluate(()=>window.JobUI.cadScene().pointCount),count-expected.selected);assert.equal(await page.evaluate(()=>busy),false);
  assert.equal(await page.evaluate(()=>window.pointGeometryUploads.length),selection.uploads,'One-million-point deletion must not reupload XYZ/RGB');
  await until(()=>held.length>0,'one-million-point background refinement');
  const nextStarted=Date.now();await page.mouse.move(cx+half*2,cy-half);await page.mouse.down();await page.mouse.move(cx+half*3,cy+half,{steps:2});await page.mouse.up();
  assert.equal(await page.locator('#job-delete-selection').isEnabled(),true);
  assert.equal(await page.evaluate(()=>window.pointGeometryUploads.length),selection.uploads,'Next selection must not reupload XYZ/RGB');
  report.large_delete={source_points:count,displayed_points:count,deleted_source_points:expected.selected,selection_ms:selection.selection_ms,hide_ms:timing.first_frame-timing.started,post_sent_ms:timing.post_sent-timing.started,post_ack_ms:timing.post_ack-timing.started,backend_action_ms:after.operation_timings.at(-1).seconds*1000,server_ready_ms:timing.server_ready-timing.started,commit_and_usable_ms:timing.finished-timing.started,next_selection_ms:Date.now()-nextStarted,post_delete_preview_still_held:true,polls:timing.polls.map(p=>({requested_ms:p.requested-timing.started,response_ms:p.response-timing.started,parsed_ms:p.parsed-timing.started,busy:p.busy})),renderer:'Chromium 141 software WebGL on Linux; timings are not a Windows hardware-GPU benchmark'};
  report.scenarios.push('One million displayed points: exact byte-mask SHA-256 matches an independent grid oracle; next box selection works before background detail returns');
  assert.deepEqual(p.errors,[]);
 }finally{await page.close();server.child.kill('SIGTERM');}
}
async function navigationAndStopRegression(browser,report){
 const server=await fixture(),p=await setupPage(browser,server),{page}=p;
 try{
  await page.waitForFunction(()=>state?.job&&document.getElementById('geometry-tools-dialog')&&document.getElementById('measure-tools-dialog'));
  let cancels=0;await page.route('**/api/cancel-operation',async route=>{cancels++;await route.fulfill({status:200,contentType:'application/json',body:'{}'});});
  const stopChecks=[];
  for(const [index,id] of ['geometry-tools-dialog','measure-tools-dialog'].entries()){
   const check=await page.evaluate(id=>{
    const canvas=document.getElementById('job-canvas'),before=[canvas.width,canvas.height],dialog=document.getElementById(id);dialog.show();
    window.savedJobForStop=state.job;state.job={...state.job,busy:true,message:'Synthetic cancel accessibility check'};updateOperationProgress(state.job);
    const stop=document.getElementById('operation-cancel'),r=stop.getBoundingClientRect();stop.focus();
    return {dialog:id,hit:document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)?.id,keyboard:document.activeElement===stop,before,after:[canvas.width,canvas.height]};
   },id);
   assert.equal(check.hit,'operation-cancel','Modeless tool dialog must not cover the Stop button');assert.equal(check.keyboard,true);assert.deepEqual(check.after,check.before);
   if(index===0){await page.screenshot({path:path.join(evidence,'progress_stop_overlay.png')});await page.locator('#operation-cancel').click();}
   else await page.keyboard.press('Enter');
   await until(()=>cancels===index+1,'pointer/keyboard Stop request');stopChecks.push(check);
   await page.evaluate(id=>{document.getElementById(id).close();state.job=window.savedJobForStop;updateOperationProgress({busy:false});},id);
  }
  report.stop_accessibility=stopChecks;
  report.scenarios.push('Progress overlay keeps canvas dimensions stable; Stop remains pointer- and keyboard-accessible above geometry and measurement dialogs');
  const navigation=await page.evaluate(()=>{
   const scene=window.JobUI.cadScene(),gl=scene.gl,count=1000000,ids=[7,17,27],rows=[[-1,5,-1,1,0,0],[0,5,0,0,1,0],[1,5,1,0,0,1]],values=new Float32Array(count*6),flags=new Uint8Array(count);flags.fill(3);
   for(let i=0;i<ids.length;i++){values.set(rows[i],ids[i]*6);flags[ids[i]]=0;}
   scene.original=scene.pointValues=new Float32Array(rows.flat());scene.gpuPointValues=values;scene.gpuPointCount=count;scene.pointCount=3;scene.pointVertexIds=new Uint32Array(ids);scene.gpuFlags=flags;scene.pendingDelete=false;
   scene.upload(scene.buffer,values);scene.uploadedPointValues=values;if(scene.deletionBuffer)gl.deleteBuffer(scene.deletionBuffer);scene.deletionBuffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,scene.deletionBuffer);gl.bufferData(gl.ARRAY_BUFFER,flags,gl.STATIC_DRAW);scene.deletionBufferCount=count;
   scene.target=[0,5,0];scene.yaw=0;scene.pitch=0;scene.scale=3;scene.meshCount=0;scene.meshWireCount=0;scene.navigationUntil=Infinity;
   const draw=gl.drawArrays.bind(gl);let submitted=[];gl.drawArrays=(mode,first,n)=>{if(mode===gl.POINTS)submitted.push(n);return draw(mode,first,n);};
   scene.draw();const during=submitted.at(-1),pixels=rows.map(row=>{
    const p=scene.projectLocal(row.slice(0,3)),x=Math.round((p[0]+1)*scene.c.width/2),y=Math.round((p[1]+1)*scene.c.height/2),b=new Uint8Array(7*7*4);gl.readPixels(x-3,y-3,7,7,gl.RGBA,gl.UNSIGNED_BYTE,b);
    let best=[0,0,0];for(let i=0;i<b.length;i+=4)if(b[i]+b[i+1]+b[i+2]>best[0]+best[1]+best[2])best=[b[i],b[i+1],b[i+2]];return best;
   });
   submitted=[];scene.pointCount=0;scene.draw();const empty=submitted.length;
   submitted=[];scene.pointCount=count;scene.pointVertexIds=null;scene.draw();const refined=submitted.at(-1);gl.drawArrays=draw;
   return {original_gpu_points:count,retained_gpu_ids:ids,during_navigation_vertices:during,pixels,empty_cloud_point_draws:empty,refined_navigation_vertices:refined};
  });
  assert.equal(navigation.during_navigation_vertices,1000000);assert.equal(navigation.empty_cloud_point_draws,0);assert.equal(navigation.refined_navigation_vertices,100000);
  for(let i=0;i<3;i++){assert(navigation.pixels[i][i]>200,'Each sparse retained point must remain visible while navigating');for(let j=0;j<3;j++)if(j!==i)assert(navigation.pixels[i][j]<80);}
  report.navigation=navigation;assert.deepEqual(p.errors,[]);
  report.scenarios.push('Sparse retained GPU IDs 7/17/27 stay visibly red/green/blue during navigation; empty clouds submit no points and refined clouds regain navigation sampling');
 }finally{await page.close();server.child.kill('SIGTERM');}
}
async function main(){
 fs.mkdirSync(evidence,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.TUNNEL_CHROMIUM||undefined,args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const report={status:'running',scenarios:[],scope:'Production app/API, immutable synthetic source files, actual WebGL display and exact backend masks. Post-delete preview responses intentionally held.'};
 try{await smallRegression(browser,report);await unknownAnalysisRegression(browser,report);if(process.env.TUNNEL_DELETE_LARGE!=='0')await largeRegression(browser,report);await navigationAndStopRegression(browser,report);report.status='passed';}
 catch(error){report.status='failed';report.error=error.stack;throw error;}
 finally{fs.writeFileSync(path.join(evidence,'point_delete_ui_0187.json'),JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report,null,2));
}
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
module.exports={fixture,setupPage,smallRegression,unknownAnalysisRegression,largeRegression,navigationAndStopRegression};
