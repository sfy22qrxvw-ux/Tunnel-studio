/* Copyright (c) 2026 Damian Gulczynski. Reviewed permanent point removal. */
'use strict';
(()=>{
 const el=document.createElement('dialog');el.id='permanent-points-dialog';el.className='permanent-points-dialog';el.setAttribute('aria-labelledby','permanent-points-title');
 el.innerHTML=`<header class="permanent-points-heading"><div><span class="permanent-points-eyebrow">EDIT · DELETED POINTS</span><h2 id="permanent-points-title">Permanently remove deleted points</h2></div><button id="permanent-points-close" aria-label="Close permanent removal">×</button></header>
 <div class="permanent-points-body"><p>Select one or many scans. Tunnel Studio compacts them sequentially so large RTC360 scans do not compete for disk or RAM. Each scan is committed safely before the next begins.</p>
 <div class="permanent-points-source"><span>Scans to compact</span><div class="permanent-points-select-actions"><button id="permanent-points-select-all" type="button">Select all eligible</button><button id="permanent-points-select-none" type="button">Clear</button></div><div id="permanent-points-scan-list" class="permanent-points-scan-list"></div></div>
 <section id="permanent-points-review" hidden><div class="permanent-points-metrics"><div><strong id="permanent-points-scans">0</strong><span>selected scans</span></div><div><strong id="permanent-points-removed">0</strong><span>deleted points to remove</span></div><div><strong id="permanent-points-kept">0</strong><span>retained points</span></div><div><strong id="permanent-points-archives">0</strong><span>archived restoration points</span></div></div>
 <div id="permanent-points-review-list" class="permanent-points-review-list"></div><p id="permanent-points-disk" class="permanent-points-note"></p>
 <div class="permanent-points-consequence"><strong>This cannot be undone.</strong><p id="permanent-points-history"></p><p>Physical compaction still has to read the source cache and write retained rows once. This version avoids the old second full read used only to build the preview and uses larger streaming blocks. Original imported files and other saved projects remain available.</p><p>Stopping a multi-scan batch keeps scans that already finished permanently compacted; the scan currently being processed remains atomic.</p></div>
 <label class="permanent-points-confirm"><input type="checkbox" id="permanent-points-confirm">I understand that this permanently removes the selected deleted points and clears this workspace’s Undo/Redo history.</label></section>
 <section id="permanent-points-result" class="permanent-points-result" hidden></section><p id="permanent-points-status" role="status" aria-live="polite">Choose one or more scans.</p></div>
 <footer class="permanent-points-actions"><button id="permanent-points-refresh">Refresh review</button><button id="permanent-points-stop" hidden>Stop</button><button id="permanent-points-cancel">Cancel</button><button id="permanent-points-apply" class="danger" disabled>Permanently remove selected</button></footer>`;
 document.body.append(el);
 const $=suffix=>document.getElementById('permanent-points-'+suffix),J=()=>window.JobUI;
 const number=value=>Number(value||0).toLocaleString('en-GB');
 const bytes=value=>{const v=Number(value||0);return (Math.abs(v)>=1024**3?(v/1024**3).toFixed(2)+' GiB':(v/1024**2).toFixed(1)+' MiB');};
 const eligible=x=>x.kind==='scan'&&(x.excluded_count>0||(x.deleted_groups||[]).some(g=>g.archived&&g.count>0));
 let candidates=[],selected=new Set(),reviews=[],loading=false,pending=false,finished=false,generation=0;
 function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
 function selectedIds(){return candidates.filter(x=>selected.has(x.id)).map(x=>x.id);}
 function renderCandidates(){
  const list=$('scan-list');list.replaceChildren();
  if(!candidates.length){const p=document.createElement('p');p.className='permanent-points-note';p.textContent='No scans currently contain deleted points or restoration archives.';list.append(p);return;}
  for(const scan of candidates){const label=document.createElement('label');label.className='permanent-points-scan-row';const input=document.createElement('input');input.type='checkbox';input.checked=selected.has(scan.id);input.dataset.scanId=scan.id;const text=document.createElement('span');text.innerHTML='<b></b><small></small>';text.querySelector('b').textContent=scan.name;text.querySelector('small').textContent=number(scan.excluded_count)+' currently excluded points';label.append(input,text);list.append(label);}
 }
 function controls(){
  const has=selected.size>0,space=reviews.length&&reviews.every(r=>Number(r.free_bytes)>=Number(r.required_free_bytes||0));
  $('refresh').disabled=loading||pending||!has;$('select-all').disabled=loading||pending||!candidates.length;$('select-none').disabled=loading||pending||!has;
  $('confirm').disabled=loading||pending||finished;$('close').disabled=pending;$('cancel').disabled=pending;$('stop').hidden=!pending;$('apply').hidden=finished;
  $('apply').disabled=loading||pending||finished||!reviews.length||!space||!$('confirm').checked;$('cancel').textContent=finished?'Close':'Cancel';
 }
 function resetReview(){reviews=[];finished=false;$('review').hidden=true;$('result').hidden=true;$('result').replaceChildren();$('confirm').checked=false;controls();}
 async function refresh(){
  if(pending)return;const ids=selectedIds(),request=++generation;resetReview();
  if(!ids.length){status('Choose at least one scan.');return;}
  loading=true;status('Reviewing '+number(ids.length)+' scan(s)…');controls();
  try{
   const rows=[];
   for(let i=0;i<ids.length;i++){
    status('Checking scan '+number(i+1)+' of '+number(ids.length)+'…');
    const value=await api('/api/compaction-preview?'+new URLSearchParams({id:ids[i]}));
    if(request!==generation||!el.open)return;rows.push(value);
   }
   reviews=rows;
   const sum=k=>rows.reduce((n,r)=>n+Number(r[k]||0),0);
   $('scans').textContent=number(rows.length);$('removed').textContent=number(sum('removed_points'));$('kept').textContent=number(sum('remaining_points'));$('archives').textContent=number(sum('archived_points'));
   const list=$('review-list');list.replaceChildren();
   for(const r of rows){const row=document.createElement('div');row.className='permanent-points-review-row';row.innerHTML='<div><b></b><small></small></div><span></span>';row.querySelector('b').textContent=r.name;row.querySelector('small').textContent=number(r.removed_points)+' deleted · '+number(r.remaining_points)+' retained'+(r.archived_points?' · '+number(r.archived_points)+' archived':'');row.querySelector('span').textContent=r.rewrites_cache===false?'metadata only':bytes(r.required_free_bytes)+' peak';list.append(row);}
   const peak=Math.max(...rows.map(r=>Number(r.required_free_bytes||0))),free=Math.min(...rows.map(r=>Number(r.free_bytes||0)));
   $('disk').textContent='Sequential batch: approximately '+bytes(peak)+' peak temporary free space is required for the largest selected scan; currently available: '+bytes(free)+'. Completed scans are reclaimed before the next scan starts where their old caches are not protected by another saved project.';
   const h=rows[0]||{};$('history').textContent='Workspace history currently contains '+number(h.workspace_undo_count)+' Undo and '+number(h.workspace_redo_count)+' Redo actions. Permanent compaction clears that point-edit history once processing begins.';
   $('review').hidden=false;
   if(rows.some(r=>Number(r.free_bytes)<Number(r.required_free_bytes||0)))status('At least one selected scan does not currently have enough temporary disk space. Remove it from the batch or free space, then refresh.',true);
   else status('Batch ready. Scans will be compacted one at a time using the faster single-pass preview path.');
  }catch(error){if(request===generation&&el.open)status(error.message,true);}
  finally{if(request===generation){loading=false;controls();}}
 }
 async function open(preferredId=null){
  if(pending||busy)return;
  if(window.CadUI?.command?.()||window.TunnelDesigner?.isPicking?.()){window.TunnelRibbon?.explainToolBlock?.('Finish or cancel the current drawing or section pick before permanently removing deleted points.');return;}
  if(window.TunnelRibbon?.prepareTool?.('permanent-points')===false)return;
  candidates=(J()?.inventory?.().items||[]).filter(eligible);selected=new Set();const current=preferredId||J()?.selectedItem?.()?.id;
  if(candidates.some(x=>x.id===current))selected.add(current);else if(candidates[0])selected.add(candidates[0].id);
  resetReview();renderCandidates();if(!el.open)el.showModal();await refresh();
 }
 function resultText(report){
  const result=$('result');result.replaceChildren();const title=document.createElement('h3');title.textContent='Permanent removal complete';result.append(title);
  const count=report.batch?report.scan_count:1;const points=document.createElement('p');points.textContent=number(count)+' scan(s) processed · '+number(report.removed_points)+' deleted points physically removed.';result.append(points);
  if(report.forgotten_archived_points){const p=document.createElement('p');p.textContent=number(report.forgotten_archived_points)+' archived restoration points were also removed.';result.append(p);}
  const net=report.net_reclaimed_bytes;const space=document.createElement('p');space.textContent=net==null?'Disk-space recovery could not be fully measured.':Number(net)>=0?'Disk space recovered: '+bytes(net)+'.':'Older protected caches currently keep '+bytes(-Number(net))+' of additional storage in use.';result.append(space);
  for(const warning of report.warnings||[]){const p=document.createElement('p');p.textContent=warning;p.className='permanent-points-note';result.append(p);}result.hidden=false;
 }
 async function apply(){
  if($('apply').disabled||!reviews.length||pending)return;const payload=reviews.map(r=>({id:r.id,revision:r.revision}));
  pending=true;controls();status('Starting permanent-removal batch…');
  try{
   J().clearCleaningPreview();
   const ok=await J().action({action:'compact_points_batch',items:payload,confirmed:true},null,{onProgress:job=>status(job.message||'Removing deleted points…')});
   if(!ok){const current=await api('/api/status');status(current.job?.cancelled?'Stopped. Scans completed before Stop remain compacted; the current scan was left unchanged.':current.job?.error||'Permanent removal did not finish. Refresh the review before trying again.',!current.job?.cancelled);reviews=[];$('confirm').checked=false;return;}
   const report=await api('/api/compaction-report');finished=true;$('review').hidden=true;resultText(report);status('Saved. The Project Explorer now shows the remaining points.');window.HistoryUI?.sync();
  }catch(error){status(error.message,true);reviews=[];$('confirm').checked=false;}
  finally{pending=false;controls();}
 }
 async function stop(){if(!pending)return;try{await post('/api/cancel-operation',{});status('Stopping at the next safe checkpoint…');}catch(error){status(error.message,true);}}
 function close(){if(pending)return;generation++;loading=false;el.close();}
 $('scan-list').addEventListener('change',event=>{const input=event.target.closest('input[data-scan-id]');if(!input)return;if(input.checked)selected.add(input.dataset.scanId);else selected.delete(input.dataset.scanId);resetReview();status(selected.size?number(selected.size)+' scan(s) selected. Refresh review to continue.':'Choose at least one scan.');controls();});
 $('select-all').onclick=()=>{selected=new Set(candidates.map(x=>x.id));renderCandidates();resetReview();status(number(selected.size)+' scan(s) selected. Refresh review to continue.');controls();};
 $('select-none').onclick=()=>{selected.clear();renderCandidates();resetReview();status('Choose at least one scan.');controls();};
 $('refresh').onclick=refresh;$('confirm').onchange=controls;$('apply').onclick=apply;$('stop').onclick=stop;$('close').onclick=close;$('cancel').onclick=close;
 el.addEventListener('cancel',event=>{if(pending){event.preventDefault();stop();}else{generation++;loading=false;}});
 document.addEventListener('click',event=>{const button=event.target.closest?.('[data-permanent-delete]');if(button){event.preventDefault();event.stopPropagation();open(button.dataset.owner||null);}});
 window.PermanentPointsUI={open};
})();
