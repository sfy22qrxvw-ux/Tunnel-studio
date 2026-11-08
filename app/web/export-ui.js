/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 let highlight=null,objectIds=null,meshIds=null;
 let pending=null,choosing=false,writing=false,lastSaved=null,failedId=null,focusPath=false;
 const message=(text,error=false)=>{$('project-dialog-message').textContent=text;$('project-dialog-message').classList.toggle('error',error);};
 const locked=()=>choosing||writing||!!pending;
 const fields=['export-source','export-format','export-scene','export-path','export-manual-path','export-deleted-context'];
 const request=()=>{const format=$('export-format').value,source=$('export-source').value;return {format,source,source_id:source==='scene'?$('export-scene').value:null,mesh_id:format==='obj'&&window.JobUI?.selectedItem?.()?.kind==='mesh'?window.JobUI.selectedItem().id:null,mesh_ids:format==='obj'?meshIds:null,...(source==='highlighted'?{highlight:highlight?.deleted_groups?{...highlight,include_deleted_context:$('export-deleted-context').checked}:highlight||window.JobUI?.highlightRequest?.()}:{}),...(source==='objects'?{object_ids:objectIds||[window.SceneObjectsUI?.selected()].filter(Boolean)}:{})};};
 function matches(item){const r=request();return item&&item.format===r.format&&item.source===r.source&&(r.source!=='scene'||item.source_id===r.source_id)&&(r.format!=='obj'||JSON.stringify(item.mesh_ids||[item.mesh_id])===JSON.stringify(r.mesh_ids||[r.mesh_id]))&&(r.source!=='highlighted'||JSON.stringify(item.highlight)===JSON.stringify(r.highlight))&&(r.source!=='objects'||JSON.stringify(item.object_ids)===JSON.stringify(r.object_ids));}
 function nameFor(r){let base=r.format==='obj'?(r.mesh_ids?.length>1?'Selected meshes':window.JobUI?.inventory?.().items.find(x=>x.id===(r.mesh_ids?.[0]||r.mesh_id))?.name):r.source==='highlighted'?(r.highlight?.deleted_groups?'Deleted points':'Highlighted points'):r.source==='objects'?'Selected geometry':r.source==='scene'?$('export-scene').selectedOptions[0]?.textContent:'Tunnel Studio export';base=(base||'Tunnel Studio export').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').replace(/[. ]+$/,'').slice(0,100);return base+(r.source==='scene'&&r.format!=='obj'?'_cleaned':r.source==='highlighted'?'_highlighted':'')+'.'+r.format;}
 function open(source){
  highlight=null;objectIds=null;meshIds=null;
  const scans=window.JobUI?.scanItems()||[],selected=window.JobUI?.selectedItem?.();
  $('export-scene').innerHTML=scans.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
  if(selected?.kind==='scan')$('export-scene').value=selected.id;
  if(!source&&scans.length){$('export-source').value='scene';$('export-format').value='e57';}
  pathMode(false);lastSaved=null;failedId=null;changed();
 }
 function changed(){
  const r=request(),points=!['tstudio','obj','json'].includes(r.format);
  $('project-dialog-intro').textContent=r.source==='highlighted'?(r.highlight?.deleted_groups?'Exports only the selected area from the captured deleted groups, including cleaning archives. Points remain deleted; no restoration is performed. Choose Include surrounding source points to add nearby wall inside the same drawn area.':'Exports the captured highlight from the original full-density scan caches. Deleted and non-visible points are excluded for area highlights.'):r.source==='objects'?'Exports the selected geometry and metadata in project coordinates.':'Choose the file format and the scan to deliver.';
  $('export-scene-label').hidden=!points||r.source!=='scene';
  $('export-deleted-context-row').hidden=!r.highlight?.deleted_groups;
  $('export-path').placeholder='C:\\Users\\YourName\\Documents\\'+nameFor(r);
  if(!locked()){lastSaved=null;failedId=null;}
 }
 function pathMode(enabled){
  $('export-path-label').hidden=!enabled;$('export-manual-path').textContent=enabled?'Use save window…':'Enter destination path';
  $('export-manual-path').setAttribute('aria-expanded',String(enabled));focusPath=enabled;
  if(enabled&&!busy&&!locked()){$('export-path').focus();focusPath=false;}
 }
 function usePath(reason,error=true){pathMode(true);message(reason+' Enter the full folder and filename, then click Save as….',error);return null;}
 async function choose(r){
  const suggested=nameFor(r);
  if(!$('export-path-label').hidden){
   focusPath=true;
   if(!$('export-path').value.trim())throw Error('Enter the full folder and filename for the exported file.');
   const d=await post('/api/export-destination',{destination:$('export-path').value,format:r.format});focusPath=false;return {path:d.destination};
  }
  if(window.pywebview?.api?.choose_export_file){
   let choice;try{choice=await window.pywebview.api.choose_export_file(suggested,r.format);}catch(e){return usePath('Save window unavailable: '+e.message);}
   if(choice?.cancelled)return usePath('Save cancelled. No file was saved.',false);
   if(choice?.error)return usePath('Save window unavailable: '+choice.error);
   if(typeof choice?.path!=='string'||!choice.path.trim())return usePath('The save window returned no destination. No file was saved.',false);
   const d=await post('/api/export-destination',{destination:choice.path,format:r.format});return {path:d.destination};
  }
  if(window.showSaveFilePicker){
   try{return {handle:await window.showSaveFilePicker({suggestedName:suggested,types:[{description:r.format.toUpperCase()+' file',accept:{'application/octet-stream':['.'+r.format]}}]})};}
   catch(e){if(e.name==='AbortError')return usePath('Save cancelled. No file was saved.',false);return usePath('Save window unavailable: '+e.message);}
  }
  return usePath('This browser cannot open a save window.');
 }
 async function deliver(item,to){
  writing=true;sync();message('Saving '+(to.path||to.handle.name)+'…');
  try{
   let location;
   if(to.path){
    let result=await post('/api/export-save',{id:item.id,destination:to.path});
    if(result.needs_overwrite){
     if(!confirm('Replace the existing file?\n'+result.destination)){message('Save cancelled. The prepared export is available to save elsewhere.');failedId=item.id;return;}
     result=await post('/api/export-save',{id:item.id,destination:result.destination,replace_version:result.replace_version});
     if(result.needs_overwrite)throw Error('The destination changed. Choose Save exported file again.');
    }
    if(!result.saved)throw Error('The file was not saved.');location=result.destination;
   }else{
    const response=await fetch('/api/export-file?'+new URLSearchParams({id:item.id}));
    if(!response.ok){let error;try{error=(await response.json()).error;}catch(_){}throw Error(error||'Could not read the prepared export.');}
    if(!response.body)throw Error('This browser cannot stream the file. Use a full destination path.');
    let writable;
    try{writable=await to.handle.createWritable();await response.body.pipeTo(writable);}
    catch(e){if(writable)try{await writable.abort();}catch(_){}throw e;}
    location=to.handle.name;
   }
   lastSaved={id:item.id,path:location};failedId=null;message('Saved successfully: '+location+(item.count!=null?' · '+fmt(item.count,0)+(item.format==='obj'?' triangles.':' points.'):'.'));
  }catch(e){failedId=item.id;message('Could not save: '+e.message+' The prepared export is available to retry.',true);}
  finally{writing=false;pending=null;sync();}
 }
 async function start(storePending){
  if(busy||locked())return;
  const r=request();if(r.source==='highlighted'&&['obj','tstudio','json'].includes(r.format)){message('Choose E57, LAS, LAZ, PLY, PTS, XYZ or CSV for highlighted points.',true);return;}if(r.source==='objects'&&r.format!=='json'){message('Choose JSON for the highlighted geometry and metadata.',true);return;}if(r.source==='highlighted'&&!r.highlight){message('Highlight points before exporting.',true);return;}if(r.source==='scene'&&!r.source_id&&!['obj','tstudio'].includes(r.format)){message('Select a scan to export.',true);return;}
  choosing=true;lastSaved=null;failedId="choosing";sync();
  try{
   const to=await choose(r); // invoked from the click before any unrelated await
   if(!to){if($('export-path-label').hidden)message('Export cancelled. No file was saved.');return;}
   await storePending?.();
   const result=await post('/api/export',r);
   if(!result.id)throw Error('The export did not start. Restart Tunnel Studio after installing this update.');
   pending={id:result.id,to};setBusy(true);message('Preparing all remaining points for '+(to.path||to.handle.name)+'…');poll();
  }catch(e){message(e.name==='AbortError'?'Export cancelled. No file was saved.':e.message,true);}
  finally{choosing=false;sync();}
 }
 async function saveAgain(){
  if(busy||locked())return;const item=state?.export_ready;if(!matches(item))return;
  choosing=true;sync();try{const to=await choose(request());if(to)await deliver(item,to);else if($('export-path-label').hidden)message('Save cancelled.');}catch(e){message(e.message,true);}finally{choosing=false;sync();}
 }
 function sync(){
  const block=busy||locked();for(const id of fields)$(id).disabled=block;
  $('project-dialog-action').disabled=block;$('project-dialog-close').disabled=locked();
  if(!block&&focusPath&&!$('export-path-label').hidden){$('export-path').focus();focusPath=false;}
  const item=state?.export_ready,ready=matches(item)&&!busy;
  $('export-download').hidden=!ready;$('export-download').disabled=block;
  if(pending&&!writing&&!busy){
   if(item?.id===pending.id){const to=pending.to;void deliver(item,to);return;}
   if(state?.job?.error||state?.job?.cancelled){pending=null;message(state.job.cancelled?'Export cancelled. No file was saved.':'Export failed: '+state.job.error,true);queueMicrotask(sync);}
  }
  if(ready){
   $('export-result').textContent=(item.deleted_points?fmt(item.deleted_points,0)+' deleted points excluded. ':'')+(item.warnings||[]).join(' ');
   if(!locked()&&!lastSaved&&!failedId)message('Export ready'+(item.count!=null?' · '+fmt(item.count,0)+(item.format==='obj'?' triangles':' points'):'')+'. Choose Save exported file.');
  }
 }
 $('export-scene').onchange=()=>{changed();$('export-download').hidden=true;};
 $('export-manual-path').onclick=()=>{if(busy||locked())return;const enabled=$('export-path-label').hidden;pathMode(enabled);message(enabled?'Enter the full folder and filename. The selected file extension is added if needed. Click Save as… to save there.':'Click Save as… to choose a destination in the save window.');};
 $('project-dialog').addEventListener('cancel',e=>{if(locked()){e.preventDefault();message('Export is running. Wait for saving to finish, or use Stop while the scan file is being prepared.');}});
 function openHighlight(value){if(!value?.ids?.length){notice('No full-density source for this highlight. Select a scan or run its review again.',true);return;}window.TunnelRibbon.export('highlighted');highlight=JSON.parse(JSON.stringify(value));$('export-deleted-context').checked=false;highlight.versions=Object.fromEntries(window.JobUI.inventory().items.filter(x=>value.ids.includes(x.id)&&x.geometry_revision).map(x=>[x.id,x.geometry_revision]));$('export-format').value='e57';$('export-format').dispatchEvent(new Event('change'));sync();}
 function openObjects(ids){window.TunnelRibbon.export('objects');objectIds=[...ids];$('export-format').value='json';$('export-format').dispatchEvent(new Event('change'));sync();}
 function openMesh(ids){window.TunnelRibbon.export('scene');meshIds=Array.isArray(ids)?[...ids]:[ids];$('export-format').value='obj';$('export-format').dispatchEvent(new Event('change'));sync();}
 async function openTargets(keys){const id=window.JobUI.selectedItem()?.id;if(id)await window.ScanRegistrationUI.activateTargets(id);openObjects(keys.map(key=>'target:'+key));}
 function openTarget(target){return openTargets([target.key]);}
 $('export-deleted-context').onchange=()=>{changed();sync();};
 window.ExportUI={open,changed,start,saveAgain,sync,openHighlight,openObjects,openMesh,openTarget,openTargets,request};
})();
