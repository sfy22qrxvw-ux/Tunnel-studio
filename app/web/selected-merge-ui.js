/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
window.SelectedMergeUI=(()=>{
 const dialog=document.createElement('dialog');dialog.id='selected-merge-dialog';
 dialog.innerHTML=`<div class="dialog-heading"><h2 id="selected-merge-title">Merge registered scans</h2><button id="selected-merge-close" aria-label="Close">×</button></div>
 <p>Copy exactly these selected setups into one independent Registered cloud at their current saved positions.</p>
 <ul id="selected-merge-sources"></ul><label>New cloud name<input id="selected-merge-name" value="Registered cloud" maxlength="120"></label>
 <p id="selected-merge-space" class="project-merge-space"></p>
 <p class="tiny">Original setups and registration links remain available. Deleted points stay excluded; temporary view filters do not trim the merge. Overlapping observations are retained. This does not recalculate registration.</p>
 <p id="selected-merge-message" role="status"></p><progress id="selected-merge-progress" hidden aria-label="Merge progress"></progress>
 <div class="report-actions"><button id="selected-merge-start" class="primary">Merge scans</button><button id="selected-merge-stop" hidden>Stop</button></div>`;
 document.body.append(dialog);
 const el=id=>document.getElementById(id);let ids=[],estimate=null,running=false,generation=0;
 const size=n=>{if(!Number.isFinite(n))return '—';let i=0;while(n>=1024&&i<4){n/=1024;i++;}return n.toFixed(i?1:0)+' '+['B','KB','MB','GB','TB'][i];};
 function eligible(selected){
  const rows=window.JobUI?.inventory().items||[],known=new Map(rows.map(x=>[x.id,x]));
  if(!Array.isArray(selected)||selected.length<2||selected.length>200)return false;
  if(selected.some(id=>{const x=known.get(id);return !x||x.kind!=='scan'||x.registered_merge||x.merge_stale||x.merge_archived;}))return false;
  const connected=new Set([selected[0]]),queue=[selected[0]];
  while(queue.length){const id=queue.pop();for(const link of known.get(id)?.registration_summary?.links||[]){const other=known.get(link.other_id);if(other&&other.kind==='scan'&&!other.registered_merge&&!connected.has(other.id)){connected.add(other.id);queue.push(other.id);}}}
  return selected.every(id=>connected.has(id));
 }
 function controls(){
  el('selected-merge-start').disabled=running||!estimate?.enough;
  el('selected-merge-name').disabled=running;el('selected-merge-close').disabled=running;
  el('selected-merge-progress').hidden=!running;el('selected-merge-stop').hidden=!running;
 }
 async function open(selected,bundleName=null){
  if(busy||running)return;
  if(!eligible(selected))return notice('Select two or more setups with saved registration connections first.',true);
  ids=[...selected];estimate=null;const token=++generation;
  el('selected-merge-title').textContent=bundleName?'Merge '+bundleName:'Merge registered scans';
  el('selected-merge-start').textContent=bundleName?'Merge bundle':'Merge scans';
  const rows=window.JobUI.inventory().items;
  el('selected-merge-sources').innerHTML=ids.map(id=>'<li>'+esc(rows.find(x=>x.id===id)?.name||id)+'</li>').join('');
  el('selected-merge-name').value='Registered cloud';el('selected-merge-message').textContent='Checking current positions and disk space…';el('selected-merge-space').textContent='';
  if(!dialog.open)dialog.showModal();controls();
  try{
   const result=await post('/api/scan-registration',{action:'selection_merge_estimate',ids});
   if(token!==generation||!dialog.open)return;
   estimate=result;el('selected-merge-space').textContent=`${result.setups} selected setups · up to ${fmt(result.points,0)} points · ${size(result.payload_bytes)} new data · ${size(result.required_free_bytes)} free required including reserve · ${size(result.free_bytes)} free.`;
   el('selected-merge-message').textContent=result.enough?'Ready. Only the setups listed above will be included.':'There is not enough free space for this merge.';
  }catch(error){if(token===generation)el('selected-merge-message').textContent=error.message;}
  finally{if(token===generation)controls();}
 }
 el('selected-merge-start').onclick=async()=>{
  if(running||!estimate?.enough||busy)return;
  const name=el('selected-merge-name').value.trim();if(!name){el('selected-merge-message').textContent='Enter a name for the merged cloud.';return;}
  running=true;controls();el('selected-merge-message').textContent='Building the independent cloud…';
  try{
   await window.TargetUI?.saveIfDirty?.();
   setBusy(true);
   const started=await post('/api/scan-registration',{action:'build_selection_merge',ids,name,expected_token:estimate.selection_token});
   const job=await window.JobUI.waitReady(p=>{el('selected-merge-message').textContent=p.message||'Merging original points…';},{skipSync:true});
   if(job?.cancelled){el('selected-merge-message').textContent='Merge cancelled. No completed cloud was published.';return;}
   await window.JobUI.sync();
   const result=window.JobUI.inventory().items.find(x=>x.registered_merge&&x.merge_registration_id===started.registration_id);
   // The public inventory deliberately omits registration IDs. The exact new
   // result is also recorded in the registration API after atomic publication.
   const network=await api('/api/scan-registration');const ident=result?.id||network.last_merge?.id;
   if(!ident||network.last_merge?.registration_id!==started.registration_id)throw Error('The completed merge could not be confirmed. Review the Explorer before trying again.');
   window.JobUI.selectScan(ident);dialog.close();notice('Merged '+ids.length+' setups into '+name+'. The new cloud is independent; original setups and links were kept.');
  }catch(error){el('selected-merge-message').textContent=error.message;}
  finally{running=false;estimate=null;controls();setBusy(false);}
 };
 el('selected-merge-stop').onclick=async()=>{el('selected-merge-stop').disabled=true;try{await post('/api/cancel-operation',{});el('selected-merge-message').textContent='Stopping safely…';}catch(error){el('selected-merge-message').textContent=error.message;}finally{el('selected-merge-stop').disabled=false;}};
 el('selected-merge-close').onclick=()=>{if(!running)dialog.close();};
 dialog.addEventListener('cancel',e=>{if(running)e.preventDefault();});dialog.addEventListener('close',()=>{generation++;estimate=null;});
 function openBundle(bundleId){
  const bundle=window.JobUI.inventory().bundles?.find(item=>item.id===bundleId);
  if(!bundle){notice('Select a connected bundle in Project Explorer first.',true);return;}
  return open(bundle.scan_ids,bundle.name);
 }
 return {eligible,open,openBundle};
})();
