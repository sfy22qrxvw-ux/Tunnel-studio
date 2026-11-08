/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 let source=null,estimate=null,pending=false;
 const dialog=document.createElement('dialog');dialog.id='mesh-to-cloud-dialog';
 dialog.style.cssText='width:min(610px,94vw);max-height:90vh;overflow:auto';
 dialog.innerHTML=`<div class="dialog-heading"><h2>Mesh to Point Cloud</h2><button id="mesh-cloud-close" aria-label="Close">×</button></div>
 <p id="mesh-cloud-source"></p><p>Sample across triangle surfaces to create a separate cloud. Coordinates use the saved mesh frame. The mesh stays in the scene.</p>
 <label>Cloud name<input id="mesh-cloud-name" maxlength="150"></label>
 <label>Sampling control<select id="mesh-cloud-mode"><option value="spacing">Average spacing</option><option value="count">Requested point count</option></select></label>
 <div class="surface-pair"><label id="mesh-cloud-spacing-label">Average spacing · mesh units<input id="mesh-cloud-spacing" type="number" min="0.000001" step="any" value="0.05"></label>
 <label id="mesh-cloud-count-label" hidden>Point count<input id="mesh-cloud-count" type="number" min="1" max="9007199254740991" step="1" value="100000"></label></div>
 <p class="tiny">Average density is calculated from surface area ÷ spacing². Samples are spread by triangle area; a minimum distance between individual points is not enforced. Mesh coordinates and units are preserved.</p>
 <p class="tiny">Only recorded mesh vertex colours are interpolated. No scan intensity is created. The result is labelled as sampled geometry, not measured scan points.</p>
 <div class="surface-actions"><button id="mesh-cloud-estimate">Estimate size</button><button id="mesh-cloud-create" class="primary" disabled>Create sampled cloud</button><button id="mesh-cloud-cancel" hidden>Cancel operation</button></div>
 <p id="mesh-cloud-estimate-info" role="status">Estimate the point count and storage before creating the cloud.</p><p id="mesh-cloud-progress" role="status"></p>`;
 document.body.append(dialog);
 const controlIds=['mesh-cloud-name','mesh-cloud-mode','mesh-cloud-spacing','mesh-cloud-count'];
 function message(text,error=false){$('mesh-cloud-progress').textContent=text;$('mesh-cloud-progress').classList.toggle('error',error);}
 function controls(){
  const spacing=$('mesh-cloud-mode').value==='spacing';
  $('mesh-cloud-spacing-label').hidden=!spacing;$('mesh-cloud-count-label').hidden=spacing;
  for(const id of controlIds)$(id).disabled=pending;
  $('mesh-cloud-estimate').disabled=pending;$('mesh-cloud-create').disabled=pending||!estimate;
  $('mesh-cloud-cancel').hidden=!pending;
 }
 function invalidate(){estimate=null;$('mesh-cloud-estimate-info').textContent='Estimate these settings before creating the cloud.';controls();}
 function settings(){
  const mode=$('mesh-cloud-mode').value,id=mode==='spacing'?'mesh-cloud-spacing':'mesh-cloud-count';
  if(!$(id).checkValidity())throw Error(mode==='spacing'?'Enter a positive average spacing.':'Enter a positive whole point count.');
  const value=+$(id).value;
  if(!Number.isFinite(value)||value<=0||mode==='count'&&!Number.isSafeInteger(value))throw Error('Enter a valid '+(mode==='spacing'?'average spacing.':'whole point count.'));
  return mode==='spacing'?{mode,spacing:value}:{mode,count:value};
 }
 function open(id){
  if(busy||pending)return;
  const item=window.JobUI?.inventory().items.find(x=>x.id===(id||window.JobUI.selectedItem()?.id));
  if(!item||item.kind!=='mesh')return notice('Select a triangle mesh first.',true);
  source={id:item.id,name:item.name};estimate=null;
  $('mesh-cloud-source').textContent=item.name+' · '+fmt(item.triangles||0,0)+' triangles';
  $('mesh-cloud-name').value=(item.name+' · sampled cloud').slice(0,150);
  $('mesh-cloud-estimate-info').textContent='Estimate the point count and storage before creating the cloud.';
  message('');controls();if(!dialog.open)dialog.show();
 }
 async function run(create){
  if(pending||busy||!source)return;
  try{
   const options=settings();if(create&&!estimate)throw Error('Estimate the cloud first.');
   pending=true;controls();setBusy(true);message(create?'Creating sampled cloud…':'Measuring mesh surface…');
   await post('/api/job-action',{action:create?'mesh_to_cloud':'mesh_to_cloud_estimate',id:source.id,...options,
     ...(create?{estimate_revision:estimate.revision,name:$('mesh-cloud-name').value.trim()||source.name+' · sampled cloud'}:{})});
   const job=await window.JobUI.waitReady(value=>message(value.message||'Processing mesh…'));
   if(job.cancelled)throw Error('Operation cancelled. The source mesh is unchanged.');
   const result=await api('/api/mesh-to-cloud-estimate');
   if(result.source_id!==source.id)throw Error('The mesh selection changed. Reopen Mesh to Point Cloud.');
   if(create){
    estimate=null;
    if(result.created_item_id){window.JobUI.chooseItem(result.created_item_id);await window.JobUI.loadPreview();}
    dialog.close();notice(fmt(result.created_points,0)+' generated points added as a separate sampled cloud.');
   }else{
    estimate=result;
    $('mesh-cloud-estimate-info').textContent=fmt(result.count,0)+' generated points · about '+fmt(result.estimated_cache_bytes/1024**2,1)+' MiB of cache · '+fmt(result.surface_area,3)+' square mesh units. '+result.degenerate_triangles_skipped+' zero-area triangles skipped. '+(result.colour_available?'Available vertex RGB will be interpolated.':'No source vertex RGB is available.');
    message('Ready to create the sampled cloud.');
   }
  }catch(error){message(error.message,true);if(!create)estimate=null;}
  finally{pending=false;setBusy(false);controls();}
 }
 async function cancel(){if(!pending)return;try{await post('/api/job-action',{action:'cancel'});message('Cancelling; incomplete sampled files will be discarded…');}catch(error){message(error.message,true);}}
 $('mesh-cloud-mode').onchange=invalidate;$('mesh-cloud-spacing').oninput=invalidate;$('mesh-cloud-count').oninput=invalidate;
 $('mesh-cloud-estimate').onclick=()=>run(false);$('mesh-cloud-create').onclick=()=>run(true);$('mesh-cloud-cancel').onclick=cancel;
 $('mesh-cloud-close').onclick=()=>dialog.close();
 dialog.addEventListener('cancel',()=>{if(pending)cancel();});
 window.MeshToCloudUI={open};
})();
