/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const M=window.CadMath,J=()=>window.JobUI,$c=id=>document.getElementById(id),canvas=$c('job-canvas');
 let model={objects:[],layers:[],frames:[],active_frame:M.worldFrame(),revision:'empty'},selected=new Set(),mode='Edit',command=null,pending=false,loading=null;
 let preview=null,dirty=true,previewDirty=true,renderOrigin=[0,0,0],edgeCache=new Map(),snapCache=null,snapHit=null,cursor=null,gesture=null,handleDrag=null,previewSequence=0,previewTimer=null,history=[],historyPosition=-1,lastCommand='';
 let extrusion=null,lastPickedFace=null;
 let tracking={points:[],frame:null,faces:[]},hoverTimer=null,hoverCandidate=null,cloudView=null,modelView=null;
 let propertiesOpen=false,dialogSpec=null,dialogSubmit=null,dialogPreview=null,lastObserved=null,refreshWanted=false;
 const nameMap={plane:'Plane',point:'Point',line:'Line',polyline:'3D polyline',circle:'Circle',arc:'Arc',rectangle:'Rectangle',extrude:'Extrude',sweep:'Sweep',loft:'Loft',box:'Box',cylinder:'Cylinder',sphere:'Sphere'};
 function el(tag,cls,html){const e=document.createElement(tag);if(cls)e.className=cls;if(html!=null)e.innerHTML=html;return e;}
 const icon=window.ToolIcons.icon;
 const buttons=items=>items.map(([id,label,title])=>`<button type="button" data-cad-command="${id}" aria-label="${esc(label)}" title="${esc(title||label)}">${icon(id)}<span class="ts-tool-label">${esc(({ucs:"Origin / axes",fit:"Fit",import:"Import model"})[id]||label)}</span></button>`).join('');
 const modebar=el('div','cad-modebar','<span>WORKSPACE</span>'+['Clean','Edit','Analyse','Model'].map(v=>`<button data-cad-mode="${v}" aria-label="${v}" title="${v} workspace" aria-pressed="${v===mode}">${icon({Clean:"clean",Edit:"edit",Analyse:"chart",Model:"cube"}[v])}<span class="ts-tool-label">${v}</span></button>`).join('')+'<label class="cad-destination-label" title="Bundle for new models and imports"><select id="cad-destination" aria-label="Working bundle"></select></label><button id="cad-workspace-import" aria-label="Import models or clouds" title="Import models or clouds">'+icon('import')+'<span class="ts-tool-label">Import</span></button><button id="cad-workspace-export" aria-label="Export models or clouds" title="Export models or clouds">'+icon('export')+'<span class="ts-tool-label">Export</span></button><button id="cad-frame-button" class="cad-frame-button" title="Set the working origin and axes">World · metres</button>');
 document.querySelector('.job-layout').before(modebar);
 const toolbar=el('div','cad-toolbar');toolbar.hidden=true;
 toolbar.innerHTML='<div class="cad-tools-group"><span>Draw</span>'+buttons([['point','Point'],['line','Line'],['polyline','Polyline'],['circle','Circle'],['arc','Arc'],['rectangle','Rectangle']])+'</div><div class="cad-tools-group"><span>3D</span>'+buttons([['extrude','Extrude'],['sweep','Sweep'],['loft','Loft'],['alignment','Along alignment']])+'<button id="cad-solid-tool" aria-label="Create a solid" title="Create a solid" aria-haspopup="menu">'+icon('cube')+'<span class="ts-tool-label">Solids ▾</span></button></div><div class="cad-tools-group"><span>Modify</span>'+buttons([['move','Move'],['cut','Cut'],['trim','Trim']])+'<button id="cad-modify-tool" aria-label="More modify tools" title="More modify tools" aria-haspopup="menu">'+icon('settings')+'<span class="ts-tool-label">More ▾</span></button></div><div class="cad-tools-group"><span>Project</span>'+buttons([['tunnel_designer','Tunnel designer'],['alignment_setup','Create / add alignment'],['ucs','Origin / axes'],['fit','Fit models'],['import','Import editable model'],['export','Export']])+'</div>';
 modebar.after(toolbar);
 const referenceToggle=el('label','cad-reference-toggle','<input id="cad-cloud-reference" type="checkbox">Cloud reference');referenceToggle.title='Show point clouds and reference meshes in Model space';referenceToggle.hidden=true;modebar.append(referenceToggle);
 const cloudSnap=el('label','cad-reference-toggle','<input id="cad-cloud-snap" type="checkbox">Snap to cloud');
 cloudSnap.hidden=true;cloudSnap.title='Recover a retained measured point on click when no geometry snap takes priority.';
 modebar.append(cloudSnap);
 const planeLock=el('button',null,'Lock to plane');
 planeLock.id='cad-view-lock';planeLock.type='button';planeLock.hidden=true;
 planeLock.title='Face the working plane. Drag to pan, scroll to zoom.';
 planeLock.setAttribute('aria-pressed','false');
 document.querySelector('.job-cad-nav').append(planeLock);
 const consoleBar=el('div','cad-console');consoleBar.hidden=true;
 consoleBar.innerHTML='<div class="cad-statusrow"><strong id="cad-active-command">MODEL</strong><span id="cad-prompt" role="status">Choose a tool or enter a command.</span><button id="cad-finish">Finish ↵</button><button id="cad-cancel">Cancel Esc</button><button id="cad-console-toggle" title="Collapse command bar" aria-expanded="true">⌄</button></div><div class="cad-inputrow"><span>Command ›</span><input id="cad-command-input" autocomplete="off" spellcheck="false" aria-label="CAD command or coordinates" placeholder="LINE · 0,0,0 · @2,0,1 · #420000,285000,100"><button id="cad-command-enter">Enter</button></div><div class="cad-options"><label>Coordinates <select id="cad-space"><option value="local">Working frame</option><option value="world">World E/N/H</option></select></label><label>Plane <select id="cad-plane"><option>XY</option><option>XZ</option><option>YZ</option></select></label><label>Lock <select id="cad-axis"><option value="">Free</option><option value="x">X</option><option value="y">Y</option><option value="z">Z</option><option value="xy">XY</option><option value="xz">XZ</option><option value="yz">YZ</option></select></label><label><input id="cad-ortho" type="checkbox">Ortho</label><label><input id="cad-snap" type="checkbox" checked>Snap</label><label title="Pause over two geometry points, then click their midpoint"><input id="cad-track" type="checkbox" checked>Track midpoint</label><button id="cad-snap-options">Snap settings</button><label><input id="cad-grid" type="checkbox">Grid</label><label>Step <input id="cad-grid-step" type="number" min="0.0001" value="1" step="0.1"> m</label><button id="cad-history-toggle">History</button><button id="cad-help">Help</button><span id="cad-coordinate-readout"></span></div><pre id="cad-history" hidden></pre>';
 document.querySelector('.job-view').after(consoleBar);
 const overlay=document.createElementNS('http://www.w3.org/2000/svg','svg');overlay.id='cad-overlay';overlay.setAttribute('aria-hidden','true');document.querySelector('.job-view').append(overlay);
 const hud=el('div','cad-hud');document.querySelector('.job-view').append(hud);
 const side=document.querySelector('.job-sidebar'),tabs=el('div',null,'<button data-cad-tab="project" aria-selected="true">Project</button><button data-cad-tab="properties" aria-selected="false">Properties</button>');tabs.id='cad-panel-tabs';tabs.hidden=true;$c('job-sidehead').after(tabs);
 const panel=el('div');panel.id='cad-properties';panel.hidden=true;side.append(panel);
 const dialog=el('dialog','cad-dialog');dialog.id='cad-tool-dialog';dialog.innerHTML='<h2 id="cad-dialog-title"></h2><p id="cad-dialog-lead" class="cad-dialog-lead"></p><div id="cad-dialog-fields"></div><div id="cad-dialog-error" class="cad-error" role="status"></div><div class="cad-dialog-actions"><button id="cad-dialog-pick" hidden>Pick in 3D</button><button id="cad-dialog-preview" hidden>Preview</button><button id="cad-dialog-close">Cancel</button><button id="cad-dialog-apply" class="primary">Apply</button></div>';document.body.append(dialog);
 const snapSettings={endpoint:true,midpoint:true,centre:true,intersection:true,nearest:false,perpendicular:false,cloud:false};
 const objects=()=>model.objects||[],get=id=>objects().find(o=>o.id===id),chosen=()=>[...selected].map(get).filter(Boolean);
 const boundsCorners=o=>Array.from({length:8},(_,k)=>[0,1,2].map(i=>o[k&(1<<i)?'maximum':'minimum'][i]));
 const effectiveVisible=o=>o.visible!==false&&model.layers.find(l=>l.name===o.layer)?.visible!==false;
 function activeFrame(){return $c('cad-space').value==='world'?M.worldFrame():model.active_frame||M.worldFrame();}
 function drawingPlane(){return command&&(command.lockPlane||['circle','arc','rectangle'].includes(command.kind))?command.frame:workPlane();}
 function workPlane(){const f=activeFrame();return $c('cad-plane').value==='XZ'?{...f,x:f.x,y:f.z,z:M.mul(f.y,-1)}:$c('cad-plane').value==='YZ'?{...f,x:f.y,y:f.z,z:f.x}:f;}
 function releaseViewLock(){
  planeLock.setAttribute('aria-pressed','false');
  if(J()?.cadScene())J().cadScene().workPlaneView=null;
 }
 function syncPlaneView(scene){
  if(mode!=='Model'||planeLock.getAttribute('aria-pressed')!=='true'){
   scene.workPlaneView=null;return;
  }
  const f=drawingPlane();
  scene.workPlaneView={right:f.x,up:f.y,forward:M.mul(f.z,-1)};
 }
 function view(){const scene=J().cadScene();return {scene,basis:scene.basis(),screenPoint:J().cadScreenPoint};}
 function say(message,error=false){$c('cad-prompt').textContent=message;$c('cad-prompt').classList.toggle('cad-error',error);}
 function log(message){history.push(message);history=history.slice(-60);$c('cad-history').textContent=history.join('\n');$c('cad-history').scrollTop=1e6;}
 function frameLabel(){$c('cad-frame-button').innerHTML=icon('ucs')+'<span>'+esc(model.active_frame?.name||'World')+' · metres</span>';const b=$c('cad-destination'),active=window.WorkspaceUI?.activeBundle?.()||'';b.innerHTML=([{id:'',name:'Project'},...(J().inventory().bundles||[])]).map(v=>`<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');b.value=active;}
 function setPanel(show){propertiesOpen=show;panel.hidden=!show;side.classList.toggle('cad-properties-open',show);tabs.querySelectorAll('button').forEach(b=>b.setAttribute('aria-selected',(b.dataset.cadTab==='properties')===show));if(show)renderProperties();}
 function enter(next,options={}){
  if(window.TunnelDesigner?.isPicking()&&next!=='Model'){say('Finish or cancel section picking before changing workspace.',true);return false;}
  if(next!==mode&&dialog.open)closeDialog();
  if(command&&next!==mode){say('Finish the active command or press Escape before changing mode.',true);return false;}
  const previous=mode,scene=J().cadScene(),saveView=()=>({target:M.add(scene.target,J().cadOrigin()),scale:scene.scale,yaw:scene.yaw,pitch:scene.pitch});
  if(previous!==next){if(previous==='Model')modelView=saveView();else if(next==='Model'&&!J().setupCamera())cloudView=saveView();}
  mode=next;referenceToggle.hidden=cloudSnap.hidden=planeLock.hidden=next!=='Model';
  if(next!=='Model')releaseViewLock();document.body.classList.toggle('cad-model-mode',next==='Model');toolbar.hidden=consoleBar.hidden=tabs.hidden=next!=='Model';
  modebar.querySelectorAll('[data-cad-mode]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.cadMode===next));
  if(next==='Model'){
   if(J().setupCamera())window.SetupCameraUI?.exit(false);
   window.TunnelRibbon?.closeTool();J().setMode('orbit');frameLabel();say('Choose a tool or enter a command. Drag to orbit; Shift/right-drag to pan.');
  }else{setPanel(false);J().setMode('orbit');if(next==='Clean'){if(options.openTool!==false)J().openSurface();}else if(options.activateRibbon!==false){if(['Analyse','Measure'].includes(next))window.TunnelRibbon?.activate(next);else window.TunnelRibbon?.activate('Edit');}}
  if(previous!==next&&!options.preserveView&&(next==='Model'||previous==='Model')){const saved=next==='Model'?modelView:cloudView;if(saved){scene.target=M.sub(saved.target,J().cadOrigin());scene.scale=saved.scale;scene.yaw=saved.yaw;scene.pitch=saved.pitch;}else if(next==='Model'){const points=objects().filter(effectiveVisible).flatMap(boundsCorners);if(points.length)J().focusWorld(points);else{scene.target=M.sub(activeFrame().origin,J().cadOrigin());scene.scale=8;scene.yaw=-.8;scene.pitch=.4;}}}
  J().draw();return true;
 }
 async function load(force=false){
  if(loading){await loading;if(force)return load(false);return;}
  loading=(async()=>{try{const next=await api('/api/cad');if(!pending&&model.context&&next.context!==model.context){cancel('Workspace changed; unfinished modelling command cancelled.');cloudView=modelView=null;}
   model=next;selected=new Set([...selected].filter(id=>get(id)));edgeCache.clear();dirty=previewDirty=true;snapCache=null;frameLabel();renderTree();if(propertiesOpen)renderProperties();J().draw();
  }catch(e){say(e.message,true);}finally{loading=null;if(refreshWanted){refreshWanted=false;queueMicrotask(()=>load());}}})();return loading;
 }
 function observe(data){const key=data.cad_revision+'|'+data.cad_context;if(data.cad_context&&key!==lastObserved){lastObserved=key;load();}renderTree();}
 async function run(body,endpoint='/api/cad-action'){
  if(pending||busy)return false;pending=true;const captured={context:model.context,revision:model.revision};say('Working…');
  try{setBusy(true);await post(endpoint,{...body,...captured});const result=await J().waitReady(job=>say(job.message),{skipSync:true});if(result.cancelled){say('Operation cancelled.');return false;}
   await load(true);selected=new Set(model.last_created||[]);dirty=true;preview=null;previewDirty=true;command=null;J().setMode('orbit');await J().sync({inventoryOnly:true});renderTree();if(propertiesOpen)renderProperties();
   window.HistoryUI?.sync();say('Ready · '+(selected.size?selected.size+' model object'+(selected.size===1?'':'s')+' selected.':'Saved.'));log('✓ '+(body.spec?.kind||body.action||'Import')+' completed');J().draw();return true;
  }catch(e){setBusy(false);say(e.message,true);log('! '+e.message);throw e;}finally{pending=false;setBusy(false);}
 }
 function defaults(){return {bundle_id:window.WorkspaceUI?.activeBundle?.()||chosen()[0]?.bundle_id||J().selectedItem()?.bundle_id||'',layer:chosen()[0]?.layer||'Model',colour:chosen()[0]?.colour||'#62bdcf'};}
 function clearSelected(){if(!selected.size)return;selected.clear();dirty=true;renderTree();if(propertiesOpen)renderProperties();J().draw();}
 function select(id,event={}){
  if(dialog.open&&extrusion)closeDialog();
  if(busy||pending||!get(id))return;if(mode!=='Model')enter('Model');if(command){say('Finish or cancel the active command before selecting other geometry.',true);return;}
  const additive=event.ctrlKey||event.metaKey;J().selectGeometry(additive);if(!additive)window.SceneObjectsUI?.clearSelected(true);if(additive)selected.has(id)?selected.delete(id):selected.add(id);else selected=new Set([id]);
  window.WorkspaceUI?.setActiveBundle?.(get(id).bundle_id);dirty=true;renderTree();if(mode==='Model')setPanel(true);J().draw();
 }
 function renderTree(){
  const body=$c('job-list');if(!body)return;body.querySelectorAll('[data-cad-row]').forEach(x=>x.remove());
  const anchors=[...body.querySelectorAll('[data-cad-anchor]')];if(!anchors.length)return;
  for(const anchor of anchors){if(anchor.dataset.cadCollapsed==='true')continue;const group=objects().filter(o=>(anchors.some(a=>a.dataset.cadAnchor===o.bundle_id)?o.bundle_id:anchors[0].dataset.cadAnchor)===anchor.dataset.cadAnchor&&(!anchor.dataset.cadFilter||o.name.toLocaleLowerCase().includes(anchor.dataset.cadFilter)));if(!group.length)continue;
   const heading=el('tr','cad-model-heading',`<td colspan="3">Models · ${group.length}</td>`);heading.dataset.cadRow='heading';anchor.before(heading);
   for(const o of group){const row=el('tr','cad-model-row',`<td><button data-cad-fit title="Fit model in 3D">⌖</button></td><td><button class="visibility-toggle" data-cad-eye aria-pressed="${effectiveVisible(o)}" aria-label="${o.visible?'Hide':'Show'} ${esc(o.name)}" title="${effectiveVisible(o)?'Visible · click to hide':o.visible?'Hidden by its layer · edit layer visibility in Properties':'Hidden · click to show'}">${window.ToolIcons.bulb(effectiveVisible(o))}</button></td><td><b><span class="cad-colour-dot" style="background:${esc(o.colour)}"></span>${esc(o.name)}</b><small>${esc(nameMap[o.shape]||o.shape)} · ${o.solid?'Solid':o.faces.length?'Surface':o.closed?'Closed profile':'Geometry'} · ${esc(o.layer)}</small></td>`);
    row.dataset.cadRow=o.id;row.tabIndex=0;row.setAttribute('aria-selected',selected.has(o.id));row.onclick=e=>{if(!e.target.closest('button'))select(o.id,e);};row.ondblclick=()=>properties(o.id);row.oncontextmenu=e=>menu(e,o.id);row.onkeydown=e=>{if(e.key==='Enter')select(o.id,e);if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'){e.preventDefault();e.stopPropagation();selected=new Set(objects().map(o=>o.id));dirty=true;renderTree();J().draw();}if(e.key==='Delete'){e.preventDefault();e.stopPropagation();if(!selected.has(o.id))selected=new Set([o.id]);remove();}};
    row.querySelector('[data-cad-fit]').onclick=()=>{select(o.id);J().focusWorld(o.vertices);};row.querySelector('[data-cad-eye]').onclick=()=>safeRun({action:'visibility',ids:[o.id]});anchor.before(row);
   }
  }
 }
 const safeRun=body=>run(body).catch(()=>false);
 function objectEdges(o){let value=edgeCache.get(o);if(!value){value=M.edges(o);edgeCache.set(o,value);}return value;}
 function pack(objectsToPack,previewMode=false){
  const scene=J().cadScene(),prefix=previewMode?'cadPreview':'cad';
  const items=objectsToPack.filter(o=>previewMode||effectiveVisible(o)).map(o=>({
   object:o,edges:previewMode?M.edges(o):objectEdges(o)
  }));
  const faceCount=items.reduce((n,{object})=>n+(object.faces?.length||0)*3,0);
  const lineCount=items.reduce((n,{edges})=>n+edges.length*2,0);
  const pointCount=items.filter(({object})=>object.vertices.length===1).length;
  const crossCount=previewMode&&pointCount?6:0;
  const faces=new Float32Array(faceCount*6),lines=new Float32Array((lineCount+crossCount)*6),dots=new Float32Array(pointCount*6);
  let fi=0,li=0,di=0;
  function put(dest,offset,p,c){
   dest[offset]=p[0]-renderOrigin[0];dest[offset+1]=p[1]-renderOrigin[1];dest[offset+2]=p[2]-renderOrigin[2];
   dest[offset+3]=c[0];dest[offset+4]=c[1];dest[offset+5]=c[2];
   return offset+6;
  }
  for(const {object:o,edges} of items){
   const colour=previewMode?[.45,.94,.80]:selected.has(o.id)?[1,.83,.25]:[1,3,5].map(i=>parseInt((o.colour||'#62bdcf').slice(i,i+2),16)/255);
   const p=o.vertices;
   for(const [a,b,c] of o.faces||[]){
    const n=M.unit(M.cross(M.sub(p[b],p[a]),M.sub(p[c],p[a])));
    const shade=.5+.5*Math.abs(M.dot(n,[.3,-.4,.8660254])),rgb=colour.map(v=>v*shade);
    fi=put(faces,fi,p[a],rgb);fi=put(faces,fi,p[b],rgb);fi=put(faces,fi,p[c],rgb);
   }
   for(const [a,b] of edges){li=put(lines,li,p[a],colour);li=put(lines,li,p[b],colour);}
   if(p.length===1)di=put(dots,di,p[0],colour);
  }
  if(crossCount){
   const position=Array.from(dots.slice(0,3),(v,i)=>v+renderOrigin[i]),colour=dots.slice(3,6);
   const radius=Math.max(.005,scene.scale*.006);
   for(let i=0;i<3;i++){
    const a=position.slice(),b=position.slice();a[i]-=radius;b[i]+=radius;
    li=put(lines,li,a,colour);li=put(lines,li,b,colour);
   }
  }
  for(const [key,buffer] of [['Faces',faces],['Lines',lines],...(!previewMode?[['Points',dots]]:[])]){
   const id=prefix+key+'Buffer';
   if(!scene[id])scene[id]=scene.gl.createBuffer();
   scene.upload(scene[id],buffer);scene[prefix+key+'Count']=buffer.length/6;
  }
 }
 function beforeDraw(){const scene=J().cadScene();syncPlaneView(scene);scene.cadHidden=mode!=='Model';scene.modelOnly=mode==='Model'&&!$c('cad-cloud-reference').checked;document.body.classList.toggle('cad-model-only',scene.modelOnly);if(!scene.gl)return;if(mode==='Model')$c('job-empty').hidden=true;
  if(dirty){const first=objects().find(effectiveVisible)?.vertices[0]||preview?.vertices?.[0]||model.active_frame?.origin||[0,0,0];if(M.norm(M.sub(first,renderOrigin))>1000){renderOrigin=first.slice();previewDirty=true;}pack(objects());dirty=false;}
  scene.cadTarget=M.sub(M.add(scene.target,J().cadOrigin()),renderOrigin);
  if(previewDirty){pack(preview?[preview]:[],true);previewDirty=false;}
 }
 function setPreview(value){preview=value;previewDirty=true;if(!objects().length&&value?.vertices?.length&&M.norm(M.sub(value.vertices[0],renderOrigin))>1000){renderOrigin=value.vertices[0].slice();dirty=true;}J().draw();}
 function screenKey(){const s=J().cadScene();return JSON.stringify([s.target,s.scale,s.yaw,s.pitch,s.workPlaneView,J().cadOrigin(),canvas.clientWidth,canvas.clientHeight,model.revision]);}
 function index(){const key=screenKey();if(snapCache?.key===key)return snapCache;const grid=new Map(),edges=[];const put=(x,y,row)=>{const k=Math.floor(x/24)+','+Math.floor(y/24);if(!grid.has(k))grid.set(k,[]);grid.get(k).push(row);};
  for(const o of objects().filter(effectiveVisible)){
   const p=o.vertices,pp=p.map(J().projectWorld);for(let i=0;i<p.length;i++){const q=pp[i];if(q&&q[0]>=-24&&q[1]>=-24&&q[0]<=canvas.clientWidth+24&&q[1]<=canvas.clientHeight+24)put(q[0],q[1],{point:p[i],pixel:q,type:'endpoint',object:o});}
   if(o.centre){const q=J().projectWorld(o.centre);if(q)put(q[0],q[1],{point:o.centre,pixel:q,type:'centre',object:o});}
   for(const [a,b] of objectEdges(o)){if(!pp[a]||!pp[b])continue;const row={a:p[a],b:p[b],pa:pp[a],pb:pp[b],object:o,type:'edge'};edges.push(row);const mid=M.mul(M.add(p[a],p[b]),.5),mp=J().projectWorld(mid);if(mp)put(mp[0],mp[1],{point:mid,pixel:mp,type:'midpoint',object:o,edge:row});
    const minx=Math.max(0,Math.floor(Math.min(pp[a][0],pp[b][0])/24)),maxx=Math.min(Math.ceil(canvas.clientWidth/24),Math.floor(Math.max(pp[a][0],pp[b][0])/24)),miny=Math.max(0,Math.floor(Math.min(pp[a][1],pp[b][1])/24)),maxy=Math.min(Math.ceil(canvas.clientHeight/24),Math.floor(Math.max(pp[a][1],pp[b][1])/24));
    for(let x=minx;x<=maxx;x++)for(let y=miny;y<=maxy;y++)if(M.segmentPixel([x*24+12,y*24+12],pp[a],pp[b]).d<30)put(x*24+12,y*24+12,row);
   }
  }
  return snapCache={key,grid,edges};
 }
 function nearby(pixel){const grid=index().grid,x=Math.floor(pixel[0]/24),y=Math.floor(pixel[1]/24),set=new Set();for(let a=x-1;a<=x+1;a++)for(let b=y-1;b<=y+1;b++)for(const row of grid.get(a+','+b)||[])set.add(row);return [...set];}
 function snapped(pixel){
  if(!$c('cad-snap').checked)return null;
  const edit=command?.profileEdit,moved=edit?.vertices[edit.index];
  let candidates=nearby(pixel);
  if(moved&&edit.objectId)candidates=candidates.filter(c=>{
   if(c.object.id!==edit.objectId)return true;
   const edge=c.edge||c;
   return c.point!==moved&&edge.a!==moved&&edge.b!==moved;
  });
  const last=command?.points?.at(-1)||moved;let best=null,distance=12;
  const use=(p,type,object,frame=null)=>{if(command?.faceLocked&&Math.abs(M.dot(M.sub(p,command.points[0]),command.frame.z))>Math.max(1e-7,J().cadScene().scale*1e-8))return;const q=J().projectWorld(p);if(!q)return;const d=Math.hypot(pixel[0]-q[0],pixel[1]-q[1]);if(d<distance-.001||type==='tracked midpoint'&&d<12&&d<=distance+.001||d<12&&best&&Math.abs(d-distance)<.001&&M.dot(M.sub(p,best.point),J().cadScene().basis().forward)>1e-7){distance=d;best={point:p,pixel:q,type,object,frame};}};
  for(const c of candidates){if(c.type!=='edge'&&snapSettings[c.type])use(c.point,c.type,c.object);if(c.type==='edge'){
   if(snapSettings.nearest){const t=M.segmentPixel(pixel,c.pa,c.pb).t;use(M.add(c.a,M.mul(M.sub(c.b,c.a),t)),'nearest',c.object);}
   if(snapSettings.perpendicular&&last)use(M.segmentClosest(last,c.a,c.b),'perpendicular',c.object);
  }}
  if(snapSettings.intersection){const near=candidates.filter(c=>c.type==='edge'&&M.segmentPixel(pixel,c.pa,c.pb).d<12).slice(0,64);for(let i=0;i<near.length;i++)for(let j=0;j<i;j++){const p=M.intersection(near[i].a,near[i].b,near[j].a,near[j].b);if(p)use(p,'intersection',near[i].object);}}
  if(tracking.points.length===2&&trackingEnabled()){const [a,b]=tracking.points;use(M.midpoint(a.point,b.point),'tracked midpoint',a.object,tracking.frame);}
  return best;
 }
 function trackingEnabled(){return mode==='Model'&&$c('cad-snap').checked&&$c('cad-track').checked&&command&&['point','line','polyline','circle','arc','rectangle','pick'].includes(command.kind)&&(!['circle','arc','rectangle'].includes(command.kind)||!command.points.length);}
 function stopHover(){clearTimeout(hoverTimer);hoverTimer=null;hoverCandidate=null;}
 function resetTracking(){stopHover();tracking={points:[],frame:null,faces:[]};}
 function trackHover(pixelPoint){
  if(command?.profileEdit?.index===null){stopHover();return;}
  const hit=snapHit;if(!trackingEnabled()||!hit||!['endpoint','midpoint','centre','intersection'].includes(hit.type)){stopHover();return;}
  const same=(a,b)=>a.object?.id===b.object?.id&&M.norm(M.sub(a.point,b.point))<1e-8;
  if(tracking.points.some(p=>same(p,hit))){stopHover();return;}
  if(hoverCandidate&&same(hoverCandidate,hit))return;
  stopHover();hoverCandidate=hit;const active=command,key=screenKey(),location=pixelPoint.slice();
  hoverTimer=setTimeout(()=>{
   hoverTimer=null;hoverCandidate=null;if(command!==active||busy||pending||!trackingEnabled()||screenKey()!==key)return;
   tracking.points=[...tracking.points,hit].slice(-2);tracking.frame=null;tracking.faces=[];
   if(tracking.points.length===2){const [a,b]=tracking.points,face=a.object===b.object?M.commonFaceFrame(a.object,a.point,b.point,J().cadScene().basis().forward):null;
    tracking.frame=face?.frame||null;tracking.faces=face?.faces||[];
    say(face?'Centre ready · click the midpoint marker · drawing will use the highlighted face plane.':'Centre ready · no shared face detected; the current work plane will be used.');
   }else say('First point tracked · hover over the opposite corner, then click the centre marker.');
   cursor=currentPoint(location);J().draw();
  },350);
 }
 function adoptTrackedPlane(){if(snapHit?.type==='tracked midpoint'&&snapHit.frame&&command&&!command.points.length&&['circle','arc','rectangle'].includes(command.kind)){command.frame=snapHit.frame;command.faceLocked=true;}}
 function currentPoint(pixel){snapHit=snapped(pixel);let point=snapHit?.point||M.planePoint(pixel,view(),drawingPlane(),command?.points?.[0]||null);if(!point)return null;
  if(!snapHit&&$c('cad-grid').checked){const f=drawingPlane(),local=M.toLocal(point,f),step=Math.max(.0001,Number($c('cad-grid-step').value)||1);local[0]=Math.round(local[0]/step)*step;local[1]=Math.round(local[1]/step)*step;point=M.toWorld(local,f);}
  point=M.constrain(point,command?.points?.at(-1),command?.faceLocked||command?.lockPlane?command.frame:activeFrame(),$c('cad-axis').value,$c('cad-ortho').checked);if(command?.lockPlane){const f=command.frame;point=M.sub(point,M.mul(f.z,M.dot(M.sub(point,f.origin),f.z)));}return point;
 }
 function pick(event){if(mode!=='Model')return null;const r=canvas.getBoundingClientRect(),pixel=[event.clientX-r.left,event.clientY-r.top];let best=null,distance=10;
  for(const c of nearby(pixel)){if(c.type==='edge'){const d=M.segmentPixel(pixel,c.pa,c.pb).d;if(d<distance){best=c.object;distance=d;}}else if(c.type==='endpoint'&&c.object.vertices.length===1){const d=Math.hypot(pixel[0]-c.pixel[0],pixel[1]-c.pixel[1]);if(d<distance){best=c.object;distance=d;}}}
  if(best)return best;const basis=J().cadScene().basis(),origin=M.add(J().cadScreenPoint(pixel),M.mul(basis.forward,1e7)),direction=M.mul(basis.forward,-1);let depth=Infinity;
  for(const o of objects().filter(effectiveVisible)){if(!o.faces?.length)continue;const corners=Array.from({length:8},(_,k)=>[0,1,2].map(i=>o[k&(1<<i)?'maximum':'minimum'][i])).map(J().projectWorld).filter(Boolean);if(!corners.length||pixel[0]<Math.min(...corners.map(p=>p[0]))||pixel[0]>Math.max(...corners.map(p=>p[0]))||pixel[1]<Math.min(...corners.map(p=>p[1]))||pixel[1]>Math.max(...corners.map(p=>p[1])))continue;
   for(const f of o.faces){const t=M.rayTriangle(origin,direction,...f.map(i=>o.vertices[i]));if(t!=null&&t<depth){depth=t;best=o;}}
  }return best;
 }
 function afterDraw(){const width=canvas.clientWidth,height=canvas.clientHeight;overlay.setAttribute('viewBox',`0 0 ${width} ${height}`);const svg=[];
  if(mode==='Model'){
   const f=drawingPlane();
   if(command?.lockPlane){const size=J().cadScene().scale*.7,centre=M.toLocal(M.add(J().cadScene().target,J().cadOrigin()),f),corners=[[-1,-1],[1,-1],[1,1],[-1,1]].map(([x,y])=>J().projectWorld(M.toWorld([centre[0]+x*size,centre[1]+y*size,0],f)));if(corners.every(Boolean))svg.push(`<polygon data-cad-drawing-plane="true" points="${corners.map(p=>p.join(',')).join(' ')}" fill="#71cbd315" stroke="#71cbd3" stroke-dasharray="5 5"/>`);}

   if($c('cad-grid').checked){const step=Math.max(.0001,Number($c('cad-grid-step').value)||1),centre=M.toLocal(M.add(J().cadScene().target,J().cadOrigin()),f);const cx=Math.round(centre[0]/step)*step,cy=Math.round(centre[1]/step)*step;for(let i=-10;i<=10;i++)for(const vertical of [false,true]){const a=M.toWorld(vertical?[cx+i*step,cy-10*step,0]:[cx-10*step,cy+i*step,0],f),b=M.toWorld(vertical?[cx+i*step,cy+10*step,0]:[cx+10*step,cy+i*step,0],f),pa=J().projectWorld(a),pb=J().projectWorld(b);if(pa&&pb)svg.push(`<line x1="${pa[0]}" y1="${pa[1]}" x2="${pb[0]}" y2="${pb[1]}" stroke="#7492a0" opacity=".22" stroke-width="1"/>`);}}
   const world=J().projectWorld(f.origin);if(world&&world[0]>0&&world[0]<width&&world[1]>0&&world[1]<height){const length=J().cadScene().scale*.12;for(const [axis,col] of [['x','#f8958e'],['y','#83daa6'],['z','#8ebcff']]){const p=J().projectWorld(M.add(f.origin,M.mul(f[axis],length)));if(p)svg.push(`<line x1="${world[0]}" y1="${world[1]}" x2="${p[0]}" y2="${p[1]}" stroke="${col}" stroke-width="2"/><text x="${p[0]+4}" y="${p[1]-3}" fill="${col}" font-size="11">${axis.toUpperCase()}</text>`);}}
   const basis=J().cadScene().basis(),x=35,y=height-37;for(const [axis,col] of [['x','#f8958e'],['y','#83daa6'],['z','#8ebcff']]){const a=f[axis],px=x+M.dot(a,basis.right)*24,py=y-M.dot(a,basis.up)*24;svg.push(`<line x1="${x}" y1="${y}" x2="${px}" y2="${py}" stroke="${col}" stroke-width="2"/><text x="${px+4}" y="${py}" fill="${col}" font-size="10">${axis.toUpperCase()}</text>`);}
   if(tracking.points.length&&trackingEnabled()){
    const points=tracking.points.map(p=>J().projectWorld(p.point));
    if(tracking.frame&&tracking.faces.length){const o=tracking.points[0].object,path=tracking.faces.slice(0,2000).map(face=>face.map(i=>J().projectWorld(o.vertices[i]))).filter(v=>v.every(Boolean)).map(v=>'M'+v.map(p=>p.join(',')).join('L')+'Z').join('');svg.push(`<path data-cad-tracked-plane="face" d="${path}" fill="#6ce7cb20" stroke="none"/>`);}
    points.forEach((p,i)=>{if(p)svg.push(`<g data-cad-tracked-point="${i+1}"><circle cx="${p[0]}" cy="${p[1]}" r="7" fill="#102d3c" stroke="#ffdf80" stroke-width="2"/><text x="${p[0]+11}" y="${p[1]+16}" fill="#ffdf80" font-size="12">${i+1}</text></g>`);});
    if(points.length===2&&points.every(Boolean)){const mid=J().projectWorld(M.midpoint(...tracking.points.map(p=>p.point)));if(mid)svg.push(`<line x1="${points[0][0]}" y1="${points[0][1]}" x2="${points[1][0]}" y2="${points[1][1]}" stroke="#ffdf80" stroke-dasharray="5 5"/><g data-cad-tracked-midpoint="true"><circle cx="${mid[0]}" cy="${mid[1]}" r="8" fill="#102d3c" stroke="#a5f5cd" stroke-width="2"/><path d="M${mid[0]-4},${mid[1]}h8 M${mid[0]},${mid[1]-4}v8" stroke="#a5f5cd"/><text x="${mid[0]+13}" y="${mid[1]+19}" fill="#b9ffe0" stroke="#102d3c" paint-order="stroke" stroke-width="3" font-size="12">Centre · ${tracking.frame?'Face plane':$c('cad-plane').value+' plane'}</text></g>`);}
   }
   if(command?.profileEdit){
    const edit=command.profileEdit;
    if(edit.index!==null&&cursor){
     const vertices=edit.previewOutline?edit.previewOutline(edit.index,cursor):
      edit.vertices.map((vertex,i)=>i===edit.index?cursor:vertex);
     const points=vertices.map(J().projectWorld),moving=J().projectWorld(cursor);
     if(points.length&&points.every(Boolean))svg.push(`<polygon data-cad-profile-preview="true" points="${points.map(p=>p.join(',')).join(' ')}" fill="#7de0ca12" stroke="#9ce8de" stroke-width="2"/>`);
     if(moving)svg.push(`<circle data-cad-profile-moving="true" cx="${moving[0]}" cy="${moving[1]}" r="6" fill="#ffcf78" stroke="#9ce8de" stroke-width="1.5"/>`);
    }
    edit.vertices.forEach((v,i)=>{
     if(i===edit.index&&cursor)return;
     const p=J().projectWorld(v);
     if(p)svg.push(`<circle cx="${p[0]}" cy="${p[1]}" r="${i===edit.index?6:4}" fill="${i===edit.index?'#ffcf78':'#153645'}" stroke="#9ce8de" stroke-width="1.5"/>`);
    });
   }
   if(snapHit&&command){const p=snapHit.pixel;svg.push(`<rect x="${p[0]-5}" y="${p[1]-5}" width="10" height="10" fill="none" stroke="#a5f5cd" stroke-width="2"/><text x="${p[0]+12}" y="${p[1]-10}" fill="#b9ffe0" stroke="#102d3c" paint-order="stroke" stroke-width="3" font-size="11">${esc(snapHit.type)}</text>`);}
   if(extrusion?.profile){const h=extrudeHandle();if(h){const polygon=extrusion.profile.vertices.map(p=>J().projectWorld(M.add(p,M.mul(extrusion.vector,extrusion.symmetric?.5:1))));if(polygon.every(Boolean)&&extrusion.profile.closed)svg.push(`<polygon data-cad-extrude-face="true" points="${polygon.map(p=>p.join(',')).join(' ')}" fill="#6ce7cb30" stroke="#8ce8d6" stroke-width="2"/>`);svg.push(`<line x1="${h.a[0]}" y1="${h.a[1]}" x2="${h.b[0]}" y2="${h.b[1]}" stroke="#ffdf80" stroke-width="3"/><circle data-cad-extrude-handle="true" cx="${h.b[0]}" cy="${h.b[1]}" r="9" fill="#ffdf80" stroke="#1b414e" stroke-width="2"/><text x="${h.b[0]+14}" y="${h.b[1]-12}" fill="#fff0b2" stroke="#102d3c" paint-order="stroke" stroke-width="4" font-size="12">${M.norm(extrusion.vector).toFixed(3)} m</text>`);}}

   hud.innerHTML=`<b>${esc(command?nameMap[command.kind]||command.kind:'Model')}</b> · ${esc(model.active_frame?.name||'World')}<br>${command?.lockPlane?'Drawing plane locked':command?.faceLocked?'Face plane locked':tracking.frame?'Face plane detected':esc($c('cad-plane').value)+' work plane'} · metres${selected.size?' · '+selected.size+' selected':''}`;
  }else hud.innerHTML='';overlay.innerHTML=svg.join('');
 }
 function cancel(message='Command cancelled.'){const callback=command?.onCancel;resetTracking();if(dialog.open)closeDialog();extrusion=null;handleDrag=null;previewSequence++;clearTimeout(previewTimer);command=null;snapHit=null;cursor=null;dialogSpec=null;setPreview(null);J().setMode('orbit');$c('cad-active-command').textContent='MODEL';say(message);callback?.(message);}
 function start(kind,custom={}){if(window.TunnelDesigner?.isPicking()){say('Finish or cancel section picking first.',true);return;}if(pending||busy)return;if(mode!=='Model'&&!enter('Model'))return;if(command){say('Finish or cancel the active command first.',true);return;}
  resetTracking();command={kind,points:[],frame:workPlane(),space:$c('cad-space').value,plane:$c('cad-plane').value,...custom};snapHit=null;J().setMode('cad');$c('cad-active-command').textContent=(nameMap[kind]||kind).toUpperCase();if(commandBarChoice!==true)setCommandBarCollapsed(false);$c('cad-command-input').focus();promptCommand();J().draw();return true;
 }
 function promptCommand(){if(!command)return;const n=command.points.length,k=command.kind;
  const message=command.prompt?.(n)||({point:'Pick or type the point coordinates.',line:n?'Specify the end point.':'Specify the start point.',polyline:n?'Next vertex · Enter finishes · C closes · Backspace removes last vertex.':'Specify the first vertex.',circle:n?'Enter radius in metres or pick a point on the circle.':'Choose centre, or hover over two opposite face corners and click their midpoint.',rectangle:n?'Specify the opposite corner on the work plane.':'Specify the first corner.',arc:['Specify the arc centre.','Specify the arc start point.','Specify the arc end point (counter-clockwise).'][n]})[command.kind];say(message||'Pick or type a point.');
 }
 function moveProfileNode({frame,vertices,objectId=null,outline=null,previewOutline=null,onMove,onDone}){
  if(command||pending||busy)return false;
  if(mode!=='Model'&&!enter('Model'))return false;
  const obj=objectId?get(objectId):null;
  if(objectId&&(!obj||!effectiveVisible(obj)))throw Error('Show the linked CAD outline in Project Explorer before editing it.');
  if(obj)select(objectId);
  const edit={vertices:obj?.vertices||vertices,index:null,objectId,previewOutline};
  const context=model.context,revision=model.revision,wasLocked=planeLock.getAttribute('aria-pressed')==='true';
  const done=(message,error=false,details={})=>{
   cursor=null;snapHit=null;
   if(!wasLocked)releaseViewLock();
   onDone(message,error,details);
  };
  const started=start('pick',{frame,lockPlane:true,count:1,profileEdit:edit,
   prompt:()=>edit.index===null?'Click the profile node to move.':'Pick or enter its new position in the profile plane. CAD snaps are available.',
   onCancel:message=>{
    const workspaceChanged=message.startsWith('Workspace changed');
    done(workspaceChanged?message:'Profile node edit cancelled.',workspaceChanged,{workspaceChanged});
   },
   callback:async ([point])=>{
    try{
     if(model.context!==context||model.revision!==revision)throw Error('The CAD workspace changed. Reopen the profile and try again.');
     if(obj){
      if(!await run({action:'vertex',ids:[obj.id],index:edit.index,point,profile_plane:true}))return done('Profile node edit cancelled.');
     }else await onMove(edit.index,point);
     done('Profile node moved.',false,{cadChanged:!!obj});
    }catch(e){done(e.message,true);}
   }});
  if(!started)return false;
  planeLock.setAttribute('aria-pressed','true');
  J().faceWorkPlane(frame.z);
  syncPlaneView(J().cadScene());
  if(outline)setPreview({vertices:outline,faces:[],closed:true});
  J().focusWorld(edit.vertices,false);
  return true;
 }
 function drawingSpec(p){const c=command,k=c.kind,all=[...c.points,...(p?[p]:[])],f=c.frame;
  if(k==='circle'&&all.length>=2)return {kind:k,centre:all[0],radius:Math.hypot(M.dot(M.sub(all[1],all[0]),f.x),M.dot(M.sub(all[1],all[0]),f.y)),frame:f,segments:96};
  if(k==='arc'&&all.length>=3){const a=M.toLocal(M.sub(all[1],all[0]),f,true),b=M.toLocal(M.sub(all[2],all[0]),f,true),start=Math.atan2(a[1],a[0])*180/Math.PI,end=Math.atan2(b[1],b[0])*180/Math.PI;return {kind:k,centre:all[0],radius:Math.hypot(a[0],a[1]),frame:f,start_angle:start,end_angle:start+(end-start+360)%360,segments:96};}
  if(k==='rectangle'&&all.length>=2){const a=all[0],d=M.toLocal(M.sub(all[1],a),f,true);return {kind:k,points:[a,M.add(a,M.mul(f.x,d[0])),M.add(a,M.add(M.mul(f.x,d[0]),M.mul(f.y,d[1]))),M.add(a,M.mul(f.y,d[1]))],closed:true};}
  return {kind:k,points:all,closed:false};
 }
 function simplePreview(spec){if(!spec)return null;if(['circle','arc'].includes(spec.kind)&&spec.frame&&spec.centre&&spec.radius>0){const points=[],f=spec.frame,steps=64,start=spec.start_angle||0,span=spec.kind==='circle'?360:spec.end_angle-start;for(let i=0;i<=(spec.kind==='circle'?steps-1:steps);i++){const t=(start+span*i/steps)*Math.PI/180;points.push(M.add(spec.centre,M.add(M.mul(f.x,spec.radius*Math.cos(t)),M.mul(f.y,spec.radius*Math.sin(t)))));}return {vertices:points,faces:[],closed:spec.kind==='circle'};}return spec.points?.length?{vertices:spec.points,faces:[],closed:!!spec.closed}:null;}
 async function acceptPoint(point){if(!command||pending)return;const c=command;
  if(c.profileEdit?.index===null)throw Error('Click the profile node you want to move first.');
  resetTracking();
  if(c.lockPlane){const f=c.frame;point=M.sub(point,M.mul(f.z,M.dot(M.sub(point,f.origin),f.z)));}
  if(c.points.length&&M.norm(M.sub(point,c.points.at(-1)))<1e-8){say('Choose a different next point.',true);return;}

  if(c.callback){c.points.push(point);if(c.points.length>=c.count){command=null;J().setMode('orbit');setPreview(null);await c.callback(c.points);}else promptCommand();return;}
  c.points.push(point);const need={point:1,line:2,circle:2,rectangle:2,arc:3}[c.kind];
  if(need&&c.points.length>=need){const spec=drawingSpec();try{await run({action:'create',spec,...defaults()});$c('cad-active-command').textContent='MODEL';}catch(_){c.points.pop();command=c;J().setMode('cad');}}
  else{setPreview(simplePreview(drawingSpec()));promptCommand();}
 }
 async function finish(close=false){if(!command)return;close=command.closedRequired||close;if(command.kind!=='polyline'){say('Complete the requested points, or press Escape to cancel.',true);return;}if(command.points.length<(close?3:2)){say(close?'A closed polyline needs at least three vertices.':'A polyline needs at least two vertices.',true);return;}
  if(command.onFinish){const c=command;if(c.openRequired&&close){say('An alignment is an open path. Use Finish to mark its end.',true);return;}command=null;setPreview(null);snapHit=null;cursor=null;J().setMode('orbit');$c('cad-active-command').textContent='MODEL';try{await c.onFinish(c.points.map(p=>p.slice()),c.frame);}catch(e){command=c;J().setMode('cad');setPreview(simplePreview(drawingSpec()));promptCommand();throw e;}return;}
  await run({action:'create',spec:{kind:'polyline',points:command.points,closed:close},...defaults()});$c('cad-active-command').textContent='MODEL';
 }
 async function input(){const field=$c('cad-command-input'),text=field.value.trim();field.value='';if(!text){await finish();return;}log('› '+text);historyPosition=-1;
  try{if(command){if(command.kind==='polyline'&&/^c(?:lose)?$/i.test(text)){await finish(true);return;}if(command.kind==='circle'&&command.points.length===1&&!/[,@#< ]/.test(text)){const radius=Number(text);if(!Number.isFinite(radius)||radius<=0)throw Error('Radius must be a positive number.');await acceptPoint(M.add(command.points[0],M.mul(command.frame.x,radius)));return;}
    await acceptPoint(M.parse(text,command.profileEdit?command.frame:activeFrame(),command.points.at(-1)||null));
   }else{const aliases={l:'line',pl:'polyline',pline:'polyline','3dpoly':'polyline',c:'circle',a:'arc',rec:'rectangle',ex:'extrude',m:'move',co:'copy',tr:'trim',u:'undo',uc:'ucs',del:'delete'};const words=text.toLowerCase().split(/\s+/),key=aliases[words[0]]||words[0];lastCommand=text;await tool(key);}
  }catch(e){say(e.message,true);log('! '+e.message);}
 }
 async function clickPoint(pixel){
  const edit=command?.profileEdit;
  if(edit?.index===null){
   let index=null,distance=13;
   edit.vertices.forEach((v,i)=>{
    const point=J().projectWorld(v);
    const error=point&&Math.hypot(pixel[0]-point[0],pixel[1]-point[1]);
    if(point&&error<distance){distance=error;index=i;}
   });
   if(index===null){say('Click closer to one of the marked profile nodes.',true);return;}
   edit.index=index;resetTracking();snapHit=null;cursor=edit.vertices[index];promptCommand();J().draw();return;
  }
  snapHit=snapped(pixel);if(!$c('cad-snap').checked||!snapSettings.cloud||snapHit){const p=currentPoint(pixel);if(!p){say('The view is edge-on to the work plane. Rotate the view or enter coordinates.',true);return;}adoptTrackedPlane();await acceptPoint(p);return;}
  const sample=$c('cad-cloud-reference').checked?J().cadCloudPoint(pixel):null;if(!sample){say('No displayed cloud point near the cursor. Zoom in, pick a point, or turn off Snap to cloud to draw on the plane.',true);return;}
  const capture=command;try{setBusy(true);await post('/api/cad-pick',{...sample,revision:model.revision,context:model.context});const result=await J().waitReady(null,{skipSync:true});if(result.cancelled)return;const found=await api('/api/cad-pick');if(command!==capture)return;if(!found.point)throw Error('No retained measured point was recovered.');log('Measured cloud point · '+found.source_id+' · index '+found.index);await acceptPoint(found.point);}catch(e){say(e.message,true);}finally{setBusy(false);}
 }
 function beginPick(count,prompt,callback){if(dialog.open)dialog.close();start('pick',{count,prompt:n=>prompt[n]||prompt[0],callback});}
 function openDialog(title,lead,fields,submit,options={}){
  if(dialog.open)dialog.close();previewSequence++;clearTimeout(previewTimer);if(options.spec?.kind!=='extrude')extrusion=null;setPreview(null);
  dialog.dataset.dialogMode='modeless';dialogSpec=options.spec||null;dialogSubmit=submit;dialogPreview=options.preview||null;$c('cad-dialog-title').textContent=title;$c('cad-dialog-lead').textContent=lead;$c('cad-dialog-fields').innerHTML=fields;$c('cad-dialog-error').textContent='';
  $c('cad-dialog-apply').textContent=options.applyLabel||'Apply';$c('cad-dialog-apply').hidden=!submit;$c('cad-dialog-preview').hidden=!dialogPreview;$c('cad-dialog-pick').hidden=!options.pick;$c('cad-dialog-pick').onclick=options.pick||null;if(!window.DetachedUI?.has(dialog.id)){dialog.show();const r=canvas.getBoundingClientRect();dialog.style.position='fixed';dialog.style.margin='0';dialog.style.left=Math.max(8,innerWidth-Math.min(380,innerWidth-24)-12)+'px';dialog.style.top=Math.max(8,Math.min(r.top+8,innerHeight-240))+'px';dialog.style.width=Math.min(380,innerWidth-24)+'px';dialog.style.maxHeight=Math.max(220,innerHeight-parseFloat(dialog.style.top)-12)+'px';}else{dialog.show();window.DetachedUI.focus(dialog.id);}
  $c('cad-dialog-fields').oninput=()=>{if(dialogPreview){clearTimeout(previewTimer);previewTimer=setTimeout(()=>dialogPreview().catch(showDialogError),180);}};
  if(dialogPreview)dialogPreview().catch(showDialogError);
 }
 function showDialogError(error){$c('cad-dialog-error').textContent=error.message||String(error);}
 function scalarField(id,label,value=1){return `<label>${esc(label)}<input id="${id}" type="number" step="any" value="${value}"></label>`;}
 function textField(id,label,value=''){return `<label>${esc(label)}<input id="${id}" value="${esc(String(value))}"></label>`;}
 function vectorField(id,label,value=[0,0,0]){return `<label>${esc(label)}</label><div class="cad-vector">${value.map((v,i)=>scalarField(id+'-'+i,['X / E','Y / N','Z / H'][i],v)).join('')}</div>`;}
 function vectorValue(id){const v=[0,1,2].map(i=>{const e=$c(id+'-'+i);if(!e||e.value.trim()==='')throw Error('Enter all three coordinate components.');return Number(e.value);});if(v.some(n=>!Number.isFinite(n)))throw Error('Enter finite coordinates.');return v;}
 function scalarValue(id){const e=$c(id);if(e.value.trim()===''||!Number.isFinite(Number(e.value)))throw Error('Enter a finite numeric value.');return Number(e.value);}
 function selectProfiles(count=null){const items=chosen();if(count!=null&&items.length!==count)throw Error('Select '+count+' profile'+(count===1?'':'s')+' first.');if(items.some(o=>o.faces.length||o.vertices.length<2))throw Error('Select drawn profiles, using Ctrl-click for multiple selection.');return items;}
 async function previewSpec(spec){const n=++previewSequence;dialogSpec=spec;if(extrusion){extrusion.vector=spec.vector;extrusion.symmetric=spec.symmetric;J().draw();}try{const out=await post('/api/cad-preview',{spec,revision:model.revision,context:model.context});if(n!==previewSequence||(!dialog.open&&!command))return;if(extrusion&&spec.kind==='extrude'){extrusion.profile=out.geometry.definition?.profile||extrusion.profile;extrusion.template=out.geometry;extrusion.templateVector=spec.vector.slice();}setPreview(out.geometry);$c('cad-dialog-error').textContent='';}catch(error){if(n!==previewSequence||!dialog.open)return;setPreview(null);throw error;}}
 function profileNormal(o,f=activeFrame()){const p=o.vertices,a=p[0],edge=p.slice(1).map(v=>M.sub(v,a)).find(v=>M.norm(v)>1e-9);if(!edge)return f.z;for(const b of p.slice(2)){const n=M.cross(edge,M.sub(b,a));if(M.norm(n)>1e-9)return M.unit(n);}const axis=Math.abs(M.dot(M.unit(edge),f.z))<.99?f.z:f.y;return M.unit(M.sub(axis,M.mul(M.unit(edge),M.dot(axis,M.unit(edge)))));}
 function faceAt(event,object=null){const pixelPoint=pixel(event),basis=J().cadScene().basis(),origin=M.add(J().cadScreenPoint(pixelPoint),M.mul(basis.forward,1e7)),direction=M.mul(basis.forward,-1);let result=null,depth=Infinity;for(const o of object?[object]:objects().filter(effectiveVisible))for(let i=0;i<o.faces.length;i++){const f=o.faces[i],t=M.rayTriangle(origin,direction,...f.map(j=>o.vertices[j]));if(t!=null&&t<depth){depth=t;result={object:o,face:i};}}return result;}
 function insidePolygon(p,points){let inside=false;for(let i=0,j=points.length-1;i<points.length;j=i++){const a=points[i],b=points[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])inside=!inside;}return inside;}
 function extrudeHandle(){if(!extrusion?.profile)return null;const p=extrusion.profile.vertices,base=M.mul(p.reduce((a,b)=>M.add(a,b),[0,0,0]),1/p.length),end=M.add(base,M.mul(extrusion.vector,extrusion.symmetric?.5:1)),a=J().projectWorld(base),b=J().projectWorld(end);if(!a||!b)return null;if(Math.hypot(b[0]-a[0],b[1]-a[1])<25){b[0]=a[0]+35;b[1]=a[1]-45;}return {base,end,a,b};}
 function updateDraggedExtrusion(vector){const ex=extrusion;if(!ex)return;ex.vector=vector;dialogSpec={...dialogSpec,vector};M.toLocal(vector,ex.frame,true).forEach((v,i)=>$c('cad-ex-vector-'+i).value=Number(v.toFixed(8)));$c('cad-ex-length').value=Number(M.norm(vector).toFixed(8));if(ex.template&&ex.profile){const p=ex.profile.vertices,base=p.map(q=>M.sub(q,M.mul(vector,ex.symmetric?.5:0))),vertices=base.concat(base.map(q=>M.add(q,vector))),sign=M.dot(vector,ex.normal)*M.dot(ex.templateVector,ex.normal)<0;const faces=sign?ex.template.faces.map(f=>[f[0],f[2],f[1]]):ex.template.faces;setPreview({...ex.template,vertices,faces,volume_m3:null,minimum:[0,1,2].map(i=>Math.min(...vertices.map(v=>v[i]))),maximum:[0,1,2].map(i=>Math.max(...vertices.map(v=>v[i])))});}else J().draw();say('Drag the highlighted face or gold handle · release to review · Apply to keep · Esc to cancel.');}
 function extrudeDialog(initial=null,picked=null,settings={}){
  const list=chosen();if(!picked&&(!list.length||list.length===1&&list[0].faces.length)){const o=list[0];if(o&&lastPickedFace?.object.id===o.id)picked=lastPickedFace;else{closeDialog();start('extrude-face',{count:1,prompt:()=> 'Click a drawn profile or a planar face to extrude. Esc cancels.',sourceId:o?.id});return;}}
  if(!picked&&list.length!==1)throw Error('Select one profile or face to extrude.');const o=picked?.object||list[0],face=picked?.face,f=activeFrame();if(o.vertices.length<2)throw Error('Choose a profile or face, not a single point.');
  const normal=face!=null?profileNormal({vertices:o.faces[face].map(i=>o.vertices[i])},f):profileNormal(o,f),value=initial||M.toLocal(normal,f,true);
  extrusion={source:o,face,frame:f,profile:face==null?o:null,normal,vector:M.toWorld(value,f,true),symmetric:!!settings.symmetric};
  const read=()=>({kind:'extrude',profile:o.id,...(face!=null?{face}:{}),vector:M.toWorld(vectorValue('cad-ex-vector'),f,true),symmetric:$c('cad-ex-symmetric').checked});
  openDialog('Extrude in 3D','Drag the highlighted face or gold handle in the scene. You can still orbit, pan and change tools.',textField('cad-ex-name','Model name',settings.name||o.name+' extruded')+'<label>Drag direction<select id="cad-ex-drag-direction"><option value="normal">Normal to profile / face</option><option value="x">Working X</option><option value="y">Working Y</option><option value="z">Working Z</option><option value="free">Free 3D · view plane</option></select></label>'+vectorField('cad-ex-vector','Displacement · '+f.name+' · metres',value)+'<label class="cad-check"><input id="cad-ex-symmetric" type="checkbox" '+(settings.symmetric?'checked':'')+'>Symmetric · total displacement</label><div class="cad-dialog-actions"><button id="cad-ex-normal">Normal</button><button id="cad-ex-reverse">Reverse</button></div>'+scalarField('cad-ex-length','Exact total length (m)',M.norm(value))+'<button id="cad-ex-set-length">Set length</button><p class="cad-help">A closed planar profile or face creates a new solid; an open profile creates a surface. The source remains. Pick two points or enter XYZ for an arbitrary 3D direction. Preview changes are kept only when you choose Apply.</p>',async()=>{const ok=await run({action:'create',spec:read(),name:$c('cad-ex-name').value,...defaults()});if(ok)closeDialog();},{preview:()=>previewSpec(read()),spec:readSpec(),pick:()=>{const saved={name:$c('cad-ex-name').value,symmetric:$c('cad-ex-symmetric').checked};beginPick(2,['Pick or type the direction start point.','Pick or type the direction end point.'],p=>extrudeDialog(M.toLocal(M.sub(p[1],p[0]),f,true),{object:o,face},saved));}});
  function readSpec(){return {kind:'extrude',profile:o.id,...(face!=null?{face}:{}),vector:M.toWorld(value,f,true),symmetric:!!settings.symmetric};}
  const set=v=>{v.forEach((n,i)=>$c('cad-ex-vector-'+i).value=n);$c('cad-ex-length').value=M.norm(v);dialogPreview().catch(showDialogError);};
  $c('cad-ex-normal').onclick=()=>{try{set(M.toLocal(M.mul(normal,scalarValue('cad-ex-length')),f,true));}catch(e){showDialogError(e);}};
  $c('cad-ex-reverse').onclick=()=>set(M.mul(vectorValue('cad-ex-vector'),-1));$c('cad-ex-set-length').onclick=()=>{try{const length=scalarValue('cad-ex-length');if(length<=0)throw Error('Length must be positive.');set(M.mul(M.unit(vectorValue('cad-ex-vector')),length));}catch(e){showDialogError(e);}};
  $c('cad-active-command').textContent='EXTRUDE';say('Pull the gold handle or highlighted face. Release to review; Apply keeps the extrusion; Escape cancels.');
 }
 function closeDialog(){if(!command)$c('cad-active-command').textContent='MODEL';clearTimeout(previewTimer);handleDrag=null;extrusion=null;dialog.close();dialogSpec=null;dialogPreview=null;dialogSubmit=null;previewSequence++;setPreview(null);}
 function alignmentDialog(){
  const alignment=model.alignment;
  if(!alignment)return window.AlignmentHub?.open();
  const profiles=objects().filter(o=>!o.faces.length&&o.vertices.length>=2);
  const options='<option value="design_circle">Alignment design circle</option>'+profiles.map(o=>`<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('');
  const output=()=>$c('cad-al-output').value;
  const trace=()=>['plan','3d'].includes(output());
  const read=()=>{
   const kind=output(),start=scalarValue('cad-al-start');
   if(trace())return {kind:'alignment_trace',projection:kind,start,end:scalarValue('cad-al-end')};
   return {kind,profile:$c('cad-al-profile').value,end_profile:kind==='alignment_loft'?$c('cad-al-end-profile').value||null:null,
    start,end:kind==='alignment_profile'?start:scalarValue('cad-al-end'),step:scalarValue('cad-al-step'),
    horizontal:scalarValue('cad-al-horizontal'),vertical:scalarValue('cad-al-vertical'),rotation:scalarValue('cad-al-rotation')};
  };
  const fields='<label>Create<select id="cad-al-output"><option value="alignment_loft">3D model between profiles</option><option value="alignment_profile">CAD profile at chainage</option><option value="plan">Plan centreline · editable copy</option><option value="3d">3D centreline · editable copy</option></select></label>'+
   scalarField('cad-al-start','Start chainage (m)',alignment.start)+
   '<div id="cad-al-end-wrap">'+scalarField('cad-al-end','End chainage (m)',Math.min(alignment.end,alignment.start+20))+'</div>'+
   '<div id="cad-al-profiles"><label>Start profile<select id="cad-al-profile">'+options+'</select></label>'+
   '<div id="cad-al-loft"><label>End profile / transition<select id="cad-al-end-profile"><option value="">Same as start</option>'+options+'</select></label>'+
   scalarField('cad-al-step','Profile spacing (m)',1)+'</div>'+
   scalarField('cad-al-horizontal','Horizontal offset (m)',0)+scalarField('cad-al-vertical','Vertical offset (m)',0)+scalarField('cad-al-rotation','Profile rotation (degrees)',0)+'</div>'+
   '<p id="cad-al-trace-help" class="tiny" hidden>CAD traces use the stored alignment segments. Plan places the copy at H = 0 for XY tools. 3D keeps the supplied heights.</p>';
  openDialog('Along alignment',alignment.name,fields,async()=>{
   if(await run({action:'create',spec:read(),...defaults()}))closeDialog();
  },{preview:()=>previewSpec(read()),applyLabel:'Create'});
  const update=()=>{
   $c('cad-al-end-wrap').hidden=output()==='alignment_profile';
   $c('cad-al-profiles').hidden=trace();
   $c('cad-al-loft').hidden=output()!=='alignment_loft';
   $c('cad-al-trace-help').hidden=!trace();
  };
  $c('cad-al-output').addEventListener('change',update);
  update();
  if(chosen().length===1&&profiles.some(o=>o.id===chosen()[0].id)){
   $c('cad-al-profile').value=chosen()[0].id;dialogPreview().catch(showDialogError);
  }
 }
 function primitiveDialog(kind,origin=null){const f=activeFrame(),base=origin||f.origin;const read=()=>({kind,origin:vectorValue('cad-primitive-origin'),frame:f,...(kind==='box'?{size:vectorValue('cad-primitive-size')}:{radius:scalarValue('cad-primitive-radius'),segments:96,...(kind==='cylinder'?{height:scalarValue('cad-primitive-height')}:{})})});
  openDialog(nameMap[kind],'Origin is in world E/N/H. Dimensions follow the current working axes.',vectorField('cad-primitive-origin','World origin (m)',base)+(kind==='box'?vectorField('cad-primitive-size','Size along working X / Y / Z (m)',[1,1,1]):scalarField('cad-primitive-radius','Radius (m)',1)+(kind==='cylinder'?scalarField('cad-primitive-height','Height (m)',2):'')),async()=>{if(await run({action:'create',spec:read(),...defaults()}))closeDialog();},{preview:()=>previewSpec(read()),pick:()=>beginPick(1,['Pick the solid origin.'],p=>primitiveDialog(kind,p[0]))});
 }
 function transformDialog(kind,base=null){const items=chosen();if(!items.length)throw Error('Select model geometry first.');const f=activeFrame(),pivot=base||f.origin;const ids=[...selected];
  const fields=vectorField('cad-transform-base','Base point / plane origin · world E/N/H (m)',pivot)+(kind==='rotate'?vectorField('cad-transform-axis','Rotation axis · working coordinates',[0,0,1])+scalarField('cad-transform-angle','Angle (degrees)',90):kind==='scale'?scalarField('cad-transform-factor','Uniform scale factor',1):vectorField('cad-transform-normal','Mirror plane normal · working coordinates',[1,0,0]));
  openDialog(kind[0].toUpperCase()+kind.slice(1)+' selection','The base point is explicit. Original scan coordinates are unaffected.',fields,async()=>{const body={action:kind,ids,base:vectorValue('cad-transform-base')};if(kind==='rotate')Object.assign(body,{axis:M.toWorld(vectorValue('cad-transform-axis'),f,true),angle:scalarValue('cad-transform-angle')});if(kind==='scale')body.factor=scalarValue('cad-transform-factor');if(kind==='mirror')body.normal=M.toWorld(vectorValue('cad-transform-normal'),f,true);if(await run(body))closeDialog();},{pick:()=>beginPick(1,['Pick the transform base point.'],p=>transformDialog(kind,p[0]))});
 }
 function moveCopy(kind){if(!selected.size)throw Error('Select model geometry first.');const ids=[...selected];start(kind,{count:2,prompt:n=>n?'Specify destination · @dX,dY,dZ enters a displacement.':'Specify the exact base point, by picking or typing.',callback:p=>run({action:kind,ids,base:p[0],vector:M.sub(p[1],p[0])})});}
 function cutDialog(point=null){if(selected.size!==1)throw Error('Select one solid or drawn profile to cut.');const ids=[...selected],f=workPlane();openDialog('Cut by a plane','Place the cutting plane anywhere in 3D. Both resulting parts are retained; the source is hidden.',vectorField('cad-cut-point','Point on plane · world E/N/H (m)',point||f.origin)+vectorField('cad-cut-normal','Plane normal · world direction',f.z),async()=>{if(await run({action:'cut',ids,point:vectorValue('cad-cut-point'),normal:vectorValue('cad-cut-normal'),both:true,...defaults()}))closeDialog();},{pick:()=>beginPick(3,['Pick the cutting plane origin.','Pick a point along its X direction.','Pick another point in the plane.'],p=>{const nf=M.frameFromPoints(...p);cutDialog(nf.origin);nf.z.forEach((v,i)=>$c('cad-cut-normal-'+i).value=v);})});}
 function frameDialog(picked=null){const f=picked||model.active_frame||M.worldFrame();openDialog('Working origin and axes','This changes coordinate entry and display. Existing scan and model positions stay fixed.',textField('cad-frame-name','Frame name',f.name)+vectorField('cad-frame-origin','Origin · world E/N/H (m)',f.origin)+vectorField('cad-frame-x','X-axis direction',f.x)+vectorField('cad-frame-y','Y-axis direction (Z is derived)',f.y)+'<label class="cad-check"><input type="checkbox" id="cad-frame-save" checked>Save this named frame with the project</label><label>Saved frame<select id="cad-frame-saved"><option value="">Choose…</option><option value="world">World</option>'+model.frames.map((v,i)=>`<option value="${i}">${esc(v.name)}</option>`).join('')+'</select></label>',async()=>{if(await run({action:'frame',frame:{name:$c('cad-frame-name').value,origin:vectorValue('cad-frame-origin'),x:vectorValue('cad-frame-x'),y:vectorValue('cad-frame-y')},save_named:$c('cad-frame-save').checked}))closeDialog();},{pick:()=>beginPick(3,['Pick the new origin.','Pick a point on the positive X axis.','Pick a point towards positive Y in the XY plane.'],p=>frameDialog(M.frameFromPoints(...p,'Picked frame')))});
  $c('cad-frame-saved').onchange=e=>{if(e.target.value==='')return;frameDialog(e.target.value==='world'?M.worldFrame():model.frames[Number(e.target.value)]);};
 }
 function parameters(){const o=chosen()[0];if(selected.size!==1||!o.definition)throw Error('Choose one object with construction parameters.');if(o.definition.kind==='tunnel_design')return window.TunnelDesigner.open(o.id);const d=o.definition,fields=[];const numeric=['radius','width','height','start_angle','end_angle','segments','start','end','step','horizontal','vertical','rotation'].filter(k=>d[k]!=null),vectors=['size','vector','centre','origin','normal','x_direction'].filter(k=>Array.isArray(d[k]));
  for(const k of numeric)fields.push(scalarField('cad-param-'+k,k.replaceAll('_',' '),d[k]));for(const k of vectors)fields.push(vectorField('cad-param-'+k,k+' · construction coordinates',d[k]));if(d.kind==='extrude')fields.push(`<label class="cad-check"><input id="cad-param-symmetric" type="checkbox" ${d.symmetric?'checked':''}>Symmetric total extrusion</label>`);
  if(!fields.length)throw Error('Use Edit vertex for this point or profile.');openDialog('Construction parameters',o.name+' · saved transforms are retained.',fields.join(''),async()=>{const values={};for(const k of numeric)values[k]=scalarValue('cad-param-'+k);for(const k of vectors)values[k]=vectorValue('cad-param-'+k);if(d.kind==='extrude')values.symmetric=$c('cad-param-symmetric').checked;if(await run({action:'parameters',ids:[o.id],values}))closeDialog();});
 }
 function vertexDialog(){const o=chosen()[0];if(selected.size!==1||o.faces.length)throw Error('Select one point or drawn profile.');openDialog('Edit vertex','Coordinates are exact world E/N/H. Editing a circle vertex converts it to a polyline.','<label>Vertex<select id="cad-vertex-index">'+o.vertices.map((_,i)=>`<option value="${i}">${i+1}</option>`).join('')+'</select></label>'+vectorField('cad-vertex-point','World coordinates (m)',o.vertices[0]),async()=>{if(await run({action:'vertex',ids:[o.id],index:Number($c('cad-vertex-index').value),point:vectorValue('cad-vertex-point')}))closeDialog();});$c('cad-vertex-index').onchange=e=>o.vertices[Number(e.target.value)].forEach((v,i)=>$c('cad-vertex-point-'+i).value=v);}
 function renderProperties(){const list=chosen();if(!list.length){panel.innerHTML='<h3>Model properties</h3><p>Select a model in the scene or Project explorer. Ctrl-click selects several; selection order is used for Loft and Subtract.</p><button id="cad-panel-project">Show project</button><h4>Layers</h4>'+model.layers.map((l,i)=>`<label class="cad-layer"><input type="checkbox" data-cad-layer="${i}" ${l.visible?'checked':''}> ${esc(l.name)}</label>`).join('');$c('cad-panel-project').onclick=()=>setPanel(false);panel.querySelectorAll('[data-cad-layer]').forEach(b=>b.onchange=()=>safeRun({action:'layer',name:model.layers[Number(b.dataset.cadLayer)].name,visible:b.checked}));return;}
  const o=list[0],tunnelDesign=o.definition?.kind==='tunnel_design';panel.innerHTML=`<h3>${list.length===1?esc(o.name):list.length+' selected models'}</h3><p>${list.length===1?esc(nameMap[o.shape]||o.shape)+' · '+(o.solid?'Closed solid':o.faces.length?'Surface':o.closed?'Closed profile':'Geometry'):list.map(x=>esc(x.name)).join(' → ')}</p>${tunnelDesign?'<p class="cad-help"><b>Editable tunnel model.</b> Reopen its alignment, heights, profiles and sections, then update this same 3D model or create a separate copy.</p>':''}`+textField('cad-property-name','Name',o.name)+textField('cad-property-layer','Layer',o.layer)+`<label>Bundle<select id="cad-property-bundle">${([{id:'',name:'Project'},...(J().inventory().bundles||[])]).map(b=>`<option value="${esc(b.id)}" ${b.id===o.bundle_id?'selected':''}>${esc(b.name)}</option>`).join('')}</select></label><label>Colour<input id="cad-property-colour" type="color" value="${esc(o.colour)}"></label><dl><dt>Vertices</dt><dd>${o.vertices.length.toLocaleString()}</dd><dt>Triangles</dt><dd>${o.faces.length.toLocaleString()}</dd><dt>Surface area</dt><dd>${o.area_m2.toFixed(4)} m²</dd>${o.volume_m3!=null?'<dt>Volume</dt><dd>'+o.volume_m3.toFixed(4)+' m³</dd>':''}</dl><div class="cad-properties-actions"><button id="cad-property-apply" class="primary">Apply properties</button><button id="cad-property-params" ${tunnelDesign?'class="primary"':''}>${tunnelDesign?'Edit tunnel design':'Parameters'}</button>${!o.faces.length?'<button id="cad-property-vertex">Edit vertex</button>':''}<button id="cad-property-move">Move</button><button id="cad-property-fit">Fit selection</button><button id="cad-property-export">Export highlighted</button><button id="cad-property-window">Window ↗</button></div><details><summary>World bounds / metadata</summary><pre>${esc(JSON.stringify({minimum:o.minimum,maximum:o.maximum,frame:'Project/world',units:'metres',id:o.id,construction:o.definition?.kind},null,2))}</pre></details>`;
  $c('cad-property-apply').onclick=()=>safeRun({action:'properties',ids:[...selected],...(list.length===1?{name:$c('cad-property-name').value}:{}),layer:$c('cad-property-layer').value,bundle_id:$c('cad-property-bundle').value,colour:$c('cad-property-colour').value});$c('cad-property-name').disabled=list.length!==1;
  $c('cad-property-params').onclick=()=>guard(parameters);$c('cad-property-params').disabled=list.length!==1||!o.definition;if($c('cad-property-vertex'))$c('cad-property-vertex').onclick=()=>guard(vertexDialog);$c('cad-property-move').onclick=()=>guard(()=>moveCopy('move'));$c('cad-property-fit').onclick=()=>fitSelected();$c('cad-property-export').onclick=()=>exportDialog();$c('cad-property-window').onclick=()=>window.DetachedUI?.detach({id:panel.id,element:panel,title:'Model properties'});
 }
 function properties(id){if(id&&!selected.has(id))select(id);if(mode!=='Model')enter('Model');setPanel(true);}
 function fitSelected(){const list=chosen().length?chosen():objects().filter(effectiveVisible);J().focusWorld(list.flatMap(boundsCorners));}
 async function remove(){if(!selected.size)return false;return J().removeSelected();}
 function menu(e,id){e.preventDefault();if(id){const face=faceAt(e,get(id));if(face)lastPickedFace=face;}if(id&&!selected.has(id))select(id);window.WorkspaceUI.showMenu(e,chosen()[0]?.name||'Model',[
  ['Properties',()=>properties()],...(chosen()[0]?.definition?.kind==='tunnel_design'?[['Edit tunnel design…',()=>guard(parameters)]]:[]),['Fit selection',fitSelected],['Export highlighted…',()=>exportDialog()],['Move to bundle…',()=>window.WorkspaceUI.moveSelection(null,[...selected])],null,['Extrude…',()=>guard(extrudeDialog)],['Move',()=>guard(()=>moveCopy('move'))],['Copy',()=>guard(()=>moveCopy('copy'))],['Rotate…',()=>guard(()=>transformDialog('rotate'))],['Cut by plane…',()=>guard(cutDialog)],['Construction parameters…',()=>guard(parameters)],['Edit vertex…',()=>guard(vertexDialog)],null,['Hide selected',()=>safeRun({action:'visibility',ids:[...selected],visible:false})],['Show all models',()=>safeRun({action:'visibility',ids:objects().map(o=>o.id),visible:true})],['Delete selected…',remove,true]
 ]);}
 async function exportDialog(requestedIds=null){const ids=Array.isArray(requestedIds)?requestedIds:[...selected];if(!ids.length){say('Select model objects to export.',true);return;}openDialog('Export highlighted models',ids.length+' selected objects · project/world coordinates · metres.','<label>Format<select id="cad-export-format"><option value="dxf">DXF · CAD geometry and 3D faces</option><option value="obj">OBJ · mesh, lines and points</option><option value="json">Model JSON · editable transfer to another project</option></select></label><p class="cad-help">Curved solid faces are tessellated. DXF preserves supported analytic circles/arcs. The exported file includes only these models. Choose Model JSON to import them into another Tunnel Studio project and keep construction parameters. DXF and OBJ exchange geometry with other 3D applications.</p>',async()=>{
   const fmt=$c('cad-export-format').value;let path=null;if(window.pywebview?.api?.choose_export_file){const choice=await window.pywebview.api.choose_export_file('Tunnel_Model.'+fmt,fmt);if(choice?.cancelled)return;if(choice?.error)throw Error(choice.error);if(!choice?.path)throw Error('No save destination selected.');path=choice.path;}
   const r=await post('/api/cad-export',{ids,format:fmt,path,revision:model.revision,context:model.context});if(!path){const a=document.createElement('a');a.href='/api/cad-download?token='+encodeURIComponent(r.token);a.download=r.filename;a.click();}log('Exported '+r.objects+' model objects · '+fmt.toUpperCase());closeDialog();say(path?'Saved '+path:'Model export downloaded.');
  },{applyLabel:'Save as…'});}
 function importDialog(path='',bundle=null){if(mode!=='Model'&&!enter('Model'))return;const target=bundle||defaults().bundle_id;openDialog('Import editable model','Add geometry to this project, then continue in Model space.',textField('cad-import-path','DXF or Tunnel Studio model JSON path on this PC',path)+'<button id="cad-import-browse">Browse…</button><label>Destination bundle<select id="cad-import-bundle">'+([{id:'',name:'Project'},...(J().inventory().bundles||[])]).map(b=>`<option value="${esc(b.id)}" ${b.id===target?'selected':''}>${esc(b.name)}</option>`).join('')+'</select></label><label>DXF source units<select id="cad-import-scale"><option value="1">Metres</option><option value="0.001">Millimetres</option><option value="0.01">Centimetres</option><option value="0.3048">Feet</option></select></label><p class="cad-help">Model JSON keeps exact world coordinates, layers and construction parameters; it is always in metres. DXF supports points, lines, polylines, circles, arcs and 3D faces. Unsupported entities are reported. Clouds and OBJ reference meshes use the main Import button. Turn on Cloud reference to see them in Model space.</p>',async()=>{const dest=$c('cad-import-bundle').value;if(await run({...defaults(),path:$c('cad-import-path').value,scale:Number($c('cad-import-scale').value),bundle_id:dest},'/api/cad-import')){const r=model.last_import;window.WorkspaceUI?.setActiveBundle?.(dest);closeDialog();say('Imported '+r.objects+' editable objects.'+(Object.keys(r.skipped).length?' Skipped: '+JSON.stringify(r.skipped):''));fitSelected();}},{applyLabel:'Import'});$c('cad-import-browse').onclick=async()=>{if(!window.pywebview?.api?.choose_large_file){showDialogError('Paste a local DXF or model JSON file path in browser mode.');return;}const paths=await window.pywebview.api.choose_large_file('cad',false);if(paths?.length)$c('cad-import-path').value=Array.isArray(paths)?paths[0]:paths;};}
 function setCloudSnap(enabled){
  snapSettings.cloud=enabled;$c('cad-cloud-snap').checked=enabled;
  if(enabled){$c('cad-cloud-reference').checked=true;$c('cad-snap').checked=true;}
  resetTracking();snapHit=null;J().draw();
 }
 function snapDialog(){openDialog('Snapping','Geometry snaps show a marker before placing a point. Cloud snapping recovers a measured source point on click.',Object.entries(snapSettings).map(([k,v])=>`<label class="cad-check"><input type="checkbox" data-cad-snap-setting="${k}" ${v?'checked':''}>${k==='cloud'?'Measured cloud point · on click':k[0].toUpperCase()+k.slice(1)}</label>`).join(''),()=>{dialog.querySelectorAll('[data-cad-snap-setting]').forEach(e=>{const key=e.dataset.cadSnapSetting;if(key==='cloud')setCloudSnap(e.checked);else snapSettings[key]=e.checked;});closeDialog();});}
 function help(){openDialog('Model command reference','Precise geometry in Model space. Enable Cloud reference to model against a scan.','<p><b>Coordinates:</b> X,Y,Z in the chosen frame; <b>@dX,dY,dZ</b> relative to the last point; <b>#E,N,H</b> absolute world coordinates. <b>@distance&lt;angle&lt;elevation</b> uses degrees.</p><p><b>Drawing:</b> POINT, LINE (L), POLYLINE (PL), CIRCLE (C), ARC (A), RECTANGLE (REC). Enter finishes a polyline; C closes it.</p><p><b>Midpoint tracking:</b> with Snap and Track midpoint enabled, pause over two opposite corners until each is numbered. Click the Centre marker, then choose a radius or type it. A common model face sets and locks the circle/arc/rectangle plane; the highlighted face and status show it before placement. Two standalone points alone do not define a plane: the current work plane is used when no shared face exists. Hover a third point to replace the oldest, or cancel to reset.</p><p><b>3D:</b> EXTRUDE (EX), SWEEP, LOFT, BOX, CYLINDER, SPHERE. Select profile then path for Sweep; select profiles in order for Loft.</p><p><b>Modify:</b> MOVE, COPY, ROTATE, SCALE, MIRROR, CUT, TRIM, EXTEND, OFFSET, BREAK, JOIN, ALIGN, UNION, SUBTRACT, INTERSECT.</p><p><b>Selection:</b> click models or their Project rows; Ctrl-click adds/removes. Select the subtraction base first. Trim uses the selected straight line as boundary and asks which portion to keep.</p><p><b>Navigation:</b> during a command, Shift/right-drag pans, Alt-drag orbits and the wheel zooms. Hold Ctrl while rotating for one-tenth speed. Escape cancels. Ctrl+Z / Ctrl+Y undo/redo outside text fields.</p><p><b>Origin / axes:</b> UCS changes the working frame without moving stored geometry. Faces and solids use a tessellated representation; curve detail is editable in Parameters.</p>',null);}
 async function tool(kind){if(window.TunnelDesigner?.isPicking()&&!['fit','tunnel_designer'].includes(kind))throw Error('Finish or cancel section picking first.');if(kind==='tunnel_designer')return window.TunnelDesigner.open();if(dialog.open)closeDialog();if(command){say('Finish or cancel the current command first.',true);return;}if(pending||busy)return;
  if(['point','line','polyline','circle','arc','rectangle'].includes(kind))return start(kind);
  if(mode!=='Model'&&!enter('Model'))return;
if(kind==='alignment_setup')return window.AlignmentHub?.open();if(kind==='alignment')return alignmentDialog();if(kind==='extrude')return extrudeDialog();if(['box','cylinder','sphere'].includes(kind))return primitiveDialog(kind);
  if(kind==='sweep'){const list=selectProfiles(2);return run({action:'create',spec:{kind,profile:list[0].id,path:list[1].id},...defaults()});}
  if(kind==='loft'){const list=selectProfiles();if(list.length<2)throw Error('Ctrl-select at least two profiles in loft order.');return run({action:'create',spec:{kind,profiles:list.map(o=>o.id)},...defaults()});}
  if(['move','copy'].includes(kind))return moveCopy(kind);if(['rotate','scale','mirror'].includes(kind))return transformDialog(kind);if(kind==='cut')return cutDialog();
  if(['union','subtract','intersect','join'].includes(kind))return run({action:kind,ids:[...selected],...defaults()});
  if(kind==='trim'){if(selected.size!==2)throw Error('Select the target first, then Ctrl-select a straight two-point boundary line in the same plane.');const ids=[...selected],f=workPlane();return start('trim',{count:1,prompt:()=> 'Pick or type a point on the portion to keep. The straight boundary is extended both ways.',callback:p=>run({action:kind,ids,keep:p[0],frame:f,...defaults()})});}
  if(kind==='extend'){if(selected.size!==2)throw Error('Select target first, then the straight boundary line.');const ids=[...selected],f=workPlane();return openDialog('Extend to boundary','The boundary is treated as an infinite straight line in the work plane.','<label>Endpoint<select id="cad-extend-end"><option value="end">Last vertex</option><option value="start">First vertex</option></select></label>',async()=>{if(await run({action:kind,ids,frame:f,end:$c('cad-extend-end').value,...defaults()}))closeDialog();});}
  if(kind==='break'){if(selected.size!==1)throw Error('Select one line or polyline.');const ids=[...selected];return start('break',{count:1,prompt:()=> 'Pick the break location on the selected curve.',callback:p=>run({action:kind,ids,point:p[0],...defaults()})});}
  if(kind==='offset'){if(selected.size!==1)throw Error('Select one profile.');return openDialog('Offset profile','Positive expands a closed profile; positive offsets an open curve to its left.',scalarField('cad-offset-distance','Distance (m)',.1),async()=>{if(await run({action:kind,ids:[...selected],distance:scalarValue('cad-offset-distance'),frame:workPlane(),...defaults()}))closeDialog();});}
  if(kind==='align'){if(!selected.size)throw Error('Select the geometry to align.');const ids=[...selected];return start('align',{count:6,prompt:n=>n<3?'Pick source reference '+(n+1)+' of 3.':'Pick matching destination reference '+(n-2)+' of 3.',callback:p=>run({action:'align',ids,from:p.slice(0,3),to:p.slice(3)})});}
  if(kind==='ucs')return frameDialog();if(kind==='fit')return fitSelected();if(kind==='export')return exportDialog();if(kind==='import')return importDialog();if(kind==='delete')return remove();if(kind==='undo')return window.HistoryUI.apply(false);if(kind==='redo')return window.HistoryUI.apply(true);if(kind==='help')return help();throw Error('Unknown command. Enter HELP for available commands.');
 }
 function guard(fn){try{Promise.resolve(fn()).catch(e=>say(e.message,true));}catch(e){say(e.message,true);}}
 function pixel(e){const r=canvas.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top];}
 canvas.addEventListener('pointerdown',e=>{stopHover();if(window.TunnelDesigner?.isPicking())return;if(mode!=='Model'||busy||pending)return;
  if(e.button===0&&!e.altKey&&!e.shiftKey&&extrusion?.profile&&!command){const h=extrudeHandle(),p=pixel(e),cap=extrusion.profile.vertices.map(q=>J().projectWorld(M.add(q,M.mul(extrusion.vector,extrusion.symmetric?.5:1)))),onFace=extrusion.profile.closed&&cap.every(Boolean)&&insidePolygon(p,cap);if(h&&(onFace||Math.hypot(p[0]-h.b[0],p[1]-h.b[1])<16)){const direction=$c('cad-ex-drag-direction').value,axis=direction==='normal'?extrusion.normal:extrusion.frame[direction];handleDrag={id:e.pointerId,start:p,vector:extrusion.vector.slice(),axis,base:h.base,end:h.end,normal:J().cadScene().basis().forward,symmetric:extrusion.symmetric};canvas.setPointerCapture(e.pointerId);e.preventDefault();e.stopImmediatePropagation();J().cadScene().cancelNavigation();return;}}

  gesture=e.button===0&&!e.shiftKey&&!e.altKey?{id:e.pointerId,start:pixel(e),moved:false}:null;if(command&&gesture)canvas.setPointerCapture(e.pointerId);},true);
 canvas.addEventListener('pointermove',e=>{if(mode!=='Model'||busy||pending)return;const p=pixel(e);if(handleDrag){e.preventDefault();e.stopImmediatePropagation();const drag=handleDrag;let vector;if(drag.axis){const a=J().projectWorld(drag.base),b=J().projectWorld(M.add(drag.base,drag.axis)),dx=b[0]-a[0],dy=b[1]-a[1],norm=dx*dx+dy*dy;let delta=norm>4?((p[0]-drag.start[0])*dx+(p[1]-drag.start[1])*dy)/norm:(drag.start[1]-p[1])*2*J().cadScene().scale/canvas.clientWidth;delta*=drag.symmetric?2:1;const length=M.dot(drag.vector,drag.axis)+delta;vector=M.mul(drag.axis,Math.abs(length)<1e-5?(length<0?-1e-5:1e-5):length);}else{const a=M.planePoint(drag.start,view(),{z:drag.normal,origin:drag.end},drag.end),b=M.planePoint(p,view(),{z:drag.normal,origin:drag.end},drag.end);if(a&&b)vector=M.add(drag.vector,M.mul(M.sub(b,a),drag.symmetric?2:1));}if(vector)updateDraggedExtrusion(vector);return;}if(gesture&&M.norm([p[0]-gesture.start[0],p[1]-gesture.start[1],0])>4)gesture.moved=true;if(!command||e.buttons||e.altKey||e.shiftKey){stopHover();return;}
  cursor=currentPoint(p);trackHover(p);if(cursor){$c('cad-coordinate-readout').textContent=M.toLocal(cursor,activeFrame()).map(v=>v.toFixed(3)).join(', ');if(!command.callback)setPreview(simplePreview(drawingSpec(cursor)));else J().draw();}else{snapHit=null;J().draw();}
 },true);
 canvas.addEventListener('pointerup',e=>{if(window.TunnelDesigner?.isPicking()){gesture=null;return;}if(handleDrag){handleDrag=null;gesture=null;e.stopImmediatePropagation();J().cadScene().cancelNavigation();if(canvas.hasPointerCapture?.(e.pointerId))canvas.releasePointerCapture(e.pointerId);dialogPreview?.().catch(showDialogError);return;}const g=gesture;gesture=null;if(mode!=='Model'||busy||pending||!g||g.id!==e.pointerId||g.moved||e.button!==0||e.altKey||e.shiftKey)return;const p=pixel(e);e.stopImmediatePropagation();J().cadScene().cancelNavigation();if(command?.kind==='extrude-face'){const wanted=command.sourceId?get(command.sourceId):null;let picked=faceAt(e,wanted);if(!picked){const o=pick(e)||objects().filter(v=>!v.faces.length&&v.closed&&effectiveVisible(v)).find(v=>{const pp=v.vertices.map(J().projectWorld);return pp.every(Boolean)&&insidePolygon(p,pp);});if(o&&(!wanted||o.id===wanted.id))picked={object:o};}if(picked){command=null;J().setMode('orbit');selected=new Set([picked.object.id]);guard(()=>extrudeDialog(null,picked));}return;}if(command)guard(()=>clickPoint(p));else{lastPickedFace=faceAt(e);const o=pick(e);if(o)select(o.id,e);else{clearSelected();if($c('cad-cloud-reference').checked&&window.SceneObjectsUI?.pickMeasurement(p,J().projectWorld,e))return;const item=$c('cad-cloud-reference').checked?J().pickedItem?.(e):null;if(item)J().activateItem(item);}}},true);
 canvas.addEventListener('contextmenu',e=>{if(window.TunnelDesigner?.isPicking())return;if(mode!=='Model')return;if(J().cadScene().suppressContextMenu())return;e.preventDefault();e.stopImmediatePropagation();if(command)return window.WorkspaceUI.showMenu(e,'Active command',[['Finish',()=>guard(()=>finish())],['Cancel',()=>cancel()]]);const o=pick(e);if(o)return menu(e,o.id);if(window.WorkspaceUI.viewportMenu)return window.WorkspaceUI.viewportMenu(e);window.WorkspaceUI.showMenu(e,'Model workspace',[['Fit models',fitSelected],['Working origin / axes',frameDialog],['Command help',help]]);},true);
 for(const type of ['pointercancel','lostpointercapture'])canvas.addEventListener(type,()=>{gesture=null;handleDrag=null;},true);
 canvas.addEventListener('pointerleave',()=>{stopHover();if(command?.profileEdit){cursor=null;snapHit=null;J().draw();}});window.addEventListener('blur',stopHover);
 canvas.addEventListener('wheel',()=>{stopHover();if(gesture)gesture.moved=true;},{passive:true,capture:true});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&dialog.open){e.preventDefault();e.stopImmediatePropagation();closeDialog();say('Preview cancelled. Choose another tool.');return;}if(mode!=='Model')return;if(e.key==='Escape'&&command){e.preventDefault();e.stopImmediatePropagation();cancel();return;}if(e.target.closest?.('input,textarea,select')||dialog.open)return;
  if(command&&['Enter','Backspace'].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();if(e.key==='Enter')guard(()=>finish());else{command.points.pop();setPreview(simplePreview(drawingSpec()));promptCommand();}return;}
  if(e.key==='Delete'&&selected.size&&!document.querySelector('dialog[open]')){e.preventDefault();e.stopImmediatePropagation();remove();}
 },true);
 modebar.querySelectorAll('[data-cad-mode]').forEach(b=>b.onclick=()=>enter(b.dataset.cadMode));toolbar.querySelectorAll('[data-cad-command]').forEach(b=>b.onclick=()=>guard(()=>tool(b.dataset.cadCommand)));
 for(const [id,title,items] of [['cad-solid-tool','Create a solid',[['box','Box'],['cylinder','Cylinder'],['sphere','Sphere']]],['cad-modify-tool','Modify model',[['copy','Copy'],['rotate','Rotate'],['scale','Scale'],['mirror','Mirror'],['offset','Offset'],['extend','Extend'],['break','Break'],['join','Join'],['align','Align 3 points'],['union','Union solids'],['subtract','Subtract solids'],['intersect','Intersect solids']]]])$c(id).onclick=()=>{const r=$c(id).getBoundingClientRect();window.WorkspaceUI.showMenu({clientX:r.left,clientY:r.bottom},title,items.map(([kind,label])=>[label,()=>guard(()=>tool(kind))]));};
 $c('cad-destination').onchange=e=>window.WorkspaceUI?.setActiveBundle(e.target.value);
 $c('cad-workspace-import').onclick=()=>window.TunnelRibbon?.openImport('scan');
 $c('cad-workspace-export').onclick=e=>window.WorkspaceUI?.exportMenu(e);
 for(const [id,key,label] of [['cad-finish','check','Finish command (Enter)'],['cad-cancel','close','Cancel command (Escape)'],['cad-command-enter','enter','Submit command (Enter)'],['cad-snap-options','target','Snap settings'],['cad-history-toggle','history','Command history'],['cad-help','help','Model command help']])window.ToolIcons.paint($c(id),key,label,({'cad-finish':'Finish','cad-cancel':'Cancel','cad-snap-options':'Snap settings','cad-history-toggle':'History','cad-help':'Help'})[id]);
 document.querySelectorAll('[data-job-nav]').forEach(b=>window.ToolIcons.paint(b,({iso:'cube'})[b.dataset.jobNav]||b.dataset.jobNav,b.title||b.textContent,({iso:'Iso',fit:'Fit',top:'Plan',front:'Front',right:'Side',orbit:'Orbit',pan:'Pan'})[b.dataset.jobNav]));
 tabs.querySelectorAll('button').forEach(b=>b.onclick=()=>setPanel(b.dataset.cadTab==='properties'));
 $c('cad-frame-button').onclick=()=>guard(()=>{if(mode!=='Model')enter('Model');frameDialog();});$c('cad-command-enter').onclick=()=>guard(input);$c('cad-finish').onclick=()=>guard(()=>finish());$c('cad-cancel').onclick=()=>cancel();
 const shortCommandWindow=matchMedia('(max-height:650px)');let commandBarChoice=null;
 function setCommandBarCollapsed(collapsed){const toggle=$c('cad-console-toggle');consoleBar.classList.toggle('collapsed',collapsed);toggle.setAttribute('aria-expanded',String(!collapsed));toggle.textContent=collapsed?'Show commands ▴':'Hide commands ▾';toggle.title=collapsed?'Expand the command bar to enter coordinates and change drawing options':'Collapse the command bar to make more room for the 3D view';toggle.setAttribute('aria-label',collapsed?'Show command bar':'Hide command bar');J().draw();window.ExplorerUI?.refresh();}
 const fitCommandBar=()=>{if(commandBarChoice===null)setCommandBarCollapsed(shortCommandWindow.matches);};
 $c('cad-console-toggle').onclick=()=>{commandBarChoice=!consoleBar.classList.contains('collapsed');setCommandBarCollapsed(commandBarChoice);};
 shortCommandWindow.addEventListener('change',fitCommandBar);fitCommandBar();
 planeLock.onclick=()=>{
  if(planeLock.getAttribute('aria-pressed')==='true')releaseViewLock();
  else{
   const f=drawingPlane();
   J().faceWorkPlane(f.z);planeLock.setAttribute('aria-pressed','true');
  }
  snapHit=null;J().draw();
 };
 $c('cad-cloud-snap').onchange=e=>setCloudSnap(e.target.checked);
 $c('cad-cloud-reference').onchange=()=>{if(!$c('cad-cloud-reference').checked)setCloudSnap(false);stopHover();J().draw();say($c('cad-cloud-reference').checked?'Cloud reference visible in Model space.':'Model space · clouds and reference meshes hidden.');};
 for(const id of ['cad-track','cad-snap'])$c(id).onchange=()=>{if(id==='cad-snap'&&!$c(id).checked)setCloudSnap(false);resetTracking();snapHit=null;J().draw();};
 $c('cad-history-toggle').onclick=()=>$c('cad-history').hidden=!$c('cad-history').hidden;$c('cad-snap-options').onclick=snapDialog;$c('cad-help').onclick=help;
 $c('cad-command-input').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();e.stopPropagation();guard(input);}if(e.key==='Escape'){e.preventDefault();cancel();}if(e.key==='ArrowUp'&&!command){e.preventDefault();e.target.value=lastCommand;}};
 for(const id of ['cad-space','cad-plane'])$c(id).onchange=()=>{if(command){$c(id).value=command[id==='cad-space'?'space':'plane']|| (id==='cad-space'?'local':'XY');say('Finish or cancel the command before changing its coordinate frame.',true);}else{snapHit=null;J().draw();}};
 $c('cad-dialog-close').onclick=closeDialog;$c('cad-dialog-preview').onclick=()=>dialogPreview?.().catch(showDialogError);$c('cad-dialog-apply').onclick=async()=>{const b=$c('cad-dialog-apply');b.disabled=true;try{await dialogSubmit?.();}catch(e){showDialogError(e);}finally{b.disabled=false;}};
 dialog.addEventListener('cancel',e=>{e.preventDefault();closeDialog();});
 const dismiss=el('button','cad-dialog-dismiss',icon('close'));dismiss.type='button';dismiss.title='Close / cancel (Escape)';dismiss.setAttribute('aria-label','Close extrusion or tool settings');dismiss.onclick=closeDialog;$c('cad-dialog-title').after(dismiss);
 const titleBar=$c('cad-dialog-title');let dialogDrag=null;
 titleBar.style.cursor='move';titleBar.title='Drag to move this panel';
 titleBar.addEventListener('pointerdown',e=>{if(e.button!==0||dialog.classList.contains('detached-source'))return;const r=dialog.getBoundingClientRect();dialogDrag={x:e.clientX-r.left,y:e.clientY-r.top};titleBar.setPointerCapture(e.pointerId);e.preventDefault();});
 titleBar.addEventListener('pointermove',e=>{if(!dialogDrag)return;dialog.style.position='fixed';dialog.style.margin='0';dialog.style.left=Math.max(0,Math.min(innerWidth-dialog.offsetWidth,e.clientX-dialogDrag.x))+'px';dialog.style.top=Math.max(0,Math.min(innerHeight-50,e.clientY-dialogDrag.y))+'px';});
 for(const type of ['pointerup','pointercancel','lostpointercapture'])titleBar.addEventListener(type,()=>dialogDrag=null);
 window.CadUI={releaseViewLock,moveProfileNode,drawPolyline:options=>start('polyline',options),finishDrawing:()=>guard(()=>finish()),workPlane:()=>workPlane(),showCloudReference:(options={})=>{if(mode!=='Model'&&!enter('Model',options))return false;$c('cad-cloud-reference').checked=true;J().draw();return true;},modelSpace:()=>mode==='Model',cloudReference:()=>mode!=='Model'||$c('cad-cloud-reference').checked,visibleBounds:()=>mode==='Model'?objects().filter(effectiveVisible).flatMap(boundsCorners):[],enter,observe,load,beforeDraw,afterDraw,select,selectedIds:(options={})=>mode==='Model'||options.includeInactive?[...selected]:[],clearSelected,properties,pick,menu,exportDialog,importDialog,refreshDestination:frameLabel,renderTree,remove,tool,command:()=>command,model:()=>model,cancel,preview:()=>preview};
 load();
})();
