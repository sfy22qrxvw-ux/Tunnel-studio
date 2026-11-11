'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const context={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'web/ribbon-layout.js'),'utf8'),context);
const layout=context.window.RibbonLayout;
const matrix={
 none:{targets:'Choose a setup or bundle',cloud:'Choose reference and moving groups',control:'Import control CSV'},
 single:{targets:'Pick & fit targets',cloud:'Select a second group',control:'Choose a bundle for control'},
 pair:{targets:'Match setup targets',cloud:'Align and refine',control:'Select a bundle instead'},
 bundle:{targets:'Optimise saved targets',cloud:'Refine bundle links',control:'Review control fit'}
};
const ids=new Set(),labels=new Set();
for(const [selection,modes] of Object.entries(matrix)){
 for(const [mode,label] of Object.entries(modes)){
  const actions=layout.registrationActions(selection,mode);
  assert.equal(actions.length,1,selection+' '+mode);
  const [action]=actions;
  assert.equal(action.label,label);
  assert.equal(ids.has(action.id),false);
  assert.equal(labels.has(action.label),false);
  ids.add(action.id);labels.add(action.label);
 }
}
const make=(id,label)=>({id,label});
const ribbon=layout.build({command:make,proxy:make,dropdown:make});
assert.equal(ribbon.Registration.flatMap(group=>group.slice(1)).length,1);
console.log('All 12 selection × mode cells have one distinct primary action; Registration ribbon has one launch command.');

// Exercise the production diagnostics hooks with isolated device storage.
const ribbonSource=fs.readFileSync(path.join(__dirname,'web/ribbon.js'),'utf8');
const section=(from,to)=>ribbonSource.slice(ribbonSource.indexOf(from),ribbonSource.indexOf(to));
const saved=new Map(),fields=new Map(),rendered=[];
const field=id=>{if(!fields.has(id))fields.set(id,{});return fields.get(id);};
const logs={window:{},$:field,localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},
 requestAnimationFrame:cb=>rendered.push(cb),performance:{now:()=>150},notice(){throw Error('unexpected notice');}};
vm.runInNewContext(section(' const logKey='," $('reg-flow-export').onclick="),logs);
const d=logs.window.RegistrationDiagnostics;
assert.equal(d.enabled(),false);
assert.equal(d.begin('bundle','targets'),null);
assert.equal(saved.size,0);
field('reg-flow-log').checked=true;field('reg-flow-log').onchange();
const run=d.begin('bundle','targets');
d.report(run,{cloud_read_ms:0,solve_ms:12},100);
d.finish(run,'abandoned');
while(rendered.length)rendered.shift()();
const data=JSON.parse([...saved.values()][0]);
assert.deepEqual(data.timing.map(row=>[row.cloud_read_ms,row.solve_ms,row.review_display_ms]),[[0,12,50]]);
assert.equal(data.navigation[0].event,'abandoned');
assert.equal(data.navigation[0].id,data.timing[0].id);
field('reg-flow-log').checked=false;field('reg-flow-log').onchange();
assert.equal(d.begin('bundle','targets'),null);
console.log('Diagnostics default off and keep timing and navigation in separate local streams.');

const linkSource=fs.readFileSync(path.join(__dirname,'web/scan-registration-ui.js'),'utf8');
const sortContext={};
vm.runInNewContext(linkSource.slice(linkSource.indexOf(' function sortPairResiduals('),
                                    linkSource.indexOf(' function renderPairPreview(')),sortContext);
const pairs=[{index:0,residual_mm:null},{index:1,residual_mm:12},
             {index:2,residual_mm:1},{index:3,residual_mm:12}];
const sorted=descending=>Array.from(sortContext.sortPairResiduals(pairs,descending),pair=>pair.index);
assert.deepEqual(sorted(true),[1,3,2,0]);
assert.deepEqual(sorted(false),[2,1,3,0]);
console.log('Target pair residuals sort both ways, with unavailable values last.');

