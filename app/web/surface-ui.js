/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const panelRenderer=window.TunnelSegmentReview;
 let data={sources:[],result:null}, preview=null, sourcePreview=null, sourceOrigin=[0,0,0], previewMode='result', visualSelection=null, selectingArea=false, selectionStart=null, clickStart=null, inspected=null, page=0, loading=false, previewId='', pendingSource=null,actionPending=false,externalSelection=null,lastWarningResult='';
 const pageSize=40;
 let selectedClusters=new Set(),hiddenClusters=new Set(),isolatedClusters=null,reviewError='',outputPending=false;
 let recessDetailEpoch=0,recessDetailKey='',recessDetailTask=null,recessDetailLoading=false,featureSelectionEpoch=0;
 function resetRecessDetail(){recessDetailEpoch++;recessDetailKey='';recessDetailTask=null;recessDetailLoading=false;}
 function selectedRecesses(){return (data.result?.clusters||[]).filter(c=>selectedClusters.has(c.id)&&['bolt_hole','bolt_fitting','lining_departure'].includes(c.feature_kind)).map(c=>c.id).sort((a,b)=>a-b);}
 function recessDetailReady(){const r=data.result,ids=selectedRecesses();return !ids.length||recessDetailKey===r.id+':'+r.revision+':'+ids.join(',')&&preview?.result_id===r?.id&&preview?.revision===r?.revision&&ids.every(id=>preview.detail?.clusters.includes(id));}
 function loadRecessPoints(){
  const r=data.result,ids=selectedRecesses();
  if(!r||!ids.length){resetRecessDetail();selectionInfo();return Promise.resolve(false);}
  const key=r.id+':'+r.revision+':'+ids.join(',');
  if(recessDetailKey===key)return recessDetailTask||Promise.resolve(recessDetailReady());
  const epoch=++recessDetailEpoch;recessDetailKey=key;recessDetailLoading=true;reviewError='';selectionInfo();updateReviewControls();
  recessDetailTask=(async()=>{
   try{
    const next=await post('/api/surface-preview',{result_id:r.id,revision:r.revision,clusters:ids});
    if(epoch!==recessDetailEpoch||data.result?.id!==r.id||data.result?.revision!==r.revision)return false;
    preview=next;previewId=r.id+':'+r.revision;sourcePreview=null;previewMode='result';sourceOrigin=preview.origin||[0,0,0];
    sceneSurface.setCloud(preview);showReview();return true;
   }catch(error){if(epoch===recessDetailEpoch){recessDetailKey='';reviewError=error.message;$('surf-progress').textContent=error.message;}return false;}
   finally{if(epoch===recessDetailEpoch){recessDetailLoading=false;selectionInfo();updateReviewControls();}}
  })();return recessDetailTask;
 }
 async function selectFeaturePoints(object,options={}){
  if(busy||actionPending)return false;const epoch=++featureSelectionEpoch;
  try{
   if(window.CadUI?.modelSpace()&&window.CadUI.enter('Edit')===false)return false;
   window.TunnelRibbon?.view('surface',{refresh:false});await sync();if(epoch!==featureSelectionEpoch)return false;
   const r=data.result,ids=object.cluster_ids||[object.cluster_id];
   if(!r||r.id!==object.result_id||!ids.every(id=>r.clusters.some(c=>c.id===id&&['bolt_hole','bolt_fitting','lining_departure'].includes(c.feature_kind))))throw Error('This recess guide belongs to an unavailable analysis. Run bolt-hole analysis on the current scan again.');
   if(r.source_item_id)window.JobUI?.chooseItem(r.source_item_id);window.SceneObjectsUI?.clearSelected();
   window.TunnelRibbon?.surfaceDrawerMode('review');$('tool-drawer-title').textContent='Bolt-hole point selection';reveal(ids);isolatedClusters=null;$('surf-isolate').checked=false;
   if(typeof options.checked==='boolean'){const next=new Set(selectedClusters);for(const id of ids)options.checked?next.add(id):next.delete(id);selectClusters([...next]);}
   else selectClusters(ids,!!options.additive);
   $('surf-cluster-selection-info').focus({preventScroll:true});
   await loadRecessPoints();if(epoch===featureSelectionEpoch&&ids.length===1&&!options.additive&&selectedClusters.has(ids[0]))window.JobUI?.focusCleaningCluster(preview,ids[0],r.clusters.find(c=>c.id===ids[0])?.centre);
   return true;
  }catch(error){notice(error.message,true);return false;}
 }
 function deletePatches(r,ids){
  const chosen=new Set(ids),count=r.clusters.reduce((total,c)=>total+(chosen.has(c.id)?c.points:0),0);
  if(!count||!confirm(`Delete ${fmt(count,0)} points from ${r.source_name}?\n\nUndo restores the deleted points.`))return false;
  return action({action:'delete_patches',revision:r.revision,clusters:ids});
 }
 function deleteSelectedGroups(){
  const r=data.result,ids=selectedRecesses();
  if(r?.settings?.method==='lining_features'&&ids.length===selectedClusters.size&&ids.length){
   if($('surf-delete-patches').disabled){notice(reviewError||'Load and review the selected bolt-feature points before deleting them.',true);return false;}
   return deletePatches(r,ids);
  }
  return markClusters(false);
 }
 let syncTask=null,pendingArea=null,initialSync=true;
 const fields=[...document.querySelectorAll('#tab-surface [data-setting]')];
 const clusterPalette=[[.22,.68,.98],[.98,.70,.18],[.20,.82,.65],[.65,.50,.96],[.98,.52,.24],[.57,.79,.22],[.32,.82,.88],[.92,.75,.52],[.43,.57,.92],[.75,.79,.34],[.64,.64,.91],[.35,.76,.48]];
 let colourRows=null,colourRowsById=new Map();
 const clusterColours=new Map();
 function categoricalColour(key){
  if(key<=clusterPalette.length)return clusterPalette[(key-1)%clusterPalette.length];
  if(clusterColours.has(key))return clusterColours.get(key);
  const h=(key*.618033988749895)%1,s=.48+((key*37)%31)/100,v=.78+((key*19)%20)/100;
  const i=Math.floor(h*6),f=h*6-i,p=v*(1-s),q=v*(1-f*s),t=v*(1-(1-f)*s);
  const colour=[[v,t,p],[q,v,p],[p,v,t],[p,q,v],[t,p,v],[v,p,q]][i%6];
  clusterColours.set(key,colour);return colour;
 }
 const colourMode=()=>$('surf-colour-by')?.value||'decisions';
 function featureColour(id){
  const r=data.result,mode=colourMode();
  if(r?.clusters!==colourRows){colourRows=r?.clusters;colourRowsById=new Map((colourRows||[]).map(c=>[c.id,c]));clusterColours.clear();}
  const row=colourRowsById.get(id);
  if(r?.settings?.method==='tmatch'&&mode!=='decisions'){
   if(!row||Number(row.cleaning_class||0)!==0||id===0)return [.65,.68,.72];
   const key=mode==='rings'?Number(row.ring_id||0):id;
   return key>0?categoricalColour(key):[.65,.68,.72];
  }
  if(row?.feature_kind==='segment'&&$('surf-panel-colour').value==='vivid')return panelRenderer.vivid(id);
  return r?.settings?.method==='lining_features'&&id>=4?clusterPalette[id%clusterPalette.length]:[.32,.82,.64];
 }
 function classificationView(){
  const r=data.result,isTmatch=r?.settings?.method==='tmatch',mode=colourMode();
  $('surf-classification-view').hidden=!isTmatch;
  const hasRings=isTmatch&&r.clusters.some(c=>Number(c.ring_id)>0);
  $('surf-colour-by').querySelector('[value="rings"]').disabled=!hasRings;
  if(mode==='rings'&&!hasRings)$('surf-colour-by').value='clusters';
  $('surf-classification-key').textContent=colourMode()==='decisions'?'Green: kept. Red: exclusion candidates. Magenta: selected.':colourMode()==='rings'?'Categorical colours show supported rings. Select a group to inspect its ID. Grey: unresolved or retained boundary points. Red: excluded candidates. Magenta: selected.':'Categorical colours show lining clusters. Select a group to inspect its ID. Grey: unresolved or retained boundary points. Red: excluded candidates. Magenta: selected. Colours show geometric groups; cable and pipe types are not inferred.';
 }
 const kept=()=>new Set(data.result?.kept||[]);
 let lowConfidenceIds=new Set();
 function panelResult(r){return r?.settings?.method==='lining_features'&&['segments','all'].includes(r.settings.lining_task);}
 function layoutFields(){
  const active=$('surf-method').value==='lining_features'&&['segments','all'].includes($('surf-lining-task').value);
  $('surf-panel-layout').hidden=!active;
  const count=$('surf-panel-sector-count');count.disabled=!active;
  count.setCustomValidity(active&&Number(count.value)===1?'Use 0 for measured joints only, or 2–24 segments per ring.':'');
  $('surf-panel-sector-offset').disabled=!active||Number(count.value)===0;
 }
 function panelReview(r){
  lowConfidenceIds=new Set();const active=panelResult(r);$('surf-panel-review').hidden=!active;
  if(!active){panelRenderer.configure(null,new Set(),new Set());return;}
  const rows=r.clusters.filter(c=>c.feature_kind==='segment');
  const minimum=+$('surf-panel-confidence').value,counts={high:0,medium:0,low:0,unrated:0};
  for(const row of rows){
   const band=Object.hasOwn(counts,row.segment_confidence)?row.segment_confidence:'unrated';counts[band]++;
   if(minimum&&panelRenderer.score(row)<minimum)lowConfidenceIds.add(row.id);
  }
  const d=r.detection||r.statistics||{},bandCounts=d.segment_confidence_counts||counts;
  const message=`${rows.length} panels across ${Number(d.intervals_with_segments||0)} of ${Number(d.observed_ring_intervals||0)} ring intervals. ${bandCounts.high||0} high · ${bandCounts.medium||0} medium · ${bandCounts.low||0} low${counts.unrated?' · '+counts.unrated+' unrated':''}.`;
  $('surf-panel-summary').textContent=message+(minimum?` ${rows.length-lowConfidenceIds.size} panels pass the display filter.`:'');
  const hidden=new Set([...lowConfidenceIds,...hiddenClusters]);const keep=kept();
  for(const row of rows){if((isolatedClusters&&!isolatedClusters.has(row.id))||($('surf-isolate').checked&&inspected!==null&&row.id!==inspected)||!(keep.has(row.id)?$('surf-show-kept'):$('surf-show-other')).checked)hidden.add(row.id);}
  panelRenderer.configure(r,hidden,new Set(selectedClusters));
 }

 function analysisSummary(r){
  if(r?.settings?.method==='tmatch')return r.classification?.summary||`${r.statistics.lining_clusters||0} connected lining groups. Use Segments from seams to inspect panel boundaries.`;
  if(r?.settings?.method!=='lining_features')return '';
  const s=r.statistics,task=r.settings.lining_task;
  if(r.detection?.summary)return r.detection.summary;
  if(task==='segments')return s.segment_candidates?`${s.segment_candidates} panel candidates in observed ring intervals. Inspect each boundary in Review.`:'No supported segment panels were found. Inspect seam support and scan coverage; unassigned points remain retained.';
  if(task==='bolts')return s.bolt_holes?`${s.bolt_holes} recess candidates to inspect. These are holes or pockets in the lining.`:'No supported recess candidates were found at these settings. Check Feature shape, depth, size and scan spacing.';
  if(task==='seams')return `${s.ring_seams||0} ring seams and ${s.longitudinal_seams||0} between-segment seam candidates. Check their measured support in the review.`;
  return `${s.bolt_holes||0} recess candidates · ${s.ring_seams||0} ring seams · ${s.segment_candidates||0} segment candidates. Review the named groups.`;
 }
 const num=(id)=>{const el=$(id);if(el.value.trim()===''||!Number.isFinite(+el.value))throw Error('Enter a number for '+(el.closest('label')?.textContent.trim()||id));return +el.value;};
 function isVisible(p,keep){if(previewMode==='source')return true;const use=keep.has(p[3]);return !lowConfidenceIds.has(p[3])&&!hiddenClusters.has(p[3])&&(!isolatedClusters||isolatedClusters.has(p[3]))&&(!$('surf-isolate').checked||inspected===null||p[3]===inspected)&&(use?$('surf-show-kept').checked:p[3]===-1?$('surf-show-noise').checked:$('surf-show-other').checked);}
 function inVisualSelection(p){if(!visualSelection||previewMode!=='source')return false;const q=[p[0]+sourceOrigin[0],p[1]+sourceOrigin[1],p[2]+sourceOrigin[2]];return q.every((v,i)=>v>=visualSelection.minimum[i]&&v<=visualSelection.maximum[i]);}
 class SurfaceScene extends CloudScene{
  setCloud(d){this.data=d;this.recolour();if(!this.initialised&&this.c.clientWidth>0){this.initialised=true;this.fit();}}
  recolour(){if(!this.gl||!this.data)return;const values=[],keep=kept(),showKept=$('surf-show-kept').checked,showOther=$('surf-show-other').checked,showNoise=$('surf-show-noise').checked,isolate=$('surf-isolate').checked;
   for(const p of this.data.points){
    if(previewMode==='source'){const c=inVisualSelection(p)?[1,.9,.25]:[.26,.72,.82];values.push(...p.slice(0,3),...c);continue;}
    const use=keep.has(p[3]);if(lowConfidenceIds.has(p[3])||hiddenClusters.has(p[3])||(isolatedClusters&&!isolatedClusters.has(p[3])))continue;if(isolate&&inspected!==null&&p[3]!==inspected)continue;if(use?!showKept:p[3]===-1?!showNoise:!showOther)continue;
    const c=selectedClusters.has(p[3])?[1,85/255,221/255]:use?featureColour(p[3]):[1,.25,.25];values.push(...p.slice(0,3),...c);}
   this.upload(this.buffer,values);this.pointCount=values.length/6;this.draw();
  }
  fit(cluster=null){if(!this.data)return;let points=cluster===null?this.data.points:this.data.points.filter(p=>p[3]===cluster);if(!points.length){notice('This small cluster has no points in the display sample. Its full-density count is still included in review and export.',true);return;}
   const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const q of points)for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],q[j]);hi[j]=Math.max(hi[j],q[j]);}this.target=lo.map((v,j)=>(v+hi[j])/2);this.yaw=-.55;this.pitch=.62;const b=this.basis(),aspect=this.c.clientWidth/Math.max(1,this.c.clientHeight);let extent=.15;for(const q of points){const v=q.slice(0,3).map((x,j)=>x-this.target[j]),dot=a=>v.reduce((n,x,j)=>n+x*a[j],0);extent=Math.max(extent,Math.abs(dot(b.right)),Math.abs(dot(b.up))*aspect);}this.scale=extent*1.16;this.draw();
  }
  draw(){if(this.data&&!this.initialised&&this.c.clientWidth>0){this.initialised=true;this.fit();return;}const old=$('point-size').value;$('point-size').value=$('surf-point-size').value;super.draw();$('point-size').value=old;}
 }
 const sceneSurface=new SurfaceScene($('surf-canvas'));
 const cleaningPresets={fast:{noise_neighbours:8,resolution:.30,normal_neighbours:10,envelope_angle_deg:6},detailed:{noise_neighbours:12,resolution:.20,normal_neighbours:15,envelope_angle_deg:3},fine:{noise_neighbours:24,resolution:.10,normal_neighbours:25,envelope_angle_deg:1.5}};
 function presetHelp(){const key=$('surf-preset').value;$('surf-preset-help').textContent=({fast:'Quicker neighbourhood checks and larger surface patches.',detailed:'Balanced checks for everyday use.',fine:'More neighbours and smaller surface patches; usually takes longer.',custom:'Your advanced settings are in use.'}[key]||'')+' Every point inside the processing area is included. More detail does not mean more accurate measurements or stronger removal.';}
 function presetFromSettings(){const match=Object.entries(cleaningPresets).find(([k,p])=>Object.entries(p).every(([name,value])=>+fields.find(f=>f.dataset.setting===name)?.value===value));$('surf-preset').value=match?.[0]||'custom';presetHelp();}
 function selectionChanged(spec){if($('surf-area').value==='screen_polygon'){externalSelection=spec?JSON.parse(JSON.stringify(spec)):null;areaFields();}}
 const hasLimit=()=>!!data.sources.find(m=>m.key===$('surf-source').value)?.has_limit_box;
 function areaFields(){$('surf-box').hidden=$('surf-area').value!=='box';$('surf-chainage').hidden=$('surf-area').value!=='chainage';if($('surf-area').value==='screen_polygon')$('surf-area-estimate').textContent=externalSelection?'Only points inside the current 3D selection will be cleaned. The source file is read to find their exact count.':'Draw a box or lasso in the main 3D workspace first.';else $('surf-area-estimate').textContent=$('surf-area').value==='limit_box'?'Processes retained points inside the saved box even when the whole scan is visible.':$('surf-area').value==='full'?'All retained points in this source scan will be cleaned.':'Only retained points inside this processing area will be cleaned.';}
 function sourceInfo(){const m=data.sources.find(m=>m.key===$('surf-source').value);$('surf-source-info').textContent=m?`${m.name} · ${fmt(m.count,0)} original points${m.synthetic?' · SYNTHETIC SCENE':''}. Existing exclusions are respected. Coordinates must be metres.`:'Select a scan in the main 3D workspace. No alignment is needed.';
  $('surf-source').querySelectorAll('option').forEach(o=>o.title=o.textContent);
  const area=$('surf-area'),limit=area.querySelector('[value=limit_box]');limit.hidden=limit.disabled=!hasLimit();if(!hasLimit()&&area.value==='limit_box')area.value='full';
  const ch=area.querySelector('[value=chainage]');ch.disabled=m?.key!=='survey';if(ch.disabled&&$('surf-area').value==='chainage')$('surf-area').value='full';areaFields();setBusySurface(busy);
 }
 function fillBox(){const m=data.sources.find(m=>m.key===$('surf-source').value);if(!m)return;['e','n','h'].forEach((axis,j)=>{$('surf-min-'+axis).value=(m.minimum[j]-.000001).toFixed(6);$('surf-max-'+axis).value=(m.maximum[j]+.000001).toFixed(6);});}
 function settings(){const c={};for(const f of fields){if(!f.checkValidity())throw Error('Check '+f.closest('label').textContent.trim());c[f.dataset.setting]=+f.value;}c.lining_bolt_kind=$('surf-bolt-kind').value;c.lining_task=$('surf-lining-task').value;c.lining_detail=$('surf-lining-detail').value;c.method=$('surf-method')?.value||'gusac';c.denoise_shape=$('surf-denoise-shape').value;c.interior_cleanup=$('surf-interior-cleanup').value;c.repair=$('surf-repair').checked;c.graph_mode=$('surf-graph').value;c.convexity_mode=$('surf-convexity').value;c.interior=$('surf-use-interior').checked?['e','n','h'].map(k=>num('surf-interior-'+k)):null;return c;}
 function fillSettings(c){$('surf-bolt-kind').value=c.lining_bolt_kind||'recess';$('surf-lining-task').value=c.lining_task||'all';$('surf-lining-detail').value=c.lining_detail||'detailed';for(const f of fields)if(c[f.dataset.setting]!==undefined)f.value=c[f.dataset.setting];if($('surf-method'))$('surf-method').value=c.method||'gusac';$('surf-denoise-shape').value=c.denoise_shape||'measured';$('surf-interior-cleanup').value=c.interior_cleanup||'standard';$('surf-repair').checked=c.repair;$('surf-graph').value=c.graph_mode;$('surf-convexity').value=c.convexity_mode;$('surf-use-interior').checked=!!c.interior;['e','n','h'].forEach((k,j)=>{$('surf-interior-'+k).value=c.interior?.[j]??'';$('surf-interior-'+k).disabled=!c.interior;});methodInfo();presetFromSettings();}
 function fillSelection(a){externalSelection=null;if(a.mode==='oriented_box'){externalSelection=JSON.parse(JSON.stringify(a));if(!$('surf-area').querySelector('[value=oriented_box]')){const o=document.createElement('option');o.value='oriented_box';o.textContent='Current rotated limit box';$('surf-area').append(o);}$('surf-area').value='oriented_box';areaFields();return;}if(['screen_polygon','screen_brush'].includes(a.mode)){externalSelection=JSON.parse(JSON.stringify(a));ensureScreenOption();$('surf-area').value='screen_polygon';areaFields();return;}$('surf-area').value=a.mode;if(a.mode==='box')['e','n','h'].forEach((k,j)=>{$('surf-min-'+k).value=a.minimum[j];$('surf-max-'+k).value=a.maximum[j];});if(a.mode==='chainage'){$('surf-ch-start').value=a.start;$('surf-ch-end').value=a.end;}areaFields();}
 function selection(){const mode=$('surf-area').value;if(mode==='oriented_box'){if(!externalSelection)throw Error('Open Clean Tunnel again after setting the limit box.');return externalSelection;}if(mode==='screen_polygon'){const live=window.JobUI?.currentSelection?.();if(live)externalSelection=live;if(!externalSelection)throw Error('Draw a selection in the main 3D workspace first.');return externalSelection;}if(mode==='box')return {mode,minimum:['e','n','h'].map(k=>num('surf-min-'+k)),maximum:['e','n','h'].map(k=>num('surf-max-'+k))};if(mode==='chainage')return {mode,start:num('surf-ch-start'),end:num('surf-ch-end')};return {mode};}
 function ensureScreenOption(){if(!$('surf-area').querySelector('[value=screen_polygon]')){const o=document.createElement('option');o.value='screen_polygon';o.textContent='Current main 3D selection';$('surf-area').prepend(o);}}
 function useJobSelection(spec){externalSelection=spec?JSON.parse(JSON.stringify(spec)):null;if(externalSelection){ensureScreenOption();$('surf-area').value='screen_polygon';areaFields();$('surf-area-estimate').textContent='Using the exact box/lasso selection from the main 3D workspace.';}}
 function sensitivityInfo(){const value=+$('surf-irregular-sensitivity').value,d=.12*10**(-value/100);$('surf-irregular-thresholds').textContent=`${value}% sensitivity · departures over ${(d*1000).toFixed(1)} mm or local roughness over ${(d*500).toFixed(1)} mm. Higher sensitivity finds smaller changes and may flag more scan noise.`;}
 function boltTypeInfo(){
  const departure=$('surf-lining-task').value==='departures';
  $('surf-bolt-kind').closest('label').hidden=departure;
  for(const key of ['lining_bolt_height_mm','lining_bolt_max_mm','lining_bolt_min_points'])$('tab-surface').querySelector('[data-setting="'+key+'"]').closest('label').hidden=departure;
  if(departure){$('surf-bolt-depth-label').textContent='Lining tolerance on both sides · ± mm';$('surf-bolt-kind-help').textContent='Every supported point farther than this distance above or below the fitted lining is a candidate. No bolt-size filter is used.';return;}
  const raised=$('surf-bolt-kind').value==='raised';
  $('surf-bolt-depth-label').textContent=raised?'Minimum height above the surrounding wall · mm':'Minimum pocket depth below the surrounding wall · mm';
  $('surf-bolt-kind-help').textContent=raised?'Find compact bumps extending from the lining into the tunnel. Height is measured from nearby wall.':'Find compact dips behind the nearby wall, including recessed bolt pockets. Depth is measured into the pocket; the bolt head can remain below the wall.';
 }
 function configureBolts(cfg){
  $('surf-lining-task').value=cfg.feature_kind==='departures'?'departures':'bolts';$('surf-method').value='lining_features';$('surf-bolt-kind').value=cfg.feature_kind==='departures'?'recess':cfg.feature_kind;
  for(const [key,value] of Object.entries({lining_depth_mm:cfg.min_height_mm,lining_bolt_height_mm:cfg.feature_kind==='departures'?0:cfg.max_height_mm,lining_bolt_max_mm:cfg.max_diameter_mm,lining_bolt_min_points:cfg.min_points})){
   const field=$('tab-surface').querySelector('[data-setting="'+key+'"]');if(field)field.value=value;
  }
  $('surf-area').value='full';externalSelection=null;areaFields();methodInfo();
  $('surf-area-estimate').textContent='Whole retained source cloud is streamed in blocks. Choose Saved limit box to restrict processing.';
 }
 $('surf-bolt-kind').onchange=boltTypeInfo;
 function setMethod(method,task=null){if(task)$('surf-lining-task').value=task;$('surf-method').value=method;methodInfo();window.TunnelRibbon?.surfaceDrawerMode('setup');}
 function methodInfo(){boltTypeInfo();layoutFields();const feature=$('surf-method').value==='lining_features';$('surf-feature-settings').hidden=!feature;$('tab-surface').dataset.featureAnalysis=String(feature);if($('tab-surface').classList.contains('drawer-active'))$('tool-drawer-title').textContent=feature?'Lining analysis':'Clean Tunnel';$('surf-preset').closest('label').hidden=feature;$('surf-preset-help').hidden=feature;const method=$('surf-method').value,noise=method==='noise_clean',combined=method==='combined_denoise',reflection=method==='reflection',ray=method==='ray_box';const paper=method==='gusac'||method==='tmatch';$('surf-gusac-settings').hidden=!paper;$('surf-tmatch-settings').hidden=method!=='tmatch';$('surf-preset').closest('label').hidden=feature||paper;$('surf-preset-help').hidden=feature||paper;$('surf-reflection-settings').hidden=!reflection;$('surf-ray-settings').hidden=!ray;$('surf-interior-settings').hidden=method!=='tunnel_envelope';$('surf-interior-band').disabled=$('surf-interior-cleanup').value!=='thorough';$('surf-denoise-settings').hidden=!combined;$('surf-irregular-settings').hidden=method!=='irregular';sensitivityInfo();$('surf-method-help').textContent=method==='gusac'?'GuSAC fits local lining patches and checks surface direction to separate services from the measured tunnel. Every selected original point receives a decision. Joint recesses and uncertain geometry remain available for review.':method==='tmatch'?'GuSAC cleaning followed by connected lining groups. Nearby points can connect across joints, so one cluster can contain several physical panels. Use Segments from seams for panel boundaries; ring labels require observed support.':feature?'Unwraps one supported tunnel branch, detects compact recessed pockets or raised fittings and measured seam evidence, and divides panels between detected boundaries. Uncertain and unresolved regions remain visible. Review measured seams and any boundaries inferred from your segment layout.':reflection?'Finds likely reflection artefacts using supported lining, mirrored geometry, known scanner position and available intensity. Weak evidence stays retained. Review candidates before excluding them; extraction behaviour is unchanged.':ray?'Uses outward radial rays and neighbouring station/angle boxes to find supported lining behind interior pipes and equipment. Unsupported boxes remain retained. Review recesses, junctions and rough SCL before applying.':combined?'First checks neighbour support, then fits the tunnel lining. Green is retained; red groups distinguish isolated points and departures from the lining. Review and keep real features before applying. Every selected retained point is checked.':method==='irregular'?'Highlights connected protrusions, recesses and rough patches relative to a supported lining model. Every retained point in the chosen area is checked. The source is unchanged until you choose Delete selected patches.':noise?'Flags sparse points and local spacing outliers. Radius is in metres; choose it to suit the actual scan spacing. Smaller support counts and larger threshold multipliers are more conservative. Review joints and small details.':method==='tunnel_envelope'?'Learns one lining shape across the selected tunnel, then checks every original point. Green is lining to keep; red is interior objects, stray points or areas needing review. Include both walls and the crown of one branch.':'SMRG groups surface patches and grows connected regions. This is an independent research implementation; inspect the suggested lining.';$('surf-noise-settings').hidden=!(noise||combined);$('surf-lining-settings').hidden=method!=='tunnel_envelope';$('surf-lining-advanced').hidden=method!=='tunnel_envelope'&&!combined&&!reflection&&!ray;$('surf-patch-settings').hidden=paper||feature||noise||reflection||ray||method==='irregular';$('surf-advanced').hidden=method!=='smrg';$('surf-advanced').open=method==='smrg';updateRunControls();}
 function showReview(){panelReview(data.result);classificationView();groupSummary();const active=(document.body.classList.contains('surface-active')||window.DetachedUI?.active?.('tab-surface'))&&$('tab-surface').dataset.drawerMode!=='setup';if(active&&preview&&data.result)window.JobUI?.cleaningPreview(preview,[...kept()],{colours:(data.result?.settings?.method==='lining_features'||(data.result?.settings?.method==='tmatch'&&colourMode()!=='decisions'))?Object.fromEntries(data.result.clusters.map(c=>[c.id,featureColour(c.id)])):null,classification:data.result?.settings?.method==='tmatch',colourMode:colourMode(),hidden:[...new Set([...hiddenClusters,...lowConfidenceIds])],selected:[...selectedClusters],onlySelected:isolatedClusters?[...isolatedClusters]:null,showKept:$('surf-show-kept').checked,showOther:$('surf-show-other').checked,showNoise:$('surf-show-noise').checked,inspected,isolate:$('surf-isolate').checked});else window.JobUI?.clearCleaningPreview('review');}
 function markSelection(keep){const r=data.result,spec=window.JobUI?.currentSelection?.();if(!r||!spec){showTunnelMessage('Select points first','Draw a Box or Lasso in the coloured cleaning preview, then choose Keep selected points or Remove selected points.',true);return;}action({action:'review_selection',revision:r.revision,selection:spec,keep});}

 function setInteriorPoint(world){if(!world?.every(Number.isFinite))return;$('surf-use-interior').checked=true;['e','n','h'].forEach((k,j)=>{$('surf-interior-'+k).disabled=false;$('surf-interior-'+k).value=world[j].toFixed(4);});$('surf-progress').textContent='Interior point set from the main 3D view. Press Preview cleaning to rerun.';showTunnelMessage('Interior point set','Tunnel Studio will use this point to resolve which side of the scanned geometry is the tunnel interior.',false);}
 function pickInterior(){showTunnelMessage('Pick inside the tunnel','After Continue, drag to rotate the scan, Shift-drag or right-drag to pan, and use the wheel to zoom. Click without dragging inside the tunnel void to choose the point. Press Escape to cancel. Choose the empty space inside, away from the lining; the point is placed at the current view-centre depth and is only used to orient the surface.',false,'Continue',()=>{const ok=window.JobUI?.armWorldPick?.(setInteriorPoint);if(ok===false)showTunnelMessage('3D preview required','Load the 3D preview first, then try again.',true);});}
 function reviewActive(){return !!data.result&&(document.body.classList.contains('surface-active')||window.DetachedUI?.active?.('tab-surface'))&&$('tab-surface').dataset.drawerMode!=='setup';}
 function clusters(){const r=data.result;if(!r)return [];return [...r.clusters,...(r.statistics.noise_points>0?[{id:-1,name:'Density noise',points:r.statistics.noise_points,overlap:0}]:[])];}
 function filteredClusters(){const query=$('surf-filter').value.trim(),group=$('surf-group-filter').value,keep=kept();return clusters().filter(c=>!lowConfidenceIds.has(c.id)&&(!query||String(c.id)===query||String(c.name||'').toLowerCase().includes(query.toLowerCase()))&&(group==='all'||keep.has(c.id)===(group==='tunnel')));}
 function groupSummary(){
  const r=data.result;$('surf-group-summary').hidden=!r;
  for(const id of ['surf-view-tunnel','surf-view-noise','surf-view-both'])$(id).disabled=busy||loading||actionPending||!r;
  if(!r)return;
  const keep=kept(),pieces=clusters(),total=r.statistics.input_points,tunnel=r.reviewed_kept_points,noise=total-tunnel;
  $('surf-tunnel-count').textContent=`${fmt(tunnel,0)} points`;$('surf-noise-count').textContent=`${fmt(noise,0)} points`;
  for(const [id,n,part] of [['tunnel',tunnel,true],['noise',noise,false]])$(`surf-${id}-pieces`).textContent=`${pieces.filter(c=>keep.has(c.id)===part).length} pieces · ${fmt(total?n/total*100:0,1)}% of processed points`;
  const method=r.settings?.method||'smrg',irregular=method==='irregular';$('surf-keep-title').textContent=irregular?'Retained / unflagged':'Tunnel / keep';$('surf-remove-title').textContent=irregular?'Candidate irregularities':'Noise or unwanted / remove';$('surf-view-tunnel').textContent=irregular?'Show retained only':'Show tunnel only';$('surf-view-noise').textContent=irregular?'Show candidates only':'Show noise only';$('surf-keep-clusters').textContent=irregular?'Keep selected patches':'Keep as tunnel';$('surf-remove-clusters').textContent=irregular?'Exclude from cleaned copy':'Mark as noise / remove';$('surf-group-help').textContent=irregular?'Red = candidate irregular patches. Green = retained, including unassessed areas. Click a patch or its row, then Hide, Show or Delete selected patches. Hiding affects this review; deletion is reversible with Undo.':'Green points go into the tunnel output; red points stay out. Select a piece and choose Keep as tunnel or Mark as noise. Counts include hidden pieces.';
  $('surf-result-explanation').textContent=method==='ray_box'?'Ray/box extraction chooses an outer supported radial mode in each station/angle neighbourhood. Wall returns behind pipes can remain green while nearer objects are red. Unsupported boxes stay green. Inspect openings, recesses, junctions, reflections and rough lining before applying.':method==='reflection'?'Red points have several reflection cues and need review. Green includes real geometry, weak evidence and unassessed areas. The detector can miss reflections and can flag real recesses or objects. Check the evidence and data availability in the report; use Keep selected points to protect real features.':method==='combined_denoise'?'Combined denoising checks neighbour support and the fitted lining. Off-lining sparse points and surface departures are separate red groups. Sparse points matching the wall and uncertain areas stay green. Use Keep selected points or Keep as tunnel to preserve joints, recesses and other details you need.':irregular?'Find irregular surfaces compares points with the surrounding lining and checks local roughness. The named patches are review candidates; joints, equipment and noise can also be detected. It cannot confirm a defect or guarantee every irregularity.':method==='noise_clean'?'Remove stray points checks how well each point is supported by its neighbours. It does not identify all equipment or isolate the lining; choose Clean tunnel in Setup for that task.':method==='tunnel_envelope'?'Clean tunnel compares every original point with one supported lining model. Red groups distinguish interior objects and outside points. Areas with insufficient lining support stay green. Review the red points before creating the cleaned scan; keep any real features you need.':'Group surface patches (SMRG) joins nearby surface pieces and suggests a connected tunnel surface to keep. Detached pieces are suggested for removal; they can still contain real tunnel points.';
  if(method==='lining_features'){$('surf-keep-title').textContent='Retained geometry';$('surf-remove-title').textContent='Exclusion candidates';$('surf-view-tunnel').textContent='Show retained';$('surf-view-noise').textContent='Show excluded';$('surf-keep-clusters').textContent='Keep selected groups';$('surf-remove-clusters').textContent='Exclude selected groups';$('surf-group-help').textContent='Feature colours distinguish groups; magenta highlights your selection and red marks exclusions. Select named bolt, seam or segment rows to isolate or export their original points. Detection retains all groups by default; only Non-lining removal proposes exclusions.';$('surf-result-explanation').textContent=`${r.statistics.bolt_holes||0} bolt recesses · ${r.statistics.bolt_fittings||0} raised fittings · ${r.statistics.ring_seams||0} ring seams · ${r.statistics.longitudinal_seams||0} between-segment seams · ${r.statistics.segment_candidates||0} segment candidates. Inspect support and coordinates in CSV/JSON reports. Missing seams may leave merged segments; local station is not design chainage.`;}
  if(method==='lining_features'&&r.settings.lining_task==='departures'){
   $('surf-result-explanation').textContent='Groups describe points within the fitted wall band, raised into the tunnel, recessed beyond the wall, and unassessed areas. They are lining departures, not detected bolt identities. Broad selected wall areas indicate a reference/tolerance mismatch: review before excluding. Selecting a row only highlights it; use Exclude selected groups to change the output.';
   $('surf-group-help').textContent='Selection (magenta) and Keep/Exclude are separate decisions. All groups are retained initially. With zero exclusions, output contains the same retained points; it does not remove pockets.';
  }
  if(method==='reflection'){$('surf-keep-title').textContent='Retained / weak evidence';$('surf-remove-title').textContent='Reflection candidates';$('surf-view-tunnel').textContent='Show retained only';$('surf-view-noise').textContent='Show candidates only';$('surf-keep-clusters').textContent='Keep selected points';$('surf-remove-clusters').textContent='Exclude selected candidates';$('surf-group-help').textContent='Red = likely reflection candidates for review. Green = retained, including weak evidence. Select and inspect a group, then keep or exclude it. The scan changes only after a reviewed apply.';}
  if(method==='gusac'||method==='tmatch'){
   $('surf-keep-title').textContent='Retained lining / review areas';$('surf-remove-title').textContent='Obstruction candidates';
   $('surf-view-tunnel').textContent='Show retained only';$('surf-view-noise').textContent='Show candidates only';
   $('surf-keep-clusters').textContent='Keep selected groups';$('surf-remove-clusters').textContent='Exclude selected groups';
   $('surf-group-help').textContent=method==='tmatch'&&colourMode()!=='decisions'?$('surf-classification-key').textContent+' Use Keep/Exclude to change the output; hiding only changes the view.':'Green includes supported lining and uncertain geometry retained for review. Red marks proposed exclusions. Keep/Exclude changes the cleaned output; hiding only changes the view.';
   $('surf-result-explanation').textContent=method==='gusac'?'Local RANSAC lining planes and surface normals separate inward obstruction candidates. Recesses, joint edges and weak support have their own retained review groups. Inspect these groups before applying; no source coordinates are moved.':analysisSummary(r)+' Select a coloured group to inspect or isolate it. Ring numbers describe supported local ordering; project segment types require the design layout.';
  }
  if(method==='tunnel_envelope'&&r.settings?.interior_cleanup==='thorough')$('surf-result-explanation').textContent+=' More thorough cleanup is on: inspect the separate Extra interior candidates group before excluding it.';
  const custom=hiddenClusters.size||isolatedClusters||($('surf-isolate').checked&&inspected!==null),green=$('surf-show-kept').checked,red=$('surf-show-other').checked&&$('surf-show-noise').checked;
  const view=custom?'custom':green&&red?'both':green&&!$('surf-show-other').checked&&!$('surf-show-noise').checked?'tunnel':!green&&red?'noise':'custom';
  $('surf-group-view').textContent=({both:'Showing both groups',tunnel:'Showing tunnel / keep only',noise:'Showing noise / remove only',custom:'Custom view — some pieces are hidden'}[view]);
  for(const id of ['tunnel','noise','both'])$(`surf-view-${id}`).setAttribute('aria-pressed',String(view===id));
  $('surf-output-summary').textContent=`Create one scan named “Tunnel · ${r.source_name}” containing all ${fmt(tunnel,0)} kept points from the processed area. ${fmt(noise,0)} excluded points stay outside the new scan. It combines the kept pieces into one scan.`;
 }
 function showGroup(group){
  if(busy||loading||actionPending||!data.result)return;
  selectedClusters.clear();hiddenClusters.clear();isolatedClusters=null;inspected=null;$('surf-isolate').checked=false;
  $('surf-show-kept').checked=group!=='noise';$('surf-show-other').checked=$('surf-show-noise').checked=group!=='tunnel';
  $('surf-group-filter').value=group==='both'?'all':group;$('surf-filter').value='';page=0;
  window.JobUI?.clearSelection?.();window.JobUI?.setMode?.('orbit');sceneSurface.recolour();showReview();table();
 }
 function selectionInfo(){const list=clusters().filter(c=>selectedClusters.has(c.id)),detail=preview?.detail;const note=selectedRecesses().length&&recessDetailLoading?' · Loading measured recess points…':selectedRecesses().length&&detail&&recessDetailReady()?` · ${fmt(detail.displayed_points,0)} ${data.result?.settings?.lining_task==='departures'?'departure':'recess'} points displayed${detail.complete?' · full recess detail':' · display sample; deletion uses every selected original point'}`:'';$('surf-cluster-selection-info').textContent=`${list.length} selected ${list.length===1?'group':'groups'} · ${fmt(list.reduce((n,c)=>n+c.points,0),0)} original points · magenta in 3D${note}`;}
 function updateReviewControls(){
  groupSummary();
  const blocked=busy||loading||actionPending,r=data.result,chosen=selectedClusters.size>0;const del=$('surf-delete-patches');del.hidden=!['irregular','lining_features'].includes(r?.settings?.method);del.textContent=r?.settings?.method==='lining_features'?(r.settings.lining_task==='departures'?'Delete selected departure points':'Delete selected bolt-feature points'):'Delete selected patches';$('surf-select-bolts').textContent=r?.settings?.lining_task==='departures'?'Select lining departures':'Select all bolt-feature candidates';$('surf-select-bolts').hidden=r?.settings?.method!=='lining_features';del.disabled=blocked||recessDetailLoading||!recessDetailReady()||!r?.can_delete_patches||!chosen||[...selectedClusters].some(id=>{const c=r.clusters.find(c=>c.id===id);return !c?.irregular_candidate&&!['bolt_hole','bolt_fitting','lining_departure'].includes(c?.feature_kind);});del.title=r?.can_delete_patches?'Delete only the selected candidate patches from the source scan. Undo restores them.':'Run detection on the current scan before deleting patches.';
  for(const id of ['surf-keep-clusters','surf-remove-clusters','surf-hide-clusters','surf-show-clusters','surf-isolate-clusters','surf-clear-clusters'])$(id).disabled=blocked||!r||!chosen;
  $('surf-separate-patches').hidden=del.hidden;$('surf-separate-patches').disabled=del.disabled;
  if($('surf-segments-from-seams'))$('surf-segments-from-seams').disabled=blocked||!r;
  $('surf-select-page').disabled=blocked||!r||!filteredClusters().length;$('surf-show-all-clusters').disabled=blocked||!r;
  for(const id of ['surf-continue','surf-continue-quick'])if($(id)){$(id).disabled=blocked||!r?.reviewed_kept_points;$(id).title=blocked?'Wait for the current operation.':!r?'Run extraction first.':!r.reviewed_kept_points?'No points are kept. Keep a cluster or restore the automatic suggestion.':'Continue to review confirmation and create a cleaned scan.';}
  $('surf-prev').disabled=blocked||page===0;$('surf-next').disabled=blocked||(page+1)*pageSize>=filteredClusters().length;
  for(const el of $('surf-table').querySelectorAll('input,button'))el.disabled=blocked;
  const status=$('surf-review-status');status.hidden=$('tab-surface').dataset.drawerMode==='setup'||!r;
  const error=reviewError||state?.job?.error;status.classList.toggle('error',!!error);
  status.textContent=error|| (blocked?(loading?'Loading review…':state?.job?.message||'Saving review…'):!r?'Run extraction first.':!r.reviewed_kept_points?'No points are kept. Keep a cluster or restore the automatic suggestion before continuing.':$('tab-surface').dataset.drawerMode==='output'?`${fmt(r.reviewed_kept_points,0)} points ready. Tick the review confirmation, then Create one tunnel scan.`:['lining_features','tmatch'].includes(r.settings?.method)?analysisSummary(r):r.settings?.method==='irregular'?`${fmt(r.statistics.irregular_patches,0)} candidate patches. Select rows to inspect, hide or delete from the source. Undo restores deleted points.`:`${fmt(r.reviewed_kept_points,0)} points kept for the tunnel. Inspect the excluded and uncertain groups, then Continue to Output.`);
 }
 function selectClusters(ids,additive=false){
  if(busy||loading||actionPending)return;const valid=new Set(clusters().map(c=>c.id));
  if(!additive)selectedClusters.clear();for(const id of ids)if(valid.has(id)){if(additive&&selectedClusters.has(id))selectedClusters.delete(id);else selectedClusters.add(id);}
  inspected=selectedClusters.size===1?[...selectedClusters][0]:null;if(selectedClusters.size)$('surf-cluster-details').open=true;
  window.JobUI?.clearSelection?.();window.JobUI?.setMode?.('orbit');sceneSurface.recolour();showReview();table();loadRecessPoints();
 }
 function reveal(ids){for(const id of ids){hiddenClusters.delete(id);if(kept().has(id))$('surf-show-kept').checked=true;else $(id===-1?'surf-show-noise':'surf-show-other').checked=true;}}
 function inspect(id,zoom=false){
  selectClusters([id]);inspected=id;reveal([id]);isolatedClusters=null;
  const c=clusters().find(c=>c.id===id);$('surf-inspected').textContent=c?`Inspecting ${id===-1?'density noise':'cluster '+id} · ${fmt(c.points,0)} points · ${data.result?.settings?.method==='irregular'?(kept().has(id)?'RETAINED (green)':'CANDIDATE (red)'):(kept().has(id)?'TUNNEL / KEEP (green)':'NOISE / REMOVE (red)')}.`:'Choose a cluster to inspect.';
  sceneSurface.recolour();showReview();table();
  if(zoom)loadRecessPoints().then(()=>{if(inspected===id)window.JobUI?.focusCleaningCluster?.(preview,id,c?.centre);});
 }
 function clusterView(mode){
  if(busy||loading||actionPending)return;
  if(mode==='all'){hiddenClusters.clear();isolatedClusters=null;$('surf-isolate').checked=false;for(const id of ['surf-show-kept','surf-show-other','surf-show-noise'])$(id).checked=true;}
  else{if(!selectedClusters.size){showTunnelMessage('Select clusters first','Select cluster rows in Review or click a cluster in the main 3D view.',true);return;}
   if(mode==='hide')for(const id of selectedClusters)hiddenClusters.add(id);
   else {reveal(selectedClusters);if(mode==='isolate'){isolatedClusters=new Set(selectedClusters);$('surf-isolate').checked=false;}else isolatedClusters=null;}}
  window.JobUI?.clearSelection?.();sceneSurface.recolour();showReview();table();
 }
 async function markClusters(keep){
  if(!selectedClusters.size){showTunnelMessage('Select clusters first','Select cluster rows in Review, or draw a Box/Lasso to edit part of the result.',true);return false;}
  const ids=kept();for(const id of selectedClusters)keep?ids.add(id):ids.delete(id);
  return saveReview(ids);
 }
 function continueToOutput(){if($('surf-continue').disabled)return;window.TunnelRibbon?.surfaceDrawerMode?.('output');$('surf-output-card').scrollIntoView({block:'start'});}
 function table(){
  const r=data.result;if(!r){$('surf-table').innerHTML='<tr><td colspan="7">Run cleaning to review the pieces.</td></tr>';selectionInfo();updateReviewControls();return;}
  const valid=new Set(clusters().map(c=>c.id));selectedClusters=new Set([...selectedClusters].filter(id=>valid.has(id)));hiddenClusters=new Set([...hiddenClusters].filter(id=>valid.has(id)));
  if(isolatedClusters)isolatedClusters=new Set([...isolatedClusters].filter(id=>valid.has(id)));
  const all=filteredClusters();page=Math.max(0,Math.min(page,Math.ceil(all.length/pageSize)-1));const list=all.slice(page*pageSize,(page+1)*pageSize),keep=kept();
  $('surf-table').innerHTML=list.map(c=>`<tr data-review-cluster="${c.id}" tabindex="0" aria-selected="${selectedClusters.has(c.id)}"><td><input type="checkbox" data-select-cluster="${c.id}" aria-label="Select cluster ${c.id}" ${selectedClusters.has(c.id)?'checked':''}></td><td><input type="checkbox" data-cluster="${c.id}" aria-label="Keep cluster ${c.id}" ${keep.has(c.id)?'checked':''}></td><td><b>${c.id}</b> ${esc(c.name||'')}${c.feature_kind==='segment'&&c.segment_confidence?' <small>'+esc(c.segment_confidence)+' confidence'+(c.inferred?' · inferred layout':'')+'</small>':''}${c.automatic_keep?' <small>auto</small>':''}${hiddenClusters.has(c.id)?' <small>hidden</small>':''}${selectedClusters.has(c.id)?' <span class="surface-selected-badge" data-selected-badge>Selected</span>':''}</td><td><span class="surface-group-badge ${keep.has(c.id)?'tunnel':'noise'}" data-cluster-group="${keep.has(c.id)?'tunnel':'noise'}">${['irregular','reflection','lining_features'].includes(r.settings?.method)?(keep.has(c.id)?'Retained':'Candidate'):(keep.has(c.id)?'Tunnel / keep':'Noise / remove')}</span></td><td>${fmt(c.points,0)}</td><td>${r.settings?.method==='smrg'?fmt(c.overlap*100,1)+'%':'—'}</td><td><button data-inspect="${c.id}">Inspect</button></td></tr>`).join('')||'<tr><td colspan="7">No pieces match this ID and group.</td></tr>';
  for(const row of $('surf-table').querySelectorAll('[data-review-cluster]')){row.onclick=e=>{if(!e.target.closest('input,button'))selectClusters([+row.dataset.reviewCluster],e.ctrlKey||e.metaKey);};row.onkeydown=e=>{if(e.target===row&&['Enter',' '].includes(e.key)){e.preventDefault();selectClusters([+row.dataset.reviewCluster],e.ctrlKey||e.metaKey);}};}
  for(const b of $('surf-table').querySelectorAll('[data-inspect]'))b.onclick=()=>inspect(+b.dataset.inspect,true);
  for(const c of $('surf-table').querySelectorAll('[data-select-cluster]'))c.onchange=()=>{selectClusters([+c.dataset.selectCluster],true);$('surf-cluster-selection-info').focus({preventScroll:true});};
  for(const c of $('surf-table').querySelectorAll('[data-cluster]'))c.onchange=()=>{const ids=kept();c.checked?ids.add(+c.dataset.cluster):ids.delete(+c.dataset.cluster);saveReview(ids);};
  $('surf-page-info').textContent=all.length?`${page*pageSize+1}–${Math.min((page+1)*pageSize,all.length)} of ${fmt(all.length,0)} clusters`:'0 matching clusters';selectionInfo();updateReviewControls();
 }
 function quality(){const r=data.result,stats=r?.statistics;$('surf-total').textContent=stats?fmt(stats.input_points,0):'—';$('surf-kept').textContent=r?fmt(r.reviewed_kept_points,0):'—';$('surf-clusters').textContent=stats?fmt(stats.clusters,0):'—';$('surf-time').textContent=stats?fmt(stats.seconds,1)+' s':'—';
  $('surf-keep-noise').checked=kept().has(-1);$('surf-warnings').textContent=r?[`Result source: ${r.source_name} · area: ${r.selection.mode}.`,...r.warnings,...(r.settings?.method==='smrg'?['Boundary overlap is a geometric merging test, not a confidence score.']:[])].join('\n'):'Select a tunnel area in the main 3D view, then run extraction. Review the suggested lining before applying.';
  $('surf-apply-info').textContent=r?.source_changed?'The source scan changed after cleaning. Run cleaning again before creating or exporting a cleaned scan.':r?.can_apply?'Ready to apply to the unchanged active survey. Points outside the processed area are preserved.':r?'Create a cleaned scene scan or export the kept points. To apply exclusions directly, extract from the current active survey without changing its points afterwards.':'Applying becomes available after extracting from the active survey.';
  $('surf-evaluation').hidden=!r?.evaluation;if(r?.evaluation){const e=r.evaluation;$('surf-evaluation-values').textContent=`Current review: intersection over union ${fmt(e.iou*100,2)}%; precision ${(e.precision===null?'—':fmt(e.precision*100,2))}%; recall ${(e.recall===null?'—':fmt(e.recall*100,2))}%. TP ${fmt(e.tp,0)}, FP ${fmt(e.fp,0)}, FN ${fmt(e.fn,0)}.`;}
  table();setBusySurface(busy);
 }
 function sync(){
  if(busy)return Promise.resolve(false);
  if(syncTask)return syncTask;
  syncTask=refreshSurface().finally(()=>{syncTask=null;});return syncTask;
 }
 async function selectSource(key,area=null){
  // An earlier GET may have captured the old source list. Wait for it, then
  // request fresh metadata after the scene-source action has finished.
  if(syncTask)await syncTask;
  pendingSource=key;pendingArea=area?JSON.parse(JSON.stringify(area)):{mode:'full'};
  return sync();
 }
 async function refreshSurface(){loading=true;setBusySurface(busy);try{
   const requestedSource=pendingSource,requestedArea=pendingArea;
   const next=await api('/api/surface');if(busy)return false;const before=data.result,oldSource=pendingSource||$('surf-source').value;data=next;
   $('surf-source').innerHTML=data.sources.map(m=>`<option value="${m.key}">${esc(m.name)}</option>`).join('')||'<option value="">Select a scan in the main workspace first</option>';if(data.sources.some(m=>m.key===oldSource))$('surf-source').value=oldSource;else if(data.sources.some(m=>m.key==='separate'))$('surf-source').value='separate';
   sourceInfo();
   const r=data.result,changed=before?.id!==r?.id,reviewChanged=before?.revision!==r?.revision;if(changed||reviewChanged)resetRecessDetail();
   if(changed){$('surf-panel-confidence').value='0';selectedClusters.clear();hiddenClusters.clear();isolatedClusters=null;reviewError='';inspected=null;page=0;$('surf-filter').value='';$('surf-group-filter').value='all';$('surf-cluster-details').open=false;for(const id of ['surf-show-kept','surf-show-other','surf-show-noise'])$(id).checked=true;$('surf-reviewed').checked=false;$('surf-isolate').checked=false;sceneSurface.initialised=false;if(r){$('surf-colour-by').value=r.settings?.method==='tmatch'?'clusters':'decisions';fillSettings(r.settings);fillSelection(r.selection);if(data.sources.some(m=>m.key===r.source_kind))$('surf-source').value=r.source_kind;sourceInfo();}}
   if(changed&&r?.settings?.method==='lining_features'&&['bolts','departures'].includes(r.settings.lining_task)&&document.body.classList.contains('surface-active'))selectedClusters=new Set(r.clusters.filter(c=>['bolt_hole','bolt_fitting','lining_departure'].includes(c.feature_kind)).map(c=>c.id));
   if(requestedSource){
    if(!data.sources.some(m=>m.key===requestedSource))throw Error('The selected scan is unavailable for cleaning. Select it in the scene list and open Clean Tunnel again.');
    $('surf-source').value=requestedSource;sourcePreview=null;visualSelection=null;stopAreaSelection();
    fillBox();fillSelection(requestedArea||{mode:hasLimit()?'limit_box':'full'});sourceInfo();pendingSource=null;pendingArea=null;
   }else if(initialSync&&!r&&data.job_selection){fillSelection(data.job_selection);}
   if(reviewChanged&&r){const valid=new Set(clusters().map(c=>c.id));selectedClusters=new Set([...selectedClusters].filter(id=>valid.has(id)));hiddenClusters=new Set([...hiddenClusters].filter(id=>valid.has(id)));if(inspected!==null&&!valid.has(inspected)){inspected=null;$('surf-isolate').checked=false;}if(isolatedClusters){isolatedClusters=new Set([...isolatedClusters].filter(id=>valid.has(id)));if(!isolatedClusters.size)isolatedClusters=null;}}
   if(r&&(document.body.classList.contains('surface-active')||window.DetachedUI?.active?.('tab-surface'))&&previewId!==r.id+':'+r.revision){preview=await api('/api/surface-preview');sourcePreview=null;previewMode='result';visualSelection=null;sourceOrigin=preview.origin||[0,0,0];previewId=r.id+':'+r.revision;sceneSurface.setCloud(preview);$('surf-empty').style.display='none';$('surf-preview-info').textContent=`Displaying ${fmt(preview.sample_count,0)} of ${fmt(preview.source_count,0)} processed points. Every processed point is used for cluster review and export.`;document.querySelector('.surface-legend').style.opacity='1';}
   else if(!r&&!sourcePreview){preview=null;previewId='';previewMode='result';sceneSurface.clear();$('surf-empty').style.display='flex';}
   showReview();if(reviewChanged||before?.source_changed!==r?.source_changed){$('surf-reviewed').checked=false;sceneSurface.recolour();}quality();
   if(r&&changed){window.TunnelRibbon?.surfaceDrawerMode?.('review');}if(r&&changed&&r.id!==lastWarningResult&&r.warnings?.length){lastWarningResult=r.id;const important=r.warnings.filter(w=>!/memory-safe batches|review required/i.test(w)).slice(0,3);if(important.length){const hasInterior=r.settings?.method==='smrg'&&important.some(w=>/interior.*(?:reference|point)|pick.*interior/i.test(w));showTunnelMessage('Surface extraction needs review',important.join('\n\n'),false,hasInterior?'Pick interior point':null,hasInterior?pickInterior:null);}}
   if(!state?.job?.error){const deletion=r?.review_log?.at(-1);$('surf-progress').textContent=deletion?.action==='delete_patches'?`Deleted ${fmt(deletion.points,0)} points from the scan. Undo or Deleted restores them.`:['lining_features','tmatch'].includes(r?.settings?.method)?analysisSummary(r):r?.settings?.method==='irregular'?`Detection ready · ${r.statistics.irregular_patches} candidate patches. Select a patch to inspect, hide or delete.`:r?`Extraction saved · ${r.source_name}. Inspect the result, then export or apply.`:'Choose a source, then extract. The source stays unchanged.';}
   else $('surf-progress').textContent=state.job.error;
   if(initialSync&&!r&&data.defaults)fillSettings(data.defaults);if(initialSync&&r?.settings?.method==='noise_clean'&&r.method?.startsWith('Clean Tunnel: radius support'))fillSettings({...r.settings,method:'tunnel_envelope',envelope_thickness:.08,envelope_quantile:.70});initialSync=false;if(outputPending&&!state?.job?.busy){outputPending=false;if(!state?.job?.error)window.TunnelRibbon?.view('job');}return true;
  }catch(e){notice(e.message,true);$('surf-progress').textContent=e.message;return false;}finally{loading=false;setBusySurface(busy);if(!busy&&selectedRecesses().length)loadRecessPoints();}}
 async function action(body){
  if(actionPending||busy||loading)return false;actionPending=true;reviewError='';
  try{setBusy(true);await post('/api/surface-action',body);if(body.action==='add_clean_scan')outputPending=true;await poll();return !state?.job?.error;}
  catch(e){setBusy(false);reviewError=e.message;$('surf-progress').textContent=e.message;if(/interior point|inside the tunnel/i.test(e.message))showTunnelMessage('Interior point required',e.message,true,'Pick interior point',pickInterior);else notice(e.message,true);return false;}
  finally{actionPending=false;table();setBusySurface(busy);}
 }
 async function saveReview(ids){if(!data.result||busy||loading||actionPending)return false;$('surf-reviewed').checked=false;return action({action:'review',revision:data.result.revision,kept:[...ids]});}
 function updateRunControls(blocked=busy||loading||actionPending){
  const method=$('surf-method')?.value||'gusac',source=$('surf-source').value;
  const label=method==='gusac'?'Preview GuSAC cleaning':method==='tmatch'?'Classify tunnel lining':method==='lining_features'?'Analyse lining':method==='reflection'?'Find reflection artefacts':method==='irregular'?'Find irregular surfaces':(method==='noise_clean'||method==='combined_denoise')?'Preview cleaning':(method==='tunnel_envelope'||method==='ray_box')?'Extract lining':'Extract surface patches';
  const areaLabel=$('surf-area').value==='limit_box'?' inside limit box':'';
  const reason=blocked?(loading?'Loading cleaning sources and preview…':state?.job?.message||'Starting cleaning…'):!source?'Select a scan in the scene list and open Clean Tunnel.':'Run '+label.toLowerCase()+' on the selected source and processing area.';
  for(const id of ['surf-run','surf-run-quick']){const b=$(id);if(b){b.disabled=!!blocked||!source;b.textContent=label+areaLabel;b.title=reason;}}
  $('tab-surface').setAttribute('aria-busy',String(!!blocked));
 }
 function setBusySurface(v){v=v||loading||actionPending;for(const id of ['surf-hide-source','surf-browse-path','surf-import','surf-demo','surf-read-path','surf-preview-source','surf-select-3d','surf-defaults','surf-original','surf-keep-noise','surf-source','surf-area','surf-reviewed'])$(id).disabled=v;
  for(const f of document.querySelectorAll('.surface-controls input,.surface-controls select,.surface-controls button'))if(f.id!=='surf-cancel')f.disabled=v;
  ['e','n','h'].forEach(k=>$('surf-interior-'+k).disabled=v||!$('surf-use-interior').checked);
  $('surf-interior-band').disabled=v||$('surf-interior-cleanup').value!=='thorough';
  updateRunControls(v);
  $('surf-cancel').disabled=!(state?.job?.busy&&state?.job?.kind==='surface');
  const r=data.result;for(const id of ['surf-export','surf-export-other','surf-report','surf-json','surf-csv','surf-original','surf-keep-noise','surf-keep-selected','surf-remove-selected'])$(id).disabled=v||!r;
  $('surf-export').disabled=v||!r||r.source_changed||!r.reviewed_kept_points;$('surf-export-other').disabled=v||!r||r.source_changed||r.reviewed_kept_points===r.statistics.input_points;
  $('surf-add-clean').disabled=v||r?.source_changed||!r?.reviewed_kept_points||!$('surf-reviewed').checked;
  $('surf-apply').disabled=v||!r?.can_apply||!$('surf-reviewed').checked||!r?.reviewed_kept_points||r.reviewed_kept_points===r.statistics.input_points;
  for(const c of $('surf-table').querySelectorAll('input'))c.disabled=v;
  updateReviewControls();
  if(v)$('surf-progress').textContent=loading?'Loading cleaning sources and preview…':state?.job?.message||'Starting…';
 }
 async function loadSourcePreview(){
  const key=$('surf-source').value;if(!key)throw Error('Choose a source scan first.');
  $('surf-progress').textContent='Preparing a lightweight source preview…';
  sourcePreview=await api('/api/surface-source-preview?'+new URLSearchParams({source:key,budget:'100000'}));
  preview=sourcePreview;previewMode='source';sourceOrigin=sourcePreview.origin||[0,0,0];previewId='source:'+key;sceneSurface.initialised=false;sceneSurface.setCloud(sourcePreview);sceneSurface.fit();
  $('surf-empty').style.display='none';$('surf-preview-info').textContent=`Source preview: ${fmt(sourcePreview.sample_count,0)} displayed points sampled from ${fmt(sourcePreview.source_count,0)} retained full-density points. Draw a rectangle to define a 3D processing box; extraction still uses every full-density point inside it.`;
  document.querySelector('.surface-legend').style.opacity='.35';
 }
 function stopAreaSelection(){selectingArea=false;selectionStart=null;$('surf-selection-box').style.display='none';document.querySelector('.surface-view').classList.remove('selecting');$('surf-select-3d').textContent='Select area in 3D';if(typeof editMode!=='undefined')editMode='orbit';}
 async function startAreaSelection(){
  if(!sourcePreview||previewMode!=='source')await loadSourcePreview();
  selectingArea=true;selectionStart=null;if(typeof editMode!=='undefined')editMode='surface-select';document.querySelector('.surface-view').classList.add('selecting');$('surf-select-3d').textContent='Drag a rectangle on the scan…';$('surf-area-estimate').textContent='Drag around the part of the scan you want to process.';
 }
 function selectedFromRectangle(start,end){
  if(!sourcePreview?.points?.length)return null;const canvas=$('surf-canvas'),r=canvas.getBoundingClientRect(),minx=Math.min(start[0],end[0]),maxx=Math.max(start[0],end[0]),miny=Math.min(start[1],end[1]),maxy=Math.max(start[1],end[1]),basis=sceneSurface.basis();let lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity],count=0;
  for(const p of sourcePreview.points){const v=[p[0]-sceneSurface.target[0],p[1]-sceneSurface.target[1],p[2]-sceneSurface.target[2]],dot=a=>v.reduce((n,x,j)=>n+x*a[j],0),px=(dot(basis.right)/sceneSurface.scale+1)*r.width/2,py=(1-dot(basis.up)*r.width/r.height/sceneSurface.scale)*r.height/2;if(px<minx||px>maxx||py<miny||py>maxy)continue;const q=[p[0]+sourceOrigin[0],p[1]+sourceOrigin[1],p[2]+sourceOrigin[2]];for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],q[j]);hi[j]=Math.max(hi[j],q[j]);}count++;}
  if(!count)return null;const span=Math.hypot(...hi.map((v,j)=>v-lo[j])),pad=Math.max(.002,span*.003);lo=lo.map(v=>v-pad);hi=hi.map(v=>v+pad);return {minimum:lo,maximum:hi,count};
 }
 async function browseSurface(){
  try{const bridge=window.pywebview?.api;if(!bridge?.choose_large_file)throw Error('The Windows file picker is unavailable in browser fallback mode. Open Tunnel Studio as the desktop application.');const paths=await bridge.choose_large_file('surface',false);if(!paths?.length)return;const path=paths[0];$('surf-path').value=path;setBusy(true);pendingSource='separate';sourcePreview=null;previewId='';visualSelection=null;await post('/api/load',{kind:'surface',path});await poll();await waitSurfaceReady();await sync();await loadSourcePreview();$('surf-progress').textContent='Scan loaded. Choose Whole retained scan, or select the processing area directly in 3D.';}catch(e){setBusy(false);notice(e.message,true);$('surf-progress').textContent=e.message;}}
 async function waitSurfaceReady(){while(true){const st=await api(jobStatusURL());updateJobSnapshot(st);if(!st.job.busy){setBusy(false);if(st.job.error)throw Error(st.job.error);return;}setBusySurface(true);$('surf-progress').textContent=st.job.message||'Loading scan…';await new Promise(r=>setTimeout(r,600));}}

 $('surf-source').onchange=()=>{sourcePreview=null;previewId='';visualSelection=null;stopAreaSelection();fillSelection({mode:hasLimit()?'limit_box':'full'});sourceInfo();fillBox();if(state?.axis){$('surf-ch-start').value=state.axis.start;$('surf-ch-end').value=state.axis.end;}updateRunControls();};
 $('surf-area').onchange=()=>{externalSelection=$('surf-area').value==='screen_polygon'?window.JobUI?.currentSelection?.():null;areaFields();if($('surf-area').value==='box'&&!$('surf-min-e').value)fillBox();if($('surf-area').value==='chainage'&&!$('surf-ch-start').value&&state?.axis){$('surf-ch-start').value=state.axis.start;$('surf-ch-end').value=state.axis.end;}};
 $('surf-fill-box').onclick=fillBox;$('surf-use-interior').onchange=()=>setBusySurface(busy);if($('surf-method'))$('surf-method').onchange=methodInfo;if($('surf-pick-interior'))$('surf-pick-interior').onclick=pickInterior;methodInfo();
 $('surf-preset').onchange=()=>{const p=cleaningPresets[$('surf-preset').value];if(p){for(const f of fields)if(p[f.dataset.setting]!==undefined)f.value=p[f.dataset.setting];}presetHelp();};for(const f of fields)f.addEventListener('change',()=>{$('surf-preset').value='custom';presetHelp();});
 $('surf-draw-box').onclick=()=>{window.JobUI?.armAreaSelection?.((spec,box)=>{fillSelection(spec);$('surf-area-estimate').textContent='Process this screen shape through the full scan depth. Choose Whole retained scan to analyse the entire source.';});};
 $('surf-defaults').onclick=()=>{fillSettings(data.defaults);notice('Default extraction settings restored. The saved result still records the settings used to produce it.');};
 $('surf-run').onclick=()=>{try{const c=settings(),area=selection();$('surf-reviewed').checked=false;action({action:'run',source:$('surf-source').value,selection:area,ignore_limit_box:area.mode==='full',settings:c});}catch(e){notice(e.message,true);$('surf-progress').textContent=e.message;}};
 $('surf-cancel').onclick=async()=>{try{await post('/api/surface-action',{action:'cancel'});$('surf-cancel').disabled=true;}catch(e){notice(e.message,true);}};
 $('surf-import').onclick=()=>$('surf-file').click();$('surf-file').onchange=async e=>{const f=e.target.files[0];if(!f)return;if(f.size>150000000){e.target.value='';showTunnelMessage('Use Browse large scan','This scan is large. Use Browse large scan so Tunnel Studio reads it directly from disk without passing it through the WebView.',true,'Browse large scan',()=>$('surf-browse-path').click());return;}try{setBusy(true);await api('/api/upload?'+new URLSearchParams({kind:'surface',name:f.name}),{method:'POST',headers:{'X-Tunnel-Token':window.TUNNEL_TOKEN},body:f});pendingSource='separate';await poll();}catch(err){setBusy(false);notice(err.message,true);}finally{e.target.value='';}};
 $('surf-read-path').onclick=async()=>{try{if(!$('surf-path').value.trim())throw Error('Paste the complete scan path first.');setBusy(true);await post('/api/load',{kind:'surface',path:$('surf-path').value});pendingSource='separate';await poll();}catch(e){setBusy(false);notice(e.message,true);}};
 $('surf-browse-path').onclick=browseSurface;$('surf-preview-source').onclick=()=>{showTunnelMessage('Use the main 3D workspace','Surface processing now uses the one main 3D viewport. Select the scan area there, then press Use main 3D selection.',false);};$('surf-select-3d').onclick=()=>{const spec=window.JobUI?.currentSelection?.();if(!spec){showTunnelMessage('Select an area first','Draw a box or lasso on the scan in the main 3D workspace, then press Use main 3D selection.',true);return;}useJobSelection(spec);$('surf-progress').textContent='Main 3D selection ready. Press Preview cleaning.';};
 $('surf-demo').onclick=()=>{pendingSource='separate';fillSettings(data.defaults);$('surf-area').value='full';areaFields();action({action:'demo'});};
 $('surf-fit').onclick=()=>sceneSurface.fit();$('surf-top').onclick=()=>{sceneSurface.pitch=Math.PI/2;sceneSurface.yaw=0;sceneSurface.draw();};$('surf-front').onclick=()=>{sceneSurface.pitch=0;sceneSurface.yaw=0;sceneSurface.draw();};$('surf-point-size').oninput=()=>sceneSurface.draw();
 $('surf-lining-task').addEventListener('change',layoutFields);
 $('surf-panel-sector-count').addEventListener('input',layoutFields);
 $('surf-panel-confidence').onchange=()=>{
  panelReview(data.result);for(const id of lowConfidenceIds)selectedClusters.delete(id);
  if(lowConfidenceIds.has(inspected)){inspected=null;$('surf-isolate').checked=false;}
  page=0;sceneSurface.recolour();showReview();table();
 };
 $('surf-panel-colour').onchange=()=>{sceneSurface.recolour();showReview();};
 for(const id of ['surf-panel-boundaries','surf-panel-numbers'])$(id).onchange=()=>window.JobUI?.draw();
 $('surf-colour-by').onchange=()=>{classificationView();sceneSurface.recolour();showReview();table();};
 for(const id of ['surf-show-kept','surf-show-other','surf-show-noise','surf-isolate'])$(id).onchange=()=>{sceneSurface.recolour();showReview();};
 $('surf-clear-inspect').onclick=()=>{selectedClusters.clear();isolatedClusters=null;inspected=null;$('surf-isolate').checked=false;visualSelection=null;sceneSurface.recolour();showReview();table();window.JobUI?.clearSelection?.();$('surf-inspected').textContent='Inspection and visible selection cleared. Click a cluster or coloured point to inspect it.';};$('surf-filter').oninput=()=>{page=0;table();};$('surf-prev').onclick=()=>{page--;table();};$('surf-next').onclick=()=>{page++;table();};
 for(const group of ['tunnel','noise','both'])$('surf-view-'+group).onclick=()=>showGroup(group);
 $('surf-group-filter').onchange=()=>{page=0;table();};
 $('surf-select-page').onclick=()=>selectClusters(filteredClusters().slice(page*pageSize,(page+1)*pageSize).map(c=>c.id));
 $('surf-clear-clusters').onclick=()=>selectClusters([]);
 $('surf-irregular-sensitivity').oninput=sensitivityInfo;$('surf-interior-cleanup').onchange=methodInfo;
 if($('surf-segments-from-seams'))$('surf-segments-from-seams').onclick=()=>setMethod('lining_features','segments');
 $('surf-select-bolts').onclick=()=>{const ids=(data.result?.clusters||[]).filter(c=>['bolt_hole','bolt_fitting','lining_departure'].includes(c.feature_kind)).map(c=>c.id);reveal(ids);isolatedClusters=null;$('surf-isolate').checked=false;selectClusters(ids);};
 $('surf-separate-patches').onclick=()=>{const r=data.result;if($('surf-separate-patches').disabled||!r)return;return action({action:'separate_patches',revision:r.revision,clusters:[...selectedClusters]});};
 $('surf-lining-task').addEventListener('change',methodInfo);
 $('surf-delete-patches').onclick=()=>{const r=data.result;if($('surf-delete-patches').disabled||!r)return;return deletePatches(r,[...selectedClusters]);};
 $('surf-keep-clusters').onclick=()=>markClusters(true);$('surf-remove-clusters').onclick=()=>markClusters(false);
 for(const [id,mode] of [['surf-hide-clusters','hide'],['surf-show-clusters','show'],['surf-isolate-clusters','isolate'],['surf-show-all-clusters','all']])$(id).onclick=()=>clusterView(mode);
 $('surf-continue').onclick=continueToOutput;
 $('surf-keep-noise').onchange=()=>{const ids=kept();$('surf-keep-noise').checked?ids.add(-1):ids.delete(-1);saveReview(ids);};
 $('surf-original').onclick=()=>data.result&&action({action:'reset_review',revision:data.result.revision});
 $('surf-keep-selected').onclick=()=>markSelection(true);$('surf-remove-selected').onclick=()=>markSelection(false);$('surf-add-clean').onclick=()=>data.result&&action({action:'add_clean_scan',revision:data.result.revision,reviewed:$('surf-reviewed').checked,hide_source:$('surf-hide-source').checked});
 $('surf-reviewed').onchange=()=>setBusySurface(busy);
 $('surf-export').onclick=()=>window.TunnelRibbon.export('surface');$('surf-export-other').onclick=()=>window.TunnelRibbon.export('surface_other');
 for(const [id,format]of [['surf-report','html'],['surf-json','json'],['surf-csv','csv']])$(id).onclick=()=>{if(data.result&&!busy)location.href='/api/surface-report?format='+format;};
 $('surf-apply').onclick=()=>{const r=data.result;if(!r?.can_apply||!$('surf-reviewed').checked)return;const excluded=r.statistics.input_points-r.reviewed_kept_points;if(!confirm(`Exclude ${fmt(excluded,0)} points inside the processed area from the active survey?\n\nAll points outside the area remain unchanged. Undo is available in Process. Rebuild heatmaps, meshes and reports afterwards.`))return;resetEditingView();action({action:'apply',revision:r.revision,reviewed:true});};
 let down=null;const surfaceCanvas=$('surf-canvas'),surfaceSelection=$('surf-selection-box');
 surfaceCanvas.addEventListener('pointerdown',e=>{const r=surfaceCanvas.getBoundingClientRect();if(selectingArea){selectionStart=[e.clientX-r.left,e.clientY-r.top];surfaceCanvas.setPointerCapture(e.pointerId);surfaceSelection.style.display='block';Object.assign(surfaceSelection.style,{left:selectionStart[0]+'px',top:selectionStart[1]+'px',width:'0px',height:'0px'});return;}down=[e.clientX,e.clientY];});
 surfaceCanvas.addEventListener('pointermove',e=>{if(!selectingArea||!selectionStart)return;const r=surfaceCanvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;Object.assign(surfaceSelection.style,{left:Math.min(x,selectionStart[0])+'px',top:Math.min(y,selectionStart[1])+'px',width:Math.abs(x-selectionStart[0])+'px',height:Math.abs(y-selectionStart[1])+'px'});});
 surfaceCanvas.addEventListener('pointerup',e=>{
  if(selectingArea&&selectionStart){const r=surfaceCanvas.getBoundingClientRect(),end=[e.clientX-r.left,e.clientY-r.top],start=selectionStart;selectionStart=null;surfaceSelection.style.display='none';if(Math.abs(end[0]-start[0])<4||Math.abs(end[1]-start[1])<4)return;const picked=selectedFromRectangle(start,end);if(!picked){$('surf-area-estimate').textContent='No displayed points were inside that rectangle. Try a larger area.';notice('No displayed points were inside that rectangle. Zoom in or draw a larger selection.',true);return;}visualSelection={minimum:picked.minimum,maximum:picked.maximum};$('surf-area').value='box';areaFields();['e','n','h'].forEach((k,j)=>{$('surf-min-'+k).value=picked.minimum[j].toFixed(4);$('surf-max-'+k).value=picked.maximum[j].toFixed(4);});const approx=Math.min(sourcePreview.source_count,Math.round(sourcePreview.source_count*picked.count/Math.max(1,sourcePreview.sample_count)));$('surf-area-estimate').textContent=`3D area selected · ${fmt(picked.count,0)} preview points · roughly ${fmt(approx,0)} full-density points before exact processing.`;sceneSurface.recolour();stopAreaSelection();return;
  }
  if(busy||previewMode!=='result'||!preview||!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;const box=surfaceCanvas.getBoundingClientRect(),x=e.clientX-box.left,y=e.clientY-box.top,b=sceneSurface.basis();let best=null,dist=100,depth=-Infinity;const keep=kept();
  for(const p of preview.points){if(!isVisible(p,keep))continue;const v=p.slice(0,3).map((n,i)=>n-sceneSurface.target[i]),dot=a=>v.reduce((n,nv,i)=>n+nv*a[i],0),px=(dot(b.right)/sceneSurface.scale+1)*box.width/2,py=(1-dot(b.up)*box.width/box.height/sceneSurface.scale)*box.height/2,d=(px-x)**2+(py-y)**2,z=dot(b.forward);if(d<dist-1||(Math.abs(d-dist)<=1&&z>depth)){best=p;dist=d;depth=z;}}
  if(best)inspect(best[3]);
 });
 window.SurfaceUI={drawSegmentOverlay:panelRenderer.draw,selectFeaturePoints,deleteSelectedGroups,exportHighlight:()=>{const r=data.result;if(!r)return null;const spec=window.JobUI?.currentSelection?.(),visible=[-1,...r.clusters.map(c=>c.id)].filter(id=>isVisible([0,0,0,id],kept()));return {ids:[r.source_item_id],review:{id:r.id,revision:r.revision,clusters:spec?[]:[...selectedClusters],visible_clusters:visible},selection:spec};},configureBolts,setMethod,selectionChanged,reviewActive,selectClusters,selectedClusters:()=>[...selectedClusters],inspect,markClusters,clusterView,continueToOutput,updateReviewControls,sync,busy:setBusySurface,draw:()=>sceneSurface.draw(),selectSource,useJobSelection,pickInterior,showReview,markSelection};
 sync().catch(e=>notice(e.message,true));
})();
