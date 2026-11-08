/* Copyright (c) 2026 Damian Gulczynski. Reviewed item and storage removal. */
'use strict';
(()=>{
 const dialog=document.createElement('dialog');dialog.id='explorer-delete-dialog';dialog.setAttribute('aria-labelledby','explorer-delete-title');
 dialog.innerHTML=`<header><div><span>PROJECT EXPLORER</span><h2 id="explorer-delete-title">Delete selected items</h2></div><button id="explorer-delete-close" aria-label="Close delete review">×</button></header>
 <div class="explorer-delete-body"><p id="explorer-delete-intro">Checking the selected items and their stored data…</p>
 <div id="explorer-delete-review" hidden><ul id="explorer-delete-items"></ul><p id="explorer-delete-preserved" hidden></p><div class="explorer-delete-metrics"><div><strong id="explorer-delete-free">0 B</strong><span>can be freed from disk</span></div><div><strong id="explorer-delete-shared">0 B</strong><span>still needed elsewhere</span></div></div>
 <p id="explorer-delete-storage"></p><p id="explorer-delete-history" class="explorer-delete-consequence"></p><p id="explorer-delete-project"></p></div>
 <div id="explorer-delete-result" hidden></div><p id="explorer-delete-status" role="status" aria-live="polite"></p></div>
 <footer><button id="explorer-delete-refresh">Refresh review</button><button id="explorer-delete-cancel">Cancel</button><button id="explorer-delete-apply" class="danger" disabled>Delete selected items</button></footer>`;
 document.body.append(dialog);
 const E=name=>document.getElementById('explorer-delete-'+name),J=()=>window.JobUI;
 const number=value=>Number(value||0).toLocaleString('en-GB');
 const bytes=value=>{const n=Math.max(0,Number(value)||0);return n>=1024**3?(n/1024**3).toFixed(2)+' GiB':n>=1024**2?(n/1024**2).toFixed(1)+' MiB':n>=1024?(n/1024).toFixed(1)+' KiB':number(n)+' B';};
 let request=null,review=null,loading=false,pending=false,finished=false,generation=0,releaseTimer=null;
 function status(text,error=false){E('status').textContent=text;E('status').classList.toggle('error',error);}
 function controls(){E('apply').disabled=loading||pending||finished||!review;E('apply').hidden=finished;E('refresh').hidden=finished;E('refresh').disabled=loading||pending;E('close').disabled=pending;E('cancel').disabled=pending;E('cancel').textContent=finished?'Close':'Cancel';}
 function renderReview(value){
  const count=Number(value.item_count)||value.items?.length||0;
  E('intro').textContent='Delete '+number(count)+' selected item'+(count===1?'':'s')+' from this project and release any working files no longer needed.';
  const list=E('items');list.replaceChildren();
  for(const row of (value.items||[]).slice(0,100)){const li=document.createElement('li');const name=document.createElement('b');name.textContent=row.name||row.id;const kind=document.createElement('small');kind.textContent=String(row.kind||'item').replaceAll('_',' ');li.append(name,kind);list.append(li);}
  if((value.items?.length||0)>100){const li=document.createElement('li');li.textContent='… and '+number(value.items.length-100)+' more selected items';list.append(li);}
  E('free').textContent=bytes(value.exclusive_bytes);E('shared').textContent=bytes(value.shared_bytes);
  E('storage').textContent=number(value.exclusive_files)+' working file'+(Number(value.exclusive_files)===1?'':'s')+' can be removed. '+(value.shared_files?number(value.shared_files)+' shared file'+(Number(value.shared_files)===1?'':'s')+' remain in use. ':'')+'Original imported files are kept.';
  E('history').textContent=value.history_cleared?'This deletion releases stored data and cannot be undone. The workspace’s Undo/Redo history will be cleared.':'The selected items will be removed. Shared working data remains available to the items or projects using it. Undo can restore this deletion.';
  E('project').textContent=value.updates_saved_project?'The current saved project will be updated so these items do not return when reopened. Other saved projects retain their data.':'The current workspace will be saved with these items removed.';
  const kept=value.preserved_registered_clouds||[];E('preserved').hidden=!kept.length;E('preserved').textContent=kept.length?'Kept in the project: '+kept.map(row=>row.name).join(', ')+'.':'';
  E('review').hidden=false;E('result').hidden=true;E('apply').textContent=value.history_cleared?'Delete items and free disk space':'Delete selected items';
 }
 async function refresh(){
  if(pending||!request)return;const token=++generation;review=null;finished=false;loading=true;E('review').hidden=true;E('result').hidden=true;status('Checking selected items, shared files and disk space…');controls();
  try{const value=await post('/api/explorer-delete-review',request);if(token!==generation||!dialog.open)return;if(!value.revision||!Number(value.item_count))throw Error('No removable items remain in this selection.');review=value;renderReview(value);status('Review the items and disk-space estimate, then confirm deletion.');}
  catch(error){if(token===generation&&dialog.open)status(error.message,true);}
  finally{if(token===generation){loading=false;controls();}}
 }
 async function open(value={}){
  if(busy||pending||dialog.open)return false;
  if(window.CadUI?.command?.()||window.TunnelDesigner?.isPicking?.()){showTunnelMessage('Finish the current command','Finish or cancel the current drawing or section pick before deleting project items.',true);return false;}
  const ids=[...new Set(value.ids||[])],bundle_ids=[...new Set(value.bundle_ids||[])];if(!ids.length&&!bundle_ids.length)return false;
  request={ids,bundle_ids};review=null;finished=false;E('intro').textContent='Checking the selected items and their stored data…';E('result').replaceChildren();E('result').hidden=true;E('review').hidden=true;dialog.showModal();await refresh();return true;
 }
 function showResult(report){
  const result=E('result');result.replaceChildren();const heading=document.createElement('h3');heading.textContent=report.pending_bytes?'Items removed · releasing stored data':'Deletion complete';result.append(heading);
  const p=document.createElement('p');p.textContent=number(report.removed_count)+' item'+(Number(report.removed_count)===1?'':'s')+' removed · '+bytes(report.freed_bytes)+' freed from disk.';result.append(p);
  if(report.shared_bytes){const shared=document.createElement('p');shared.textContent=bytes(report.shared_bytes)+' remains because other items or saved projects still use it.';result.append(shared);}
  if(report.pending_bytes){const pendingText=document.createElement('p');pendingText.textContent=bytes(report.pending_bytes)+' is being released in the background. You can close this window and continue working.'+(report.pending_message?' '+report.pending_message:'');result.append(pendingText);}
  for(const warning of report.warnings||[]){const p=document.createElement('p');p.textContent=warning;result.append(p);}
  E('review').hidden=true;result.hidden=false;E('intro').textContent='The project has been saved with the selected items removed.';
 }
 function pollRelease(report){
  clearTimeout(releaseTimer);if(!report.pending_bytes||!dialog.open)return;const token=generation;
  releaseTimer=setTimeout(async()=>{try{const next=await api('/api/explorer-delete-report');if(token!==generation||!dialog.open)return;showResult(next);status(next.pending_bytes?'Items removed. Stored data is being released in the background.':'Saved. Project Explorer and disk storage are up to date.');pollRelease(next);}catch(error){if(token===generation&&dialog.open)status('Items removed. Could not update the background release status: '+error.message,true);}},1000);
 }
 async function apply(){
  if(pending||!review||E('apply').disabled)return;pending=true;const revision=review.revision;status('Deleting selected items and releasing unused working files…');controls();
  try{
   J().clearCleaningPreview();setBusy(true);await post('/api/explorer-delete',{...request,revision,confirmed:true});
   const completed=await J().waitReady(job=>status(job.message||'Deleting selected items…'),{skipSync:true});
   if(completed?.cancelled)throw Error('Deletion stopped. Refresh the review to check the current project.');
   const report=await api('/api/explorer-delete-report');
   window.SceneObjectsUI?.clearSelected();window.CadUI?.clearSelected();window.WorkspaceUI?.clearBundleSelection?.();
   await J().sync();await window.CadUI?.load(true);await window.InspectionUI?.sync(true);window.HistoryUI?.sync();J().draw();
   finished=true;showResult(report);status(report.pending_bytes?'Items removed. Stored data is being released in the background.':'Saved. Project Explorer and disk storage are up to date.');pollRelease(report);
   window.WorkStatus?.show('Items deleted',number(report.removed_count)+' removed · '+bytes(report.freed_bytes)+' freed');
  }catch(error){review=null;status(error.message,true);}
  finally{pending=false;setBusy(false);controls();}
 }
 function close(){if(pending)return;generation++;loading=false;clearTimeout(releaseTimer);dialog.close();}
 E('apply').onclick=apply;E('refresh').onclick=refresh;E('cancel').onclick=close;E('close').onclick=close;
 dialog.addEventListener('cancel',event=>{if(pending)event.preventDefault();else{generation++;loading=false;clearTimeout(releaseTimer);}});
 window.ExplorerDeleteUI={open};
})();
