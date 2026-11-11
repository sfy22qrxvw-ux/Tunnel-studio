/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 let selectedTargets=new Set(),targetSearching=false;
 let data={},rows=[],dirty=false,revision='',preview=null,pickArmed=false,renderKey='',lastProject=null,loading=false;
 class TargetScene extends CloudScene{
  setCloud(d){this.data=d;this.pointCount=d.points.length;const values=[];for(const p of d.points)values.push(...p.slice(0,3),p[3],p[3],p[3]);this.upload(this.buffer,values);this.markers();if(!this.initialised){this.initialised=true;this.reset();}else this.draw();}
  reset(){if(!this.data)return;const p=this.data.points,lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const q of p)for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],q[j]);hi[j]=Math.max(hi[j],q[j]);}this.target=lo.map((v,j)=>(v+hi[j])/2);this.yaw=-.55;this.pitch=.7;const basis=this.basis(),aspect=this.c.clientWidth/Math.max(1,this.c.clientHeight);let extent=.15;for(const q of p){const v=q.slice(0,3).map((x,j)=>x-this.target[j]),dot=a=>v.reduce((n,x,j)=>n+x*a[j],0);extent=Math.max(extent,Math.abs(dot(basis.right)),Math.abs(dot(basis.up))*aspect);}this.scale=extent*1.18;this.draw();}
  markers(){if(!this.data||!this.gl)return;const vals=[],origin=this.data.origin;this.labels=[];const positioned=$('reg-positioned').checked&&data.solution;const transformed=p=>positioned?data.solution.rotation.map((r,j)=>r.reduce((n,v,i)=>n+v*(p[i]-data.solution.source_centroid[i]),0)+data.solution.control_centroid[j]):p;
   const put=(id,p,c)=>{const q=p.map((v,j)=>v-origin[j]);const w=.02;for(let j=0;j<3;j++)for(const sign of [-1,1])vals.push(...q.map((v,k)=>v+(j===k?w*sign:0)),...c);this.labels.push({id,p:q});};
   for(const c of data.candidates||[])if(c.status!=='rejected')put(targetName(c),transformed(c.source),c.status==='accepted'?[.3,1,.65]:[1,.8,.2]);
   for(const row of rows)if(!data.candidates?.some(c=>c.id===row.candidate_id)&&row.source.every(v=>v!==null&&Number.isFinite(v)))put(row.id,transformed(row.source),[.3,.85,1]);
   this.upload(this.objectBuffer,vals);this.objectCount=vals.length/6;this.draw();}
  draw(){super.draw();if(!this.labels||!this.data||!$('reg-labels'))return;const b=this.basis(),aspect=this.c.clientWidth/Math.max(1,this.c.clientHeight),dot=(p,v)=>p.reduce((n,x,j)=>n+x*v[j],0);let labels='';for(const marker of this.labels){const q=marker.p.map((v,j)=>v-this.target[j]);const x=(dot(q,b.right)/this.scale+1)*this.c.clientWidth/2,y=(1-dot(q,b.up)*aspect/this.scale)*this.c.clientHeight/2;if(x>0&&x<this.c.clientWidth&&y>0&&y<this.c.clientHeight)labels+=`<span style="left:${x+7}px;top:${y-10}px">${esc(marker.id)}</span>`;}$('reg-labels').innerHTML=labels;}
 }
 const sceneReg=new TargetScene($('reg-canvas'));
 function targetName(c){return rows.find(r=>r.candidate_id===c.id)?.id||c.name||c.id;}
 function asNumber(text){return String(text).trim()===''?null:Number(text);}
 function values(){return [...$('reg-pairs').querySelectorAll('tr[data-row]')].map(tr=>{const fields=tr.querySelectorAll('input');return {id:fields[0].value.trim(),role:tr.querySelector('select').value,source:[...fields].slice(1,4).map(f=>asNumber(f.value)),control:[...fields].slice(4,7).map(f=>asNumber(f.value)),candidate_id:rows[+tr.dataset.row]?.candidate_id,method:rows[+tr.dataset.row]?.method||'Manual centre'};});}
 function invalidateSolution(){const wasPositioned=$('reg-positioned').checked;data.solution=null;data.transformed=false;$('reg-positioned').checked=false;renderKey='';if(wasPositioned)loadPreview();}
 function markDirty(){rows=values();dirty=true;invalidateSolution();quality();sceneReg.markers();}
 function pairTable(){const residuals=new Map((data.solution?.residuals||[]).map(r=>[r.id,r]));$('reg-pairs').innerHTML=rows.map((r,i)=>{const err=residuals.get(r.id);return `<tr data-row="${i}"><td><input aria-label="Target ID ${i+1}" value="${esc(r.id)}" maxlength="60"></td><td><select aria-label="Target role ${i+1}">${['fit','check','off'].map(role=>`<option ${r.role===role?'selected':''} value="${role}">${role==='off'?'Exclude':role==='fit'?'Fit':'Check'}</option>`).join('')}</select></td>${[...r.source,...r.control].map((v,j)=>`<td><input aria-label="${['Source X','Source Y','Source Z','Control E','Control N','Control H'][j]} ${i+1}" type="number" step="0.0001" value="${v===null||v===undefined?'':Number(v).toFixed(6)}"></td>`).join('')}<td class="${err&&!err.within_limit?'residual-bad':''}">${err?fmt(err.error_mm,3):'—'}</td><td><button data-remove="${i}" aria-label="Remove target ${i+1}">×</button></td></tr>`;}).join('')||'<tr><td colspan="10">Fit a target or add a manual centre, then name it to match other scans. Surveyed control coordinates are optional for scan matching.</td></tr>';
  $('reg-pairs').querySelectorAll('input,select').forEach(input=>input.addEventListener('input',markDirty));$('reg-pairs').querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{rows=values();const row=rows[+b.dataset.remove];if(!row)return;if(!row.candidate_id&&!row.source.every(v=>v!==null&&Number.isFinite(v))){rows.splice(+b.dataset.remove,1);dirty=true;invalidateSolution();pairTable();quality();sceneReg.markers();return;}selectTargets([row.candidate_id?'candidate:'+row.candidate_id:'row:'+row.id]);deleteSelectedTargets();});
 }
 function quality(){const sol=data.solution;const survey=$('reg-to-survey'),reason=data.survey_analysis_unavailable;survey.disabled=busy||!!reason;if(reason)survey.checked=false;$('reg-survey-help').textContent=reason||'Results tied to the previous position must be recalculated.';$('reg-rms').textContent=sol?fmt(sol.fit.rms_mm,3):'—';$('reg-max').textContent=sol?fmt(Math.max(sol.fit.max_mm,sol.check.max_mm||0),3):'—';$('reg-check').textContent=sol?.check.count?fmt(sol.check.rms_mm,3):'—';$('reg-quality').textContent=sol?[`${sol.fit.count} fit targets · ${sol.check.count} independent checks · scale 1.000000`,...sol.warnings].join('\n'):'Use at least 3 non-collinear fit targets, with all coordinates in metres. Save or solve after editing.';for(const id of ['reg-json','reg-residuals','reg-apply'])$(id).disabled=busy||!sol||dirty;$('reg-export').disabled=busy||!data.transformed||dirty;$('reg-apply-info').textContent=data.transformed?'Positioned scan ready for export.':'';showControlMatches();showGroupReview();window.TunnelRibbon?.sync();}
 function targetThumb(c){const t=c?.thumbnail;if(!t?.data_url?.startsWith('data:image/png;base64,'))return '<small>Preview available after a new search</small>';return `<img class="target-thumbnail" src="${esc(t.data_url)}" width="128" height="128" alt="Observed scan patch for ${esc(c.id)}; red cross marks ${t.manual?'picked point':'fitted centre'}" title="${esc(t.note||'Observed scan points')}"><small>${t.manual?'Setup view crop':fmt(t.points,0)+' patch points'} · ${esc(c.channel||'intensity')}<br>Red cross = ${t.manual?'manually picked point':'detected centre'}</small>`;}
 function candidateWarnings(c){return [...new Set([...(Array.isArray(c.quality_warnings)?c.quality_warnings:[]),...(Array.isArray(c.warnings)?c.warnings:[])])];}
 function candidates(){
  const list=data.candidates||[],container=$('reg-candidates');
  const expanded=new Set([...container.querySelectorAll('details[open]')].map(row=>row.dataset.targetDetails));
  $('reg-target-count').textContent='· '+list.length;
  container.innerHTML=list.map(c=>{
   const sphere=c.target_type==='sphere',warnings=candidateWarnings(c);
   const row=rows.find(r=>r.candidate_id===c.id),rms=sphere?c.sphere_rms_mm:c.plane_rms_mm;
   const status=c.status==='rejected'||row?.role==='off'?'Excluded':row?.role==='check'?'Check':c.status==='accepted'?'Included':'Found';
   const id=esc(c.id),name=esc(targetName(c));
   const type=sphere?'Sphere':c.manual?'Manual pick':'Black & white';
   const metrics=[['Scan XYZ (m)',c.source.map(v=>fmt(v,5)).join(' / ')],['Type',type]];
   if(Number.isFinite(c.score))metrics.push(['Fit score',fmt(c.score,3)]);
   if(sphere){
    metrics.push(['Nominal diameter',fmt(c.diameter_m*1000,1)+' mm'],['Support',fmt(c.support_points,0)+' points']);
    if(Number.isFinite(c.fitted_radius_m))metrics.push(['Free-fit diameter',fmt(c.fitted_radius_m*2000,1)+' mm']);
    if(Number.isFinite(c.radius_error_mm))metrics.push(['Radius error',fmt(c.radius_error_mm,3)+' mm']);
   }else if(c.validation_full_density){
    metrics.push(['Support',fmt(c.validation_points,0)+' points · 4 quadrants'],['Edge margin',fmt(c.edge_advantage,3)]);
   }
   return `<details class="reg-fitted-row" data-target-details="${id}" ${expanded.has(c.id)?'open':''}>
    <summary><b>${name}</b><small>${status}</small>${Number.isFinite(rms)?`<small title="Fit RMS">${fmt(rms,3)} mm</small>`:''}${warnings.length?'<small class="reg-fit-review">Review</small>':''}</summary>
    <div class="reg-fitted-details">
     <label>Target name<input data-target-name="${id}" aria-label="Target name" maxlength="60" value="${name}"></label>
     <div class="reg-fitted-actions"><button data-focus="${id}">Zoom to target</button><button data-refine="${id}">Refine</button><button data-delete-candidate="${id}" class="danger-lite">Delete</button></div>
     <dl>${metrics.map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('')}</dl>
     ${warnings.length?'<ul class="target-fit-warnings">'+warnings.map(w=>'<li>'+esc(w)+'</li>').join('')+'</ul>':''}
     ${c.thumbnail?.data_url?.startsWith('data:image/png;base64,')?targetThumb(c):''}
    </div></details>`;
  }).join('')||'<p class="reg-fitted-empty">No fitted targets yet. Use Pick &amp; fit centre.</p>';
  for(const input of $('reg-candidates').querySelectorAll('[data-target-name]'))input.onchange=async()=>{try{await saveIfDirty();await post('/api/registration-action',{action:'rename_target',id:input.dataset.targetName,name:input.value,source_id:data.source_id});await sync();}catch(e){notice(e.message,true);}};
  for(const b of $('reg-candidates').querySelectorAll('[data-focus]'))b.onclick=()=>{selectTargets(['candidate:'+b.dataset.focus]);focusTarget('candidate:'+b.dataset.focus);};
  for(const b of $('reg-candidates').querySelectorAll('[data-delete-candidate]'))b.onclick=()=>{selectTargets(['candidate:'+b.dataset.deleteCandidate]);deleteSelectedTargets();};
  for(const b of $('reg-candidates').querySelectorAll('[data-refine]'))b.onclick=()=>{const c=list.find(c=>c.id===b.dataset.refine);detect(c.source,{target_type:c.target_type||'bw_known',diameter:c.target_type==='sphere'?c.diameter_m:(c.diameter_m||null)});};
  window.JobUI?.targets(list,rows,data.source_item_id||data.source_id);renderManager();
 }
 async function saveIfDirty(){if(!dirty)return;rows=values();await post('/api/registration-action',{action:'rows',rows,source_id:data.source_id});dirty=false;data.solution=null;data.transformed=false;await sync();}
 async function loadPreview(){const canvas=$('reg-canvas');if(!data.cloud||busy||loading||!canvas.getClientRects().length||!canvas.clientWidth||!canvas.clientHeight)return false;loading=true;try{preview=await api('/api/reg-preview?'+new URLSearchParams({positioned:$('reg-positioned').checked?'1':'0',channel:$('reg-channel').value}));sceneReg.setCloud(preview);$('reg-empty').style.display='none';return true;}catch(e){notice(e.message,true);return false;}finally{loading=false;}}
 async function sync(options={}){if(busy)return;const next=await api('/api/registration');if(busy)return;const projectChanged=lastProject!==state?.project_id;lastProject=state?.project_id;const cloudChanged=next.source_id!==data.source_id||next.source_item_id!==data.source_item_id;if(projectChanged||cloudChanged){selectedTargets.clear();dirty=false;sceneReg.initialised=false;$('reg-positioned').checked=false;}
  if(dirty&&!projectChanged&&!cloudChanged){data.candidates=next.candidates;data.detection=next.detection;}else{data=next;rows=(data.rows||[]).map(r=>({...r}));pairTable();}
  if(targetSearching){targetSearching=false;window.WorkStatus?.show(state?.job?.error?'Target search failed':'Target search complete',state?.job?.error||data.detection?.message||'Target fitting finished.',false,!!state?.job?.error);}
  $('reg-source-info').textContent=data.cloud?`${data.cloud.name} · ${fmt(data.cloud.count,0)} points · ${data.cloud.attribute_counts?.[0]?'intensity available':'no intensity'} · ${data.cloud.attribute_counts?.[1]?'RGB available':'no RGB'}. Display is sampled; the original source stays unchanged.`:'Select a scan in the main workspace. Registration can use it directly; no alignment is required. Coordinates must be in metres.';$('reg-badge').textContent=data.demo?'SYNTHETIC TARGET DEMO':data.cloud?'SOURCE SCAN':'NO SOURCE SCAN';$('reg-detection-info').textContent=data.detection?.message||'Choose Sphere or B&W fitting above. Raw RTC360 imports keep full measured coordinates on disk; target fitting reads the original local points, not only the screen preview.';
  candidates();quality();const key=[data.revision,$('reg-positioned').checked,$('reg-channel').value].join('|');if(data.cloud&&(renderKey!==key||cloudChanged)){if(!options.skipPreview&&await loadPreview())renderKey=key;}else if(!data.cloud){sceneReg.clear();$('reg-empty').style.display='flex';$('reg-labels').innerHTML='';}sceneReg.markers();window.JobUI?.targets(data.candidates||[],rows,data.source_item_id||data.source_id);setBusyReg(busy);window.TunnelRibbon?.sync();
 }
 function showControlMatches(){
  const summary=data.control_matches,info=$('reg-control-matches'),button=$('reg-solve-group-control');
  const names=$('reg-control-matched-names');
  info.hidden=!summary;button.hidden=!summary;
  names.hidden=!summary?.matches?.length;
  $('reg-control-matched-list').innerHTML=(summary?.matches||[]).map(m=>
   `<li><b>${esc(m.id)}</b> · ${m.role==='check'?'Check':'Fit'} · ${esc(m.setups.join(', '))}</li>`).join('');
  if(!summary)return;
  button.disabled=busy||summary.fit<3;
  info.textContent=`${summary.anchor_name} · ${summary.setups} setup(s) · Matched ${summary.fit+summary.check} of ${summary.controls} control IDs by name. `+
   (summary.unmatched.length?`Unmatched control IDs: ${summary.unmatched.slice(0,8).join(', ')}${summary.unmatched.length>8?' …':''}. `:'')+
   (summary.fit<3?'Match at least three non-collinear control IDs to measured targets.':'Ready to calculate and review the survey position.');
 }
 function showGroupReview(){
  const solution=data.solution,group=solution?.control_group,review=$('reg-group-review');
  review.hidden=!group;
  if(!group){review.replaceChildren();return;}
  review.innerHTML=`<p><b>${group.setups} linked setup(s)</b> · ${group.fit} Fit · ${group.check} Check. Each matched control ID is used once.</p>
   <details><summary>Target residuals</summary><ul>${solution.residuals.map(r=>`<li>${esc(r.id)} · ${r.role==='check'?'Check':'Fit'} · ${fmt(r.error_mm,3)} mm${r.within_limit?'':' · over review limit'}</li>`).join('')}</ul></details>`;
 }
 async function action(body){try{await saveIfDirty();setBusy(true);await post('/api/registration-action',body);poll();}catch(e){setBusy(false);if(targetSearching){targetSearching=false;window.WorkStatus?.show('Target search failed',e.message,false,true);}notice(e.message,true);}}
 function targetSettings(){const type=$('reg-target-type').value;let diameter=null;if(type!=='bw_auto'){const mm=+$('reg-target-size').value;if(!Number.isFinite(mm)||mm<30||mm>2000)throw Error('Enter the physical target diameter / printed size in millimetres.');diameter=mm/1000;}return {target_type:type,diameter};}
 function targetMethodUI(){const type=$('reg-target-type').value,sphere=type==='sphere',known=type!=='bw_auto';$('reg-target-size-label').hidden=!known;if(sphere&&(!$('reg-target-size').dataset.userEdited||$('reg-target-size').dataset.lastType!=='sphere'))$('reg-target-size').value='198.8';if(type==='bw_known'&&$('reg-target-size').dataset.lastType!=='bw_known')$('reg-target-size').value='152.4';$('reg-target-size').dataset.lastType=type;$('reg-detect').textContent=sphere?'Sphere needs a pick':'Find target centres';$('reg-detect').disabled=busy||sphere;$('reg-channel').disabled=busy||sphere;$('reg-intensity-view').disabled=busy||sphere;$('reg-target-method-help').textContent=sphere?'Known-diameter sphere fitting: pick a measured point on the sphere. The centre is refined from local 3D curvature; the nominal diameter stays fixed.':type==='bw_known'?'Known-size B&W fitting: use the printed target width/diameter to reduce false detections, then include each fitted centre automatically. RMS and fit warnings remain visible.':'Automatic B&W search: several physical scales are tested using intensity / RGB. Found centres are included automatically. Inspect their fit errors and delete any unwanted targets.';$('reg-pick-info').textContent=sphere?'Click a visible measured point on the sphere in the MAIN 3D VIEW. Tunnel Studio will fit the entered sphere diameter to full-density local points.':'Click near the printed target centre in the MAIN 3D VIEW. Tunnel Studio will refine the B&W crossing from full-density local data.';}
 async function detect(seed=null,override=null,seedFrame='stored'){if(busy)return;if(window.JobUI?.selectedItem?.()?.kind==='scan'&&data.source_item_id!==window.JobUI.selectedItem().id){if(!await window.JobUI.ensureRegistrationSource())return;await sync();}if(!data.cloud){notice('Select a scan for target detection first.',true);return;}let settings;try{settings=override||targetSettings();}catch(e){notice(e.message,true);return;}if(settings.target_type==='sphere'&&!seed){notice('Sphere fitting needs an approximate pick. Click Pick & fit centre, then click on the sphere.',true);return;}targetSearching=true;window.WorkStatus?.show('Finding target centre…',settings.target_type==='sphere'?'Fitting the known sphere diameter to full-density local geometry':'Checking target contrast and geometry',true);await action({action:'detect',channel:$('reg-channel').value,seed,seed_frame:seedFrame,...settings});}
 async function waitRegistrationReady(){while(true){const st=await api(jobStatusURL());updateJobSnapshot(st);if(!st.job.busy){setBusy(false);if(st.job.error)throw Error(st.job.error);await poll();return;}setBusyReg(true);$('reg-source-info').textContent=st.job.message||'Importing registration scan…';await new Promise(r=>setTimeout(r,600));}}
 $('reg-browse').onclick=async()=>{if(data.cloud&&!confirm('Replace the registration source and target work? Save the project first if you need to keep it.'))return;try{await saveIfDirty();const bridge=window.pywebview?.api;if(!bridge?.choose_large_file)throw Error('The Windows file picker is unavailable in browser fallback mode.');const paths=await bridge.choose_large_file('registration',false);if(!paths?.length)return;setBusy(true);dirty=false;renderKey='';await post('/api/load',{kind:'registration',path:paths[0]});await waitRegistrationReady();}catch(err){setBusy(false);notice(err.message,true);}};
 $('reg-import').onclick=()=>$('reg-file').click();$('reg-file').onchange=async e=>{const f=e.target.files[0];if(!f)return;if(f.size>150000000){e.target.value='';showTunnelMessage('Use Browse source scan','This file is large. Use Browse source scan so Tunnel Studio reads it directly from disk without transferring it through the WebView.',true,'Browse source scan',()=>$('reg-browse').click());return;}if(data.cloud&&!confirm('Replace the registration source and target work? Save the project first if you need to keep it.'))return;try{await saveIfDirty();setBusy(true);await api('/api/upload?'+new URLSearchParams({kind:'registration',name:f.name}),{method:'POST',headers:{'X-Tunnel-Token':window.TUNNEL_TOKEN},body:f});dirty=false;renderKey='';poll();}catch(err){setBusy(false);notice(err.message,true);}finally{e.target.value='';}};
 for(const id of ['reg-contrast','reg-gamma'])$(id).oninput=()=>{window.JobUI?.setDisplayEnhancement(+$('reg-contrast').value,+$('reg-gamma').value);sceneReg.pointContrast=+$('reg-contrast').value;sceneReg.pointGamma=+$('reg-gamma').value;sceneReg.draw();};
 $('reg-reset-contrast').onclick=()=>{$('reg-contrast').value='1';$('reg-gamma').value='1';$('reg-contrast').oninput();};
 $('reg-intensity-view').onclick=()=>{window.JobUI?.setDisplayMode('intensity-gray');window.JobUI?.loadViewDetail?.(true);};
 $('reg-demo').onclick=()=>{if(data.cloud&&!confirm('Replace the registration workspace with synthetic target data? Save first to keep the current targets.'))return;action({action:'demo'});};$('reg-detect').onclick=()=>detect();$('reg-fit-seed').onclick=()=>{const v=['reg-x','reg-y','reg-z'].map(id=>asNumber($(id).value));if(v.some(v=>v===null||!Number.isFinite(v)))notice('Enter X, Y and Z for the approximate target centre.',true);else detect(v);};
 $('reg-pick').onclick=()=>{
  if(window.JobUI?.setupCamera?.()){window.SetupCameraUI?.pickTarget();return;}
  if(!data.cloud){notice('Choose a scan first. Registration can use the scan already selected in the main workspace.',true);return;}
  const source=data.source_item_id;
  $('reg-positioned').checked=false;
  window.JobUI?.armPointPick?.(async (seed,sample)=>{
   if(source&&sample?.source_id!==source){notice('Pick a measured point on the active scan.');return;}
   ['reg-x','reg-y','reg-z'].forEach((id,i)=>$(id).value=seed[i].toFixed(6));
   await detect(seed,null,'world');
  },{repeat:true,prompt:'Click targets to fit · Click a name to rename'});
 };
 let down=null;$('reg-canvas').addEventListener('pointerdown',e=>down=[e.clientX,e.clientY]);$('reg-canvas').addEventListener('pointerup',e=>{if(!pickArmed||busy||!preview||!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;const box=$('reg-canvas').getBoundingClientRect(),x=e.clientX-box.left,y=e.clientY-box.top,b=sceneReg.basis();let best=null,dist=144;for(const p of preview.points){const v=p.slice(0,3).map((n,i)=>n-sceneReg.target[i]),dot=a=>v.reduce((n,x,i)=>n+x*a[i],0),px=(dot(b.right)/sceneReg.scale+1)*box.width/2,py=(1-dot(b.up)*box.width/box.height/sceneReg.scale)*box.height/2,d=(px-x)**2+(py-y)**2;if(d<dist){best=p;dist=d;}}if(!best){notice('No displayed point close to the cursor. Zoom closer and try again.',true);return;}pickArmed=false;$('reg-pick').classList.remove('chosen');const seed=best.slice(0,3).map((v,i)=>v+preview.origin[i]);['reg-x','reg-y','reg-z'].forEach((id,i)=>$(id).value=seed[i].toFixed(6));detect(seed);});
 $('reg-target-type').onchange=targetMethodUI;$('reg-target-size').oninput=()=>{$('reg-target-size').dataset.userEdited='1';};targetMethodUI();
 $('reg-fit-view').onclick=()=>sceneReg.reset();$('reg-top').onclick=()=>{sceneReg.pitch=Math.PI/2;sceneReg.yaw=0;sceneReg.draw();};$('reg-front').onclick=()=>{sceneReg.pitch=0;sceneReg.yaw=0;sceneReg.draw();};$('reg-channel').onchange=()=>{loadPreview();window.JobUI?.setDisplayMode?.($('reg-channel').value==='rgb'?'rgb':'intensity-gray');};$('reg-positioned').onchange=()=>{if(!data.solution){$('reg-positioned').checked=false;notice('Solve registration first.',true);}loadPreview();};
 $('reg-clear-candidates').onclick=()=>{selectTargets((data.candidates||[]).map(c=>'candidate:'+c.id));deleteSelectedTargets();};
 $('reg-add-row').onclick=()=>{rows=values();rows.push({id:'CP'+String(rows.length+1).padStart(2,'0'),role:'fit',source:[null,null,null],control:[null,null,null]});dirty=true;invalidateSolution();pairTable();quality();};$('reg-save-pairs').onclick=async()=>{try{await saveIfDirty();notice('Target edits saved. Solve to calculate a new position.');}catch(e){notice(e.message,true);}};
 $('reg-solve').onclick=async()=>{rows=values();if(rows.some(r=>r.role!=='off'&&[...r.source,...r.control].some(v=>v===null||!Number.isFinite(v)))){notice('Survey-control registration needs complete E, N, H. To match scans using their measured targets, use Register setups.',true);return;}try{await post('/api/registration-action',{action:'solve',source_id:data.source_id,rows,tolerance_mm:+$('reg-limit').value});dirty=false;await sync();$('reg-quality').scrollIntoView({block:'center'});notice('Registration calculated. Target errors and independent checks are shown below; Apply registration uses this result directly.');}catch(e){notice(e.message,true);}};
 $('reg-limit').onchange=()=>{dirty=true;invalidateSolution();quality();};$('reg-json').onclick=()=>{if(data.solution&&!dirty)location.href='/api/registration-report';};$('reg-residuals').onclick=()=>{if(data.solution&&!dirty)location.href='/api/registration-report?format=csv';};
 $('reg-solve-group-control').onclick=async()=>{
  if(busy||!data.source_item_id)return;
  try{
   await saveIfDirty();
   await post('/api/registration-action',{action:'solve_group_control',scan_id:data.source_item_id,
    source_id:data.source_id,tolerance_mm:+$('reg-limit').value});
   await sync({skipPreview:true});
   window.TunnelRibbon?.registrationDrawerMode('apply');
   notice('Survey fit calculated. Review the fit and target residuals before applying.');
  }catch(e){notice(e.message,true);}
 };
 $('reg-apply').onclick=async()=>{
  if(!data.solution||dirty||busy)return;
  const solution=data.solution,body={action:'apply',solution_id:solution.id,reviewed:true,to_survey:!$('reg-to-survey').disabled&&$('reg-to-survey').checked};
  const source=data.source_item_id;
  if(!source&&!solution.control_group){await action(body);return;}
  try{
   setBusy(true);await post('/api/registration-action',body);
   const job=await window.JobUI.waitReady(null,{skipSync:true});
   if(job?.cancelled)return;
   const bundle=window.JobUI.inventory?.().bundles?.find(item=>item.scan_ids.includes(source));
   const displayed=await window.JobUI.loadPreview({northUp:true,fitIds:bundle?.scan_ids||[source]});
   await poll();
   const count=solution.control_group?.setups||bundle?.scan_ids.length||1;
   notice(`Applied survey control to ${count} setup(s).${displayed?' Showing north-up plan.':''}`);
  }catch(e){setBusy(false);notice(e.message,true);}
 };
 $('reg-export').onclick=()=>window.TunnelRibbon.export('registered');
 function readControlRows(text,delimiter){
  const records=[];let cells=[],value='',quoted=false,closed=false,line=1,start=1;
  const cell=()=>{cells.push(value.trim());value='';closed=false;};
  const row=()=>{cell();if(cells.some(Boolean))records.push({line:start,cells});cells=[];};
  text=text.replace(/^\uFEFF/,'').replace(/\r\n?/g,'\n');
  for(let i=0;i<text.length;i++){
   const ch=text[i];
   if(quoted){
    if(ch==='"'&&text[i+1]==='"'){value+='"';i++;}
    else if(ch==='"'){quoted=false;closed=true;}
    else{value+=ch;if(ch==='\n')line++;}
   }else if(ch===delimiter)cell();
   else if(ch==='\n'){row();start=++line;}
   else if(ch==='"'&&!value.trim()&&!closed){value='';quoted=true;}
   else if(closed){if(ch!==' ')throw Error('Unexpected text after a quoted value on line '+line+'.');}
   else value+=ch;
  }
  if(quoted)throw Error('Unclosed quoted field starting on line '+start+'.');
  row();
  return records;
 }
 const controlFields=['ID','E','N','H'];
 const controlAliases=[['id','point','pointid','pointname','pointnumber','name','target','targetid','targetname'],['e','easting','east','x'],['n','northing','north','y'],['h','height','elevation','elev','level','z','rl']];
 function mappedControls(records,columns){
  if(columns.some(i=>i<0)||new Set(columns).size!==4)throw Error('Choose a different CSV column for each of ID, E, N and H.');
  if(!records.length)throw Error('The file contains no control points.');
  if(records.length>2000)throw Error('Import up to 2,000 control points at a time.');
  const controls=new Map();
  for(const {line,cells} of records){
   const id=cells[columns[0]],xyz=columns.slice(1).map(i=>{
    const value=(cells[i]||'').replace(',','.');
    return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value)?Number(value):NaN;
   });
   if(!id||id.length>60||xyz.some(v=>!Number.isFinite(v)))throw Error('Line '+line+' needs a target ID (up to 60 characters) and numeric E, N and H.');
   if(controls.has(id))throw Error('Line '+line+' repeats target ID '+id+'.');
   controls.set(id,xyz);
  }
  return Object.fromEntries(controls);
 }
 const csvDialog=document.createElement('dialog');
 csvDialog.id='control-csv-dialog';csvDialog.className='ts-analysis-dialog';
 csvDialog.innerHTML=`<header><div><h2>Preview control CSV</h2><p id="control-csv-name"></p></div><button id="control-csv-close" aria-label="Close CSV preview">×</button></header>
 <div class="ts-analysis-settings"><label>Separator<select id="control-csv-delimiter"><option value=",">Comma</option><option value=";">Semicolon</option><option value="&#9;">Tab</option></select></label><label><input id="control-csv-header" type="checkbox"> First row has column names</label></div>
 <p>Choose the ID, Easting, Northing and Height columns. Coordinates must be in metres.</p>
 <div class="ts-analysis-settings">${controlFields.map(f=>`<label>${f}<select id="control-map-${f}"></select></label>`).join('')}</div>
 <div class="ts-analysis-table"><table><thead id="control-csv-head"></thead><tbody id="control-csv-rows"></tbody></table></div>
 <footer><p id="control-csv-status" role="status"></p><button id="control-csv-confirm" class="primary">Import control</button></footer>`;
 document.body.append(csvDialog);
 let csvText='',csvRecords=[],csvControls=null,csvProject=null,csvImporting=false,csvImportContext=null;
 const csvMapping=()=>controlFields.map(f=>$('control-map-'+f));
 function validateControlPreview(){
  const columns=csvMapping().map(x=>x.value===''?-1:Number(x.value));
  const rows=csvRecords.slice(0,12);
  for(const tr of $('control-csv-rows').rows)for(let i=1;i<tr.cells.length;i++)tr.cells[i].classList.toggle('control-mapped',columns.includes(i-1));
  csvControls=null;
  try{
   csvControls=mappedControls(csvRecords,columns);
   $('control-csv-status').textContent=`${csvRecords.length} valid control points · showing ${rows.length}`;
  }catch(e){$('control-csv-status').textContent=e.message;}
  $('control-csv-confirm').disabled=!csvControls||csvImporting;
 }
 function renderControlPreview(){
  const mappings=csvMapping();
  const table=$('control-csv-rows'),head=$('control-csv-head');
  try{
   const records=readControlRows(csvText,$('control-csv-delimiter').value);
   const header=$('control-csv-header').checked?records.shift()?.cells||[]:[];
   csvRecords=records;
   const width=records.reduce((n,r)=>Math.max(n,r.cells.length),header.length);
   if(width>128)throw Error('The file has more than 128 columns. Check the separator.');
   const labels=Array.from({length:width},(_,i)=>header[i]||'Column '+(i+1));
   const keys=labels.map(v=>v.toLowerCase().replace(/\W/g,''));
   for(let f=0;f<mappings.length;f++){
    mappings[f].innerHTML='<option value="">Choose column</option>'+labels.map((v,i)=>`<option value="${i}">${esc(v)} (${i+1})</option>`).join('');
    const matches=keys.flatMap((key,i)=>controlAliases[f].includes(key)?[i]:[]);
    if(matches.length===1)mappings[f].value=String(matches[0]);
    else if(!header.length&&width===4)mappings[f].value=String(f);
   }
   head.innerHTML='<tr><th>Line</th>'+labels.map(v=>'<th>'+esc(v)+'</th>').join('')+'</tr>';
   table.innerHTML=records.slice(0,12).map(r=>'<tr><th>'+r.line+'</th>'+labels.map((_,i)=>'<td>'+esc(r.cells[i]||'')+'</td>').join('')+'</tr>').join('');
   validateControlPreview();
  }catch(e){csvRecords=[];csvControls=null;head.replaceChildren();table.replaceChildren();$('control-csv-status').textContent=e.message;$('control-csv-confirm').disabled=true;}
 }
 function importControl(projectWide=true){
  const selected=window.JobUI?.selectedItem?.();
  csvImportContext={projectWide,source_item_id:selected?.kind==='scan'?selected.id:null};
  $('reg-control-file').click();
 }
 $('reg-control-import').onclick=()=>importControl(!!window.WorkspaceUI?.selectedBundleId?.()||!!window.JobUI?.inventory?.().bundles?.length);
 $('reg-control-file').onchange=async e=>{
  const file=e.target.files[0];if(!file)return;
  const context=csvImportContext||{projectWide:true};const project=state?.project_id;
  e.target.value='';
  try{
   if(file.size>2000000)throw Error('Control CSV is limited to 2 MB.');
   const text=await file.text();
   if(project!==state?.project_id)throw Error('The project changed. Choose the CSV again.');
   const candidates=[];
   for(const delimiter of [',',';','\t']){
    try{
     const records=readControlRows(text,delimiter);
     const width=records[0]?.cells.length||0;
     const score=width>=4?records.slice(0,12).filter(r=>r.cells.length===width).length:0;
     candidates.push({delimiter,score,width,first:records[0]?.cells||[]});
    }catch(_error){continue;}
   }
   candidates.sort((a,b)=>b.score-a.score||b.width-a.width);
   const best=candidates[0];
   csvText=text;csvProject=project;csvImportContext=context;
   $('control-csv-name').textContent=file.name;
   $('control-csv-delimiter').value=best?.delimiter||',';
   const first=best?.first||[];
   $('control-csv-header').checked=first.some(v=>controlAliases.some(aliases=>aliases.includes(v.toLowerCase().replace(/\W/g,''))))||
    first.slice(1).filter(v=>v&&Number.isFinite(Number(v.replace(',','.')))).length<3;
   renderControlPreview();csvDialog.showModal();
  }catch(err){notice(err.message,true);}
 };
 $('control-csv-close').onclick=()=>{if(!csvImporting)csvDialog.close();};
 csvDialog.addEventListener('cancel',e=>{if(csvImporting)e.preventDefault();});
 $('control-csv-delimiter').onchange=$('control-csv-header').onchange=renderControlPreview;
 for(const mapping of csvMapping())mapping.onchange=validateControlPreview;
 $('control-csv-confirm').onclick=async()=>{
  if(csvImporting||!csvControls)return;
  csvImporting=true;$('control-csv-confirm').disabled=true;
  try{
   if(csvProject!==state?.project_id)throw Error('The project changed. Choose the CSV again.');
   await saveIfDirty();
   const {projectWide,source_item_id}=csvImportContext;
   await post('/api/scan-registration',{action:'controls',controls:csvControls,use_for_fit:true,project_wide:projectWide,source_item_id});
   csvDialog.close();
   await sync({skipPreview:true});
   if(bundleDialog.open)await refreshBundle();
   else if(projectWide){window.WorkspaceUI?.refreshBundleQA?.();notice('Control imported. Select a bundle and click Apply control.');}
   else{window.TunnelRibbon?.registrationDrawerMode('match');notice('Control imported. Review the matched target names before fitting.');}
  }catch(e){$('control-csv-status').textContent=e.message;notice(e.message,true);}
  finally{csvImporting=false;$('control-csv-confirm').disabled=!csvControls;}
 };
 const bundleDialog=document.createElement('dialog');
 bundleDialog.id='bundle-control-dialog';bundleDialog.className='ts-analysis-dialog';
 bundleDialog.innerHTML=`<header><div><h2>Bundle registration</h2><p id="bundle-control-name"></p></div><button id="bundle-control-close" aria-label="Close bundle registration">×</button></header>
 <div id="bundle-control-metrics" class="bundle-qa-metrics"></div>
 <div class="ts-analysis-settings"><button id="bundle-control-import">Import control CSV…</button><label>Review limit (mm)<input id="bundle-control-limit" type="number" min=".01" step=".1" value="5"></label><button id="bundle-control-solve">Calculate control fit</button></div>
 <p>Optimise the linked setups first if needed. Applying control then moves the entire bundle together into E, N, H.</p>
 <div id="bundle-control-review"></div>
 <details id="bundle-control-matches"><summary>Matched control IDs</summary><div id="bundle-control-match-list"></div></details>
 <details id="bundle-control-links"><summary>Link and cloud-to-cloud errors</summary><div id="bundle-control-link-list"></div></details>
 <footer><p id="bundle-control-message" role="status"></p><button id="bundle-control-apply" class="primary" disabled>Apply control</button></footer>`;
 document.body.append(bundleDialog);
 let bundleId=null,bundleData=null,bundleDraft=null,bundleWorking=false,bundleRead=0,controlDiagnostic=null;
 function endControlDiagnostic(outcome){window.RegistrationDiagnostics?.finish(controlDiagnostic,outcome);controlDiagnostic=null;}
 const bc=id=>$('bundle-control-'+id);
 const rms=v=>Number.isFinite(v)?fmt(v,2)+' mm':'—';
 const rmsChange=(a,b)=>Number.isFinite(a)?rms(a)+' → '+rms(b):rms(b);
 function bundleMetrics(value,draft=null){
  const adj=value.adjustment,applied=value.control_report,fit=draft||applied?.solution;
  const global=adj?.target_fit?.count?adj.target_fit:adj?.fit;
  const globalBefore=global?.count?adj.before?.rms_mm:null;
  const clouds=value.links.map(l=>l.plane_after).filter(Number.isFinite);
  const low=Math.min(...clouds),high=Math.max(...clouds);
  const cloudRange=low===high?rms(low):rms(low)+' – '+rms(high);
  const measured=adj?.measured_cloud_links;
  const cloudLabel=adj?.targets_only?'Cloud-to-cloud (not run)':measured>0&&measured<adj?.link_count?'Cloud-to-cloud (subset)':'Cloud-to-cloud';
  return [
   {name:adj?.stale?'Global error (previous)':'Global error',value:rmsChange(globalBefore,global?.rms_mm),note:adj?.stale?'Previous result · links changed':adj?(adj.target_fit?.count?'Target RMS after whole-bundle adjustment':'Plane RMS after whole-bundle adjustment'):'Run whole-bundle optimisation to measure'},
   {name:adj?.stale?cloudLabel+' (previous)':cloudLabel,value:Number.isFinite(adj?.cloud_fit?.rms_mm)?rmsChange(adj.cloud_before?.rms_mm,adj.cloud_fit.rms_mm):clouds.length&&!adj?.targets_only?cloudRange:'—',note:adj?.stale?'Previous result · links changed':Number.isFinite(adj?.cloud_fit?.rms_mm)?(Number.isInteger(measured)?measured+' of '+adj.link_count+' links with measured surface RMS':'Sampled point-to-plane RMS'):'Saved link plane RMS range'},
   {name:draft?'Control fit preview':applied?.stale?'Control fit (previous)':'Control fit',value:rms(fit?.fit?.rms_mm),note:draft?'Review signed E/N/H residuals':applied?.stale?'Previous fit · inputs changed':applied?'Applied · fit RMS':'Control coordinates not applied'}
  ];
 }
 function metricsHTML(value,draft){
  return bundleMetrics(value,draft).map(m=>`<div><span>${esc(m.name)}</span><b>${esc(m.value)}</b><small>${esc(m.note)}</small></div>`).join('');
 }
 function tableHTML(head,rows){
  return '<div class="ts-analysis-table"><table><thead><tr>'+head.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr></thead><tbody>'+
   rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
 }
 function renderBundle(){
  const value=bundleData,draft=bundleDraft;
  bc('solve').disabled=bundleWorking||!value||value.summary.fit<3;
  bc('apply').disabled=bundleWorking||!draft;bc('apply').hidden=!draft;
  bc('solve').classList.toggle('primary',!draft);
  for(const id of ['import','limit','close'])bc(id).disabled=bundleWorking;
  if(!value)return;
  const {bundle,summary,links,adjustment,control_report}=value;
  bc('name').textContent=`${bundle.name} · ${bundle.scan_ids.length} setups · ${links.length} links`;
  bc('metrics').innerHTML=metricsHTML(value,draft);
  bc('matches').hidden=!summary.controls;
  bc('match-list').innerHTML=tableHTML(['ID','Role','Measured in'],summary.matches.map(m=>[m.id,m.role==='check'?'Check':'Fit',m.setups.join(', ')]))+
   (summary.unmatched.length?'<p>Unmatched IDs: '+esc(summary.unmatched.join(', '))+'</p>':'');
  bc('links').hidden=!links.length;
  bc('link-list').innerHTML=(adjustment?.fallback_used?'<p>Pairwise fallback was used. Review the link errors before using this bundle.</p>':'')+
   tableHTML(['Link','Target RMS','Cloud-to-cloud RMS','Status'],links.map(l=>[l.a+' ↔ '+l.b,rms(l.target_rms),rmsChange(l.plane_before,l.plane_after),l.status||'Saved']));
  const solution=draft||control_report?.solution;
  bc('review').innerHTML=solution?`<h3>${draft?'Review control fit':'Last applied control'}</h3><p>Fit RMS ${rms(solution.fit.rms_mm)} · Check RMS ${rms(solution.check.rms_mm)} · Maximum fit error ${rms(solution.fit.max_mm)}</p>`+
   tableHTML(['Control ID','Role','ΔE (mm)','ΔN (mm)','ΔH (mm)','3D error'],solution.residuals.map(r=>[r.id,r.role==='check'?'Check':'Fit',...(r.residual_mm||[]).map(v=>fmt(v,2)),rms(r.error_mm)]))+
   (solution.warnings.length?'<ul>'+solution.warnings.map(w=>'<li>'+esc(w)+'</li>').join('')+'</ul>':''):'';
 }
 async function refreshBundle(){
  const read=++bundleRead,id=bundleId;
  endControlDiagnostic('abandoned');bundleData=bundleDraft=null;
  for(const key of ['metrics','review','match-list','link-list'])bc(key).replaceChildren();
  bc('matches').hidden=bc('links').hidden=true;bc('name').textContent='Reading selected bundle…';
  bc('message').textContent='';renderBundle();
  try{
   const value=await bundleQA(id);
   if(read!==bundleRead||!bundleDialog.open)return;
   bundleData=value;
   const {fit,check,controls}=value.summary;
   bc('message').textContent=controls?`${fit} Fit · ${check} Check · ${controls} imported IDs. ${fit<3?'At least three non-collinear Fit targets are needed.':'Calculate the fit and review before applying.'}`:'Import a control CSV to match IDs across the selected bundle.';
   renderBundle();
  }catch(e){if(read===bundleRead)bc('message').textContent=e.message;}
 }
 const bundleQA=id=>post('/api/scan-registration',{action:'bundle_control_status',bundle_id:id});
 async function openBundle(id=window.WorkspaceUI?.selectedBundleId?.()){
  if(busy||bundleWorking)return;
  if(!id){notice('Select a joined bundle in the Project Explorer first.',true);return;}
  bundleId=id;bc('limit').value=5;
  if(!bundleDialog.open)bundleDialog.showModal();
  await refreshBundle();
 }
 bc('close').onclick=()=>bundleDialog.close();
 bundleDialog.addEventListener('close',()=>endControlDiagnostic('abandoned'));
 bundleDialog.addEventListener('cancel',e=>{if(bundleWorking)e.preventDefault();});
 bc('import').onclick=()=>importControl();
 bc('limit').oninput=()=>{endControlDiagnostic('abandoned');bundleDraft=null;renderBundle();};
 bc('solve').onclick=async()=>{
  if(bundleWorking||!bundleData)return;
  endControlDiagnostic('abandoned');controlDiagnostic=window.RegistrationDiagnostics?.begin('control_fit','control');
  bundleWorking=true;bundleDraft=null;renderBundle();
  try{
   const tolerance_mm=Number(bc('limit').value);
   if(!Number.isFinite(tolerance_mm)||tolerance_mm<=0)throw Error('Enter a positive review limit in millimetres.');
   await saveIfDirty();
   const result=await post('/api/scan-registration',{action:'bundle_control_solve',bundle_id:bundleId,tolerance_mm,...(controlDiagnostic?{diagnostics:true}:{})});
   const ready=performance.now();bundleDraft=result.solution;
   window.RegistrationDiagnostics?.report(controlDiagnostic,result.timings,ready);
   bc('message').textContent='Review the residuals, then click Apply control to position the bundle.';
  }catch(e){endControlDiagnostic('failed');bc('message').textContent=e.message;}
  finally{bundleWorking=false;renderBundle();}
 };
 bc('apply').onclick=async()=>{
  if(bundleWorking||!bundleDraft)return;
  const fitIds=bundleData?.bundle.scan_ids||[];
  bundleWorking=true;renderBundle();
  try{
   await post('/api/scan-registration',{action:'bundle_control_apply',bundle_id:bundleId,solution_id:bundleDraft.id,reviewed:true});
   const job=await window.JobUI.waitReady(null,{skipSync:true});
   if(job?.cancelled){endControlDiagnostic('abandoned');throw Error('Control was not applied.');}
   endControlDiagnostic('applied');
   bundleDraft=null;bundleDialog.close();
   const displayed=await window.JobUI.loadPreview({northUp:true,fitIds});
   await sync({skipPreview:true});
   window.WorkspaceUI?.refreshBundleQA?.();
   notice('Control applied to the entire bundle.'+(displayed?' Showing north-up plan.':''));
  }catch(e){endControlDiagnostic('failed');bc('message').textContent=e.message;bundleDraft=null;}
  finally{bundleWorking=false;renderBundle();}
 };
 function setBusyReg(v){if(targetSearching){if(v)window.WorkStatus?.show('Finding target centres…',state?.job?.message||'Reading original scan points',true);}$('targets-delete').disabled=v||!selectedTargets.size;$('targets-undo').disabled=v||!data.can_undo_targets;for(const id of ['reg-browse','reg-import','reg-demo','reg-detect','reg-pick','reg-intensity-view','reg-fit-seed','reg-add-row','reg-control-import','reg-save-pairs','reg-solve','reg-clear-candidates'])$(id).disabled=v;for(const input of $('reg-pairs').querySelectorAll('input,select,button'))input.disabled=v;quality();targetMethodUI();}

 function managedTargets(){const items=(data.candidates||[]).map(c=>({...c,name:targetName(c),key:'candidate:'+c.id}));for(const row of rows){if(items.some(c=>c.id===row.id||c.id===row.candidate_id))continue;items.push({id:row.id,name:row.id,key:'row:'+row.id,source:row.source,status:row.role==='off'?'excluded':'correspondence'});}return items;}
 function visibleTargets(){const filter=$('targets-search').value.trim().toLowerCase();return managedTargets().filter(t=>!filter||((t.name||t.id)+' '+t.status).toLowerCase().includes(filter));}
 function renderManager(){const all=managedTargets(),keys=new Set(all.map(t=>t.key));selectedTargets=new Set([...selectedTargets].filter(k=>keys.has(k)));$('targets-list').innerHTML=visibleTargets().map(t=>`<tr class="${selectedTargets.has(t.key)?'target-selected':''}"><td><input type="checkbox" data-target-key="${esc(t.key)}" aria-label="Select target ${esc(t.id)}" ${selectedTargets.has(t.key)?'checked':''}></td><td>${esc(t.name||t.id)}${t.thumbnail?targetThumb(t):''}</td>${(t.source||[null,null,null]).map(x=>`<td>${Number.isFinite(x)?fmt(x,4):'—'}</td>`).join('')}<td>${esc(t.status||'unreviewed')}</td><td><button data-target-focus="${esc(t.key)}">Zoom</button></td></tr>`).join('')||'<tr><td colspan="7">No targets match. Find targets in Registration or clear the filter.</td></tr>';
 for(const cb of $('targets-list').querySelectorAll('[data-target-key]'))cb.onchange=()=>selectTargets([cb.dataset.targetKey],true);
 for(const b of $('targets-list').querySelectorAll('[data-target-focus]'))b.onclick=()=>{selectTargets([b.dataset.targetFocus]);focusTarget(b.dataset.targetFocus);};
 $('targets-selection-info').textContent=`${selectedTargets.size} selected · ${all.length} targets · ${data.cloud?.name||'No registration source'}`;
 $('targets-delete').disabled=busy||!selectedTargets.size;$('targets-undo').disabled=busy||!data.can_undo_targets;
 window.JobUI?.highlightTargets?.([...selectedTargets]);}
 function selectTargets(keys,additive=false){if(!additive)selectedTargets.clear();for(const key of keys){if(additive&&selectedTargets.has(key))selectedTargets.delete(key);else selectedTargets.add(key);}renderManager();}
 function focusTarget(key){const t=managedTargets().find(x=>x.key===key);if(!t?.source?.every(Number.isFinite)){notice('This target has no complete source coordinates.',true);return;}window.JobUI?.focusTarget?.(t.source,Math.max(.08,(t.support_scale_m||t.diameter_m||.15)*1.5));}
 async function openManager(){try{const x=window.JobUI?.selectedItem();if(x?.kind==='scan'&&data.source_item_id!==x.id&&window.ScanRegistrationUI)await window.ScanRegistrationUI.activateTargets(x.id);await sync({skipPreview:true});window.TunnelRibbon?.view('targets');renderManager();}catch(e){notice(e.message,true);}}
 async function deleteSelectedTargets(){if(busy||!selectedTargets.size)return;const selected=managedTargets().filter(t=>selectedTargets.has(t.key));try{await saveIfDirty();const ids=selected.filter(t=>t.key.startsWith('candidate:')).map(t=>t.id),row_ids=selected.filter(t=>t.key.startsWith('row:')).map(t=>t.id);await post('/api/registration-action',{action:'remove_targets',ids,row_ids,source_id:data.source_id});selectedTargets.clear();dirty=false;invalidateSolution();await sync();notice(`${selected.length} target(s) deleted. Undo is available in Targets.`);}catch(e){notice(e.message,true);}}
 $('targets-open').onclick=openManager;$('targets-search').oninput=renderManager;
 $('targets-select-all').onclick=()=>selectTargets(visibleTargets().map(t=>t.key));$('targets-clear').onclick=()=>selectTargets([]);
 $('targets-delete').onclick=deleteSelectedTargets;$('targets-box').onclick=()=>{window.JobUI?.setMode('target-box');notice('Drag a rectangle in the main 3D view to select target markers. Ctrl-drag adds/removes them.');};$('targets-orbit').onclick=()=>window.JobUI?.setMode('orbit');
 $('targets-undo').onclick=async()=>{if(busy||dirty){notice('Save target edits before undoing a deletion.',true);return;}try{await post('/api/registration-action',{action:'undo_targets',source_id:data.source_id});selectedTargets.clear();await sync();notice('Target deletion undone.');}catch(e){notice(e.message,true);}};

 async function renameMarker(scanId,key,name){
  if(busy)throw Error('Wait for the current operation before renaming.');
  if(data.source_item_id!==scanId)await window.ScanRegistrationUI.activateTargets(scanId,{setup:true});
  if(busy||!window.JobUI.targetOwnerVisible(scanId)||data.source_item_id!==scanId)
   throw Error('The active scan changed. Choose its target again.');
  await saveIfDirty();
  const target=key.startsWith('candidate:')?{id:key.slice(10)}:{row_id:key.slice(4)};
  await post('/api/registration-action',{action:'rename_target',scan_id:scanId,source_id:data.source_id,name,...target});
  await sync({skipPreview:true});
 }
 window.TargetUI={openBundle,bundleQA,metricsHTML,importControl,renameMarker,fitPoint:detect,sync,busy:setBusyReg,draw:()=>sceneReg.draw(),saveIfDirty,openManager,selectTargets,selectedKeys:()=>[...selectedTargets],deleteSelectedTargets,focusTarget};
 sync().catch(e=>notice(e.message,true));
})();
