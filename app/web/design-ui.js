/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 let result=null,loading=false,showAfterRun=false,displayedId=null,previousResultId=null,pendingMeshIds=null;
 function choices(id,items){const el=$(id),old=el.value;if(!items.length){el.innerHTML='<option value="">'+(id==='design-reference'?'Import a design mesh first':'Import a scan first')+'</option>';return;}el.innerHTML=items.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');if(items.some(x=>x.id===old))el.value=old;}
 function modeUI(){const mode=$('design-mode').value;$('design-reference-label').hidden=mode==='alignment';$('design-help').textContent=mode==='alignment'?(state?.axis?.estimated?'Uses the estimated alignment and its node radii. The heatmap shows variation from that reference; it is not an independent design check.':state?.axis?`Design: ${state.axis.name}. Positive outside / negative inside the circular design.`:'Import a circular alignment CSV (chainage,E,N,H,radius) before this comparison. Importing an alignment starts a new design project; save the current project first.'):mode==='mesh_normal'?'Positive follows the nearest face normal; verify winding. Open-edge tangential signs are excluded. Requires Open3D.':'Unsigned distance to the full imported triangle mesh. Works with open design surfaces. Requires Open3D.';}
 function render(){const ready=!!result&&!result.stale;$('design-show').disabled=!ready||busy;$('design-export').disabled=!ready||busy;$('design-run').disabled=busy||!$('design-source').value||($('design-mode').value!=='alignment'&&!$('design-reference').value);$('design-import-mesh').disabled=busy;
  if(!result){$('design-result-state').textContent='No comparison yet.';$('design-summary').replaceChildren();return;}
  $('design-result-state').textContent=result.stale?result.stale_reason:`${result.source_name} → ${result.reference_name} · ${result.mode_label}`;
  const rows=[['Compared points',fmt(result.used,0)],['Within tolerance',`${fmt(result.within_tolerance,0)} (${fmt(result.within_percent,1)}%)`],['Below / above tolerance',`${fmt(result.below_tolerance,0)} / ${fmt(result.above_tolerance,0)}`],['Mean / RMS · mm',`${fmt(result.mean_mm,3)} / ${fmt(result.rms_mm,3)}`],['Minimum / maximum · mm',`${fmt(result.min_mm,3)} / ${fmt(result.max_mm,3)}`],['P95 absolute · mm',fmt(result.p95_absolute_mm,3)],['Excluded by comparison band',fmt(result.excluded_by_band,0)],['Outside design / ambiguous sign',fmt(result.outside_design_or_ambiguous,0)],['Tolerance / band',`${fmt(result.tolerance_mm,2)} mm / ${result.band_m?fmt(result.band_m,3)+' m':'off'}`]];
  $('design-summary').innerHTML=`<table>${rows.map(([k,v])=>`<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table><p class="tiny">${esc(result.note)} ${esc(result.statistics_note)}</p>`;
 }
 async function show(){if(!result||result.stale||busy)return;try{const p=await window.TunnelDisplayBuffers.load('/api/design-comparison-preview');window.JobUI?.analysisPreview(p);displayedId=result.id;}catch(e){window.JobUI?.clearCleaningPreview('design');displayedId=null;notice(e.message,true);}}
 async function sync(){if(loading||busy)return;loading=true;try{
  const scope=await api('/api/design-sources');choices('design-source',scope.sources||[]);const meshes=window.JobUI?.meshItems()||[];choices('design-reference',meshes);if(pendingMeshIds){const added=meshes.filter(x=>!pendingMeshIds.has(x.id));if(added.length){$('design-reference').value=added.at(-1).id;pendingMeshIds=null;}}modeUI();
  const next=await api('/api/design-comparison');result=next.result||null;window.JobUI?.invalidateAnalysis?.(result);
  if(displayedId&&(!result||result.stale||displayedId!==result.id)){window.JobUI?.clearCleaningPreview('design');displayedId=null;}
  render();if(showAfterRun&&state?.job?.error)showAfterRun=false;
  if(showAfterRun&&result&&!result.stale&&result.id!==previousResultId){showAfterRun=false;await show();}
 }catch(e){$('design-result-state').textContent=e.message;}finally{loading=false;}}
 $('design-run').onclick=async()=>{if(busy)return;try{
  if(!$('design-source').value)throw Error('Choose a source scan.');
  if($('design-mode').value!=='alignment'&&!$('design-reference').value)throw Error('Import an OBJ design mesh using Import design mesh, then choose it as the reference.');
  if($('design-mode').value==='alignment'&&!state?.axis)throw Error('Import the circular design alignment first, or choose a design mesh.');
  previousResultId=result?.id||null;setBusy(true);window.JobUI?.clearCleaningPreview('design');displayedId=null;
  await post('/api/design-compare',{source_id:$('design-source').value,mode:$('design-mode').value,reference_id:$('design-reference').value,tolerance_mm:Number($('design-tolerance').value),band_m:Number($('design-band').value)});
  showAfterRun=true;poll();
 }catch(e){showAfterRun=false;setBusy(false);notice(e.message,true);}};
 $('design-show').onclick=show;$('design-clear').onclick=()=>{window.JobUI?.clearCleaningPreview('design');displayedId=null;};
 $('design-export').onclick=()=>{if(result&&!result.stale&&!busy)location.href='/api/design-comparison.csv';};
 $('design-import-mesh').onclick=()=>{if(busy)return;pendingMeshIds=new Set((window.JobUI?.meshItems()||[]).map(x=>x.id));window.TunnelRibbon?.openImport('model');};
 $('design-mode').onchange=()=>{modeUI();render();};
 window.DesignUI={sync};sync();
})();
