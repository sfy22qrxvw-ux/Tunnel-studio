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
