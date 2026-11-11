/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const el=id=>document.getElementById('feature-'+id);
 const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const count=value=>Number(value||0).toLocaleString('en-GB');
 const panel=document.createElement('section');panel.id='tab-feature-selection';panel.className='tab feature-panel';
 const field=(id,label,value,min,max,help)=>`<label><span>${label}</span><input id="feature-${id}" type="number" value="${value}" min="${min}" max="${max}" step="any" title="${help}"><small>${help}</small></label>`;
 panel.innerHTML=`<header class="feature-heading"><span class="feature-eyebrow">POINT SELECTION</span><h2 id="feature-title">Grow a connected region</h2><p id="feature-intro"></p></header>
 <section class="feature-step"><h3><span>1</span> Choose the measured surface</h3><label>Source scan<select id="feature-source" title="The scan containing the original measured points. Only this scan can be changed by this tool."></select></label><label id="feature-scope-row">Processing area<select id="feature-scope"><option value="whole">Lining analysis</option><option value="local">Near a picked point · local search</option></select><small>The saved limit box or current highlight scopes lining analysis.</small></label><label id="feature-kind-row">Feature shape<select id="feature-kind"><option value="departures">All lining departures · both raised and recessed</option><option value="recess">Recessed bolt pocket · dip below the wall</option><option value="raised">Raised fitting · bump above the wall</option></select></label><button id="feature-pick" type="button">Pick seed in 3D</button><p id="feature-seed" class="feature-hint">Pick a point on the surface you want to select.</p></section>
 <section class="feature-step"><h3><span>2</span> Set the local search</h3><div class="feature-fields">${field('radius','Search radius · m',.6,.03,20,'Only retained points within this distance of the seed are checked. A smaller area is quicker and easier to review.')}${field('connection','Connection distance · mm',18,.1,500,'Largest gap between neighbouring points in the same group. Increase for sparse scans; decrease to avoid joining adjacent objects.')}<div id="feature-region-fields">${field('angle','Maximum surface turn · °',25,1,85,'Neighbours must have similar surface directions. Lower values stop growth at smaller changes of direction.')}${field('offset','Surface step limit · mm',12,.1,500,'Stops growth across a height step between nearby surfaces. Smaller values can separate a pipe or bolt from the wall.')}</div><div id="feature-bolt-fields" hidden>${field('height','Minimum protrusion · mm',8,.5,300,'Points must stand at least this far towards you from surrounding measured lining. Choose a value above the scan roughness.')}${field('diameter','Maximum fitting diameter · mm',250,5,2000,'Rejects broad or elongated protrusions. The search radius must be at least this diameter, with lining visible around the feature.')}<label class="feature-check"><input id="feature-flip" type="checkbox" title="Reverses which side counts as protruding. The default side faces the camera when you picked the seed.">Reverse inferred inside direction</label><small id="feature-side-help">Local search uses the view direction recorded when you picked the seed. Look from inside the tunnel towards the wall.</small></div></div>
 <details class="feature-advanced"><summary>Advanced settings</summary><div class="feature-fields">${field('neighbours','Neighbours for surface direction',16,6,40,'More neighbours smooth local direction estimates; fewer follow small details. Used by seeded region growth.')}${field('max-height','Maximum protrusion · mm',150,1,1000,'Taller points are left for a separate cleaning review, so distant services are not treated as bolt heads.')}${field('min-points','Minimum candidate points',8,3,100000,'Rejects small disconnected groups with less measured support. Used for bolt and fitting candidates.')}<label>Local point budget<select id="feature-budget" title="This is a processing limit, not sampling. If the local area exceeds it, the operation stops and asks for a smaller area."><option value="100000">100,000 points</option><option value="250000">250,000 points</option><option value="500000" selected>500,000 points</option><option value="1000000">1,000,000 points</option></select><small>Every retained point inside the area is checked. Over-budget areas stop without changing the previous result.</small></label><label class="feature-check"><input id="feature-use-area" type="checkbox" title="Also restrict the local search to the current box, lasso or polygon selection.">Also use the current drawn selection</label></div></details>
 <div class="feature-actions"><button id="feature-preview" class="primary" type="button">Preview selection</button><button id="feature-stop" hidden type="button">Stop</button></div><p id="feature-status" role="status" aria-live="polite"></p></section>
 <section id="feature-review" class="feature-step" hidden><h3><span>3</span> Review before removing</h3><p id="feature-summary"></p><p class="feature-hint"><b>Orange:</b> selected points. <b>Cyan:</b> unselected candidate. <b>Grey:</b> surrounding measured points.</p><div id="feature-warnings" class="feature-warnings"></div><div class="feature-actions"><button id="feature-all" type="button">Select all candidates</button><button id="feature-none" type="button">Clear selection</button></div><div class="feature-group-list" id="feature-groups"></div><p id="feature-page"></p><div class="feature-actions"><button id="feature-previous" type="button">Previous</button><button id="feature-next" type="button">Next</button></div>
 <details class="feature-advanced"><summary>Refine the selected points</summary><p class="feature-hint">Draw a box, polygon or lasso in the main view, then keep only that area or exclude it. Choosing candidate groups again resets this trimming.</p><div class="feature-actions"><button id="feature-keep" type="button">Keep within drawn area</button><button id="feature-exclude" type="button">Exclude drawn area</button></div></details><p id="feature-display-note" class="feature-hint"></p><div class="feature-actions"><button id="feature-separate" type="button">Separate selected points to new scan</button><button id="feature-remove" class="danger" type="button">Remove selected points</button><button id="feature-restore" type="button">Restore normal view</button></div><p class="feature-hint">Removal keeps original scan coordinates and is reversible with Undo. Missing lining behind fittings is not filled.</p></section>`;
 document.querySelector('.view-shell').append(panel);
 let mode='region',seed=null,result=null,running=false,dirty=true,page=0;
 const controlIds=['source','scope','kind','pick','radius','connection','angle','offset','height','diameter','flip','neighbours','max-height','min-points','budget','use-area','preview'];
 const status=(text,error=false)=>{el('status').textContent=text;el('status').classList.toggle('error',error);};
 function controls(){
  const locked=running||!!busy;
  controlIds.forEach(id=>el(id).disabled=locked);
  el('preview').disabled=locked||(!wholeScope()&&!seed);
  el('pick').disabled=locked||wholeScope();
  ['all','none','keep','exclude'].forEach(id=>el(id).disabled=locked||!result||dirty||result.stale);
  el('remove').disabled=locked||!result||dirty||result.stale||!result.statistics.selected_points;el('separate').disabled=el('remove').disabled;
  el('stop').hidden=!running;el('previous').disabled=locked||page===0;el('next').disabled=locked||!result||(page+1)*40>=result.groups.length;
  el('groups').querySelectorAll('input').forEach(input=>input.disabled=locked||dirty||result?.stale);
 }
 function changed(){dirty=true;if(result)status('Settings changed. Preview again before selecting or removing points.');controls();}
 function settings(){
  for(const id of ['radius','connection','angle','offset','height','diameter','neighbours','max-height','min-points'])if(!(wholeScope()&&['radius','connection','angle','offset','neighbours'].includes(id))&&!el(id).checkValidity())throw Error('Check '+el(id).closest('label').querySelector('span').textContent+'.');
  return {radius_m:+el('radius').value,connection_mm:+el('connection').value,normal_angle_deg:+el('angle').value,surface_distance_mm:+el('offset').value,normal_neighbours:+el('neighbours').value,min_height_mm:+el('height').value,max_height_mm:+el('max-height').value,max_diameter_mm:+el('diameter').value,min_points:+el('min-points').value,max_points:+el('budget').value,flip_side:el('flip').checked,feature_kind:el('kind').value};
 }
 function render(){
  el('review').hidden=!result;
  if(!result){controls();return;}
  const s=result.statistics;
  el('summary').textContent=`${count(s.selected_points)} selected points · ${count(s.candidate_points)} candidate points · ${count(s.local_points)} local points checked. ${s.reference_rms_mm!==undefined?'Surrounding lining fit RMS: '+Number(s.reference_rms_mm).toFixed(2)+' mm.':''}`;
  el('warnings').replaceChildren();for(const text of [...(result.stale?['The source or its visible area changed. Preview again before applying this selection.']:[]),...result.warnings]){const p=document.createElement('p');p.textContent=text;el('warnings').append(p);}
  const rows=result.groups.slice(page*40,(page+1)*40);
  el('groups').innerHTML=rows.map(g=>`<label class="feature-group"><input type="checkbox" data-feature-group="${g.id}" ${g.selected?'checked':''}><span><strong>${escape(g.name)}</strong><small>${count(g.points)} points${g.partial?' · '+count(g.selected_points)+' selected':''}${g.height_mm===undefined?'':` · ${g.height_mm.toFixed(1)} mm ${result.settings.feature_kind==='recess'?'pocket depth':'height above wall'} · ${g.diameter_mm.toFixed(1)} mm extent`}</small></span></label>`).join('');
  for(const input of el('groups').querySelectorAll('input'))input.indeterminate=!!rows.find(g=>g.id===+input.dataset.featureGroup)?.partial;
  el('page').textContent=result.groups.length?`${page*40+1}–${Math.min((page+1)*40,result.groups.length)} of ${count(result.groups.length)} groups`:'No candidate groups';
  el('display-note').textContent=`3D display: ${count(result.preview.preview_count)} of ${count(s.local_points)} local points. Selection and removal use all ${count(s.selected_points)} selected original point IDs.`;
  controls();
 }
 async function receiveSeed(value){
  if(!value?.point||!value?.source_id){status('Pick a retained scan point with an identifiable source.',true);return;}
  seed=value;el('source').value=value.source_id;dirty=true;result=null;page=0;
  el('seed').textContent=`Picked E ${value.point[0].toFixed(4)} · N ${value.point[1].toFixed(4)} · H ${value.point[2].toFixed(4)} m`;
  status('Seed picked. Adjust the local search, then preview.');render();
 }
 async function open(which='region'){
  if(running||busy)return;
  mode=['bolts','departures'].includes(which)?'bolts':'region';seed=null;result=null;dirty=true;page=0;
  const scans=window.JobUI.scanItems().filter(x=>x.visible!==false),selected=window.JobUI.selectedItem?.();
  el('source').innerHTML=scans.map(x=>`<option value="${escape(x.id)}">${escape(x.name)}</option>`).join('');
  if(selected?.kind==='scan'&&scans.some(x=>x.id===selected.id))el('source').value=selected.id;
  el('title').textContent=mode==='bolts'?'Select bolt pockets & fittings':'Grow a connected region';
  el('intro').textContent=mode==='bolts'?'Choose a recessed pocket for a bolt sitting in a dip, or a raised fitting for a part sticking above the wall. Preview candidates, then remove only reviewed measured points.':'Pick a point on a pipe, lining patch or other surface. Growth follows connected neighbours with similar surface directions.';
  el('region-fields').hidden=mode!=='region';el('bolt-fields').hidden=mode!=='bolts';
  el('scope').value=mode==='bolts'?'whole':'local';el('kind').value=which==='departures'?'departures':'recess';if(which==='departures')el('height').value=5;scopeInfo();
  el('radius').value=mode==='bolts'?.6:1;el('flip').checked=false;el('use-area').checked=false;
  if(mode!=='bolts')el('seed').textContent='Pick a point on the surface you want to select.';
  window.JobUI.clearFeaturePreview?.();window.TunnelRibbon.view('feature-selection');status(scans.length?(wholeScope()?'Choose the feature shape and preview the selected processing area.':'Pick the seed in the main 3D view.'):'Import a scan before using this tool.',!scans.length);render();
 }
 function wholeScope(){return mode==='bolts'&&el('scope').value==='whole';}
 function activeLimitBox(){
  const scan=window.JobUI.scanItems().find(item=>item.id===el('source').value);
  return !!scan?.limit_box;
 }
 function scopeInfo(){
  const whole=wholeScope(),bolt=mode==='bolts',recess=el('kind').value==='recess';
  el('scope-row').hidden=!bolt;el('kind-row').hidden=!bolt;
  for(const id of ['radius','connection','flip','budget','use-area'])el(id).closest('label').hidden=whole;
  for(const id of ['diameter','max-height','min-points'])el(id).closest('label').hidden=false;
  el('pick').hidden=whole;el('seed').hidden=whole;el('side-help').hidden=whole;
  for(const [id,label] of [['height',recess?'Minimum pocket depth · mm':'Minimum height above wall · mm'],['max-height',recess?'Maximum pocket depth · mm':'Maximum height above wall · mm']]){
   const field=el(id).closest('label');field.querySelector('span').textContent=label;
   field.querySelector('small').textContent=recess?'Measured into the dip from nearby wall. A bolt may sit inside the pocket without projecting above the wall.':'Measured from nearby wall into the tunnel. Use this for a bolt head or fitting that stands above the wall.';
   el(id).title=field.querySelector('small').textContent;
  }
  el('diameter').closest('label').querySelector('span').textContent=recess?'Maximum pocket diameter · mm':'Maximum fitting diameter · mm';
  el('height').min=whole?2:.5;el('height').max=whole?100:300;
  el('diameter').min=whole?50:5;el('diameter').max=whole?800:2000;
  if(whole)el('flip').checked=false;
  const highlighted=window.JobUI.selectedItem()?.id===el('source').value&&window.JobUI.currentSelection();
  const area=highlighted?'Highlighted points':activeLimitBox()?'Saved limit box':'Whole retained scan';
  el('scope').querySelector('[value=whole]').textContent=area;
  el('preview').textContent=whole?(highlighted?'Analyse highlighted points':activeLimitBox()?'Analyse inside limit box':'Analyse whole scan'):'Preview local selection';
  el('seed').textContent=recess?'From inside the tunnel, face the wall and click inside or beside the bolt pocket. Include surrounding wall.':'From inside the tunnel, face the wall and click a raised fitting. Include surrounding wall.';
  if(el('kind').value==='departures'&&bolt){
   for(const id of ['diameter','max-height','min-points','flip','connection'])el(id).closest('label').hidden=true;
   el('height').closest('label').querySelector('span').textContent='Lining tolerance on both sides · ± mm';
   el('height').closest('label').querySelector('small').textContent='Select every supported point farther than this distance above or below the fitted lining. No pocket-size or shape filter is used.';
   el('height').title=el('height').closest('label').querySelector('small').textContent;
   el('seed').textContent='Pick surrounding lining beside the feature. Include plenty of wall in the local search.';
   el('title').textContent='Select all lining departures';el('intro').textContent='Fit the measured lining, keep a tolerance band, then review points on both sides. Select, remove or separate them without changing original coordinates.';
  }
  controls();
 }
 async function runWhole(){
  const cfg=settings();if(cfg.feature_kind!=='departures'&&cfg.max_height_mm<=cfg.min_height_mm)throw Error('Maximum depth/height must exceed the minimum.');
  const id=el('source').value;if(!id)throw Error('Choose a source scan.');
  running=true;controls();
  try{
   window.JobUI.clearFeaturePreview?.();
   if(window.JobUI.selectedItem()?.id!==id)window.JobUI.selectScan(id);
   if(!await window.JobUI.openSurface())throw Error('Choose the scan and open lining analysis again.');
   window.SurfaceUI.configureBolts(cfg);
   $('surf-run').click();
  }finally{running=false;controls();}
 }
 async function run(action,extra={}){
  if(running||busy)return;
  if(action==='preview'&&wholeScope()){try{await runWhole();}catch(error){status(error.message,true);notice(error.message,true);}return;}
  try{
   let body;
   if(action==='preview'){
    if(!seed||seed.source_id!==el('source').value)throw Error('Pick a seed on the selected scan.');
    body={action,mode,source_id:seed.source_id,source_revision:seed.source_revision,seed:seed.point,view_direction:seed.view_direction,settings:settings()};
    if(el('use-area').checked){body.selection=window.JobUI.currentSelection();if(!body.selection)throw Error('Draw a box, polygon or lasso first, or clear Also use current drawn selection.');}
   }else{
    if(!result||dirty||result.stale)throw Error('Preview the current settings and source again.');
    body={action,preview_id:result.id,revision:result.revision,...extra};
   }
   running=true;setBusy(true);controls();status(action==='delete'?'Removing the reviewed original points…':action==='preview'?'Checking the local original points…':'Updating the reviewed selection…');
   await post('/api/feature-selection',body);
   if(action!=='select'){
    const job=await window.JobUI.waitReady(value=>status(value.message||'Working…'));
    if(job.cancelled)throw Error('Stopped. The previous result and source scan are unchanged.');
   }
   const data=await api('/api/feature-selection');result=data.result;dirty=false;page=action==='preview'?0:page;
   if(['delete','separate'].includes(action)){
    window.JobUI.clearFeaturePreview?.();status(`${count(result?.last_delete?.count)} points ${action==='separate'?'separated into a new scan':'removed'}. Undo reverses the edit.`);
   }else if(result&&!result.stale){window.JobUI.showFeaturePreview(result);status(action==='preview'?'Review the coloured points. Choose candidates and refine them before removal.':'Selection updated. Orange points are the ones selected for removal.');}
   render();
  }catch(error){status(error.message,true);}finally{running=false;setBusy(false);controls();}
 }
 el('pick').onclick=()=>{
  if(running||busy)return;
  window.JobUI.clearFeaturePreview?.();window.JobUI.selectScan(el('source').value);
  window.JobUI.pickFeatureSeed(receiveSeed,{source_id:el('source').value,prompt:mode==='bolts'?(el('kind').value==='departures'?'Pick surrounding wall beside the feature, with lining visible around it.':el('kind').value==='recess'?'Face the wall from inside the tunnel, then click inside or beside the bolt pocket. Include surrounding wall.':'Face the wall from inside the tunnel, then click a raised fitting. Include surrounding wall.'):'Pick the connected surface to grow.'});
 };
 el('preview').onclick=()=>run('preview');
 el('source').onchange=()=>{seed=null;el('seed').textContent='Pick a seed on this scan.';scopeInfo();changed();};
 for(const id of ['scope','kind'])el(id).addEventListener('change',()=>{scopeInfo();changed();});
 for(const id of controlIds.filter(x=>!['source','pick','preview'].includes(x)))el(id).addEventListener('input',changed);
 el('groups').addEventListener('change',event=>{if(!event.target.dataset.featureGroup)return;const groups=new Set(result.groups.filter(g=>g.selected).map(g=>g.id)),id=+event.target.dataset.featureGroup;event.target.checked?groups.add(id):groups.delete(id);run('select',{group_ids:[...groups]});});
 el('all').onclick=()=>run('select',{group_ids:result.groups.map(g=>g.id)});el('none').onclick=()=>run('select',{group_ids:[]});
 for(const [id,operation] of [['keep','keep'],['exclude','exclude']])el(id).onclick=()=>{const selection=window.JobUI.currentSelection();if(!selection){status('Draw a box, polygon or lasso in the main view first.',true);return;}run('refine',{operation,selection});};
 el('remove').onclick=()=>{
  if(!result||dirty||result.stale||running||busy||!result.statistics.selected_points)return;
  showTunnelMessage('Remove reviewed points',`Remove ${count(result.statistics.selected_points)} selected original points from ${result.source_name}? Undo can restore them. No lining will be added behind the removed points.`,true,'Remove selected points',()=>run('delete',{confirmed:true}));
 };
 el('separate').onclick=()=>{if(!el('separate').disabled)run('separate',{confirmed:true});};
 el('restore').onclick=()=>window.JobUI.clearFeaturePreview?.();
 el('stop').onclick=async()=>{status('Stopping at the next local read or geometry checkpoint…');try{await post('/api/cancel-operation',{});}catch(error){status(error.message,true);}};
 el('previous').onclick=()=>{page=Math.max(0,page-1);render();};el('next').onclick=()=>{page++;render();};
 window.FeatureSelectionUI={open,receiveSeed,close:()=>{window.JobUI.clearFeaturePreview?.();window.TunnelRibbon.closeTool();},onToolClose:()=>{if(!running){window.JobUI.clearFeaturePreview?.();window.JobUI.cancelFeatureSeed?.();window.JobUI.setMode?.('orbit');}},removeSelected:()=>el('remove').onclick(),removableCount:()=>!dirty&&!result?.stale?result?.statistics.selected_points||0:0,result:()=>result,busy:controls,render,active:()=>panel.classList.contains('drawer-active')};
})();
