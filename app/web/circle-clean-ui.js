/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const el=id=>document.getElementById('circle-'+id),count=n=>Number(n||0).toLocaleString('en-GB');
 const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const panel=document.createElement('section');panel.id='tab-circle-clean';panel.className='tab feature-panel';
 panel.innerHTML=`<header class="feature-heading"><h2>Local-circle cleaning</h2><p class="feature-hint">Fit circles in sections perpendicular to a curved centreline. Keep a band around each local circle and review inward/outward departures. Windows overlap and blend to avoid hard section cuts.</p></header>
 <section class="feature-step"><h3><span>1</span> Source and section length</h3><label>Registered scan<select id="circle-source"></select></label><label>Section direction<select id="circle-axis"><option value="project">Perpendicular to project alignment</option><option value="auto">Estimate from this scan</option></select><small>Project alignment fixes the plane at each chainage. The circle centre and radius are fitted to the measured lining.</small></label>
 <label>Fitting window length · m<input id="circle-length" type="number" min="0.5" max="50" step="0.5" value="5"><small>5 m by default, with 50% overlap. Each circle uses the local perpendicular direction, rather than one fixed tunnel direction.</small></label>
 <label>Processing area<select id="circle-scope"></select></label><p id="circle-scope-note" class="feature-hint"></p></section>
 <section class="feature-step"><h3><span>2</span> Choose the keep band</h3><label>Keep this far inward from the circle · mm<input id="circle-inward" type="number" min="1" max="2000" step="1" value="20"><small>Points closer to the tunnel centre than this are inward candidates, such as services.</small></label>
 <label>Keep this far outward from the circle · mm<input id="circle-outward" type="number" min="1" max="2000" step="1" value="20"><small>Points beyond the outside of this band are outward candidates, such as recesses.</small></label>
 <p class="feature-hint">Poorly supported or poorly circular sections remain unselected. If earlier cleaning removed good lining, restore it with Undo or Deleted points before fitting.</p>
 <div class="feature-actions"><button id="circle-run" class="primary">Preview circles</button><button id="circle-stop" hidden>Stop</button></div><p id="circle-status" role="status" aria-live="polite"></p></section>
 <section id="circle-review" class="feature-step" hidden><h3><span>3</span> Inspect circles and candidates</h3><p id="circle-extent"></p><p id="circle-summary"></p><p class="feature-hint"><b>Green:</b> within band. <b>Yellow:</b> insufficient reference. <b>Cyan:</b> candidate. <b>Orange:</b> selected for an edit.</p><div id="circle-warnings" class="feature-warnings"></div>
 <details id="circle-fit-details"><summary>Inspect fitted circles along the centreline</summary><p id="circle-fit-view" class="feature-hint">Choose a section to see its measured slice and fitted circle.</p><div class="feature-actions"><button id="circle-fit-prev">Previous fits</button><button id="circle-fit-next">Next fits</button><button id="circle-reverse">Reverse view</button><button id="circle-clear-guide">Hide circle</button><button id="circle-whole">Review processed area</button></div><div id="circle-fits" class="feature-group-list"></div><p id="circle-fit-page" class="feature-hint"></p></details>
 <div class="feature-actions"><button id="circle-inward-select">Select inward</button><button id="circle-outward-select">Select outward</button><button id="circle-both">Select both sides</button><button id="circle-none">Clear selection</button></div><div id="circle-groups" class="feature-group-list"></div><p id="circle-page" class="feature-hint"></p><div class="feature-actions"><button id="circle-previous">Previous groups</button><button id="circle-next">Next groups</button></div>

 <div class="feature-actions"><button id="circle-remove" class="danger">Remove selected points</button><button id="circle-separate">Separate selected to new scan</button><button id="circle-normal">Restore normal view</button></div><p class="feature-hint">Preview alone changes no scan points. Reviewed edits preserve original coordinates and support Undo. A fitted circle is not a design surface and does not recreate missing lining.</p></section>`;
 document.querySelector('.view-shell').append(panel);
 let result=null,running=false,dirty=true,fitPage=0,viewIndex=null,reverse=false,captured=null,preferredDirection=null;
 const sliceM=.25;
 const sectionLabel=q=>Number.isFinite(q.chainage_m)?`Ch. ${q.chainage_m.toFixed(3)} m`:`Guide ${q.station_m.toFixed(2)} m`;
 const status=(text,error=false)=>{el('status').textContent=text;el('status').style.color=error?'var(--danger,#b64c45)':'';};
 const source=()=>window.JobUI.scanItems().find(scan=>scan.id===el('source').value);
 const hasLimit=scan=>!!scan?.limit_box;
 const hasFilters=scan=>scan?.isolated||scan?.hidden_regions>0;
 const selectedArea=()=>captured?.source===el('source').value?captured.selection:null;
 function setScopes(){
  const scan=source(),options=[];
  if(selectedArea())options.push(new Option('Selected points','selection'));
  if(hasLimit(scan))options.push(new Option('Saved limit box','limit'));
  else if(hasFilters(scan))options.push(new Option('Current view filters','view'));
  options.push(new Option('Whole retained cloud','whole'));
  el('scope').replaceChildren(...options);
 }
 function controls(){const lock=running||!!busy;
  for(const button of el('fits').querySelectorAll('button'))button.disabled=lock;for(const id of ['source','axis','length','inward','outward','scope'])el(id).disabled=lock;
  const scope=el('scope').value;
  el('run').textContent=scope==='selection'?'Preview selected points':scope==='limit'?'Preview inside limit box':scope==='view'?'Preview with view filters':'Preview whole cloud';
  el('scope-note').textContent=scope==='selection'?'Uses the highlighted points within the saved limit box if present.':scope==='limit'?'Uses the saved box even when the whole scan is visible.':scope==='view'?'Uses retained points inside the active view restrictions.':'Processes all retained points in the scan.';
  el('run').disabled=lock||!el('source').value;el('stop').hidden=!running;
  for(const id of ['inward-select','outward-select','both','none'])el(id).disabled=lock||!result||dirty||result.stale||!result.statistics.candidate_points;
  el('remove').disabled=el('separate').disabled=lock||dirty||!result||result.stale||!result.statistics.selected_points;
  el('previous').disabled=lock||!result||result.page===0;el('next').disabled=lock||!result||(result.page+1)*40>=result.group_count;
  el('fit-prev').disabled=lock||fitPage===0;el('fit-next').disabled=lock||!result||(fitPage+1)*20>=result.circle_sections.length;
  el('reverse').disabled=el('clear-guide').disabled=lock||viewIndex===null;
  el('groups').querySelectorAll('input').forEach(x=>x.disabled=lock||dirty||result?.stale);
 }
 function changed(){dirty=true;controls();if(result)status('Settings changed. Preview again before applying an edit.');}
 function render(){el('review').hidden=!result;if(!result){controls();return;}const s=result.statistics;
  const area={selection:'Selected points',limit_box:'Limit box',view:'Filtered view',whole:'Whole cloud'}[result.search_area]||'Processed area';
  const total=s.source_points?` of ${count(s.source_points)} source rows`:'';
  const extent=window.AnalysisArea.extent(s.effective_bounds);
  el('extent').textContent=`${area} · ${count(s.input_points)} retained points${total}${extent?' · '+extent:''}`;
  el('summary').textContent=s.has_supported_span===false
   ?`All ${count(s.input_points)} points retained · 0 selected. ${count(s.supported_windows)}/${count(s.fit_windows)} windows passed. Two neighbouring fits are needed.`
   :`${count(s.within_points)} within band · ${count(s.inward_points)} inward · ${count(s.outward_points)} outward · ${count(s.unassessed_points)} unassessed and retained. ${count(s.supported_windows)}/${count(s.fit_windows)} fitting windows supported. ${count(s.selected_points)} points selected.`;
  el('warnings').innerHTML=[...(result.stale?['Source changed. Preview again before further edits.']:[]),...result.warnings].map(x=>`<p>${escape(x)}</p>`).join('');
  el('groups').innerHTML=result.groups.map(g=>`<label class="feature-group"><input type="checkbox" data-circle-group="${g.id}" ${g.selected?'checked':''}><span><strong>${escape(g.name)}</strong><small>${count(g.points)} original points${g.partial?' · '+count(g.selected_points)+' selected':''}</small></span></label>`).join('');
  for(const input of el('groups').querySelectorAll('input'))input.indeterminate=!!result.groups.find(g=>g.id===+input.dataset.circleGroup)?.partial;
  const rows=result.circle_sections.slice(fitPage*20,(fitPage+1)*20);
  el('fits').innerHTML=rows.map((q,i)=>{
   const fitted=q.rms_mm!==null;
   const measures=fitted?`RMS ${q.rms_mm.toFixed(2)} mm · ${q.coverage_deg.toFixed(0)}° coverage · ${(q.inlier_fraction*100).toFixed(1)}% support. `:'';
   return `<div style="padding:8px 0;border-bottom:1px solid var(--ts-theme-border,#b9cccf)"><strong>${sectionLabel(q)}${fitted?' · R '+q.radius_m.toFixed(4)+' m':''}</strong><small>${measures}${escape(q.reason)}</small><button data-circle-view="${fitPage*20+i}">View section</button></div>`;
  }).join('');
  el('fit-page').textContent=`${fitPage*20+1}–${Math.min((fitPage+1)*20,result.circle_sections.length)} of ${result.circle_sections.length} overlapping fits`;
  el('page').textContent=result.group_count?`${result.page*40+1}–${Math.min((result.page+1)*40,result.group_count)} of ${count(result.group_count)} departure groups`:'No supported departures at these settings. No source points are selected.';controls();
 }
 function show(){
  if(!result||result.stale)return;
  let p=result.preview;
  const q=viewIndex===null?null:result.circle_sections[viewIndex];
  if(q){
   const c=q.centre.map((v,i)=>v-p.origin[i]),[nx,ny,nz]=q.normal;
   const points=p.points.filter((point,i)=>{
    if(p.stations&&Math.abs(p.stations[i]-q.station_m)>result.settings.section_m/2)return false;
    return Math.abs(nx*(point[0]-c[0])+ny*(point[1]-c[1])+nz*(point[2]-c[2]))<=sliceM/2;
   });
   p={...p,points,preview_count:points.length};
  }
  window.JobUI.analysisPreview({...p,result_id:result.id,sample_count:p.preview_count,used:result.statistics.input_points},'circle');
  el('fit-view').textContent=q
   ?`${sectionLabel(q)} · ${sliceM.toFixed(2)} m slice · ${q.supported?'supported fit':'unassessed section'} · ${count(p.preview_count)} displayed points`
   :'Choose a section to see its measured slice and fitted circle.';
  $('job-preview-info').textContent=`LOCAL-CIRCLE REVIEW · ${count(result.statistics.input_points)} checked · ${count(result.statistics.selected_points)} selected · green within band / yellow unassessed / cyan candidate / orange selected.`;
 }
 function viewCircle(index){
  if(!result||result.stale)return;
  const q=result.circle_sections[index];if(!q)return;
  viewIndex=index;show();
  const normal=q.normal.map(x=>x*(reverse?-1:1)),scene=window.JobUI.cadScene(),origin=window.JobUI.cadOrigin();
  window.CadUI?.releaseViewLock();
  window.JobUI.faceWorkPlane(normal);
  scene.target=q.centre.map((x,j)=>x-origin[j]);scene.scale=q.radius_m*2.1;
  const rows=q.rms_mm===null?[]:Array.from({length:129},(_,i)=>{
   const a=i*2*Math.PI/128;
   return [i,...q.centre.map((x,j)=>x+q.radius_m*(Math.cos(a)*q.right[j]+Math.sin(a)*q.up[j])),q.radius_m];
  });
  window.JobUI.geometryOverlay(rows);controls();scene.draw();
 }
 async function refresh(page=0){const next=await api('/api/shape-selection?'+new URLSearchParams({page}));result=next.result?.mode==='circle'?next.result:null;render();}
 function settings(){for(const id of ['length','inward','outward'])if(!el(id).checkValidity())throw Error('Check '+el(id).closest('label').textContent.trim());return {section_m:+el('length').value,inward_mm:+el('inward').value,outward_mm:+el('outward').value,axis_source:el('axis').value,respect_view:el('scope').value!=='whole'};}
 async function run(action,extra={}){if(running||busy)return;try{let body;
  if(action==='preview'){
   const scope=el('scope').value,selection=scope==='selection'?selectedArea():null;
   if(scope==='selection'&&!selection)throw Error('Select points and reopen Local circles.');
   if(scope==='limit'&&!hasLimit(source()))throw Error('Set a limit box and reopen Local circles.');
   if(scope==='view'&&!hasFilters(source()))throw Error('Set a limit box or choose Whole retained cloud.');
   viewIndex=null;fitPage=0;window.JobUI.geometryOverlay([]);body={action,mode:'circle',source_id:el('source').value,scope,settings:settings(),selection};
  }else{if(!result||dirty||result.stale)throw Error('Preview the current source and settings again.');body={action,preview_id:result.id,revision:result.revision,...extra};}
  running=true;setBusy(true);controls();status('Working on full-density original points…');await post('/api/shape-selection',body);const job=await window.JobUI.waitReady(value=>status(value.message||'Working…'));if(job.cancelled)throw Error('Stopped. Source points and the previous completed review are unchanged.');await refresh(action==='preview'?0:result?.page||0);dirty=false;
  if(['delete','separate'].includes(action)){window.JobUI.geometryOverlay([]);window.JobUI.clearCleaningPreview('circle');await window.JobUI.loadPreview();status(`${count(result?.last_edit?.count)} points ${action==='separate'?'separated':'removed'}. Undo reverses the edit.`);}else{show();
   const unsupported=result.statistics.has_supported_span===false;
   if(action==='preview'&&unsupported)el('fit-details').open=true;
   status(unsupported?'All points retained. Inspect the fitting windows below.':'Inspect the circles and the proposed departures.');
  }
 }catch(error){status(error.message,true);}finally{running=false;setBusy(false);render();}}
 async function open(){
  if(running||busy)return;
  const scans=window.JobUI.scanItems().filter(x=>x.visible!==false),selected=window.JobUI.selectedItem();
  const selection=window.JobUI.currentSelection();
  captured=selected?.kind==='scan'&&selection?{source:selected.id,selection}:null;
  el('source').replaceChildren(...scans.map(scan=>new Option(scan.name,scan.id)));
  if(selected?.kind==='scan')el('source').value=selected.id;
  setScopes();result=null;dirty=true;fitPage=0;viewIndex=null;reverse=false;
  window.JobUI.clearCleaningPreview();window.TunnelRibbon.view('circle-clean');status('');render();
  const setDirection=available=>{
   el('axis').querySelector('[value=project]').disabled=!available;
   el('axis').value=available?(preferredDirection||'project'):'auto';
  };
  setDirection(!!state?.axis);
  try{const data=await api('/api/shape-selection');setDirection(data.axis_available);}catch{}
 }
 el('run').onclick=()=>run('preview');el('stop').onclick=async()=>{status('Stopping at the next fitting or source-read checkpoint…');try{await post('/api/cancel-operation',{});}catch(e){status(e.message,true);}};
 el('both').onclick=()=>run('select',{operation:'all'});el('none').onclick=()=>run('select',{operation:'none'});for(const side of ['inward','outward'])el(side+'-select').onclick=()=>run('select',{operation:'side',side});
 el('groups').onchange=e=>{if(e.target.dataset.circleGroup)run('select',{operation:'toggle',group_ids:[+e.target.dataset.circleGroup],selected:e.target.checked});};
 el('fits').onclick=e=>{const button=e.target.closest('[data-circle-view]');if(button)viewCircle(+button.dataset.circleView);};
 el('reverse').onclick=()=>{reverse=!reverse;if(viewIndex!==null)viewCircle(viewIndex);};el('clear-guide').onclick=()=>window.JobUI.geometryOverlay([]);el('whole').onclick=()=>{viewIndex=null;window.JobUI.geometryOverlay([]);show();window.JobUI.cadScene().fit();};
 for(const [id,d] of [['fit-prev',-1],['fit-next',1]])el(id).onclick=()=>{fitPage+=d;render();};
 for(const [id,d] of [['previous',-1],['next',1]])el(id).onclick=async()=>{try{await refresh(result.page+d);}catch(e){status(e.message,true);}};
 
 el('remove').onclick=()=>{if(!el('remove').disabled)showTunnelMessage('Remove reviewed circle departures',`Remove ${count(result.statistics.selected_points)} selected original points? Inspect fitted circles first. Undo remains available.`,true,'Remove selected points',()=>run('delete',{confirmed:true}));};
 el('separate').onclick=()=>{if(!el('separate').disabled)run('separate',{confirmed:true});};el('normal').onclick=()=>{window.JobUI.geometryOverlay([]);window.JobUI.clearCleaningPreview('circle');};
 for(const id of ['length','inward','outward','axis','scope'])el(id).addEventListener('input',changed);
 el('axis').onchange=()=>{preferredDirection=el('axis').value;};
 el('source').onchange=()=>{setScopes();result=null;dirty=true;status('');render();};
 window.CircleCleanUI={open,busy:controls,result:()=>result,removeSelected:()=>el('remove').click(),removableCount:()=>!dirty&&!result?.stale?result?.statistics.selected_points||0:0,onToolClose:()=>{if(!running){window.JobUI.geometryOverlay([]);window.JobUI.clearCleaningPreview('circle');}}};
})();
