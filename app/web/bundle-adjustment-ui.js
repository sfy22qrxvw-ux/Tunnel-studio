/* Joint bundle review. Draft poses never change the main workspace before Apply. */
'use strict';
(()=>{
 const dialog=document.createElement('dialog');dialog.id='bundle-adjustment-dialog';dialog.className='ts-analysis-dialog';
 const el=id=>document.getElementById('ba-'+id),fmt=v=>Number.isFinite(v)?v.toFixed(3):'—';
 let graph=null,loops=[],token=null,result=null,working=false,inspecting=false,generation=0,frame='plan',after=true,zoom=1,pan=[0,0],drag=null,onApplied=null,selectedBundle=null,opening=false,running=false,showTargetsRetry=false,lastUsedCloud=true,diagnostic=null;
 dialog.innerHTML=`<header><div><h2 id="ba-title">Bundle adjustment & loop closure</h2><p id="ba-subtitle">Adjust all connected setups together. Every setup can move within the group’s existing local frame.</p></div><button id="ba-close" aria-label="Close bundle adjustment">×</button></header>
 <div class="ts-analysis-settings" id="ba-settings"><label>Connected group<select id="ba-anchor"></select></label><label>Sampling<select id="ba-quality"><option value="fast">Fast</option><option value="balanced" selected>Balanced</option><option value="high">High</option></select></label><label>Residual review level (mm)<input id="ba-tolerance" type="number" min=".01" max="1000" value="5" step=".1"></label><label>Cloud search distance (mm)<input id="ba-capture" type="number" min="1" max="10000" value="250"></label></div>
 <p id="ba-group"></p><div id="ba-layout" class="ts-analysis-split"><section id="ba-loop-tools">
 <h3>Links used for adjustment</h3><p id="ba-link-plan" aria-live="polite"></p>
 <details><summary>Add optional loop pairs</summary>
 <p>Queue pairs that see the same physical surfaces. Queued pairs must pass validation during adjustment.</p>
 <div class="ts-analysis-settings"><label>First setup<select id="ba-a"></select></label><label>Second setup<select id="ba-b"></select></label><button id="ba-add">Queue loop pair</button></div>
 <div id="ba-loops"></div>
 </details>
 <details><summary id="ba-suggestion-label">Possible overlaps · suggestions only</summary>
 <p>These pairs are suggested because their scan bounds overlap. They are outside the adjustment unless you queue them.</p>
 <div id="ba-suggestions"></div></details>
 <button id="ba-run" class="primary">Adjust bundle</button></section>
 <section class="ts-analysis-preview"><nav aria-label="Bundle preview controls"><button data-ba-view="plan">Plan</button><button data-ba-view="side">Side elevation</button><button data-ba-view="front">Front elevation</button><button id="ba-before">Before</button><button id="ba-after" class="chosen">After</button><button id="ba-fit">Fit</button></nav><canvas id="ba-canvas" aria-label="Sampled bundle adjustment preview" tabindex="0"></canvas><p>Bounded display samples · Drag to pan, wheel to zoom</p></section></div>
 <section id="ba-review" hidden><div id="ba-summary"></div><div class="ts-analysis-table"><table><thead><tr><th>Link</th><th>Type</th><th>Target RMS (mm)</th><th>Plane RMS before → after (mm)</th><th>Distinct overlap</th></tr></thead><tbody id="ba-links"></tbody></table></div><details><summary>Setup movements</summary><div id="ba-movements"></div></details><div id="ba-warnings"></div></section>
 <footer><p id="ba-status" role="status" aria-live="polite">Select a connected bundle.</p><button id="ba-retry" hidden>Run optimisation again</button><button id="ba-target-retry" hidden>Retry with targets first</button><button id="ba-stop" hidden>Stop adjustment</button><button id="ba-apply" class="primary" disabled>Apply reviewed adjustment</button></footer>`;
 document.body.append(dialog);
 const message=(text,error=false)=>{el('status').textContent=text;el('status').classList.toggle('error',error);};
 function controls(){const pending=working||inspecting;for(const x of dialog.querySelectorAll('input,select,#ba-run,#ba-add,[data-ba-suggest],[data-ba-remove]'))x.disabled=pending||(x.hasAttribute('data-ba-suggest')&&x.hasAttribute('data-ba-queued'));el('close').disabled=working;el('stop').hidden=!running;el('target-retry').hidden=pending||!showTargetsRetry||!!result;el('retry').hidden=!selectedBundle||pending||!!result;el('apply').disabled=pending||!result;el('run').disabled=pending||!graph||graph.scans.length<2;}
 function endDiagnostic(outcome){window.RegistrationDiagnostics?.finish(diagnostic,outcome);diagnostic=null;}
 function invalidate(){endDiagnostic('abandoned');generation++;token=null;result=null;el('review').hidden=true;controls();draw();}
 const name=id=>graph?.scans.find(s=>s.id===id)?.name||id;
 const samePair=(pair,a,b)=>pair.a===a&&pair.b===b||pair.a===b&&pair.b===a;
 function renderLoops(){
  el('loops').innerHTML=loops.map((x,i)=>`<p>${esc(name(x.a))} ↔ ${esc(name(x.b))} · Queued <button data-ba-remove="${i}" aria-label="Remove queued loop pair">Remove</button></p>`).join('')||'<p>No loop pairs queued.</p>';
  for(const x of el('loops').querySelectorAll('button'))x.onclick=()=>{loops.splice(+x.dataset.baRemove,1);invalidate();renderLoops();};
  if(graph){
   const saved=graph.links.length;
   el('link-plan').textContent=`${saved} saved links will be used. ${loops.length?loops.length+' additional '+(loops.length===1?'pair is':'pairs are')+' queued for validation.':'0 additional pairs queued. Adjust bundle uses saved links only.'}`;
   el('suggestion-label').textContent=`Possible overlaps (${graph.suggestions.length} shown of ${graph.suggestions_total}) · suggestions only`;
   el('suggestions').innerHTML=graph.suggestions.map((x,i)=>{
    const queued=loops.some(pair=>samePair(pair,x.a,x.b));
    return `<button data-ba-suggest="${i}" ${queued?'data-ba-queued disabled':''}>${queued?'Queued':'Queue'}: ${esc(name(x.a))} ↔ ${esc(name(x.b))}</button>`;
   }).join('')||'<p>No additional overlaps suggested.</p>';
   for(const x of el('suggestions').querySelectorAll('button'))x.onclick=()=>{const pair=graph.suggestions[+x.dataset.baSuggest];add(pair.a,pair.b);};
  }else{
   el('link-plan').textContent='';
   el('suggestion-label').textContent='Possible overlaps · suggestions only';
   el('suggestions').replaceChildren();
  }
  controls();
 }
 function add(a,b){
  if(working||inspecting||!graph)return;
  if(a===b||!a||!b){message('Choose two different setups.',true);return;}
  if([...loops,...graph.links].some(pair=>samePair(pair,a,b))){message('That pair is already saved or queued.',true);return;}
  loops.push({a,b});invalidate();renderLoops();message('Loop pair queued for validation. Saved links stay as they are until Apply.');
 }
 async function inspect(){
  invalidate();const stamp=generation;inspecting=true;loops=[];graph=null;
  el('group').textContent='Reading connected setups…';
  for(const key of ['a','b'])el(key).replaceChildren();
  renderLoops();message('Reading connected setups…');
  try{
   const value=await post('/api/scan-registration',{action:'bundle_inspect',anchor:el('anchor').value});
   if(stamp!==generation)return;
   graph=value;
   el('group').textContent=`${value.scans.length} setups · ${value.links.length} saved links · ${value.cycle_count} independent loops`;
   for(const key of ['a','b'])el(key).innerHTML=value.scans.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
   if(value.scans.length>1)el('b').selectedIndex=1;
   renderLoops();message('Adjust the saved links, or queue optional loop pairs first. Changes are saved when you click Apply.');
  }catch(e){if(stamp===generation)message(e.message,true);}
  finally{if(stamp===generation){inspecting=false;controls();}}
 }
 const rms=(value)=>Number.isFinite(value)?fmt(value)+' mm':'—';
 const change=(before,after)=>rms(before)+' → '+rms(after);
 function review(value){
  result=value;const s=value.solution;
  el('review').hidden=false;
  const added=selectedBundle?0:loops.length;
  el('summary').innerHTML=`<h3>Optimisation results</h3><p>${s.scan_count} setups · ${s.link_count-added} saved links${added?' · '+added+' validated loop '+(added===1?'pair':'pairs'):''} · ${s.cycle_count} independent loops</p>
   <div class="bundle-qa-metrics"><div><span>Global error</span><b>${change(s.before?.rms_mm,s.fit?.rms_mm)}</b><small>${s.target_fit?.count?'Target RMS':'Cloud-to-cloud plane RMS'}</small></div>
   <div><span>Cloud-to-cloud</span><b>${change(s.cloud_before?.rms_mm,s.cloud_fit?.rms_mm)}</b><small>${s.targets_only?'Targets-only run':(s.measured_cloud_links??0)+' of '+s.link_count+' links with measured surface RMS'}</small></div>
   <div><span>Independent checks</span><b>${rms(s.check?.rms_mm)}</b><small>Check-target RMS</small></div></div>`;
  el('links').innerHTML=s.edge_reports.map(x=>`<tr><td>${esc(x.a)} ↔ ${esc(x.b)}</td><td>${added&&!graph.links.some(link=>link.id===x.id)?'New loop pair':x.loop_closure?'Saved loop':'Saved link'}</td><td>${fmt(x.target_fit?.rms_mm)}</td><td>${fmt(x.cloud?.before?.rms_mm)} → ${fmt(x.cloud?.after?.rms_mm)}</td><td>${x.cloud?.inlier_count??'—'}</td></tr>`).join('');
  el('movements').innerHTML='<table><thead><tr><th>Setup</th><th>Centre movement (mm)</th><th>Rotation (°)</th></tr></thead><tbody>'+value.preview.map(x=>`<tr><td>${esc(x.name)}${x.fixed?' · held fixed':''}</td><td>${fmt(x.shift_mm)}</td><td>${fmt(x.rotation_deg)}</td></tr>`).join('')+'</tbody></table>';
  el('warnings').replaceChildren(...s.warnings.map(x=>{const p=document.createElement('p');p.textContent=x;return p;}));
  zoom=1;pan=[0,0];controls();draw();
 }
 async function runAdjustment(useCloud=true){
  if(working||inspecting||(!selectedBundle&&(!graph||graph.scans.length<2)))return;
  const targetsOnly=!!selectedBundle&&!useCloud;
  showTargetsRetry=false;lastUsedCloud=useCloud;invalidate();working=running=true;controls();message(targetsOnly?'Adjusting from saved Fit targets…':!useCloud?'Adjusting from measured targets and any links that still need cloud refinement…':selectedBundle||!loops.length?'Reading retained points and optimising the saved links…':'Validating queued loop pairs and optimising the bundle…');
  const stamp=generation;
  diagnostic=window.RegistrationDiagnostics?.begin('bundle',useCloud?'cloud':'targets');
  try{
   const options=selectedBundle?{bundle_id:selectedBundle.id,loops:[]}:{anchor:el('anchor').value,quality:el('quality').value,tolerance_mm:+el('tolerance').value,capture_mm:+el('capture').value,loops};
   const started=await post('/api/scan-registration',{action:'bundle_preview',floating:true,...options,...(targetsOnly?{targets_only:true}:!useCloud?{use_cloud:false}:{}),...(diagnostic?{diagnostics:true}:{})});
   token=started.token;
   const job=await window.JobUI.waitReady(p=>{if(p.busy)message(p.message||'Optimising bundle…');},{skipSync:true});
   if(stamp!==generation)return;
   if(job?.cancelled){endDiagnostic('abandoned');message('Optimisation cancelled. You can run it again.');return;}
   const ready=performance.now();
   const value=await post('/api/scan-registration',{action:'bundle_status',token});
   if(value.state!=='ready')throw Error(value.reason||'Bundle optimisation did not produce a reviewable result.');
   review(value);window.RegistrationDiagnostics?.report(diagnostic,value.timings,ready);
   message(value.solution.fallback_used?'The full adjustment did not converge. Review the pairwise fallback before applying.':'Review the RMS and setup movements. Apply saves all adjusted poses together.',!!value.solution.fallback_used);
  }catch(e){
   endDiagnostic('failed');result=null;showTargetsRetry=!selectedBundle&&useCloud&&e.message.includes('Pairwise fallback');
   message(e.message+(showTargetsRetry?' Try targets first to skip cloud refinement on links with a good measured target fit.':''),true);
  }finally{working=running=false;controls();}
 }
 el('run').onclick=()=>runAdjustment();
 el('retry').onclick=()=>runAdjustment(lastUsedCloud);
 el('target-retry').onclick=()=>runAdjustment(false);
 el('stop').onclick=async()=>{el('stop').disabled=true;try{await post('/api/cancel-operation',{});message('Stopping adjustment…');}catch(e){message(e.message,true);}finally{el('stop').disabled=false;}};
 el('apply').onclick=async()=>{if(working||!result)return;working=true;controls();try{const applied=await post('/api/scan-registration',{action:'bundle_apply',token,review_id:result.review_id});endDiagnostic('applied');result=null;dialog.close();await window.JobUI.sync();await poll();await window.JobUI.loadPreview();window.WorkspaceUI?.refreshBundleQA?.();notice(applied.fallback_used?'Full bundle adjustment did not converge. A pairwise adjustment was applied instead. Review residuals before measurement use.':'Floating bundle adjustment saved. Workspace Undo restores the previous poses and links.');if(onApplied)await onApplied();}catch(e){endDiagnostic('failed');result=null;message(e.message,true);}finally{working=false;controls();}};
 el('anchor').onchange=inspect;for(const id of ['quality','tolerance','capture'])el(id).onchange=()=>{invalidate();message('Settings changed. Adjust again before applying.');};el('add').onclick=()=>add(el('a').value,el('b').value);
 function close(){if(working){message('Stop the adjustment before closing.');return;}invalidate();inspecting=false;controls();dialog.close();}el('close').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
 const canvas=el('canvas');function draw(){const ctx=canvas.getContext('2d'),box=canvas.getBoundingClientRect();if(!box.width||!box.height)return;const ratio=window.devicePixelRatio||1;canvas.width=Math.round(box.width*ratio);canvas.height=Math.round(box.height*ratio);ctx.scale(ratio,ratio);ctx.fillStyle='#0b1b24';ctx.fillRect(0,0,box.width,box.height);const clouds=result?.preview||[];if(!clouds.length){ctx.fillStyle='#b5cbd6';ctx.font='14px system-ui';ctx.fillText('Run adjustment to review the bundle',20,35);return;}const axes=frame==='plan'?[0,1]:frame==='side'?[0,2]:[1,2];let lo=[Infinity,Infinity],hi=[-Infinity,-Infinity];for(const c of clouds)for(const p of c[after?'after':'before'])for(let a=0;a<2;a++){lo[a]=Math.min(lo[a],p[axes[a]]);hi[a]=Math.max(hi[a],p[axes[a]]);}const scale=Math.min((box.width-30)/Math.max(.01,hi[0]-lo[0]),(box.height-30)/Math.max(.01,hi[1]-lo[1]))*zoom,centre=lo.map((x,i)=>(x+hi[i])/2);for(const c of clouds){ctx.fillStyle=c.fixed?'#39b6ff':'#ffad5a';ctx.globalAlpha=.65;for(const p of c[after?'after':'before'])ctx.fillRect(box.width/2+(p[axes[0]]-centre[0])*scale+pan[0],box.height/2-(p[axes[1]]-centre[1])*scale+pan[1],1.5,1.5);}ctx.globalAlpha=1;}
 for(const b of dialog.querySelectorAll('[data-ba-view]'))b.onclick=()=>{frame=b.dataset.baView;pan=[0,0];zoom=1;draw();};el('before').onclick=()=>{after=false;el('after').classList.remove('chosen');el('before').classList.add('chosen');draw();};el('after').onclick=()=>{after=true;el('before').classList.remove('chosen');el('after').classList.add('chosen');draw();};el('fit').onclick=()=>{pan=[0,0];zoom=1;draw();};canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(.1,Math.min(50,zoom*Math.exp(-e.deltaY*.001)));draw();};canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY,...pan];canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(drag){pan=[drag[2]+e.clientX-drag[0],drag[3]+e.clientY-drag[1]];draw();}};canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;};new ResizeObserver(draw).observe(canvas);
 function chooseGroup(network,selectedId){
  const unvisited=new Set(network.scans.map(s=>s.id)),neighbours=new Map(network.scans.map(s=>[s.id,[]])),groups=[];
  for(const {a,b} of network.links){
   if(!neighbours.has(a)||!neighbours.has(b))continue;
   neighbours.get(a).push(b);neighbours.get(b).push(a);
  }
  for(const {id,name} of network.scans){
   if(!unvisited.delete(id))continue;
   const members=[id];
   for(let i=0;i<members.length;i++)for(const next of neighbours.get(members[i]))if(unvisited.delete(next))members.push(next);
   if(members.length>1)groups.push({id,name,members});
  }
  if(!groups.length)throw Error('Register at least two setups first.');
  const bundleId=selectedId||window.WorkspaceUI?.selectedBundleId();
  const bundle=window.JobUI.inventory().bundles?.find(b=>b.id===bundleId);
  const picked=bundle?.scan_ids||[window.JobUI.selectedItem()?.id||network.active];
  el('anchor').innerHTML=groups.map(g=>`<option value="${esc(g.id)}">${esc(g.name)} group · ${g.members.length} setups</option>`).join('');
  el('anchor').value=(groups.find(g=>picked.some(id=>g.members.includes(id)))||groups[0]).id;
 }
 async function openAdjustment(bundleId,returnToControl=null,immediate=false,useCloud=false){
  if(busy||working||inspecting||opening||dialog.open)return;
  opening=true;
  try{
   if(window.TunnelRibbon?.prepareTool?.('registration')===false)return;
   await window.TargetUI?.saveIfDirty();
   selectedBundle=null;showTargetsRetry=false;
   if(immediate){
    selectedBundle=window.JobUI.inventory().bundles?.find(b=>b.id===bundleId);
    if(!selectedBundle)throw Error('Select a joined bundle in Project Explorer first.');
   }else chooseGroup(await api('/api/scan-registration'),bundleId);
   onApplied=returnToControl;
   el('settings').hidden=el('loop-tools').hidden=immediate;
   el('layout').classList.toggle('ts-analysis-split',!immediate);
   el('title').textContent=immediate?'Optimise '+selectedBundle.name:'Bundle adjustment & loop closure';
   el('subtitle').textContent=immediate?(useCloud?'Refine the surfaces on the saved links and review the RMS.':'Adjust the bundle using saved Fit targets only. For surface refinement, use the Cloud-to-cloud mode.'):'Adjust all connected setups together. Every setup can move within the group’s existing local frame.';
   if(immediate)el('group').textContent=selectedBundle.scan_ids.length+' linked setups';
   dialog.showModal();
   if(immediate)await runAdjustment(useCloud);
   else await inspect();
  }catch(e){notice(e.message,true);}
  finally{opening=false;}
 }
 window.BundleAdjustmentUI={open:openAdjustment,optimise:(id,onApply)=>openAdjustment(id,onApply,true),refine:id=>openAdjustment(id,null,true,true)};
})();
