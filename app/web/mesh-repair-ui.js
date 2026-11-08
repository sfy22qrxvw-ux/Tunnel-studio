/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const dialog=document.createElement('dialog');dialog.id='mesh-repair-dialog';
 dialog.innerHTML=`<div class="dialog-heading"><h2>Preview and repair mesh openings</h2><button id="mr-close" aria-label="Close">×</button></div>
 <label>Mesh to repair<select id="mr-mesh" aria-label="Mesh to repair"><option value="">Choose a mesh or CAD surface</option></select></label>
 <p id="mr-source"></p><p class="tiny">Inspect openings, select only the local caps you want, then create a repaired copy. Orange triangles are inferred geometry. Existing model geometry and editable design parameters stay in the project. Distance controls assume one source coordinate unit is one metre; coordinates are never rescaled.</p>
 <div class="mr-layout"><section><div class="surface-pair"><label>Maximum overall extent · m<input id="mr-extent" type="number" min="0.001" max="5" step="any" value="0.5"></label><label>Maximum plane deviation · mm<input id="mr-plane" type="number" min="0.1" max="50" step="any" value="10"></label><label>Maximum turn from neighbours · °<input id="mr-angle" type="number" min="1" max="80" step="any" value="60"></label><label>Cloud support distance · m<input id="mr-support" type="number" min="0.001" max="0.5" step="any" value="0.05"></label></div>
 <p class="tiny">Overall extent is the largest distance between boundary vertices, including the length of a narrow gap. Portals, sharp openings, overlapping faces and uncertain boundaries are preserved. Gaps above 0.5 m need nearby cloud support at at least 80% of sampled patch locations.</p>
 <label>Reference cloud for support check<select id="mr-cloud"><option value="">No reference cloud</option></select></label>
 <div class="surface-actions"><button id="mr-inspect" class="primary">Inspect openings</button><button id="mr-stop" hidden>Cancel operation</button></div>
 <p id="mr-summary" role="status">Inspect the mesh to see repairable, preserved and unfilled openings.</p>
 <div class="surface-actions"><button id="mr-all" disabled>Select repairable</button><button id="mr-none" disabled>Clear selection</button></div>
 <div class="mr-table"><table><thead><tr><th>Repair</th><th>Opening</th><th>Extent</th><th>Decision / reason</th></tr></thead><tbody id="mr-holes"></tbody></table></div>
 <div class="surface-actions"><button id="mr-prev">Previous</button><span id="mr-page"></span><button id="mr-next">Next</button></div>
 </section><section><canvas id="mr-canvas" width="900" height="600" aria-label="Mesh opening and inferred cap preview"></canvas><p class="tiny">Drag to orbit · wheel to zoom · click an opening to inspect/select it. Cyan: repairable · gold: preserved · rose: unfilled · orange: selected inferred caps.</p><p id="mr-view-note" class="tiny"></p><p id="mr-detail" class="tiny"></p></section></div>
 <div class="mr-footer"><label>New mesh name<input id="mr-name" maxlength="120"></label><label><input id="mr-hide" type="checkbox" checked> Hide original after repair</label><button id="mr-apply" class="primary" disabled>Create repaired copy</button><button id="mr-undo" disabled>Undo last repair</button></div><p id="mr-status" role="status"></p>`;
 const style=document.createElement('style');style.textContent='#mesh-repair-dialog{width:min(1380px,96vw);max-height:94vh;overflow:auto;box-sizing:border-box}#mesh-repair-dialog .mr-layout{display:grid;grid-template-columns:minmax(370px,1fr) minmax(420px,1.2fr);gap:20px}#mesh-repair-dialog .mr-table{max-height:320px;overflow:auto}#mesh-repair-dialog table{width:100%;font-size:12px}#mesh-repair-dialog td{padding:7px;vertical-align:top}#mesh-repair-dialog tr[data-highlight=true]{background:#36516a}#mr-canvas{width:100%;height:400px;background:#102334;touch-action:none;cursor:crosshair}#mesh-repair-dialog .mr-footer{display:flex;flex-wrap:wrap;align-items:end;gap:14px;margin-top:18px}#mr-status[data-error=true]{color:#ffb7a5}@media(max-width:900px){#mesh-repair-dialog .mr-layout{grid-template-columns:1fr}}';
 document.head.append(style);document.body.append(dialog);
 const $r=id=>document.getElementById('mr-'+id),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let source=null,preview=null,pending=false,loadingSource=false,sourceEpoch=0,sourceChoices=[],selected=new Set(),highlight=null,page=0,dirty=false,yaw=-.7,pitch=.5,zoom=1,drag=null,screen=[];
 const fields=['extent','plane','angle','support','cloud'];
 function message(text,error=false){$r('status').textContent=text;$r('status').dataset.error=String(error);}
 function controls(){for(const id of [...fields,'inspect','name','hide','all','none'])$r(id).disabled=pending||loadingSource||!source||(['all','none'].includes(id)&&(!preview||dirty));$r('mesh').disabled=pending||!sourceChoices.length;$r('apply').disabled=pending||loadingSource||!source||!preview||dirty||!selected.size;$r('stop').hidden=!pending;$r('undo').disabled=pending||loadingSource||!preview?.last_apply;$r('close').disabled=pending;$r('prev').disabled=pending||loadingSource||!page;$r('next').disabled=pending||loadingSource||!preview||(page+1)*100>=preview.holes.length;}
 function settings(){for(const id of fields.slice(0,4))if(!$r(id).checkValidity())throw Error('Check '+$r(id).closest('label').textContent);return{max_extent_m:+$r('extent').value,max_plane_deviation_mm:+$r('plane').value,max_normal_angle_deg:+$r('angle').value,support_distance_m:+$r('support').value};}
 function invalidate(){dirty=true;selected.clear();message('Settings changed. Inspect openings again before repairing.');controls();draw();}
 function detail(row){highlight=row.id;const support=row.support_fraction==null?row.support_status:`${(row.support_fraction*100).toFixed(1)}% of ${row.support_samples} sampled patch locations have a nearby retained cloud point. The cap remains inferred.`;$r('detail').textContent=`${row.id}: ${row.reason} ${row.extent_m==null?'':`Overall extent ${row.extent_m.toFixed(4)} m. `}${row.width_m==null?'':`Narrow width ${row.width_m.toFixed(4)} m. `}${support}`;}
 function render(){
  if(!preview){$r('holes').innerHTML='';$r('page').textContent='';$r('summary').textContent=source?'Inspect the mesh to see repairable, preserved and unfilled openings.':'Choose a mesh or CAD surface above.';$r('view-note').textContent='';controls();draw();return;}
  const summary=preview.last_apply?.summary||preview.summary;
  $r('summary').textContent=`${preview.holes.length} detected openings · ${preview.last_apply?summary.filled+' filled · ':''}${preview.summary.repairable} repairable · ${summary.preserved} preserved · ${summary.unfilled} unfilled · ${selected.size} selected.`;
  $r('view-note').textContent=preview.context.note;
  const start=page*100,rows=preview.holes.slice(start,start+100);
  $r('holes').innerHTML=rows.map(row=>`<tr data-hole="${esc(row.id)}" data-highlight="${row.id===highlight}"><td><input type="checkbox" data-repair="${esc(row.id)}" aria-label="Repair ${esc(row.id)}" ${selected.has(row.id)?'checked':''} ${row.status!=='repairable'||dirty?'disabled':''}></td><td>${esc(row.id)}</td><td>${row.extent_m==null?'—':row.extent_m.toFixed(3)+' m'}</td><td><b>${esc(row.status)}</b><br>${esc(row.reason)}</td></tr>`).join('');
  $r('page').textContent=preview.holes.length?`${start+1}–${Math.min(start+100,preview.holes.length)} of ${preview.holes.length}`:'No open boundaries';
  controls();draw();
 }
 function resetSource(){source=null;preview=null;selected.clear();highlight=null;page=0;dirty=false;zoom=1;$r('source').textContent='';$r('name').value='';$r('detail').textContent='';render();}
 async function loadSource(id){
  if(pending)return;
  const epoch=++sourceEpoch;loadingSource=!!id;resetSource();$r('mesh').value=id||'';
  if(!id){message('Choose a mesh or CAD surface above.');controls();return;}
  message('Loading selected mesh…');controls();
  try{
   const data=await api('/api/mesh-repair?'+new URLSearchParams({source_id:id}));
   if(epoch!==sourceEpoch||!dialog.open)return;
   source=data.source;preview=data.preview;
   if(!source||source.id!==id)throw Error('The selected mesh is no longer available. Choose another source.');
   $r('source').textContent=source.name+' · '+source.units;$r('name').value=(source.name+' repaired').slice(0,120);
   $r('cloud').innerHTML='<option value="">No reference cloud</option>'+data.clouds.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
   const cfg=preview?.settings||data.defaults;[['extent','max_extent_m'],['plane','max_plane_deviation_mm'],['angle','max_normal_angle_deg'],['support','support_distance_m']].forEach(([key,value])=>$r(key).value=cfg[value]);
   $r('cloud').value=preview?.reference?.id||'';message('Inspect this mesh, then select the openings to repair.');render();
  }catch(error){if(epoch===sourceEpoch&&dialog.open){resetSource();message(error.message,true);}}
  finally{if(epoch===sourceEpoch){loadingSource=false;controls();}}
 }
 async function open(id=null){
  if(pending||busy)return;
  const item=window.JobUI?.selectedItem?.();
  const chosen=new Set([...(window.JobUI?.selectedItems?.()||(item?[item.id]:[])),...(window.CadUI?.selectedIds?.({includeInactive:true})||[])]);
  const epoch=++sourceEpoch;loadingSource=true;sourceChoices=[];resetSource();$r('mesh').innerHTML='<option value="">Loading meshes…</option>';
  window.TunnelRibbon?.closeTool?.();if(!dialog.open)dialog.showModal();message('Loading available meshes…');controls();
  try{
   const data=await api('/api/mesh-repair');if(epoch!==sourceEpoch||!dialog.open)return;
   sourceChoices=data.sources||[];$r('mesh').innerHTML='<option value="">Choose a mesh or CAD surface</option>'+sourceChoices.map(x=>`<option value="${esc(x.id)}">${esc(x.name)} · ${x.kind==='cad'?'CAD surface':'Mesh'}</option>`).join('');
   const selectedSources=sourceChoices.filter(x=>chosen.has(x.id));
   const target=id?sourceChoices.find(x=>x.id===id):selectedSources.length===1?selectedSources[0]:!selectedSources.length&&sourceChoices.length===1?sourceChoices[0]:null;
   if(target){await loadSource(target.id);return;}
   message(!sourceChoices.length?'No triangular meshes or CAD surfaces are available in this project. Import or create a mesh first.':id?'The requested object is unavailable for repair. Choose a source above.':selectedSources.length>1?'Several meshes are selected. Choose one source above.':'Choose the mesh or CAD surface to repair above.',!!id);render();
  }catch(error){if(epoch===sourceEpoch&&dialog.open)message(error.message,true);}
  finally{if(epoch===sourceEpoch){loadingSource=false;controls();}}
 }
 async function run(apply){
  if(pending||busy||!source)return;
  try{
   const cfg=settings();if(apply&&(!preview||dirty||!selected.size))throw Error('Inspect the mesh and select repairable openings first.');
   pending=true;controls();setBusy(true);message(apply?'Creating a repaired copy…':'Inspecting every mesh boundary…');
   await post('/api/mesh-repair',{action:apply?'apply':'analyse',source_id:source.id,settings:cfg,reference_id:$r('cloud').value||null,...(apply?{preview_token:preview.preview_token,selected_ids:[...selected],name:$r('name').value,hide_source:$r('hide').checked}:{})});
   const job=await window.JobUI.waitReady(value=>message(value.message||'Checking mesh…'));
   if(job.cancelled)throw Error('Mesh repair cancelled. Original geometry is unchanged.');
   const data=await api('/api/mesh-repair?'+new URLSearchParams({source_id:source.id}));
   if(!data.preview)throw Error('The source changed. Reopen the repair tool and inspect again.');
   preview=data.preview;dirty=false;page=0;if(!apply)selected.clear();
   if(apply&&preview.last_apply){await window.JobUI.sync();window.JobUI.chooseItem(preview.last_apply.result_id);await window.JobUI.loadPreview();message(`${preview.last_apply.summary.filled} openings filled in a new mesh; ${preview.last_apply.summary.preserved} preserved and ${preview.last_apply.summary.unfilled} unfilled. Orange faces are inferred. Undo restores the previous scene.`);}
   else message('Review the highlighted openings. Nothing is selected automatically.');
   render();
  }catch(error){message(error.message,true);}finally{pending=false;setBusy(false);controls();}
 }
 async function undo(){try{const status=await api('/api/status');if(status.history?.undo_label!=='Repair selected holes')throw Error('The repair is no longer the latest workspace action. Use Workspace Undo to review later changes first.');await post('/api/history',{action:'undo'});preview.last_apply=null;await window.JobUI.sync();await window.CadUI?.load(true);message('Repair undone; the original scene and editable models are restored.');render();}catch(error){message(error.message,true);}}
 function pick(id){const row=preview?.holes.find(x=>x.id===id);if(!row)return;detail(row);if(row.status==='repairable'&&!dirty){selected.has(id)?selected.delete(id):selected.add(id);}page=Math.floor(preview.holes.indexOf(row)/100);render();}
 $r('holes').addEventListener('click',event=>{const row=event.target.closest('[data-hole]');if(row&&!pending)pick(row.dataset.hole);});
 $r('all').onclick=()=>{selected=new Set(preview.holes.filter(x=>x.status==='repairable').map(x=>x.id));render();};$r('none').onclick=()=>{selected.clear();render();};
 $r('prev').onclick=()=>{page=Math.max(0,page-1);render();};$r('next').onclick=()=>{page++;render();};
 for(const id of fields)$r(id).addEventListener(id==='cloud'?'change':'input',invalidate);
 $r('mesh').onchange=()=>loadSource($r('mesh').value);
 $r('inspect').onclick=()=>run(false);$r('apply').onclick=()=>run(true);$r('undo').onclick=undo;$r('close').onclick=()=>dialog.close();
 $r('stop').onclick=async()=>{try{await post('/api/cancel-operation',{});message('Stopping at the next geometry or cloud-read checkpoint…');}catch(error){message(error.message,true);}};
 dialog.addEventListener('cancel',event=>{if(pending)event.preventDefault();});
 dialog.addEventListener('close',()=>{if(!dialog.open){sourceEpoch++;loadingSource=false;}});
 function draw(){
  const canvas=$r('canvas'),ctx=canvas.getContext('2d');if(!ctx)return;ctx.clearRect(0,0,canvas.width,canvas.height);screen=[];if(!preview)return;
  const rotate=p=>{const x=p[0]*Math.cos(yaw)-p[1]*Math.sin(yaw),y=p[0]*Math.sin(yaw)+p[1]*Math.cos(yaw);return[x,y*Math.sin(pitch)-p[2]*Math.cos(pitch),y*Math.cos(pitch)+p[2]*Math.sin(pitch)];};
  let lo=[Infinity,Infinity],hi=[-Infinity,-Infinity];const include=p=>{const q=rotate(p);for(let j=0;j<2;j++){lo[j]=Math.min(lo[j],q[j]);hi[j]=Math.max(hi[j],q[j]);}};
  preview.context.vertices.forEach(include);preview.holes.forEach(row=>row.boundary.forEach(include));
  if(!Number.isFinite(lo[0]))return;const scale=Math.min((canvas.width-70)/Math.max(.01,hi[0]-lo[0]),(canvas.height-70)/Math.max(.01,hi[1]-lo[1]))*zoom;
  const project=p=>{const q=rotate(p);return[canvas.width/2+(q[0]-(lo[0]+hi[0])/2)*scale,canvas.height/2+(q[1]-(lo[1]+hi[1])/2)*scale,q[2]];};
  const triangles=preview.context.faces.map(face=>({points:face.map(i=>project(preview.context.vertices[i])),inferred:false}));
  for(const row of preview.holes)if(selected.has(row.id)&&!dirty)for(const face of row.cap_faces)triangles.push({points:face.map(i=>project(row.boundary[i])),inferred:true});
  triangles.sort((a,b)=>b.points.reduce((n,p)=>n+p[2],0)-a.points.reduce((n,p)=>n+p[2],0));
  for(const triangle of triangles){ctx.beginPath();triangle.points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.fillStyle=triangle.inferred?'rgba(255,137,55,.88)':'rgba(145,171,187,.16)';ctx.strokeStyle=triangle.inferred?'#ffb16b':'rgba(125,154,172,.2)';ctx.lineWidth=triangle.inferred?1:.5;ctx.fill();ctx.stroke();}
  if(!preview.context.faces.length){ctx.fillStyle='#8194a5';for(const p of preview.context.vertices){const q=project(p);ctx.fillRect(q[0],q[1],1.5,1.5);}}
  for(const row of preview.holes){const points=row.boundary.map(project);screen.push({id:row.id,points,ordered:row.boundary_ordered});if(!row.boundary_ordered){ctx.fillStyle='#ee96ab';for(const p of points)ctx.fillRect(p[0]-2,p[1]-2,4,4);continue;}ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));if(row.reason_code!=='branched_boundary')ctx.closePath();ctx.strokeStyle=selected.has(row.id)?'#ff9d49':row.status==='repairable'?'#55d5eb':row.status==='preserved'?'#e8c879':'#ee96ab';ctx.lineWidth=row.id===highlight?4:2;ctx.stroke();}
 }
 const canvas=$r('canvas');
 canvas.addEventListener('pointerdown',event=>{drag={x:event.clientX,y:event.clientY,start:[event.clientX,event.clientY],moved:false};canvas.setPointerCapture?.(event.pointerId);});
 canvas.addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(event.clientX-drag.start[0],event.clientY-drag.start[1])>4)drag.moved=true;if(drag.moved){yaw+=dx*.01;pitch=Math.max(-1.5,Math.min(1.5,pitch+dy*.01));draw();}drag.x=event.clientX;drag.y=event.clientY;});
 canvas.addEventListener('pointerup',event=>{const previous=drag;drag=null;if(!previous||previous.moved||pending)return;const rect=canvas.getBoundingClientRect(),q=[(event.clientX-rect.left)*canvas.width/rect.width,(event.clientY-rect.top)*canvas.height/rect.height];let closest=null,best=18;for(const row of screen)for(let i=0;i<row.points.length;i++){const a=row.points[i],b=row.ordered?row.points[(i+1)%row.points.length]:a,dx=b[0]-a[0],dy=b[1]-a[1],t=Math.max(0,Math.min(1,((q[0]-a[0])*dx+(q[1]-a[1])*dy)/(dx*dx+dy*dy||1))),distance=Math.hypot(q[0]-a[0]-t*dx,q[1]-a[1]-t*dy);if(distance<best){best=distance;closest=row.id;}}if(closest)pick(closest);});
 canvas.addEventListener('pointercancel',()=>drag=null);canvas.addEventListener('wheel',event=>{event.preventDefault();zoom=Math.max(.2,Math.min(8,zoom*Math.exp(-event.deltaY*.001)));draw();},{passive:false});
 // Existing repair buttons now open the same reviewable local-cap workflow.
 for(const button of document.querySelectorAll('[data-mesh-refine="fill_small"],[data-mesh-refine="watertight"]')){button.textContent='Preview / repair holes';button.onclick=()=>open();if(button.dataset.meshRefine==='watertight')button.hidden=true;}
 const legacySize=document.getElementById('mesh-refine-hole');if(legacySize)legacySize.closest('label').hidden=true;
 const size=document.getElementById('mesh-hole-size');if(size){size.max='5';size.title='Maximum overall extent in metres: largest 3D distance between boundary vertices. Portals and unsupported large gaps are preserved.';const label=size.closest('label');if(label?.firstChild?.nodeType===3)label.firstChild.textContent='Maximum hole extent · m';}
 const handling=document.getElementById('mesh-hole-handling');if(handling){const option=[...handling.options].find(x=>x.value==='watertight');if(option)option.textContent='Conservative local caps · preserve portals';}
 const engine=document.getElementById('mesh-engine');if(engine&&handling)engine.addEventListener('change',()=>{if(engine.value==='watertight_tunnel')handling.value='fill_small';});
 window.MeshRepairUI={open};
})();
