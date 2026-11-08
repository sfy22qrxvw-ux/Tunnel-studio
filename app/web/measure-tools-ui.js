/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const byId=id=>document.getElementById(id), J=()=>window.JobUI, icon=key=>window.ToolIcons.icon(key);
 const definitions={
  point_point:{label:'Between points',intro:'Pick or enter two measured points. Read the 3D distance, horizontal distance and height difference.',points:[['points','Point A',2]]},
  point_plane:{label:'Point to plane',intro:'Pick or enter a point and three plane points, or choose a planar model. The distance updates automatically. Hover while picking to preview the snapped point and transparent plane.',points:[['point','Measured point',1]],refs:[['plane','Reference plane','plane']]},
  point_triangle:{label:'Point to triangle',intro:'Measure the shortest distance to a finite triangle, including its edges and corners.',points:[['point','Measured point',1]],refs:[['triangle','Reference triangle','triangle']]},
  plane_plane:{label:'Between planes',intro:'Parallel infinite planes have a perpendicular separation. Intersecting planes have zero separation; their angle is also reported.',refs:[['plane_a','First plane','plane'],['plane_b','Second plane','plane']]},
  angle_points:{label:'Three-point angle',intro:'Pick A, the angle vertex B, then C. The angle is measured at B, from B→A to B→C.',points:[['points','Point',3]]},
  angle_lines:{label:'Angle between lines',intro:'Choose two line objects or define each by two points. Read the smaller direction angle from 0° to 90°.',refs:[['line_a','First line','line'],['line_b','Second line','line']]},
  angle_planes:{label:'Angle between planes',intro:'Choose two planar objects or define each by three points. Read the smaller plane angle from 0° to 90°.',refs:[['plane_a','First plane','plane'],['plane_b','Second plane','plane']]},
  tunnel_volume:{label:'Tunnel volume',intro:'Measure the space inside a tunnel mesh between two alignment chainages. Choose one continuous bore and review the end section areas.',object:true},
  surface:{label:'Measure surface',intro:'Measure every triangle of a CAD surface or imported mesh, or the enclosed area of a closed planar profile. No display sampling is used.',object:true},
  geometry:{label:'Measure geometry',intro:'Read curve length, surface area, extents and validated closed CAD solid volume. Open or imported surfaces are not silently closed.',object:true}
 };
 const panel=document.createElement('dialog');panel.id='measure-tools-dialog';panel.className='measure-tools-panel';
 panel.setAttribute('aria-labelledby','measure-tools-title');
 panel.innerHTML=`<header class="measure-tools-head"><div><span class="measure-tools-eyebrow">SURVEY MEASUREMENTS</span><h2 id="measure-tools-title">Measure</h2></div><button type="button" id="measure-tools-close" aria-label="Close measurements">${icon('close')}</button></header>
 <nav class="measure-tools-nav" aria-label="Measurement view"><button type="button" id="measure-new-tab">New measurement</button><button type="button" id="measure-saved-tab">Saved measures</button></nav>
 <div id="measure-tools-content"><section id="measure-editor"><label>Tool<select id="measure-operation">${Object.entries(definitions).map(([key,d])=>`<option value="${key}">${esc(d.label)}</option>`).join('')}</select></label><p id="measure-tools-intro" class="measure-tools-help"></p>
 <div id="measure-inputs"></div><div class="measure-tools-actions"><button type="button" class="primary" id="measure-calculate">Calculate</button><button type="button" id="measure-cancel-job" hidden>Stop calculation</button></div>
 <section id="measure-result" hidden aria-label="Measurement result"><h3>Result</h3><dl id="measure-metrics"></dl><div id="measure-notes"></div></section>
 <section class="measure-save-fields"><h3>Save this measurement</h3><label>Name<input id="measure-name" maxlength="150" autocomplete="off"></label><label>Comment<textarea id="measure-comment" rows="2" maxlength="2000" placeholder="Survey notes or location"></textarea></label><label class="measure-check"><input type="checkbox" id="measure-show-label" checked>Show label in the 3D view</label>
 <details class="measure-tolerance"><summary>Nominal value and tolerance</summary><p class="measure-tools-help" id="measure-tolerance-help">Optional: compare the primary result with an expected value. Leave both fields blank to omit this check.</p><div class="measure-tolerance-grid"><label>Nominal <span id="measure-nominal-unit"></span><input id="measure-nominal" type="number" step="any"></label><label>± tolerance <span id="measure-tolerance-unit"></span><input id="measure-tolerance" type="number" min="0" step="any"></label></div></details>
 <div class="measure-tools-actions"><button type="button" class="primary" id="measure-save" disabled>Save measurement</button><button type="button" id="measure-remove" hidden>Remove</button></div><p class="measure-tools-help">Saved values use fixed coordinates. Changing a source later does not silently change a saved measurement. Workspace Undo restores measurement edits.</p></section></section>
 <section id="measure-saved" hidden><p class="measure-tools-help">Select a measurement to edit its name, comment, tolerances, label or coordinates.</p><div id="measure-saved-list"></div><section class="measure-export"><h3>Export saved measurements</h3><label>Format<select id="measure-export-format"><option value="csv">CSV · results and units</option><option value="json">JSON · results, coordinates and comments</option></select></label><label class="measure-check"><input id="measure-export-current" type="checkbox">Only the measurement currently being edited</label><label id="measure-export-path-label" hidden>Full folder and filename<input id="measure-export-path" autocomplete="off" placeholder="C:\\Users\\YourName\\Documents\\Measurements.csv"></label><div class="measure-tools-actions"><button type="button" id="measure-export-save">Save as…</button><button type="button" id="measure-export-manual">Enter destination path</button></div></section></section>
 </div><p id="measure-tools-message" role="status" aria-live="polite"></p>`;
 document.body.append(panel);
 let model=null,operation='point_point',editId=null,preview=null,inputDirty=true,pending=false,picking=false,pickGeneration=0,view='editor',token=0;
 let autoTimer=null,autoTask=null,autoWanted=false,livePending=false,currentPick=null,hoverSample=null,referencePlane=null,planeRequest=0;
 const overlay=document.createElementNS('http://www.w3.org/2000/svg','svg');overlay.id='measure-live-overlay';overlay.setAttribute('aria-hidden','true');Object.assign(overlay.style,{position:'absolute',inset:'0',width:'100%',height:'100%',pointerEvents:'none',zIndex:'4'});byId('job-canvas').parentElement.append(overlay);
 const sub=(a,b)=>a.map((v,i)=>v-b[i]),add=(a,b)=>a.map((v,i)=>v+b[i]),mul=(a,k)=>a.map(v=>v*k),dot=(a,b)=>a.reduce((n,v,i)=>n+v*b[i],0),cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
 function optionalPoint(key,index){try{return readPoint(key,index);}catch{return null;}}
 function planeGeometry(points,point=null){
  if(points?.length!==3||points.some(p=>!p?.every(Number.isFinite)))return null;
  const origin=points[0],a=sub(points[1],origin),b=sub(points[2],origin),c=cross(a,b),scale=Math.max(Math.hypot(...a),Math.hypot(...b)),length=Math.hypot(...c);
  if(scale<=1e-10||length<=Math.max(1e-14,scale*scale*1e-10))return null;
  const normal=mul(c,1/length),x=mul(a,1/Math.hypot(...a)),y=cross(normal,x),signed=point?dot(sub(point,origin),normal):null,foot=point?sub(point,mul(normal,signed)):null;
  const extent=foot?[...points,foot]:points,uv=extent.map(p=>[dot(sub(p,origin),x),dot(sub(p,origin),y)]),lo=[0,1].map(i=>Math.min(...uv.map(p=>p[i]))),hi=[0,1].map(i=>Math.max(...uv.map(p=>p[i]))),pad=Math.max(.05,...hi.map((v,i)=>(v-lo[i])*.15));
  const corners=[[lo[0]-pad,lo[1]-pad],[hi[0]+pad,lo[1]-pad],[hi[0]+pad,hi[1]+pad],[lo[0]-pad,hi[1]+pad]].map(([u,v])=>add(origin,add(mul(x,u),mul(y,v))));
  return {corners,foot,signed,normal};
 }
 function draw(){
  if((!panel.open&&!picking)||view!=='editor'){overlay.innerHTML='';return;}
  let html='',point=operation==='point_plane'?optionalPoint('point',0):null;
  if(operation==='point_plane'){
   const ref=byId('measure-ref-plane')?.value;let points=ref?(referencePlane?.id===ref?referencePlane.points:null):[0,1,2].map(i=>optionalPoint('plane',i));
   if(currentPick&&hoverSample){if(currentPick.key==='point')point=hoverSample.point;else if(currentPick.key==='plane'&&points){points=points.slice();points[currentPick.index]=hoverSample.point;}}
   const geometry=planeGeometry(points,point);
   if(geometry){const corners=geometry.corners.map(p=>J().projectWorld(p));if(corners.every(Boolean))html+=`<polygon data-live-plane="true" points="${corners.map(p=>p.join(',')).join(' ')}" fill="#45c4ce" fill-opacity="0.18" stroke="#70dce4" stroke-opacity="0.8" stroke-width="1.5"/>`;
    if(point&&geometry.foot){const a=J().projectWorld(point),b=J().projectWorld(geometry.foot);if(a&&b){const label=(hoverSample&&currentPick?'Cursor preview · ':'')+(Math.abs(geometry.signed)*1000).toFixed(3)+' mm';html+=`<line data-live-distance="true" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#f3f8fa" stroke-width="2"/><circle cx="${b[0]}" cy="${b[1]}" r="4" fill="#70dce4"/><text data-live-distance-label="true" x="${(a[0]+b[0])/2+10}" y="${(a[1]+b[1])/2-10}" fill="#ffffff" stroke="#10252d" stroke-width="4" paint-order="stroke" font-size="14">${label}</text>`;}}
   }
  }
  if(hoverSample){const p=J().projectWorld(hoverSample.point);if(p)html+=`<rect data-point-snap="true" data-source-id="${esc(hoverSample.source_id)}" x="${p[0]-6}" y="${p[1]-6}" width="12" height="12" fill="none" stroke="#70f5ef" stroke-width="2"/><circle cx="${p[0]}" cy="${p[1]}" r="2" fill="#ffffff"/>`;}
  overlay.innerHTML=html;
 }
 function hoverEnabled(){return !pending&&(picking||panel.open&&view==='editor'&&operation==='point_plane');}
 function hoverPoint(sample){hoverSample=sample;draw();}
 function cancelLive(){clearTimeout(autoTimer);autoTimer=null;autoWanted=false;token++;planeRequest++;currentPick=null;hoverSample=null;overlay.innerHTML='';}
 async function loadReferencePlane(){
  const id=byId('measure-ref-plane')?.value,generation=++planeRequest;referencePlane=null;
  if(operation!=='point_plane'||!id){draw();return;}
  try{const value=await post('/api/measure-plane',{...stamp(),plane:{id}});if(generation!==planeRequest||operation!=='point_plane')return;referencePlane={id,points:value.points};draw();}catch(error){if(generation===planeRequest)setMessage(error.message,true);}
 }
 function scheduleLive(){
  clearTimeout(autoTimer);draw();if(operation!=='point_plane')return;
  autoTimer=setTimeout(()=>{autoTimer=null;void calculateLive();},120);
 }
 async function calculateLive(){
  if(operation!=='point_plane'||!panel.open||view!=='editor'||picking||pending)return;
  if(autoTask){autoWanted=true;return autoTask;}
  if(busy){scheduleLive();return;}
  let input;try{input=inputs();}catch{setMessage('Pick or enter the plane and measured point. The distance updates automatically.');return;}
  const generation=token;livePending=true;refreshSaveButton();setMessage('Updating point-to-plane distance…');
  autoTask=(async()=>{
   try{const value=await post('/api/measure-preview',{...stamp(),input,live:true});if(generation!==token||operation!=='point_plane'||!panel.open)return;
    preview=value.preview;inputDirty=false;showResult(preview.result);setMessage('Distance updated. Continue placing points or save this measurement.');draw();
   }catch(error){if(generation===token){preview=null;inputDirty=true;byId('measure-result').hidden=true;setMessage(error.message,true);}}
   finally{livePending=false;autoTask=null;refreshSaveButton();if(autoWanted){autoWanted=false;scheduleLive();}}
  })();return autoTask;
 }
 const setMessage=(text,error=false)=>{byId('measure-tools-message').textContent=text;byId('measure-tools-message').classList.toggle('error',error);};
 const guard=fn=>Promise.resolve().then(fn).catch(error=>{setMessage(error.message||String(error),true);});
 const stamp=()=>({context:model?.context,revision:model?.revision});
 const pointKey=(key,index)=>'measure-'+key+'-'+index;
 function finiteInput(element,label){const text=element.value.trim();const value=Number(text);if(!text||!Number.isFinite(value))throw Error(label+' needs a finite number.');return value;}
 function readPoint(key,index){return ['e','n','h'].map((axis,j)=>finiteInput(byId(pointKey(key,index)+'-'+axis),['E / X','N / Y','H / Z'][j]));}
 function writePoint(key,index,point){['e','n','h'].forEach((axis,j)=>{byId(pointKey(key,index)+'-'+axis).value=String(point[j]);});}
 function pointRow(key,index,label,p=null){return `<fieldset id="${pointKey(key,index)}" class="measure-point-row" data-point-key="${key}" data-point-index="${index}"><legend>${esc(label)}</legend><div class="measure-coordinate-grid">${['e','n','h'].map((axis,j)=>`<label>${['E / X','N / Y','H / Z'][j]}<input id="${pointKey(key,index)}-${axis}" type="number" step="any" value="${p?esc(p[j]):''}" aria-label="${esc(label)} ${['E / X','N / Y','H / Z'][j]} in metres"></label>`).join('')}</div><button type="button" class="measure-pick" data-measure-pick="${key}" data-index="${index}" title="Click an existing scan point; its original float64 XYZ is recovered before use.">${icon('point')}Pick measured point</button></fieldset>`;}
 function optionsFor(kind){return (model?.references||[]).filter(x=>kind==='line'?x.line:kind==='triangle'?x.kind==='cad'&&x.triangles>0:x.kind==='cad'&&x.plane);}
 function referenceFields(key,label,kind,value={}){
  const n=kind==='line'?2:3,choices=optionsFor(kind),id=value.id||'';
  return `<section class="measure-reference"><h3>${esc(label)}</h3><label>Definition<select data-measure-reference="${key}" data-kind="${kind}" id="measure-ref-${key}"><option value="">${n} points · pick or enter coordinates</option>${choices.map(x=>`<option value="${esc(x.id)}" ${x.id===id?'selected':''}>${esc(x.name)}${kind==='plane'?' · planar geometry required':''}</option>`).join('')}</select></label>${kind==='triangle'?`<label id="measure-face-label-${key}" ${id?'':'hidden'}>Triangle number <input id="measure-face-${key}" type="number" min="1" step="1" value="${(value.face_index||0)+1}"><small>Starts at 1; the result includes the finite edges and corners of this triangle.</small></label>`:''}<div id="measure-ref-points-${key}" ${id?'hidden':''}>${Array.from({length:n},(_,i)=>pointRow(key,i,label+' '+String.fromCharCode(65+i),value.points?.[i])).join('')}</div></section>`;
 }
 function refreshSaveButton(){const ready=!!preview||!!editId&&!inputDirty;byId('measure-save').disabled=pending||livePending||!ready;byId('measure-save').textContent=editId?'Save changes':'Save measurement';byId('measure-calculate').disabled=pending||livePending;byId('measure-operation').disabled=pending;byId('measure-remove').disabled=pending;byId('measure-tools-close').disabled=pending;}
 function invalidate(){token++;preview=null;inputDirty=true;byId('measure-result').hidden=true;refreshSaveButton();setMessage(operation==='point_plane'?'Point-to-plane distance updates as you place or edit points.':'Coordinates changed. Calculate to review the new result.');scheduleLive();}
 function unitSettings(result=null){
  const unit=result?.unit||(operation.startsWith('angle')?'°':operation==='surface'?'m²':['geometry','tunnel_volume'].includes(operation)?'m³':'m');
  const toleranceUnit=unit==='m'?'mm':unit;
  byId('measure-nominal-unit').textContent='('+unit+')';byId('measure-tolerance-unit').textContent='('+toleranceUnit+')';
  byId('measure-tolerance').dataset.factor=unit==='m'?'0.001':'1';
  byId('measure-tolerance-help').textContent='Optional: compare '+(result?.metrics?.[0]?.label||'the primary result')+' with an expected value. Nominal uses '+unit+'; tolerance uses '+toleranceUnit+'. Leave both blank to omit this check.';
 }
 function renderFields(value={}){
  const d=definitions[operation];byId('measure-operation').value=operation;byId('measure-tools-title').textContent=d.label;byId('measure-tools-intro').textContent=d.intro;
  let html='';
  for(const [key,label,n] of d.points||[]){for(let i=0;i<n;i++)html+=pointRow(key,i,n===1?label:operation==='angle_points'?['Point A','Point B · angle vertex','Point C'][i]:['Point A','Point B'][i],n===1?value[key]:value[key]?.[i]);}
  for(const [key,label,kind] of d.refs||[])html+=referenceFields(key,label,kind,value[key]||{});
  if(operation==='tunnel_volume'){
   const choices=model.references.filter(x=>x.kind==='mesh');
   html='<label>Tunnel wall mesh<select id="measure-object">'+choices.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('')+'</select></label>';
   const axis=model.alignment;
   html+=axis?`<p class="measure-tools-help">Alignment ${esc(axis.name)} · ${esc(axis.start)} to ${esc(axis.end)} m</p>`:'<p class="measure-tools-help">Load a tunnel alignment to define the end planes.</p>';
   for(const [key,label] of [['start_chainage','Start'],['end_chainage','End']]){
    html+=`<label>${label} chainage (m)<input id="measure-${key}" type="number" step="any" value="${esc(value[key]??'')}" ${axis?`min="${esc(axis.start)}" max="${esc(axis.end)}"`:''}></label><button type="button" data-volume-pick="${key}" ${axis?'':'disabled'}>Pick ${label.toLowerCase()} station in 3D</button>`;
   }
   if(!choices.length)html+='<p class="measure-tools-help">Create or import the tunnel wall mesh to measure.</p>';
  }else if(d.object){const choices=(model?.references||[]).filter(x=>operation==='geometry'||x.triangles||x.kind==='cad'&&x.vertices>=3);html=`<label>Geometry or surface<select id="measure-object">${choices.map(x=>`<option value="${esc(x.id)}">${esc(x.name)} · ${x.kind==='mesh'?'imported mesh':x.solid?'closed solid':'CAD geometry'}</option>`).join('')}</select><small>Triangle areas use every original face. A closed CAD solid is required for volume.</small></label>`;}
  byId('measure-inputs').innerHTML=html;
  if(d.object){const selected=value.object_id||window.CadUI?.selectedIds?.()[0]||J()?.selectedItem?.()?.id;if([...byId('measure-object').options].some(x=>x.value===selected))byId('measure-object').value=selected;}
  byId('measure-inputs').querySelectorAll('input,select').forEach(el=>el.addEventListener('input',invalidate));
  byId('measure-inputs').querySelectorAll('[data-volume-pick]').forEach(button=>button.onclick=()=>guard(()=>pickChainage(button.dataset.volumePick)));
  byId('measure-inputs').querySelectorAll('[data-measure-reference]').forEach(el=>el.onchange=()=>{byId('measure-ref-points-'+el.dataset.measureReference).hidden=!!el.value;const face=byId('measure-face-label-'+el.dataset.measureReference);if(face)face.hidden=!el.value;invalidate();if(operation==='point_plane')loadReferencePlane();});
  byId('measure-inputs').querySelectorAll('[data-measure-pick]').forEach(button=>button.onclick=()=>guard(()=>pick(button.dataset.measurePick,Number(button.dataset.index))));
  unitSettings();refreshSaveButton();if(operation==='point_plane')loadReferencePlane();draw();
 }
 function inputs(){
  const d=definitions[operation],value={operation};
  for(const [key,,n] of d.points||[])value[key]=n===1?readPoint(key,0):Array.from({length:n},(_,i)=>readPoint(key,i));
  for(const [key,,kind] of d.refs||[]){const id=byId('measure-ref-'+key).value;value[key]=id?{id}:{points:Array.from({length:kind==='line'?2:3},(_,i)=>readPoint(key,i))};if(id&&kind==='triangle'){const n=finiteInput(byId('measure-face-'+key),'Triangle number');if(!Number.isInteger(n)||n<1)throw Error('Triangle number must be a whole number starting at 1.');value[key].face_index=n-1;}}
  if(d.object){value.object_id=byId('measure-object').value;if(!value.object_id)throw Error('Create or import the geometry you want to measure first.');}
  if(operation==='tunnel_volume'){
   if(!model.alignment)throw Error('Load the tunnel alignment before measuring volume.');
   for(const key of ['start_chainage','end_chainage'])value[key]=finiteInput(byId('measure-'+key),key==='start_chainage'?'Start chainage':'End chainage');
   if(value.end_chainage<=value.start_chainage)throw Error('End chainage must be greater than start chainage.');
  }
  return value;
 }
 function metadata(){
  const nominal=byId('measure-nominal').value.trim(),tolerance=byId('measure-tolerance').value.trim();
  return {name:byId('measure-name').value,comment:byId('measure-comment').value,show_label:byId('measure-show-label').checked,
   nominal:nominal===''?null:finiteInput(byId('measure-nominal'),'Nominal value'),tolerance:tolerance===''?null:finiteInput(byId('measure-tolerance'),'Tolerance')*Number(byId('measure-tolerance').dataset.factor||1)};
 }
 function showResult(result){
  byId('measure-result').hidden=false;
  byId('measure-metrics').innerHTML=result.metrics.map(m=>`<div><dt>${esc(m.label)}</dt><dd>${Number(m.value).toLocaleString(undefined,{maximumFractionDigits:8})} <span>${esc(m.unit)}</span></dd></div>`).join('');
  byId('measure-notes').innerHTML=(result.notes||[]).map(text=>`<p class="measure-tools-help">${esc(text)}</p>`).join('');unitSettings(result);
 }
 async function load(){model=await api('/api/measures');byId('measure-saved-tab').textContent='Saved measures ('+model.measurements.length+')';return model;}
 function show(which){view=which;byId('measure-editor').hidden=which!=='editor';byId('measure-saved').hidden=which!=='saved';byId('measure-new-tab').classList.toggle('chosen',which==='editor');byId('measure-saved-tab').classList.toggle('chosen',which==='saved');if(which==='saved'){cancelLive();byId('measure-tools-title').textContent='Saved measurements';renderSaved();}draw();}
 function showPanel(){if(!panel.open)panel.show();panel.style.maxHeight=Math.max(240,innerHeight-parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ts-ribbon-bottom')||'194')-24)+'px';}
 function close(){if(pending)return;cancelLive();pickGeneration++;picking=false;J()?.setMode('orbit');panel.close();}
 function closeInactive(){if(pending||picking)return false;if(panel.open)close();return true;}
 async function open(mode='point_point'){
  if(picking){window.TunnelRibbon?.explainToolBlock?.('Finish the measured point pick or press Escape before opening another measurement.');return;}if(pending||busy)return;
  if(window.TunnelRibbon?.prepareTool?.('measure')===false)return;
  if(window.CadUI?.modelSpace?.()&&window.CadUI.enter('Measure',{openTool:false})===false)return;
  cancelLive();referencePlane=null;await load();showPanel();setMessage('');
  if(mode==='list'){show('saved');return;}
  operation=definitions[mode]?mode:'point_point';editId=null;preview=null;inputDirty=true;byId('measure-name').value=definitions[operation].label;
  byId('measure-comment').value='';byId('measure-nominal').value='';byId('measure-tolerance').value='';byId('measure-show-label').checked=true;
  byId('measure-result').hidden=true;byId('measure-remove').hidden=true;renderFields();show('editor');
 }
 async function edit(id){
  if(busy||picking)return;if(window.TunnelRibbon?.prepareTool?.('measure')===false)return;cancelLive();await load();const row=model.measurements.find(x=>x.id===id);if(!row)throw Error('This saved measurement is no longer available.');
  operation=row.data.operation;editId=id;preview=null;inputDirty=false;renderFields(row.data.inputs);showResult(row.data);
  byId('measure-name').value=row.name;byId('measure-comment').value=row.data.comment||'';byId('measure-show-label').checked=row.data.show_label!==false;
  byId('measure-nominal').value=row.data.nominal??'';byId('measure-tolerance').value=row.data.tolerance==null?'':row.data.tolerance/Number(byId('measure-tolerance').dataset.factor||1);
  byId('measure-remove').hidden=false;showPanel();show('editor');refreshSaveButton();setMessage(row.data.within_tolerance==null?'Saved coordinate snapshot. Edit labels, or change coordinates and calculate again.':row.data.within_tolerance?'Saved result is within tolerance.':'Saved result is outside tolerance.');
 }
 async function calculate(){
  if(operation==='point_plane')return calculateLive();
  if(pending||busy)return;const input=inputs(),generation=++token;pending=true;refreshSaveButton();setMessage('Calculating…');byId('measure-cancel-job').hidden=false;
  try{
   await post('/api/measure-preview',{...stamp(),input});setBusy(true);
   const job=await J().waitReady(status=>setMessage(status.message||'Measuring…'),{skipSync:true});
   if(job.cancelled){setMessage('Calculation cancelled. Saved measurements are unchanged.');return;}
   await load();if(generation!==token)return;preview=model.preview;
   if(!preview)throw Error('The workspace changed during calculation. Calculate again.');
   inputDirty=false;showResult(preview.result);setMessage('Result ready. Review it, then save this measurement.');
  }finally{pending=false;setBusy(false);byId('measure-cancel-job').hidden=true;refreshSaveButton();}
 }
 async function save(){
  if(pending||busy)return;if(!preview&&(!editId||inputDirty))throw Error('Calculate this measurement before saving.');
  pending=true;refreshSaveButton();
  try{const result=await post('/api/measure-action',{...stamp(),action:preview?'save':'update',id:editId,...(preview?{token:preview.token}:{}),...metadata()});await load();await J().sync();await poll();await edit(result.id);window.SceneObjectsUI?.select(result.id);setMessage('Measurement saved. Workspace Undo is available.');}
  finally{pending=false;refreshSaveButton();}
 }
 async function remove(){if(!editId||pending||busy)return;if(!confirm('Remove this saved measurement? Workspace Undo can restore it.'))return;await post('/api/measure-action',{...stamp(),action:'remove',id:editId});editId=null;preview=null;await load();await J().sync();show('saved');setMessage('Measurement removed. Workspace Undo is available.');}
 function renderSaved(){
  const rows=model?.measurements||[];
  byId('measure-saved-list').innerHTML=rows.length?rows.map(row=>`<article class="measure-saved-row"><button type="button" class="measure-saved-open" data-open-measure="${esc(row.id)}"><strong>${esc(row.name)}</strong><span>${esc(row.summary)}</span>${row.data.comment?`<small>${esc(row.data.comment)}</small>`:''}</button><button type="button" class="visibility-toggle" data-measure-visible="${esc(row.id)}" aria-pressed="${row.visible}" aria-label="${row.visible?'Hide':'Show'} ${esc(row.name)}">${window.ToolIcons.bulb(row.visible)}</button></article>`).join(''):'<div class="measure-empty"><strong>No saved measurements yet</strong><p>Choose a distance, angle, surface or geometry tool, calculate, then save.</p></div>';
  byId('measure-saved-list').querySelectorAll('[data-open-measure]').forEach(button=>button.onclick=()=>guard(()=>edit(button.dataset.openMeasure)));
  byId('measure-saved-list').querySelectorAll('[data-measure-visible]').forEach(button=>button.onclick=()=>guard(async()=>{await post('/api/measure-action',{...stamp(),action:'visibility',id:button.dataset.measureVisible});await load();renderSaved();await J().sync();}));
  byId('measure-export-save').disabled=!rows.length||pending;byId('measure-export-current').disabled=!editId;if(!editId)byId('measure-export-current').checked=false;
 }
 async function recoverPoint(world,picked=null){
  const pixel=J().projectWorld(world),sample=picked||(pixel?J().cadCloudPoint(pixel):null);
  if(!sample)throw Error('No original scan source could be resolved. Return to the scan and pick again.');
  const cad=await api('/api/cad');await post('/api/cad-pick',{...sample,revision:cad.revision,context:cad.context});setBusy(true);
  const job=await J().waitReady(null,{skipSync:true});if(job.cancelled)throw Error('Point picking cancelled.');
  const result=await api('/api/cad-pick');if(!result.point)throw Error('No retained measured point was recovered.');return result;
 }
 async function pick(key,index){
  if(pending||busy)return;
  clearTimeout(autoTimer);token++;preview=null;inputDirty=true;const generation=++pickGeneration;picking=true;currentPick={key,index};hoverSample=null;panel.close();draw();
  if(window.CadUI?.modelSpace?.()&&window.CadUI.enter('Measure',{openTool:false})===false){picking=false;currentPick=null;showPanel();draw();return;}
  const armed=J().armPointPick((world,sample)=>guard(async()=>{
   try{const found=await recoverPoint(world,sample);if(generation!==pickGeneration)return;await load();writePoint(key,index,found.point);invalidate();showPanel();setMessage('Original measured XYZ recovered · point '+found.index.toLocaleString()+'.');}
   catch(error){showPanel();throw error;}finally{picking=false;currentPick=null;hoverSample=null;setBusy(false);scheduleLive();}
  }),{prompt:'Measurement · click '+byId(pointKey(key,index)).querySelector('legend').textContent,done:'Recovering original point coordinates…'});
  if(!armed){picking=false;currentPick=null;showPanel();draw();}
 }
 async function pickChainage(key){
  if(pending||busy||picking)return;
  const generation=++pickGeneration;
  picking=true;panel.close();
  const armed=J().armPointPick(world=>guard(async()=>{
   try{
    const response=await post('/api/measure-chainage',{...stamp(),point:world});
    if(generation!==pickGeneration)return;
    byId('measure-'+key).value=String(response.chainage);
    invalidate();setMessage('Picked alignment chainage '+response.chainage.toFixed(3)+' m. You can edit it before calculating.');
   }finally{if(generation===pickGeneration){picking=false;showPanel();}}
  }),{prompt:'Pick a displayed tunnel point near the desired section. Its plan position is projected onto the alignment.'});
  if(!armed){picking=false;showPanel();}
 }
 async function pickPoint(){
  if(picking){window.TunnelRibbon?.explainToolBlock?.('Finish the measured point pick or press Escape before starting another pick.');return;}if(pending||busy)return;
  if(window.TunnelRibbon?.prepareTool?.('measure')===false)return;
  if(window.CadUI?.modelSpace?.()&&window.CadUI.enter('Measure',{openTool:false})===false)return;
  J().armPointPick(world=>guard(async()=>{try{const found=await recoverPoint(world);showTunnelMessage('Measured point coordinates',`E / X: ${found.point[0].toFixed(6)} m\nN / Y: ${found.point[1].toFixed(6)} m\nH / Z: ${found.point[2].toFixed(6)} m\nOriginal retained point index: ${found.index.toLocaleString()}\nRecovered from the float64 scan cache.`,false);}finally{setBusy(false);}}),{prompt:'Pick Point · click a measured scan point'});
 }
 function manualPath(show=true){byId('measure-export-path-label').hidden=!show;byId('measure-export-manual').textContent=show?'Use save window…':'Enter destination path';if(show)byId('measure-export-path').focus();}
 async function exportSaved(){
  if(pending||busy)return;const fmt=byId('measure-export-format').value,ids=byId('measure-export-current').checked?[editId]:model.measurements.map(x=>x.id);if(!ids.length)throw Error('Save a measurement first.');
  const filename='Tunnel_Studio_Measurements.'+fmt;let destination=null,handle=null;
  if(!byId('measure-export-path-label').hidden){destination=byId('measure-export-path').value.trim();if(!destination)throw Error('Enter the full folder and filename.');}
  else if(window.pywebview?.api?.choose_export_file){
   let choice;try{choice=await window.pywebview.api.choose_export_file(filename,fmt);}catch(error){manualPath();setMessage('The save window is unavailable. Enter a destination path. '+error.message,true);return;}
   if(choice?.cancelled){setMessage('Save cancelled. No file was saved.');return;}
   if(!choice?.path){manualPath();setMessage(choice?.error||'No save destination was returned. Enter a full path.',true);return;}destination=choice.path;
  }else if(window.showSaveFilePicker){try{handle=await window.showSaveFilePicker({suggestedName:filename,types:[{description:fmt.toUpperCase()+' measurements',accept:{'application/octet-stream':['.'+fmt]}}]});}catch(error){if(error.name==='AbortError'){setMessage('Save cancelled. No file was saved.');return;}manualPath();throw error;}}
  else{manualPath();setMessage('Enter the full folder and filename, then click Save as….');return;}
  pending=true;byId('measure-export-save').disabled=true;
  try{
   if(destination)destination=(await post('/api/export-destination',{destination,format:fmt})).destination;
   const prepared=await post('/api/measure-export',{...stamp(),ids,format:fmt});
   if(destination){let saved=await post('/api/export-save',{id:prepared.id,destination});if(saved.needs_overwrite){if(!confirm('Replace the existing file?\n'+saved.destination)){setMessage('Save cancelled. The existing file is unchanged.');return;}saved=await post('/api/export-save',{id:prepared.id,destination:saved.destination,replace_version:saved.replace_version});}if(!saved.saved)throw Error('The destination changed. Choose Save as… again.');setMessage('Saved successfully: '+saved.destination);}
   else{const response=await fetch('/api/export-file?'+new URLSearchParams({id:prepared.id}));if(!response.ok)throw Error('Could not read the prepared measurement export.');let writer;try{writer=await handle.createWritable();await response.body.pipeTo(writer);}catch(error){if(writer)try{await writer.abort();}catch(_){}throw error;}setMessage('Saved successfully: '+handle.name);}
  }finally{pending=false;renderSaved();refreshSaveButton();}
 }
 byId('measure-tools-close').onclick=close;panel.addEventListener('cancel',event=>{event.preventDefault();close();});
 byId('measure-operation').onchange=()=>guard(()=>open(byId('measure-operation').value));
 byId('measure-new-tab').onclick=()=>guard(()=>open(operation));byId('measure-saved-tab').onclick=()=>guard(async()=>{await load();show('saved');});
 byId('measure-calculate').onclick=()=>guard(calculate);byId('measure-save').onclick=()=>guard(save);byId('measure-remove').onclick=()=>guard(remove);
 byId('measure-cancel-job').onclick=()=>guard(async()=>{await post('/api/cancel-operation',{});setMessage('Stopping at the next triangle batch…');});
 byId('measure-export-save').onclick=()=>guard(exportSaved);byId('measure-export-manual').onclick=()=>manualPath(byId('measure-export-path-label').hidden);
 window.addEventListener('resize',()=>{if(panel.open)showPanel();});
 window.addEventListener('keydown',event=>{if(event.key==='Escape'&&picking&&!panel.open){pickGeneration++;picking=false;currentPick=null;hoverSample=null;showPanel();draw();scheduleLive();setMessage('Point picking cancelled. The existing coordinates are unchanged.');}});
 function register(){const add=(group,commands)=>window.TunnelRibbon?.registerMeasureGroup(group,commands.map(([id,label,description,run,ico='measure'])=>({id,label,description,run,ico})));
  add('Pick', [['measure-point','Pick Point','Read original float64 coordinates of a retained scan point.',()=>guard(pickPoint),'point'],['measure-info','Pick Info','Read information for the selected scan, mesh or geometry item.',()=>J()?.showDetails?.(),'info']]);
  add('Edit measures',[['measure-edit','Edit Measures','Open saved distances, angles, areas and volumes; edit labels, comments and tolerances.',()=>guard(()=>open('list')),'edit']]);
  add('Distance',[['measure-distance','Between Points','Pick or enter two precise points; save 3D distance, horizontal distance and height difference.',()=>guard(()=>open('point_point'))],['measure-point-plane','Point Plane','Measure the perpendicular distance from a point to an infinite plane.',()=>guard(()=>open('point_plane'))],['measure-plane-plane','Between Planes','Measure parallel-plane separation and report the angle of nonparallel planes.',()=>guard(()=>open('plane_plane'))],['measure-point-triangle','Point Triangle','Measure the shortest distance to a finite triangle, including edges and corners.',()=>guard(()=>open('point_triangle'))]]);
  add('Angle',[['measure-angle-lines','Lines','Measure the smaller direction angle between two lines.',()=>guard(()=>open('angle_lines')),'angle'],['measure-angle-planes','Planes','Measure the smaller angle between two infinite planes.',()=>guard(()=>open('angle_planes')),'angle'],['measure-angle-points','Three Points','Measure the angle at the middle of three picked or entered points.',()=>guard(()=>open('angle_points')),'angle']]);
  add('Surface / volume',[['measure-tunnel-volume','Tunnel volume','Measure enclosed tunnel space between two perpendicular chainage sections.',()=>guard(()=>open('tunnel_volume')),'cube'],['measure-surface','Measure Surface','Measure every triangle or the enclosed area of a planar profile.',()=>guard(()=>open('surface')),'mesh'],['measure-geometry','Measure Geometry','Read curve length, area, extents and validated closed CAD solid volume.',()=>guard(()=>open('geometry')),'cube']]);
 }
 window.MeasureTools={draw,hoverEnabled,hoverPoint,planeGeometry,open:mode=>guard(()=>open(mode)),edit:id=>guard(()=>edit(id)),pickPoint:()=>guard(pickPoint),close,closeInactive,canDeactivate:()=>!pending&&!picking,isPicking:()=>picking,isOpen:()=>panel.open,register};register();
})();
