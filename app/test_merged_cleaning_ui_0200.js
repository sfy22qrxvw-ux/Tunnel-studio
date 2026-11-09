'use strict';
// Production UI in Chromium, with controlled job transport and merge receipts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const playwright = require(process.env.TUNNEL_PLAYWRIGHT_MODULE || 'playwright');

async function run() {
 const browser = await playwright.chromium.launch({headless:true,
  executablePath:process.env.TUNNEL_CHROMIUM || undefined,args:['--no-sandbox']});
 try {
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const html=fs.readFileSync(path.join(__dirname,'web/index.html'),'utf8')
   .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
  await page.setContent(html);
  await page.addStyleTag({content:'[hidden]{display:none!important}dialog{width:1100px;max-height:95vh;overflow:auto}label{display:block}'});
  await page.evaluate(()=>{
   window.calls=[];window.confirms=[];window.selected=[];window.sourceSelection=['S00'];
   window.enough=true;window.waitMode='success';window.jobNumber=0;window.pending=null;
   window.$=id=>document.getElementById(id);
   window.esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
   window.fmt=(value,digits=0)=>Number.isFinite(Number(value))?Number(value).toFixed(digits):'—';
   window.notice=()=>{};window.confirm=message=>{confirms.push(message);return true;};
   window.TargetUI={saveIfDirty:async()=>{},sync:async()=>{}};
   window.SceneObjectsUI={setLinks(){},select(){}};
   window.TunnelRibbon={view(){}};window.WorkStatus={show(){}};
   window.SetupCameraUI={exit:async()=>{}};
   window.networkData={active:'S00',controls:{},merge_caches:[],scans:[],links:[]};
   for(let i=0;i<16;i++)networkData.scans.push({id:'S'+String(i).padStart(2,'0'),name:'Setup '+i,targets:[]});
   networkData.scans.push({id:'X',name:'Unconnected setup',targets:[]});
   for(let i=1;i<16;i++)networkData.links.push({id:'L'+i,a:networkData.scans[i-1].id,b:networkData.scans[i].id,a_name:'Setup '+(i-1),b_name:'Setup '+i,kind:'cloud_to_cloud',status:'applied'});
   networkData.links.push({id:'loop',a:'S15',b:'S00',a_name:'Setup 15',b_name:'Setup 0',kind:'manual',status:'applied'});
   const clone=value=>JSON.parse(JSON.stringify(value));
   window.api=async url=>{if(url==='/api/scan-registration')return clone(networkData);throw Error('Unexpected GET '+url);};
   window.post=async(url,body)=>{
    calls.push({url,body:clone(body)});
    if(body.action==='merge_estimate')return {scan_id:body.id,setups:16,points:921000000,required_free_bytes:37400000000,free_bytes:enough?100000000000:1000,enough};
    if(body.action==='build_merge'){
     if(waitMode==='error')throw Error('Workspace save failed; prior result retained.');
     window.pending={registration_id:'merge-request-'+(++jobNumber),id:'merged-'+jobNumber};
     return {started:true,registration_id:pending.registration_id};
    }
    throw Error('Unexpected POST '+body.action);
   };
   window.finishMerge=mode=>{
    if(mode==='cancel')return {busy:false,cancelled:true};
    if(mode==='stale')return {busy:false};
    networkData.last_merge={...pending,setups:16,count:921000000};
    networkData.merge_caches=[{id:pending.id,name:'Registered cloud',setups:16,count:921000000,cache_bytes:36840000000,stale:false,archived:false,edited:false}];
    return {busy:false};
   };
   window.JobUI={
    selectedItems:()=>sourceSelection,selectScan:id=>selected.push(id),sync:async()=>{},clearSelection(){},
    waitReady:async progress=>{
     progress({busy:true,message:'Writing merged points'});
     if(waitMode==='defer')return new Promise(resolve=>{window.finishPending=mode=>resolve(finishMerge(mode));});
     return finishMerge(waitMode);
    }
   };
  });
  await page.addScriptTag({path:path.join(__dirname,'web/scan-registration-ui.js')});
  const open=async()=>page.evaluate(async()=>{$('scan-links-dialog').close();await window.ScanRegistrationUI.openLinks();});
  const builds=async()=>page.evaluate(()=>calls.filter(c=>c.body.action==='build_merge').length);

  // The 16-scan group is available without targets, a prior merge, or bundle solve.
  await open();
  assert.equal(await page.locator('#link-merge-tools').isVisible(),true);
  assert.equal(await page.locator('#link-build-merge').isEnabled(),true);
  assert.match(await page.locator('#link-merge-space').textContent(),/16 connected setups/);
  assert.match(await page.locator('#link-merge-space').textContent(),/another bundle adjustment is not required/);

  // Availability follows the first setup, rather than a historical successful pair.
  await page.selectOption('#link-fixed','X');
  assert.equal(await page.locator('#link-merge-tools').isVisible(),false);
  await page.selectOption('#link-fixed','S00');
  assert.equal(await page.locator('#link-build-merge').isEnabled(),true);

  // A broken saved connection must not join otherwise unrelated scans via a ghost.
  await page.evaluate(()=>networkData.links.push({id:'bad',a:'S03',b:'missing',a_name:'Setup 3',b_name:'Removed',status:'unavailable'}));
  await open();
  assert.equal(await page.locator('#link-build-merge').isDisabled(),true);
  assert.match(await page.locator('#link-merge-space').textContent(),/unavailable setup/);
  await page.evaluate(()=>{networkData.links=networkData.links.filter(x=>x.id!=='bad');});
  await open();

  // A matched completion selects the new merged scan for the normal cleaning path.
  await page.click('#link-build-merge');
  await page.waitForFunction(()=>$('link-feedback-title').textContent==='Merged cloud ready');
  assert.match(await page.evaluate(()=>confirms[0]),/16 connected setups/);
  assert.deepEqual(await page.evaluate(()=>selected),['merged-1']);
  assert.match(await page.locator('#link-message').textContent(),/Clean Tunnel or Survey analysis/);
  assert.equal(await page.locator('#link-view-scans').textContent(),'View merged cloud');
  assert.deepEqual(await page.evaluate(()=>calls.map(c=>c.body.action)),['merge_estimate','build_merge']);

  // Existing cache controls remain accessible even on an unconnected setup.
  await page.selectOption('#link-fixed','X');
  assert.equal(await page.locator('#link-merge-tools').isVisible(),true);
  assert.equal(await page.locator('#link-build-merge').isDisabled(),true);
  assert.equal(await page.locator('[data-release-merge]').isEnabled(),true);

  // Insufficient space is detected before any build job or confirmation.
  await page.evaluate(()=>{enough=false;});await open();
  const before=await builds();
  await page.click('#link-build-merge');
  await page.waitForFunction(()=>$('link-feedback-title').textContent==='Merged cloud was not built');
  assert.equal(await builds(),before);
  assert.match(await page.locator('#link-message').textContent(),/Not enough free space/);
  assert.equal(await page.locator('#link-build-merge').isDisabled(),true);

  // Cancellation preserves the previously selected completed result.
  await page.evaluate(()=>{enough=true;waitMode='cancel';});await open();
  await page.click('#link-build-merge');
  await page.waitForFunction(()=>$('link-feedback-title').textContent==='Merged cloud cancelled');
  assert.deepEqual(await page.evaluate(()=>selected),['merged-1']);
  assert.equal(await page.locator('#link-build-merge').isEnabled(),true);

  // A stale receipt cannot select or report an older merge as this job's result.
  await page.evaluate(()=>{waitMode='stale';});await open();
  await page.click('#link-build-merge');
  await page.waitForFunction(()=>$('link-feedback-title').textContent==='Merged cloud was not built');
  assert.match(await page.locator('#link-message').textContent(),/could not be confirmed/);
  assert.deepEqual(await page.evaluate(()=>selected),['merged-1']);

  // Failed startup/save remains an error, without changing the selected cloud.
  await page.evaluate(()=>{waitMode='error';});await open();
  await page.click('#link-build-merge');
  await page.waitForFunction(()=>$('link-message').textContent.includes('Workspace save failed'));
  assert.deepEqual(await page.evaluate(()=>selected),['merged-1']);

  // One in-flight merge disables pair edits and cannot start duplicate jobs.
  await page.evaluate(()=>{waitMode='defer';});await open();
  await page.click('#link-build-merge');
  await page.waitForFunction(()=>typeof finishPending==='function');
  const inFlight=await builds();
  assert.equal(await page.locator('#link-fixed').isDisabled(),true);
  assert.equal(await page.locator('[data-release-merge]').isDisabled(),true);
  await page.evaluate(()=>$('link-build-merge').onclick());
  assert.equal(await builds(),inFlight);
  await page.evaluate(()=>finishPending('cancel'));
  await page.waitForFunction(()=>$('link-feedback-title').textContent==='Merged cloud cancelled');
  assert.equal(await page.locator('#link-fixed').isEnabled(),true);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',scenarios:10,checks:[
   '16-scan connected component without bundle adjustment','selection-sensitive availability',
   'missing-setup guard','matched merge receipt selects Registered cloud',
   'existing cache controls preserved','disk preflight','cancelled merge retains prior selection',
   'stale receipt rejection','failed job retains prior selection','duplicate-job exclusion'
  ]},null,2));
 } finally {await browser.close();}
}
run().catch(error=>{console.error(error);process.exitCode=1;});
