/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const J=()=>window.JobUI,collapsed=new Set();let filter='',selectedBundle=null,archivesOpen=false;
 const style=document.createElement('style');style.textContent=`
 .job-context-menu{position:fixed!important;z-index:2147483000;max-height:calc(100vh - 16px);max-width:calc(100vw - 16px);overflow:auto!important;width:260px;box-shadow:0 12px 40px #0005;border:1px solid #708c98;border-radius:8px;padding:5px;background:#142b36;color:#eef8fc}
 .job-context-menu button{display:block;width:100%;text-align:left;min-height:32px;white-space:normal;border:0;background:transparent;color:inherit;padding:7px 11px}.job-context-menu button:hover,.job-context-menu button:focus{background:#315260;outline:2px solid #91d3ec;outline-offset:-2px}.job-context-menu button.danger{color:#ffbcb1}.job-context-menu h4{margin:7px 10px;color:#9ac2d1;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.job-context-menu hr{border:0;border-top:1px solid #47606b;margin:5px}
 .bundle-heading td{background:#e7f0f4;color:#163b4a;border-top:1px solid #bdd1db;padding:8px}.bundle-heading[aria-selected=true] td{box-shadow:inset 3px 0 #147fa4;background:#cee6f0}.bundle-heading button{border:0;background:transparent;color:inherit}.bundle-heading small{display:block;margin-left:25px;color:#466473}.explorer-tools{display:flex;gap:6px;padding:9px}.explorer-tools input{min-width:0;flex:1}.explorer-tools button{white-space:nowrap}.explorer-footer{display:flex;gap:6px;padding:9px;border-top:1px solid #c5d6dd}.explorer-footer button{flex:1}.job-sidebar>.job-actions,.job-sidebar>#job-delete-controls{display:none}.job-sidebar #job-storage{font-size:10px;overflow-wrap:anywhere}.job-sidebar .scene-list-help{margin:0;padding:0 10px 9px;font-size:11px}.job-sidebar{display:flex!important;flex-direction:column;overflow:hidden!important}.job-sidebar .job-sidehead,.job-sidebar .explorer-tools,.job-sidebar .scene-list-help,.job-sidebar .explorer-footer{flex-shrink:0}.job-sidebar .job-table{flex:1;min-height:0;max-height:none;overflow:auto!important}.job-sidebar.job-sidebar-collapsed .job-table{display:none}.job-sidebar table th{font-size:10px;text-transform:uppercase;letter-spacing:.04em}.scan-connections{border-left:2px solid #bad6e2;margin-left:22px;padding-left:9px}.scan-connections li{padding:4px 0}.job-table b{overflow-wrap:anywhere}.workspace-property-grid{display:grid;grid-template-columns:auto 1fr;gap:8px 16px}.workspace-property-grid dt{font-weight:600}.workspace-property-grid dd{margin:0}#mesh-display-parking[hidden]{display:none!important}
 `;document.head.append(style);
 const head=$('job-sidehead');
 head.querySelector('b').textContent='Project explorer';
 head.querySelector('b').title='Click an item to select it. Ctrl / Shift or checkboxes select several. Yellow bulbs show visible objects; grey bulbs hide them. Right-click for tools.';
 const footer=document.createElement('div');footer.className='explorer-footer';footer.innerHTML='<button id="export-highlighted" title="Export highlighted full-density points or selected objects">Export highlighted…</button><button id="explorer-properties">Properties</button>';document.querySelector('.job-table').after(footer);
 const bundleQA=document.createElement('section');bundleQA.id='explorer-bundle-qa';bundleQA.hidden=true;
 bundleQA.innerHTML='<b id="explorer-bundle-name"></b><div id="explorer-bundle-metrics" aria-live="polite"></div><div class="explorer-bundle-actions"><button id="explorer-registration" class="primary">Registration…</button></div>';
 document.querySelector('.job-table').before(bundleQA);
 $('explorer-registration').onclick=()=>window.RegistrationPanel?.open();
 let bundleQAGeneration=0;
 async function refreshBundleQA(){
  const request=++bundleQAGeneration,id=selectedBundle,b=J().inventory().bundles?.find(item=>item.id===id);
  bundleQA.hidden=!b||!id;
  if(bundleQA.hidden)return;
  $('explorer-bundle-name').textContent=b.name+' · Registration QA';
  const panel=$('explorer-bundle-metrics');panel.textContent='Reading saved errors…';
  try{
   const qa=await window.TargetUI.bundleQA(id);
   if(request!==bundleQAGeneration||selectedBundle!==id)return;
   panel.className='bundle-qa-metrics';
   panel.innerHTML=window.TargetUI.metricsHTML(qa);
  }catch(e){if(request===bundleQAGeneration&&selectedBundle===id)panel.textContent=e.message;}
 }
 $('export-highlighted').onclick=()=>exportHighlighted();$('explorer-properties').onclick=()=>{if(window.CadUI?.selectedIds().length)return window.CadUI.properties();if(window.SceneObjectsUI?.selected())window.SceneObjectsUI.properties();else if(selectedBundle)bundleProperties(selectedBundle);else properties();};
 function activeBundle(){const rows=J().inventory().bundles||[],cad=window.CadUI?.model().objects?.find(o=>window.CadUI.selectedIds().includes(o.id));return rows.find(b=>b.id===selectedBundle)?.id||cad?.bundle_id||J().selectedItem()?.bundle_id||null;}
 function setActiveBundle(id){if(id&&!(J().inventory().bundles||[]).some(b=>b.id===id))return;selectedBundle=id;J().refreshTree();window.CadUI?.refreshDestination();void refreshBundleQA();}
 function moveSelection(e,ids=[...J().selectedItems(),...(window.CadUI?.selectedIds()||[])]){if(!ids.length)return notice('Select models or clouds first.',true);const r=e?.currentTarget?.getBoundingClientRect();show({clientX:r?.left??e?.clientX??80,clientY:r?.bottom??e?.clientY??140},'Move to bundle',(J().inventory().bundles||[]).map(b=>[b.name,async()=>{if(await bundleCommand({action:'assign',id:b.id,ids})){await window.CadUI?.load(true);setActiveBundle(b.id);}} ]));}
 function exportMenu(e){const r=e?.currentTarget?.getBoundingClientRect(),models=window.CadUI?.model().objects||[],scans=J().inventory().items.filter(x=>x.kind==='scan'&&!x.merge_archived);show({clientX:r?.left??e?.clientX??80,clientY:r?.bottom??e?.clientY??140},'Export',[
 ['Export highlighted…',exportHighlighted],...(models.length?[["Export all editable models…",()=>window.CadUI.exportDialog(models.map(x=>x.id))]]:[]),...(scans.length?[["Export all clouds…",()=>window.ExportUI.openHighlight({ids:scans.map(x=>x.id),whole:true})]]:[]),null,['Portable project · models + clouds…',()=>window.TunnelRibbon.exportProject()]]);}
 async function workInModel(x){if(!window.CadUI.showCloudReference())return;setActiveBundle(x.bundle_id||activeBundle());if(x.visible===false)await J().action({action:'visibility',id:x.id,visible:true});$('job-load-preview').click();fit([x]);notice('Model in the same scene as '+x.name+'. Enable measured cloud snapping in Snap settings to draw from survey points.');}
 for(const [id,key,label] of [['export-highlighted','export','Export highlighted'],['explorer-properties','properties','Properties / metadata']])window.ToolIcons.paint($(id),key,label,({'export-highlighted':'Export highlighted','explorer-properties':'Properties'})[id]);
 function tree(data,itemRow){
  const groups=data.bundles||[],valid=new Set(groups.map(b=>b.id));
  const archived=data.items.filter(x=>x.merge_archived),top=data.items.filter(x=>!x.merge_archived&&(valid.has(x.bundle_id)||!data.items.some(p=>p.id===x.merge_parent&&p.registered_merge&&!p.merge_archived)));
  const archives=archived.length?`<tr class="bundle-heading"><td colspan="3"><button data-registration-archives aria-expanded="${archivesOpen}">${archivesOpen?'▾':'▸'} Previous registered results (${archived.length})</button><small>Retained for review · excluded from the active scene</small></td></tr>`+(archivesOpen?archived.map(x=>itemRow(x,1)).join(''):''):'';
  const unjoined=top.filter(x=>!valid.has(x.bundle_id)&&(!filter||x.name.toLocaleLowerCase().includes(filter)));
  const loose=unjoined.map(x=>itemRow(x,0)).join('')+`<tr data-cad-anchor="" data-cad-collapsed="false" data-cad-filter="${esc(filter)}" hidden></tr>`;
  return loose+groups.map(b=>{
   const all=top.filter(x=>x.bundle_id===b.id),rows=all.filter(x=>!filter||b.name.toLocaleLowerCase().includes(filter)||x.name.toLocaleLowerCase().includes(filter)||data.items.some(c=>c.merge_parent===x.id&&c.name.toLocaleLowerCase().includes(filter)));
   const models=(data.objects||[]).filter(x=>x.kind==='cad'&&x.bundle_id===b.id),matched=models.filter(x=>!filter||b.name.toLocaleLowerCase().includes(filter)||x.name.toLocaleLowerCase().includes(filter));
   if(!all.length&&!models.length)return '';
   if(filter&&!rows.length&&!matched.length&&!b.name.toLocaleLowerCase().includes(filter))return '';
   const open=!!filter||!collapsed.has(b.id),scans=b.scan_ids?.length||all.filter(x=>x.kind==='scan').length;
   return `<tr class="bundle-heading" data-bundle="${esc(b.id)}" tabindex="0" aria-selected="${selectedBundle===b.id}"><td colspan="3"><button data-bundle-expand="${esc(b.id)}" aria-expanded="${open}" aria-label="${open?'Collapse':'Expand'} ${esc(b.name)}">${open?'▾':'▸'}</button><b>${esc(b.name)}</b><small>${scans} joined scans${models.length?' · '+models.length+' models':''}</small></td></tr>`+(open?rows.map(x=>itemRow(x,1)).join(''):'')+`<tr data-cad-anchor="${esc(b.id)}" data-cad-collapsed="${!open}" data-cad-filter="${esc(b.name.toLocaleLowerCase().includes(filter)?'':filter)}" hidden></tr>`;
  }).join('')+archives;
 }
 async function selectBundle(id){
  if(busy)return;
  const fromSetup=!!J().setupCamera();
  if(fromSetup)await window.SetupCameraUI?.exit(false);
  selectedBundle=id;window.SceneObjectsUI.clearSelected();J().selectGeometry();
  fit(members(id),false);void refreshBundleQA();
  if(fromSetup)void J().sync();
 }
 function bindTree(data){
  for(const b of $('job-list').querySelectorAll('[data-registration-archives]'))b.onclick=()=>{archivesOpen=!archivesOpen;J().refreshTree();};
  for(const row of $('job-list').querySelectorAll('[data-bundle]')){
   const id=row.dataset.bundle;
   row.onclick=e=>{
    if(e.target.closest('[data-bundle-expand]')){
     collapsed.has(id)?collapsed.delete(id):collapsed.add(id);J().refreshTree();return;
    }
    void selectBundle(id);
   };
   row.oncontextmenu=e=>bundleMenu(e,id);
   row.onkeydown=e=>{
    if(e.key==='Delete'){e.preventDefault();e.stopPropagation();window.ExplorerDeleteUI?.open({bundle_ids:[id]});return;}
    if(e.target===row&&['Enter',' '].includes(e.key)){e.preventDefault();void selectBundle(id);}
    if(e.key==='ContextMenu'||e.shiftKey&&e.key==='F10'){
     e.preventDefault();const r=row.getBoundingClientRect();bundleMenu({clientX:r.left,clientY:r.bottom,preventDefault(){}},id);
    }
   };
  }
  for(const button of $('job-list').querySelectorAll('[data-scan-connection]'))button.oncontextmenu=e=>{e.stopPropagation();connectionMenu(e,button.dataset.scanConnection);};
  $('export-highlighted').disabled=busy;
 }
 async function bundleCommand(body){try{await post('/api/bundle',body);await J().sync();window.CadUI?.refreshDestination();return true;}catch(e){notice(e.message,true);}}
 function createBundle(){if(busy)return;return window.ScanRegistrationUI?.solveSelected();}
 function group(id){return J().inventory().bundles?.find(b=>b.id===id)||{id,name:'Joined scans'};}
 function members(id){const d=J().inventory();return d.items.concat((d.objects||[]).filter(x=>x.kind==='cad')).filter(x=>x.bundle_id===id);}
 function fit(items,refine=true){
  const points=[];
  for(const x of items){
   if(x.minimum&&x.maximum)for(let i=0;i<8;i++)points.push(x.minimum.map((v,j)=>i&(1<<j)?x.maximum[j]:v));
   if(!refine){
    if(x.has_setup_view&&x.scan_origin?.every(Number.isFinite))points.push(x.scan_origin);
    for(const marker of x.registration_summary?.markers||[])if(marker.source?.every(Number.isFinite))points.push(marker.source);
   }
  }
  J().focusWorld(points,refine);
 }
 function propertiesHTML(title,html){const d=$('scene-info-dialog');d.querySelector('h2').textContent=title||'Properties';$('scene-info-body').innerHTML=html;if(!d.open)d.showModal();}
 function bundleProperties(id){const b=group(id),items=members(id);propertiesHTML(b.name,`<p>Bundle · ${items.filter(x=>x.kind==='scan').length} scans · ${items.filter(x=>x.kind==='mesh').length} meshes · ${items.filter(x=>x.kind==='cad').length} models</p><p>This bundle contains joined scans. Expand a scan to review its saved connections and targets.</p><ul>${items.map(x=>`<li>${esc(x.name)} · ${esc(x.position_status||x.kind)}</li>`).join('')}</ul>`);}
 function properties(){const x=J().selectedItem();if(!x)return notice('Select an item first.',true);J().showDetails();$('scene-info-dialog').querySelector('h2').textContent=x.name+' · Properties';if(x.kind==='mesh'){
   const box=document.createElement('label');box.innerHTML='Visible mesh preview style <select><option value="solid">Solid triangles</option><option value="both">Solid + edges</option><option value="wire">Triangle edges</option></select>';const el=box.querySelector('select');el.value=$('job-mesh-display').value;el.onchange=()=>meshStyle(el.value);$('scene-info-body').prepend(box);
  }}
 function meshStyle(value){$('job-mesh-display').value=value;$('job-mesh-display').dispatchEvent(new Event('change'));}
 const menu=$('job-context-menu');document.body.append(menu);let returnFocus=null;
 function hide(){menu.hidden=true;}
 function show(e,title,entries){e.preventDefault?.();if(busy)return;returnFocus=document.activeElement;menu.innerHTML='<h4>'+esc(title)+'</h4>';menu.setAttribute('role','menu');menu.setAttribute('aria-label',title);
  for(const row of entries){if(!row){menu.append(document.createElement('hr'));continue;}if(typeof row==='string'){const label=document.createElement('h4');label.textContent=row;menu.append(label);continue;}const [label,fn,danger]=row,b=document.createElement('button');b.textContent=label;b.setAttribute('role','menuitem');if(danger)b.className='danger';b.onclick=async()=>{hide();try{await fn();}catch(error){notice(error.message,true);}};menu.append(b);}
  menu.hidden=false;menu.style.left='8px';menu.style.top='8px';const r=menu.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(innerWidth-r.width-8,e.clientX))+'px';menu.style.top=Math.max(8,Math.min(innerHeight-Math.min(r.height,innerHeight-16)-8,e.clientY))+'px';menu.querySelector('button')?.focus();
 }
 menu.onkeydown=e=>{const buttons=[...menu.querySelectorAll('button')],i=buttons.indexOf(document.activeElement);if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}if(e.key==='Escape'){e.preventDefault();e.stopPropagation();hide();returnFocus?.focus();}if(e.key==='Tab')hide();};
 document.addEventListener('pointerdown',e=>{if(!menu.contains(e.target))hide();});
 function itemEntries(x){const entries=x.merge_stale?[['Properties / metadata',properties],['Review registration / saved cache…',()=>window.ScanRegistrationUI.openLinks()],['Delete selected items…',()=>J().removeSelected(),true]]:[['Properties / metadata',properties],['Fit / zoom to item',()=>fit([x])],[x.visible?'Hide item':'Show item',()=>J().action({action:'visibility',id:x.id})],['Isolate selection',()=>$('job-isolate').click()],['Show all items',()=>$('job-show-all').click()],null,['Export highlighted…',exportHighlighted],['Export this '+(x.kind==='mesh'?'mesh…':'cloud…'),()=>x.kind==='mesh'?window.ExportUI.openMesh(x.id):window.TunnelRibbon.export('scene')],null,['Rename',()=>$('job-rename').click()],['Duplicate',()=>J().action({action:'duplicate',id:x.id})]];
  if(x.merge_stale)return entries;const mergeIds=J().selectedItems();if(window.SelectedMergeUI?.eligible(mergeIds))entries.unshift(['Merge scans ('+mergeIds.length+')…',()=>window.SelectedMergeUI.open(mergeIds)],null);const bundles=J().inventory().bundles||[];if(bundles.length)entries.push(['Move to bundle…',()=>moveSelection(null)]);entries.push(['Join scans / registration…',createBundle]);
  entries.push(['Work in Model mode',()=>workInModel(x)]);
  if(x.kind==='scan'){entries.push(['Scan colour…',()=>window.ScanAppearanceUI?.open(x.id)]);entries.push(null,'Scan tools',['Clean Tunnel',()=>J().openSurface()],['Create mesh',()=>window.TunnelRibbon.view('structures')],['Cross-section',()=>window.ScanTools.openSection()],['3D heat map',()=>{window.TunnelRibbon.view('heatmap');window.HeatUI.sync();$('heat-source').value=x.id;}],['Registration / targets',()=>window.ScanRegistrationUI?.activateTargets(x.id).then(()=>window.TargetUI.openManager())]);if(x.has_setup_view)entries.push(['Go to setup view',()=>window.ScanRegistrationUI.openSetup(x.id)]);}
  else entries.push(null,'Mesh tools',['Solid triangles',()=>meshStyle('solid')],['Solid + edges',()=>meshStyle('both')],['Triangle edges',()=>meshStyle('wire')],['Refine / repair mesh',()=>window.TunnelRibbon.view('structures')],['Preview / repair holes…',()=>window.MeshRepairUI?.open(x.id)],['Mesh to Point Cloud…',()=>window.MeshToCloudUI?.open(x.id)]);
  if(x.kind==='scan'&&(x.excluded_count>0||(x.deleted_groups||[]).some(g=>g.archived&&g.count>0)))entries.push(['Permanently remove deleted points…',()=>J().compactSelected(),true]);
  entries.push(null,['Delete selected items…',()=>J().removeSelected(),true]);return entries;
 }
 function itemMenu(e,id){e.preventDefault?.();if(busy)return;selectedBundle=null;void refreshBundleQA();J().activateItem(id);const x=J().inventory().items.find(v=>v.id===id);if(x)show(e,x.name,itemEntries(x));}
 function pointEntries(){return [...(['heatmap','design','inspection'].includes(J().previewOwner())?[["Restore scan colours",()=>J().clearCleaningPreview()]]:[]),['Export highlighted…',exportHighlighted],['Full-density statistics',fullStats],null,['Copy selection to new scans',()=>J().selAction('copy_selection')],['Separate selection to new scans…',()=>$('job-separate-selection').click()],['Hide selected points',()=>$('job-hide-selection').click()],['Isolate selected points',()=>$('job-isolate-selection').click()],['Invert selection',()=>$('job-invert-selection').click()],['Clear highlight',()=>J().clearSelection()],null,['Extract surface',()=>J().openSurface()],['Delete selected points…',()=>$('job-delete-selection').click(),true]];}
 function viewportMenu(e){e.preventDefault();if(busy)return;
  if(J().previewOwner()==='deleted')return show(e,'Deleted points',[...(J().currentSelection()?[['Restore selected points',()=>J().restoreDeletedSelection()],['Export selected deleted points…',()=>J().exportDeletedSelection()],['Invert selection',()=>$('job-invert-selection').click()],['Clear selection',()=>J().clearSelection()]]:[]),['Box select',()=>J().setMode('box')],['Lasso select',()=>J().setMode('lasso')],['Back to scan',()=>$('job-back-deleted').click()]]);
  if(window.CadUI?.modelSpace()&&!window.CadUI.cloudReference()){const model=window.CadUI.pick(e);if(model)return window.CadUI.menu(e,model.id);return show(e,'Model workspace',[['Fit models',()=>window.CadUI.tool('fit')],['Working origin / axes',()=>window.CadUI.tool('ucs')],['Show cloud reference',()=>window.CadUI.showCloudReference()],['Command help',()=>window.CadUI.tool('help')]]);}
  const referenceRect=$('job-canvas').getBoundingClientRect(),reference=window.SceneObjectsUI?.pickNear([e.clientX-referenceRect.left,e.clientY-referenceRect.top],J().projectWorld);if(reference)return objectMenu(e,reference);
  if(J().previewOwner()==='review'&&window.SurfaceUI.reviewActive()){
   const id=J().reviewAt(e);if(id!==null&&!window.SurfaceUI.selectedClusters().includes(id)&&!J().currentSelection())window.SurfaceUI.selectClusters([id]);
   return show(e,'Highlighted review', [['Export highlighted…',exportHighlighted],['Full-density statistics',fullStats],null,['Keep selected clusters',()=>window.SurfaceUI.markClusters(true)],['Remove selected clusters',()=>window.SurfaceUI.deleteSelectedGroups(),true],['Hide selected clusters',()=>window.SurfaceUI.clusterView('hide')],['Isolate selected clusters',()=>window.SurfaceUI.clusterView('isolate')],['Show all clusters',()=>window.SurfaceUI.clusterView('all')],...(J().currentSelection()?[['Keep selected points',()=>window.SurfaceUI.markSelection(true)],['Remove selected points',()=>window.SurfaceUI.markSelection(false),true]]:[]),['Continue to Output',()=>window.SurfaceUI.continueToOutput()]]);
  }
  const target=J().targetAt(e);if(target)return show(e,'Target '+target.id,[['Select target / properties',()=>J().selectScanTarget(target)],['Targets window',()=>window.TargetUI.openManager()],['Export target coordinates…',async()=>{if(target.scanId!==J().selectedItem()?.id)await J().selectScanTarget(target);window.ExportUI.openTarget(target);}],['Delete target…',async()=>{await J().selectScanTarget(target);window.TargetUI.deleteSelectedTargets();},true]]);
  const model=window.CadUI?.pick(e);if(model)return window.CadUI.menu(e,model.id);if(J().currentSelection())return show(e,'Highlighted points',pointEntries());
  const id=J().pickedItem(e);if(id)return itemMenu(e,id);
  const rect=$('job-canvas').getBoundingClientRect(),object=window.SceneObjectsUI.pickNear([e.clientX-rect.left,e.clientY-rect.top],J().projectWorld);if(object)return objectMenu(e,object);
  show(e,'3D workspace',[['Fit visible scene',()=>$('job-fit').click()],['Show all items',()=>$('job-show-all').click()],['Restore scan colours',()=>J().clearCleaningPreview()],['Choose cloud / model…',()=>show(e,'Choose item',J().inventory().items.filter(x=>x.visible).map(x=>[x.name,()=>itemMenu(e,x.id)]))]]);
 }
 function bundleMenu(e,id){selectedBundle=id;const b=group(id),items=members(id);void refreshBundleQA();show(e,b.name,[['Registration…',()=>window.RegistrationPanel?.open()],['Bundle properties',()=>bundleProperties(id)],['Work in Model mode',()=>{setActiveBundle(id);if(items.some(x=>x.kind==='scan'||x.kind==='mesh'))window.CadUI.showCloudReference();else window.CadUI.enter('Model');fit(items);} ],['Import models into this bundle…',()=>{setActiveBundle(id);window.CadUI.importDialog('',id);} ],['Import clouds into this bundle…',()=>{setActiveBundle(id);window.TunnelRibbon.openImport('scan',id);} ],['Rename bundle',()=>{const name=prompt('Bundle name',b.name);if(name?.trim())return bundleCommand({action:'rename',id,name});}],['Expand all scans',()=>{collapsed.delete(id);J().expandScans(items.map(x=>x.id));}],['Fit bundle',()=>fit(items)],['Hide bundle',()=>bundleCommand({action:'visibility',id,visible:false})],['Show bundle',()=>bundleCommand({action:'visibility',id,visible:true})],['Isolate bundle',()=>bundleCommand({action:'isolate',id})],null,...(items.some(x=>x.kind==='cad')?[["Export bundle models…",()=>window.CadUI.exportDialog(items.filter(x=>x.kind==='cad').map(x=>x.id))]]:[]),['Export bundle scans…',()=>{const ids=items.filter(x=>x.kind==='scan'&&!x.merge_archived).map(x=>x.id);window.ExportUI.openHighlight({ids,whole:true});}],null,['Delete source bundle…',()=>window.ExplorerDeleteUI?.open({bundle_ids:[id]}),true]]);}
 function connectionMenu(e,id){const links=J().inventory().items.flatMap(x=>(x.registration_summary?.links||[]).map(l=>({...l,source:x}))),link=links.find(l=>l.id===id);const object=window.SceneObjectsUI.find('link:'+id);show(e,'Scan connection',[...(object?[[object.visible?'Hide connection line':'Show connection line',()=>window.SceneObjectsUI.change(object,'visibility')],['Export connection metadata…',()=>window.ExportUI.openObjects([object.id])]]:[]),['Review connection / residuals',()=>window.ScanRegistrationUI.openLinks(id)],['Fit connected scans',()=>fit(links.filter(l=>l.id===id).map(l=>l.source))],['Connection metadata',()=>propertiesHTML('Connection',`<pre style="white-space:pre-wrap">${esc(JSON.stringify(link?Object.fromEntries(Object.entries(link).filter(([k])=>k!=='source')):{id},null,2))}</pre>`)]]);}
 function objectMenu(e,o){if(o.kind==='lining_feature'&&o.feature?.kind==='bolt_hole')return show(e,o.name,[['Select measured recess points',()=>window.SurfaceUI.selectFeaturePoints(o)],['Fit recess centre',()=>window.SceneObjectsUI.focus(o)],[o.visible?'Hide centre guide':'Show centre guide',()=>window.SceneObjectsUI.change(o,'visibility')]]);if(o.kind==='cad')return window.CadUI?.menu(e,o.id);if(!window.SceneObjectsUI.selectedIds().includes(o.id))window.SceneObjectsUI.select(o.id);show(e,o.name,[['Properties / metadata',()=>window.SceneObjectsUI.properties()],['Fit in 3D',()=>window.SceneObjectsUI.focus(o)],[o.visible?'Hide':'Show',()=>window.SceneObjectsUI.change(o,'visibility')],['Export highlighted…',()=>window.ExportUI.openObjects(window.SceneObjectsUI.selectedIds())],...(o.editable?[['Open / edit',()=>window.SceneObjectsUI.open(o)]]:[]),['Rename',()=>{const name=prompt('Object name',o.name);if(name?.trim())return window.SceneObjectsUI.change(o,'rename',{name});}],...(window.SceneObjectsUI.canRemove()?[["Delete selected geometry…",()=>window.SceneObjectsUI.removeSelected(),true]]:[])]);}
 function exportHighlighted(){if(J().previewOwner()==='deleted'){if(!J().exportDeletedSelection())notice('Draw an area around the deleted points before exporting.',true);return;}if(window.CadUI?.selectedIds().length)return window.CadUI.exportDialog();const request=J().highlightRequest();if(request)return window.ExportUI.openHighlight(request);const targets=J().selectedTargets();if(targets.length)return window.ExportUI.openTargets(targets);if(selectedBundle)return window.ExportUI.openHighlight({ids:members(selectedBundle).filter(x=>x.kind==='scan'&&!x.merge_archived).map(x=>x.id),whole:true});const object=window.SceneObjectsUI.selected();if(object)return window.ExportUI.openObjects(window.SceneObjectsUI.selectedIds());const selected=J().inventory().items.filter(x=>J().selectedItems().includes(x.id));if(!selected.length)return notice('Highlight points or select an item first.',true);if(selected.every(x=>x.kind==='scan'))return window.ExportUI.openHighlight({ids:selected.map(x=>x.id),whole:true});if(selected.every(x=>x.kind==='mesh'))return window.ExportUI.openMesh(selected.map(x=>x.id));notice('Select scans or meshes together, then export that selection in its matching format.',true);}
 async function fullStats(){const request=J().highlightRequest();if(!request)return showTunnelMessage('No selection','Highlight points in the main 3D view first.',true);window.WorkStatus.show('Counting highlighted points…','Reading original scan points.');try{setBusy(true);await post('/api/highlight-statistics',request);await J().waitReady(job=>{if(job.busy)window.WorkStatus.show('Counting highlighted points…',job.message,true);});const r=await api('/api/highlight-statistics');propertiesHTML('Highlighted points',`<p><b>${fmt(r.count,0)} full-density retained points</b></p>${r.count?'<dl class="workspace-property-grid">'+['E / X','N / Y','H / Z'].map((k,i)=>`<dt>${k} · m</dt><dd>${fmt(r.minimum[i],4)} to ${fmt(r.maximum[i],4)}</dd>`).join('')+'</dl>':''}`);window.WorkStatus.show('Selection counted',fmt(r.count,0)+' original points.');}catch(e){setBusy(false);notice(e.message,true);}}
 window.WorkspaceUI={selectedBundleId:()=>selectedBundle,clearBundleSelection:()=>{selectedBundle=null;void refreshBundleQA();},refreshBundleQA,activeBundle,setActiveBundle,moveSelection,exportMenu,tree,bindTree,itemMenu,viewportMenu,objectMenu,showMenu:show,propertiesHTML,exportHighlighted,fullStats};J().refreshTree();
})();
