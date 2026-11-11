/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const style=document.createElement('style');style.textContent=`
 #tunnel-designer{width:min(1420px,96vw);max-width:96vw;height:91vh;max-height:96vh;padding:20px;box-sizing:border-box;color:#dbe6ef;background:#14202c;border:1px solid #40576c;border-radius:12px}
 #tunnel-designer[open]{display:flex;flex-direction:column;gap:12px}#tunnel-designer .td-heading{display:flex;gap:16px;align-items:center;justify-content:space-between}#tunnel-designer h2,#tunnel-designer h3,#tunnel-designer p{margin:0 0 10px}#tunnel-designer .td-heading input{width:min(360px,35vw)}
 #tunnel-designer .td-step-help{margin:0;padding:10px 12px;border:1px solid #3f596c;border-radius:6px;background:#1b3040;color:#d8e8f1;font-size:12px;line-height:1.5}#tunnel-designer nav{display:flex;gap:5px;flex-wrap:wrap}#tunnel-designer nav button{flex:1}#tunnel-designer nav button[aria-current=step]{background:#256b75;color:white;border-color:#68c6d0}
 #tunnel-designer .td-body{display:grid;grid-template-columns:minmax(400px,1.2fr) minmax(300px,1fr);gap:18px;flex:1;min-height:0}#tunnel-designer .td-editor{overflow:auto;padding-right:8px}#tunnel-designer .td-preview{min-height:0;display:flex;flex-direction:column;gap:8px}
 #tunnel-designer canvas{width:100%;background:#0d1721;border:1px solid #2d4355;border-radius:8px;min-height:100px;touch-action:none}#td-plot{flex:1;cursor:grab}#td-plot[data-sketch=true]{cursor:crosshair}#tunnel-designer .td-plot-tools{display:flex;flex-wrap:wrap;align-items:center;gap:6px}#tunnel-designer .td-plot-tools span{flex:1;min-width:120px}#tunnel-designer [hidden]{display:none!important}#td-wire{flex:1.3;cursor:grab}
 #tunnel-designer .td-fields{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}#tunnel-designer label{display:flex;flex-direction:column;gap:5px;flex:1;font-size:12px;min-width:100px}#tunnel-designer label input,#tunnel-designer label select{width:100%;box-sizing:border-box}
 #tunnel-designer input,#tunnel-designer select{color:#e2edf6;background:#1b2c3b;border:1px solid #486074;border-radius:4px;padding:8px;min-width:0}#tunnel-designer input[type=number]{min-width:72px}#tunnel-designer table{width:100%;border-collapse:collapse;margin:10px 0}#tunnel-designer th{font-size:11px;text-align:left;color:#a7bece;padding:5px}#tunnel-designer td{padding:3px}#tunnel-designer td input,#tunnel-designer td select{width:100%;box-sizing:border-box}#tunnel-designer td:last-child button{padding:6px}
 #tunnel-designer .td-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}#tunnel-designer .td-note{font-size:12px;line-height:1.55;color:#adbfce}#tunnel-designer .td-footer{border-top:1px solid #324b60;padding-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}#td-status{flex:1;min-width:200px;font-size:12px;white-space:pre-wrap}#td-status[data-error=true]{color:#ffb8a8}#td-summary{font-size:12px;min-height:30px;color:#9bd4da}#tunnel-designer .td-profile-head{display:flex;gap:8px;align-items:end}#tunnel-designer .td-profile-head label{flex:1}
 #tunnel-designer .td-section{border:1px solid #40576c;border-radius:8px;padding:12px;margin:12px 0}#tunnel-designer .td-section.active{border-color:#ffd780}#tunnel-designer .td-section-header{display:flex;align-items:center;gap:8px}#tunnel-designer .td-section-header strong{flex:1}.td-drawbar{position:fixed;top:90px;left:50%;transform:translateX(-50%);z-index:90;background:#142b39;color:#e8f3fa;border:1px solid #7cd8da;border-radius:10px;padding:14px;max-width:min(660px,90vw);box-shadow:0 6px 28px #0008;display:flex;gap:12px;align-items:center}.td-drawbar[hidden]{display:none}.td-drawbar p{margin:4px 0;font-size:13px}
 #tunnel-designer .td-heading,#tunnel-designer nav,#tunnel-designer .td-step-help,#tunnel-designer .td-footer,#tunnel-designer>.window-tools{flex-shrink:0}
 #tunnel-designer .td-body{overflow:hidden}#tunnel-designer .td-preview{overflow:auto}#tunnel-designer button{font-size:12px;line-height:1.3}
 @media(max-width:900px){#tunnel-designer .td-body{display:flex;flex-direction:column;overflow:auto}#tunnel-designer .td-editor{overflow:visible;flex:0 0 auto}#tunnel-designer .td-preview{min-height:360px;flex:0 0 360px;overflow:hidden}#tunnel-designer nav button{min-width:100px}}
 @media(max-height:650px){
  #tunnel-designer{height:96vh;max-height:96vh;padding:10px 12px}#tunnel-designer[open]{gap:7px}
  #tunnel-designer .td-heading{gap:8px}#tunnel-designer .td-heading h2{font-size:16px;margin:0}#tunnel-designer .td-heading>.td-note,#tunnel-designer .td-heading>div>.td-note{display:none}
  #tunnel-designer .td-heading input{width:min(220px,25vw);padding:6px}#tunnel-designer .td-heading button{padding:6px 9px;white-space:nowrap}
  #tunnel-designer nav{gap:4px;flex-wrap:nowrap}#tunnel-designer nav button{min-width:0;min-height:34px;padding:5px 7px;font-size:11px}
  #tunnel-designer .td-step-help{padding:6px 9px;font-size:11px;line-height:1.4}
  #tunnel-designer .td-body{display:flex;flex-direction:column;gap:10px;overflow:auto;min-height:120px}
  #tunnel-designer .td-editor{overflow:visible;flex:0 0 auto;padding-right:5px}#tunnel-designer .td-editor h3{font-size:14px}
  #tunnel-designer .td-preview{flex:0 0 320px;min-height:320px;overflow:hidden}
  #tunnel-designer .td-footer{padding-top:7px;gap:6px}#tunnel-designer .td-footer button{padding:7px 10px;font-size:11px}
  #tunnel-designer #td-status{font-size:11px;line-height:1.3;min-width:100px;max-height:2.8em;overflow:auto}
  #tunnel-designer>.window-tools{padding:0}#tunnel-designer>.window-tools button{font-size:10px;padding:3px 7px}
 }
 #tunnel-designer[data-step="2"] .td-body{grid-template-columns:minmax(285px,.66fr) minmax(360px,1.5fr)}
 #tunnel-designer[data-step="2"] .td-preview{overflow:hidden}
 #tunnel-designer[data-expanded="true"] .td-body{grid-template-columns:minmax(0,1fr)}
 #tunnel-designer[data-expanded="true"] .td-editor{display:none}
 #tunnel-designer .td-profile-draw{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
 #tunnel-designer .td-profile-draw button[aria-pressed="true"]{background:#256b75;border-color:#68c6d0;color:#fff}
 #tunnel-designer .td-profile-draw label{min-width:0;flex:0 0 auto;flex-direction:row;align-items:center}
 #tunnel-designer .td-profile-draw label input[type=number]{width:72px;padding:4px 5px}
 #td-plot{box-sizing:border-box;display:block;min-width:0;min-height:120px}
 #td-plot[data-grip="true"]{cursor:move}
 #tunnel-designer .td-drawing-panel{border-top:1px solid #324b60;margin-top:12px;padding-top:12px}
 #tunnel-designer .td-drawing-list{max-height:168px;overflow:auto}
 #tunnel-designer .td-drawing-choice{display:flex;flex-direction:row;align-items:center;padding:5px 0}
 #tunnel-designer .td-drawing-choice input{width:auto}
 @media(max-width:900px),(max-height:650px){
  #tunnel-designer[data-step="2"] .td-preview{order:-1;flex-basis:clamp(300px,56vh,600px);min-height:300px}
  #tunnel-designer[data-expanded="true"] .td-preview{flex:1 0 300px}
 }
 `;document.head.append(style);
 const stepLabels=['Plan alignment','Heights / gradients','Tunnel profiles','Chainage ranges','Preview & build'];
 const stepGuidance=[
  'The alignment is the route through the tunnel in plan. Draw its centreline in 3D, or enter Easting and Northing coordinates. Next, set heights along that route.',
  'Set a height at each chainage where the slope changes. Add as many height rows as your design needs; the gradient between neighbouring rows is calculated for you.',
  'Draw or edit the cross-section on the X/Y plane. Assign the finished profile in Chainage ranges.',
  'Choose where each profile is used: enter the start and end chainages or pick them in 3D. Use Constant theoretical profile for one shape, or Interpolate between profiles for a transition.',
  'Check the 3D preview, section direction and spacing, then select Create 3D model. To change it later, select the model and choose Model → Edit tunnel design.'
 ];
 const el=document.createElement('dialog');el.id='tunnel-designer';el.innerHTML=`<div class="td-heading"><div><h2 id="td-title">Create 3D model from alignment</h2><span class="td-note">Plan alignment → heights and gradients → tunnel profiles → chainage ranges → 3D model · coordinates in metres</span></div><input id="td-name" aria-label="Design name" maxlength="120"><button id="td-new">New design</button><button id="td-close" aria-label="Close designer">×</button></div><nav aria-label="Design steps">${stepLabels.map((x,i)=>`<button data-step="${i}" title="${stepGuidance[i]}">${i+1}. ${x}</button>`).join('')}</nav><p id="td-step-help" class="td-step-help"></p><div class="td-body"><div id="td-editor" class="td-editor"></div><div class="td-preview"><div id="td-summary" role="status">Preview</div><div class="td-plot-tools"><button id="td-profile-error" hidden>Edit profile with error</button><button id="td-range-shortcut" hidden>Choose model range</button><button id="td-plot-fit">Fit</button><span id="td-plot-readout" class="td-note">Wheel to zoom · drag to pan</span><button id="td-sketch-undo" hidden>Undo point</button><button id="td-sketch-finish" hidden>Close outline</button><button id="td-sketch-open" hidden>Finish open</button><button id="td-sketch-cancel" hidden>Cancel sketch</button></div><div class="td-profile-draw" id="td-profile-draw" hidden aria-label="Cross-section tools"><button data-profile-draw="line">Line</button><button data-profile-draw="custom">Polyline</button><button data-profile-draw="circle">Circle</button><button data-profile-draw="rectangle">Rectangle</button><button data-profile-draw="arch">Arch</button><button id="td-origin">Centre on origin</button><button id="td-expand" aria-pressed="false">Expand</button><label><input type="checkbox" id="td-show-profiles"> Show all profiles</label><label><input type="checkbox" id="td-object-snap" checked> Object snap</label><label><input type="checkbox" id="td-grid-snap"> Grid snap</label><label>Step · m<input id="td-grid-step" type="number" min="0.001" step="any" value="0.01"></label></div><form class="td-profile-draw" id="td-coordinate-entry" hidden><label><span id="td-coordinate-label">Point X,Y · m</span> <input id="td-coordinate" style="width:180px" aria-label="Drawing coordinates in metres" placeholder="0, 0" autocomplete="off"></label><button type="submit">Place</button></form><canvas id="td-plot" width="680" height="310" tabindex="0" aria-label="Alignment or profile diagram"></canvas><canvas id="td-wire" width="680" height="400" aria-label="3D design preview"></canvas><span id="td-wire-help" class="td-note">3D preview: drag to orbit · wheel to zoom. Build opens the solid in the main 3D viewer.</span></div></div><div class="td-footer"><button id="td-back">Back</button><button id="td-next">Next</button><span id="td-status" role="status"></span><button id="td-save">Save design</button><button id="td-build" class="primary">Create 3D model</button></div>`;document.body.append(el);
 const $=id=>document.getElementById('td-'+id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let pickState=null,sectionIndex=0,wireMap=null,wirePick=null;
 const plotViews=new Map();
 let plotFrame=null,plotDrag=null,plotHover=null,sketch=null,chain=null,previousSections=null,profilePlane='alignment',profileStation=null,plotSnap=null,editDrawingId=null;
 const drawingSelection=new Set();
 const drawingObjects=()=>d.drawing_objects??=[];
 const activeDrawing=()=>drawingObjects().find(o=>o.id===editDrawingId);
 const drawingKey=()=>'drawing-'+(globalThis.crypto?.randomUUID?.()||Date.now().toString(36)+Math.random().toString(36).slice(2));
 let d=null,modelId=null,context=null,revision=null,axis=null,step=0,profileIndex=0,info=null,pending=false,dirty=0,timer=null,previewRunning=false,previewAgain=false,yaw=-.7,pitch=.45,zoom=1;
 const api=async(path,body)=>{const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Tunnel-Token':window.TUNNEL_TOKEN},body:JSON.stringify(body)}:{});const v=await r.json();if(!r.ok)throw Error(v.error||'The operation could not finish.');return v;};
 const status=(text,error=false)=>{$('status').textContent=text;$('status').dataset.error=String(error);};
 const numeric=(path,value,label)=>`<input type="number" step="any" data-field="${path}" aria-label="${esc(label)}" value="${esc(Number.isFinite(value)?Number(value.toFixed(6)):value)}">`;
 const text=(path,value,label)=>`<input data-field="${path}" aria-label="${esc(label)}" value="${esc(value)}">`;
 const opts=(values,current)=>values.map(v=>`<option value="${esc(v[0])}" ${String(v[0])===String(current)?'selected':''}>${esc(v[1])}</option>`).join('');
 const select=(path,values,current,label)=>`<select data-field="${path}" aria-label="${esc(label)}">${opts(values,current)}</select>`;
 const field=(label,html)=>`<label>${label}${html}</label>`;
 const remove=(action,index)=>`<button data-action="${action}" data-index="${index}" aria-label="Remove row ${index+1}">×</button>`;
 function stations(){const v=[Number(d.start)];for(let i=1;i<d.plan.length;i++){const a=d.plan[i-1],b=d.plan[i],chord=Math.hypot(b[0]-a[0],b[1]-a[1]),r=Math.abs(a[2]);v.push(v.at(-1)+(r?2*r*Math.asin(Math.min(1,chord/(2*r))):chord));}return v;}
 function boundsChanged(oldEnd){const end=stations().at(-1);if(Math.abs(d.vertical.at(-1)[0]-oldEnd)<1e-7)d.vertical.at(-1)[0]=end;if(d.sections.length===1&&Math.abs(d.sections[0].end-oldEnd)<1e-7)d.sections[0].end=end;}
 function interpolate(ch){const a=d.vertical;let i=a.findIndex(r=>r[0]>=ch);if(i<0)return a.at(-1)[1];if(!i)return a[0][1];const [x,y]=a[i-1],[xx,yy]=a[i];return y+(yy-y)*(ch-x)/(xx-x);}
 function sectionType(r){return r.type||(r.first===r.last?'constant':'interpolate');}
 const transitionOptions=[['linear','Linear'],['smoothstep','Smooth at both ends']];
 function profileOptions(){return d.profiles.map(p=>[p.id,p.name]);}
 function selectedOutline(){
  const ids=window.CadUI.selectedIds?.()||[];
  return ids.length===1?window.CadUI.model().objects.find(o=>o.id===ids[0]&&o.closed&&!o.faces.length):null;
 }
 function profileTools(profile){
  const candidate=selectedOutline();
  const linked=profile.kind==='cad'&&window.CadUI.model().objects.find(o=>o.id===profile.object_id);
  const canMove=profile.kind==='custom'||linked&&['polyline','rectangle'].includes(linked.shape);
  return `<div class="td-actions"><button data-action="profile-link" ${candidate?'':'disabled'} title="Select one closed outline in Model space first">Use selected CAD outline</button>
   ${canMove?'<button data-action="profile-move">Move a node with CAD snaps</button>':''}</div>
   <details id="td-profile-tools"><summary>Offset and mirror</summary><div class="td-fields">
   <label>Offset · m<input id="td-profile-offset" type="number" step="any" placeholder="e.g. 0.30"></label>
   </div><div class="td-actions"><button data-action="profile-offset">Offset copy</button><button data-action="profile-mirror" title="Reflect across profile X = 0">Mirror left/right copy</button></div>
   <p class="td-note">Positive offsets expand the outline. Curved outlines without analytic dimensions use the selected curve detail.</p></details>`;
 }
 function drawingPanel(profile){
  const objects=drawingObjects(),obj=activeDrawing(),i=objects.indexOf(obj),base='drawing_objects.'+i;
  const option=(id,name)=>`<label class="td-drawing-choice"><input type="checkbox" data-drawing-id="${esc(id)}" ${drawingSelection.has(id)?'checked':''}> ${esc(name)}</label>`;
  let values='';
  if(obj){
   values=field('Object name',text(base+'.name',obj.name,'Drawing object name'));
   values+=`<div class="td-fields">${field('X · m',numeric(base+'.x',obj.x||0,'Drawing X'))}${field('Y · m',numeric(base+'.y',obj.y||0,'Drawing Y'))}</div>`;
   if(obj.kind==='circle')values+=field('Radius · m',numeric(base+'.radius',obj.radius,'Drawing radius'));
   if(['rectangle','arch'].includes(obj.kind))values+=`<div class="td-fields">${field('Width · m',numeric(base+'.width',obj.width,'Drawing width'))}${field('Height · m',numeric(base+'.height',obj.height,'Drawing height'))}</div>`;
   if(obj.nodes)values+=`<table><thead><tr><th>X · m</th><th>Y · m</th><th>Radius to next · m</th></tr></thead><tbody>${obj.nodes.map((node,j)=>`<tr>${node.map((value,k)=>`<td>${k===2&&obj.kind==='polyline'&&j===obj.nodes.length-1?'—':numeric(base+'.nodes.'+j+'.'+k,value,['Drawing node X','Drawing node Y','Drawing edge radius'][k])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
   values+=`<button data-action="drawing-remove">Remove drawing object</button>`;
  }
  if(!objects.length)return '';
  return `<section class="td-drawing-panel"><h3>Drawing objects</h3><div class="td-drawing-list">${profile.kind!=='composite'?option('profile','Include '+profile.name):''}${objects.map(o=>option(o.id,o.name)).join('')}</div><div class="td-actions"><button data-action="drawing-use" ${drawingSelection.size?'':'disabled'}>Use selected as profile</button></div>${values}</section>`;
 }
 function selectDrawing(id,additive=false,checked=true){
  if(!additive)drawingSelection.clear();
  if(id){if(checked)drawingSelection.add(id);else drawingSelection.delete(id);}
  editDrawingId=id&&id!=='profile'&&checked?id:[...drawingSelection].reverse().find(key=>key!=='profile')||null;
  plotSnap=null;render();
 }
 function selectProfile(index,keepView=false){
  if(keepView&&plotFrame)plotViews.set('profile:'+d.profiles[index].id,{...plotFrame.view});
  profileIndex=index;sketch=null;plotHover=plotSnap=null;editDrawingId=null;drawingSelection.clear();render();
 }
 async function useDrawingProfile(profile){
  if(sketch)throw Error('Finish or cancel the active drawing first.');
  const objects=drawingObjects().slice(),ids=[...drawingSelection].filter(id=>id!=='profile');
  if(drawingSelection.has('profile')&&profile.kind!=='composite'){
   if(objects.length>=128)throw Error('Use up to 128 drawing objects.');
   let copy=JSON.parse(JSON.stringify(profile));
   if(copy.kind==='cad'){
    const points=outlinePoints(profile);
    if(points.length<3)throw Error('Preview the linked CAD outline before using it.');
    copy={kind:'custom',nodes:points.map(([x,y])=>[x,y,0]),x:0,y:0};
   }
   const id=drawingKey();objects.push({...copy,id,name:profile.name+' outline'});ids.push(id);
  }
  if(!ids.length)throw Error('Select one or more drawing objects.');
  const shape={kind:'composite',drawing_ids:ids,x:0,y:0};
  let result;
  lock(true);
  try{result=await profileOperation('outline',{profile:shape,drawing_objects:objects});}
  finally{lock(false);}
  if(!Array.isArray(result?.points)||result.points.length<3)throw Error('The selected drawing does not form a closed profile.');
  d.drawing_objects=objects;Object.assign(profile,shape);
  drawingSelection.clear();editDrawingId=null;
  info={...info,profiles:[...(info?.profiles||[]).filter(row=>row.id!==profile.id),{id:profile.id,points:result.points}]};
  render();changed();status('Drawing objects now define '+profile.name+'.');
 }
 function transitionChain(){
  if(!chain)return `<div class="td-actions"><button data-action="chain-start" ${d.profiles.length<2?'disabled':''}>Build transition chain</button>
    ${previousSections?'<button data-action="chain-undo">Restore previous ranges</button>':''}</div>`;
  return `<div class="td-section"><h3>Transition chain</h3>
   <table><thead><tr><th>Chainage · m</th><th>Profile</th><th></th></tr></thead><tbody>${chain.rows.map((row,i)=>
    `<tr><td><input type="number" step="any" aria-label="Chainage ${i+1}" data-chain-row="${i}" data-chain-key="station" value="${esc(row.station)}"></td>
     <td><select aria-label="Profile ${i+1}" data-chain-row="${i}" data-chain-key="profile">${opts(profileOptions(),row.profile)}</select></td>
     <td><button data-action="chain-remove" data-index="${i}" aria-label="Remove chainage ${i+1}" ${chain.rows.length<=2?'disabled':''}>×</button></td></tr>`).join('')}
   </tbody></table><label>Transition<select id="td-chain-method">${opts(transitionOptions,chain.method)}</select></label>
   <div class="td-actions"><button data-action="chain-add" ${chain.rows.length>=32?'disabled':''}>Add chainage</button>
   <button data-action="chain-apply" class="primary">Replace ranges with this chain</button><button data-action="chain-cancel">Cancel</button></div></div>`;
 }
 function chainAction(name,index){
  if(name==='chain-start'){
   const start=d.sections[0].start,end=d.sections.at(-1).end,profiles=d.profiles.slice(0,3);
   chain={rows:profiles.map((p,i)=>({station:i===0?start:i===profiles.length-1?end:Number((start+(end-start)*i/(profiles.length-1)).toFixed(6)),profile:p.id})),method:'linear'};
  }else if(name==='chain-add'){
   const before=chain.rows.at(-2),last=chain.rows.at(-1);
   chain.rows.splice(-1,0,{station:Number.isFinite(before.station)&&Number.isFinite(last.station)?(before.station+last.station)/2:'',profile:last.profile});
  }else if(name==='chain-remove')chain.rows.splice(index,1);
  else if(name==='chain-cancel')chain=null;
  else if(name==='chain-undo'){
   d.sections=previousSections;previousSections=null;sectionIndex=0;changed();
  }else if(name==='chain-apply'){
   const {rows,method}=chain,end=stations().at(-1);
   if(rows.some((row,i)=>!Number.isFinite(row.station)||row.station<d.start-1e-7||row.station>end+1e-7||
      i>0&&row.station-rows[i-1].station<1e-6||!d.profiles.some(p=>p.id===row.profile)))
    throw Error('Enter increasing chainages within the alignment and choose a profile at each one.');
   previousSections=d.sections;
   d.sections=rows.slice(1).map((row,i)=>({start:rows[i].station,end:row.station,first:rows[i].profile,last:row.profile,
    type:rows[i].profile===row.profile?'constant':'interpolate',interpolation:method}));
   chain=null;sectionIndex=0;changed();
  }
  render();
 }
 function addProfile(profile,name){
  if(d.profiles.length>=32)throw Error('Use up to 32 profiles in one design.');
  d.profiles.push({...profile,id:window.crypto?.randomUUID?.()||'p'+Date.now().toString(36)+d.profiles.length,name});
  profileIndex=d.profiles.length-1;sketch=null;
 }
 function cadOptions(){return (window.CadUI?.model?.()?.objects||[]).filter(o=>o.closed&&!o.faces.length).map(o=>[o.id,o.name]);}
 function render(){if(!d)return;el.dataset.step=String(step);if(step!==2)el.dataset.expanded='false';$('title').textContent=modelId?'Edit tunnel design':'Create 3D model from alignment';$('step-help').textContent=modelId&&step===4?'Review your changes, then select Update 3D model. Choose Create a separate 3D copy to keep the original model as well.':stepGuidance[step];profileIndex=Math.min(profileIndex,d.profiles.length-1);$('name').value=d.name||'Tunnel design';el.querySelectorAll('[data-step]').forEach(b=>b.setAttribute('aria-current',Number(b.dataset.step)===step?'step':'false'));$('back').disabled=step===0;$('next').hidden=step===4;$('build').textContent=modelId?'Update 3D model':'Create 3D model';let html='';
 if(step===0){const ch=stations();html=`<h3>Plan alignment · the tunnel route</h3><p class="td-note">The main viewer faces the drawing plane when you start. Click the route in order, then select Finish. Edit coordinates and curve radii below.</p><div class="td-fields"><label>Drawing plane<select id="td-draw-plane"><option value="horizontal">Horizontal · E/N</option><option value="current">Current CAD work plane</option></select></label><label>Drawing height · m<input id="td-draw-height" type="number" step="any" value="${esc(d.vertical?.[0]?.[1]||0)}"></label></div><div class="td-actions"><button class="primary" data-action="draw-alignment">Start drawing alignment</button></div><details><summary>Coordinates and curves</summary><p class="td-note">Place centreline nodes in plan. Each row describes the segment to the next node. Radius 0 is straight; a positive radius turns left and a negative radius turns right (arcs up to 180°).</p><div class="td-fields">${field('Start chainage · m',numeric('start',d.start,'Start chainage'))}</div><table><thead><tr><th>Easting / X</th><th>Northing / Y</th><th>Radius to next · m</th><th>Chainage</th><th></th></tr></thead><tbody>${d.plan.map((r,i)=>`<tr>${r.map((v,j)=>`<td>${j===2&&i===d.plan.length-1?'End':numeric('plan.'+i+'.'+j,v,['Easting','Northing','Signed radius'][j]+' node '+(i+1))}</td>`).join('')}<td data-chainage="${i}">${ch[i].toFixed(3)}</td><td>${remove('plan-remove',i)}</td></tr>`).join('')}</tbody></table><div class="td-actions"><button data-action="plan-add">Add plan node</button><button data-action="active-axis" ${axis?.rows?.length?'':'disabled'}>Use active alignment</button></div><p class="td-note">Using an active alignment copies its coordinates and heights into this design. Designer chainage follows horizontal distance; supplied chainages are recalculated. Existing Along alignment modelling retains the imported chainages.</p></details>`;}
 if(step===1){html=`<h3>Vertical alignment · heights and gradients</h3><p class="td-note">Add a height at every chainage where the gradient changes. One design can contain many different heights. Heights interpolate linearly between rows; 1% gradient is 10 mm rise per metre. Enter heights in your project datum.</p><table><thead><tr><th>Chainage · m</th><th>Height / Z · m</th><th>Gradient to next</th><th></th></tr></thead><tbody>${d.vertical.map((r,i)=>`<tr><td>${numeric('vertical.'+i+'.0',r[0],'Chainage height row '+(i+1))}</td><td>${numeric('vertical.'+i+'.1',r[1],'Height at row '+(i+1))}</td><td data-gradient="${i}"></td><td>${remove('height-remove',i)}</td></tr>`).join('')}</tbody></table><div class="td-actions"><button data-action="height-add">Add chainage / height</button><button data-action="height-sort">Sort by chainage</button><button data-action="height-ends">Cover alignment ends</button></div><p class="td-note">The first and last height rows must cover ${stations()[0].toFixed(3)}–${stations().at(-1).toFixed(3)} m. You can return and change any height after building or reopening the project.</p>`;}
 if(step===2){const p=d.profiles[profileIndex],base='profiles.'+profileIndex,r=activeRadius();html=`<h3>Tunnel profiles</h3><details><summary>Draw on a plane in the 3D viewer</summary><div class="td-actions"><label>Plane<select id="td-profile-plane">${opts([['alignment','Perpendicular to tunnel'],['XZ','World X/Z'],['YZ','World Y/Z'],['XY','World X/Y'],['current','Current CAD work plane']],profilePlane)}</select></label><label id="td-profile-station-field" ${profilePlane!=='alignment'?'hidden':''}>Chainage · m<input id="td-profile-station" type="number" step="any" value="${esc(profileStation??d.sections[sectionIndex]?.start??d.start)}"></label><button data-action="draw-profile">Start 3D drawing</button></div></details><div class="td-profile-head">${field('Profile',`<select id="td-profile-choice">${opts(d.profiles.map((x,i)=>[i,x.name]),profileIndex)}</select>`)}<button data-action="profile-add">Add</button><button data-action="profile-copy">Duplicate</button><button data-action="profile-remove">Remove</button></div><div class="td-fields">${field('Name',text(base+'.name',p.name,'Profile name'))}${field('Shape',select(base+'.kind',[['circle','Circle'],['rectangle','Rectangle'],['arch','Arched'],['custom','Lines and arcs'],['cad','Existing CAD profile'],...(p.kind==='composite'?[['composite','Combined drawing']]:[])],p.kind,'Profile shape'))}</div>`;
 if(p.kind==='circle')html+=field('Radius · m',numeric(base+'.radius',p.radius,'Circle radius'));
 if(['rectangle','arch'].includes(p.kind))html+=`<div class="td-fields">${field('Width · m',numeric(base+'.width',p.width,'Profile width'))}${field('Total height · m',numeric(base+'.height',p.height,'Profile height'))}</div>`;
 if(p.kind==='cad')html+=field('Closed profile',select(base+'.object_id',[['','Choose a closed profile…'],...cadOptions()],p.object_id||'','Existing profile'))+'<div class="td-actions"><button data-action="cad-refresh">Refresh profile list</button></div><p class="td-note">The designer reads the linked CAD outline when previewing or rebuilding the model.</p>';
 if(p.kind==='composite')html+=`<p class="td-note">Combined from ${p.drawing_ids.length} drawing objects. Edit the source objects below.</p>`;
 if(p.kind==='custom')html+=`<p class="td-note">List boundary nodes in order. The last joins the first. Radius to next is 0 for a straight edge, positive for counterclockwise and negative for clockwise.</p><details id="td-boundary-table"><summary>Boundary coordinates and arcs</summary><table><thead><tr><th>X · m</th><th>Y · m</th><th>Radius to next · m</th><th></th></tr></thead><tbody>${(p.nodes||[]).map((r,i)=>`<tr>${r.map((v,j)=>`<td>${numeric(base+'.nodes.'+i+'.'+j,v,['X','Y','Radius'][j]+' profile node '+(i+1))}</td>`).join('')}<td>${remove('node-remove',i)}</td></tr>`).join('')}</tbody></table><button data-action="node-add">Add boundary node</button></details>`;
 html+=`<div class="td-fields">${field('Horizontal offset · m',numeric(base+'.x',p.x||0,'Profile horizontal offset'))}${field('Vertical offset · m',numeric(base+'.y',p.y||0,'Profile vertical offset'))}</div><p class="td-note">${d.sections.some(s=>s.first===p.id||sectionType(s)==='interpolate'&&s.last===p.id)?'Assigned to a chainage range':'Unassigned · choose this profile in Chainage ranges'}</p>${r?`<button data-action="alignment-radius">Use alignment circle · R ${r.toFixed(3)} m</button>`:''}${profileTools(p)}${drawingPanel(p)}`;}
 if(step===3){html=`<h3>Chainage ranges · where each profile is used</h3><p class="td-note">Choose a constant section or a transition, then pick its start and end on the highlighted centreline in 3D. Chainages remain editable. Sections must touch and share the same boundary shape.</p>${transitionChain()}${d.sections.map((r,i)=>`<article class="td-section ${i===sectionIndex?'active':''}" data-section="${i}"><div class="td-section-header"><strong>Section ${i+1}</strong>${remove('section-remove',i)}</div>${field('Section type',select('sections.'+i+'.type',[['constant','Constant theoretical profile'],['interpolate','Interpolate between profiles']],sectionType(r),'Section '+(i+1)+' type'))}<div class="td-fields">${field('From · m',numeric('sections.'+i+'.start',r.start,'Section '+(i+1)+' start'))}${field('To · m',numeric('sections.'+i+'.end',r.end,'Section '+(i+1)+' end'))}</div><div class="td-actions"><button data-action="pick-range" data-index="${i}">Pick start / end in main 3D</button><button data-action="pick-preview" data-index="${i}">Pick in this preview</button></div><div class="td-fields">${field(sectionType(r)==='constant'?'Theoretical profile':'Start profile',select('sections.'+i+'.first',profileOptions(),r.first,'Section '+(i+1)+' start profile'))}${sectionType(r)==='interpolate'?field('End profile',select('sections.'+i+'.last',profileOptions(),r.last,'Section '+(i+1)+' end profile')):''}</div>${sectionType(r)==='interpolate'?field('Transition',select('sections.'+i+'.interpolation',transitionOptions,r.interpolation||'linear','Section '+(i+1)+' transition')):''}</article>`).join('')}<div class="td-actions"><button data-action="section-split">Split last section in two</button><button data-action="section-add">Append section</button><button data-action="section-full">One section over full alignment</button></div><p class="td-note">Alignment range: ${stations()[0].toFixed(3)}–${stations().at(-1).toFixed(3)} m. Model all of it or a shorter continuous range. Yellow highlights the selected section.</p>`;}
 if(step===4){html=`<h3>Preview and create the 3D tunnel model</h3><p class="td-note">The plan, heights and section transitions produce a closed solid. After building, orbit and zoom in the main viewer, inspect area / volume, or use Move, Rotate, Cut and Export. Open this model’s Parameters to edit its original design and rebuild.</p><div class="td-fields">${field('Requested profile spacing · m',numeric('spacing',d.spacing,'Profile spacing'))}${field('Curve quality',select('detail',[['fast','Fast'],['balanced','Balanced'],['fine','Fine']],d.detail,'Curve quality'))}</div>${field('Cross-section orientation',select('orientation',[['vertical','Keep sections vertical'],['normal','Perpendicular to 3D gradient']],d.orientation,'Cross-section orientation'))}<p class="td-note">Smaller spacing follows bends more closely. Curve quality controls arc facets. The Designer keeps every alignment and profile station. Review the actual maximum spacing when the requested spacing exceeds the project’s geometry capacity.</p><div class="td-actions"><button data-action="use-alignment">Also use this design as active alignment</button>${modelId?'<button data-action="build-copy">Create a separate 3D copy</button>':''}</div><p class="td-note">Using a new active alignment resets active alignment-based survey results; scene scans and independent models stay in the project. Workspace Undo can restore the previous alignment. A rebuilt designer model replaces edits made with Cut or other shape operations; duplicate the model first to keep both versions.</p>`;}
 const tableOpen=$('boundary-table')?.open,toolsOpen=$('profile-tools')?.open,offset=$('profile-offset')?.value;
 $('editor').innerHTML=html;if(tableOpen&&$('boundary-table'))$('boundary-table').open=true;if(toolsOpen&&$('profile-tools'))$('profile-tools').open=true;if(offset&&$('profile-offset'))$('profile-offset').value=offset;updateNumbers();draw();const plane=$('draw-plane');if(plane)plane.onchange=()=>{$('draw-height').disabled=plane.value==='current';};const profilePlaneField=$('profile-plane'),station=$('profile-station');if(profilePlaneField){profilePlaneField.onchange=()=>{profilePlane=profilePlaneField.value;$('profile-station-field').hidden=profilePlane!=='alignment';};station.oninput=()=>{profileStation=station.value;};}
 const choice=$('profile-choice');if(choice)choice.onchange=()=>selectProfile(Number(choice.value));}
 function updateNumbers(){const ch=stations();el.querySelectorAll('[data-chainage]').forEach(e=>e.textContent=ch[Number(e.dataset.chainage)]?.toFixed(3)||'—');el.querySelectorAll('[data-gradient]').forEach(e=>{const i=Number(e.dataset.gradient),a=d.vertical[i],b=d.vertical[i+1];e.textContent=b&&b[0]>a[0]?((b[1]-a[1])/(b[0]-a[0])*100).toFixed(3)+'%':b?'Check order':'End';});}
 function changed(){dirty++;status('Changes pending · save the design or build the model.');updateNumbers();$('summary').textContent='Updating preview…';clearTimeout(timer);timer=setTimeout(preview,550);draw();}
 $('name').oninput=()=>{d.name=$('name').value;changed();};
 $('editor').addEventListener('input',e=>{
 const drawingId=e.target.dataset.drawingId;
 if(drawingId){selectDrawing(drawingId,true,e.target.checked);return;}
 const chainKey=e.target.dataset.chainKey;
 if(chain&&chainKey){
  const row=chain.rows[Number(e.target.dataset.chainRow)];
  row[chainKey]=chainKey==='station'?(e.target.value.trim()?Number(e.target.value):''):e.target.value;
  return;
 }
 if(chain&&e.target.id==='td-chain-method'){chain.method=e.target.value;return;}
 const path=e.target.dataset.field;if(!path)return;const parts=path.split('.'),key=parts.pop();let obj=d;for(const p of parts)obj=obj[p];const oldEnd=stations().at(-1),oldStart=d.start,oldType=path.startsWith('sections.')?sectionType(obj):null;obj[key]=e.target.type==='number'?(e.target.value.trim()?Number(e.target.value):null):e.target.value;
 if(path.startsWith('plan.'))boundsChanged(oldEnd);
 if(path==='start'&&Number.isFinite(d.start)){const delta=d.start-oldStart;d.vertical.forEach(r=>r[0]+=delta);d.sections.forEach(r=>{r.start+=delta;r.end+=delta;});}
 if(path.endsWith('.kind')&&obj.kind==='custom'&&!obj.nodes)obj.nodes=[[-3,-3,0],[3,-3,0],[3,3,0],[-3,3,0]];
 if(oldType){sectionIndex=Number(parts[1]);if(key==='type')obj.type=e.target.value;else obj.type=oldType;if(obj.type==='constant')obj.last=obj.first;}
 changed();if(path.endsWith('.kind')||path.endsWith('.type'))render();});
 function profileOperation(operation,options={}){
  return api('/api/tunnel-design-preview',{operation,detail:d.detail,context,revision,drawing_objects:d.drawing_objects||[],...options});
 }
 async function action(name,index){const p=d.profiles[profileIndex],oldEnd=stations().at(-1);
 if(name==='drawing-use'){await useDrawingProfile(p);return;}
 if(name==='drawing-remove'){
  if(d.profiles.some(profile=>profile.kind==='composite'&&profile.drawing_ids.includes(editDrawingId)))throw Error('This drawing object defines a tunnel profile. Choose a different outline for that profile first.');
  d.drawing_objects=drawingObjects().filter(o=>o.id!==editDrawingId);
  drawingSelection.delete(editDrawingId);editDrawingId=null;render();changed();return;
 }
 if(name.startsWith('chain-')){chainAction(name,index);return;}
 if(name==='profile-move'){await moveProfileNode(p);return;}
 if(['profile-link','profile-offset','profile-mirror'].includes(name)){
  if(d.profiles.length>=32)throw Error('Use up to 32 profiles in one design.');
  if(sketch)throw Error('Finish or cancel the current sketch first.');
  const candidate=name==='profile-link'?selectedOutline():null;
  if(name==='profile-link'&&!candidate)throw Error('Select one closed CAD outline in Model space.');
  const operation=name.slice(8),distance=operation==='offset'&&$('profile-offset').value.trim()?Number($('profile-offset').value):null;
  let result;lock(true);
  try{result=await profileOperation(operation,candidate?{object_id:candidate.id}:{profile:p,distance});}
  finally{lock(false);}
  addProfile(result.profile,candidate?candidate.name:p.name+(operation==='offset'?' offset':' mirrored'));
  render();changed();
  status(result.faceted?'Offset copy created from the sampled outline. Review its curve detail before using it.':
   candidate?'Linked to '+candidate.name+'.':'Profile copy created. Review the outline before building.');
  return;
 }
 if(name==='alignment-radius'){
  const radius=activeRadius();
  if(radius)Object.assign(p,{kind:'circle',radius,x:0,y:0});
 }
 if(name==='draw-alignment'||name==='draw-profile'){await beginDrawing(name==='draw-profile');return;}
 if(name==='pick-range'||name==='pick-preview'){await beginRange(index,name==='pick-preview');return;}

 if(name==='plan-add'){const a=d.plan.at(-1),b=d.plan.at(-2);d.plan.push([a[0]+a[0]-b[0],a[1]+a[1]-b[1],0]);boundsChanged(oldEnd);}
 if(name==='plan-remove'&&d.plan.length>2){d.plan.splice(index,1);boundsChanged(oldEnd);}
 if(name==='active-axis'){const a=axis.rows;d.start=a[0][0];d.plan=a.map(r=>[r[1],r[2],0]);const ch=stations();d.vertical=a.map((r,i)=>[ch[i],r[3]]);d.sections=[{start:ch[0],end:ch.at(-1),first:p.id,last:p.id}];status('Copied active coordinates. Designer chainages use horizontal distance.');}
 if(name==='height-add'){d.vertical.sort((a,b)=>a[0]-b[0]);let i=0;for(let j=1;j<d.vertical.length-1;j++)if(d.vertical[j+1][0]-d.vertical[j][0]>d.vertical[i+1][0]-d.vertical[i][0])i=j;const a=d.vertical[i],b=d.vertical[i+1];d.vertical.splice(i+1,0,[(a[0]+b[0])/2,(a[1]+b[1])/2]);}
 if(name==='height-remove'&&d.vertical.length>2)d.vertical.splice(index,1);
 if(name==='height-sort')d.vertical.sort((a,b)=>a[0]-b[0]);
 if(name==='height-ends'){d.vertical.sort((a,b)=>a[0]-b[0]);for(const ch of [d.start,stations().at(-1)])if(!d.vertical.some(r=>Math.abs(r[0]-ch)<1e-7))d.vertical.push([ch,interpolate(ch)]);d.vertical.sort((a,b)=>a[0]-b[0]);}
 if(name==='profile-add'||name==='profile-copy'){
  const copy=name==='profile-copy'?JSON.parse(JSON.stringify(p)):{kind:'circle',radius:3,width:6,height:6,x:0,y:0};
  addProfile(copy,name==='profile-copy'?p.name+' copy':'Section '+(d.profiles.length+1));
 }
 if(name==='profile-remove'){if(d.profiles.length===1)throw Error('Keep at least one profile.');if(d.sections.some(r=>r.first===p.id||r.last===p.id))throw Error('This profile is used in Sections. Choose another profile there before removing it.');if(previousSections?.some(r=>r.first===p.id||r.last===p.id))previousSections=null;d.profiles.splice(profileIndex,1);profileIndex=0;}
 if(name==='node-add')p.nodes.push([p.nodes.at(-1)[0]+.5,p.nodes.at(-1)[1]+.5,0]);
 if(name==='node-remove'&&p.nodes.length>3)p.nodes.splice(index,1);
 if(name==='section-remove'&&d.sections.length>1)d.sections.splice(index,1);
 if(name==='section-add'){const last=d.sections.at(-1);if(last.end>=oldEnd-1e-7)throw Error('The last section reaches the alignment end. Use Split last section to add a transition within this range.');d.sections.push({start:last.end,end:oldEnd,first:last.last,last:last.last});}
 if(name==='section-split'){const last=d.sections.at(-1),end=last.end;if(sectionType(last)==='interpolate'&&last.first!==last.last)throw Error('Split a constant section first, then choose the transition profiles for each new section.');last.end=(last.start+end)/2;d.sections.push({start:last.end,end,first:last.last,last:last.last});}
 if(name==='section-full')d.sections=[{start:d.start,end:oldEnd,first:p.id,last:p.id}];
 if(name==='cad-refresh'){await window.CadUI.load(true);render();return;}
 if(name==='use-alignment'){await commit('use_alignment');return;}
 if(name==='build-copy'){await commit('build',true);return;}
 render();changed();}
 $('editor').addEventListener('click',e=>{const b=e.target.closest('[data-action]');if(b&&!pending)action(b.dataset.action,Number(b.dataset.index)).catch(e=>status(e.message,true));});
 function goStep(next){wirePick=null;sketch=null;plotHover=null;step=next;render();}
 el.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>goStep(Number(b.dataset.step)));
 $('back').onclick=()=>goStep(Math.max(0,step-1));$('next').onclick=()=>goStep(Math.min(4,step+1));
 $('range-shortcut').onclick=()=>goStep(3);
 $('profile-error').onclick=()=>{
  const index=d.profiles.findIndex(p=>p.id===info?.profile_error_id);
  if(index<0)return;
  profileIndex=index;el.dataset.expanded='false';goStep(2);
  $('editor').querySelector('[data-field="profiles.'+index+'.radius"]')?.focus();
 };
 async function preview(){
  if(!el.open||!d)return;
  if(previewRunning){previewAgain=true;return;}
  previewRunning=true;
  const generation=dirty;
  try{
   const result=await api('/api/tunnel-design-preview',{design:d,model_id:modelId});
   if(generation!==dirty)return;
   info=result;
   const spacing=result.maximum_spacing>result.requested_spacing+1e-7
    ?' · requested '+result.requested_spacing.toFixed(3)+' m · spacing adjusted to fit capacity':'';
   $('summary').textContent=result.model_error?'Alignment ready · '+result.model_error:
    result.profile_count.toLocaleString()+' cross-sections · '+result.maximum_spacing.toFixed(3)+' m maximum spacing'+spacing;
   draw();
  }catch(e){if(generation===dirty){info=null;$('summary').textContent='Preview needs correction';status(e.message,true);draw();}}
  finally{previewRunning=false;if(previewAgain){previewAgain=false;preview();}}
 }
 function lock(value){pending=value;el.querySelectorAll('button,input,select').forEach(x=>{if(value){x.dataset.wasDisabled=String(x.disabled);x.disabled=true;}else{x.disabled=x.dataset.wasDisabled==='true';delete x.dataset.wasDisabled;}});}
 async function commit(action,copy=false){if(pending)return;if(sketch){status('Finish or cancel the profile sketch first.',true);return;}lock(true);clearTimeout(timer);status(action==='build'?'Building and validating the 3D solid…':'Saving design…');try{const response=await api('/api/tunnel-design-action',{action,design:d,model_id:modelId,separate_copy:copy,context,revision});modelId=response.model_id;const saved=await api('/api/tunnel-design');context=saved.context;revision=saved.revision;d=saved.design;axis=saved.active_alignment;previousSections=null;chain=null;await window.CadUI.load(true);await window.JobUI.sync({inventoryOnly:true});status(action==='build'?(copy?'Separate 3D copy built and saved.':'3D model built and saved.'):action==='use_alignment'?'Design saved and active alignment updated.':'Design draft saved with the project.');if(action==='build'){el.close();window.CadUI.enter('Model');window.CadUI.select(modelId);window.CadUI.tool('fit');}else render();return response;}catch(e){status(e.message,true);throw e;}finally{lock(false);}}
 $('save').onclick=()=>commit('save').catch(()=>{});$('build').onclick=()=>commit('build').catch(()=>{});$('close').onclick=()=>{if(!pending){wirePick=null;clearTimeout(timer);el.close();}};el.addEventListener('cancel',e=>{if(pending)e.preventDefault();else clearTimeout(timer);});
 function activeRadius(){
  const r=axis?.rows?.[0]?.[4];
  return r>0&&axis.rows.every(row=>Math.abs(row[4]-r)<1e-6)?r:null;
 }
 function plotKey(){return step===2?'profile:'+d.profiles[profileIndex].id:step===1?'elevation':'plan';}
 function arcBetween(a,b,radius){
  const dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy),r=Math.abs(radius);
  if(!r||length<1e-8||2*r<length-1e-8)return null;
  const side=Math.sign(radius),offset=side*Math.sqrt(Math.max(0,r*r-length*length/4));
  const x=(a[0]+b[0])/2-dy*offset/length,y=(a[1]+b[1])/2+dx*offset/length;
  const start=Math.atan2(a[1]-y,a[0]-x),sweep=side*2*Math.asin(Math.min(1,length/(2*r)));
  return {sweep,centre:[x,y],start,at:t=>[x+r*Math.cos(start+t*sweep),y+r*Math.sin(start+t*sweep)]};
 }
 function outlinePoints(p){
  const x=p.x||0,y=p.y||0;
  if(['cad','composite'].includes(p.kind))return info?.profiles?.find(row=>row.id===p.id)?.points||[];
  if(p.kind==='circle'){
   if(!(p.radius>0))return [];
   return Array.from({length:180},(_,i)=>[x+p.radius*Math.cos(i*Math.PI/90),y+p.radius*Math.sin(i*Math.PI/90)]);
  }
  if(['custom','polyline'].includes(p.kind)){
   const nodes=p.nodes||[];
   if(nodes.some(node=>!node.every(Number.isFinite)))return [];
   return nodes.flatMap((a,i)=>{
    if(p.kind==='polyline'&&i===nodes.length-1)return [[a[0]+x,a[1]+y]];
    const arc=arcBetween(a,nodes[(i+1)%nodes.length],a[2]);
    const n=arc?Math.max(1,Math.ceil(Math.abs(arc.sweep)*90/Math.PI)):1;
    return Array.from({length:n},(_,j)=>{const q=j?arc.at(j/n):a;return [q[0]+x,q[1]+y];});
   });
  }
  const w=p.width,h=p.height;
  if(!(w>0&&h>0)||p.kind==='arch'&&h<w/2)return [];
  const corners=[[x-w/2,y-h/2],[x+w/2,y-h/2]];
  if(p.kind==='rectangle')return [...corners,[x+w/2,y+h/2],[x-w/2,y+h/2]];
  const crown=Array.from({length:91},(_,i)=>[x+w/2*Math.cos(i*Math.PI/90),y+h/2-w/2+w/2*Math.sin(i*Math.PI/90)]);
  if(Math.abs(h-w/2)<1e-9){crown.pop();crown.shift();}
  return [...corners,...crown];
 }
 function shapeFromPoints(kind,a,b){
  if(kind==='circle')return {kind,x:a[0],y:a[1],radius:precise(Math.hypot(b[0]-a[0],b[1]-a[1]))};
  return {kind,x:precise((a[0]+b[0])/2),y:precise((a[1]+b[1])/2),
   width:precise(Math.abs(a[0]-b[0])),height:precise(Math.abs(a[1]-b[1]))};
 }
 const precise=value=>Number(value.toFixed(6));
 function profileGrips(p){
  const x=p.x||0,y=p.y||0;
  if(p.kind==='circle'&&p.radius>0)return [{kind:'centre',point:[x,y]},{kind:'radius',point:[x+p.radius,y]}];
  if(['rectangle','arch'].includes(p.kind)&&p.width>0&&p.height>0)return [{kind:'centre',point:[x,y]},
   {kind:'size',point:[x+p.width/2,y-p.height/2]}];
  if(!['custom','polyline'].includes(p.kind)||p.nodes?.some(node=>!node.every(Number.isFinite)))return [];
  return (p.nodes||[]).flatMap((a,i,nodes)=>{
   const node={kind:'node',index:i,point:[a[0]+x,a[1]+y]};
   if(p.kind==='polyline'&&i===nodes.length-1)return [node];
   const b=nodes[(i+1)%nodes.length],arc=arcBetween(a,b,a[2]),m=arc?arc.at(.5):[(a[0]+b[0])/2,(a[1]+b[1])/2];
   return [node,{kind:'edge',index:i,point:[m[0]+x,m[1]+y]}];
  });
 }
 function plotData(){
  if(step===1)return {points:d.vertical.filter(row=>row.every(Number.isFinite)),title:'Elevation · vertical scale ×10',exaggeration:10};
  if(step===2){
   const outline=outlinePoints(d.profiles[profileIndex]);
   return {points:outline.length?[...outline,outline[0]]:[],title:'Cross-section · X right, Y up',exaggeration:1};
  }
  return {points:(info?.plan||d.plan).map(p=>p.slice(0,2)),title:'Horizontal alignment · plan',exaggeration:1};
 }
 function chart(){
  const canvas=$('plot'),key=plotKey(),profile=step===2?d.profiles[profileIndex]:null;
  const editing=!!profile&&sketch?.id===profile.id;
  const active=profile&&(activeDrawing()||profile);
  const objects=profile?drawingObjects().map(o=>({object:o,points:outlinePoints(o)})):[];
  const otherProfiles=profile&&$('show-profiles').checked?d.profiles.flatMap((object,index)=>
   object===profile?[]:[{object,index,points:outlinePoints(object)}]):[];
  $('wire').hidden=$('wire-help').hidden=!!profile;
  $('profile-draw').hidden=!profile;
  $('coordinate-entry').hidden=!editing;
  $('coordinate-label').textContent=editing&&sketch.kind==='circle'&&sketch.points.length?'X,Y or radius · m':'Point X,Y · m';
  $('profile-error').hidden=!info?.profile_error_id||profile?.id===info.profile_error_id;
  const expanded=el.dataset.expanded==='true';$('expand').textContent=expanded?'Show settings':'Expand';
  $('expand').setAttribute('aria-pressed',String(expanded));
  for(const button of el.querySelectorAll('[data-profile-draw]'))button.setAttribute('aria-pressed',String(editing&&button.dataset.profileDraw===sketch.kind));
  for(const id of ['sketch-undo','sketch-cancel'])$(id).hidden=!editing;
  $('sketch-open').hidden=$('sketch-finish').hidden=!editing||sketch.kind!=='custom';
  const width=canvas.clientWidth,height=canvas.clientHeight;
  if(width>=120&&height>=120){if(canvas.width!==width)canvas.width=width;if(canvas.height!==height)canvas.height=height;}
  const c=canvas.getContext('2d'),w=canvas.width,h=canvas.height;
  const {points,title,exaggeration}=plotData();
  canvas.dataset.sketch=String(editing);
  $('sketch-undo').disabled=!sketch?.points.length;
  $('sketch-finish').disabled=(sketch?.points.length||0)<3;
  $('sketch-open').disabled=(sketch?.points.length||0)<2;
  $('range-shortcut').hidden=!info?.range_limited||step===3;
  c.clearRect(0,0,w,h);
  let view=plotViews.get(key);
  if(!view||view.width!==w||view.height!==h){
   const extent=[...points,...objects.flatMap(o=>o.points),...otherProfiles.flatMap(o=>o.points)];
   const lo=[Infinity,Infinity],hi=[-Infinity,-Infinity];
   for(const point of extent.length?extent:[[-4,-4],[4,4]])for(let i=0;i<2;i++){lo[i]=Math.min(lo[i],point[i]);hi[i]=Math.max(hi[i],point[i]);}
   const scale=Math.min((w-90)/Math.max(.1,hi[0]-lo[0]),(h-85)/Math.max(.1,(hi[1]-lo[1])*exaggeration));
   view={x:view?.x??(lo[0]+hi[0])/2,y:view?.y??(lo[1]+hi[1])/2,
    scale:scale*(view?view.scale/view.fit:1),fit:scale,width:w,height:h};
   if(extent.length||editing)plotViews.set(key,view);
  }
  const map=([x,y])=>[w/2+(x-view.x)*view.scale,h/2-(y-view.y)*view.scale*exaggeration];
  const world=([x,y])=>[view.x+(x-w/2)/view.scale,view.y-(y-h/2)/view.scale/exaggeration];
  const left=30,right=w-20,top=36,bottom=h-30;
  const grips=active&&!editing?profileGrips(active):[];
  plotFrame={view,world,map,grips,exaggeration,objects,otherProfiles};
  const [minX,maxY]=world([left,top]),[maxX,minY]=world([right,bottom]);
  function gridStep(span){const power=10**Math.floor(Math.log10(span));return [1,2,5,10].map(x=>x*power).find(x=>x>=span);}
  const dx=gridStep((maxX-minX)/8),dy=gridStep((maxY-minY)/5);
  c.save();c.beginPath();c.rect(left,top,right-left,bottom-top);c.clip();
  c.font='11px sans-serif';
  for(let i=Math.ceil(minX/dx);i<=Math.floor(maxX/dx);i++){
   const x=i*dx,[px]=map([x,0]);c.strokeStyle=i===0&&step===2?'#577586':'#263b4c';
   c.beginPath();c.moveTo(px,top);c.lineTo(px,bottom);c.stroke();
   c.fillStyle='#a7bece';c.fillText(Number(x.toPrecision(8)).toString(),px+4,bottom-4);
  }
  for(let i=Math.ceil(minY/dy);i<=Math.floor(maxY/dy);i++){
   const y=i*dy,[,py]=map([0,y]);c.strokeStyle=i===0&&step===2?'#577586':'#263b4c';
   c.beginPath();c.moveTo(left,py);c.lineTo(right,py);c.stroke();
   c.fillStyle='#a7bece';c.fillText(Number(y.toPrecision(8)).toString(),left+4,py-4);
  }
  function stroke(rows,colour,dashed=false){
   if(!rows.length)return;
   c.strokeStyle=colour;c.lineWidth=2;c.setLineDash(dashed?[5,4]:[]);c.beginPath();
   rows.forEach((p,i)=>{const q=map(p);i?c.lineTo(...q):c.moveTo(...q);});
   c.stroke();c.setLineDash([]);
  }
  for(const {object,points:shape} of otherProfiles){
   if(!shape.length)continue;
   stroke([...shape,shape[0]],'#7eaac0',true);
   const top=shape.reduce((a,b)=>b[1]>a[1]?b:a),[x,y]=map(top);
   c.fillStyle='#bbd3e5';c.fillText(object.name,x+6,y-7);
  }
  stroke(points,drawingSelection.has('profile')?'#ffd984':'#76d6d5');
  for(const {object,points:shape} of objects){
   const closed=object.kind!=='polyline'&&shape.length;
   stroke(closed?[...shape,shape[0]]:shape,drawingSelection.has(object.id)?'#ffd984':'#a3a0c7',profile.drawing_ids?.includes(object.id));
  }
  if(profile){
   const [x,y]=map([0,0]);c.strokeStyle='#b4cbd5';c.beginPath();c.arc(x,y,4,0,2*Math.PI);c.stroke();
   c.fillStyle='#b4cbd5';c.fillText('0, 0',x+9,y+14);
   for(const grip of grips){
    const [gx,gy]=map(grip.point),active=plotDrag?.grip?.kind===grip.kind&&plotDrag.grip.index===grip.index;
    c.fillStyle=active?'#ffd984':'#123041';c.strokeStyle=grip.kind==='edge'?'#e2bd79':'#a4e9e5';c.lineWidth=1.5;
    c.beginPath();if(grip.kind==='edge'){c.moveTo(gx,gy-5);c.lineTo(gx+5,gy);c.lineTo(gx,gy+5);c.lineTo(gx-5,gy);c.closePath();}
    else if(grip.kind==='node'||grip.kind==='radius')c.arc(gx,gy,5,0,2*Math.PI);
    else c.rect(gx-5,gy-5,10,10);
    c.fill();c.stroke();
    if(grip.kind==='radius'||grip.kind==='size'){c.fillStyle='#dbeef0';c.fillText(grip.kind==='radius'?'R':'Size',gx+9,gy-8);}
   }
  }
  if(editing){
   const vertices=sketch.points;
   if(['custom','line'].includes(sketch.kind)){
    stroke(vertices,'#ffcf78');
    if(vertices.length&&plotHover)stroke([vertices.at(-1),plotHover],'#ffcf78',true);
    if(sketch.kind==='custom'&&vertices.length>2)stroke([vertices.at(-1),vertices[0]],'#b09162',true);
   }else if(vertices.length&&plotHover){
    const candidate=outlinePoints(shapeFromPoints(sketch.kind,vertices[0],plotHover));
    if(candidate.length)stroke([...candidate,candidate[0]],'#ffcf78');
   }
   c.fillStyle='#ffcf78';
   for(const p of vertices){c.beginPath();c.arc(...map(p),3.5,0,Math.PI*2);c.fill();}
  }
  if(profile&&plotSnap){
   const [sx,sy]=map(plotSnap.point);c.strokeStyle='#a5f5cd';c.lineWidth=1.5;c.strokeRect(sx-5,sy-5,10,10);
   c.fillStyle='#b9ffe0';c.fillText(plotSnap.label,sx+9,sy-9);
  }
  c.restore();c.fillStyle='#bbd3e5';c.font='14px sans-serif';c.fillText(title,18,22);
  const dimensions=step===1?'Chainage / height (m)':step===2?'X / Y (m)':'E / N (m)';
  c.fillStyle='#9bb1c2';c.font='12px sans-serif';c.fillText(dimensions,18,h-10);
  const coord=plotHover?.map(v=>v.toFixed(3)).join(' / ');
  let hint='Wheel to zoom · drag the background to pan';
  if(editing)hint=sketch.kind==='custom'?'Place nodes · Enter finishes the polyline':
   sketch.kind==='line'?(sketch.points.length?'Place the line end':'Place the line start'):
   sketch.kind==='circle'?(sketch.points.length?'Click a point on the circle':'Click the centre'):
   (sketch.points.length?'Click the opposite corner':'Click the first corner');
  else if(['custom','polyline'].includes(active?.kind))hint='Drag a node or side · Alt+drag a side to change its arc';
  else if(active?.kind==='circle')hint='Drag the centre or radius handle';
  else if(['rectangle','arch'].includes(active?.kind))hint='Drag the centre or size handle';
  $('plot-readout').textContent=(coord?dimensions+' '+coord+' · ':'')+hint;
 }
 function draw(){if(!d||!el.open)return;chart();
  if($('wire').hidden)return;
 const c=$('wire').getContext('2d');if(!c)return;const w=$('wire').width,h=$('wire').height;c.clearRect(0,0,w,h);c.fillStyle='#bbd3e5';c.font='14px sans-serif';c.fillText('3D model preview',18,24);const rings=info?.rings||[],plan=info?.plan||[];wireMap=null;if(!rings.length&&!plan.length){c.fillText('Complete the alignment to see its 3D preview.',18,60);return;}
 const all=[...rings.flat(),...plan],min=[0,1,2].map(i=>Math.min(...all.map(p=>p[i]))),max=[0,1,2].map(i=>Math.max(...all.map(p=>p[i]))),centre=min.map((v,i)=>(v+max[i])/2);const project=p=>{const [x,y,z]=p.map((v,i)=>v-centre[i]);return [Math.cos(yaw)*x-Math.sin(yaw)*y,Math.sin(pitch)*(Math.sin(yaw)*x+Math.cos(yaw)*y)-Math.cos(pitch)*z];};const pp=all.map(project),extent=[0,1].map(i=>Math.max(...pp.map(p=>p[i]))-Math.min(...pp.map(p=>p[i]))),scale=Math.min((w-90)/Math.max(.1,extent[0]),(h-90)/Math.max(.1,extent[1]))*zoom,map=p=>{const q=project(p);return [w/2+q[0]*scale,h/2+q[1]*scale];};
 wireMap=map;
 c.strokeStyle='#79c9d1';c.lineWidth=1;c.globalAlpha=.65;for(const ring of rings){c.beginPath();ring.forEach((p,i)=>{const q=map(p);i?c.lineTo(...q):c.moveTo(...q);});c.closePath();c.stroke();}c.globalAlpha=.45;for(let k=0;k<(rings[0]?.length||0);k+=Math.max(1,Math.floor(rings[0].length/12))){c.beginPath();rings.forEach((r,i)=>{const q=map(r[k]);i?c.lineTo(...q):c.moveTo(...q);});c.stroke();}c.globalAlpha=1;drawCentreline(c,map,plan,info?.plan_chainages||[]);}
 const plot=$('plot');
 if(window.ResizeObserver)new ResizeObserver(()=>{if(d&&el.open)chart();}).observe(plot);
 function plotPixel(e){
  const bounds=plot.getBoundingClientRect();
  return [(e.clientX-bounds.left-plot.clientLeft)*plot.width/(plot.clientWidth||bounds.width),
   (e.clientY-bounds.top-plot.clientTop)*plot.height/(plot.clientHeight||bounds.height)];
 }
 function plotPoint(pixel){
  const point=plotFrame.world(pixel);
  plotSnap=null;
  if(step!==2)return point;
  if((sketch||plotDrag?.grip)&&$('object-snap').checked){
   plotSnap=objectSnap(point,plotDrag?.target);
   if(plotSnap)return plotSnap.point;
  }
  if(!$('grid-snap').checked)return point;
  const stepSize=Math.max(.001,Number($('grid-step').value)||.01);
  return point.map(v=>precise(Math.round(v/stepSize)*stepSize));
 }
 function objectSnap(point,excluded){
  const limit=10/plotFrame.view.scale;
  let anchor=null,nearest=null;
  function consider(p,label,edge=false){
   const distance=Math.hypot(p[0]-point[0],p[1]-point[1]);
   const old=edge?nearest:anchor;
   if(distance>limit||old&&old.distance<=distance)return;
   const hit={point:p,label,distance};
   if(edge)nearest=hit;else anchor=hit;
  }
  function segment(a,b,radius=0,anchors=true){
   const arc=arcBetween(a,b,radius);
   if(anchors){consider(a,'Endpoint');consider(b,'Endpoint');consider(arc?arc.at(.5):[(a[0]+b[0])/2,(a[1]+b[1])/2],'Midpoint');}
   if(arc){
    const angle=Math.atan2(point[1]-arc.centre[1],point[0]-arc.centre[0]);
    const turns=((angle-arc.start)*Math.sign(arc.sweep)+4*Math.PI)%(2*Math.PI);
    if(turns<=Math.abs(arc.sweep))consider(arc.at(turns/Math.abs(arc.sweep)),'Nearest',true);
   }else consider(window.CadMath.segmentClosest(point,a,b),'Nearest',true);
  }
  if(sketch?.kind==='custom'&&sketch.points.length>=3)consider(sketch.points[0],'Close outline');
  const profile=d.profiles[profileIndex];
  for(const object of [profile,...drawingObjects(),...plotFrame.otherProfiles.map(row=>row.object)]){
   if(object===excluded)continue;
   const x=object.x||0,y=object.y||0;
   if(object.kind==='circle'&&object.radius>0){
    const r=object.radius,dx=point[0]-x,dy=point[1]-y,length=Math.hypot(dx,dy);
    consider([x,y],'Centre');
    for(const p of [[x+r,y],[x-r,y],[x,y+r],[x,y-r]])consider(p,'Quadrant');
    if(length)consider([x+r*dx/length,y+r*dy/length],'Nearest',true);
   }else if(['custom','polyline'].includes(object.kind)){
    const nodes=object.nodes||[],count=nodes.length-(object.kind==='polyline'?1:0);
    for(let i=0;i<count;i++){
     const a=nodes[i],b=nodes[(i+1)%nodes.length];
     if(a.every(Number.isFinite)&&b.every(Number.isFinite))segment([a[0]+x,a[1]+y],[b[0]+x,b[1]+y],a[2]);
    }
   }else{
    const rows=object===profile?outlinePoints(profile):[...plotFrame.objects,...plotFrame.otherProfiles].find(row=>row.object===object)?.points||[];
    if(object.kind==='rectangle'||object.kind==='arch')consider([x,y],'Centre');
    for(let i=0;i<rows.length;i++)segment(rows[i],rows[(i+1)%rows.length],0,object.kind==='rectangle');
   }
  }
  return anchor||nearest;
 }
 function hitDrawing(pixel){
  const profile=d.profiles[profileIndex],rows=[...plotFrame.objects,{object:profile,points:outlinePoints(profile)},...plotFrame.otherProfiles];
  let best=null,distance=10;
  for(const row of rows){
   const {object,points}=row;
   const end=points.length-(object.kind==='polyline'?1:0);
   for(let i=0;i<end;i++){
    const error=window.CadMath.segmentPixel(pixel,plotFrame.map(points[i]),plotFrame.map(points[(i+1)%points.length])).d;
    if(error<distance){best=row;distance=error;}
   }
  }
  return best;
 }
 function hitGrip(pixel){
  let best=null,distance=11;
  for(const grip of plotFrame.grips){
   const [x,y]=plotFrame.map(grip.point),d=Math.hypot(x-pixel[0],y-pixel[1]);
   if(d<distance){best=grip;distance=d;}
  }
  return best;
 }
 function moveGrip(gesture,point){
  const {before,grip,target:p}=gesture,x=before.x||0,y=before.y||0;
  const [u,v]=point;
  if(grip.kind==='centre'){p.x=precise(u);p.y=precise(v);}
  if(grip.kind==='radius')p.radius=precise(Math.max(.001,Math.hypot(u-x,v-y)));
  if(grip.kind==='size'){
   p.width=precise(Math.max(.001,2*Math.abs(u-x)));
   p.height=precise(Math.max(p.kind==='arch'?p.width/2:.001,2*Math.abs(v-y)));
  }
  if(grip.kind==='node')p.nodes[grip.index]=[precise(u-x),precise(v-y),before.nodes[grip.index][2]];
  if(grip.kind==='edge'){
   const i=grip.index,j=(i+1)%before.nodes.length,a=before.nodes[i],b=before.nodes[j];
   if(gesture.curve){
    const dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);
    if(length<1e-8)return;
    const sag=((-dy)*(u-x-(a[0]+b[0])/2)+dx*(v-y-(a[1]+b[1])/2))/length;
    const depth=Math.min(Math.abs(sag),length/2);
    p.nodes[i][2]=depth<.001?0:-Math.sign(sag)*Math.max(length/2,precise(length*length/(8*depth)+depth/2));
   }else{
    const dx=u-grip.point[0],dy=v-grip.point[1];
    p.nodes[i]=[precise(a[0]+dx),precise(a[1]+dy),a[2]];
    p.nodes[j]=[precise(b[0]+dx),precise(b[1]+dy),b[2]];
   }
  }
  const index=drawingObjects().indexOf(p),base=index>=0?'drawing_objects.'+index+'.':'profiles.'+profileIndex+'.';
  for(const field of $('editor').querySelectorAll('input[type=number]')){
   if(!field.dataset.field?.startsWith(base))continue;
   const value=field.dataset.field.slice(base.length).split('.').reduce((obj,key)=>obj?.[key],p);
   if(Number.isFinite(value))field.value=String(value);
  }
 }
 function finishGrip(commit){
  const drag=plotDrag;plotDrag=null;
  if(!drag?.grip||!drag.moved)return;
  if(!commit)Object.assign(drag.target,drag.before);
  plotSnap=null;
  render();changed();
 }
 function applyDrawnShape(shape){
  const objects=drawingObjects(),id=drawingKey();
  const label=({line:'Line',custom:shape.kind==='custom'?'Closed polyline':'Polyline',circle:'Circle',rectangle:'Rectangle',arch:'Arch'})[sketch.kind];
  objects.push({...shape,id,name:label+' '+(objects.length+1)});
  sketch=null;plotHover=plotSnap=null;$('coordinate').value='';plotViews.delete(plotKey());
  selectDrawing(id);changed();
 }
 function placeDrawingPoint(point){
  if(!sketch||step!==2)return;
  const first=sketch.points[0];
  if(first&&sketch.kind!=='custom'){
   if(sketch.kind==='line'){
    if(Math.hypot(point[0]-first[0],point[1]-first[1])<1e-6)throw Error('Place the line end away from its start.');
    applyDrawnShape({kind:'polyline',nodes:[first,point].map(([x,y])=>[x,y,0]),x:0,y:0});return;
   }
   const shape=shapeFromPoints(sketch.kind,first,point);
   if(!outlinePoints(shape).length)throw Error(sketch.kind==='arch'?'Choose an arch at least half as tall as it is wide.':'Place a second point to give the shape a positive size.');
   applyDrawnShape(shape);return;
  }
  if(sketch.points.length>=512)throw Error('Use up to 512 nodes in one polyline.');
  if(sketch.points.some(p=>Math.hypot(p[0]-point[0],p[1]-point[1])<1e-6)){
   if(sketch.kind==='custom'&&sketch.points.length>=3&&Math.hypot(first[0]-point[0],first[1]-point[1])<1e-6){finishPolyline(true);return;}
   throw Error('Place the next node at a different point.');
  }
  sketch.points.push(point);plotSnap=null;chart();
 }
 function finishPolyline(closed){
  if(sketch?.kind!=='custom'||sketch.points.length<(closed?3:2))return;
  const points=sketch.points;
  if(closed){
   const area=points.reduce((sum,[x,y],i)=>{const [a,b]=points[(i+1)%points.length];return sum+x*b-y*a;},0);
   if(Math.abs(area)<1e-8){status('Add corners around an area before closing the outline.',true);return;}
  }
  applyDrawnShape({kind:closed?'custom':'polyline',nodes:points.map(([x,y])=>[x,y,0]),x:0,y:0});
 }
 $('coordinate-entry').onsubmit=event=>{
  event.preventDefault();
  if(!sketch||pending)return;
  const field=$('coordinate'),raw=field.value.trim(),first=sketch.points[0];
  try{
   let point;
   if(sketch.kind==='circle'&&first&&raw&&Number.isFinite(Number(raw))){
    const radius=Number(raw);
    if(radius<=0)throw Error('Enter a positive radius in metres.');
    point=[first[0]+radius,first[1]];
   }else{
    const last=sketch.points.at(-1),value=window.CadMath.parse(raw,window.CadMath.worldFrame(),last?[...last,0]:null);
    if(Math.abs(value[2])>1e-9)throw Error('Enter X,Y coordinates on the cross-section plane.');
    point=value.slice(0,2);
   }
   placeDrawingPoint(point);field.value='';
  }catch(error){status(error.message,true);}
 };
 for(const button of el.querySelectorAll('[data-profile-draw]'))button.onclick=()=>{
  if(drawingObjects().length>=128){status('Use up to 128 drawing objects.',true);return;}
  sketch={kind:button.dataset.profileDraw,id:d.profiles[profileIndex].id,points:[]};
  plotHover=plotSnap=null;$('coordinate').value='';chart();plot.focus();
 };
 $('origin').onclick=()=>{if(plotFrame){plotFrame.view.x=0;plotFrame.view.y=0;chart();}};
 $('expand').onclick=()=>{el.dataset.expanded=String(el.dataset.expanded!=='true');chart();};
 $('show-profiles').onchange=()=>{plotViews.delete(plotKey());plotHover=plotSnap=null;chart();};
 $('plot-fit').onclick=()=>{plotViews.delete(plotKey());plotHover=null;chart();};
 plot.addEventListener('wheel',e=>{
  if(!plotFrame||plotDrag?.grip)return;
  e.preventDefault();
  const pixel=plotPixel(e),before=plotFrame.world(pixel),{view,world}=plotFrame;
  view.scale=Math.max(view.fit*.25,Math.min(view.fit*512,view.scale*Math.exp(-e.deltaY*.0015)));
  const after=world(pixel);
  view.x+=before[0]-after[0];view.y+=before[1]-after[1];
  plotHover=plotPoint(pixel);chart();
 },{passive:false});
 plot.onpointerdown=e=>{
  if(pending||e.button!==0||!plotFrame)return;
  const pixel=plotPixel(e);
  const grip=hitGrip(pixel);
  const target=step===2?(activeDrawing()||d.profiles[profileIndex]):null;
  plotDrag={id:e.pointerId,start:pixel,last:pixel,moved:false,grip,curve:e.altKey,target,
   before:grip?JSON.parse(JSON.stringify(target)):null};
  plot.focus();plot.setPointerCapture?.(e.pointerId);
 };
 plot.onpointermove=e=>{
  if(pending||!plotFrame)return;
  const pixel=plotPixel(e),g=plotDrag;
  plot.dataset.grip=String(!!(g?.grip||hitGrip(pixel)));
  if(g){
   const moved=Math.hypot(pixel[0]-g.start[0],pixel[1]-g.start[1])>(g.grip?2:5);
   if(!g.moved&&moved&&g.grip){dirty++;clearTimeout(timer);}
   g.moved=g.moved||moved;
   if(g.moved&&g.grip)moveGrip(g,plotPoint(pixel));
   else if(g.moved){
    const {view,exaggeration}=plotFrame;
    view.x-=(pixel[0]-g.last[0])/view.scale;
    view.y+=(pixel[1]-g.last[1])/(view.scale*exaggeration);
    g.last=pixel;
   }
  }
  plotHover=plotPoint(pixel);chart();
 };
 plot.onpointerup=e=>{
  const g=plotDrag;
  if(!g||g.id!==e.pointerId)return;
  if(g.grip){finishGrip(true);return;}
  plotDrag=null;
  if(g.moved||step!==2)return;
  const pixel=plotPixel(e);
  if(pixel[0]<30||pixel[0]>plot.width-20||pixel[1]<36||pixel[1]>plot.height-30)return;
  if(!sketch){
   const hit=hitDrawing(pixel);
   if(Number.isInteger(hit?.index))selectProfile(hit.index,true);
   else selectDrawing(hit?.object===d.profiles[profileIndex]?'profile':hit?.object.id,e.ctrlKey||e.metaKey);
   return;
  }
  try{placeDrawingPoint(plotPoint(pixel).map(precise));}catch(error){status(error.message,true);}
 };
 plot.onpointercancel=()=>finishGrip(false);
 plot.onpointerleave=()=>{plotHover=plotSnap=null;plot.dataset.grip='false';if(plotFrame)chart();};
 $('sketch-undo').onclick=()=>{sketch?.points.pop();chart();};
 $('sketch-cancel').onclick=()=>{sketch=null;plotHover=plotSnap=null;draw();};
 $('sketch-open').onclick=()=>finishPolyline(false);
 $('sketch-finish').onclick=()=>finishPolyline(true);
 plot.onkeydown=e=>{
  if(e.key==='Escape'&&plotDrag?.grip){e.preventDefault();e.stopPropagation();finishGrip(false);return;}
  const action={Escape:'sketch-cancel',Backspace:'sketch-undo',Enter:'sketch-open'}[e.key];
  if(sketch&&action){e.preventDefault();e.stopPropagation();$(action).click();}
 };
 let drag=null;$('wire').onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,start:[e.clientX,e.clientY],moved:false};$('wire').setPointerCapture?.(e.pointerId);};$('wire').onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.hypot(e.clientX-drag.start[0],e.clientY-drag.start[1])>4)drag.moved=true;if(drag.moved){yaw+=dx*.01;pitch=Math.max(-1.5,Math.min(1.5,pitch+dy*.01));}drag.x=e.clientX;drag.y=e.clientY;draw();};$('wire').onpointerup=e=>{const g=drag;drag=null;if(!g||g.moved||!wirePick||!wireMap)return;const r=$('wire').getBoundingClientRect(),pixel=[(e.clientX-r.left)*$('wire').width/r.width,(e.clientY-r.top)*$('wire').height/r.height],hit=nearestChainage(pixel,wireMap,info);if(hit)rangeHit(hit,true);else status('Click closer to the centreline. You can drag to turn the preview.');};$('wire').onpointercancel=()=>drag=null;$('wire').addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.2,Math.min(8,zoom*Math.exp(-e.deltaY*.001)));draw();},{passive:false});
 $('new').onclick=()=>{step=0;profileIndex=0;open('new');};
 async function open(id=null){if(pending)return;wirePick=null;pickState=null;drawingSelection.clear();editDrawingId=null;drawbar.hidden=true;try{window.TunnelRibbon?.closeTool?.();await window.CadUI.load(true);const fromAlignment=id==='alignment',drawAlignment=id==='draw',selected=window.CadUI.selectedIds?.()||[];if(!id&&selected.length===1&&window.CadUI.model().objects.find(o=>o.id===selected[0])?.definition?.kind==='tunnel_design')id=selected[0];const value=await api('/api/tunnel-design'+(id==='new'||fromAlignment||drawAlignment?'?new=1':id&&id!=='draft'?'?model='+encodeURIComponent(id):''));plotViews.clear();plotHover=null;sketch=null;chain=null;previousSections=null;d=value.design;modelId=value.model_id;context=value.context;revision=value.revision;axis=value.active_alignment;step=drawAlignment?0:(value.initial_step??0);profileIndex=0;sectionIndex=0;if(value.initialization_warning)status(value.initialization_warning,true);else if(drawAlignment)status('Draw the route in plan view, then set its heights and profiles.');else if(value.initialized_from_alignment)status('Alignment loaded. Draw or choose the tunnel profile, then confirm the section range and build.'+(value.alignment_chainages_recalculated?' Designer chainages were recalculated from horizontal distances; the active alignment is unchanged.':''));else status(modelId?'Edit the saved profiles, alignment, heights or sections, then update this model or create a separate copy.':'Saved designs remain editable. Start with the plan, then add heights and profiles.');info=null;dirty++;if(!el.open)el.showModal();render();preview();}catch(e){notice(e.message,true);status(e.message,true);}}
 const drawbar=document.createElement('div');drawbar.className='td-drawbar';drawbar.id='td-drawbar';drawbar.hidden=true;drawbar.innerHTML='<div><strong id="td-draw-title"></strong><p id="td-draw-help"></p></div><button id="td-draw-finish">Finish</button><button id="td-draw-cancel">Cancel</button>';document.body.append(drawbar);
 function showDrawbar(title,help,finish,cancel){$('draw-title').textContent=title;$('draw-help').textContent=help;$('draw-finish').hidden=!finish;$('draw-finish').onclick=finish;$('draw-cancel').onclick=cancel;drawbar.hidden=false;}
 function resumeDesigner(message){drawbar.hidden=true;pickState=null;wirePick=null;window.JobUI.draw();if(!el.open)el.showModal();render();if(message)status(message);}
 async function drawingFrame(profile){
  const M=window.CadMath,choice=profile?$('profile-plane').value:$('draw-plane').value;
  if(profile&&choice==='alignment'){
   const station=$('profile-station').value;
   if(!station.trim())throw Error('Enter the chainage of the perpendicular section.');
   const {frame}=await profileOperation('drawing_frame',{design:d,chainage:Number(station)});
   return {M,choice,height:frame.origin[2],frame};
  }
  const height=profile?Number(d.vertical[0][1]):Number($('draw-height').value);
  if(choice!=='current'&&(!Number.isFinite(height)||(!profile&&!$('draw-height').value.trim())))throw Error('Enter a drawing height in metres.');
  let frame=choice==='current'?window.CadUI.workPlane():{...M.worldFrame(),origin:[Number(d.plan[0][0]),Number(d.plan[0][1]),height]};
  if(choice==='XZ')frame={...frame,y:[0,0,1],z:[0,-1,0]};
  if(choice==='YZ')frame={...frame,x:[0,1,0],y:[0,0,1],z:[1,0,0]};
  return {M,choice,height,frame};
 }
 async function moveProfileNode(profile){
  if(sketch)throw Error('Finish or cancel the current sketch first.');
  if(window.CadUI.command())throw Error('Finish or cancel the current drawing command first.');
  let frame,vertices,outline;
  const M=window.CadMath,objectId=profile.kind==='cad'?profile.object_id:null;
  lock(true);
  try{
   if(objectId)({frame}=await profileOperation('edit_frame',{object_id:objectId}));
   else{
    ({frame}=await drawingFrame(true));
    const shape=await profileOperation('outline',{profile});
    outline=shape.points.map(([x,y])=>M.toWorld([x,y,0],frame));
    vertices=profile.nodes.map(([x,y])=>M.toWorld([Number(x)+Number(profile.x??0),Number(y)+Number(profile.y??0),0],frame));
   }
  }finally{lock(false);}
  clearTimeout(timer);el.close();
  try{
   const started=window.CadUI.moveProfileNode({frame,vertices,objectId,outline,
    previewOutline:objectId?null:(index,point)=>{
     const [x,y]=M.toLocal(point,frame),nodes=profile.nodes.slice();
     nodes[index]=[x-(profile.x||0),y-(profile.y||0),nodes[index][2]];
     return outlinePoints({...profile,nodes}).map(([u,v])=>M.toWorld([u,v,0],frame));
    },
    onMove:async(index,point)=>{
     const [x,y]=M.toLocal(point,frame),nodes=profile.nodes.map(node=>node.slice());
     nodes[index]=[x-(profile.x||0),y-(profile.y||0),nodes[index][2]];
     await profileOperation('outline',{profile:{...profile,nodes}});
     profile.nodes=nodes;
    },
    onDone:(message,error,{cadChanged,workspaceChanged})=>{
     if(workspaceChanged){drawbar.hidden=true;notice(message,true);return;}
     if(cadChanged){
      const model=window.CadUI.model();
      context=model.context;revision=model.revision;
     }
     info=null;resumeDesigner();changed();status(message,error);
    }});
   if(!started)throw Error('The Model workspace is busy. Finish the current command first.');
   showDrawbar('Edit profile node','Click a marked node, then click its new position. Geometry snaps work on the profile plane.',null,()=>window.CadUI.cancel());
  }catch(e){resumeDesigner();throw e;}
 }
 async function beginDrawing(profile){
  if(window.CadUI.command())throw Error('Finish or cancel the current drawing command first.');
  let placement;
  lock(true);
  try{placement=await drawingFrame(profile);}finally{lock(false);}
  const {M,choice,height,frame}=placement;
  const title=profile?'Draw theoretical profile':'Draw alignment';
  clearTimeout(timer);wirePick=null;el.close();
  const started=window.CadUI.drawPolyline({frame,lockPlane:true,closedRequired:profile,openRequired:!profile,
   prompt:n=>n?`${title} · click the next point or Finish at the end · Backspace undoes a point.`:`${title} · click the first point on the highlighted plane.`,
   onCancel:()=>resumeDesigner('Drawing cancelled. Your previous design is unchanged.'),
   onFinish:points=>{
    if(profile){
     if(points.length>3&&M.norm(M.sub(points[0],points.at(-1)))<1e-7)points.pop();
     if(points.length<3||points.length>512)throw Error('Draw between 3 and 512 profile corners.');
     const local=points.map(p=>M.toLocal(p,frame));
     const area=Math.abs(local.reduce((a,p,i)=>{const q=local[(i+1)%local.length];return a+p[0]*q[1]-p[1]*q[0];},0))/2;
     if(area<1e-8)throw Error('The profile has no area. Add corners around a closed shape.');
     Object.assign(d.profiles[profileIndex],{kind:'custom',nodes:local.map(p=>[p[0],p[1],0]),x:0,y:0});step=2;
    }else{
     if(points.length>10000)throw Error('Use at most 10,000 alignment nodes.');
     const plan=[],vertical=[];let ch=Number(d.start);
     for(let i=0;i<points.length;i++){
      const point=points[i];if(i){const length=Math.hypot(point[0]-points[i-1][0],point[1]-points[i-1][1]);if(length<1e-8)throw Error('An alignment needs horizontal distance between nodes. Use a horizontal or inclined plane, or move the next point along the tunnel.');ch+=length;}
      plan.push([point[0],point[1],0]);vertical.push([ch,point[2]]);
     }
     d.plan=plan;d.vertical=vertical;const id=d.profiles[profileIndex].id;d.sections=[{start:d.start,end:ch,first:id,last:id,type:'constant'}];sectionIndex=0;step=1;
    }
    info=null;resumeDesigner();changed();status(profile?'Profile drawn. Choose where to use it in Sections.':'Alignment drawn. Heights follow your picked points; edit any chainage height here.');
   }});
  if(!started){resumeDesigner();throw Error('The 3D drawing tool is busy. Finish the current operation and try again.');}
  showDrawbar(title,profile?'Click the boundary corners on the plane. Finish joins the last point to the first.':'Click the start, any bends and the end. Finish keeps the route open. Heights and chainages remain editable.',()=>window.CadUI.finishDrawing(),()=>window.CadUI.cancel());
  const profileSize=outlinePoints(d.profiles[profileIndex]).reduce((span,[x,y])=>Math.max(span,Math.abs(x)*2,Math.abs(y)*2),5);
  const size=profile?profileSize*1.3:Math.max(5,Math.min(100,stations().at(-1)-d.start));window.JobUI.faceWorkPlane(frame.z);
  const corners=[[-1,-1],[-1,1],[1,-1],[1,1]].map(([x,y])=>M.toWorld([x*size/2,y*size/2,0],frame));
  window.JobUI.focusWorld(profile||choice==='current'?corners:[...corners,...d.plan.map(p=>[p[0],p[1],height])],false);window.JobUI.draw();
 }
 async function beginRange(index,inPreview){
  if(window.CadUI.command())throw Error('Finish or cancel the current drawing command first.');
  const generation=dirty,result=await api('/api/tunnel-design-preview',{design:d,model_id:modelId});
  if(generation!==dirty||!el.open)return;
  if(!result.plan?.length||result.plan_chainages?.length!==result.plan.length)throw Error('Complete the alignment and its heights before picking a section.');
  info=result;sectionIndex=index;clearTimeout(timer);const state={info:result,index,first:null};
  if(inPreview){wirePick=state;render();status('Click the start on the yellow/blue centreline in the 3D preview, then click the end. Drag to orbit. Escape cancels.');return;}
  if(!window.CadUI.enter('Model'))return;
  pickState=state;wirePick=null;el.close();window.JobUI.setMode('orbit');
  showDrawbar('Pick section start','Click on the highlighted alignment. Drag to rotate, wheel to zoom. Escape cancels.',null,()=>resumeDesigner('Range picking cancelled.'));
  window.JobUI.focusWorld(result.plan);window.JobUI.draw();
 }
 function nearestChainage(pixel,project,data){
  const pts=data?.plan||[],ch=data?.plan_chainages||[];let best=null,distance=18;
  for(let i=1;i<pts.length;i++){
   const a=project(pts[i-1]),b=project(pts[i]);if(!a||!b||Math.hypot(b[0]-a[0],b[1]-a[1])<1e-6)continue;
   const hit=window.CadMath.segmentPixel(pixel,a,b);if(hit.d>=distance)continue;distance=hit.d;
   best={chainage:ch[i-1]+hit.t*(ch[i]-ch[i-1]),point:pts[i-1].map((v,j)=>v+hit.t*(pts[i][j]-v))};
  }return best;
 }
 function rangeHit(hit,inPreview){
  const state=inPreview?wirePick:pickState;if(!state)return;
  if(!state.first){state.first=hit;const message=`Start ${hit.chainage.toFixed(3)} m picked. Click the end on the centreline.`;if(inPreview){status(message);draw();}else{$('draw-title').textContent='Pick section end';$('draw-help').textContent=message;window.JobUI.draw();}return;}
  let start=Math.min(state.first.chainage,hit.chainage),end=Math.max(state.first.chainage,hit.chainage);
  if(end-start<1e-6){const message='The end must be at a different chainage. Pick another point.';inPreview?status(message,true):$('draw-help').textContent=message;return;}
  start=Number(start.toFixed(6));end=Number(end.toFixed(6));const i=state.index,r=d.sections[i],prev=d.sections[i-1],next=d.sections[i+1];
  const movePrev=prev&&Math.abs(prev.end-r.start)<1e-6,moveNext=next&&Math.abs(next.start-r.end)<1e-6;
  if((movePrev&&start<=prev.start+1e-6)||(moveNext&&end>=next.end-1e-6)){const message='That range would remove a neighbouring section. Pick an end inside the adjacent section boundaries.';inPreview?status(message,true):$('draw-help').textContent=message;state.first=null;return;}
  if(movePrev)prev.end=start;if(moveNext)next.start=end;r.start=start;r.end=end;
  step=3;resumeDesigner();changed();status(`Section ${i+1}: ${start.toFixed(3)}–${end.toFixed(3)} m. Touching section boundaries follow this change.`);
 }
 function rangePoints(plan,ch,range){
  const out=[];if(!range||plan.length!==ch.length)return out;
  for(let i=1;i<plan.length;i++){
   if(ch[i]<range.start||ch[i-1]>range.end)continue;const span=ch[i]-ch[i-1];if(span<=0)continue;
   for(const value of [Math.max(ch[i-1],range.start),Math.min(ch[i],range.end)]){const t=(value-ch[i-1])/span;out.push(plan[i-1].map((v,j)=>v+t*(plan[i][j]-v)));}
  }return out;
 }
 function drawCentreline(c,map,plan,ch){
  const stroke=(pts,col,width)=>{if(!pts.length)return;c.strokeStyle=col;c.lineWidth=width;c.beginPath();pts.forEach((p,i)=>{const q=map(p);i?c.lineTo(...q):c.moveTo(...q);});c.stroke();};
  stroke(plan,'#5cbafa',2);stroke(rangePoints(plan,ch,d.sections[sectionIndex]),'#ffda85',3);
  for(const i of [0,plan.length-1])if(plan[i]){const p=map(plan[i]);c.fillStyle='#d9efff';c.font='12px sans-serif';c.fillText(`${i?'End':'Start'} ${ch[i]?.toFixed(3)||''}`,p[0]+7,p[1]-8);}
  if(wirePick?.first){const p=map(wirePick.first.point);c.fillStyle='#ffda85';c.beginPath();c.arc(...p,6,0,Math.PI*2);c.fill();}
 }
 function alignmentOverlay(project){
  if(!pickState)return '';const {plan,plan_chainages:ch}=pickState.info,poly=(points,colour,width)=>{const pixels=points.map(project).filter(Boolean);return `<polyline points="${pixels.map(p=>p.join(',')).join(' ')}" fill="none" stroke="${colour}" stroke-width="${width}"/>`;};
  let svg=poly(plan,'#55c5ff',3)+poly(rangePoints(plan,ch,d.sections[pickState.index]),'#ffda85',4);
  if(pickState.first){const p=project(pickState.first.point);if(p)svg+=`<circle cx="${p[0]}" cy="${p[1]}" r="7" fill="#ffda85" stroke="#142b39" stroke-width="2"/><text x="${p[0]+12}" y="${p[1]-10}" fill="#ffda85" stroke="#142b39" paint-order="stroke" stroke-width="3">Start ${pickState.first.chainage.toFixed(3)} m</text>`;}
  return `<g data-designer-alignment="true">${svg}</g>`;
 }
 const mainCanvas=document.getElementById('job-canvas');let mainGesture=null;
 mainCanvas.addEventListener('pointerdown',e=>{mainGesture=pickState&&e.button===0&&!e.shiftKey&&!e.altKey?{id:e.pointerId,x:e.clientX,y:e.clientY,moved:false}:null;},true);
 mainCanvas.addEventListener('pointermove',e=>{if(mainGesture&&Math.hypot(e.clientX-mainGesture.x,e.clientY-mainGesture.y)>4)mainGesture.moved=true;},true);
 mainCanvas.addEventListener('pointerup',e=>{const g=mainGesture;mainGesture=null;if(!pickState||!g||g.id!==e.pointerId||g.moved||e.button!==0||e.shiftKey||e.altKey)return;e.stopImmediatePropagation();window.JobUI.cadScene().cancelNavigation();const r=mainCanvas.getBoundingClientRect(),hit=nearestChainage([e.clientX-r.left,e.clientY-r.top],window.JobUI.projectWorld,pickState.info);if(hit)rangeHit(hit,false);else $('draw-help').textContent='Click closer to the highlighted centreline. Drag to rotate if it is hidden.';},true);
 for(const type of ['pointercancel','lostpointercapture'])mainCanvas.addEventListener(type,()=>mainGesture=null,true);
 mainCanvas.addEventListener('wheel',()=>{if(mainGesture)mainGesture.moved=true;},{passive:true,capture:true});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&(pickState||wirePick)){e.preventDefault();e.stopImmediatePropagation();resumeDesigner('Range picking cancelled.');}},true);
 window.TunnelDesigner={open,isPicking:()=>!!pickState,overlay:alignmentOverlay};
})();