async function checkControlPlan(){
 const jobSource=fs.readFileSync(path.join(__dirname,'web/job-ui.js'),'utf8');
 const extract=(from,to)=>jobSource.slice(jobSource.indexOf(from),jobSource.indexOf(to));
 const calls=[];
 const context={
  busy:false,uploading:false,loading:false,setupState:true,previewRequest:null,detailView:null,
  previewRequested:false,signature:'old',fitPreviewPending:false,packed:null,
  data:{items:[{id:'a',minimum:[0,0,0],maximum:[1,1,1]}]},
  jobScene:{yaw:0,pitch:0,draw(){}},$(){return {};},clearCleaningPreview(){},clearPreviewCaches(){},
  focusWorld:(bounds,refine)=>calls.push({bounds,refine}),fitWholeScene:()=>calls.push('fit all'),
  window:{SetupCameraUI:{exit:async()=>{context.setupState=false;calls.push('exit setup');}}},
  sync:async()=>{
   context.data.items=[{id:'a',minimum:[100,200,1],maximum:[105,208,4]},
     {id:'unrelated',minimum:[900,900,0],maximum:[950,950,5]}];
   context.packed=[1];
  }
 };
 vm.runInNewContext(extract(' async function loadPreview(',' async function projectOpened(')+
                    extract(' function setView(name)','\n for(const b of document.querySelectorAll(\'[data-job-nav]\'))'),context);
 assert.equal(await context.loadPreview({northUp:true,fitIds:['a']}),true);
 assert.equal(context.jobScene.yaw,Math.PI);
 assert.equal(context.jobScene.pitch,Math.PI/2);
 assert.deepEqual(JSON.parse(JSON.stringify(calls)),['exit setup',{bounds:[[100,200,1],[105,208,4]],refine:false}]);
 context.jobScene.yaw=.7;
 await context.loadPreview();
 assert.equal(context.jobScene.yaw,.7);
 assert.equal(calls.length,2);
}
checkControlPlan().then(()=>console.log('Apply control plan exits setup view, uses updated bounds and frames only the positioned scans.')).catch(error=>{console.error(error);process.exitCode=1;});

async function checkBundleMerge(){
 const source=fs.readFileSync(path.join(__dirname,'web/selected-merge-ui.js'),'utf8');
 const fields=new Map(),requests=[],messages=[];
 const get=id=>{if(!fields.has(id))fields.set(id,{});return fields.get(id);};
 const dialog={open:false,addEventListener(){},showModal(){this.open=true;}};
 const items=[
  {id:'a',kind:'scan',name:'Setup A',registration_summary:{links:[{other_id:'b'}]}},
  {id:'b',kind:'scan',name:'Setup B',registration_summary:{links:[{other_id:'a'}]}},
  {id:'other',kind:'scan',name:'Other scan'}
 ];
 const context={busy:false,window:{JobUI:{inventory:()=>({items,bundles:[{id:'bundle',name:'Bundle 1',scan_ids:['a','b']}]})}},
  document:{createElement:()=>dialog,getElementById:get,body:{append(){}}},
  esc:x=>x,fmt:x=>String(x),notice:x=>messages.push(x),
  post:async(_url,body)=>{requests.push(JSON.parse(JSON.stringify(body)));return {setups:2,points:100,enough:true};}};
 vm.runInNewContext(source,context);
 await context.window.SelectedMergeUI.openBundle('bundle');
 assert.deepEqual(requests,[{action:'selection_merge_estimate',ids:['a','b']}]);
 assert.equal(get('selected-merge-title').textContent,'Merge Bundle 1');
 assert.equal(get('selected-merge-start').textContent,'Merge bundle');
 assert.equal(get('selected-merge-start').disabled,false);
 await context.window.SelectedMergeUI.openBundle('missing');
 assert.equal(requests.length,1);
 assert.match(messages[0],/Select a connected bundle/);
}
checkBundleMerge().then(()=>console.log('Merge bundle reviews exactly the setups in the selected bundle.')).catch(error=>{console.error(error);process.exitCode=1;});
