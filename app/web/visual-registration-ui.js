/* Manual visual alignment: fixed reference, moving component, reversible pose. */
'use strict';
(()=>{
 const el=id=>document.getElementById('va-'+id),copy=x=>JSON.parse(JSON.stringify(x));
 const I=()=>[[1,0,0],[0,1,0],[0,0,1]],mv=(R,p)=>R.map(row=>row.reduce((n,v,i)=>n+v*p[i],0)),add=(a,b)=>a.map((v,i)=>v+b[i]),sub=(a,b)=>a.map((v,i)=>v-b[i]),mul=(a,k)=>a.map(v=>v*k),dot=(a,b)=>a.reduce((n,v,i)=>n+v*b[i],0),mm=(A,B)=>A.map(row=>[0,1,2].map(j=>row.reduce((n,v,k)=>n+v*B[k][j],0)));
 const frames={top:{right:[1,0,0],up:[0,1,0],forward:[0,0,1]},side:{right:[1,0,0],up:[0,0,1],forward:[0,-1,0]},front:{right:[0,1,0],up:[0,0,1],forward:[1,0,0]}};
 let session=null,scans=[],R=I(),t=[0,0,0],pivot=[0,0,0],view='top',tool='move',undo=[],redo=[],loading=false,applying=false,refining=false,refinement=null,epoch=0,drag=null,frame=null,packed=null;
 const dialog=document.createElement('dialog');dialog.id='visual-registration-dialog';dialog.setAttribute('aria-labelledby','va-title');
 dialog.innerHTML=`<header><div><h2 id="va-title">Visual alignment</h2><p>Align the orange group to the blue reference. Original scan coordinates are preserved.</p></div><button id="va-close" aria-label="Close visual alignment">×</button></header>
 <section class="va-pair"><label><span class="va-fixed-dot" aria-hidden="true">●</span> Fixed reference<select id="va-fixed"></select></label><button id="va-swap" title="Swap fixed and moving groups">⇄</button><label><span class="va-moving-dot" aria-hidden="true">●</span> Moving setup<select id="va-moving"></select></label><button id="va-load">Load pair</button></section>
 <nav class="va-toolbar" aria-label="Visual alignment controls"><div>${[['top','Top / plan'],['side','Side elevation · X/Z'],['front','Front elevation · Y/Z'],['3d','3D check']].map(([v,label])=>`<button data-va-view="${v}">${label}</button>`).join('')}</div><div><button data-va-tool="move">Move</button><button data-va-tool="rotate">Rotate</button><button data-va-tool="pivot">Set pivot</button><button id="va-fit">Fit pair</button><button id="va-undo">Undo move</button><button id="va-redo">Redo</button><button id="va-reset">Reset alignment</button></div></nav>
 <div class="va-body"><div class="va-viewport"><canvas id="va-canvas" aria-label="Fixed and moving scan alignment" tabindex="0"></canvas><div id="va-view-label"></div><div id="va-pivot-marker" hidden>⊕</div><div id="va-legend"><span class="va-fixed-dot" aria-hidden="true">● Fixed</span> <span class="va-moving-dot" aria-hidden="true">● Moving</span></div></div>
 <aside><label><input type="checkbox" id="va-tilt"> Correct tilt (unlock roll/pitch)</label><p class="va-note">Keep tilt locked to preserve the imported vertical direction.</p><details id="va-cloud-panel"><summary>Cloud-to-cloud refinement</summary><p class="va-note">Fits retained points from the whole groups; display crops are ignored. Enable Correct tilt for this six-axis refinement.</p><label>Sampling<select id="va-quality"><option value="fast">Fast</option><option value="balanced" selected>Balanced</option><option value="high">High</option></select></label><label>Plane RMS limit (mm)<input id="va-tolerance" type="number" min="0.01" step="1" value="5"></label><button id="va-refine">Refine cloud to cloud</button><button id="va-stop" hidden>Stop refinement</button><output id="va-quality-result" aria-live="polite"></output><p class="va-note">Local alignment only. Plane residuals are not survey accuracy; repetitive geometry can produce a wrong match.</p></details><h3>Align in this view</h3><p id="va-help"></p><label><input type="checkbox" id="va-height-only" checked> Height only in elevation</label><h3>Fine movement</h3><label>Step (mm)<input id="va-step" type="number" min="0.001" step="1" value="10"></label><div class="va-nudges"><button data-va-nudge="left" aria-label="Move left">←</button><button data-va-nudge="up" aria-label="Move up">↑</button><button data-va-nudge="down" aria-label="Move down">↓</button><button data-va-nudge="right" aria-label="Move right">→</button></div><label>Rotation step (degrees)<input id="va-angle" type="number" min="0.000001" step="0.1" value="0.1"></label><div class="va-nudges"><button data-va-turn="-1">Rotate −</button><button data-va-turn="1">Rotate +</button></div><output id="va-delta"></output><p id="va-group-info"></p><p class="va-note">Sampled view for manual alignment. Use plan and elevation for a rough initial alignment.</p>
</aside></div>
 <footer><p id="va-status" role="status" aria-live="polite">Choose the setup that must stay fixed.</p><button id="va-cancel">Cancel</button><button id="va-apply" class="primary">Join and lock</button></footer>`;
 document.body.append(dialog);
 const canvas=el('canvas');
 // Capture alignment gestures before CloudScene navigation. Right/Ctrl drag
 // still pans; left drag in 3D is navigation only, never an alignment edit.
 canvas.addEventListener('pointerdown',down,{capture:true});
 canvas.addEventListener('pointermove',move,{capture:true});
 for(const name of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(name,finish,{capture:true});
 class AlignmentScene extends CloudScene{
  basis(){return view==='3d'?super.basis():frames[view];}
  draw(){super.draw();drawPivot();}
 }
 const scene=new AlignmentScene(canvas);scene.canNavigate=()=>true;
 const status=(message,error=false)=>{el('status').textContent=message;el('status').classList.toggle('error',error);};
 const dirty=()=>session&&(Math.hypot(...t)>1e-12||R.some((row,i)=>row.some((v,j)=>Math.abs(v-(i===j?1:0))>1e-12)));
 function controls(){
  const blocked=loading||applying||refining,ready=!!session&&!blocked;
  dialog.querySelectorAll('button,input,select').forEach(x=>x.disabled=blocked);
  el('apply').disabled=!ready;el('refine').disabled=!ready;el('stop').hidden=!refining;el('stop').disabled=false;el('apply').textContent=refinement?'Join and lock · refined':'Join and lock';el('undo').disabled=!ready||!undo.length;el('redo').disabled=!ready||!redo.length;
  dialog.querySelectorAll('[data-va-tool],[data-va-nudge],[data-va-turn]').forEach(x=>x.disabled=!ready||view==='3d'||(x.dataset.vaTurn||x.dataset.vaTool==='rotate')&&view!=='top'&&!el('tilt').checked);
  dialog.querySelectorAll('[data-va-view]').forEach(x=>x.classList.toggle('chosen',x.dataset.vaView===view));
  dialog.querySelectorAll('[data-va-tool]').forEach(x=>x.classList.toggle('chosen',x.dataset.vaTool===tool));
  el('view-label').textContent=({top:'TOP / PLAN · X/Y',side:'SIDE ELEVATION · X/Z',front:'FRONT ELEVATION · Y/Z','3d':'3D CHECK · navigation only'})[view];
  el('help').textContent=view==='3d'?'Drag to orbit. Right-drag to pan. Return to an orthographic view to move the setup.':tool==='pivot'?'Click a matching feature to place the rotation pivot.':tool==='rotate'?'Drag horizontally to rotate around the marked pivot. Ctrl/right-drag pans the view.':'Left-drag moves the orange group. Shift-drag rotates around the pivot. Ctrl/right-drag pans; wheel zooms.';
  el('height-only').disabled=blocked||view==='top'||view==='3d';
 }
 function drawPivot(){
  const mark=el('pivot-marker');if(!mark)return;
  const p=session?scene.projectLocal(pivot):null;mark.hidden=!p||!dialog.open;
  if(p){mark.style.left=(p[0]+1)*canvas.clientWidth/2+'px';mark.style.top=(1-p[1])*canvas.clientHeight/2+'px';}
 }
 function render(){
  if(!session){scene.clear();return;}
  const fixed=session.clouds[0],moving=session.clouds[1],n=fixed.length+moving.length;
  if(!packed||packed.length!==n*6)packed=new Float32Array(n*6);
  let at=0;
  for(const [rows,active] of [[fixed,false],[moving,true]])for(const p of rows){const q=active?add(mv(R,p),t):p;packed.set([...q,...(active?[1,.5,.15]:[.12,.65,1])],at);at+=6;}
  scene.upload(scene.buffer,packed);scene.pointCount=n;scene.draw();
  const centre=session.movingCentre,delta=sub(add(mv(R,centre),t),centre);
  el('delta').textContent='Moving centre Δ (mm)\nX '+(delta[0]*1000).toFixed(3)+'\nY '+(delta[1]*1000).toFixed(3)+'\nZ '+(delta[2]*1000).toFixed(3);
  controls();
 }
 function schedule(){if(!frame)frame=requestAnimationFrame(()=>{frame=null;render();});}
 function fit(){
  if(!session)return;render();const lo=[Infinity,Infinity,Infinity],hi=lo.map(()=>-Infinity);
  for(let i=0;i<packed.length;i+=6)for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],packed[i+j]);hi[j]=Math.max(hi[j],packed[i+j]);}
  scene.target=lo.map((v,j)=>(v+hi[j])/2);const b=scene.basis(),aspect=canvas.clientWidth/Math.max(1,canvas.clientHeight);let extent=.1;
  for(let i=0;i<packed.length;i+=6){const p=[0,1,2].map(j=>packed[i+j]-scene.target[j]);extent=Math.max(extent,Math.abs(dot(p,b.right)),Math.abs(dot(p,b.up))*aspect);}
  scene.scale=extent*1.12;scene.draw();
 }
 const snapshot=()=>({R:copy(R),t:t.slice(),pivot:pivot.slice(),refinement:copy(refinement)});
 function qualityReport(){const q=refinement?.result;el('quality-result').textContent=q?'Sampled plane residuals\nRMS '+q.rms_mm.toFixed(3)+' mm · median '+q.median_mm.toFixed(3)+' mm · max '+q.max_mm.toFixed(3)+' mm\n'+q.inlier_count.toLocaleString()+' inliers · '+q.iterations+' iterations\n'+q.warnings.join('\n'):'No current cloud refinement. Manual edits require a new refinement.';}
 function restore(value){R=copy(value.R);t=value.t.slice();pivot=value.pivot.slice();refinement=copy(value.refinement||null);qualityReport();render();}
 function remember(value=snapshot()){refinement=null;qualityReport();undo.push(value);if(undo.length>100)undo.shift();redo=[];}
 function translate(delta){t=add(t,delta);pivot=add(pivot,delta);}
 function rotate(angle,axis){
  const c=Math.cos(angle),sn=Math.sin(angle),[x,y,z]=axis,Q=[[c+x*x*(1-c),x*y*(1-c)-z*sn,x*z*(1-c)+y*sn],[y*x*(1-c)+z*sn,c+y*y*(1-c),y*z*(1-c)-x*sn],[z*x*(1-c)-y*sn,z*y*(1-c)+x*sn,c+z*z*(1-c)]];
  R=mm(Q,R);t=add(pivot,mv(Q,sub(t,pivot)));
 }
 function localPixel(event){const box=canvas.getBoundingClientRect();return [event.clientX-box.left,event.clientY-box.top];}
 function down(event){
  if(!session||loading||applying||refining||view==='3d'||event.button!==0)return;
  event.preventDefault();event.stopImmediatePropagation();canvas.focus();scene.cancelNavigation();
  const p=localPixel(event),b=scene.basis();
  if(event.ctrlKey||event.metaKey){drag={id:event.pointerId,start:p,last:p,panning:true};canvas.setPointerCapture(event.pointerId);return;}
  if(tool==='pivot'){
   remember();const k=scene.scale*2/canvas.clientWidth,depth=dot(sub(pivot,scene.target),b.forward);
   pivot=add(add(scene.target,mul(b.forward,depth)),add(mul(b.right,(p[0]-canvas.clientWidth/2)*k),mul(b.up,(canvas.clientHeight/2-p[1])*k)));tool='rotate';render();return;
  }
  const rotating=tool==='rotate'||event.shiftKey;
  if(rotating&&view!=='top'&&!el('tilt').checked){status('Enable Correct tilt to rotate in elevation. Height movement keeps tilt locked.');return;}
  drag={id:event.pointerId,start:p,last:p,before:snapshot(),rotating,changed:false};canvas.setPointerCapture(event.pointerId);
 }
 function move(event){
  if(!drag||event.pointerId!==drag.id)return;event.preventDefault();event.stopImmediatePropagation();
  const p=localPixel(event),dx=p[0]-drag.last[0],dy=p[1]-drag.last[1];drag.last=p;
  if(drag.panning){const b=scene.basis(),k=scene.scale*2/canvas.clientWidth;scene.target=add(scene.target,add(mul(b.right,-dx*k),mul(b.up,dy*k)));scene.draw();return;}
  if(!drag.changed&&Math.hypot(p[0]-drag.start[0],p[1]-drag.start[1])<3)return;
  drag.changed=true;const b=scene.basis();
  if(drag.rotating)rotate(dx*.005,b.forward);
  else{const k=scene.scale*2/canvas.clientWidth;translate(view!=='top'&&el('height-only').checked?[0,0,-dy*k]:add(mul(b.right,dx*k),mul(b.up,-dy*k)));}
  schedule();
 }
 function finish(event){
  if(!drag||event.pointerId!==drag.id)return;const d=drag;drag=null;
  if(!d.panning){if(event.type==='pointercancel')restore(d.before);else if(d.changed)remember(d.before);}
  if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);render();
 }
 function changedPair(){epoch++;session=null;refinement=null;qualityReport();undo=[];redo=[];packed=null;scene.clear();controls();status('Pair changed. Load the pair before aligning.');}
 async function loadPair(){
  if(loading||applying||refining||busy)return;if(dirty()&&!confirm('Discard the unapplied alignment and reload this pair?'))return;
  const generation=++epoch;loading=true;session=null;scene.clear();controls();status('Loading bounded scan previews…');
  try{const value=await post('/api/scan-registration',{action:'visual_preview',fixed:el('fixed').value,moving:el('moving').value});if(generation!==epoch||!dialog.open)return;
   session=value;R=I();t=[0,0,0];undo=[];redo=[];refinement=null;qualityReport();
   session.movingCentre=session.clouds[1].reduce((a,p)=>add(a,mul(p,1/session.clouds[1].length)),[0,0,0]);pivot=session.movingCentre.slice();
   el('group-info').textContent=`Fixed: ${value.fixed_ids.length} setup(s). Moving together: ${value.moving_ids.length} setup(s). Display: ${value.clouds.map(p=>p.length.toLocaleString()).join(' + ')} sampled points.`;
   status(value.replaced_link?'This pair has an existing link. Join and lock will replace it with a manual alignment.':'Drag the moving group into alignment; check plan and elevation before joining.');fit();
  }catch(error){if(generation===epoch)status(error.message,true);}finally{loading=false;controls();}
 }
 async function open(options={}){
  if(busy||applying||refining)return;if(window.TunnelRibbon?.prepareTool?.('measure')===false)return;await window.TargetUI?.saveIfDirty();
  const network=await api('/api/scan-registration');scans=network.scans||[];
  const selected=window.JobUI.selectedItems().filter(id=>scans.some(s=>s.id===id));
  changedPair();for(const key of ['fixed','moving'])el(key).innerHTML='<option value="">Choose setup</option>'+scans.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
  el('fixed').value=options.fixed||selected[0]||'';el('moving').value=options.moving||selected[1]||'';view='top';tool='move';el('tilt').checked=false;el('height-only').checked=true;
  el('cloud-panel').open=!!options.cloud;dialog.showModal();controls();if(el('fixed').value&&el('moving').value)await loadPair();else status('Choose the fixed reference and moving setup, then Load pair.');
 }
 function close(){if(applying||refining)return;if(dirty()&&!confirm('Discard this unapplied visual alignment?'))return;epoch++;dialog.close();session=null;drag=null;scene.clear();}
 el('close').onclick=close;el('cancel').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
 el('fixed').onchange=changedPair;el('moving').onchange=changedPair;el('swap').onclick=()=>{if(dirty()&&!confirm('Discard the unapplied alignment and swap the two groups?'))return;const a=el('fixed').value;el('fixed').value=el('moving').value;el('moving').value=a;changedPair();};
 el('load').onclick=loadPair;el('fit').onclick=fit;
 dialog.querySelectorAll('[data-va-view]').forEach(b=>b.onclick=()=>{view=b.dataset.vaView;if(view==='3d'){scene.yaw=-.8;scene.pitch=.4;}controls();fit();});
 dialog.querySelectorAll('[data-va-tool]').forEach(b=>b.onclick=()=>{tool=b.dataset.vaTool;controls();});
 el('tilt').onchange=()=>{if(!el('tilt').checked&&!mv(R,[0,0,1]).every((v,i)=>Math.abs(v-(i===2?1:0))<1e-8)){el('tilt').checked=true;status('Undo the tilt moves or reset alignment before locking tilt again.');}controls();};
 function step(id){const v=Number(el(id).value);if(!Number.isFinite(v)||v<=0)throw Error('Enter a positive '+(id==='step'?'movement step in millimetres.':'angle in degrees.'));return v;}
 dialog.querySelectorAll('[data-va-nudge]').forEach(b=>b.onclick=()=>{try{const k=step('step')/1000,key=b.dataset.vaNudge,axes=scene.basis();if(view!=='top'&&el('height-only').checked&&['left','right'].includes(key)){status('Uncheck Height only to move horizontally in elevation.');return;}remember();translate(mul(['left','right'].includes(key)?axes.right:axes.up,k*(['left','down'].includes(key)?-1:1)));render();}catch(e){status(e.message,true);}});
 dialog.querySelectorAll('[data-va-turn]').forEach(b=>b.onclick=()=>{try{const angle=step('angle');remember();rotate(Number(b.dataset.vaTurn)*angle*Math.PI/180,scene.basis().forward);render();}catch(e){status(e.message,true);}});
 el('undo').onclick=()=>{if(undo.length){redo.push(snapshot());restore(undo.pop());}};el('redo').onclick=()=>{if(redo.length){undo.push(snapshot());restore(redo.pop());}};
 el('reset').onclick=()=>{if(!session)return;remember();R=I();t=[0,0,0];pivot=session.movingCentre.slice();render();};
 el('apply').onclick=async()=>{
  if(!session||loading||applying||refining||busy)return;applying=true;controls();status(refinement?'Saving reviewed cloud registration…':'Saving manual registration…');
  try{await post('/api/scan-registration',{action:'visual_apply',token:session.token,rotation:R,translation:t,correct_tilt:el('tilt').checked,refinement_id:refinement?.id});session=null;dialog.close();await window.JobUI.sync();await poll();await window.JobUI.loadPreview();notice((refinement?'Cloud-to-cloud refinement':'Manual alignment')+' saved. The blue reference stayed fixed; Workspace Undo restores the previous positions.');}
  catch(e){status(e.message,true);}finally{applying=false;controls();}
 };
 el('refine').onclick=async()=>{
  if(!session||loading||applying||refining||busy)return;
  el('cloud-panel').open=true;if(!el('tilt').checked){status('Enable Correct tilt: cloud-to-cloud refinement adjusts rotation in all axes.',true);return;}
  const tolerance=Number(el('tolerance').value);if(!Number.isFinite(tolerance)||tolerance<=0){status('Enter a positive plane RMS limit in millimetres.',true);return;}
  const before=snapshot(),generation=epoch;refining=true;controls();status('Sampling retained points for cloud-to-cloud refinement…');
  try{
   const started=await post('/api/scan-registration',{action:'cloud_refine',token:session.token,rotation:R,translation:t,correct_tilt:true,quality:el('quality').value,tolerance_mm:tolerance});
   await window.JobUI.waitReady(p=>{if(p.busy)status(p.message||'Refining clouds…');},{skipSync:true});
   if(generation!==epoch||!session)return;
   const out=await post('/api/scan-registration',{action:'cloud_status',token:session.token,refinement_id:started.refinement_id});
   if(out.state!=='ready'){status(out.reason||out.result?.reason||'Refinement did not converge; the manual draft is unchanged.',true);el('quality-result').textContent=(out.result?.warnings||[]).join('\n');return;}
   remember(before);pivot=add(mv(mm(out.rotation,[0,1,2].map(i=>R.map(row=>row[i]))),sub(pivot,t)),out.translation);R=out.rotation;t=out.translation;refinement=out;qualityReport();render();el('quality-result').scrollIntoView({block:'nearest'});
   status('Refinement ready. Review plan, elevation and the residuals, then Join and lock. No positions saved yet.');
  }catch(e){status(e.message,true);}finally{refining=false;controls();}
 };
 el('stop').onclick=async()=>{el('stop').disabled=true;status('Stopping refinement…');try{await post('/api/cancel-operation',{});}catch(e){status(e.message,true);el('stop').disabled=false;}};
 window.addEventListener('blur',()=>{if(drag){const d=drag;drag=null;if(!d.panning)restore(d.before);}});
 window.VisualRegistrationUI={open:options=>open(options).catch(e=>notice(e.message,true)),close};
})();

