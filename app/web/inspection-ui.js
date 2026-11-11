/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(() => {
 const modes = {
  cloud_mesh: {label:'Cloud to mesh', source:'Point cloud', reference:'Reference mesh', help:'Measure the gap from each retained scan point to the nearest triangle on the reference mesh.'},
  cloud_cloud: {label:'Cloud to cloud', source:'Point cloud', reference:'Reference cloud', help:'Measure each retained scan point against the nearest point in another cloud. Point spacing affects this comparison.'},
  cloud_cad: {label:'Cloud to CAD', source:'Point cloud', reference:'CAD model', help:'Compare the source cloud’s retained points with the CAD model. The heatmap covers the scanned portion.'},
  cloud_geometry: {label:'Cloud to geometry', source:'Point cloud', reference:'Reference geometry', help:'Compare retained scan points with a supported reference shape. The reference description explains which surface is used.'},
  mesh_mesh: {label:'Mesh to mesh', source:'Mesh to inspect', reference:'Reference mesh', help:'Sample the inspected mesh surface and measure its distance to the reference mesh. Results describe those area-weighted samples.'},
  mesh_cad: {label:'Mesh to CAD', source:'Mesh to inspect', reference:'CAD model', help:'Compare samples from the inspected mesh surface with the supported CAD surface. Results describe the chosen sampling spacing.'}
 };
 const byId = id => document.getElementById(id);
 const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const number = (value, digits=0) => Number.isFinite(Number(value)) && value !== null && value !== undefined ? Number(value).toLocaleString('en-GB',{maximumFractionDigits:digits,minimumFractionDigits:digits}) : '—';
 const first = (object,...keys) => {for(const key of keys)if(object?.[key]!==undefined&&object[key]!==null)return object[key];return null;};
 const panel = document.createElement('section');
 panel.id='tab-inspection';panel.className='tab inspection-panel';
 panel.innerHTML=`
  <div class="inspection-heading"><span class="inspection-eyebrow">COMPARE / INSPECT</span><h2>Compare objects</h2><p>Choose a scan, whole registered bundle or whole project, then its comparison reference. Every retained source point is evaluated; only the 3D preview is sampled.</p></div>
  <div class="inspection-form">
   <label class="inspection-wide">Comparison<select id="inspection-mode">${Object.entries(modes).map(([id,m])=>`<option value="${id}">${m.label}</option>`).join('')}</select></label>
   <p id="inspection-mode-help" class="inspection-help inspection-wide"></p>
   <label class="inspection-wide"><span>Object to inspect</span><select id="inspection-source" title="The cloud or mesh whose deviations will be measured. Comparisons run from this object towards the reference."></select><small id="inspection-source-info"></small></label>
   <label class="inspection-wide"><span>Reference · compare against</span><select id="inspection-reference" title="The object treated as the reference. This comparison does not move or register either object."></select><small id="inspection-reference-info"></small></label>
   <label>Tolerance <span class="inspection-unit">mm</span><input id="inspection-tolerance" type="number" min="0.001" step="1" value="25" title="Distances within this value count as within tolerance. This changes the assessment threshold, not the measured coordinates or scan accuracy."><small>Accepted distance from the reference.</small></label>
   <label>Distance direction<select id="inspection-sign" title="Unsigned reports the size of the gap. Oriented distance also reports its direction along the reference surface normals; check the orientation before interpreting the sign."><option value="unsigned">Unsigned · gap size</option><option value="oriented">Oriented · reference normals</option></select><small id="inspection-sign-info"></small></label>
   <label id="inspection-spacing-label" class="inspection-wide" hidden>Mesh sampling spacing <span class="inspection-unit">mm</span><input id="inspection-spacing" type="number" min="0.1" step="5" value="50" title="Approximate spacing of samples on the inspected mesh. Smaller spacing checks finer detail and takes longer. Statistics are area-weighted samples, not an exact maximum over every location on the surface."><small>Smaller spacing checks finer detail; more samples take longer.</small></label>
  </div>
  <details class="inspection-advanced"><summary>Advanced settings</summary><label>Exclude distances above <span class="inspection-unit">m</span><input id="inspection-band" type="number" min="0" step="0.05" value="0" title="Zero includes every valid distance. A positive value excludes larger absolute distances from the statistics. Excluded counts remain visible; review them before accepting the result."><small>0 = no exclusion. Excluded samples remain counted in the result.</small></label></details>
  <p class="inspection-coordinate-note">Both objects must already use the same coordinates and metre units. This tool measures differences; it does not align the objects.</p>
  <div class="inspection-actions"><button id="inspection-run" class="primary" type="button">Run comparison</button><button id="inspection-cancel" type="button" hidden>Stop</button></div>
  <progress id="inspection-progress" max="100" hidden aria-label="Comparison progress"></progress>
  <p id="inspection-status" class="inspection-status" role="status" aria-live="polite">Choose an object and its reference.</p>
  <section id="inspection-result" class="inspection-result" hidden>
   <div class="inspection-result-heading"><span class="inspection-eyebrow">LAST RESULT</span><h3 id="inspection-result-title"></h3><p id="inspection-result-context"></p></div>
   <p id="inspection-result-state" class="inspection-help"></p>
   <div id="inspection-summary"></div>
   <p id="inspection-sampling-note" class="inspection-result-note"></p>
   <details id="inspection-method-details" class="inspection-method-details"><summary>Method and sampling details</summary><p id="inspection-method-note" class="inspection-result-note"></p></details>
   <ul id="inspection-warnings" class="inspection-warnings" hidden></ul>
   <details class="inspection-display" open><summary>3D heatmap</summary><div class="inspection-form"><label>Colours<select id="inspection-colouring" title="Colouring changes only the displayed heatmap. The saved comparison statistics do not change."><option value="ramp">Continuous colour ramp</option><option value="tolerance">Tolerance bands</option></select></label><label>Colour scale <span class="inspection-unit">mm</span><input id="inspection-colour-scale" type="number" min="0.001" step="1" value="25" title="Display-only colour range. In continuous mode, distances at or beyond this value use the end colour. This does not change the calculation tolerance."></label></div><div class="inspection-actions"><button id="inspection-show" type="button">Show heatmap</button><button id="inspection-restore" type="button">Restore colours</button></div><p id="inspection-preview-info" class="inspection-help"></p></details>
   <a id="inspection-csv" class="inspection-download" href="/api/inspection.csv" download>Download result CSV</a>
  </section>`;
 document.querySelector('.view-shell').append(panel);
 const legend=document.createElement('div');legend.id='inspection-legend';legend.className='inspection-legend';legend.hidden=true;legend.setAttribute('aria-label','Comparison colour legend');
 byId('job-canvas').parentElement.append(legend);
 let options={sources:[],references:[]},result=null,preview=null,displayedId=null,running=false,stopping=false,loading=null,showing=false,preferredSource=null,lastResultId=null;
 const controls=['inspection-mode','inspection-source','inspection-reference','inspection-tolerance','inspection-sign','inspection-spacing','inspection-band'];
 const isBusy=()=>running||!!(typeof busy!=='undefined'&&busy);
 const mode=()=>byId('inspection-mode').value;
 const meshSource=()=>mode().startsWith('mesh_');
 const matching=item=>Array.isArray(item.modes)?item.modes.includes(mode()):true;
 const source=()=>options.sources.find(x=>String(x.id)===byId('inspection-source').value);
 const reference=()=>options.references.find(x=>String(x.id)===byId('inspection-reference').value);
 const status=(text,error=false)=>{byId('inspection-status').textContent=text;byId('inspection-status').classList.toggle('error',error);};
 function prepareView(){
  if(window.CadUI?.modelSpace?.()&&window.CadUI.enter('Analyse',{openTool:false})===false){
   const message='Finish or cancel the active modelling command before opening a comparison.';
   status(message,true);window.WorkStatus?.show('Comparison needs the Analyse workspace',message,false,true);return false;
  }
  return true;
 }
 function dismissFinishedStatus(){const box=byId('job-work-status');if(box&&!box.classList.contains('running')&&!box.classList.contains('error'))box.hidden=true;}
 function placeLegend(){
  if(legend.hidden)return;
  const host=legend.parentElement.getBoundingClientRect(),pad=12;let left=pad,right=host.width-pad;
  const drawer=byId('tool-drawer');
  if(drawer&&!drawer.hidden){
   const d=drawer.getBoundingClientRect();
   if(d.bottom>host.top+pad&&d.top<host.bottom&&d.right>host.left&&d.left<host.right){
    const before=d.left-host.left-pad,after=d.right-host.left+pad;
    if(before-left>=right-after)right=Math.min(right,before);else left=Math.max(left,after);
   }
  }
  const width=Math.max(1,Math.min(270,right-left));let height=Math.max(60,host.height-64);
  const work=byId('job-work-status');
  if(work&&!work.hidden){const r=work.getBoundingClientRect();if(r.left<host.left+left+width&&r.right>host.left+left&&r.top>host.top+pad+60)height=Math.min(height,r.top-host.top-pad-8);}
  legend.style.left=left+'px';legend.style.top=pad+'px';legend.style.bottom='auto';legend.style.width=width+'px';legend.style.maxHeight=height+'px';legend.classList.toggle('compact',width<220);
 }
 let legendLayoutPending=false;
 function scheduleLegendLayout(){if(legendLayoutPending)return;legendLayoutPending=true;requestAnimationFrame(()=>{legendLayoutPending=false;placeLegend();});}
 window.addEventListener('resize',scheduleLegendLayout);
 if(window.ResizeObserver){const observer=new ResizeObserver(scheduleLegendLayout);observer.observe(legend.parentElement);if(byId('tool-drawer'))observer.observe(byId('tool-drawer'));}
 if(window.MutationObserver){const observer=new MutationObserver(scheduleLegendLayout);for(const id of ['tool-drawer','job-work-status'])if(byId(id))observer.observe(byId(id),{attributes:true,attributeFilter:['hidden','class','style']});}
 function fill(id,items,placeholder,preferred){
  const select=byId(id),old=select.value;
  select.innerHTML=items.length?items.map(x=>`<option value="${escape(x.id)}">${escape(x.name||x.id)}</option>`).join(''):`<option value="">${escape(placeholder)}</option>`;
  if(items.some(x=>String(x.id)===String(preferred)))select.value=String(preferred);
  else if(items.some(x=>String(x.id)===old))select.value=old;
 }
 function description(item){
  if(!item)return '';
  const parts=[item.representation||item.kind];
  if(Number.isFinite(item.count))parts.push(number(item.count)+' points');
  if(Number.isFinite(item.triangles))parts.push(number(item.triangles)+' triangles');
  if(item.note)parts.push(item.note);
  return parts.filter(Boolean).join(' · ');
 }
 function choices(){
  const definition=modes[mode()]||modes.cloud_mesh;
  fill('inspection-source',options.sources.filter(matching),'Import an appropriate '+(meshSource()?'mesh':'cloud')+' first',preferredSource);preferredSource=null;
  fill('inspection-reference',options.references.filter(x=>matching(x)&&String(x.id)!==byId('inspection-source').value&&!(mode()==='cloud_cloud'&&(x.source_scan_ids||[]).some(id=>(source()?.source_scan_ids||[]).includes(id)))),'No compatible reference available');
  byId('inspection-mode-help').textContent=definition.help;
  byId('inspection-source-info').textContent=description(source());
  byId('inspection-reference-info').textContent=description(reference());
  byId('inspection-spacing-label').hidden=!meshSource();
  const oriented=reference()?.signed_supported===true;
  byId('inspection-sign').options[1].disabled=!oriented;
  if(!oriented)byId('inspection-sign').value='unsigned';
  byId('inspection-sign-info').textContent=oriented?'Oriented signs follow the reference normals; verify their direction.':'This reference supports unsigned distances.';
 }
 function request(){
  const value={mode:mode(),source_id:byId('inspection-source').value,reference_id:byId('inspection-reference').value,tolerance_mm:Number(byId('inspection-tolerance').value),band_m:Number(byId('inspection-band').value),signed:byId('inspection-sign').value==='oriented',spacing_m:Number(byId('inspection-spacing').value)/1000};
  if(!value.source_id||!value.reference_id)throw Error('Choose an object to inspect and a compatible reference.');
  if(value.source_id===value.reference_id)throw Error('Choose two different objects.');
  if(!Number.isFinite(value.tolerance_mm)||value.tolerance_mm<=0)throw Error('Enter a positive tolerance in millimetres.');
  if(!Number.isFinite(value.band_m)||value.band_m<0)throw Error('Enter a non-negative exclusion distance; use 0 for no exclusion.');
  if(meshSource()&&(!Number.isFinite(value.spacing_m)||value.spacing_m<=0))throw Error('Enter a positive mesh sampling spacing.');
  if(!meshSource())value.spacing_m=.05;
  return value;
 }
 function changedFromResult(){
  if(!result)return false;
  let r;try{r=request();}catch(_){return true;}
  return ['mode','source_id','reference_id','signed','tolerance_mm','band_m',...(meshSource()?['spacing_m']:[])].some(key=>result[key]!==undefined&&String(result[key])!==String(r[key]));
 }
 function render(){
  const blocked=isBusy();for(const id of controls)byId(id).disabled=blocked;
  byId('inspection-run').disabled=blocked||!source()||!reference();
  byId('inspection-cancel').hidden=!running;byId('inspection-cancel').disabled=stopping;
  byId('inspection-progress').hidden=!running;
  byId('inspection-result').hidden=!result;
  const ready=!!result&&!result.stale;
  byId('inspection-show').disabled=!ready||blocked||showing;
  byId('inspection-restore').disabled=!displayedId;
  byId('inspection-colouring').disabled=!ready||blocked;
  byId('inspection-colour-scale').disabled=!ready||blocked||byId('inspection-colouring').value!=='ramp';
  byId('inspection-csv').setAttribute('aria-disabled',String(!ready||blocked));
  byId('inspection-csv').tabIndex=ready&&!blocked?0:-1;
  if(!result)return;
  const signed=!!result.signed,used=first(result,'used','used_count'),evaluated=first(result,'evaluated','evaluated_count','input_points','source_count','total');
  byId('inspection-result-title').textContent=result.mode_label||modes[result.mode]?.label||'Comparison';
  byId('inspection-result-context').textContent=(result.source_name||result.source_id||'Inspected object')+' → '+(result.reference_name||result.reference_id||'Reference');
  byId('inspection-result-state').textContent=result.stale?(result.stale_reason||'The source or reference changed. Run the comparison again.'):changedFromResult()?'Settings changed. This result uses the previous settings; run again to update it.':'Tolerance '+number(result.tolerance_mm,2)+' mm · '+(signed?'oriented distances':'unsigned distances');
  byId('inspection-result-state').classList.toggle('error',!!result.stale);
  const measured=(result.mode||'').startsWith('mesh_')?'Mesh samples evaluated':'Source points evaluated';
  const rows=[
   [measured,number(evaluated)],['Used in statistics',number(used)],
   ['Within tolerance',number(result.within_tolerance)+(Number.isFinite(result.within_percent)?' · '+number(result.within_percent,1)+'%':'')],
   ['Excluded by distance band',number(first(result,'excluded_by_band','excluded_count'))],
   ['No valid reference / ambiguous sign',number(first(result,'outside_design_or_ambiguous','invalid_count','unassessed'))],
   [signed?'Mean signed deviation':'Mean distance',number(result.mean_mm,3)+' mm'],[signed?'Median signed deviation':'Median distance',number(result.median_mm,3)+' mm'],['RMS distance',number(result.rms_mm,3)+' mm'],
   ['Smallest / largest',number(result.min_mm,3)+' / '+number(result.max_mm,3)+' mm'],['95% within this absolute distance',number(result.p95_absolute_mm,3)+' mm']
  ];
  if((result.mode||'').startsWith('mesh_'))rows.splice(5,0,['Surface area evaluated',number(result.evaluated_area_m2,4)+' m²'],['Surface area included',number(result.included_area_m2,4)+' m²'],['Surface area within tolerance',number(result.within_area_m2,4)+' m²']);
  byId('inspection-summary').innerHTML='<dl class="inspection-statistics">'+rows.map(([label,value])=>`<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join('')+'</dl>';
  const notes=[result.note,result.statistics_note,result.sampling_note,result.quantiles_note];
  let summary=(result.mode||'').startsWith('mesh_')?'Mesh statistics describe area-weighted surface samples'+(Number.isFinite(result.spacing_m)?' at '+number(result.spacing_m*1000,2)+' mm spacing':'')+'. They do not establish the exact maximum over the continuous surface.':'Statistics use retained source points with valid included distances. The 3D preview can show fewer points.';
  if(signed)summary+=' Oriented signs follow the reference normals.';
  byId('inspection-sampling-note').textContent=summary;
  byId('inspection-method-note').textContent=[...new Set(notes.filter(Boolean))].join(' ');byId('inspection-method-details').hidden=!notes.some(Boolean);
  const warnings=Array.isArray(result.warnings)?result.warnings:[];byId('inspection-warnings').hidden=!warnings.length;
  byId('inspection-warnings').innerHTML=warnings.map(x=>'<li>'+escape(x)+'</li>').join('');
  byId('inspection-csv').href='/api/inspection.csv'+(result.id?'?id='+encodeURIComponent(result.id):'');
 }
 async function sync(force=false){
  if(loading)return loading;
  if(!force&&(running||isBusy()||(!panel.classList.contains('drawer-active')&&!displayedId)))return;
  loading=(async()=>{
   try{
    const [available,next]=await Promise.all([api('/api/inspection-options'),api('/api/inspection')]);
    options={...available,sources:available.sources||[],references:available.references||[]};
    const offered=Array.isArray(available.modes)?available.modes.map(x=>typeof x==='string'?x:x.id||x.mode):Object.keys(modes);
    for(const opt of byId('inspection-mode').options)opt.disabled=!offered.includes(opt.value);
    if(!offered.includes(mode())&&offered.some(x=>modes[x]))byId('inspection-mode').value=offered.find(x=>modes[x]);
    choices();result=next.result||null;
    window.JobUI?.invalidateAnalysis?.(result,'inspection');
    if(displayedId&&(!result||result.stale||displayedId!==result.id))restore();
    if(result?.id!==lastResultId){preview=null;lastResultId=result?.id||null;if(result)byId('inspection-colour-scale').value=result.tolerance_mm||25;}
    render();return true;
   }catch(error){status('Comparison tools could not refresh: '+error.message,true);return false;}
   finally{loading=null;}
  })();return loading;
 }
 function hideLegend(){const wasDisplayed=!!displayedId;legend.hidden=true;displayedId=null;if(byId('inspection-restore'))byId('inspection-restore').disabled=true;if(wasDisplayed)window.JobUI?.refreshTree();}
 function restore(){window.JobUI?.clearCleaningPreview?.('inspection');hideLegend();byId('inspection-preview-info').textContent='Normal object colours restored.';render();}
 function paint(){
  if(!preview||!result||result.stale)return;
  const ramp=byId('inspection-colouring').value==='ramp';
  const scale=ramp?Number(byId('inspection-colour-scale').value):Number(result.tolerance_mm);
  if(!Number.isFinite(scale)||scale<=0)throw Error('Enter a positive colour scale in millimetres.');
  const coloured=window.TunnelDisplayBuffers.recolour(preview,scale,ramp);
  window.JobUI?.analysisPreview({...coloured,source_id:result.source_id,result_id:result.id,signed:!!result.signed,mode_label:result.mode_label||modes[result.mode]?.label},'inspection');
  displayedId=result.id;legend.hidden=false;
  const signed=!!result.signed;
  legend.innerHTML=`<div class="inspection-legend-heading"><b>${escape(result.mode_label||modes[result.mode]?.label||'Comparison')} · mm</b><button type="button" title="Restore the original object colours">Restore</button></div>${ramp?'<div class="inspection-ramp '+(signed?'signed':'unsigned')+'"></div><div class="inspection-legend-ticks"><span>'+(signed?'−'+number(scale,1):'0')+'</span><span>'+(signed?'0':'Distance')+'</span><span>+'+number(scale,1)+'</span></div>':'<div class="inspection-legend-bands">'+(signed?'<span><i class="below"></i>Below −'+number(scale,1)+'</span>':'')+'<span><i class="within"></i>Within '+(signed?'±':'')+number(scale,1)+'</span><span><i class="above"></i>Above +'+number(scale,1)+'</span></div>'}<small>${signed?'Sign follows reference normals.':'Unsigned distance to the reference.'} ${ramp?'Display scale only.':'Comparison tolerance.'}</small>`;
  legend.querySelector('button').onclick=restore;
  const count=preview.sample_count??window.TunnelDisplayBuffers.packed(preview).length/6;
  byId('inspection-preview-info').textContent=number(count)+' displayed of '+number(first(result,'used','used_count'))+' compared '+((result.mode||'').startsWith('mesh_')?'mesh samples':'points')+'. The CSV and statistics use the comparison result, not the display sample.';
  dismissFinishedStatus();placeLegend();render();window.JobUI?.refreshTree();
 }
 async function show(){
  if(!result||result.stale||isBusy()||showing)return false;
  if(!prepareView())return false;
  showing=true;render();
  try{
   const id=result.id;const p=await window.TunnelDisplayBuffers.load('/api/inspection-preview'+(id?'?id='+encodeURIComponent(id):''));
   if(!(p.packed instanceof Float32Array)||!Array.isArray(p.origin)||p.origin.length!==3)throw Error('The comparison preview is incomplete.');
   if(result?.id!==id||(p.result_id&&p.result_id!==id))throw Error('The result changed while its preview was loading. Show the latest result again.');
   preview=p;paint();return true;
  }catch(error){status('Could not show the heatmap: '+error.message,true);return false;}
  finally{showing=false;render();}
 }
 async function run(){
  if(isBusy())return;
  let body;try{body=request();}catch(error){status(error.message,true);return;}
  const previousId=result?.id;
  running=true;stopping=false;byId('inspection-progress').removeAttribute('value');render();
  try{
   setBusy(true);status('Starting comparison…');window.WorkStatus?.show('Comparing objects…','Reading the selected source and reference.',true);
   const started=await post('/api/inspect',body);if(!started.started)throw Error('The comparison did not start.');
   const job=await window.JobUI.waitReady(j=>{
    if(j.busy){status(j.message||'Comparing…');window.WorkStatus?.show('Comparing objects…',j.message,true);}
   });
   if(job.cancelled){status('Comparison cancelled. Any previous result remains available.');window.WorkStatus?.show('Comparison cancelled','No object coordinates were changed.');}
   else{status('Comparison complete. Loading the result…');}
   running=false;stopping=false;const refreshed=await sync(true);
   if(!job.cancelled){if(!refreshed||!result||result.stale||result.id===previousId)throw Error('The comparison finished but its new result could not be loaded. Refresh Results to check the saved calculation.');const shown=await show();if(shown)status('Comparison complete. Review the heatmap and excluded counts.');window.WorkStatus?.show('Comparison ready',number(first(result,'used','used_count'))+' samples used in the statistics.'+(shown?'':' The heatmap could not be shown; use Show heatmap to retry.'));if(shown)dismissFinishedStatus();}
  }catch(error){setBusy(false);status('Comparison failed: '+error.message,true);window.WorkStatus?.show('Comparison failed',error.message,false,true);}
  finally{running=false;stopping=false;render();}
 }
 async function exportCSV(){
  if(!result||result.stale||isBusy())return;
  const id=result.id;running=true;stopping=false;byId('inspection-progress').removeAttribute('value');render();
  try{
   setBusy(true);status('Preparing the full comparison CSV…');window.WorkStatus?.show('Preparing result CSV…','Writing comparison rows with progress and cancellation.',true);
   const started=await post('/api/inspection-export',{id});if(!started.started)throw Error('The CSV export did not start.');
   const job=await window.JobUI.waitReady(j=>{if(j.busy){status(j.message||'Preparing CSV…');window.WorkStatus?.show('Preparing result CSV…',j.message,true);}});
   if(job.cancelled){status('CSV preparation cancelled. The comparison result remains available.');window.WorkStatus?.show('CSV preparation cancelled','The comparison result remains available.');return;}
   const link=document.createElement('a');link.href='/api/inspection.csv'+(id?'?id='+encodeURIComponent(id):'');link.download='Tunnel_Studio_inspection.csv';document.body.append(link);link.click();link.remove();
   status('CSV prepared and sent to the download handler.');window.WorkStatus?.show('Result CSV ready','The full prepared comparison file is ready to save.');if(displayedId)dismissFinishedStatus();
  }catch(error){setBusy(false);status('CSV preparation failed: '+error.message,true);window.WorkStatus?.show('CSV could not be prepared',error.message,false,true);}
  finally{running=false;stopping=false;render();}
 }
 byId('inspection-mode').onchange=()=>{choices();render();};
 byId('inspection-source').onchange=()=>{choices();render();};
 byId('inspection-reference').onchange=()=>{choices();render();};
 for(const id of ['inspection-tolerance','inspection-sign','inspection-spacing','inspection-band'])byId(id).onchange=render;
 byId('inspection-run').onclick=run;
 byId('inspection-cancel').onclick=async()=>{if(!running||stopping)return;stopping=true;render();try{await post('/api/cancel-operation',{});status('Stopping the current operation…');}catch(error){stopping=false;status('Could not request cancellation: '+error.message,true);render();}};
 byId('inspection-show').onclick=show;byId('inspection-restore').onclick=restore;
 for(const id of ['inspection-colouring','inspection-colour-scale'])byId(id).onchange=()=>{if(displayedId)try{paint();}catch(error){status(error.message,true);}render();};
 byId('inspection-csv').onclick=e=>{e.preventDefault();if(!result||result.stale||isBusy()){status('Run or finish a current comparison before downloading its result.',true);return;}void exportCSV();};
 async function open(selectedMode){
  if(!prepareView())return false;
  if(selectedMode&&modes[selectedMode])byId('inspection-mode').value=selectedMode;
  preferredSource=window.JobUI?.selectedItem?.()?.id||null;
  window.TunnelRibbon?.view('inspection',{refresh:false});
  const refreshed=await sync(true);render();return refreshed;
 }
 async function openResult(){
  if(!await open()||!result)return;
  if(modes[result.mode])byId('inspection-mode').value=result.mode;
  preferredSource=result.source_id;choices();
  if(options.references.some(x=>String(x.id)===String(result.reference_id)))byId('inspection-reference').value=result.reference_id;
  choices();
  for(const [id,key] of [['inspection-tolerance','tolerance_mm'],['inspection-band','band_m']])if(Number.isFinite(result[key]))byId(id).value=result[key];
  if(Number.isFinite(result.spacing_m))byId('inspection-spacing').value=result.spacing_m*1000;
  byId('inspection-sign').value=result.signed&&reference()?.signed_supported?'oriented':'unsigned';
  render();await show();
 }
 window.InspectionUI={open,openResult,sync,busy:render,show,restore,hideLegend,run,exportCSV,request,displayedResult:()=>displayedId,modes:()=>Object.keys(modes)};
 choices();render();
})();
