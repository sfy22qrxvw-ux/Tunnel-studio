/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const card=document.getElementById('mesh-result-card');
 if(!card)return;
 const el=id=>document.getElementById(id),number=n=>Number(n).toLocaleString('en-GB'),percent=n=>(100*Number(n)).toFixed(1)+'%';
 let shownId=null,lastKey='',changing=false;
 const inventory=()=>window.JobUI?.inventory?.()?.items||[];
 function resultItem(){
  const items=inventory(),selected=window.JobUI?.selectedItem?.();
  if(selected?.kind==='mesh')return items.find(x=>x.id===selected.id)||selected;
  return items.find(x=>x.id===shownId&&x.kind==='mesh')||items.filter(x=>x.kind==='mesh'&&x.mesh_summary?.creation_engine).at(-1)||null;
 }
 function metric(id,value){el(id).textContent=value;}
 function sync(){
  const item=resultItem();
  card.hidden=!item;
  if(!item){shownId=null;lastKey='';return;}
  const changedItem=shownId!==item.id;
  shownId=item.id;
  if(changedItem)requestAnimationFrame(()=>{
   if(card.getClientRects().length)card.scrollIntoView({block:'start',behavior:'smooth'});
  });
  const summary=item.mesh_summary||{},preview=window.JobUI?.meshPreviewState?.()||{state:'empty',items:[]};
  const display=(preview.items||[]).find(x=>x.id===item.id);
  const source=inventory().find(x=>x.id===summary.source_item_id&&x.kind==='scan');
  const key=JSON.stringify([item,display,preview.mode,preview.state,preview.error,source?.visible,changing,typeof busy!=='undefined'&&busy]);
  if(key===lastKey)return;
  lastKey=key;
  const blocked=changing||(typeof busy!=='undefined'&&busy);
  el('mesh-result-name').textContent=item.name;
  const status=summary.surface_status||'unknown';
  const labels={connected:'Joined triangle surface',multiple:'Separate surface pieces',fragmented:'Fragmented · review mesh',nonmanifold:'Surface needs review',unknown:'Triangle mesh'};
  el('mesh-result-state').textContent=labels[status]||labels.unknown;
  card.dataset.review=String(!!summary.review_required||['fragmented','nonmanifold'].includes(status));
  metric('mesh-result-triangles',number(item.triangles||summary.triangles||0));
  metric('mesh-result-pieces',Number.isFinite(summary.connected_components)?number(summary.connected_components):'—');
  metric('mesh-result-main-piece',Number.isFinite(summary.largest_component_area_fraction)?percent(summary.largest_component_area_fraction):'—');
  metric('mesh-result-support',Number.isFinite(summary.observed_cell_coverage_fraction)?percent(summary.observed_cell_coverage_fraction):'—');
  el('mesh-result-support-metric').hidden=!Number.isFinite(summary.observed_cell_coverage_fraction);
  el('mesh-result-source').textContent=source||summary.source_name?'Source: '+(source?.name||summary.source_name)+(summary.source_scope?' · '+summary.source_scope:''):'Shared vertices and triangle faces are saved with this model.';
  const reasons=(summary.review_reasons||[]).filter(x=>typeof x==='string');
  el('mesh-result-review').hidden=!reasons.length;
  el('mesh-result-review').textContent=reasons.join(' ');
  let description='';
  if(summary.method==='refine:fill_bolt_gaps')description=number(summary.filled_boundary_loops)+' bolt-sized gaps reconstructed · '+number(summary.estimated_bolt_triangles)+' new estimated triangles · '+number(summary.preserved_openings)+' openings preserved. The full mesh preview colours estimated faces orange.';
  if(summary.support_mode==='full_retained_grid'){
   description='All retained points in the chosen area contribute measured support.';
   if(summary.median_approximation)description+=' Each cell uses a bounded robust radius estimate.';
  }else if(summary.support_mode==='spatial_representatives')description='Measured representatives are spread through the selected area; working detail is managed automatically.';
  if(Number.isFinite(summary.spacing_m))description+=' Grid spacing '+number(Math.round(summary.spacing_m*1000))+' mm.';
  if(summary.boundary_edges>0)description+=' Open edges can be tunnel ends or unmeasured openings; they do not turn the mesh into points.';
  el('mesh-result-method').textContent=description;
  el('mesh-result-method').closest('details').hidden=!description;
  let viewText='';
  let failed=false;
  if(item.visible===false)viewText='This mesh is hidden. Show it here or use its bulb in the Project Explorer.';
  else if(display?.state==='error'){failed=true;viewText='Triangle preview unavailable: '+(display.error||'Use Retry preview.');}
  else if(display?.state==='ready')viewText=number(display.displayed_triangles)+' triangles displayed'+(display.simplified?' · simplified preview; all '+number(item.triangles||display.total_triangles)+' faces remain saved and exportable.':'.');
  else if(display?.state==='loading'||preview.state==='loading')viewText='Loading triangle faces…';
  else if(preview.state==='error'){failed=true;viewText='Triangle preview unavailable: '+(preview.error||'Use Retry preview.');}
  else viewText='Triangle display will refresh when this mesh is shown.';
  el('mesh-result-preview').textContent=viewText;
  el('mesh-result-preview').classList.toggle('mesh-result-error',failed);
  el('mesh-result-retry').hidden=!failed;
  el('mesh-result-retry').disabled=blocked;
  el('mesh-result-show').hidden=item.visible!==false;
  el('mesh-result-show').disabled=blocked;
  el('mesh-result-source-toggle').hidden=!source;
  el('mesh-result-source-toggle').textContent=source?.visible?'Hide source scan':'Show source scan';
  el('mesh-result-source-toggle').disabled=blocked;
  el('mesh-result-export').disabled=blocked;
  for(const button of card.querySelectorAll('[data-mesh-display]')){
   button.setAttribute('aria-pressed',String(button.dataset.meshDisplay===(preview.mode||'solid')));
   button.disabled=blocked;
  }
 }
 async function toggleVisibility(sourceOnly){
  if(changing||(typeof busy!=='undefined'&&busy))return;
  const mesh=resultItem();if(!mesh)return;
  const target=sourceOnly?inventory().find(x=>x.id===mesh.mesh_summary?.source_item_id&&x.kind==='scan'):mesh;
  if(!target)return;
  changing=true;sync();
  try{await window.JobUI.action({action:'visibility',id:target.id});}
  finally{changing=false;sync();}
 }
 for(const button of card.querySelectorAll('[data-mesh-display]'))button.addEventListener('click',()=>{window.JobUI?.setMeshDisplay?.(button.dataset.meshDisplay);sync();});
 el('mesh-result-retry').addEventListener('click',()=>window.JobUI?.retryMeshPreview?.());
 el('mesh-result-show').addEventListener('click',()=>toggleVisibility(false));
 el('mesh-result-source-toggle').addEventListener('click',()=>toggleVisibility(true));
 el('mesh-result-export').addEventListener('click',()=>{const item=resultItem();if(item)window.ExportUI?.openMesh(item.id);});
 window.addEventListener('tunnel-mesh-preview',sync);
 window.MeshResultsUI={sync};
 sync();
})();