/* Saved scanner poses are reviewed separately from numerical cloud fitting. */
(()=>{
 'use strict';
 let review=null,working=false;
 const dialog=document.createElement('dialog');dialog.id='rtc360-vis-dialog';dialog.setAttribute('aria-labelledby','vis-title');
 dialog.innerHTML=`<header><h2 id="vis-title">RTC360 starting positions</h2><button id="vis-close" aria-label="Close RTC360 starting positions">×</button></header><p>Use saved scanner positions to place imported raw setups before refining their alignment. Existing registration links and independently changed poses are preserved.</p><p class="va-note">Scanner odometry can drift. These positions are not survey-grade control; verify against survey targets before measurement use.</p><label>Review scope<select id="vis-scope"><option value="all">All imported raw setups</option><option value="selected">Selected setups only</option></select></label><p>These are starting positions. Inspect overlapping surfaces in 3D, then refine with cloud-to-cloud or target registration.</p><p id="vis-status" role="status" aria-live="polite"></p><div class="vis-table"><table><thead><tr><th>Setup</th><th>Job</th><th>Position (m)</th><th>Review</th></tr></thead><tbody id="vis-rows"></tbody></table></div><footer><button id="vis-inspect">Inspect again</button><button id="vis-show">Show reviewed setups in 3D</button><button id="vis-refine">Refine cloud alignment…</button><button id="vis-download">Download inspection</button><button id="vis-cancel">Close</button><button id="vis-apply" class="primary">Apply initial poses</button></footer>`;
 document.body.append(dialog);
 const el=id=>document.getElementById('vis-'+id);
 const message=(text,error=false)=>{el('status').textContent=text;el('status').classList.toggle('error',error);};
 function controls(){dialog.querySelectorAll('button,select').forEach(b=>b.disabled=working);el('apply').disabled=working||!review?.eligible_count;el('download').disabled=working||!review?.rows?.length;el('show').disabled=working||!review?.rows?.length;el('refine').disabled=working||!refinePair();}
 function refinePair(){const rows=(review?.rows||[]).filter(r=>r.status==='positioned');for(const a of rows){const b=rows.find(x=>x.id!==a.id&&x.result.job_id===a.result.job_id);if(b)return [a.id,b.id];}return null;}
 async function readReview(){
  const ids=el('scope').value==='selected'?window.JobUI.selectedItems():[];
  review=await post('/api/scan-registration',{action:'vis_preview',scope:el('scope').value,scan_ids:ids});
  const labels={ready:'Ready to apply initial pose',positioned:'Already positioned · alignment still needs review',protected:'Registered position preserved',unavailable:'Unavailable'};
  el('rows').innerHTML=review.rows.map(row=>`<tr><td>${esc(row.name)}</td><td>${esc(row.result.job_name||'—')}</td><td>${row.result.t?row.result.t.map(v=>fmt(v,3)).join(' / '):'—'}</td><td>${esc(labels[row.status]||row.result.reason||'Unavailable')}${['protected','unavailable'].includes(row.status)?'<br>'+esc(row.result.reason||''):''}</td></tr>`).join('');
  const c=review.counts;message(`${c.ready} setup(s) ready to position · ${c.positioned} already positioned · ${c.protected} registered/protected · ${c.unavailable} unavailable. ${review.scope==='all'?'All imported raw setups reviewed.':'Selected setups reviewed.'}`);
 }
 async function inspect(){
  if(working||busy)return;working=true;review=null;controls();el('rows').replaceChildren();message('Reading saved positions from the original scanner folders…');
  try{await readReview();}catch(e){message(e.message,true);}finally{working=false;controls();}
 }
 function close(){if(working)return;dialog.close();review=null;}
 el('close').onclick=close;el('cancel').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});el('inspect').onclick=inspect;el('scope').onchange=inspect;
 el('show').onclick=async()=>{if(working||!review)return;working=true;controls();try{const out=await post('/api/scan-registration',{action:'vis_show',token:review.token});await window.JobUI.leaveSetup?.();window.JobUI.clearCleaningPreview?.();await window.JobUI.sync();await window.JobUI.loadPreview();window.JobUI.focusWorld(out.bounds);dialog.close();notice('Reviewed setups shown at their current positions. Check overlap before refining alignment.');}catch(e){message(e.message,true);}finally{working=false;controls();}};
 el('refine').onclick=async()=>{const pair=refinePair();if(!pair)return;dialog.close();await window.VisualRegistrationUI.open({cloud:true,fixed:pair[0],moving:pair[1]});};
 el('apply').onclick=async()=>{
  if(working||busy||!review?.eligible_count)return;working=true;controls();message('Applying reviewed initial poses…');
  try{
   const started=await post('/api/scan-registration',{action:'vis_apply',token:review.token});
   const job=await window.JobUI.waitReady(p=>{if(p.busy)message(p.message||'Applying initial poses…');},{skipSync:true});
   if(job?.cancelled)throw Error('Initial position application was cancelled. No poses were saved.');
   const saved=(await api('/api/scan-registration')).last_vis_pose_result;
   if(!saved?.applied||saved.id!==started.request_id)throw Error('Could not confirm this application. Inspect the positions again.');
   await window.JobUI.sync();await poll();await window.JobUI.loadPreview();
   const ids=new Set(saved.scan_ids);window.JobUI.focusWorld(window.JobUI.inventory().items.filter(x=>ids.has(x.id)&&x.minimum&&x.maximum).flatMap(x=>[x.minimum,x.maximum]));
   await readReview();message(`${saved.count} initial pose(s) saved. ${review.counts.positioned} already positioned. Check the 3D overlap, then refine the alignment. Workspace Undo restores the previous positions.`);
  }catch(e){message(e.message,true);}finally{working=false;controls();}
 };
 el('download').onclick=async()=>{
  if(!review||working)return;working=true;controls();message('Preparing metadata inspection report…');
  try{
   const reports=[];for(const row of review.rows)reports.push({scan:row.name,...await post('/api/scan-registration',{action:'vis_inspect',scan_id:row.id})});
   const url=URL.createObjectURL(new Blob([JSON.stringify({note:'Inspection only. No poses applied by this report.',reports},null,2)],{type:'application/json'}));
   const a=document.createElement('a');a.href=url;a.download='rtc360-position-inspection.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('Inspection report prepared.');
  }catch(e){message(e.message,true);}finally{working=false;controls();}
 };
 window.RTC360VisUI={open:async()=>{if(busy||working)return;try{if(window.TunnelRibbon?.prepareTool?.('measure')===false)return;await window.TargetUI?.saveIfDirty();dialog.showModal();await inspect();}catch(e){notice(e.message,true);}}};
})();
