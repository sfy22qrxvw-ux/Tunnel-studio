/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const el=id=>document.getElementById('shape-'+id), count=n=>Number(n||0).toLocaleString('en-GB');
 const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const panel=document.createElement('section');panel.id='tab-shape-selection';panel.className='tab feature-panel';
 const field=(id,title,value,min,max,help)=>`<label><span>${title}</span><input id="shape-${id}" type="number" value="${value}" min="${min}" max="${max}" step="any" title="${help}"><small>${help}</small></label>`;
 panel.innerHTML=`<header class="feature-heading"><span class="feature-eyebrow">WHOLE-CLOUD SELECTION</span><h2>Find similar shapes</h2><p class="feature-hint">Draw one bolt pocket, fitting or short section of a pipe or duct. Find similar measured shapes in a limit box or across the registered cloud, then review and remove or separate them.</p></header>
 <section class="feature-step"><h3><span>1</span> Build your shape library</h3><label>Saved shape type<select id="shape-library"><option value="">Drawn example only · unsaved</option></select></label><div class="feature-actions"><input id="shape-type-name" maxlength="80" placeholder="e.g. Pocket 1" aria-label="Shape type name"><button id="shape-create">Create type</button><button id="shape-rename">Rename type</button></div><p id="shape-library-counts" class="feature-hint">Save several confirmed examples of the same object type. Different types belong in different library entries.</p><label class="feature-check"><input id="shape-use-library" type="checkbox" checked>Search with all confirmed examples of the selected type</label><details><summary>Saved examples and management</summary><p class="feature-hint">Orange object points and green wall context are stored independently. Examples remain after source deletion and are available in other projects on this computer. Manage an incorrect teaching example below; workspace Undo also reverses recent teaching.</p><div id="shape-examples"></div><div class="feature-actions"><button id="shape-refresh-library">Refresh library</button><button id="shape-archive">Archive type</button><button id="shape-restore">Restore archived type</button></div><p id="shape-library-folder" class="feature-hint"></p></details>
 <label>Source registered cloud · also used for wall context<select id="shape-source"></select></label><details><summary>Use pockets already separated into a scan</summary><label>Separated pocket scan<select id="shape-separated-source"></select></label><p class="feature-hint">Choose the main tunnel above for nearby lining context. Choose your existing pocket scan here, including hidden scans. Each disconnected compact group is fitted separately. Only use this when you have checked that the scan contains the selected object type.</p><button id="shape-add-separated">Confirm pockets from separated scan</button><p id="shape-import-summary" class="feature-hint"></p></details><div class="feature-actions"><button id="shape-box">Draw box</button><button id="shape-lasso">Draw lasso</button><button id="shape-polygon">Draw polygon</button></div><button id="shape-use">Use current drawn area</button><button id="shape-save-example">Confirm drawn object and save example</button><p id="shape-example" class="feature-hint">Draw the complete pocket with a little nearby wall. The object is separated from the fitted wall context when saved. The teaching crop describes one object. Choose the search area below.</p><p class="feature-hint"><b>No point-count cap.</b> Original point IDs are processed in blocks within the chosen search area.</p></section>
 <section class="feature-step"><h3><span>2</span> Set its lining offset</h3><label>Side of the lining<select id="shape-side"><option value="either">Either side · bumps and recesses</option><option value="recess">Recessed · below the wall</option><option value="raised">Raised · into the tunnel</option></select></label>
 ${field('minimum','Minimum distance from lining · mm',5,.5,5000,'Points closer to the fitted wall remain unselected. Use a value above the wall roughness.')}
 ${field('maximum','Maximum distance from lining · mm',2000,1,10000,'Only points in this lining-offset range can be matched and selected.')}
 <label>Example type<select id="shape-continuation"><option value="auto">Infer compact or continuing shape</option><option value="compact">Compact object · bolt pocket or fitting</option><option value="long">Continuing service · pipe or duct section</option></select><small>A continuing service can extend beyond the two ends of the example; its width and depth still need to match.</small></label>
 <details class="feature-advanced"><summary>Match settings</summary>
 <label>Measured lining reference<select id="shape-reference"><option value="local">Follow nearby measured wall · recommended</option><option value="longitudinal">Longitudinal profile · previous method</option></select><small>The local reference follows gradual wall changes at the crown, walls and invert. Areas without reliable measured wall support remain unselected.</small></label>
 ${field('similarity','Minimum geometric match · %',75,20,99.9,'Higher values require closer shape and depth agreement. This is a similarity score, not a confidence percentage.')}
 ${field('depth','Depth variation between matches · mm',15,.5,1000,'Allows this variation in the depth pattern relative to the fitted lining.')}
 ${field('grid','Shape grid size · mm',10,2,100,'Working resolution for the shape map, not a point limit. Final selection tests all original observations.')}
 <label class="feature-check"><input id="shape-rotations" type="checkbox" checked>Allow different orientations on the lining</label><label class="feature-check"><input id="shape-view-filters" type="checkbox">Also use hidden and isolated areas</label><p class="feature-hint">Compact objects use their principal directions, including oblique rotations. Continuing services use quarter turns. This option applies hidden areas and isolation in addition to the search area below.</p></details>
 <label class="feature-check"><input id="shape-include-reviewed" type="checkbox">Show previously reviewed matches<small>Current match settings and incorrect-example filters still apply.</small></label>
 <label>Search area<select id="shape-search-scope"><option value="auto">Use the saved limit box</option><option value="whole">Ignore limit box</option></select></label>
 <p id="shape-scope-note" class="feature-hint"></p>
 <div class="feature-actions"><button id="shape-run" class="primary">Find across whole cloud</button><button id="shape-stop" hidden>Stop</button></div><p id="shape-status" role="status" aria-live="polite"></p></section>
 <section id="shape-review" class="feature-step" hidden><h3><span>3</span> Review matching points</h3><p id="shape-summary"></p><p class="feature-hint"><b>Orange:</b> selected. <b>Cyan:</b> unselected match. <b>Grey:</b> surrounding scan. These are geometric candidates, not confirmed object identities.</p><div id="shape-warnings" class="feature-warnings"></div><div class="feature-actions"><button type="button" id="shape-pick-3d" aria-pressed="false">Pick matches in 3D</button><button id="shape-all">Select all matches</button><button id="shape-none">Clear selection</button></div>
 <div class="feature-actions"><button id="shape-add-review-box">Add box to unselect</button><button id="shape-apply-review-boxes">Unselect boxed matches</button><button id="shape-undo-review-box">Undo last box</button></div><p id="shape-review-box-status" class="feature-hint" role="status" aria-live="polite">Draw boxes around displayed match points. Each box queues whole matches. Orbit between boxes to check overlapping lining.</p><p class="feature-hint">Click a match name to zoom in and load its original points. Checkboxes control removal selection independently.</p><p id="shape-pick-status" class="feature-hint" role="status" aria-live="polite" hidden></p><section id="shape-inspect-panel" class="feature-step" hidden><h4 id="shape-inspect-title">Inspect pocket</h4><p id="shape-inspect-status" role="status" aria-live="polite"></p><div class="feature-actions"><button id="shape-inspect-previous">Previous pocket</button><button id="shape-inspect-next">Next pocket</button><button id="shape-inspect-back">Back to matches</button><button id="shape-inspect-stop" hidden>Stop loading detail</button></div><label class="feature-check"><input type="checkbox" id="shape-inspect-highlight" checked>Highlight pocket · turn off for measured intensity</label><p class="feature-hint">Inspecting does not change the checkboxes or teach the library. Removal and teaching still apply to all selected groups. Nearby lining is a context sample.</p></section><details id="shape-coverage" class="shape-coverage" hidden><summary>Search coverage · crown, walls and invert</summary><p id="shape-coverage-note" class="feature-hint"></p><div id="shape-coverage-grid" class="shape-coverage-grid"></div></details><section class="shape-candidates" aria-labelledby="shape-candidate-title">
  <div class="shape-candidate-heading"><div><span class="feature-eyebrow">MATCH CANDIDATES</span><h4 id="shape-candidate-title" tabindex="-1">All regions</h4></div><button type="button" id="shape-all-regions" hidden>Show all regions</button></div>
  <label for="shape-sort">Sort candidates<select id="shape-sort"><option value="best">Geometric match · highest first</option><option value="worst">Geometric match · lowest first</option><option value="most">Point count · highest first</option><option value="fewest">Point count · lowest first</option><option value="station">Position along scan axis</option></select></label>
  <p id="shape-page" class="feature-hint" role="status"></p><div id="shape-groups" class="feature-group-list"></div>
  <div class="feature-actions"><button id="shape-previous">Previous</button><button id="shape-next">Next</button></div>
 </section>
 <details class="feature-advanced"><summary>Refine with a drawn area</summary><p class="feature-hint">Draw a box, lasso or polygon over the preview, then keep or exclude that area from the exact original-point selection.</p><div class="feature-actions"><button id="shape-keep">Keep within drawn area</button><button id="shape-exclude">Exclude drawn area</button></div></details>
 <div class="feature-actions"><button id="shape-teach">Confirm selected as correct examples</button><button id="shape-reject">Selected are incorrect matches</button></div><label class="feature-check"><input id="shape-teach-removal" type="checkbox" checked>Save confirmed selected objects to the chosen type when removing or separating</label><p class="feature-hint">Teaching requires complete object groups. Incorrect matches teach what to reject and remain in the scan. Detection alone never teaches the library.</p><div class="feature-actions"><button id="shape-separate">Separate selected to new scan</button><button id="shape-remove" class="danger">Remove selected points</button><button id="shape-normal">Restore normal view</button></div><p class="feature-hint">Edits use full-density original points and support Undo. Remove and teach saves independent examples first; one workspace Undo reverses both the edit and that teaching. Removing an object does not reconstruct the lining behind it.</p></section>`;
 document.querySelector('.view-shell').append(panel);
 let example=null,result=null,running=false,dirty=true,picking=false,library={classes:[]};
 let displayed=null,browsing=false,browseEpoch=0;
 let reviewDetail=null,detailRequest=null;
 const reviewBoxes=[],reviewBoxIds=new Set(),maxReviewBoxes=32;
 let inspection=null,inspectionLoading=false,inspectionPhase='',inspectionEpoch=0,inspectionBusyEpoch=null,inspectionAbort=null,overviewView=null,inspectionHighlight=true;
 const regionName=()=>result?.region==null?'All regions':result.coverage[result.region].region;
 const selectedType=()=>library.classes.find(x=>x.id===el('library').value);
 const librarySearch=()=>!!(el('use-library').checked&&selectedType()?.active&&selectedType()?.positive);
 const scopeNames={limit_box:'saved limit box',view:'current view filters',whole:'whole retained cloud'};
 function searchArea(){
  const source=window.JobUI.scanItems().find(scan=>scan.id===el('source').value);
  if(el('search-scope').value!=='whole'&&source?.limit_box)return 'limit_box';
  return el('view-filters').checked?'view':'whole';
 }
 const status=(message,error=false)=>{el('status').textContent=message;el('status').style.color=error?'var(--danger,#b64c45)':'';};
 function controls(){
  const lock=running||!!busy||browsing,editingLocked=lock||inspectionLoading;
  for(const id of ['source','box','lasso','polygon','use','side','minimum','maximum','continuation','similarity','depth','grid','rotations','view-filters','reference','include-reviewed','search-scope'])el(id).disabled=editingLocked;
  for(const id of ['library','type-name','create','refresh-library','use-library'])el(id).disabled=editingLocked;
  for(const id of ['rename','archive','restore'])el(id).disabled=editingLocked||!selectedType();
  el('archive').hidden=!selectedType()?.active;el('restore').hidden=!selectedType()||selectedType().active;
  el('save-example').disabled=editingLocked||!example||!selectedType()?.active||!el('source').value;
  el('separated-source').disabled=editingLocked;el('add-separated').disabled=editingLocked||!selectedType()?.active||!el('separated-source').value||el('source').value===el('separated-source').value;
  const area=searchArea();
  el('run').textContent=area==='limit_box'?'Find inside limit box':area==='view'?'Find with view filters':'Find across whole cloud';
  el('scope-note').textContent=area==='limit_box'
   ?'Only retained points inside the source scan’s saved limit box will be searched. Include both walls and the crown so the lining can be fitted.'
   :`Search area: ${scopeNames[area]}.`;
  el('run').disabled=editingLocked||(!example&&!librarySearch())||!el('source').value;el('stop').hidden=!running;
  for(const id of ['all','none','keep','exclude'])el(id).disabled=lock||inspectionLoading||!result||dirty||result.stale;
  el('remove').disabled=el('separate').disabled=lock||reviewBoxes.length>0||inspectionLoading||dirty||!result||result.stale||!result.statistics.selected_points;
  el('teach').disabled=el('reject').disabled=el('remove').disabled||!selectedType()?.active||result?.statistics.continuing_service;
  el('teach-removal').disabled=lock||!selectedType()?.active||result?.statistics.continuing_service;
  el('remove').textContent=selectedType()?.active&&el('teach-removal').checked?'Remove and teach selected':'Remove selected points';
  el('examples').querySelectorAll('button').forEach(x=>x.disabled=editingLocked);
  el('previous').disabled=editingLocked||!result||result.page===0;el('next').disabled=editingLocked||!result||(result.page+1)*40>=result.group_count;
  el('sort').disabled=editingLocked||!result;
  el('all-regions').disabled=editingLocked||!result;
  for(const card of el('coverage-grid').querySelectorAll('[data-shape-region]')){
   card.disabled=editingLocked||result?.coverage?.[+card.dataset.shapeRegion]?.candidate_count==null;
  }
  el('groups').querySelectorAll('input').forEach(x=>x.disabled=lock||inspectionLoading||dirty||result?.stale);
  el('groups').querySelectorAll('[data-shape-inspect]').forEach(x=>x.disabled=lock||dirty||result?.stale||result?.statistics.continuing_service);
  el('inspect-panel').hidden=!inspection&&!inspectionLoading;
  el('inspect-previous').disabled=lock||!inspection?.previous;
  el('inspect-next').disabled=lock||!inspection?.next;
  el('inspect-highlight').disabled=lock||inspectionLoading||!inspection;
  el('inspect-back').disabled=running||(!inspection&&!inspectionLoading);
  el('inspect-stop').hidden=!inspectionLoading;
  const pick=el('pick-3d'),available=hasDisplayedMatches()&&!dirty&&!result?.stale&&!inspection&&!inspectionLoading&&window.JobUI.previewOwner()==='shape';
  if(!available&&!inspection&&!inspectionLoading&&!running)picking=false;
  pick.disabled=lock||!available;
  el('add-review-box').disabled=lock||!available||reviewBoxes.length>=maxReviewBoxes;
  el('apply-review-boxes').disabled=lock||!available||!reviewBoxes.length;
  el('undo-review-box').disabled=lock||!reviewBoxes.length;
  el('review-box-status').textContent=reviewBoxes.length
   ? `${count(reviewBoxes.length)} ${reviewBoxes.length===1?'box':'boxes'} · ${count(reviewBoxIds.size)} matches queued. Apply the boxes before removing or teaching.`
   : 'Draw boxes around displayed match points. Each box queues whole matches. Orbit between boxes to check overlapping lining.';
  pick.setAttribute('aria-pressed',String(picking&&available));
  pick.classList.toggle('chosen',picking&&available);
  el('pick-status').hidden=!picking||!available;
  $('job-canvas').classList.toggle('shape-match-picking',picking&&available);

 }
 function changed(){stopReviewDetail();resetReviewBoxes();dirty=true;controls();if(result)status('Settings changed. Find shapes again before applying an edit.');}
 function render(){
  el('review').hidden=!result;if(!result){controls();return;}
  const s=result.statistics;
  el('summary').textContent=`${count(s.input_points)} original points checked · ${scopeNames[result.search_area]||'search area'} · ${count(result.total_group_count)} geometric groups · ${count(s.selected_points)} selected points across all regions. ${s.library_positive?`${count(s.library_positive)} confirmed examples · ${count(s.library_negative)} incorrect examples.`:`Example: ${count(s.example_candidate_points)} off-lining points.`} ${s.continuing_service?'Continuing-service matching.':'Complete compact-shape matching.'} ${result.reviewed_skipped?`${count(result.reviewed_skipped)} previously reviewed pockets skipped.`:''}`;
  el('warnings').innerHTML=[...(result.stale?['Source changed. Find shapes again before editing.']:[]),...result.warnings].map(x=>`<p>${escape(x)}</p>`).join('');
  el('candidate-title').textContent=regionName();
  el('all-regions').hidden=result.region==null;
  el('sort').value=result.sort;
  el('groups').innerHTML=result.groups.map(g=>{
   const size=result.region==null?count(g.points)+' points':count(g.region_points)+' of '+count(g.points)+' points in this region';
   const score=g.similarity_percent.toFixed(1)+'% geometric match';
   return `<div class="feature-group" ${inspection?.group_id===g.id?'aria-current="true"':''}>
    <input type="checkbox" data-shape-group="${g.id}" aria-label="Select ${escape(g.name)} for removal" ${g.selected?'checked':''}>
    <button type="button" class="shape-inspect-item" data-shape-inspect="${g.id}" title="Zoom to this measured candidate and load original points">
     <strong>${escape(g.name)} · ${g.side==='recess'?'recessed':'raised'}</strong>
     <small class="shape-candidate-score">${score}</small>
     <small>${size} · ${g.station_m.toFixed(2)} m along scan axis${g.partial?' · '+count(g.selected_points)+' selected':''}</small>
     <small>Inspect candidate ↗</small>
    </button>
   </div>`;
  }).join('');
  for(const input of el('groups').querySelectorAll('input'))input.indeterminate=!!result.groups.find(g=>g.id===+input.dataset.shapeGroup)?.partial;
  renderCoverage();
  el('page').textContent=result.group_count
   ? `${result.page*40+1}–${Math.min((result.page+1)*40,result.group_count)} of ${count(result.group_count)} candidates`
   : result.region==null?(result.reviewed_skipped?'No new matches. Turn on Show previously reviewed matches to see those results again.':'No matching groups. Check the example, side, offset range and match settings.'):'No candidates in '+regionName()+'.';
  controls();
 }
 function renderCoverage(){
  const rows=result?.coverage||[];el('coverage').hidden=!rows.length;if(!rows.length)return;
  el('coverage-note').textContent='Choose a region to show its candidates. '+('Search area: '+(scopeNames[result.search_area]||'retained source')+'. ')+(result.lining_reference?.method||'Measured wall reference')+'. Counts describe the detection pass, not a probability or a verified object count.';
  el('coverage-grid').innerHTML=rows.map((row,index)=>{
   const state=!row.processed?'empty':!row.supported?'missing':row.matched?'matched':'unmatched';
   const reason=!row.processed?'No source observations in this region':!row.supported?'Measured wall reference unavailable':!row.offset_candidates?'No measured points outside the chosen wall band':!row.matched?'Offset candidates did not match the saved shapes':'Matched original observations';
   return `<button type="button" class="shape-coverage-card" data-shape-region="${index}" data-state="${state}" aria-pressed="${result.region===index}">
    <strong>${escape(row.region)}</strong><b>${count(row.matched)} matched points</b>
    <span>${count(row.processed)} checked · ${count(row.supported)} with wall support</span>
    <span>${count(row.offset_candidates)} points in the offset range</span><span>${escape(reason)}</span>
    ${row.candidate_count==null?'':`<span class="shape-coverage-action">${count(row.candidate_count)} ${row.candidate_count===1?'candidate':'candidates'} ›</span>`}
   </button>`;
  }).join('');
 }
 function receive(spec){example=JSON.parse(JSON.stringify(spec));el('example').textContent='Example captured. Similar shapes will be found in the chosen search area.';changed();status('Example ready. Set the lining offset, then start the search.');}
 function settings(){
  for(const id of ['minimum','maximum','similarity','depth','grid'])if(!el(id).checkValidity())throw Error('Check '+el(id).closest('label').querySelector('span').textContent+'.');
  if(+el('minimum').value>=+el('maximum').value)throw Error('Maximum lining offset must exceed the minimum.');
  return {min_offset_mm:+el('minimum').value,max_offset_mm:+el('maximum').value,depth_tolerance_mm:+el('depth').value,grid_mm:+el('grid').value,similarity_percent:+el('similarity').value,side:el('side').value,continuation:el('continuation').value,rotations:el('rotations').checked,respect_view:el('view-filters').checked,search_scope:el('search-scope').value||'auto',lining_reference:el('reference').value,include_reviewed:el('include-reviewed').checked};
 }
 function show(){
  if(!result?.preview||result.stale)return;
  stopReviewDetail();
  const full=result.preview;
  displayed=full;
  if(result.region!=null){
   const start=full.points.length-full.match_ids.length;
   const points=full.points.slice(0,start),ids=[];
   full.match_regions?.forEach((region,index)=>{
    if(region===result.region){points.push(full.points[start+index]);ids.push(full.match_ids[index]);}
   });
   displayed={...full,points,match_ids:ids};
  }
  window.JobUI.analysisPreview({...displayed,result_id:result.id,sample_count:displayed.points.length,used:result.statistics.input_points},'shape');
  $('job-preview-info').textContent=overviewInfo();
  window.JobUI.scheduleDetail?.();
 }
 function overviewInfo(){
  const detail=result.region!=null&&result.group_count>0&&!displayed.match_ids.length?' Open a candidate to load its original points.':'';
  return `SHAPE REVIEW · ${regionName()} · ${count(result.group_count)} candidates · orange selected / cyan match / grey context.`+detail;
 }
 function stopReviewDetail(){
  if((reviewDetail||detailRequest)&&displayed&&result&&window.JobUI.previewOwner()==='shape')$('job-preview-info').textContent=overviewInfo();
  detailRequest?.controller.abort();detailRequest=null;
  if(reviewDetail){reviewDetail=null;const scene=window.JobUI.cadScene();scene.clearPointPages();scene.draw();}
 }
 async function loadViewDetail(view,covered,force){
  if(!displayed||!result||dirty||result.stale||running||busy||browsing||inspection||inspectionLoading)return;
  if(!force&&view.scale>2){stopReviewDetail();return;}
  if(!force&&reviewDetail&&view.scale>=reviewDetail.view.scale*.6&&covered(reviewDetail.view))return;
  if(!force&&detailRequest&&view.scale>=detailRequest.view.scale*.6&&covered(detailRequest.view))return;
  detailRequest?.controller.abort();
  const request={view,controller:new AbortController()},id=result.id,revision=result.revision,region=result.region;
  detailRequest=request;
  const params=new URLSearchParams({preview_id:id,revision,detail_view:JSON.stringify(view),...(region==null?{}:{region})});
  const info=$('job-preview-info');
  const loading='SHAPE REVIEW · Loading measured close-up detail…';
  info.textContent=loading;
  try{
   const page=await window.TunnelDisplayBuffers.load('/api/shape-selection?'+params,{signal:request.controller.signal});
   if(detailRequest!==request||result?.id!==id||result?.revision!==revision||result?.region!==region||!covered(view)||!window.JobUI.canLoadReviewDetail())return;
   if(page.review_id!==id||page.revision!==revision||page.match_ids.length!==page.match_offsets.length||
      page.match_offsets.some(i=>!Number.isInteger(i)||i<0||i>=page.sample_count))throw Error('Invalid shape detail.');
   const delta=page.origin.map((value,i)=>value-displayed.origin[i]),packed=page.packed;
   for(let i=0;i<packed.length;i+=6)for(let axis=0;axis<3;axis++)packed[i+axis]+=delta[axis];
   const scene=window.JobUI.cadScene();scene.clearPointPages();
   reviewDetail=null;
   if(page.sample_count){scene.addPointPage(packed);reviewDetail={view,page};}
   info.textContent=`SHAPE REVIEW · ${count(page.sample_count)} measured close-up points · orange selected / cyan match / grey intensity.`;
   controls();
  }catch(error){
   if(detailRequest===request&&error.name!=='AbortError')info.textContent='SHAPE REVIEW · Overview available · close-up detail could not load.';
  }finally{
   if(detailRequest===request){detailRequest=null;if(info.textContent===loading)info.textContent=overviewInfo();}
  }
 }
 function hasDisplayedMatches(){return !!(displayed?.match_ids?.length||reviewDetail?.page.match_ids.length);}
 function forEachDisplayedMatch(visit){
  const {points,match_ids:ids}=displayed,start=points.length-ids.length;
  ids.forEach((id,i)=>{const p=points[start+i];visit(id,p[0],p[1],p[2]);});
  const page=reviewDetail?.page;
  if(page)page.match_offsets.forEach((index,i)=>{const j=index*6,p=page.packed;visit(page.match_ids[i],p[j],p[j+1],p[j+2]);});
 }
 function resetReviewBoxes(){
  window.JobUI.cancelAreaSelection(receiveReviewBox);
  if(!reviewBoxes.length)return;
  reviewBoxes.length=0;updateReviewBoxes();
 }
 function updateReviewBoxes(){
  reviewBoxIds.clear();
  for(const box of reviewBoxes)for(const id of box.ids)reviewBoxIds.add(id);
  window.JobUI.setReviewBoxes(reviewBoxes.map(box=>box.spec));
 }
 function receiveReviewBox(spec){
  window.JobUI.clearSelection();
  if(!hasDisplayedMatches()||dirty||result?.stale||window.JobUI.previewOwner()!=='shape')return;
  const {origin}=displayed,hit=new Set();
  forEachDisplayedMatch((id,x,y,z)=>{
   if(hit.has(id))return;
   const world=[x+origin[0],y+origin[1],z+origin[2]];
   if(window.JobUI.selectionContains(world,spec))hit.add(id);
  });
  if(!hit.size){status('This box contains no displayed match points.',true);return;}
  reviewBoxes.push({spec,ids:hit});updateReviewBoxes();
  controls();status('Box added. Orbit or add another box, then unselect the boxed matches.');
 }
 function pickMatch(pixel){
  if(!picking||el('pick-3d').disabled)return false;
  const scene=window.JobUI.cadScene();
  const width=scene.c.clientWidth,height=scene.c.clientHeight,basis=scene.basis();
  let hit=null;
  forEachDisplayedMatch((id,x,y,z)=>{
   const projected=scene.projectLocal([x,y,z],basis);
   if(!projected||Math.abs(projected[0])>1||Math.abs(projected[1])>1)return;
   const dx=(1+projected[0])*width/2-pixel[0],dy=(1-projected[1])*height/2-pixel[1];
   const distance=Math.hypot(dx,dy),depth=projected[2];
   if(distance>9)return;
   if(!hit||distance<hit.distance-1||Math.abs(distance-hit.distance)<=1&&depth>hit.depth){
    hit={id,distance,depth};
   }
  });
  if(!hit){el('pick-status').textContent='Click orange or cyan points. Zoom in to separate nearby matches.';return true;}
  el('pick-status').textContent='Updating match '+hit.id+'…';
  run('select',{operation:'invert',group_ids:[hit.id]});
  return true;
 }
 function cameraView(){const scene=window.JobUI.cadScene(),origin=window.JobUI.cadOrigin();return {yaw:scene.yaw,pitch:scene.pitch,scale:scene.scale,target:scene.target.map((v,i)=>v+origin[i])};}
 function restoreCamera(view){if(!view)return;const scene=window.JobUI.cadScene(),origin=window.JobUI.cadOrigin();scene.yaw=view.yaw;scene.pitch=view.pitch;scene.scale=view.scale;scene.target=view.target.map((v,i)=>v-origin[i]);scene.draw();}
 function focusPocket(meta){
  const scene=window.JobUI.cadScene(),origin=window.JobUI.cadOrigin();
  if(meta.normal?.length===3&&meta.normal.every(Number.isFinite)){
   let normal=meta.normal.slice();const forward=scene.basis().forward;
   if(normal.reduce((v,x,i)=>v+x*forward[i],0)<0)normal=normal.map(x=>-x);
   scene.yaw=Math.atan2(normal[0],normal[1]);scene.pitch=Math.max(-1.55,Math.min(1.55,Math.asin(Math.max(-1,Math.min(1,normal[2])))));
  }
  scene.target=meta.origin.map((v,i)=>v-origin[i]);const basis=scene.basis(),rect=scene.c.getBoundingClientRect(),aspect=rect.width/Math.max(1,rect.height);
  let left=12,right=rect.width-12,top=12,bottom=rect.height-12;
  // The explorer and tool drawer overlay the canvas. Frame the object in the
  // uncovered area, instead of placing half of it behind the review controls.
  for(const panel of [document.querySelector('.job-sidebar'),document.getElementById('tool-drawer')]){
   if(!panel||getComputedStyle(panel).visibility==='hidden')continue;const p=panel.getBoundingClientRect();
   if(!p.width||!p.height||p.bottom<=rect.top||p.top>=rect.bottom||p.right<=rect.left||p.left>=rect.right)continue;
   if((p.left+p.right)/2<(rect.left+rect.right)/2)left=Math.max(left,p.right-rect.left+12);
   else right=Math.min(right,p.left-rect.left-12);
  }
  if(right-left<140){left=12;right=rect.width-12;}
  const width=Math.max(.05,(right-left)/rect.width),height=Math.max(.05,(bottom-top)/rect.height);let extent=.03;
  for(let bits=0;bits<8;bits++){
   const delta=[0,1,2].map(i=>(bits>>i&1?meta.maximum[i]:meta.minimum[i])-meta.origin[i]);
   const dot=a=>delta.reduce((v,x,i)=>v+x*a[i],0);extent=Math.max(extent,Math.abs(dot(basis.right))/width,Math.abs(dot(basis.up))*aspect/height);
  }
  scene.scale=extent*1.6;const x=(left+right)/rect.width-1,y=1-(top+bottom)/rect.height;
  scene.target=scene.target.map((v,i)=>v-basis.right[i]*x*scene.scale-basis.up[i]*y*scene.scale/aspect);scene.draw();
 }
 function abandonInspection(){stopReviewDetail();inspectionEpoch++;inspectionAbort?.abort();inspectionAbort=null;inspection=null;inspectionLoading=false;inspectionPhase='';window.JobUI.cadScene()?.clearPointPages();}
 async function backToMatches(){
  const preparing=inspectionPhase==='prepare',view=overviewView;abandonInspection();overviewView=null;
  if(preparing){try{await post('/api/cancel-operation',{});await window.JobUI.waitReady();}catch{}finally{setBusy(false);}}
  if(result&&!result.stale){show();restoreCamera(view);}render();status('Returned to the matches. Removal selection is unchanged.');
 }
 async function inspectPocket(ident){
  if(running||busy||browsing||!result||dirty||result.stale)return;
  stopReviewDetail();
  const epoch=++inspectionEpoch;inspectionAbort?.abort();const controller=new AbortController();inspectionAbort=controller;
  const review=result.id,revision=result.revision;let prepared=false;
  if(!overviewView)overviewView=cameraView();inspectionLoading=true;inspectionPhase='prepare';if(inspection)inspection={...inspection,complete:false};
  const current=()=>epoch===inspectionEpoch&&result?.id===review&&result?.revision===revision;
  try{
   inspectionBusyEpoch=epoch;setBusy(true);controls();el('inspect-title').textContent='Inspect pocket '+ident;el('inspect-status').textContent='Loading original measured points…';
   await post('/api/shape-selection',{action:'inspect',preview_id:review,revision,group_id:ident,region:result.region,sort:result.sort});
   const job=await window.JobUI.waitReady(j=>{if(current())el('inspect-status').textContent=j.message||'Preparing pocket detail…';});
   if(inspectionBusyEpoch===epoch){inspectionBusyEpoch=null;setBusy(false);}
   if(!current())return;if(job.cancelled)throw Error('Pocket detail loading stopped. Selection and source data are unchanged.');
   prepared=true;inspectionPhase='pages';
   const meta=await api('/api/shape-inspection?'+reviewParams());if(!current())return;
   if(meta.group_id!==ident||meta.revision!==revision||meta.review_id!==review)throw Error('The inspected pocket changed. Inspect it again.');
   if(meta.original_points*24>192*1024*1024)throw Error('This group exceeds the local graphics memory budget. Inspect an individual compact pocket; full-density selection and export are unchanged.');
   inspection={...meta,loaded_points:0,complete:false};
   if(meta.page!==result.page){await refresh(meta.page,{points:false});if(!current())return;}
   const query=new URLSearchParams({inspection:meta.id,part:'context',start:0,size:meta.page_points});
   const context=await window.TunnelDisplayBuffers.load('/api/shape-inspection?'+query,{signal:controller.signal});if(!current())return;
   window.JobUI.analysisPreview({...context,result_id:review,sample_count:context.sample_count,used:meta.original_points},'shape');
   const scene=window.JobUI.cadScene();scene.pointPagesActive=true;scene.inspectionId=meta.id;window.JobUI.setMode('orbit');focusPocket(meta);
   const message=()=>`Pocket ${ident} · ${count(inspection.loaded_points)} / ${count(meta.original_points)} original points loaded · ${count(meta.context_count)} context sample points. Removal selection: ${count(result.statistics.selected_points)} points across the review.`;
   for(let start=0;start<meta.original_points;start+=meta.page_points){
    const params=new URLSearchParams({inspection:meta.id,part:'object',start,size:meta.page_points,highlight:inspectionHighlight?'1':'0'});
    const page=await window.TunnelDisplayBuffers.load('/api/shape-inspection?'+params,{signal:controller.signal});
    if(!current()||scene.inspectionId!==meta.id)return;
    const expected=Math.min(meta.page_points,meta.original_points-start);
    if(page.inspection_id!==meta.id||page.start!==start||page.part!=='object'||page.sample_count!==expected)throw Error('Incomplete pocket detail page. Reload the pocket.');
    scene.addPointPage(page.packed);inspection.loaded_points+=page.sample_count;
    el('inspect-status').textContent=message();$('job-preview-info').textContent=message();
    await new Promise(resolve=>requestAnimationFrame(resolve));
   }
   if(!current())return;
   inspection.complete=inspection.loaded_points===meta.original_points;el('inspect-status').textContent='Full pocket detail · '+message();
   $('job-preview-info').textContent='FULL POCKET DETAIL · '+message();render();
  }catch(error){
   if(current()){
    const view=overviewView;inspection=null;overviewView=null;
    if(result&&!result.stale){show();restoreCamera(view);}
    status(error.message||'Pocket detail could not load.',true);
   }
  }finally{
   if(inspectionBusyEpoch===epoch){inspectionBusyEpoch=null;setBusy(false);}
   if(current()){inspectionLoading=false;inspectionPhase='';render();}
  }
 }
 function reviewParams({region=result?.region??null,sort=result?.sort??'best'}={}){
  return new URLSearchParams({sort,...(region==null?{}:{region})});
 }
 async function readReview(page,options={}){
  const params=reviewParams(options),prior=result;
  params.set('page',page);
  if(options.points===false)params.set('points','0');
  const next=(await api('/api/shape-selection?'+params)).result;
  if(options.points===false){
   if(!next||next.id!==prior?.id||next.revision!==prior?.revision)throw Error('The review changed. Refresh the candidates before browsing.');
   next.preview=prior.preview;
  }
  return next;
 }
 async function refresh(page=0,options={}){result=await readReview(page,options);render();}
 async function browse(page,options={}){
  if(running||busy||browsing||inspectionLoading||!result)return;
  const epoch=++browseEpoch;
  browsing=true;controls();
  try{
   const next=await readReview(page,{...options,points:false});
   if(epoch!==browseEpoch)return;
   const view=overviewView;
   const redraw=!!inspection||next.region!==result.region;
   if(inspection){abandonInspection();overviewView=null;}
   result=next;
   if(redraw){show();restoreCamera(view);}
   render();
   if('region' in options)el('candidate-title').focus();
  }catch(error){
   if(epoch===browseEpoch)status(error.message,true);
  }finally{
   if(epoch===browseEpoch){browsing=false;el('sort').value=result?.sort??'best';controls();}
  }
 }
 async function refreshLibrary(preferred){
  const old=preferred??el('library').value;library=await api('/api/shape-library');
  el('library').innerHTML='<option value="">Drawn example only · unsaved</option>'+library.classes.map(c=>`<option value="${escape(c.id)}">${escape(c.name)}${c.active?'':' · archived'} · ${count(c.positive)} examples</option>`).join('');
  if(library.classes.some(c=>c.id===old))el('library').value=old;
  const c=selectedType();
  el('library-counts').textContent=c?`${c.name}: ${count(c.positive)} confirmed examples · ${count(c.negative)} incorrect matches. Only examples you confirm affect future searches.`:'Create a type such as Pocket 1, then save several examples of that same pocket.';
  el('examples').innerHTML=c?c.examples.map((e,i)=>`<div class="feature-group"><span><strong>${e.label==='positive'?'Confirmed object':'Incorrect match'} ${i+1}</strong><small>${count(e.points)} object points · ${count(e.context_points)} separate wall points · ${e.side==='either'?'both lining sides':e.side==='recess'?'recessed':'raised'}${Number.isFinite(e.reference?.wall_rms_mm)?' · wall fit '+e.reference.wall_rms_mm.toFixed(2)+' mm RMS':''}</small>${(e.warnings||[]).map(w=>'<small>'+escape(w)+'</small>').join('')}</span><button data-example-preview="${escape(e.id)}">View</button><button data-example-forget="${escape(e.id)}">Forget</button></div>`).join('')+(c.example_count>50?'<p>Showing the latest 50 examples; searches use all active examples.</p>':''):'';
  el('library-folder').textContent=library.folder?`${library.folder} · ${(library.bytes/1048576).toFixed(1)} MB` :'';
  const imported=library.last_import;
  el('import-summary').textContent=imported?`${imported.source_name}: ${count(imported.usable)} usable objects · ${count(imported.added)} new examples · ${count(imported.skipped.length)} groups skipped. ${imported.warning} ${imported.skipped.slice(0,5).map(g=>'Group '+g.group+': '+g.reason).join(' ')}`:'';
  controls();
 }
 async function libraryAction(action,extra={}){
  if(running||busy||inspectionLoading)return;
  try{
   if(!selectedType()?.active&& !['forget_example'].includes(action))throw Error('Choose an available shape type first.');
   if(action==='capture'&&!example)throw Error('Draw one measured pocket with nearby wall first.');
   if(['teach_selected','reject_selected'].includes(action)&&(!result||dirty||result.stale||!result.statistics.selected_points))throw Error('Find shapes and review selected complete groups first.');
   const body={action,class_id:el('library').value,confirmed:true,...extra};
   if(action==='capture')Object.assign(body,{source_id:el('source').value,example,settings:settings()});
   if(action==='capture_scan')Object.assign(body,{source_id:el('source').value,example_source_id:el('separated-source').value,settings:settings()});
   if(result)Object.assign(body,{preview_id:result.id,revision:result.revision});
   running=true;setBusy(true);controls();status('Saving confirmed shape examples and separate measured wall context…');
   await post('/api/shape-library',body);const done=await window.JobUI.waitReady(j=>status(j.message||'Saving…'));if(done.cancelled)throw Error('Stopped. No teaching was saved.');
   await refreshLibrary();
   if(action==='reject_selected'){
    await post('/api/shape-selection',{action:'select',preview_id:result.id,revision:result.revision,operation:'none'});await window.JobUI.waitReady();await refresh(result.page);show();
   }
   if(['capture','capture_scan'].includes(action))el('use-library').checked=true;
   status(action==='reject_selected'?'Incorrect examples saved. Their points remain in the scan. Search again to apply this feedback.':action==='forget_example'?'Teaching example removed from matching. Undo can restore this change.':'Confirmed examples saved independently. Search again to use the expanded library. Undo can reverse this teaching.');
  }catch(e){status(e.message,true);}finally{running=false;setBusy(false);controls();}
 }
 async function run(action,extra={}){
  if(running||busy||browsing||inspectionLoading)return;
  if(action==='preview')resetReviewBoxes();
  const focused=inspection?.group_id,view=overviewView;let succeeded=false;
  abandonInspection();
  if(['preview','delete','separate'].includes(action)){overviewView=null;picking=false;}
  else if(focused){show();restoreCamera(view);}
  try{
   let body;if(action==='preview'){if(!example&&!librarySearch())throw Error('Draw an example or select a saved shape type first.');body={action,source_id:el('source').value,example,settings:settings(),...(librarySearch()?{class_id:el('library').value}:{})};}
   else{if(!result||dirty||result.stale)throw Error('Find shapes with the current source and settings again.');body={action,preview_id:result.id,revision:result.revision,...extra};}
   running=true;setBusy(true);controls();status(action==='preview'?`Searching ${scopeNames[searchArea()]} in blocks…`:'Updating full-density original points…');
   await post('/api/shape-selection',body);
   const picked=extra.operation==='invert';
   const job=await window.JobUI.waitReady(value=>status(value.message||'Working…'),picked?{pollMs:120,skipSync:true}:undefined);
   if(job.cancelled)throw Error('Stopped. Source points and the previous completed result are unchanged.');
   await refresh(action==='preview'?0:result?.page||0,action==='preview'?{region:null}:{});dirty=false;
   succeeded=true;
   if(['delete','separate'].includes(action)){window.JobUI.clearCleaningPreview('shape');await window.JobUI.loadPreview();await refreshLibrary();status(`${count(result?.last_edit?.count)} points ${action==='separate'?'separated':'removed'} · ${count(result?.last_edit?.taught)} new library examples. Undo reverses the edit and its teaching.`);}
   else{
    show();status('Review orange points. Adjust groups or refine a drawn area before removal or separation.');
    if(picked){
     const id=extra.group_ids[0],group=result.groups.find(g=>g.id===id);
     el('pick-status').textContent=group?'Match '+id+(group.selected?' included.':' excluded.'):'Match '+id+' updated.';
    }
   }
  }catch(error){status(error.message,true);}finally{running=false;setBusy(false);render();if(succeeded&&focused&&['select','refine'].includes(action))queueMicrotask(()=>inspectPocket(focused));}
  return succeeded;
 }
 async function open(){
  if(running||busy)return;const scans=window.JobUI.scanItems(),selected=window.JobUI.selectedItem();
  resetReviewBoxes();abandonInspection();overviewView=null;
  el('source').innerHTML=scans.map(x=>`<option value="${escape(x.id)}">${escape(x.name)}</option>`).join('');
  el('separated-source').innerHTML='<option value="">Choose an existing separated scan</option>'+window.JobUI.scanItems().map(x=>`<option value="${escape(x.id)}">${escape(x.name)}</option>`).join('');
  if(selected?.kind==='scan'&&scans.some(x=>x.id===selected.id))el('source').value=selected.id;
  el('search-scope').value='auto';example=null;result=null;displayed=null;dirty=true;window.JobUI.clearCleaningPreview();window.TunnelRibbon.view('shape-selection');
  el('example').textContent='Draw one object or a short service section. The example does not limit the search.';
  status(scans.length?'Choose a saved type, or draw and save several examples as Pocket 1.':'Import or show a registered scan to search; saved examples can still be reviewed.',!scans.length);render();
  try{await refreshLibrary();}catch(e){status(e.message,true);}
 }
 for(const mode of ['box','lasso','polygon'])el(mode).onclick=()=>{if(running||busy)return;resetReviewBoxes();window.JobUI.clearCleaningPreview();window.JobUI.selectScan(el('source').value);window.JobUI.armAreaSelection(receive,mode);status('Draw around one example in the main 3D view. Polygon: Enter to finish.');};
 el('use').onclick=()=>{const spec=window.JobUI.currentSelection();if(spec)receive(spec);else status('Draw an area in the main 3D view first.',true);};
 el('run').onclick=()=>run('preview');el('stop').onclick=async()=>{status('Stopping at the next source block or matching window…');try{await post('/api/cancel-operation',{});}catch(e){status(e.message,true);}};
 el('pick-3d').onclick=()=>{
  window.JobUI.cancelAreaSelection(receiveReviewBox);
  picking=!picking;
  if(picking){
   window.JobUI.setMode('orbit');
   el('pick-status').textContent='Click an orange match to exclude it. Click a cyan match to include it. Drag to orbit.';
  }
  controls();
 };
 el('add-review-box').onclick=()=>{
  if(el('add-review-box').disabled)return;
  picking=false;
  if(window.JobUI.armAreaSelection(receiveReviewBox,'box','shape'))status('Drag a box around the unwanted displayed matches.');
  controls();
 };
 el('undo-review-box').onclick=()=>{
  window.JobUI.cancelAreaSelection(receiveReviewBox);reviewBoxes.pop();
  updateReviewBoxes();controls();status('Last queued box removed.');
 };
 el('apply-review-boxes').onclick=async()=>{
  controls();
  if(el('apply-review-boxes').disabled)return;
  window.JobUI.cancelAreaSelection(receiveReviewBox);
  const ids=[...reviewBoxIds];
  if(await run('select',{operation:'toggle',group_ids:ids,selected:false})){
   resetReviewBoxes();controls();status(`${count(ids.length)} boxed matches left unselected. Review the remaining orange points.`);
  }
 };
 for(const operation of ['all','none'])el(operation).onclick=()=>run('select',{operation});
 el('groups').onchange=e=>{if(e.target.dataset.shapeGroup)run('select',{operation:'toggle',group_ids:[+e.target.dataset.shapeGroup],selected:e.target.checked});};
 el('groups').onclick=e=>{const button=e.target.closest('[data-shape-inspect]');if(button)inspectPocket(+button.dataset.shapeInspect);};
 el('inspect-previous').onclick=()=>{if(inspection?.previous)inspectPocket(inspection.previous);};
 el('inspect-next').onclick=()=>{if(inspection?.next)inspectPocket(inspection.next);};
 el('inspect-back').onclick=backToMatches;el('inspect-stop').onclick=backToMatches;
 el('inspect-highlight').onchange=()=>{inspectionHighlight=el('inspect-highlight').checked;if(inspection)inspectPocket(inspection.group_id);};
 for(const operation of ['keep','exclude'])el(operation).onclick=()=>{const selection=window.JobUI.currentSelection();if(selection)run('refine',{operation,selection});else status('Draw an area over the preview first.',true);};
 const teaching=()=>({teach:!!(selectedType()?.active&&el('teach-removal').checked&&!result?.statistics.continuing_service),class_id:el('library').value});
 el('remove').onclick=()=>{if(el('remove').disabled)return;const t=teaching();showTunnelMessage('Remove reviewed shape matches',`Remove ${count(result.statistics.selected_points)} selected original points across all regions from ${result.source_name}? ${inspection?'You are inspecting one pocket; this action applies to ALL checked groups in the review. ':''}${t.teach?'Confirm these complete objects as '+selectedType().name+' examples. They will be saved independently before removal. One Undo reverses both.':'Review the geometric matches first. Undo remains available.'}`,true,t.teach?'Remove and teach':'Remove selected points',()=>run('delete',{confirmed:true,...t}));};
 el('separate').onclick=()=>{if(!el('separate').disabled)run('separate',{confirmed:true,...teaching()});};
 el('normal').onclick=()=>{resetReviewBoxes();const view=overviewView;abandonInspection();overviewView=null;window.JobUI.clearCleaningPreview();restoreCamera(view);controls();};
 el('source').onchange=()=>{resetReviewBoxes();abandonInspection();overviewView=null;example=null;result=null;dirty=true;el('example').textContent='Draw an example on this source scan.';render();};
 for(const id of ['side','minimum','maximum','continuation','similarity','depth','grid','rotations','view-filters','reference','include-reviewed','search-scope'])el(id).addEventListener('input',changed);
 el('library').onchange=async()=>{changed();try{await refreshLibrary();}catch(e){status(e.message,true);}};
 el('use-library').onchange=changed;el('teach-removal').onchange=controls;
 el('refresh-library').onclick=async()=>{try{await refreshLibrary();}catch(e){status(e.message,true);}};
 for(const [id,action] of [['create','create_type'],['rename','rename_type'],['archive','archive_type'],['restore','restore_type']])el(id).onclick=async()=>{
  if(running||busy)return;
  try{
   const response=await post('/api/shape-library',{action,class_id:el('library').value,name:el('type-name').value});
   await refreshLibrary(response.class_id);changed();if(action==='create_type')el('type-name').value='';
   status(action==='archive_type'?'Type archived. Its measured examples remain saved.':'Shape type saved.');
  }catch(e){status(e.message,true);}
 };
 el('save-example').onclick=()=>libraryAction('capture');
 el('separated-source').onchange=controls;
 el('add-separated').onclick=()=>{if(el('add-separated').disabled)return;showTunnelMessage('Teach existing separated pockets',`Confirm that the compact disconnected groups in “${el('separated-source').selectedOptions[0].textContent}” are examples of ${selectedType().name}? Nearby measured wall will come from “${el('source').selectedOptions[0].textContent}”. Unusable groups are reported. Source scans remain unchanged; Undo reverses this teaching.`,true,'Confirm separated examples',()=>libraryAction('capture_scan'));};
 el('teach').onclick=()=>libraryAction('teach_selected');
 el('reject').onclick=()=>libraryAction('reject_selected');
 el('examples').onclick=async e=>{
  const view=e.target.closest('[data-example-preview]'),forget=e.target.closest('[data-example-forget]');
  if(running||busy)return;
  if(view)try{const p=await api('/api/shape-library?'+new URLSearchParams({example:view.dataset.examplePreview}));window.JobUI.analysisPreview({...p,result_id:view.dataset.examplePreview,sample_count:p.preview_count,used:p.count},'shape-library');status('Saved example: orange object, green wall context. These independent points are not a source-scan selection.');}catch(error){status(error.message,true);}
  if(forget)showTunnelMessage('Forget this teaching example','Remove this example from future matching? Its source scan is unchanged. Workspace Undo can restore this teaching label.',true,'Forget example',()=>libraryAction('forget_example',{example_id:forget.dataset.exampleForget}));
 };
 el('coverage-grid').onclick=e=>{
  const card=e.target.closest('[data-shape-region]');
  if(card){const region=+card.dataset.shapeRegion;browse(0,{region:result?.region===region?null:region});}
 };
 el('all-regions').onclick=()=>browse(0,{region:null});
 el('sort').onchange=()=>browse(0,{sort:el('sort').value});
 for(const [id,delta] of [['previous',-1],['next',1]])el(id).onclick=()=>{if(result)browse(result.page+delta);};
 window.ShapeSelectionUI={open,pickMatch,loadViewDetail,refreshLibrary,busy:controls,inspect:inspectPocket,inspection:()=>inspection,backToMatches,removeSelected:()=>el('remove').click(),removableCount:()=>!dirty&&!result?.stale?result?.statistics.selected_points||0:0,result:()=>result,onToolClose:()=>{resetReviewBoxes();const view=overviewView;browseEpoch++;browsing=false;abandonInspection();overviewView=null;displayed=null;if(!running){window.JobUI.clearCleaningPreview('shape');restoreCamera(view);controls();}}};
})();
