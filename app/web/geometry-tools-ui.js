/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const $g=id=>document.getElementById(id),J=()=>window.JobUI;
 const titles={plane:'Create plane',fit_plane:'Fit best plane',fit_cylinder:'Fit best cylinder'};
 const dialog=document.createElement('dialog');dialog.id='geometry-tools-dialog';dialog.className='geometry-tools-dialog';
 dialog.setAttribute('aria-labelledby','geometry-tools-title');
 const vector=(id,label,values,help)=>`<fieldset class="geometry-vector"><legend>${label}</legend><div>${['E / X','N / Y','H / Z'].map((axis,i)=>`<label>${axis}<input id="${id}-${i}" type="number" step="any" value="${values[i]}" aria-label="${label} ${axis}" title="${help}"></label>`).join('')}</div></fieldset>`;
 dialog.innerHTML=`<header class="geometry-dialog-head"><div><small>MODEL · GEOMETRY</small><h2 id="geometry-tools-title">Create plane</h2></div><button id="geometry-tools-close" aria-label="Close geometry tool">×</button></header>
 <div class="geometry-dialog-body"><p id="geometry-tools-intro"></p><label>Model name<input id="geometry-tools-name" maxlength="120" value="Plane"></label>
 <section id="geometry-create-fields"><label>Create from<select id="geometry-plane-source"><option value="points">Three measured scan points</option><option value="manual">Centre, direction and dimensions</option></select></label>
 <div id="geometry-point-fields"><div class="geometry-pick-actions"><button id="geometry-pick-three" type="button">Pick 3 scan points</button><button id="geometry-pick-cancel" type="button" hidden>Cancel picking</button></div><p class="geometry-help">Click three points on the visible scan. Each click snaps to an original retained measurement. The first two define the plane direction; the third defines its tilt. Hover near a third point to see a tentative plane.</p><ol id="geometry-picked-points" class="geometry-picked-points"></ol></div>
 <div id="geometry-manual-fields" hidden>${vector('geometry-origin','Centre · metres',[0,0,0],'World survey coordinates of the middle of the plane. The scan is not moved.')}
 <label>Plane direction<select id="geometry-orientation"><option value="xy">Horizontal · XY</option><option value="xz">Vertical · XZ</option><option value="yz">Vertical · YZ</option><option value="custom">Custom normal</option></select></label>
 <div class="geometry-pair"><label>Width · m<input id="geometry-width" type="number" value="2" min="0.000001" step="any" title="Size along the first plane direction."></label><label>Height · m<input id="geometry-height" type="number" value="2" min="0.000001" step="any" title="Size along the second plane direction; for a horizontal plane this is its plan depth."></label></div>
 <div id="geometry-custom-normal" hidden>${vector('geometry-normal','Normal direction',[0,0,1],'The normal points perpendicular to the plane. Enter a non-zero direction, for example 0,0,1 for horizontal. Its length does not affect the plane size.')}</div>
 </div><div class="geometry-area-actions"><button id="geometry-plane-area" type="button">Draw point area</button><button id="geometry-plane-highlight" type="button">Fit current highlight</button><p class="geometry-help">An area fits a best-fit plane to every retained point in that selection. Inspect the deviation before adding the model.</p></div>
 </section>
 <section id="geometry-fit-fields"><label>Fit to<select id="geometry-scope"></select></label><p class="geometry-help" id="geometry-scope-help"></p><button id="geometry-refresh-scope" type="button">Use current point selection</button>
 <details class="geometry-details"><summary>How the fit works</summary><p id="geometry-fit-method"></p><p>All selected retained points contribute to the final fit and residuals. Points excluded by deletion remain excluded. The point display density does not reduce the fit.</p><p>For a pipe or a lining patch, select that surface first. Including nearby objects changes the fitted model. Processing a whole large scan takes longer and needs temporary disk space.</p></details></section>
 <section id="geometry-fit-result" class="geometry-fit-result" hidden aria-live="polite"></section>
 <details class="geometry-details"><summary>Model appearance</summary><div class="geometry-pair"><label>Colour<input id="geometry-colour" type="color" value="#35b9c1"></label><label>Layer<input id="geometry-layer" value="Geometry" maxlength="80"></label></div></details>
 <p id="geometry-tools-status" class="geometry-status" role="status">Preview the model before adding it.</p></div>
 <footer class="geometry-dialog-actions"><button id="geometry-tools-preview">Preview</button><button id="geometry-tools-fit-view" hidden>View preview</button><button id="geometry-tools-stop" hidden>Stop</button><button id="geometry-tools-apply" class="primary" disabled>Add model</button></footer>`;
 document.body.append(dialog);
 let operation='plane',pending=false,preview=null,scope={},context=null,openGeneration=0,pickMode=null,pickedPoints=[],hoverPoint=null,lastHover=0;
 function message(text,error=false){$g('geometry-tools-status').textContent=text;$g('geometry-tools-status').classList.toggle('error',error);}
 function controls(){
  dialog.querySelectorAll('input,select').forEach(el=>el.disabled=pending);
  $g('geometry-tools-preview').disabled=pending||!!pickMode||(operation==='plane'&&$g('geometry-plane-source').value==='points'&&pickedPoints.length!==3);$g('geometry-tools-apply').disabled=pending||!preview;
  $g('geometry-refresh-scope').disabled=pending;$g('geometry-tools-close').disabled=pending;
  $g('geometry-tools-stop').hidden=!pending;$g('geometry-tools-fit-view').hidden=!preview;$g('geometry-tools-apply').textContent=preview?.statistics?.fit_quality==='poor'?'Add approximate model':'Add model';
  for(const id of ['geometry-pick-three','geometry-plane-area','geometry-plane-highlight'])$g(id).disabled=pending||!!pickMode;
  $g('geometry-pick-cancel').hidden=!pickMode;$g('geometry-pick-cancel').disabled=pending;
 }
 function clearPreview(text='Settings changed. Preview again before adding the model.'){
  preview=null;$g('geometry-fit-result').hidden=true;message(text);controls();J()?.draw();
 }
 function vectorValue(id){const values=[0,1,2].map(i=>Number($g(id+'-'+i).value));if(!values.every(Number.isFinite)||[0,1,2].some(i=>$g(id+'-'+i).value.trim()===''))throw Error('Enter three finite coordinates or direction values.');return values;}
 function request(){
  if(operation==='plane'){
   if($g('geometry-plane-source').value==='points'){
    if(pickedPoints.length!==3)throw Error('Pick three measured scan points first.');
    return {operation,name:$g('geometry-tools-name').value,plane:{points:pickedPoints.map(p=>p.point),point_sources:pickedPoints.map(({source_id,source_revision,index})=>({source_id,source_revision,index}))}};
   }
   const selected=$g('geometry-orientation').value,normal={xy:[0,0,1],xz:[0,-1,0],yz:[1,0,0]}[selected]||vectorValue('geometry-normal');
   const width=Number($g('geometry-width').value),height=Number($g('geometry-height').value);
   if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)throw Error('Plane width and height must be positive metres.');
   return {operation,name:$g('geometry-tools-name').value,plane:{origin:vectorValue('geometry-origin'),normal,width,height}};
  }
  const chosen=scope[$g('geometry-scope').value];if(!chosen)throw Error('Select points in the 3D view or choose one or more scans in the Project explorer, then refresh the scope.');
  return {operation,name:$g('geometry-tools-name').value,request:chosen};
 }
 function captureScope(){
  scope={};const highlight=J()?.highlightRequest?.(),inventory=J()?.inventory?.().items||[],ids=J()?.selectedItems?.()||[];
  if(highlight?.ids?.length)scope.highlight=JSON.parse(JSON.stringify(highlight));
  const chosen=inventory.filter(x=>x.kind==='scan'&&ids.includes(x.id));
  if(chosen.length)scope.whole={ids:chosen.map(x=>x.id),whole:true,versions:Object.fromEntries(chosen.filter(x=>x.geometry_revision).map(x=>[x.id,x.geometry_revision]))};
  const select=$g('geometry-scope');select.replaceChildren();
  if(scope.highlight)select.add(new Option('Highlighted points · full density','highlight'));
  if(scope.whole)select.add(new Option(chosen.length===1?'Entire selected scan · '+chosen[0].name:`Entire ${chosen.length} selected scans`,'whole'));
  if(!Object.keys(scope).length)select.add(new Option('No scan points selected',''));
  scopeHelp();
 }
 function scopeHelp(){const mode=$g('geometry-scope').value;$g('geometry-scope-help').textContent=mode==='highlight'?'Uses the captured 3D highlight, including its depth. To replace it, change your point selection and click Use current point selection.':mode==='whole'?'Uses every retained point in the selected scan objects, including points temporarily hidden by a view filter. For local surfaces, draw a point selection first.':'Close this panel, select the required points or scan objects, then reopen the fitting tool.';}
 function pointList(){const list=$g('geometry-picked-points');list.replaceChildren();for(const [i,p] of pickedPoints.entries()){const li=document.createElement('li');li.textContent=p.point.map(v=>Number(v).toFixed(4)).join(', ')+' m';li.title='Measured point '+(i+1)+' · '+p.source_id+' · original index '+p.index;list.append(li);}}
 function cancelPick(){if(pickMode==='points')J()?.cancelFeatureSeed?.();if(pickMode==='area'){J()?.clearSelection();J()?.setMode('orbit');}pickMode=null;hoverPoint=null;controls();J()?.draw();}
 function sourceFields(){const points=$g('geometry-plane-source').value==='points';$g('geometry-point-fields').hidden=!points;$g('geometry-manual-fields').hidden=points;}
 function pickThree(){
  if(pending||busy)return;cancelPick();J()?.clearCleaningPreview();J()?.clearSelection();pickedPoints=[];pointList();clearPreview('Pick point 1 of 3 on the visible scan.');pickMode='points';controls();const generation=openGeneration;
  function next(){
   if(generation!==openGeneration||!dialog.open||pickMode!=='points')return;
   const n=pickedPoints.length+1;message('Pick point '+n+' of 3 on the visible scan.'+(n===3?' Move near the third point to preview the tilt.':''));
   const armed=J()?.pickFeatureSeed?.(point=>{
    if(generation!==openGeneration||pickMode!=='points')return;
    if(pickedPoints.some(p=>p.source_id===point.source_id&&p.index===point.index)){message('That measurement is already selected. Pick a different point.',true);setTimeout(next,0);return;}
    pickedPoints.push(point);hoverPoint=null;pointList();J()?.draw();
    if(pickedPoints.length<3)setTimeout(next,0);
    else{pickMode=null;controls();message('Three original measured points recovered. Preparing the plane…');setTimeout(()=>{if(generation===openGeneration&&dialog.open)run(false);},0);}
   },{prompt:'Plane · pick measured point '+n+' of 3'});
   if(!armed){pickMode=null;controls();message('Load the 3D scan preview before picking measured points.',true);}
  }
  next();
 }
 function fitHighlight(){
  if(pending||busy)return;const selected=J()?.highlightRequest?.();if(!selected?.ids?.length){message('Highlight scan points first, or choose Draw point area.',true);return;}
  cancelPick();open('fit_plane');$g('geometry-scope').value='highlight';scopeHelp();run(false);
 }
 function pickArea(){
  if(pending||busy)return;cancelPick();J()?.clearCleaningPreview();if(window.CadUI?.modelSpace?.()&&window.CadUI.enter('Clean',{openTool:false})===false)return;
  clearPreview('Draw a rectangle around the scan surface to fit.');pickMode='area';controls();const generation=openGeneration;
  if(!J()?.armAreaSelection?.(()=>{if(generation!==openGeneration||pickMode!=='area')return;pickMode=null;controls();fitHighlight();})){pickMode=null;controls();message('Load the normal 3D scan preview before selecting an area.',true);}
 }
 function open(mode='plane'){
  if(pending||busy)return;
  if(!titles[mode])return;
  if(window.TunnelRibbon?.prepareTool?.('geometry')===false)return;
  cancelPick();operation=mode;openGeneration++;preview=null;pickedPoints=[];pointList();context=window.CadUI?.model?.().context;sourceFields();
  $g('geometry-tools-title').textContent=titles[mode];$g('geometry-tools-name').value=mode==='plane'?'Plane':mode==='fit_plane'?'Best-fit plane':'Best-fit cylinder';
  $g('geometry-create-fields').hidden=mode!=='plane';$g('geometry-fit-fields').hidden=mode==='plane';
  $g('geometry-tools-intro').textContent=mode==='plane'?'Create a plane through measured scan points, fit a selected surface, or enter survey coordinates and dimensions. Preview it in 3D before adding it.':mode==='fit_plane'?'Fit a flat reference surface through the selected scan points. Review its deviation before adding the model.':'Fit the axis, radius and length of a cylindrical surface such as a pipe. Select that surface and review the fit before adding the model.';
  $g('geometry-fit-method').textContent=mode==='fit_plane'?'Orthogonal least squares finds the plane with the lowest summed squared perpendicular distance to all selected points.':'A bounded sample supplies starting guesses. The axis and radius are then refined against all selected points. Unstable or degenerate cylinder fits are rejected. A high residual fit is marked as poor so its approximation can be inspected.';
  if(mode==='plane'){
   const item=J()?.selectedItem?.(),frame=window.CadUI?.workPlane?.(),origin=item?.minimum&&item?.maximum?item.minimum.map((v,i)=>(v+item.maximum[i])/2):frame?.origin||[0,0,0];
   origin.forEach((v,i)=>$g('geometry-origin-'+i).value=v);
  }else captureScope();
  clearPreview('Preview the model before adding it.');if(!dialog.open)dialog.show();$g('geometry-tools-preview').focus();
 }
 function resultSummary(value){
  const result=$g('geometry-fit-result'),s=value.statistics||{},f=(n,d=3)=>Number(n).toLocaleString('en-GB',{minimumFractionDigits:d,maximumFractionDigits:d});
  const rows=s.points?`<div class="geometry-metrics"><div><strong>${Number(s.points).toLocaleString('en-GB')}</strong><span>retained points used</span></div><div title="Root mean square of perpendicular distances; lower means a closer fit."><strong>${f(s.rms_mm)} mm</strong><span>RMS deviation</span></div><div title="Largest absolute deviation among all selected retained points."><strong>${f(s.max_absolute_mm)} mm</strong><span>maximum deviation</span></div></div>`:'<p>Plane preview is ready.</p>';
  const dimensions=s.radius_m!=null?`Radius <b>${f(s.radius_m,4)} m</b> · diameter <b>${f(2*s.radius_m,4)} m</b> · length <b>${f(s.height_m,4)} m</b>`:s.width_m!=null?`Width <b>${f(s.width_m,4)} m</b> · height <b>${f(s.height_m,4)} m</b>`:`Surface area <b>${f(value.geometry.area_m2,4)} m²</b>`;
  result.classList.toggle('poor-fit',s.fit_quality==='poor');result.innerHTML=`<h3>${s.fit_quality==='poor'?'Poor fit · inspect before adding':'Preview ready'}</h3>${rows}<p>${dimensions}</p>${(value.warnings||[]).map(w=>`<p class="geometry-fit-note">${esc(w)}</p>`).join('')}<details><summary>Fit details</summary><p>${esc(s.method||'')}</p>${s.angular_coverage_degrees!=null?`<p>Approximate observed angular coverage: ${f(s.angular_coverage_degrees,0)}° · measured in 5° bins</p>`:''}${s.mean_absolute_mm!=null?`<p>Mean absolute deviation: ${f(s.mean_absolute_mm)} mm</p>`:''}<p>The original scan coordinates are retained. Saved model dimensions can be edited from its Properties → Parameters.</p></details>`;
  result.hidden=false;
 }
 async function run(apply=false){
  if(pending||busy)return;const generation=openGeneration;
  try{
   const body=apply?{token:preview?.token,name:$g('geometry-tools-name').value.trim()||titles[operation],colour:$g('geometry-colour').value,layer:$g('geometry-layer').value,bundle_id:window.WorkspaceUI?.activeBundle?.()||''}:request();
   if(apply&&!preview)throw Error('Preview the model first.');
   if(!apply){preview=null;$g('geometry-fit-result').hidden=true;J()?.draw();}
   pending=true;controls();setBusy(true);message(apply?'Saving model…':'Fitting geometry…');
   await post(apply?'/api/geometry-apply':'/api/geometry-preview',body);
   const job=await J().waitReady(value=>message(value.message||'Processing geometry…'),{skipSync:true});
   if(job.cancelled)throw Error('Operation stopped. No model was added.');
   const result=await api('/api/geometry');if(generation!==openGeneration)return;
   if(apply){
    const added=result.last_applied;if(!added?.id)throw Error('The saved model could not be identified. Refresh the Project explorer.');
    preview=null;await window.CadUI?.load(true);await J().sync({inventoryOnly:true});
    dialog.close();window.CadUI?.select(added.id);window.CadUI?.showCloudReference?.();window.HistoryUI?.sync();J().draw();notice(added.name+' added. Edit its dimensions in Model properties.');
   }else{
    if(!result.preview)throw Error('No geometry preview was produced.');preview=result.preview;context=window.CadUI?.model?.().context;resultSummary(preview);J().draw();
    message(preview.statistics?.fit_quality==='poor'?'This cylinder is a poor fit to the selected points. Inspect its deviation and shape. Add approximate model keeps this reviewed approximation.':'Review the cyan model in 3D, then choose Add model.');
   }
  }catch(error){message(error.message,true);if(!apply)preview=null;}
  finally{pending=false;setBusy(false);controls();}
 }
 async function stop(){if(!pending)return;try{await post('/api/cancel-operation',{});message('Stopping geometry calculation…');}catch(error){message(error.message,true);}}
 function close(){if(pending)return;const token=preview?.token;cancelPick();openGeneration++;preview=null;dialog.close();J()?.draw();if(token)post('/api/geometry-discard',{token}).catch(()=>{});}
 function closeInactive(){if(pending)return false;if(dialog.open)close();return true;}
 function draw(project){
  if(!dialog.open)return '';
  if(context&&window.CadUI?.model?.().context!==context)return '';
  let result=[];if(!preview){
   const points=pickedPoints.map(p=>p.point),tentative=pickMode==='points'&&points.length===2&&hoverPoint;if(tentative)points.push(hoverPoint.point);const pixels=points.map(project);
   if(pixels.length===3&&pixels.every(Boolean))result.push(`<polygon data-plane-tentative="${!!tentative}" points="${pixels.map(p=>p[0]+','+p[1]).join(' ')}" fill="#35d8e8" fill-opacity=".14" stroke="#6defff" stroke-width="2" stroke-dasharray="6 4"/>`);
   pixels.forEach((p,i)=>{if(p)result.push(`<circle cx="${p[0]}" cy="${p[1]}" r="5" fill="${i===2&&tentative?'#fff':'#6defff'}" stroke="#173944" stroke-width="2"/><text x="${p[0]+9}" y="${p[1]-9}" fill="#aaf8ff" stroke="#173944" stroke-width="3" paint-order="stroke" font-size="12">${i===2&&tentative?'Tentative · click to snap':i+1}</text>`);});
   return `<g class="geometry-preview-overlay" style="pointer-events:none">${result.join('')}</g>`;
  }
  const geometry=preview.geometry,points=geometry.vertices.map(project);
  if(geometry.shape==='plane'){
   if(points.every(Boolean))result.push(`<polygon points="${points.map(p=>p[0]+','+p[1]).join(' ')}" fill="#35d8e8" fill-opacity=".16" stroke="#6defff" stroke-width="2"/>`);
  }else{
   const seen=new Set();for(const face of geometry.faces)for(let i=0;i<3;i++){const a=face[i],b=face[(i+1)%3],key=Math.min(a,b)+':'+Math.max(a,b);if(seen.has(key)||!points[a]||!points[b])continue;seen.add(key);result.push(`<line x1="${points[a][0]}" y1="${points[a][1]}" x2="${points[b][0]}" y2="${points[b][1]}" stroke="#6defff" stroke-width="1.1" opacity=".7"/>`);}
  }
  return `<g class="geometry-preview-overlay" style="pointer-events:none">${result.join('')}</g>`;
 }
 $g('geometry-tools-preview').onclick=()=>run(false);$g('geometry-tools-apply').onclick=()=>run(true);
 $g('geometry-tools-stop').onclick=stop;$g('geometry-tools-close').onclick=close;
 $g('geometry-pick-three').onclick=pickThree;$g('geometry-pick-cancel').onclick=()=>{cancelPick();message('Picking cancelled. Pick 3 scan points to start again.');};
 $g('geometry-plane-area').onclick=pickArea;$g('geometry-plane-highlight').onclick=fitHighlight;
 $g('geometry-plane-source').onchange=()=>{cancelPick();sourceFields();clearPreview();};
 $g('geometry-tools-fit-view').onclick=()=>preview&&J()?.focusWorld(preview.geometry.vertices);
 $g('geometry-refresh-scope').onclick=()=>{captureScope();clearPreview('Point selection captured. Preview the fit again.');};
 $g('geometry-scope').onchange=()=>{scopeHelp();clearPreview();};
 $g('geometry-orientation').onchange=()=>{$g('geometry-custom-normal').hidden=$g('geometry-orientation').value!=='custom';clearPreview();};
 dialog.querySelectorAll('#geometry-create-fields input').forEach(el=>el.addEventListener('input',()=>clearPreview()));
 dialog.addEventListener('cancel',e=>{e.preventDefault();if(pending)stop();else close();});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&dialog.open&&pickMode&&!pending){e.preventDefault();cancelPick();message('Picking cancelled. Pick 3 scan points to start again.');}});
 const canvas=$g('job-canvas');canvas?.addEventListener('pointermove',e=>{if(!dialog.open||pickMode!=='points'||pickedPoints.length!==2||busy||e.buttons||performance.now()-lastHover<150)return;lastHover=performance.now();const r=canvas.getBoundingClientRect();hoverPoint=J()?.previewNearbyPoint?.([e.clientX-r.left,e.clientY-r.top])||null;J()?.draw();});canvas?.addEventListener('pointerleave',()=>{if(hoverPoint){hoverPoint=null;J()?.draw();}});
 window.GeometryToolsUI={open,draw,close,closeInactive,canDeactivate:()=>!pending,isOpen:()=>dialog.open};
})();
