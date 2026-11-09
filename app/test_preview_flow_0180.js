'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/web/job-ui.js','utf8');
const start=source.indexOf(' async function loadBinary(){');
const end=source.indexOf(' async function sync(options={})',start);
assert(start>=0&&end>start);
const fn=source.slice(start,end);
function environment(fetch){
 const shown=[],status=[],nodes={};
 const context={AbortController,URLSearchParams,Map,Promise,JSON,Float32Array,DataView,
  requestAnimationFrame:fn=>setImmediate(fn),busy:false,busyUI:()=>{},
  deletedMode:false,setupState:null,setupDisplay:'points',detailView:null,liveLimitEditing:false,
  data:{items:[{id:'scan',kind:'scan',visible:true,count:10000000}]},
  normalOverview:null,setupOverview:null,meshPreview:null,previewRequest:null,previewEpoch:0,
  cleanSnapshot:null,previewOwner:null,packed:null,
  previewRefreshPending:false,setupPreviews:new Map(),sourceInfo:{sample:0},
  key:'geometry-1',previewContentKey:()=>context.key,overviewKey:()=>context.key,
  density:()=>35,displayMode:'rgb',clearCleaningPreview:()=>{},fetch,
  showPointBuffer:(buffer,detailed,stage)=>{shown.push([buffer.id,stage]);context.packed=buffer;},
  previewFeedback:(kind,text)=>status.push([kind,text]),
  showMeshCache:()=>true,visibleMeshes:()=>[],publishMeshPreview:()=>{},
  $:id=>nodes[id]??=( {} ),window:{},jobScene:{},
 };
 vm.createContext(context);vm.runInContext(fn+'\nthis.load=loadBinary;',context);
 return {context,shown,status};
}
function reply(id){
 // The real loader reads the binary header before displaying a quick response.
 // Supply a real, nonempty TSPV buffer rather than a byteLength-only stand-in.
 const buffer=new ArrayBuffer(56+24),header=new DataView(buffer);
 new Uint8Array(buffer,0,4).set([84,83,80,86]);
 header.setFloat64(32,1,true);buffer.id=id;
 return {ok:true,arrayBuffer:async()=>buffer};
}
async function run(){
 let calls=[];
 let env=environment(async url=>{calls.push(url);if(url.includes('quick=1'))return reply('overview');throw Error('detail unavailable');});
 await assert.rejects(env.context.load(),/detail unavailable/);
 assert.deepEqual(env.shown,[['overview','overview']]);
 assert.equal(env.context.normalOverview.complete,false);
 // A usable initial view survives failure; retry requests the missing detail,
 // instead of mistaking the early overview for a completed cached response.
 env.context.fetch=async url=>{calls.push(url);return reply('complete');};
 await env.context.load();
 assert.equal(calls.length,3);assert(!calls[2].includes('quick=1'));
 assert.equal(env.context.normalOverview.complete,true);
 assert.equal(env.shown.at(-1)[0],'complete');
 let count=calls.length;await env.context.load();assert.equal(calls.length,count);

 // A registration/visibility change while a response is pending cannot publish
 // the obsolete geometry, even if that server request eventually completes.
 let complete;let ready;
 const pending=new Promise(resolve=>ready=resolve);
 env=environment(async url=>url.includes('quick=1')?reply('overview'):
  new Promise(resolve=>{complete=resolve;ready();}));
 const loading=env.context.load();await pending;env.context.key='geometry-2';complete(reply('obsolete'));
 await loading;assert.equal(env.context.previewRefreshPending,true);
 assert(!env.shown.some(([id])=>id==='obsolete'));
 console.log('Progressive preview: retry, complete-cache reuse and stale-response checks passed.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
