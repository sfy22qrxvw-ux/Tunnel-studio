/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
window.ProjectBrowser=(()=>{
 const el=id=>document.getElementById(id);
 let version=0,selectionVersion=0,controller=null,observer=null,scene=null,loaded=null,selected=null,callbacks=null;
 let records=[],browseId=null,pending=0,queue=[],memo=new Map();
 const leica=p=>p?.kind==='leica_register360';
 const date=value=>{const d=new Date(value);return value&&Number.isFinite(d.getTime())?d.toLocaleString():'Date unavailable';};
 function card(p,external=false){
  const key=external?p.key:p.project_id;
  return `<article class="saved-project-row project-card" data-project-row="${esc(key)}">
   <button type="button" class="project-thumbnail" data-project-preview="${esc(key)}" aria-label="Preview ${esc(p.project_title)}"><canvas width="320" height="170" aria-hidden="true"></canvas><span class="project-thumbnail-status">Preparing overview…</span></button>
   <div class="project-card-copy"><b class="project-card-title">${esc(p.project_title)}</b><span class="project-card-date">${leica(p)?'Metadata modified · ':''}${esc(date(p.saved_at))}</span>${leica(p)?`<small class="project-card-name-source">${esc(p.title_source==='project_metadata'?'Saved project title':p.title_source==='site_map'?'Site-map label · project title unavailable':'Project title needs review')}</small>`:''}
   <span class="project-size">${external?esc(leica(p)?'Leica REGISTER 360 · '+p.setups+' setups':p.kind==='portable'?'Portable project':'Saved working project'):esc(p.size_summary||'Saved project')}${p.active?' · OPEN · protected':''}</span></div>
   <div class="project-card-actions">${external?'':`<label class="project-select" title="${p.active?'Open project is protected':'Select for deletion review'}"><input type="checkbox" data-project-check="${esc(key)}" ${p.active?'disabled':''}><span class="sr-only">Select ${esc(p.project_title)}</span></label>`}
   <button type="button" class="project-open" ${leica(p)&&!p.import_available?'disabled title="A supported setup database is unavailable in this folder"':''} ${external?'data-project-path':'data-project'}="${esc(external?p.path:key)}">${leica(p)?'Import setups…':external?'Open copy…':'Open project'}</button>
   ${external?'':`<button type="button" class="project-delete danger" data-delete-project="${esc(key)}" ${p.active?'disabled':''}>${p.active?'In use':'Delete…'}</button>`}</div></article>`;
 }
 function layout(rows){return `<section id="project-gallery" aria-label="Saved projects">
  <div class="project-search-tools"><label>Search projects<input id="project-search" type="search" placeholder="Search by project name" autocomplete="off"></label><button type="button" id="project-browse-folder">Browse a project folder…</button><button type="button" id="project-browser-back" hidden>Saved projects here</button></div>
  <details class="project-folder-path"><summary>Enter a folder path</summary><div><input id="project-folder-path" aria-label="Folder containing projects" placeholder="e.g. D:\\Tunnel surveys" spellcheck="false"><button type="button" id="project-folder-search">Search folder</button></div></details>
  <p id="project-search-status" role="status"></p><div class="project-browser-layout"><div id="project-gallery-cards" class="project-gallery-cards">${rows}</div>
  <section class="project-preview-panel" aria-label="Selected project 3D overview"><div class="project-preview-heading"><span class="eyebrow">PROJECT OVERVIEW</span><h3 id="project-preview-title">Select a project</h3><p id="project-preview-subtitle">Preview it before opening.</p></div>
  <div class="project-preview-view"><canvas id="project-preview-canvas" aria-label="Saved project 3D preview"></canvas><span id="project-preview-empty">Select a project card to see its overview.</span><div class="project-preview-nav"><button type="button" data-project-view="iso">3D</button><button type="button" data-project-view="top">Top</button><button type="button" data-project-view="front">Front</button><button type="button" data-project-view="fit">Fit</button></div></div>
  <label id="project-leica-database-label" hidden>Saved database<select id="project-leica-database"><option value="">Choose a saved database</option></select></label><p id="project-preview-info" role="status"></p><p class="tiny">Drag to orbit · Shift-drag to pan · Wheel to zoom.</p>
  <button type="button" id="project-preview-open" class="primary" disabled>Open project</button><details><summary>Project details</summary><p id="project-preview-location" class="project-path"></p><p id="project-preview-notes" class="tiny"></p></details></section></div></section>`;}
 function keyFor(p){return browseId?p.key:p.project_id;}
 function payload(p,thumbnail=false){return {action:'preview',...(browseId?{browse_id:browseId,key:p.key}:{id:p.project_id}),...(p.preview_database?{database:p.preview_database}:{}),thumbnail};}
 async function fetchPreview(p,thumbnail,token){
  const key=(browseId||'local')+':'+keyFor(p)+':'+(p.preview_database||'')+':'+thumbnail;
  if(memo.has(key))return memo.get(key);
  const request=api('/api/project-browser',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json','X-Tunnel-Token':window.TUNNEL_TOKEN},body:JSON.stringify(payload(p,thumbnail))});
  memo.set(key,request);while(memo.size>32)memo.delete(memo.keys().next().value);
  try{return await request;}catch(error){if(token===version)memo.delete(key);throw error;}
 }
 function thumbnail(canvas,preview){
  const ctx=canvas.getContext('2d');if(!ctx)return;const w=canvas.width,h=canvas.height;ctx.fillStyle='#102832';ctx.fillRect(0,0,w,h);
  const yaw=-.85,pitch=.37,right=[Math.cos(yaw),-Math.sin(yaw),0],up=[-Math.sin(yaw)*Math.sin(pitch),-Math.cos(yaw)*Math.sin(pitch),Math.cos(pitch)];
  const project=p=>[p[0]*right[0]+p[1]*right[1],p[0]*up[0]+p[1]*up[1]+p[2]*up[2]];
  const points=preview.points||[],lines=preview.lines||[],rows=[...points,...lines];if(!rows.length)return;
  let lo=[Infinity,Infinity],hi=[-Infinity,-Infinity];for(const p of rows){const q=project(p);for(let j=0;j<2;j++){lo[j]=Math.min(lo[j],q[j]);hi[j]=Math.max(hi[j],q[j]);}}
  const scale=Math.min((w-28)/Math.max(.01,hi[0]-lo[0]),(h-24)/Math.max(.01,hi[1]-lo[1])),centre=lo.map((v,i)=>(v+hi[i])/2),pixel=p=>{const q=project(p);return [w/2+(q[0]-centre[0])*scale,h/2-(q[1]-centre[1])*scale];};
  for(let i=0;i<lines.length;i+=2){const a=pixel(lines[i]),b=pixel(lines[i+1]);ctx.strokeStyle='#69c5cb';ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();}
  const step=Math.max(1,Math.ceil(points.length/1600));for(let i=0;i<points.length;i+=step){const p=points[i],q=pixel(p);ctx.fillStyle=`rgb(${p.slice(3).map(v=>Math.round(v*255)).join(',')})`;ctx.fillRect(q[0],q[1],1.5,1.5);}
 }
 function pump(){while(pending<2&&queue.length){const task=queue.shift();pending++;task().finally(()=>{pending--;pump();});}}
 function watchCards(){
  observer?.disconnect();queue=[];const token=version;
  observer=new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;observer.unobserve(entry.target);const button=entry.target,p=records.find(p=>keyFor(p)===button.dataset.projectPreview);if(!p)continue;
   queue.push(async()=>{if(token!==version)return;try{const r=await fetchPreview(p,true,token);if(token!==version||!button.isConnected)return;paintCard(button,selected===p&&loaded?loaded:r.preview,leica(p));}catch(error){if(token===version&&error.name!=='AbortError')button.querySelector('span').textContent='Overview unavailable';}});
  }pump();},{root:el('project-dialog'),rootMargin:'120px'});
  for(const button of el('project-gallery-cards').querySelectorAll('[data-project-preview]'))observer.observe(button);
 }
 function paintCard(button,preview,isLeica){
  thumbnail(button.querySelector('canvas'),preview);
  button.querySelector('span').textContent=preview.quality==='sampled'?(isLeica?'Point cloud preview':'3D display overview'):preview.quality==='bounds_only'?'Geometry bounds':preview.quality==='setup_layout'?'Saved setup layout':'No geometry saved';
 }
 function fit(){
  if(!scene?.gl||!loaded)return;const rows=[...(loaded.points||[]),...(loaded.lines||[])];if(!rows.length)return;
  const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const p of rows)for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],p[j]);hi[j]=Math.max(hi[j],p[j]);}
  scene.target=lo.map((v,i)=>(v+hi[i])/2);
  const aspect=scene.c.clientWidth/Math.max(1,scene.c.clientHeight),basis=scene.basis();
  let width=0,height=0;
  for(const p of rows){const d=p.slice(0,3).map((v,i)=>v-scene.target[i]);width=Math.max(width,Math.abs(dot3(d,basis.right)));height=Math.max(height,Math.abs(dot3(d,basis.up)));}
  scene.scale=Math.max(.1,width*1.12,height*aspect*1.16);scene.draw();
 }
 const dot3=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
 function show(preview){
  loaded=preview;const canvas=el('project-preview-canvas'),isLeica=leica(selected);
  if(!scene){scene=new CloudScene(canvas);scene.canNavigate=()=>true;}
  if(scene.gl){scene.upload(scene.buffer,new Float32Array(preview.points.flat()));scene.pointCount=preview.points.length;scene.upload(scene.lines,new Float32Array(preview.lines.flat()));scene.lineCount=preview.lines.length/1;scene.yaw=-.85;scene.pitch=.37;scene.initialised=true;fit();}
  el('project-preview-empty').hidden=!!scene.gl&&!!(preview.points.length||preview.lines.length);
  el('project-preview-empty').textContent=scene.gl?(isLeica?preview.scan_message:'This project has no visible saved geometry.'):'WebGL is unavailable. The project can still be opened.';
  let message;
  if(isLeica){
   const button=el('project-gallery-cards').querySelector('.project-preview-selected [data-project-preview]');
   if(button)paintCard(button,preview,true);
  }
  if(isLeica&&preview.quality==='sampled'){
   message=`Point cloud preview · ${fmt(preview.points.length,0)} measured points from ${preview.sampled_setups} of ${preview.item_count} setups. ${preview.scan_message}.`;
  }else if(isLeica){
   message=preview.setup_count?`${preview.setup_count} saved setup locations · Setup layout only. ${preview.scan_message}`:preview.scan_message;
  }else{
   message=preview.quality==='sampled'?`${fmt(preview.points.length,0)} display points · ${fmt(preview.source_points,0)} stored scan observations before exclusions. This is a sampled overview.`:preview.quality==='bounds_only'?'Geometry bounds only · this older project has no available saved point overview.':'No visible scan or model geometry is saved.';
  }
  el('project-preview-info').textContent=message;
  el('project-preview-notes').textContent=[preview.note,...(preview.warnings||[])].join(' ');
 }
 async function select(p){
  if(!p)return;selected=p;const token=version,selectionToken=++selectionVersion,key=keyFor(p);el('project-preview-open').disabled=false;el('project-preview-title').textContent=p.project_title;el('project-preview-subtitle').textContent=date(p.saved_at);el('project-preview-info').textContent='Loading saved overview…';el('project-preview-location').textContent=p.path||p.project_file||'Saved in the current data folder.';el('project-preview-open').textContent=leica(p)?'Import setups…':browseId?'Open project copy…':'Open project';el('project-preview-open').disabled=leica(p)&&!p.import_available;
  const label=el('project-leica-database-label'),choice=el('project-leica-database');label.hidden=!leica(p)||(p.databases||[]).length<2;if(leica(p)){choice.innerHTML='<option value="">Choose a saved database</option>'+(p.databases||[]).map((d,i)=>`<option value="${esc(d.path)}">${esc(d.kind==='deliverable'?'Deliverable':'Saved version')} ${i+1} · ${esc(date(d.modified_at))}</option>`).join('');choice.value=p.preview_database||'';choice.onchange=()=>{p.preview_database=choice.value;select(p);};}
  for(const row of el('project-gallery-cards').querySelectorAll('[data-project-row]'))row.classList.toggle('project-preview-selected',row.dataset.projectRow===key);
  loaded=null;if(scene){scene.clear();scene.upload(scene.buffer,new Float32Array());scene.upload(scene.lines,new Float32Array());}
  el('project-preview-empty').hidden=false;el('project-preview-empty').textContent=leica(p)&&(p.preview_database||p.databases?.length===1)?'Reading a sample of the scan points…':'Loading saved 3D overview…';
  try{const r=await fetchPreview(p,false,token);if(token!==version||selectionToken!==selectionVersion||selected!==p)return;show(r.preview);}catch(error){if(token===version&&selectionToken===selectionVersion&&selected===p&&error.name!=='AbortError'){el('project-preview-info').textContent=error.message;el('project-preview-empty').textContent='Overview unavailable. You can still open the project.';}}
 }
 function open(p){if(!p||busy)return;if(leica(p)){if(!p.import_available)return;const path=p.preview_database||(p.databases?.length===1?p.databases[0].path:p.path);el('project-dialog').close();return window.ScanRegistrationUI?.openLeica(path);}if(browseId)return callbacks.openPath(p.path);return callbacks.openNamed(p.project_id);}
 function bindCards(){
  for(const b of el('project-gallery-cards').querySelectorAll('[data-project-preview]')){const p=records.find(p=>keyFor(p)===b.dataset.projectPreview);b.onclick=()=>select(p);b.ondblclick=()=>open(p);}
  for(const b of el('project-gallery-cards').querySelectorAll('[data-project-path]'))b.onclick=()=>open(records.find(p=>p.path===b.dataset.projectPath));
  el('project-search').value='';el('project-search').oninput=()=>{const q=el('project-search').value.trim().toLocaleLowerCase();let shown=0;for(const row of el('project-gallery-cards').querySelectorAll('[data-project-row]')){const p=records.find(p=>keyFor(p)===row.dataset.projectRow);row.hidden=!!q&&!p?.project_title.toLocaleLowerCase().includes(q);if(!row.hidden)shown++;}el('project-search-status').textContent=shown+' of '+records.length+' projects';};watchCards();
 }
 async function browse(folder){
  if(busy||!folder?.trim())return;const token=++version;controller?.abort();controller=new AbortController();memo.clear();observer?.disconnect();queue=[];el('project-search-status').textContent='Reading project names and saved overviews…';
  try{
   const data=await post('/api/project-browser',{action:'browse',folder});if(token!==version||!el('project-dialog').open)return;
   browseId=data.browse_id;records=data.projects;selected=null;loaded=null;el('project-gallery-cards').innerHTML=records.map(p=>card(p,true)).join('')||'<p>No supported Tunnel Studio or REGISTER 360 project metadata found in this folder. Choose the project folder or its parent.</p>';el('project-browser-back').hidden=false;el('project-folder-path').value=data.folder;const toolbar=document.querySelector('.project-bulk-toolbar');if(toolbar)toolbar.hidden=true;
   el('project-search-status').textContent=records.length+' projects · '+data.folder+(data.limited?' · Search reached its metadata/folder limit. Choose a narrower folder.':'')+(data.warnings.length?' · '+data.warnings.length+' metadata notice(s).':'');el('project-preview-open').disabled=true;el('project-preview-info').textContent='Opening a copy uses the normal checked import and leaves the original saved file unchanged.';bindCards();if(records.length)select(records[0]);
  }catch(error){if(token===version)el('project-search-status').textContent=error.message;}
 }
 function mount(projects,options){
  dispose();callbacks=options;records=projects;browseId=null;controller=new AbortController();
  if(scene&&scene.c!==el('project-preview-canvas'))el('project-preview-canvas').replaceWith(scene.c);
  // Reuse one isolated scene/canvas for the whole session. This avoids extra
  // WebGL contexts and navigation/resize listeners on repeated browsing.
  el('project-preview-open').onclick=()=>open(selected);
  for(const b of document.querySelectorAll('[data-project-view]'))b.onclick=()=>{if(!scene)return;const v=b.dataset.projectView;if(v==='fit')return fit();scene.yaw=v==='top'||v==='front'?0:-.85;scene.pitch=v==='top'?Math.PI/2:v==='front'?0:.37;fit();};
  el('project-folder-search').onclick=()=>browse(el('project-folder-path').value);el('project-folder-path').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();browse(e.target.value);}};
  el('project-browse-folder').onclick=async()=>{const choose=window.pywebview?.api?.choose_project_folder;if(!choose){el('project-folder-path').closest('details').open=true;el('project-folder-path').focus();return;}try{const folder=await choose();if(folder)await browse(folder);}catch(error){el('project-search-status').textContent=error.message;}};
  el('project-browser-back').onclick=()=>callbacks.reload();bindCards();if(records.length)select(records.find(p=>p.active)||records[0]);
 }
 function dispose(){
  version++;controller?.abort();observer?.disconnect();queue=[];memo.clear();selected=null;loaded=null;
  if(scene){scene.cancelNavigation?.();clearTimeout(scene.navigationTimer);cancelAnimationFrame(scene.navigationFrame);scene.navigationFrame=null;scene.clear();if(scene.gl){scene.upload(scene.buffer,new Float32Array());scene.upload(scene.lines,new Float32Array());}}
 }
 return {card,layout,mount,dispose};
})();
