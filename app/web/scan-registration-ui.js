/* Scan-scoped targets, reviewed pair links, Leica folder import and setup picking. */
'use strict';
(()=>{
 let network={},leica=null,view=null,picked=null,linkSelection=null,matchingPair='';
 let leicaPhase='idle',leicaReceipt=null,inspectionVersion=0;
 let linkPhase='idle',reviewDraft=null,setupLoading=false,mergeRunning=false,mergeEstimate=null;
 let matchReview=null,matchReviewInput=null,matchReviewVersion=0,matchReviewOperation=null,linkOpenVersion=0;
 const panel=document.createElement('div');panel.innerHTML=`
 <style>
 #link-match-review[hidden],#link-adopt-review[hidden],#link-review-cancel[hidden]{display:none!important}
 #link-review-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:12px 0}
 #link-review-help{font-size:12px;line-height:1.5;color:var(--ts-theme-muted,#576f7c)}
 #link-match-review{margin:12px 0;padding:12px 15px;border:1px solid var(--ts-theme-border,#c6d6de);border-radius:8px;background:var(--ts-theme-surface,#f3f7f9);color:var(--ts-theme-text,#203b4a);overflow-wrap:anywhere}
 #link-match-review h3{margin:0 0 7px;font-size:15px}#link-match-review p{margin:7px 0;font-size:12px;line-height:1.5}
 #link-match-review summary{cursor:pointer;padding:7px 0;font-size:12px}#link-match-review .table-scroll{max-height:260px;overflow:auto}
 #link-match-review table{font-size:12px}#link-match-review th,#link-match-review td{padding:7px;vertical-align:top}
 #link-match-review th:nth-child(2),#link-match-review td:nth-child(2),#link-match-review td:nth-child(3){white-space:nowrap}
 #link-match-review .review-suspect{color:var(--ts-theme-warning-text,#765d2b)}#link-match-review .review-note{color:var(--ts-theme-muted,#576f7c)}
 #link-match-review ul{padding-left:20px;font-size:12px;line-height:1.5}#link-adopt-review{margin-top:10px}
 </style>
 <dialog id="scan-links-dialog" class="registration-workspace-dialog"><div class="dialog-heading"><h2>Register setups</h2><button data-close="scan-links-dialog">×</button></div><p>Shared target names are matched automatically. Register both scans or connected groups together. Full 3D needs at least three non-collinear Fit targets; Keep vertical can use two horizontally separated targets. Coordinates must be in metres; scale stays fixed.</p>
 <section id="link-feedback" aria-live="polite" aria-atomic="true"><strong id="link-feedback-title">Choose two scans</strong><p id="link-message" role="status">Choose the two setups to register.</p><progress id="link-progress" hidden aria-label="Geometry operation"></progress></section><div class="registration-pair"><label>First scan<select id="link-fixed"></select></label><label>Second scan<select id="link-moving"></select></label></div><label>Geometry method<select id="link-method"><option value="rigid">Full 3D · three or more targets</option><option value="vertical">Two targets — keep vertical</option></select></label><p id="link-method-help">Full rotation and translation will be solved.</p><p id="link-up-label" hidden>Keep vertical assumes the setups already share the same vertical (Z) direction. It solves heading and translation while keeping roll and pitch.</p><section id="link-network-options" hidden><p id="link-network-help"></p><p>Existing links keep their recorded 2D or 3D method. All connected setups can move during adjustment using shared targets and available cloud surfaces.</p></section><button id="link-match-names">Match target names</button><button id="link-add-match">Add match</button><div class="table-scroll"><table><thead><tr><th>First scan target</th><th>Second scan target</th><th>Use</th><th></th></tr></thead><tbody id="link-matches"></tbody></table></div><p id="link-reference-help">The adjustment preserves the group’s overall local position. All matching Fit targets are used automatically; Check targets remain independent and Excluded targets stay out of the solution.</p><label>Residual warning level (mm)<input id="link-tolerance" type="number" value="5" min="0.01" step="0.1"></label><div id="link-review-actions"><button id="link-review-matches" aria-describedby="link-review-help">Review suspect matches</button><button id="link-review-cancel" hidden>Stop review</button></div><p id="link-review-help">Optional: check target-pair consistency before solving. Review needs at least four Fit pairs for Full 3D, or three for Keep vertical, with useful spread. Check targets stay independent.</p><section id="link-match-review" hidden aria-labelledby="link-match-review-title"><h3 id="link-match-review-title"></h3><div id="link-match-review-summary"></div><details id="link-match-review-details"><summary id="link-match-review-details-title">Target residuals</summary><div class="table-scroll"><table><thead><tr><th>Target pair</th><th>Use</th><th>Residual (mm)</th><th>Review</th></tr></thead><tbody id="link-match-review-rows"></tbody></table></div><div id="link-match-review-notes"></div></details><button id="link-adopt-review" hidden>Exclude suggested matches</button></section><button id="link-solve" class="primary">Register setups</button><div id="link-residuals"></div><p>Registration saves the setup transforms immediately and shows the residuals here. High residuals remain visible as warnings; they do not prevent a valid result from being used. Original point coordinates are kept.</p><button id="link-accept" hidden>Apply calculated position</button><button id="link-view-scans" hidden>View positioned scans</button><section id="link-merge-tools" hidden><p id="link-merge-space" class="tiny"></p><button id="link-build-merge">Build merged cloud…</button><div id="link-old-merges"></div></section><h3 id="scan-link-list-title">Saved links</h3><div id="scan-link-list"></div></dialog>
 <dialog id="leica-project-dialog" class="registration-workspace-dialog"><div class="dialog-heading"><h2>Import RTC360 raw / Leica project</h2><button data-close="leica-project-dialog">×</button></div><p>Choose the original RTC360 job/setup folder for direct raw import, or a REGISTER 360 project folder. Raw setups are listed automatically and stay separate until you register them. When pasting a path, click <b>Inspect project</b>.</p><label>Project folder or deliverable database path<input id="leica-project-path" placeholder="Paste the full local folder path"></label><button id="leica-folder-browse">Choose folder…</button><button id="leica-inspect">Inspect project</button><div id="leica-project-summary"></div><label>Positioning<select id="leica-position-mode"><option value="registered">Use saved Register 360 positions</option><option value="unregistered">Import unregistered setups</option></select></label><p id="leica-native-note">Native import includes measured points, intensity and supported setup photographs. External cleaning masks, point RGB and HDR are not imported. Saved positions require independent checks.</p><section id="leica-feedback" aria-live="polite" aria-atomic="true"><strong id="leica-feedback-title">Choose a folder or inspect a pasted path</strong><p id="leica-message" role="status">The setups must be listed before they can be imported.</p><progress id="leica-progress" hidden aria-label="Leica project operation"></progress><ul id="leica-completed-list" hidden></ul></section><button id="leica-import" disabled class="primary">Import selected setups</button><button id="leica-view-imported" hidden class="primary">View imported scans</button></dialog>
`;
 document.body.append(panel);for(const b of panel.querySelectorAll('[data-close]'))b.onclick=()=>$(b.dataset.close).close();
 function button(id,text,fn,where){const b=document.createElement('button');b.id=id;b.textContent=text;b.onclick=fn;where.append(b);return b;}
 button('open-scan-links','Register setups',()=>openLinks(),$('job-register').parentElement);
 button('job-setup-view','Go to setup view',()=>openSetup(),$('job-setup-controls'));
 button('import-leica-project','RTC360 raw / Leica project folder',()=>{$('import-dialog').close();$('leica-project-dialog').showModal();},$('import-dialog').querySelector('.import-grid'));
 const select=document.createElement('select');select.id='registration-scan-choice';select.setAttribute('aria-label','Registration scan');const label=document.createElement('label');label.textContent='Work on scan ';label.append(select);$('reg-source-info').before(label);
 button('reg-match-scans','Register setups',()=>openLinks(),label.parentElement);
 button('reg-share-control','Save entered coordinates to shared control list',async()=>{try{await window.TargetUI.saveIfDirty();const r=await api('/api/registration');const controls={};for(const row of r.rows||[])if(row.control?.every(v=>v!==null&&Number.isFinite(v)))controls[row.id]=row.control;await post('/api/scan-registration',{action:'controls',controls});notice('Survey control coordinates saved by target name for all scans.');}catch(e){notice(e.message,true);}},label.parentElement);
 button('reg-use-controls','Fill coordinates from shared control list',async()=>{try{await window.TargetUI.saveIfDirty();const r=await api('/api/registration');network=await api('/api/scan-registration');const rows=(r.rows||[]).map(row=>({...row,control:network.controls[row.id]||row.control}));await post('/api/registration-action',{action:'rows',rows,source_id:r.source_id});await window.TargetUI.sync();}catch(e){notice(e.message,true);}},label.parentElement);
 async function refresh(){network=await api('/api/scan-registration');const old=select.value;select.innerHTML=(network.scans||[]).map(x=>`<option value="${esc(x.id)}">${esc(x.name)} · ${x.targets.length} named targets</option>`).join('');select.value=network.active||old;window.SceneObjectsUI?.setLinks?.(network.links||[],network.scans||[]);return network;}
 async function activateTargets(id,{setup=false}={}){await window.TargetUI.saveIfDirty();await post('/api/scan-registration',{action:setup?'activate_setup':'activate',id,isolate:false});await window.TargetUI.sync({skipPreview:true});}
 async function activate(id){await window.TargetUI.saveIfDirty();await post('/api/scan-registration',{action:'activate',id,isolate:true});await window.JobUI.sync();window.JobUI.selectScan(id);await window.TargetUI.sync();await refresh();window.JobUI.clearSelection();$('job-load-preview').click();}
 select.onchange=async()=>{try{await activate(select.value);}catch(e){notice(e.message,true);}};
 select.onfocus=()=>refresh().catch(e=>notice(e.message,true));
 function clearMatchReview(){
  matchReviewVersion++;matchReview=null;matchReviewInput=null;
  $('link-match-review').hidden=true;$('link-adopt-review').hidden=true;$('link-adopt-review').disabled=true;
  $('link-match-review-summary').replaceChildren();$('link-match-review-rows').replaceChildren();$('link-match-review-notes').replaceChildren();$('link-match-review-details').open=false;
 }
 function matchInputs(){return {a:$('link-fixed').value,b:$('link-moving').value,matches:[...$('link-matches').children].map(tr=>({a:tr.querySelector('[data-a]').value,b:tr.querySelector('[data-b]').value,role:tr.querySelector('[data-role]').value,keep:tr.querySelector('[data-keep]').value})),mode:$('link-method').value,tolerance:Number($('link-tolerance').value),vertical_confirmed:$('link-method').value==='vertical'};}
 function plainMatches(matches){return Array.isArray(matches)?matches.map(m=>({a:m.a,b:m.b,role:m.role||'fit',keep:m.keep||''})):null;}
 function linkRunning(){return mergeRunning||!!matchReviewOperation||['loading','solving','applying'].includes(linkPhase);}
 function currentReviewOperation(operation){return matchReviewOperation===operation&&operation.version===matchReviewVersion&&$('scan-links-dialog').open;}
 function suggestedRows(review=matchReview){
  const proposed=new Set(review?.result?.proposed_exclusions||[]),input=review?.input_matches||[];
  return (review?.result?.rows||[]).filter(row=>{const original=input[row.match_index];return proposed.has(row.id)&&original?.role==='fit'&&!original.keep&&!row.protected;});
 }
 function canAdoptReview(){return !!matchReview&&matchReview.result?.status==='suspects'&&matchReview.result.can_adopt===true&&suggestedRows().length>0&&matchReviewInput===JSON.stringify(matchInputs());}
 const invalidDraft=()=>{clearMatchReview();reviewDraft=null;linkPhase='idle';$('link-view-scans').hidden=true;$('link-residuals').textContent='Inputs changed. Register setups to calculate and save the new position.';linkControls();};
 $('scan-links-dialog').addEventListener('close',()=>{linkOpenVersion++;clearMatchReview();if(linkPhase==='loading')linkPhase='idle';linkControls();});
 function options(id,value){return (network.scans.find(x=>x.id===id)?.targets||[]).map(n=>`<option ${n===value?'selected':''} value="${esc(n)}">${esc(n)}</option>`).join('');}
 function addMatch(a,b,role='fit',keep=''){
  const tr=document.createElement('tr');
  tr.innerHTML=`<td><select data-a aria-label="First target">${options($('link-fixed').value,a)}</select><input data-keep type="hidden" value=""></td><td><select data-b aria-label="Second target">${options($('link-moving').value,b)}</select></td><td><select data-role aria-label="Target use"><option value="fit">Fit</option><option value="check">Check</option><option value="off">Exclude</option></select></td><td><button>Remove</button></td>`;
  tr.querySelector('[data-role]').value=role;tr.querySelector('[data-keep]').value=role==='fit'?keep:'';
  tr.querySelector('button').onclick=()=>{tr.remove();invalidDraft();};
  for(const el of tr.querySelectorAll('select'))el.onchange=()=>{if(tr.querySelector('[data-role]').value!=='fit')tr.querySelector('[data-keep]').value='';invalidDraft();};
  $('link-matches').append(tr);
 }
 function restoreReferences(d){for(const tr of $('link-matches').children){const value=(d.coordinate_references||d.solution?.coordinate_references||[]).find(r=>r.scan_id===d.a&&r.target===tr.querySelector('[data-a]').value)?'a':(d.coordinate_references||d.solution?.coordinate_references||[]).find(r=>r.scan_id===d.b&&r.target===tr.querySelector('[data-b]').value)?'b':'';tr.querySelector('[data-keep]').value=value;}}
 function connectedIds(id){const ids=new Set(id?[id]:[]);let changed=true;while(changed){changed=false;for(const link of network.links||[])if(ids.has(link.a)||ids.has(link.b)){for(const v of [link.a,link.b])if(!ids.has(v)){ids.add(v);changed=true;}}}return ids;}
 function showConnectedMerge(){
  const id=$('link-fixed').value,ids=connectedIds(id),known=new Set((network.scans||[]).map(x=>x.id));
  const complete=!!id&&[...ids].every(x=>known.has(x)),eligible=complete&&ids.size>=2;
  const hasCaches=(network.merge_caches||[]).length>0;
  $('link-merge-tools').hidden=!(eligible||hasCaches||(id&&!complete));
  const estimate=eligible&&mergeEstimate?.scan_id===id&&mergeEstimate?.setups===ids.size?mergeEstimate:null;
  $('link-build-merge').disabled=linkRunning()||!eligible||estimate?.enough===false;
  if(estimate)$('link-merge-space').textContent=mergeEstimateText(estimate);
  else $('link-merge-space').textContent=!complete&&id?'A saved connection references an unavailable setup. Review the saved links before building a merged cloud.':eligible?`${ids.size} connected setups can be copied into one Registered cloud for Clean Tunnel and Survey analysis. Their current saved positions will be used; another bundle adjustment is not required. Disk space is checked before building.`:'Connect at least two existing setups to build a merged cloud. Existing merged clouds remain available below.';
  renderOldMerges();
 }
 function networkOptions(){const a=$('link-fixed').value,b=$('link-moving').value,ids=connectedIds(a),isNetwork=!!a&&!!b&&a!==b&&ids.has(b);$('link-network-options').hidden=!isNetwork;
  const direct=(network.links||[]).some(l=>new Set([l.a,l.b]).has(a)&&new Set([l.a,l.b]).has(b));
  $('link-network-help').textContent=isNetwork?(direct?'This pair already has a saved connection. Solving will replace its target matches and adjust the connected scans together.':'These scans are connected through other setups. Solving adds this loop link and adjusts the whole connected group.'):'';
  const vertical=$('link-method').value==='vertical';$('link-up-label').hidden=!vertical;$('link-method-help').textContent=vertical?'Heading and translation will be solved. Roll and pitch stay fixed; two points alone cannot determine a full 3D rotation.':'Full rotation and translation will be solved.';return isNetwork;
 }
 function commonRole(a,b,name,previous){
  const roles=[a?.target_roles?.[name],b?.target_roles?.[name],previous?.role];
  return roles.includes('off')?'off':roles.includes('check')?'check':'fit';
 }
 function matchNames(){
  const a=network.scans.find(x=>x.id===$('link-fixed').value),b=network.scans.find(x=>x.id===$('link-moving').value),pair=(a?.id||'')+'|'+(b?.id||'');
  const current=matchingPair===pair?matchInputs().matches:[];
  const saved=(network.links||[]).find(l=>l.a===a?.id&&l.b===b?.id||l.a===b?.id&&l.b===a?.id);
  const historical=(saved?.matches||[]).map(m=>saved.a===a?.id?m:{...m,a:m.b,b:m.a});
  invalidDraft();$('link-matches').replaceChildren();
  for(const name of new Set(a?.targets||[]))if(b?.targets.includes(name)){
   const prior=current.find(m=>m.a===name&&m.b===name)||historical.find(m=>m.a===name&&m.b===name);
   addMatch(name,name,commonRole(a,b,name,prior));
  }
  matchingPair=pair;
  const count=matchInputs().matches.filter(m=>m.role==='fit').length;
  $('link-method').value=count===2?'vertical':'rigid';
  networkOptions();
 }
 function linkList(){$('scan-link-list').hidden=linkPhase==='applied';$('scan-link-list-title').hidden=linkPhase==='applied';const links=network.links||[];$('scan-link-list').innerHTML=links.map(l=>`<div class="scan-link-entry ${l.id===linkSelection?'chosen':''}"><button data-view-link="${esc(l.id)}">${esc(l.a_name)} ↔ ${esc(l.b_name)}</button> <span>${esc(l.status)}${l.solution&&!l.solution.manual?' · RMS '+fmt(l.solution.fit.rms_mm,3)+' mm':''}</span><button data-remove-link="${esc(l.id)}">Remove link</button></div>`).join('')||'No links yet.';for(const b of $('scan-link-list').querySelectorAll('[data-view-link]'))b.onclick=()=>{const l=links.find(x=>x.id===b.dataset.viewLink);linkSelection=l.id;clearMatchReview();reviewDraft=null;showResiduals(l);linkStatus('Saved link',l.kind==='leica'?'Saved Leica connection. Imported positions have already been applied.':'Historical result for this saved connection. Solve a new link before applying another position.');linkList();window.SceneObjectsUI?.select('link:'+l.id);};for(const b of $('scan-link-list').querySelectorAll('[data-remove-link]'))b.onclick=async()=>{if(!confirm('Remove this connection? Scan coordinates stay where they are; the groups become disconnected if no other links join them.'))return;try{await post('/api/scan-registration',{action:'remove_link',id:b.dataset.removeLink});await refresh();invalidDraft();linkList();linkStatus('Link removed','Coordinates are unchanged. Match the targets and solve again to calculate a new connection.');window.JobUI.sync({inventoryOnly:true});}catch(e){linkStatus('Could not remove link',e.message,'error');}};}
 function showResiduals(d){const s=d.solution;if(s?.cloud_to_cloud){$('link-residuals').innerHTML='<p><b>Cloud-to-cloud alignment</b> · '+esc(s.method)+'</p><p>Sampled point-to-plane distances: RMS '+fmt(s.rms_mm,3)+' mm · median '+fmt(s.median_mm,3)+' mm · maximum '+fmt(s.max_mm,3)+' mm</p><p>'+fmt(s.inlier_count,0)+' inliers · '+s.iterations+' iterations · fixed: '+esc(s.anchor_name||s.anchor)+'</p><p>'+s.warnings.map(esc).join('<br>')+'</p>';return;}if(s?.manual){$('link-residuals').textContent='Manual visual alignment · no target residual or cloud optimisation calculated. Fixed reference: '+(s.anchor_name||s.anchor)+'. Use Visual alignment to review the position.';return;}$('link-residuals').innerHTML=s?`${s.constraint?'<p><b>'+esc(s.constraint)+'</b></p>':''}${s.edge_reports?'<p>Joint adjustment · '+s.scan_count+' scans · '+s.link_count+' links'+(s.anchor_name?' · fixed: '+esc(s.anchor_name):'')+'</p><p>Total fit RMS: '+fmt(s.before.rms_mm,3)+' → '+fmt(s.fit.rms_mm,3)+' mm</p><table><thead><tr><th>Link</th><th>Before mm</th><th>After mm</th></tr></thead><tbody>'+s.edge_reports.map(e=>'<tr><td>'+esc(e.a)+' ↔ '+esc(e.b)+'</td><td>'+fmt(e.before_mm,3)+'</td><td>'+fmt(e.after_mm,3)+'</td></tr>').join('')+'</tbody></table>':''}${s.coordinate_references?.length?'<p><b>Target coordinates kept</b></p><ul>'+s.coordinate_references.map(r=>'<li>'+esc(r.target)+' · '+esc(r.scan_name)+' · '+r.coordinates.map(n=>fmt(n,6)).join(' / ')+'</li>').join('')+'</ul>':''}<p><b>Fit RMS ${fmt(s.fit.rms_mm,3)} mm · maximum ${fmt(s.fit.max_mm,3)} mm</b></p><table><thead><tr><th>Match</th><th>Use</th><th>3D residual mm</th></tr></thead><tbody>${s.residuals.map(r=>`<tr><td>${esc(r.id)}</td><td>${esc(r.role)}</td><td class="${r.within_limit?'':'residual-bad'}">${fmt(r.error_mm,3)}</td></tr>`).join('')}</tbody></table><p>${s.warnings.map(esc).join('<br>')}</p>`:`<p>Imported Leica link. Saved setup poses were used; this link has no independently recomputed target residuals.</p>${d.source_method?'<p>Method: '+esc(d.source_method)+'</p>':''}${d.matches?.length?'<b>Leica saved target pairs</b><ul>'+d.matches.map(m=>'<li>'+esc(m.a)+' ↔ '+esc(m.b)+(m.role==='off'?' · Excluded':' · Recorded correspondence')+'</li>').join('')+'</ul>':'<p>Target pairs were not recorded for this imported link.</p>'}`;}
 function linkStatus(title,message,kind='info'){
  $('link-feedback-title').textContent=title;$('link-message').textContent=message;$('link-feedback').dataset.kind=kind;$('link-progress').hidden=kind!=='running';$('link-feedback').scrollIntoView({block:'nearest'});
 }
 function showMatchReview(review){
  const result=review.result,rows=result.rows||[],diagnostics=result.diagnostics||{},proposed=new Set(result.proposed_exclusions||[]);
  const titles={consistent:'Fit target pairs are consistent',suspects:'Suspect target matches',ambiguous:'Matches need manual review',insufficient:'More target evidence is needed'};
  $('link-match-review-title').textContent=titles[result.status]||'Target match review';
  const protectedSuspects=rows.filter(row=>row.role==='fit'&&row.inlier===false&&(row.protected||review.input_matches?.[row.match_index]?.keep));
  const suggestions=suggestedRows(review),proposedCount=suggestions.length,failedChecks=rows.filter(row=>row.role==='check'&&typeof row.error_mm==='number'&&Number.isFinite(row.error_mm)&&row.error_mm>review.tolerance_mm);
  const pairLabel=row=>{const original=review.input_matches?.[row.match_index];return (original?.a??row.a??row.id)+' ↔ '+(original?.b??row.b??'');};
  $('link-match-review-summary').innerHTML=`<p>${esc(result.message||'Review the target residuals before solving.')}</p>${failedChecks.length?'<p class="review-suspect"><b>'+failedChecks.length+' independent Check match'+(failedChecks.length===1?' is':'es are')+' outside the tolerance.</b> These observations remain independent of the registration fit.</p>':''}${protectedSuspects.length?'<p><b>Kept coordinates need manual review.</b> Check the target pairing and the saved coordinate constraints. Suggested exclusions cannot change kept coordinates.</p>':''}${proposedCount?'<p><b>Suggested pairs: '+suggestions.slice(0,4).map(row=>esc(pairLabel(row))).join('; ')+'</b>'+(proposedCount>4?' · '+(proposedCount-4)+' more in the residuals below.':'.')+' Review the residuals, then use the button below.</p>':''}<p class="review-note">This review checks geometric consistency. A suspect result is not proof of a wrong target. Run Register setups when the target pairs are ready.</p>`;
  const priority=row=>row.role==='off'?4:row.inlier===false?0:row.role==='check'?1:2;
  $('link-match-review-rows').innerHTML=[...rows].sort((a,b)=>priority(a)-priority(b)||(a.match_index??0)-(b.match_index??0)).map(row=>{
   const original=review.input_matches?.[row.match_index],role=original?.role||row.role,kept=!!(original?.keep||row.protected),hasError=typeof row.error_mm==='number'&&Number.isFinite(row.error_mm);
   const outside=hasError&&row.error_mm>review.tolerance_mm;
   let note=role==='off'?'Excluded from the fit':role==='check'?'Independent check'+(hasError?(outside?' · outside tolerance':' · within tolerance'):''):kept?(outside?'Kept coordinates · review manually':'Kept coordinates'):proposed.has(row.id)?'Suggested exclusion':row.inlier===true?'Within tolerance':row.inlier===false?'Outside tolerance · review manually':'Not assessed';
   const first=original?.a??row.a??row.id,second=original?.b??row.b??'';
   return `<tr data-review-index="${esc(String(row.match_index??''))}"><td>${esc(first)}${second?' ↔ '+esc(second):''}</td><td>${esc({fit:'Fit',check:'Check',off:'Exclude'}[role]||role)}</td><td${outside?' class="review-suspect"':''}>${hasError?esc(fmt(row.error_mm,3)):'—'}</td><td${outside?' class="review-suspect"':''}>${esc(note)}</td></tr>`;
  }).join('');
  let sampling='';
  if(diagnostics.sampling==='exhaustive')sampling='Every minimal group of Fit target pairs was checked. No match-quality ranking was used.';
  else if(diagnostics.sampling==='uniform')sampling='Fit target groups were sampled without a match-quality ranking. The bounded search may not check every group.';
  else if(diagnostics.sampling==='ranked_with_uniform_fallback'&&diagnostics.quality_used)sampling='Target groups were sampled using supplied match-quality rankings, with uniform fallback sampling.';
  const warnings=Array.isArray(result.warnings)?result.warnings:[];
  $('link-match-review-notes').innerHTML=(sampling?'<p class="review-note">'+esc(sampling)+'</p>':'')+(warnings.length?'<ul>'+warnings.map(w=>'<li>'+esc(w)+'</li>').join('')+'</ul>':'');
  $('link-match-review-details-title').textContent='Target residuals and review notes ('+rows.length+')';
  $('link-match-review').hidden=false;$('link-adopt-review').hidden=!canAdoptReview();
  $('link-adopt-review').textContent='Exclude suggested matches'+(proposedCount?' ('+proposedCount+')':'');
 }
 function linkControls(){
  const running=linkRunning();
  for(const e of $('scan-links-dialog').querySelectorAll('select,input,button'))if(!e.dataset.close)e.disabled=running;
  $('link-review-matches').textContent=matchReviewOperation?.kind==='reviewing'?'Reviewing matches…':'Review suspect matches';
  $('link-adopt-review').disabled=running||!canAdoptReview();
  $('link-review-cancel').hidden=matchReviewOperation?.kind!=='reviewing'||!matchReviewOperation?.started;
  $('link-review-cancel').disabled=!matchReviewOperation?.started||!!matchReviewOperation?.stopping;
  $('link-review-cancel').textContent=matchReviewOperation?.stopping?'Stopping…':'Stop review';
  $('link-solve').textContent=linkPhase==='solving'?'Registering setups…':'Register setups';$('link-accept').textContent=linkPhase==='applying'?'Applying position…':'Apply calculated position';$('link-accept').hidden=!reviewDraft||linkPhase==='applied';
  for(const tr of $('link-matches').children)tr.querySelector('[data-keep]').disabled=running||tr.querySelector('[data-role]').value!=='fit';
  if(linkPhase==='applied')$('link-accept').disabled=true;
  showConnectedMerge();
 }
 async function openLinks(id,review=false){
  if(linkRunning()){if(!$('scan-links-dialog').open)$('scan-links-dialog').showModal();if(matchReviewOperation&&!currentReviewOperation(matchReviewOperation))linkStatus('Finishing the previous review…','The closed review will be discarded. Wait for completion, then review the current matches again.','running');return false;}
  mergeEstimate=null;invalidDraft();$('link-merge-tools').hidden=true;const opening=++linkOpenVersion;linkPhase='loading';linkControls();
  if(!$('scan-links-dialog').open)$('scan-links-dialog').showModal();linkStatus('Loading scan targets…','Reading saved targets and links.','running');
  try{
   await window.TargetUI.saveIfDirty();await refresh();if(opening!==linkOpenVersion||!$('scan-links-dialog').open)return false;linkSelection=id||null;const picked=window.JobUI.selectedItems().filter(id=>network.scans.some(x=>x.id===id));
   for(const field of ['link-fixed','link-moving'])$(field).innerHTML=network.scans.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
   $('link-fixed').value=picked[0]||network.scans[0]?.id||'';$('link-moving').value=picked[1]||network.scans.find(x=>x.id!==$('link-fixed').value)?.id||'';matchNames();linkList();
   if(review&&network.draft){
    const draft=network.draft;$('link-fixed').value=draft.a;$('link-moving').value=draft.b;$('link-matches').replaceChildren();for(const m of draft.matches)addMatch(m.a,m.b,m.role);$('link-tolerance').value=draft.solution.tolerance_mm||5;$('link-method').value=draft.solution.mode||'rigid';networkOptions();restoreReferences(draft);reviewDraft=draft;linkPhase='solved';showResiduals(draft);linkStatus('Calculated position','The residuals are shown below. Apply calculated position saves this existing result directly.');
   }else if(review&&network.last_registration_result){const result=network.last_registration_result;$('link-fixed').value=result.request?.a||result.a;$('link-moving').value=result.request?.b||result.b;matchNames();$('link-method').value=result.request?.mode||result.solution.mode||'rigid';networkOptions();linkPhase='applied';showResiduals(result);$('link-view-scans').hidden=false;linkStatus('Registration result',result.solution?.cloud_to_cloud?'This cloud alignment has been saved. Sampled plane residuals are shown below.':'This position has been saved. Target residuals and independent checks are shown below.');}else if(id){const l=network.links.find(l=>l.id===id);if(l){$('link-fixed').value=l.a;$('link-moving').value=l.b;matchNames();$('link-matches').replaceChildren();for(const m of l.matches||[])addMatch(m.a,m.b,m.role==='leica'?'fit':m.role);restoreReferences(l);showResiduals(l);linkStatus('Saved link',l.kind==='leica'?'Saved Leica positions are already applied. Target pairs and residuals are shown below where recorded.':'This is the saved result of an applied connection.');}}
   else linkStatus('Ready to register','Shared target names are matched. Register setups adjusts the connected setups together.');
   linkControls();return true;
  }catch(e){if(opening===linkOpenVersion&&$('scan-links-dialog').open){linkPhase='idle';linkControls();linkStatus('Could not load scan targets',e.message,'error');}return false;}
 }
 async function reviewMatches(){
  if(linkRunning())return;invalidDraft();const operation={kind:'reviewing',version:matchReviewVersion,started:false,stopping:false};matchReviewOperation=operation;let discarded=false;
  linkControls();linkStatus('Reviewing target matches…','Checking geometric agreement between these two scans.','running');
  try{
   await window.TargetUI.saveIfDirty();if(!currentReviewOperation(operation)){discarded=true;return;}
   const request=matchInputs();
   if(!request.a||!request.b||request.a===request.b)throw Error('Choose two different scans.');
   if(!Number.isFinite(request.tolerance)||request.tolerance<=0)throw Error('Enter a positive review tolerance in millimetres.');
   
   const started=await post('/api/scan-registration',{action:'review_matches',...request});operation.reviewId=typeof started.review_id==='string'?started.review_id:null;operation.started=true;linkControls();
   const job=await window.JobUI.waitReady(progress=>{if(currentReviewOperation(operation)&&progress.busy)linkStatus(operation.stopping?'Stopping the review…':'Reviewing target matches…',operation.stopping?'Waiting for the current check to stop.':progress.message||'Testing the target-pair geometry…','running');},{skipSync:true});
   if(!currentReviewOperation(operation)){discarded=true;return;}
   if(job?.cancelled||operation.stopping)throw Error('Match review cancelled. Review the pairs again when ready.');
   await refresh();if(!currentReviewOperation(operation)){discarded=true;return;}
   const review=network.match_review;
   if(!review?.id||!['consistent','suspects','ambiguous','insufficient'].includes(review.result?.status)||!Array.isArray(review.result.rows))throw Error('No completed target match review was returned. Review the pairs again.');
   if(!operation.reviewId||review.id!==operation.reviewId||review.a!==request.a||review.b!==request.b||review.mode!==request.mode||Number(review.tolerance_mm)!==request.tolerance||JSON.stringify(plainMatches(review.input_matches))!==JSON.stringify(request.matches)||JSON.stringify(matchInputs())!==JSON.stringify(request))throw Error('The target matches changed during review. Review the current pairs again.');
   matchReview=review;matchReviewInput=JSON.stringify(request);showMatchReview(review);
   linkStatus($('link-match-review-title').textContent,review.result.message||'Review the target residuals below.',review.result.status==='consistent'&&!review.result.rows.some(row=>row.role==='check'&&row.inlier===false)?'success':'info');
  }catch(e){
   if(currentReviewOperation(operation)){clearMatchReview();linkStatus('Target match review was not completed',e.message,'error');}else discarded=true;
  }finally{
   if(matchReviewOperation===operation){matchReviewOperation=null;linkControls();if(discarded&&$('scan-links-dialog').open)linkStatus('Previous review discarded','The dialog or matching inputs changed. Click Review suspect matches to check the current pairs.');}
  }
 }
 async function adoptMatchReview(){
  if(linkRunning())return;
  if(!canAdoptReview()){clearMatchReview();linkControls();linkStatus('Review the current matches first','The previous review no longer matches these inputs. Click Review suspect matches again.','error');return;}
  const review=matchReview,request=matchInputs(),allowed=new Set(suggestedRows(review).map(row=>row.match_index));
  const operation={kind:'adopting',version:matchReviewVersion};matchReviewOperation=operation;let discarded=false;
  linkControls();linkStatus('Updating reviewed matches…','Checking that the targets and review inputs are still current.','running');
  try{
   const response=await post('/api/scan-registration',{action:'adopt_match_review',id:review.id,...request});
   if(!currentReviewOperation(operation)){discarded=true;return;}
   if(JSON.stringify(matchInputs())!==JSON.stringify(request))throw Error('The matching inputs changed. Review the current pairs again.');
   const matches=plainMatches(response.matches);
   if(!matches||matches.length!==request.matches.length||matches.some((row,index)=>{const original=request.matches[index];return row.a!==original.a||row.b!==original.b||row.keep!==original.keep||(row.role!==original.role&&!(allowed.has(index)&&original.role==='fit'&&!original.keep&&row.role==='off'));}))throw Error('The reviewed match update did not preserve the target pairs and protected choices. Review the pairs again.');
   const excluded=matches.filter((row,index)=>row.role==='off'&&request.matches[index].role==='fit').length;if(!excluded)throw Error('No exclusions were confirmed. Review the current pairs again.');
   invalidDraft();$('link-matches').replaceChildren();for(const row of matches)addMatch(row.a,row.b,row.role,row.keep);
   linkStatus('Suggested matches excluded',excluded+' Fit match'+(excluded===1?' was':'es were')+' set to Exclude. Check the remaining pairs and kept coordinates, then click Register setups.','success');
  }catch(e){if(currentReviewOperation(operation)){clearMatchReview();linkStatus('Suggested matches were not adopted',e.message,'error');}else discarded=true;}
  finally{if(matchReviewOperation===operation){matchReviewOperation=null;linkControls();if(discarded&&$('scan-links-dialog').open)linkStatus('Previous review discarded','The dialog or matching inputs changed. Review the current pairs again before excluding any suggestions.');}}
 }
 $('link-review-matches').onclick=reviewMatches;$('link-adopt-review').onclick=adoptMatchReview;
 $('link-review-cancel').onclick=async()=>{
  const operation=matchReviewOperation;if(operation?.kind!=='reviewing'||!operation.started||operation.stopping)return;
  operation.stopping=true;linkControls();linkStatus('Stopping the review…','Waiting for the current check to stop.','running');
  try{await post('/api/cancel-operation',{});}catch(e){if(matchReviewOperation===operation){operation.stopping=false;linkControls();if(currentReviewOperation(operation))linkStatus('Could not stop the review',e.message,'error');}}
 };
 $('link-fixed').onchange=matchNames;$('link-moving').onchange=matchNames;$('link-match-names').onclick=matchNames;$('link-add-match').onclick=()=>{addMatch();invalidDraft();};$('link-tolerance').oninput=invalidDraft;$('link-tolerance').onchange=invalidDraft;$('link-method').onchange=()=>{invalidDraft();networkOptions();};
 async function solveLink(){
  if(linkRunning())return;invalidDraft();linkPhase='solving';linkControls();linkStatus('Registering setups…','Calculating the target geometry and saving the setup transforms.','running');
  try{
   await window.TargetUI.saveIfDirty();const input=matchInputs(),{a,b,matches,mode}=input;
   if(!a||!b||a===b)throw Error('Choose two different setups.');
   const minimum=mode==='vertical'?2:3;
   if(matches.filter(m=>m.role==='fit'&&m.a&&m.b).length<minimum)throw Error(mode==='vertical'?'At least two matching Fit targets are needed.':'Full 3D needs at least three non-collinear matching Fit targets in this pair. Use the same target names in both setups; Check and Excluded targets do not determine the fit.');
   if(!Number.isFinite(input.tolerance)||input.tolerance<=0)throw Error('Enter a positive residual warning level in millimetres.');
   const started=await post('/api/scan-registration',{action:'register_setups',a,b,matches,mode,floating:true,tolerance:input.tolerance});
   const job=await window.JobUI.waitReady(progress=>{if(progress.busy)linkStatus('Registering setups…',progress.message||'Saving the calculated setup positions…','running');},{skipSync:true});
   if(job?.cancelled)throw Error('Registration cancelled. No completed position was confirmed.');
   await refresh();const result=network.last_registration_result;
   if(!started.registration_id||result?.id!==started.registration_id||!result.applied||result.request?.a!==a||result.request?.b!==b||result.request?.mode!==mode||result.request?.floating!==true)throw Error('The completed registration could not be confirmed. Check saved links before running it again.');
   reviewDraft=null;linkPhase='applied';showResiduals(result);linkList();$('link-view-scans').hidden=false;$('link-view-scans').textContent='View positioned scans';
   const count=result.solution.scan_count||new Set([...(result.fixed||[]),...(result.moving||[])]).size;
   linkStatus('Registration saved',`${count||2} setups positioned · floating adjustment · fit RMS ${fmt(result.solution.fit.rms_mm,3)} mm. Residuals and warnings are shown below.`,'success');
   window.WorkStatus?.show('Registration complete','Setup positions saved. Target residuals remain available in Registration result.');
   await window.SetupCameraUI?.exit(false);await window.JobUI.sync({inventoryOnly:true});window.JobUI.loadPreview?.();
  }catch(e){reviewDraft=null;linkPhase='idle';linkStatus('Registration was not completed',e.message,'error');}
  finally{linkControls();}
 }
 $('link-solve').onclick=solveLink;
 $('link-accept').onclick=async()=>{
  if(linkRunning()||linkPhase==='applied')return;
  if(!reviewDraft){linkStatus('Solve geometry first','Choose two scans and solve their matching targets before applying a position.','error');return;}
  
  const draft=reviewDraft;linkPhase='applying';linkControls();linkStatus('Applying geometry…','Saving rigid scan poses. Full-density point caches are not being rewritten.','running');
  try{
   await post('/api/scan-registration',{action:'accept',id:draft.id,reviewed:true,merge:false});
   const job=await window.JobUI.waitReady(progress=>{if(progress.busy)linkStatus('Applying geometry…',progress.message||'Saving registration transforms…','running');},{skipSync:true});
   if(job?.cancelled){linkPhase='solved';linkStatus('Geometry application cancelled','No completed position was confirmed. Review the scans before trying again.');return;}
   await refresh();if(draft.network?network.last_adjustment?.id!==draft.id:!network.links.some(l=>l.id===draft.id))throw Error('The applied link could not be confirmed. Check saved links before trying again.');
   reviewDraft=null;linkPhase='applied';linkList();$('link-view-scans').hidden=false;$('link-view-scans').textContent='View positioned scans';
   $('link-merge-tools').hidden=false;
   try{const est=await post('/api/scan-registration',{action:'merge_estimate',id:draft.a});showMergeEstimate(est);}catch(_){$('link-merge-space').textContent='Merged-cloud estimate is unavailable; registration itself is already saved.';}
   linkStatus('Geometry applied successfully',`${draft.network?draft.solution.scan_count:(new Set([...(draft.fixed||[]),...(draft.moving||[])])).size} connected setups are virtually positioned. No transformed or merged XYZ cache was created.`,'success');
   window.WorkStatus?.show('Registration complete','Connected setups are positioned by lightweight rigid transforms; full-density source caches were not rewritten.');await window.SetupCameraUI?.exit(false);await window.JobUI.sync({inventoryOnly:true});
  }catch(e){linkPhase='solved';linkStatus('Geometry was not applied',e.message,'error');}
  finally{linkControls();}
 };
 function bytes(v){if(!Number.isFinite(v))return '—';const units=['B','KB','MB','GB','TB'];let n=v,i=0;while(n>=1024&&i<units.length-1){n/=1024;i++;}return n.toFixed(i>1?1:0)+' '+units[i];}
 function renderOldMerges(){const box=$('link-old-merges');if(!box)return;const rows=network.merge_caches||[];box.innerHTML=rows.length?'<p class="tiny"><b>Existing physical merged caches</b></p>'+rows.map(x=>`<div class="tiny">${esc(x.name)} · ${fmt(x.setups,0)} setups · ${bytes(x.cache_bytes)}${x.stale?' · previous registration':''}${x.edited?' · edited/protected':` · <button data-release-merge="${esc(x.id)}">Release cache</button>`}</div>`).join(''):'';for(const b of box.querySelectorAll('[data-release-merge]')){b.disabled=linkRunning();b.onclick=async()=>{const x=rows.find(v=>v.id===b.dataset.releaseMerge);if(linkRunning()||!x||x.edited)return;if(!confirm(`Release ${x.name}? This deletes only its derived merged cache (${bytes(x.cache_bytes)}). The individual registered setups and links remain.`))return;try{const out=await post('/api/scan-registration',{action:'release_merge',id:x.id});await refresh();mergeEstimate=null;showConnectedMerge();window.JobUI?.sync({inventoryOnly:true});linkStatus('Merged cache released',`${bytes(out.reclaimed_bytes)} reclaimed.${out.protected_bytes?' '+bytes(out.protected_bytes)+' remains referenced by another saved project or Undo.':''} Registered setup coordinates and links were kept.`,'success');}catch(e){linkStatus('Merged cache was not released',e.message,'error');}};}}
 function mergeEstimateText(est){return `Physical merge: ${fmt(est.setups,0)} setups · up to ${fmt(est.points,0)} points · about ${bytes(est.required_free_bytes)} free required (including the workspace reserve) · ${bytes(est.free_bytes)} currently free.${est.enough?'':' Free more disk space before building the merged cloud.'}`;}
 function showMergeEstimate(est){mergeEstimate=est;showConnectedMerge();}
 $('link-build-merge').onclick=async()=>{
  if(linkRunning())return;const id=$('link-fixed').value,ids=connectedIds(id),known=new Set((network.scans||[]).map(x=>x.id));
  if(!id||ids.size<2||![...ids].every(x=>known.has(x)))return;
  mergeRunning=true;linkControls();linkStatus('Checking merged-cloud size…','Checking the connected setups and available disk space.','running');
  try{
   const est=await post('/api/scan-registration',{action:'merge_estimate',id});
   if(est.scan_id!==id||est.setups!==ids.size)throw Error('The connected setup group changed. Close and reopen Register setups before merging.');
   showMergeEstimate(est);
   if(!est.enough)throw Error('Not enough free space to build a merged cloud. Registration is already saved; free disk space or continue working with the positioned setups.');
   if(!confirm(`Build a physical merged cloud from ${est.setups} connected setups now? This can use about ${bytes(est.required_free_bytes)} of free disk space. Current saved positions will be used. Your original setups and registration links remain.`)){linkStatus('Merged cloud not started','The positioned setups and existing results are unchanged.');return;}
   linkStatus('Building merged cloud…','Writing a full-density Registered cloud for Clean Tunnel and Survey analysis.','running');
   const started=await post('/api/scan-registration',{action:'build_merge',id});
   const job=await window.JobUI.waitReady(p=>{if(p.busy)linkStatus('Building merged cloud…',p.message||'Writing merged points…','running');},{skipSync:true});
   if(job?.cancelled){linkStatus('Merged cloud cancelled','Registration remains applied; no completed merged cloud was published.');return;}
   await refresh();const merged=network.last_merge,cache=(network.merge_caches||[]).find(x=>x.id===merged?.id);
   if(!started.registration_id||merged?.registration_id!==started.registration_id||merged.setups!==est.setups||!cache||cache.stale||cache.archived)throw Error('The completed merged cloud could not be confirmed. Check the Project Explorer before building again.');
   await window.SetupCameraUI?.exit(false);await window.JobUI.sync({inventoryOnly:true});window.JobUI.selectScan(merged.id);
   $('link-view-scans').hidden=false;$('link-view-scans').textContent='View merged cloud';
   linkStatus('Merged cloud ready',`${est.setups} setups were copied into one Registered cloud, now selected in the Project Explorer. Close this window and choose Clean Tunnel or Survey analysis.`,'success');
   window.WorkStatus?.show('Registered cloud ready','Select Clean Tunnel or Survey analysis to process the merged scan.');
  }catch(e){linkStatus('Merged cloud was not built',e.message,'error');}
  finally{mergeRunning=false;linkControls();}
 };
 $('link-view-scans').onclick=async()=>{$('scan-links-dialog').close();window.TunnelRibbon?.view('job');await window.JobUI.sync();$('job-load-preview').click();window.TargetUI.sync().catch(e=>notice(e.message,true));};
 async function solveSelected(){if(await openLinks())linkStatus('Ready to register','Shared target names are matched automatically. Register setups adjusts the connected setups together.');}
 function leicaStatus(title,message,kind='info'){
  $('leica-feedback-title').textContent=title;$('leica-message').textContent=message;
  $('leica-feedback').dataset.kind=kind;$('leica-progress').hidden=kind!=='running';
 }
 function selectedSetups(){return [...$('leica-project-summary').querySelectorAll('input:checked')].map(x=>x.dataset.leicaSetup);}
 function leicaControls(){
  if(leica?.format==='rtc360_raw'&&leicaPhase==='ready'){window.LeicaBridgeUI?.controls();return;}
  const running=['inspecting','importing'].includes(leicaPhase),complete=leicaPhase==='complete';
  for(const id of ['leica-folder-browse','leica-project-path','leica-inspect'])$(id).disabled=running;
  $('leica-position-mode').disabled=running||complete||leica?.format==='rtc360_raw';
  for(const input of $('leica-project-summary').querySelectorAll('input'))input.disabled=running||complete||!leica?.setups.find(x=>x.guid===input.dataset.leicaSetup)?.available;
  $('leica-import').disabled=running||complete||!leica||leica.format==='rtc360_raw'||!selectedSetups().length;
  $('leica-import').textContent=leicaPhase==='importing'?'Importing setups…':complete?'Imported successfully':'Import selected setups';
  $('leica-view-imported').hidden=!complete;
 }
 function resetLeica(){
  window.LeicaBridgeUI?.reset();
  inspectionVersion++;leica=null;leicaReceipt=null;leicaPhase='idle';$('leica-project-summary').replaceChildren();$('leica-completed-list').replaceChildren();$('leica-completed-list').hidden=true;
  leicaStatus('Inspect this project to list its setups','Click Inspect project, then choose the setups to import.');leicaControls();
 }
 async function inspectLeica(){
  if(['inspecting','importing'].includes(leicaPhase))return;
  resetLeica();const version=inspectionVersion,path=$('leica-project-path').value;
  if(!path.trim()){leicaStatus('Choose a project folder','Choose a folder or paste its full local path.','error');return;}
  leicaPhase='inspecting';leicaStatus('Inspecting project…','Reading setup names and checking which scan files are available.','running');leicaControls();
  try{
   const found=await post('/api/leica-project',{action:'inspect',path});if(version!==inspectionVersion)return;
   if(found.format==='leica_database_choices'){
    leica=null;leicaPhase='choose_database';
    $('leica-project-summary').innerHTML='<p>Choose the saved REGISTER 360 project to inspect:</p>'+found.databases.map((db,i)=>`<button type="button" data-leica-database="${i}">${esc(db.project_title||'REGISTER 360 project')} · saved database ${i+1}<small>${esc(db.relative)}</small></button>`).join('');
    for(const button of $('leica-project-summary').querySelectorAll('[data-leica-database]'))button.onclick=()=>{$('leica-project-path').value=found.databases[+button.dataset.leicaDatabase].path;return inspectLeica();};
    leicaStatus('Choose a project database',found.search_incomplete?'The folder search reached its limit. Choose one of the databases found, or choose a closer project folder.':'Several saved databases were found. Select the project above; the setup list will open automatically.');return;
   }
   leica=found;leicaPhase='ready';
   if(leica.format==='rtc360_raw'){
    $('leica-project-summary').innerHTML='<p><b>Original RTC360 scanner data</b></p>'+leica.setups.map(x=>`<p><b>${esc(x.name||'Unnamed setup')}</b> · ${x.error?esc(x.error):`${fmt(x.scan_slices,0)} scan slices · ${fmt(x.photographs,0)} camera images${x.missing?.length?' · '+fmt(x.missing.length,0)+' missing files':' · all referenced files present'}`}</p>`).join('');
    leicaPhase='ready';window.LeicaBridgeUI.present(leica);return;
   }
   $('leica-project-summary').innerHTML=`<h3>${esc(leica.project_title||'REGISTER 360 project')}</h3><p class="tiny">${esc(leica.name_note||'Saved Leica project metadata')}</p><details><summary>Source database</summary><p class="project-path">${esc(leica.database)}</p></details>`+leica.setups.map(x=>`<label><input type="checkbox" data-leica-setup="${esc(x.guid)}" ${x.available?'checked':'disabled'}>${esc(x.name)} · ${x.targets.length} saved targets · ${x.available?fmt(x.bytes/1e6,1)+' MB':'FILE MISSING'}</label>`).join('');
   for(const el of $('leica-project-summary').querySelectorAll('input'))el.onchange=leicaControls;
   const available=leica.setups.filter(x=>x.available).length;
   leicaStatus(`${available} setup${available===1?'':'s'} available`,available?'Select the setups above, then click Import selected setups.':'No scan files are available. Check that SetupsFolder is present and its files are stored on this PC.',available?'info':'error');
  }catch(e){if(version!==inspectionVersion)return;leica=null;leicaPhase='error';leicaStatus('Project inspection failed',e.message,'error');}
  finally{if(version===inspectionVersion)leicaControls();}
 }
 $('leica-project-path').oninput=resetLeica;$('leica-inspect').onclick=inspectLeica;
 $('leica-folder-browse').onclick=async()=>{
  try{
   const bridge=window.pywebview?.api;
   if(!bridge?.choose_project_folder){leicaStatus('Paste the folder path','In browser mode, paste the full local project folder path above, then click Inspect project.');$('leica-project-path').focus();return;}
   const path=await bridge.choose_project_folder();if(path){$('leica-project-path').value=path;await inspectLeica();}
  }catch(e){leicaStatus('Could not choose folder',e.message,'error');}
 };
 $('leica-import').onclick=async()=>{
  if(!leica||['importing','inspecting','complete'].includes(leicaPhase)||!selectedSetups().length)return;
  const setups=selectedSetups();leicaPhase='importing';leicaControls();
  leicaStatus('Importing selected setups…','Starting import. Progress will appear here.','running');
  try{
   await window.TargetUI.saveIfDirty();
   const started=await post('/api/leica-project',{action:'import',path:leica.database||$('leica-project-path').value,database_sha256:leica.database_sha256,setups,mode:$('leica-position-mode').value});
   const job=await window.JobUI.waitReady(progress=>{if(progress.busy)leicaStatus('Importing selected setups…',progress.message||'Reading scan points…','running');},{skipSync:true});
   if(job?.cancelled){leicaPhase='ready';leicaStatus('Import cancelled','The project import was cancelled. Check Scans & models before trying again.');return;}
   const receipt=(await api('/api/leica-project')).last_import;
   if(!started.import_id||receipt?.id!==started.import_id)throw Error('The import result could not be confirmed. Check Scans & models before importing again.');
   leicaReceipt=receipt;leicaPhase='complete';
   leicaStatus(`Imported successfully — ${receipt.count} setup${receipt.count===1?'':'s'}`,`${fmt(receipt.point_count,0)} points imported. Click View imported scans to close this window and open them in the workspace.`,'success');
   $('leica-completed-list').innerHTML=receipt.items.map(x=>`<li>${esc(x.name)} · ${fmt(x.count,0)} points</li>`).join('');$('leica-completed-list').hidden=false;
   window.WorkStatus?.show('Leica project imported',`${receipt.count} setups · ${fmt(receipt.point_count,0)} points`);
   window.JobUI.sync({inventoryOnly:true}).catch(()=>{});
  }catch(e){leicaPhase='error';leicaStatus('Import did not complete or could not be confirmed',e.message,'error');}
  finally{leicaControls();}
 };
 $('leica-view-imported').onclick=async()=>{
  if(!leicaReceipt)return;$('leica-project-dialog').close();window.TunnelRibbon?.view('job');
  try{await window.JobUI.sync();window.JobUI.selectScan(leicaReceipt.items[0]?.id);await window.JobUI.loadPreview();window.TargetUI.sync().catch(e=>notice(e.message,true));}
  catch(e){notice('The import was saved, but the preview could not be opened: '+e.message,true);}
 };
 async function openSetup(id){
  try{
   if(typeof window.SetupCameraUI?.open!=='function')throw Error('The 3D setup view did not load. Close Tunnel Studio, run the latest update again, then restart.');
   return await window.SetupCameraUI.open(id);
  }catch(e){
   const detail=e?.message||String(e);if($('scene-info-dialog')?.open)$('scene-info-dialog').close();
   $('job-progress').textContent='Setup view could not open: '+detail;
   window.WorkStatus?.show('Setup view could not open',detail,false,true);notice('Setup view could not open: '+detail);return false;
  }
 }
 async function openLeica(path){
  if(busy||['inspecting','importing'].includes(leicaPhase))return false;
  $('leica-project-path').value=path;resetLeica();
  if(!$('leica-project-dialog').open)$('leica-project-dialog').showModal();
  await inspectLeica();return true;
 }
 window.ScanRegistrationUI={openLeica,activateTargets,refresh,openLinks,openSetup,activate,solveSelected,reviewPending:()=>openLinks(null,true)};refresh().catch(()=>{});
})();
