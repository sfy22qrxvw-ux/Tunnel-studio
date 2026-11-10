'use strict';
// Executes the production registration scripts and markup in a real browser.
// Transport is controlled so target roles, requests, and stale results are observable.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const playwright=require(process.env.TUNNEL_PLAYWRIGHT_MODULE||'playwright');

async function run(){
 const browser=await playwright.chromium.launch({headless:true,executablePath:process.env.TUNNEL_CHROMIUM||undefined,args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const html=fs.readFileSync(path.join(__dirname,'web/index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
 await page.setContent(html);
 await page.addStyleTag({content:'[hidden]{display:none!important}.reg-view{display:none!important}dialog{max-width:95vw;width:1100px;max-height:90vh;overflow:auto}td{padding:5px}table{width:100%}label{display:block}'});
 await page.evaluate(()=>{
  window.calls=[];window.confirms=[];window.notices=[];window.previewLoads=0;window.staleResult=false;
  window.busy=false;window.state={project_id:'registration-ui-fixture',job:{busy:false}};
  window.$=id=>document.getElementById(id);
  window.esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  window.fmt=(value,digits=3)=>Number.isFinite(Number(value))?Number(value).toFixed(digits):'—';
  window.notice=(text,error=false)=>window.notices.push({text,error});
  window.confirm=text=>{window.confirms.push(text);return true;};
  window.setBusy=value=>{window.busy=value;};
  window.CloudScene=class{constructor(canvas){this.c=canvas;this.gl=null;}clear(){}draw(){}upload(){}reset(){}};
  window.TunnelRibbon={sync(){},view(){},export(){}};
  window.SceneObjectsUI={setLinks(){},select(){}};
  window.WorkStatus={show(){}};
  window.SetupCameraUI={exit:async()=>{}};
  window.JobUI={selectedItems:()=>['A','B'],selectedItem:()=>({id:'A',kind:'scan'}),targets(){},highlightTargets(){},clearSelection(){},selectScan(){},setMode(){},sync:async()=>{},loadPreview:async()=>{window.previewLoads++;},waitReady:async()=>({busy:false}),focusTarget(){}};
  window.regData={source_id:'A',source_item_id:'A',revision:1,cloud:{name:'Setup A',count:10000000,attribute_counts:[1,0]},candidates:[],rows:[],detection:{message:'Ready'},can_undo_targets:false};
  window.networkData={active:'A',scans:[
   {id:'A',name:'Setup A',targets:['T1','T2'],target_roles:{T1:'fit',T2:'fit'}},
   {id:'B',name:'Setup B',targets:['T1','T2','T3','T4','Check','Excluded'],target_roles:{T1:'fit',T2:'fit',T3:'fit',T4:'fit',Check:'check',Excluded:'off'}},
   {id:'C',name:'Setup C',targets:['T2','T3','T4','Check','Excluded'],target_roles:{T2:'fit',T3:'fit',T4:'fit',Check:'fit',Excluded:'fit'}}
  ],links:[],controls:{},merge_caches:[]};
  const clone=value=>JSON.parse(JSON.stringify(value));
  window.api=async url=>{
   window.calls.push({kind:'get',url});
   if(url==='/api/registration')return clone(window.regData);
   if(url==='/api/scan-registration')return clone(window.networkData);
   if(url.startsWith('/api/reg-preview'))throw Error('Hidden registration preview was requested');
   if(url==='/api/status')return clone(window.state);
   throw Error('Unexpected GET '+url);
  };
  window.post=async(url,body)=>{
   window.calls.push({kind:'post',url,body:clone(body)});
   if(body.action==='register_setups'){
    const id='registration-'+window.calls.length;
    const result={id:window.staleResult?'obsolete':id,a:body.a,b:body.b,applied:true,request:{a:body.a,b:body.b,mode:body.mode,floating:body.floating},fixed:[],moving:[body.a,body.b],matches:clone(body.matches),solution:{mode:body.mode,fit:{count:body.matches.filter(m=>m.role==='fit').length,rms_mm:22.5,max_mm:36.7},check:{count:1,rms_mm:44},residuals:body.matches.map(m=>({id:m.a+' ↔ '+m.b,role:m.role,error_mm:m.role==='check'?44:22.5,within_limit:false})),warnings:['Fit RMS exceeds the warning level; result is retained.'],anchor:null,datum:'floating',scan_count:body.a==='B'?3:2}};
    window.networkData.last_registration_result=result;
    if(!window.staleResult)window.networkData.links.push({...result,a_name:'Setup '+body.a,b_name:'Setup '+body.b,status:'applied'});
    return {registration_id:id};
   }
   if(body.action==='remove_targets'){
    const ids=new Set(body.ids);window.regData.candidates=window.regData.candidates.filter(c=>!ids.has(c.id));window.regData.rows=window.regData.rows.filter(r=>!ids.has(r.candidate_id));window.regData.can_undo_targets=true;return {};
   }
   if(body.action==='rename_target')return {};
   if(body.action==='rows'){window.regData.rows=clone(body.rows);return {};}
   if(body.action==='accept'||body.action==='apply')return {};
   throw Error('Unexpected POST '+body.action);
  };
  window.poll=async()=>{window.busy=false;await window.TargetUI?.sync();};
 });
 await page.addScriptTag({path:path.join(__dirname,'web/registration-ui.js')});
 await page.addScriptTag({path:path.join(__dirname,'web/scan-registration-ui.js')});
 await page.evaluate(()=>window.TargetUI.sync());
 assert.equal(await page.evaluate(()=>calls.some(c=>c.url.startsWith('/api/reg-preview'))),false,'Hidden secondary viewer must not download a cloud');

 // Survey control applies with no alignment even if an earlier project left the checkbox checked.
 await page.evaluate(async()=>{
  $('reg-to-survey').checked=true;
  window.regData.survey_analysis_unavailable='Import an alignment to use the positioned scan for survey analysis.';
  window.regData.solution={id:'control-fit',control_group:{setups:3,fit:3,check:1,matched:[]},fit:{count:3,rms_mm:1,max_mm:2},check:{count:1,rms_mm:3,max_mm:3},warnings:[],residuals:[]};
  await window.TargetUI.sync();
 });
 assert(await page.locator('#reg-to-survey').isDisabled());
 assert(!(await page.locator('#reg-to-survey').isChecked()));
 await page.evaluate(()=>$('reg-apply').click());
 await page.waitForFunction(()=>calls.some(c=>c.body?.action==='apply'));
 assert.equal(await page.evaluate(()=>calls.find(c=>c.body?.action==='apply').body.to_survey),false);
 await page.evaluate(async()=>{window.regData.solution=null;delete window.regData.survey_analysis_unavailable;await window.TargetUI.sync();});

 // A new pair automatically includes the common Fit targets and chooses its own mode.
 await page.evaluate(()=>window.ScanRegistrationUI.solveSelected());
 assert.equal(await page.locator('#link-matches tr').count(),2);
 assert.equal(await page.locator('#link-method').inputValue(),'vertical');
 assert.equal(await page.locator('#link-reviewed').count(),0);
 assert.equal(await page.locator('#link-up-confirmed').count(),0);
 assert.equal(await page.locator('#link-anchor').count(),0);
 await page.click('#link-solve');
 await page.waitForFunction(()=>document.getElementById('link-feedback-title').textContent==='Registration saved');
 const first=await page.evaluate(()=>calls.find(c=>c.body?.action==='register_setups').body);
 assert.equal(first.floating,true);assert(!Object.hasOwn(first,'anchor'));assert.equal(first.mode,'vertical');assert.equal(first.matches.length,2);
 assert(!Object.hasOwn(first,'references'),'No per-target coordinate selection is required');
 assert.equal(await page.evaluate(()=>calls.some(c=>c.body?.action==='accept')),false,'Register must be one backend operation');
 assert.match(await page.locator('#link-residuals').textContent(),/22.500/);
 assert.match(await page.locator('#link-residuals').textContent(),/result is retained/);

 // Earlier two-point 2D registration does not poison the next three-point 3D pair.
 await page.selectOption('#link-fixed','B');
 await page.selectOption('#link-moving','C');
 assert.equal(await page.locator('#link-method').inputValue(),'rigid');
 const roles=await page.locator('#link-matches tr').evaluateAll(rows=>rows.map(tr=>[tr.querySelector('[data-a]').value,tr.querySelector('[data-role]').value]));
 assert.deepEqual(roles,[['T2','fit'],['T3','fit'],['T4','fit'],['Check','check'],['Excluded','off']]);
 await page.click('#link-solve');
 await page.waitForFunction(()=>document.getElementById('link-feedback-title').textContent==='Registration saved'&&calls.filter(c=>c.body?.action==='register_setups').length===2);
 const second=await page.evaluate(()=>calls.filter(c=>c.body?.action==='register_setups')[1].body);
 assert.equal(second.mode,'rigid');assert.equal(second.floating,true);
 assert.equal(second.matches.filter(m=>m.role==='fit').length,3);
 assert.equal(second.matches.find(m=>m.a==='Check').role,'check');
 assert.equal(second.matches.find(m=>m.a==='Excluded').role,'off');
 // User exclusions are retained when names are refreshed for this same pair.
 await page.locator('#link-matches tr').filter({has:page.locator('[data-a] option:checked[value="T4"]')}).locator('[data-role]').selectOption('off');
 await page.click('#link-match-names');
 assert.equal(await page.locator('#link-matches tr').evaluateAll(rows=>rows.find(tr=>tr.querySelector('[data-a]').value==='T4').querySelector('[data-role]').value),'off');

 // A mismatched completion cannot be presented as a successful registration.
 await page.selectOption('#link-fixed','A');await page.selectOption('#link-moving','B');
 await page.evaluate(()=>{window.staleResult=true;});await page.click('#link-solve');
 await page.waitForFunction(()=>document.getElementById('link-feedback-title').textContent==='Registration was not completed');
 assert.match(await page.locator('#link-message').textContent(),/could not be confirmed/);
 await page.evaluate(()=>{$('scan-links-dialog').close();window.staleResult=false;});

 // High-RMS fitted targets remain included, with fit errors visible and no acceptance step.
 await page.evaluate(async()=>{
  window.regData.candidates=[{id:'sphere-1',name:'T1',source:[1,2,3],status:'accepted',target_type:'sphere',diameter_m:.1988,sphere_rms_mm:12.4,radius_error_mm:4.1,quality:'warning',quality_warnings:['Sphere RMS is above the warning level; centre is retained.'],score:.8,support_points:420}];
  window.regData.rows=[{id:'T1',candidate_id:'sphere-1',role:'fit',source:[1,2,3],control:[null,null,null]}];window.regData.revision++;
  await window.TargetUI.sync();
 });
 assert.equal(await page.locator('[data-accept]').count(),0);
 assert.equal(await page.locator('[data-reject]').count(),0);
 assert.match(await page.locator('#reg-candidates').textContent(),/Included/);
 assert.match(await page.locator('#reg-candidates').textContent(),/12.400/);
 assert.match(await page.locator('#reg-candidates').textContent(),/centre is retained/);
 assert.equal(await page.locator('#reg-pairs tr[data-row]').count(),1);
 assert.equal(await page.evaluate(()=>calls.some(c=>c.url.startsWith('/api/reg-preview'))),false);
 assert.equal(await page.locator('[data-delete-candidate="sphere-1"]').isVisible(),false);
 await page.locator('[data-target-details="sphere-1"] > summary').click();
 await page.click('[data-delete-candidate="sphere-1"]');
 await page.waitForFunction(()=>!window.regData.candidates.length);
 assert.equal(await page.evaluate(()=>confirms.length),0,'Reversible target deletion should not ask for another acceptance');
 assert.equal(await page.evaluate(()=>calls.filter(c=>c.body?.action==='remove_targets').length),1);
 // The correspondence-row delete follows the same target-and-pair deletion path.
 await page.evaluate(async()=>{
  window.regData.candidates=[{id:'bw-2',name:'T2',source:[2,3,4],status:'accepted',target_type:'bw_known',plane_rms_mm:1.2,score:.9}];
  window.regData.rows=[{id:'T2',candidate_id:'bw-2',role:'fit',source:[2,3,4],control:[null,null,null]}];
  await window.TargetUI.sync();
 });
 await page.click('#reg-pairs [data-remove]');
 await page.waitForFunction(()=>!window.regData.candidates.length);
 assert.equal(await page.evaluate(()=>calls.filter(c=>c.body?.action==='remove_targets').length),2);
 // A blank manual row can be removed locally before valid coordinates exist.
 await page.click('#reg-add-row');assert.equal(await page.locator('#reg-pairs tr[data-row]').count(),1);
 await page.click('#reg-pairs [data-remove]');assert.equal(await page.locator('#reg-pairs tr[data-row]').count(),0);
 assert.deepEqual(errors,[]);
 await browser.close();
 console.log(JSON.stringify({status:'passed',scenarios:10,checks:['automatic common targets','fixed setup choice','2D to 3D pair transition','independent check/excluded roles','no accept gate','high-RMS target display','stale result rejection','hidden preview avoided','correspondence deletion removes target','blank manual-row deletion']},null,2));
}
run().catch(error=>{console.error(error);process.exitCode=1;});
