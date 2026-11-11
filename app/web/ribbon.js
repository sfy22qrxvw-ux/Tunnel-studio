/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const icon=window.ToolIcons.icon;
 const command=(id,label,ico,description,run,need=null,target=null)=>({id,label,ico,description,run,need,target});
 const proxy=(target,label,ico,description,tool=null,need=null)=>command(target,label,ico,description,()=>{if(tool)viewTab(tool);$(target)?.click();},need,target);
 const dropdown=(id,label,ico,description,items)=>({...command(id,label,ico,description,null),menu:items});
 const inspection=(mode)=>window.InspectionUI?.open(mode);
 const ribbonMenu=document.createElement('div');ribbonMenu.className='ribbon-popup';ribbonMenu.id='ribbon-command-menu';ribbonMenu.hidden=true;ribbonMenu.setAttribute('role','menu');document.body.append(ribbonMenu);let menuTrigger=null;
 function closeRibbonMenu(focus=false){ribbonMenu.hidden=true;if(menuTrigger){menuTrigger.setAttribute('aria-expanded','false');if(focus)menuTrigger.focus();}menuTrigger=null;}
 function openRibbonMenu(button,item){
  if(menuTrigger===button&&!ribbonMenu.hidden){closeRibbonMenu();return;}closeRibbonMenu();menuTrigger=button;button.setAttribute('aria-expanded','true');ribbonMenu.setAttribute('aria-label',item.label+' commands');ribbonMenu.replaceChildren();
  for(const option of item.menu){const entry=document.createElement('button');entry.type='button';entry.dataset.command=option.id;entry.setAttribute('role','menuitem');entry.setAttribute('aria-disabled',!!availability(option));entry.title=availability(option)||option.description;entry.innerHTML=icon(option.ico||item.ico)+`<span><strong>${esc(option.label)}</strong><small>${esc(option.description)}</small></span>`;entry.onclick=()=>{closeRibbonMenu();const reason=availability(option);if(reason){notice(reason,true);return;}runCommand(option);};ribbonMenu.append(entry);}
  ribbonMenu.hidden=false;const r=button.getBoundingClientRect(),m=ribbonMenu.getBoundingClientRect();ribbonMenu.style.left=Math.max(8,Math.min(innerWidth-m.width-8,r.left))+'px';ribbonMenu.style.top=Math.max(8,Math.min(innerHeight-m.height-8,r.bottom+6))+'px';ribbonMenu.querySelector('button')?.focus();
 }
 ribbonMenu.onkeydown=e=>{const buttons=[...ribbonMenu.querySelectorAll('button')],i=buttons.indexOf(document.activeElement);let n;if(e.key==='ArrowDown')n=(i+1)%buttons.length;if(e.key==='ArrowUp')n=(i-1+buttons.length)%buttons.length;if(e.key==='Home')n=0;if(e.key==='End')n=buttons.length-1;if(n!==undefined){e.preventDefault();buttons[n]?.focus();}if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeRibbonMenu(true);}if(e.key==='Tab')closeRibbonMenu();};
 document.addEventListener('pointerdown',e=>{if(!ribbonMenu.contains(e.target)&&!menuTrigger?.contains(e.target))closeRibbonMenu();});window.addEventListener('resize',()=>closeRibbonMenu());
 function measureModelProperties(){if(!window.CadUI?.selectedIds?.().length){notice('Select an editable model in Model space to inspect its surface area and, for a closed solid, its volume.',true);return;}window.CadUI.properties();}
 const toolTitles={targets:'Targets · select and manage',registration:'Target setup and measurements','registration-flow':'Registration',surface:'Clean Tunnel',section:'Cross-section analysis',joints:'Ring joint suggestions',creep:'Ring creep',heatmap:'Design deviation heatmap',structures:'Mesh & reconstruction','analysis-tools':'Point-cloud QA & comparison',inspection:'Compare / Inspect','circle-clean':'Local-circle cleaning','shape-selection':'Find similar shapes','feature-selection':'Local feature selection',reports:'Survey reports',settings:'Settings',methods:'Methods & help'};
 let openTool=null,toolOrigin=null,toolNext=null;
 function closeTool(){window.JobUI?.clearCleaningPreview?.('review');if(openTool?.id==='tab-feature-selection')window.FeatureSelectionUI?.onToolClose?.();if(openTool?.id==='tab-shape-selection')window.ShapeSelectionUI?.onToolClose?.();if(openTool?.id==='tab-circle-clean')window.CircleCleanUI?.onToolClose?.();const drawer=$('tool-drawer');if(openTool){openTool.classList.remove('active','drawer-active');}if(openTool&&toolOrigin){if(toolNext&&toolNext.parentNode===toolOrigin)toolOrigin.insertBefore(openTool,toolNext);else toolOrigin.append(openTool);}openTool=null;toolOrigin=null;toolNext=null;if(drawer){drawer.hidden=true;$('tool-drawer-body').innerHTML='';}document.body.classList.remove('registration-active','surface-active');window.JobUI?.draw();}
 // Claim the tool area only when opening a tool, never when a picker temporarily
 // hides its editor or returns to it. Active calculations/picks keep their owner.
 function explainToolBlock(message){notice(message);$('ribbon-hint').textContent=message;}
 function prepareTool(owner){
  if(owner==='alignment'&&(window.CadUI?.command?.()||window.TunnelDesigner?.isPicking?.())){explainToolBlock('Finish the current drawing or section pick, or press Escape, before opening alignment tools.');return false;}
  const others=[['measure',window.MeasureTools],['geometry',window.GeometryToolsUI]].filter(([name])=>name!==owner);
  if(others.some(([,ui])=>ui?.canDeactivate?.()===false)){explainToolBlock('Finish or cancel the active point pick or calculation before opening another tool.');return false;}
  for(const [,ui] of others)ui?.closeInactive?.();
  if(owner!=='drawer'&&!(owner==='registration'&&openTool?.id==='tab-registration-flow'))closeTool();return true;
 }

 function surfaceDrawerMode(mode){const section=$('tab-surface');if(!section)return;section.dataset.drawerMode=mode;const controls=section.querySelector('.surface-controls'),review=section.querySelector('.surface-review'),reviewCard=$('surf-review-card'),output=$('surf-output-card'),evaluation=$('surf-evaluation'),learn=$('surf-learn');if(controls)controls.hidden=mode!=='setup';if(review)review.hidden=mode==='setup';if(reviewCard)reviewCard.hidden=mode!=='review';if(output)output.hidden=mode!=='output';if(evaluation)evaluation.hidden=true;if(learn)learn.hidden=true;section.querySelectorAll('[data-surface-mode]').forEach(b=>b.classList.toggle('chosen',b.dataset.surfaceMode===mode));for(const id of ['surf-run-quick','surf-use-selection-quick'])if($(id))$(id).hidden=mode!=='setup';if($('surf-continue-quick'))$('surf-continue-quick').hidden=mode!=='review';window.SurfaceUI?.updateReviewControls?.();window.SurfaceUI?.showReview?.();}
 function registrationDrawerMode(mode){
  const section=$('tab-registration');if(!section)return;section.dataset.drawerMode=mode;
  const ids={search:['reg-find-card','reg-suggestions-card'],match:['reg-quality-card','reg-correspondence-card'],apply:['reg-quality-card','reg-apply-card']};
  const visible=new Set(ids[mode]||ids.search);
  for(const id of ['reg-find-card','reg-suggestions-card','reg-quality-card','reg-correspondence-card','reg-apply-card']){const el=$(id);if(el)el.hidden=!visible.has(id);}
  section.querySelectorAll('[data-registration-mode]').forEach(b=>b.classList.toggle('chosen',b.dataset.registrationMode===mode));
 }
 function addToolActions(id){
  const body=$('tool-drawer-body');if(!body)return;const old=body.querySelector('#tool-quick-actions');if(old)old.remove();
  const bar=document.createElement('div');bar.id='tool-quick-actions';bar.className='tool-quick-actions';
  const add=(label,primary,fn,cls='')=>{const b=document.createElement('button');b.textContent=label;if(primary)b.className='primary';if(cls)b.classList.add(cls);b.onclick=fn;bar.appendChild(b);return b;};
  if(id==='surface'){
    const nav=document.createElement('div');nav.className='tool-mode-tabs';for(const [key,label] of [['setup','Setup'],['review','Review'],['output','Output']]){const b=document.createElement('button');b.textContent=label;b.dataset.surfaceMode=key;b.onclick=()=>surfaceDrawerMode(key);nav.appendChild(b);}bar.appendChild(nav);
    const useSelection=add('Use main 3D selection',false,()=>{const s=window.JobUI?.currentSelection?.();if(!s){showTunnelMessage('Select an area first','Draw a box or lasso in the main 3D viewport first.',true);return;}window.SurfaceUI?.useJobSelection?.(s);surfaceDrawerMode('setup');});useSelection.id='surf-use-selection-quick';
    const next=add('Continue to Output',true,()=>window.SurfaceUI?.continueToOutput?.());next.id='surf-continue-quick';
    const run=add('Preview cleaning',true,()=>$('surf-run')?.click());run.id='surf-run-quick';window.SurfaceUI?.busy(busy);
    surfaceDrawerMode($('tab-surface')?.dataset.drawerMode||'setup');
  }else if(id==='registration'){
    add('Back to Registration',false,()=>window.RegistrationPanel.open('targets'));
    const nav=document.createElement('div');nav.className='tool-mode-tabs';for(const [key,label] of [['search','Targets'],['match','Match / QA'],['apply','Apply']]){const b=document.createElement('button');b.textContent=label;b.dataset.registrationMode=key;b.onclick=()=>registrationDrawerMode(key);nav.appendChild(b);}bar.appendChild(nav);
    add('Pick & fit target',true,()=>{registrationDrawerMode('search');$('reg-pick')?.click();});add('Survey targets',false,()=>{registrationDrawerMode('search');$('reg-detect')?.click();});add('Apply bundle control',false,()=>window.TargetUI?.openBundle());
    registrationDrawerMode($('tab-registration')?.dataset.drawerMode||'search');
  }else if(id==='structures'){
    add('Create mesh',true,()=>$('build-mesh')?.click());
  }else if(id==='section'){
    add('Analyse current section',true,()=>$('analyse')?.click());
  }else if(id==='heatmap'){
    add('Generate heatmap',true,()=>$('build-heat')?.click());
  }
  if(bar.childElementCount)body.prepend(bar);
 }
 function viewTab(id,options={}){if(!id||id==='job'||id==='three'){closeTool();return;}const section=$('tab-'+id);if(!section)return;if(!prepareTool('drawer'))return false;window.DetachedUI?.mainTool?.();if(window.DetachedUI?.has(section.id)){window.DetachedUI.focus(section.id);return;}closeTool();openTool=section;toolOrigin=section.parentNode;toolNext=section.nextSibling;$('tool-drawer-title').textContent=toolTitles[id]||'Tool';section.classList.add('active','drawer-active');$('tool-drawer-body').append(section);$('tool-drawer').hidden=false;addToolActions(id);document.body.classList.toggle('registration-active',id==='registration');document.body.classList.toggle('surface-active',id==='surface');if(id==='inspection'&&options.refresh!==false)window.InspectionUI?.sync();if(id==='joints')window.JointUI?.sync(true);if(id==='registration'){Promise.resolve(window.TargetUI?.sync?.({skipPreview:!!options.skipPreview})).finally(()=>window.TargetUI?.draw?.());}if(id==='surface'){window.SurfaceUI?.draw();window.SurfaceUI?.showReview?.();if(options.refresh!==false)window.SurfaceUI?.sync();}window.JobUI?.draw();}
 $('tool-drawer-close').onclick=closeTool;
 const drawer=$('tool-drawer'),drawerHead=$('tool-drawer-head'),drawerSplitter=$('tool-drawer-splitter');
 function setToolDock(mode){
  if(!drawer)return;drawer.classList.remove('dock-left','dock-right','floating');drawer.classList.add(mode==='right'?'dock-right':mode==='float'?'floating':'dock-left');
  localStorage.setItem('tunnelstudio.toolDrawerDock',mode);
  if(mode!=='float'){drawer.style.left='';drawer.style.right='';drawer.style.top='';drawer.style.bottom='';drawer.style.height='';}
  else{
   const r=drawer.getBoundingClientRect();drawer.style.left=Math.max(8,Math.min(innerWidth-r.width-8,r.left))+'px';drawer.style.top=Math.max(88,Math.min(innerHeight-r.height-28,r.top))+'px';drawer.style.width=r.width+'px';drawer.style.height=Math.min(Math.max(340,r.height),innerHeight-120)+'px';
  }
  setTimeout(()=>window.JobUI?.draw(),30);
 }
 window.TunnelRibbonSetToolDock=setToolDock;
 $('tool-dock-left').onclick=()=>setToolDock('left');$('tool-dock-right').onclick=()=>setToolDock('right');$('tool-float').onclick=()=>setToolDock('float');
 const savedDock=localStorage.getItem('tunnelstudio.compactWorkspace')?localStorage.getItem('tunnelstudio.toolDrawerDock')||'right':'right';setToolDock(savedDock);
 if(drawerSplitter){let ds=null;const saved=+localStorage.getItem('tunnelstudio.toolDrawerWidth');if(saved>=300&&saved<=850)drawer.style.width=saved+'px';drawerSplitter.addEventListener('pointerdown',e=>{if(drawer.classList.contains('floating'))return;ds=[e.clientX,drawer.getBoundingClientRect().width,drawer.classList.contains('dock-left')?'left':'right'];drawerSplitter.setPointerCapture(e.pointerId);});drawerSplitter.addEventListener('pointermove',e=>{if(!ds)return;const delta=e.clientX-ds[0],w=Math.max(300,Math.min(850,ds[1]+(ds[2]==='left'?delta:-delta)));drawer.style.width=w+'px';localStorage.setItem('tunnelstudio.toolDrawerWidth',w);window.JobUI?.draw();});drawerSplitter.addEventListener('pointerup',()=>ds=null);}
 if(drawerHead){let drag=null;drawerHead.addEventListener('pointerdown',e=>{if(!drawer.classList.contains('floating')||e.target.closest('button'))return;const r=drawer.getBoundingClientRect();drag=[e.clientX-r.left,e.clientY-r.top];drawerHead.setPointerCapture(e.pointerId);});drawerHead.addEventListener('pointermove',e=>{if(!drag)return;const r=drawer.getBoundingClientRect(),x=Math.max(4,Math.min(innerWidth-r.width-4,e.clientX-drag[0])),y=Math.max(78,Math.min(innerHeight-r.height-24,e.clientY-drag[1]));drawer.style.left=x+'px';drawer.style.top=y+'px';});drawerHead.addEventListener('pointerup',()=>drag=null);}
 function openImport(pref='scan',bundle=null){
  const d=$('import-dialog');d.dataset.pref=pref;const rows=[{id:'',name:'Project — separate items'},...(window.JobUI?.inventory().bundles||[])];$('import-destination').innerHTML=rows.map(b=>`<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('');$('import-destination').value=bundle||'';
  $('import-local-kind').value=['scan','model','axis','rings'].includes(pref)?pref:'scan';
  $('import-mode-help').textContent=window.pywebview?.api?.choose_large_file?'Choose a file below. Large scans can be read directly from their local path.':'Browser mode: choose a file below, or use a local path for files larger than 150 MB to avoid a transfer copy.';
  if(!d.open)d.showModal();
  d.querySelector('[data-import-kind="'+pref+'"]')?.focus();
 }
 function openLocalImport(pref='scan',message=''){
  openImport(pref);$('import-local-details').open=true;
  $('import-local-message').textContent=message;$('import-local-paths').focus();
 }
 async function importChoice(kind){
  if(busy)return;
  const destination=$('import-destination').value;window.JobUI?.setImportDestination(destination);$('import-dialog').close();
  if(kind==='rtc360'){window.RTC360VisUI?.open();return;}
  if(kind==='cad'){window.CadUI?.importDialog('',destination);return;}
  const pickerIds={scan:'job-scan-files',model:'job-mesh-files',axis:'axis-file',rings:'rings-file'};
  // Keep the browser selector inside the original user click; no native bridge is required.
  if(pickerIds[kind]&&!window.pywebview?.api?.choose_large_file){$(pickerIds[kind]).click();return;}
  let choosing=!!pickerIds[kind];
  try{
   if(kind==='scan'||kind==='model'){const paths=await window.pywebview.api.choose_large_file(kind==='model'?'job_mesh':'job_scan',true);choosing=false;if(paths?.length)await window.JobUI?.importPaths(paths,destination);return;}
   if(kind==='axis'||kind==='rings'){const paths=await window.pywebview.api.choose_large_file(kind,false);choosing=false;if(!paths?.length)return;setBusy(true);await post('/api/load',{kind,path:paths[0],alignment_radius:kind==='axis'?$('import-alignment-radius').value:null});poll();return;}
   if(kind==='control'){window.TargetUI?.importControl();return;}
   if(kind==='project'){projectDialog('open');setTimeout(()=>$('project-import-file').click(),100);return;}
  }catch(e){
   setBusy(false);
   if(choosing)openLocalImport(kind,'The desktop picker could not complete: '+e.message+'. Paste the full file path below, or use Choose in browser.');
   else notice(e.message,true);
  }
 }
 $('import-browser-pick').onclick=()=>{
  if(busy)return;
  const ids={scan:'job-scan-files',model:'job-mesh-files',axis:'axis-file',rings:'rings-file'};
  const id=ids[$('import-local-kind').value];window.JobUI?.setImportDestination($('import-destination').value);$('import-dialog').close();$(id).click();
 };
 $('import-local-read').onclick=async()=>{
  if(busy)return;
  const kind=$('import-local-kind').value,destination=$('import-destination').value;
  const paths=$('import-local-paths').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  if(!paths.length){$('import-local-message').textContent='Paste the full path copied from File Explorer.';return;}
  if(!['scan','model'].includes(kind)&&paths.length!==1){$('import-local-message').textContent='Choose one alignment or ring file at a time.';return;}
  $('import-dialog').close();
  try{
   if(kind==='scan'||kind==='model')await window.JobUI?.importPaths(paths,destination);
   else{setBusy(true);await post('/api/load',{kind,path:paths[0],alignment_radius:kind==='axis'?$('import-alignment-radius').value:null});poll();}
  }catch(e){setBusy(false);notice(e.message,true);}
 };
 $('import-dialog-close').onclick=()=>$('import-dialog').close();$('import-dialog').querySelectorAll('[data-import-kind]').forEach(b=>b.onclick=()=>importChoice(b.dataset.importKind));
 async function runRegistration(target){try{
  if(target==='reg-control-import'){
   window.TargetUI?.importControl();return;
  }
 if(target==='reg-pick'&&window.JobUI?.setupCamera?.()){window.SetupCameraUI?.pickTarget();return;}const ok=await window.JobUI?.ensureRegistrationSource?.();if(ok===false)return;viewTab('registration');await window.TargetUI?.sync?.();setTimeout(()=>$(target)?.click(),80);}catch(e){notice(e.message||String(e),true);}}
 const groups=window.RibbonLayout.build({command,proxy,dropdown,viewTab,projectNew,closeProject,projectDialog,saveProject,openImport,runRegistration,registrationDrawerMode});
 const ribbon=$('ribbon');ribbon.innerHTML='<div class="ribbon-tabs" role="tablist" aria-label="Tool groups"></div><div class="ribbon-panel" id="ribbon-panel" role="tabpanel"></div><div class="ribbon-hint"><span id="ribbon-hint">Choose a command. Hover for guidance.</span><span>Ctrl+S Save · Ctrl+Shift+S Save As</span></div>';
 const tabs=ribbon.querySelector('.ribbon-tabs');let current='Home';
 for(const name of Object.keys(groups)){const b=document.createElement('button');b.textContent=name;b.id='ribbon-tab-'+name;b.setAttribute('role','tab');b.onclick=()=>activate(name);tabs.append(b);}
 const collapse=document.createElement('button');collapse.textContent='⌃';collapse.id='ribbon-collapse';collapse.setAttribute('aria-label','Collapse ribbon');collapse.title='Collapse or expand ribbon (Ctrl+F1)';collapse.onclick=()=>document.body.classList.toggle('ribbon-collapsed');tabs.append(collapse);
 function availability(c){if(busy)return 'Wait for the current operation.';if(c.need==='cad'&&!window.CadUI?.selectedIds?.().length)return 'Select an editable model in Model space first.';if(c.need==='axis'&&!state?.axis)return 'Import a design alignment first.';if(c.need==='mesh'&&window.JobUI?.selectedItem?.()?.kind!=='mesh')return 'Select a mesh in the left object explorer first.';if(c.need==='scan'&&window.JobUI?.selectedItem?.()?.kind!=='scan')return 'Select a scan in the left object explorer first.';if(c.need==='cloud'&&!state?.cloud)return 'Import a survey scan first.';if(c.need==='reg'&&!state?.registration_ready)return 'Import a registration source scan first.';if(c.need==='selection'&&(!selectSpec||!state?.selection_count))return 'Box-select points first.';if(c.target&&$(c.target)?.disabled)return c.id.includes('undo')?'There is no workspace edit to undo.':c.id.includes('redo')?'There is no undone workspace edit to redo.':c.id.includes('selection')?'Select points in the 3D view first.':c.id.includes('setup')?'The selected scan has no original scanner setup data.':('Select the required object or finish the active tool before using '+c.label+'.');return '';}
 function runCommand(c){
  const reason=availability(c);if(reason){notice(reason,true);return;}
  try{Promise.resolve(c.run?.()).catch(error=>notice(error.message||String(error),true)).finally(()=>sync());}catch(error){notice(error.message||String(error),true);}sync();
 }
 function buttonFor(c){
  const b=document.createElement('button');b.type='button';b.className='ribbon-command'+(c.compact?' ribbon-command-small':'')+(c.wide?' ribbon-command-wide':'');b.dataset.command=c.id;
  b.innerHTML=icon(c.ico)+`<span>${esc(c.label)}</span>`;b.title=c.label+' — '+c.description;b.setAttribute('aria-label',c.label);
  if(c.menu){b.classList.add('has-menu');b.insertAdjacentHTML('beforeend','<span class="ribbon-chevron" aria-hidden="true">▾</span>');b.setAttribute('aria-haspopup','menu');b.setAttribute('aria-expanded','false');b.setAttribute('aria-controls','ribbon-command-menu');}
  b.onmouseenter=b.onfocus=()=>{$('ribbon-hint').textContent=availability(c)||c.description;window.ParameterHelp?.ribbonHint(b,c,availability(c));};
  b.onmouseleave=()=>window.ParameterHelp?.hideRibbonHint();b.onblur=()=>window.ParameterHelp?.hideRibbonHint();
  b.onclick=()=>{window.ParameterHelp?.hideRibbonHint();const reason=availability(c);if(reason){notice(reason,true);return;}if(c.menu)openRibbonMenu(b,c);else runCommand(c);};return b;
 }
 function createGroup(label,commands){
  const group=document.createElement('div');group.className='ribbon-group';group.dataset.group=label;group.setAttribute('role','group');group.setAttribute('aria-label',label);
  const buttons=document.createElement('div');buttons.className='ribbon-buttons';let stack=null;
  for(const c of commands){if(c.compact){if(!stack||stack.childElementCount===3){stack=document.createElement('div');stack.className='ribbon-command-stack';buttons.append(stack);}stack.append(buttonFor(c));}else{stack=null;buttons.append(buttonFor(c));}}
  const all=commands.flatMap(c=>c.menu||[c]);const menu=buttonFor(dropdown('group-'+label.toLowerCase().replace(/[^a-z0-9]+/g,'-'),label,commands[0]?.ico||'settings','Show all '+label.toLowerCase()+' commands.',all));menu.classList.add('ribbon-group-menu');
  const caption=document.createElement('div');caption.className='ribbon-group-label';caption.textContent=label;group.append(buttons,menu,caption);return group;
 }
 let layoutQueued=false;
 function layoutRibbon(){
  if(layoutQueued)return;layoutQueued=true;requestAnimationFrame(()=>{layoutQueued=false;const panel=$('ribbon-panel');if(!panel)return;
   const normal=[...panel.querySelectorAll(':scope>.ribbon-group:not(.ribbon-options)')];for(const g of normal)g.classList.remove('is-condensed');
   if(current!=='View'){
    const available=panel.clientWidth-20;let used=normal.reduce((sum,g)=>sum+g.getBoundingClientRect().width,0);
    // Keep the first everyday command group expanded as long as space allows.
    for(const g of [...normal.slice(1).reverse(),normal[0]].filter(Boolean)){if(used<=available)break;const before=g.getBoundingClientRect().width;g.classList.add('is-condensed');used+=g.getBoundingClientRect().width-before;}
   }
   measureRibbon();
  });
 }
 function viewControls(panel){
  const group=document.createElement('div');group.className='ribbon-group ribbon-options';group.setAttribute('role','group');group.setAttribute('aria-label','3D display');
  sizeLabel.classList.add('ribbon-slider');if(!sizeLabel.querySelector('span')){for(const n of [...sizeLabel.childNodes])if(n.nodeType===3)n.remove();const label=document.createElement('span');label.textContent='Point size';sizeLabel.prepend(label);}const pointControl=sizeLabel.querySelector('input');pointControl.value=$('job-point-size').value;pointControl.oninput=()=>{$('job-point-size').value=pointControl.value;window.JobUI.draw();};group.append(sizeLabel);
  const density=document.createElement('label');density.className='ribbon-slider';density.innerHTML='<span>Display detail</span><input id="ribbon-density" type="range" min="1" max="100" aria-label="Display detail"><output id="ribbon-density-value"></output>';
  density.querySelector('input').value=$('job-density').value;density.querySelector('output').textContent=$('job-density').value+'%';
  density.querySelector('input').oninput=e=>{$('job-density').value=e.target.value;$('job-density').dispatchEvent(new Event('input'));density.querySelector('output').textContent=e.target.value+'%';};group.append(density);
  const mode=document.createElement('label');mode.className='display-mode-label';mode.innerHTML='<span>Scan colour</span><select id="ribbon-display-mode"><option value="rgb">RGB / intensity</option><option value="intensity-gray">Greyscale intensity</option><option value="intensity-hue">Hue intensity</option><option value="bundle">Scan / bundle colours</option><option value="elevation">Elevation colours</option></select>';const select=mode.querySelector('select');select.value=window.JobUI?.displayMode?.()||'rgb';select.onchange=()=>window.JobUI?.setDisplayMode(select.value);group.append(mode);
  const tone=window.JobUI?.displayEnhancement?.()||{contrast:1,gamma:1};for(const [key,label] of [['contrast','Contrast'],['gamma','Brightness']]){const control=document.createElement('label');control.className='ribbon-slider';control.innerHTML=`<span>${label}</span><input id="ribbon-${key}" type="range" min="0.4" max="3" step="0.1" value="${tone[key]}" aria-label="${label}"><output id="ribbon-${key}-value">${Math.round(tone[key]*100)}%</output>`;control.title='100% is neutral. Changes the displayed scan and setup photographs only.';control.querySelector('input').oninput=e=>{const value=window.JobUI.displayEnhancement();value[key]=+e.target.value;window.JobUI.setDisplayEnhancement(value.contrast,value.gamma);};group.append(control);}
  const detail=document.createElement('button');detail.type='button';detail.className='ribbon-detail-button';detail.innerHTML=icon('fit')+'<span>Load detail here</span>';detail.title='Load a denser display of the current view. Full-density editing is independent of the display detail.';detail.onclick=()=>window.JobUI?.loadViewDetail(true);group.append(detail);
  const label=document.createElement('div');label.className='ribbon-group-label';label.textContent='3D display · display settings do not change exported points';group.append(label);panel.append(group);
 }
 function activate(name){
  if(!groups[name])return;closeRibbonMenu();window.ParameterHelp?.hideRibbonHint();parking.append(sizeLabel);current=name;document.body.classList.remove('ribbon-collapsed');document.body.dataset.ribbonTab=name;
  tabs.querySelectorAll('[role=tab]').forEach(b=>{const active=b.textContent===name;b.setAttribute('aria-selected',active);b.tabIndex=active?0:-1;});
  const panel=$('ribbon-panel');panel.setAttribute('aria-labelledby','ribbon-tab-'+name);panel.replaceChildren();for(const [label,...commands] of groups[name])panel.append(createGroup(label,commands));
  if(name==='View')viewControls(panel);$('ribbon-hint').textContent=({Clean:'Select scan → choose area → preview → review → apply. GuSAC is the primary tunnel method.',Alignment:'Create or import the horizontal alignment, then set heights at chainages. Use Model to build the tunnel.',Model:'Create 3D model from alignment → theoretical profiles → chainage sections → preview and build. Or draw and fit geometry.',Meshing:'Create or refine a mesh, or inspect circular openings.',Analyse:'Choose inspected data and reference, then review differences and export results.',Measure:'Pick points or geometry, calculate distances and angles, then save labelled measurements.'})[name]||'Choose a tool. Hover or focus for guidance.';
  sync();layoutRibbon();if(name==='Registration')window.RegistrationPanel.open();
 }
 function openMesh(operation='create',engine=null){
  viewTab('structures');const section=$('tab-structures'),create=section.querySelector('.mesh-primary-card'),refine=$('mesh-refine-name').closest('details'),transform=$('mesh-tx').closest('details'),reference=$('object-name').closest('details');section.dataset.meshTask=operation;
  create.hidden=operation!=='create';refine.hidden=!['taubin','quadric','largest_component','flip_normals','refine'].includes(operation);transform.hidden=operation!=='transform';reference.hidden=operation!=='reference';
  for(const block of [refine,transform,reference])if(!block.hidden)block.open=true;
  if(engine){$('mesh-engine').value=engine;$('mesh-engine').dispatchEvent(new Event('change'));}
  $('mesh-alignment-options').hidden=operation!=='create'||$('mesh-engine').value!=='alignment_grid';
  const title={create:'Create mesh',taubin:'Smooth mesh',quadric:'Reduce triangles',largest_component:'Keep largest piece',flip_normals:'Flip mesh normals',transform:'Transform mesh',reference:'Reference structures',refine:'Improve selected mesh'}[operation]||'Mesh tools';$('tool-drawer-title').textContent=title;
  $('mesh-inspect-openings').hidden=operation!=='refine';
  for(const b of refine.querySelectorAll('[data-mesh-refine]'))b.hidden=operation!=='refine'&&b.dataset.meshRefine!==operation;
  $('mesh-refine-smoothing').closest('label').hidden=!['taubin','refine'].includes(operation);$('mesh-refine-triangles').closest('label').hidden=!['quadric','refine'].includes(operation);
  const quick=$('tool-quick-actions');if(quick)quick.hidden=operation!=='create';section.querySelector('h2').textContent=title;
 }
 // Keep moved controls in a hidden parking area between panel renders.
 const parking=document.createElement('div');parking.hidden=true;parking.id='ribbon-parking';document.body.append(parking);const sizeLabel=$('point-size').closest('label');parking.append(sizeLabel);
 function measureRibbon(){document.documentElement.style.setProperty('--ts-ribbon-bottom',Math.ceil(ribbon.getBoundingClientRect().bottom)+'px');}
 if(window.ResizeObserver){const observer=new ResizeObserver(measureRibbon);observer.observe(ribbon);}window.addEventListener('resize',()=>{measureRibbon();layoutRibbon();});requestAnimationFrame(measureRibbon);
 function registerMeasureGroup(label,items){const commands=items.filter(x=>x&&typeof x.run==='function').map(x=>({...x,ico:x.ico||'measure',description:x.description||x.label}));if(!commands.length)return;let group=groups.Measure.find(x=>x[0]===label);if(!group){group=[label];groups.Measure.push(group);}for(const c of commands){const index=group.findIndex(x=>x&&x.id===c.id);if(index>=1)group[index]=c;else group.push(c);}if(current==='Measure')activate('Measure');}
 const originalActivate=activate; // retain original DOM control when changing tabs
 const switchRibbon=name=>{
  if(window.MeasureTools?.canDeactivate?.()===false&&!['Measure','View'].includes(name)){explainToolBlock('Finish the measurement point pick or calculation, or cancel it, before switching tools. View controls remain available.');return;}
  if(window.GeometryToolsUI?.canDeactivate?.()===false&&!['Model','View'].includes(name)){explainToolBlock('Finish or stop the geometry calculation before switching tools. View controls remain available.');return;}
  if(['Alignment','Model'].includes(name)&&window.CadUI&&!window.CadUI.modelSpace()){
   if(window.CadUI.showCloudReference({preserveView:true})===false)return;
  }else if(['Edit','Registration','Clean','Meshing','Analyse','Measure'].includes(name)&&window.CadUI?.modelSpace()){
   const mode=['Clean','Analyse','Measure'].includes(name)?name:'Edit';
   if(window.CadUI.enter(mode,{openTool:false,activateRibbon:false})===false)return;
  }
  parking.append(sizeLabel);originalActivate(name);
 };
 tabs.querySelectorAll('[role=tab]').forEach(b=>b.onclick=()=>switchRibbon(b.textContent));
 tabs.onkeydown=e=>{const names=Object.keys(groups),i=names.indexOf(current);let n;if(e.key==='ArrowRight')n=names[(i+1)%names.length];if(e.key==='ArrowLeft')n=names[(i+names.length-1)%names.length];if(e.key==='Home')n=names[0];if(e.key==='End')n=names.at(-1);if(n){e.preventDefault();switchRibbon(n);$('ribbon-tab-'+n).focus();}};
 let dialogMode='save',lastProjectId=null,projectTransition=false;
 function uiValues(){const values={};for(const id of ['station','width','band','tolerance','creep-limit','point-size','report-project','report-reference','report-surveyor','report-date','report-notes','report-start','report-end','report-step'])values[id]=$(id).value;return values;}
 async function storePending(){if(window.TargetUI)await window.TargetUI.saveIfDirty();}
 async function saveProject(action='save',name){
  if(busy||projectTransition)return;
  if(name===undefined&&!state?.project_id){await projectDialog('save');return;}
  const dialog=$('project-dialog'),submit=$('project-dialog-action');
  projectTransition=true;
  submit.disabled=$('project-dialog-close').disabled=$('project-copy-in').disabled=$('project-copy-manual').disabled=true;
  $('project-dialog-message').textContent='';
  try{
   await storePending();
   setBusy(true);
   const saved=await post('/api/project-action',{action,name:name??state.project_title,ui:uiValues()});
   if(dialog.open)dialog.close();
   state=await api('/api/status');sync();
   notice('Project saved: '+state.project_title+(saved.project_file?' · '+saved.project_file:''));
  }catch(e){
   if(dialog.open)$('project-dialog-message').textContent=e.message;
   else notice(e.message,true);
  }
  finally{
   projectTransition=false;
   submit.disabled=$('project-dialog-close').disabled=$('project-copy-in').disabled=$('project-copy-manual').disabled=false;
   setBusy(false);
  }
 }
 async function projectReady(showRecovery=true){
  if($('project-dialog').open)$('project-dialog').close();setBusy(false);resetEditingView();formAxis=null;scene.initialised=false;lastProjectId=Symbol('opened project');
  await window.JobUI?.projectOpened();await poll();viewTab('three');
  if(showRecovery){const report=await api('/api/project-recovery');if(report.needs_review){
   const total=report.unavailable?.length||0;const text=`Project opened with ${report.warnings.length} recovery notice(s)${total?` and ${total} unavailable asset or history record(s)`:''}. Available scans, models and current edits have been restored.\n\n`+report.warnings.slice(0,12).join('\n\n')+(report.warnings.length>12?'\n\nDownload the recovery report to review all notices.':'');
   showTunnelMessage('Project recovery',text,false,'Download recovery report',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='Tunnel_Studio_project_recovery.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  }}
 }
 function hasProjectWork(){return Boolean(state?.project_id||state?.job_item_count||state?.axis||state?.cloud||state?.structures?.length);}
 async function allowProjectClose(){
  if(!hasProjectWork())return true;
  const choice=await window.StorageCleanup.open({mode:'close'});
  return choice==='skipped'||choice==='cleaned';
 }
 async function closeProject(){
  if(busy||projectTransition)return;projectTransition=true;
  try{
   if(!await allowProjectClose())return;
   await storePending();setBusy(true);
   await post('/api/project-action',{action:'new',ui:uiValues()});
   await projectReady(false);
  }catch(e){notice(e.message,true);}finally{projectTransition=false;setBusy(false);}
 }
 async function projectNew(){
  if(busy||projectTransition)return;
  async function start(){
   if(busy||projectTransition)return;
   projectTransition=true;
   try{
    await storePending();setBusy(true);
    await post('/api/project-action',{action:'new',ui:uiValues()});
    await projectReady(false);
   }catch(e){notice(e.message,true);}
   finally{projectTransition=false;setBusy(false);}
  }
  if(hasProjectWork()){
   showTunnelMessage('Start a new project?','Tunnel Studio will keep a recovery copy of the current workspace. To update the named project, use Save before starting a new one.',false,'Start new project',start);
  }else await start();
 }
 async function openNamedProject(id){if(busy||projectTransition)return false;projectTransition=true;try{await storePending();setBusy(true);const response=await post('/api/project-action',{action:'open',id,ui:uiValues()});if(response.started){const job=await window.JobUI.waitReady(job=>{$('project-dialog-message').textContent=job.message||'Checking saved project…';},{skipSync:true});if(job?.cancelled)throw Error('Project opening was cancelled. The previous workspace remains active.');}await projectReady();return true;}catch(e){$('project-dialog-message').textContent=e.message;notice(e.message,true);return false;}finally{projectTransition=false;setBusy(false);}}
 const storageSize=window.StorageInventory.bytes;
 let storageReviewVersion=0;
 const selectedProjectIds=new Set();
 function projectSizeSummary(p){
  const total=storageSize(p.total_bytes),free=storageSize(p.exclusive_bytes),shared=storageSize(p.shared_bytes);
  return `Uses ${total} · frees ${free}${p.shared_bytes?` · shared ${shared}`:''}`;
 }
 async function refreshWorkspaceStorage(){
  const host=$('workspace-storage-overview');if(!host)return;host.innerHTML='<p>Calculating current imported-data storage…</p>';
  try{
   const data=await api('/api/workspace-storage');const rows=data.items||[];
   if(!rows.length){host.innerHTML='<div class="workspace-storage-summary"><h3>Current imported data</h3><p>No scan or reference-mesh cache is loaded in the current workspace.</p></div>';return;}
   const body=rows.map(r=>`<div class="workspace-storage-row"><span><b>${esc(r.name)}</b><small>${esc((r.source_format||r.kind||'data').toUpperCase())}${r.source_name&&r.source_name!==r.name?' · '+esc(r.source_name):''}</small></span><span>${esc(storageSize(r.cache_bytes))}${r.protected_bytes?`<small>protected/shared ${esc(storageSize(r.protected_bytes))}</small>`:''}</span></div>`).join('');
   host.innerHTML=`<div class="workspace-storage-summary"><h3>Current imported data</h3><p><b>${esc(storageSize(data.cache_bytes))}</b> internal cache across ${rows.length} imported scan/model item(s).</p><p class="tiny">E57, LAS/LAZ, PLY, PTS, XYZ/TXT/CSV, Leica HSPCA/RTC360, OBJ/ZIP reference meshes and portable-project assets all use the same reference-aware storage rules. DXF/model JSON, alignments, ring observations and survey-control imports are stored mainly as project metadata and follow the same project deletion rules. Original source files outside Tunnel Studio are never deleted by project cleanup.</p>${body}</div>`;
  }catch(e){host.innerHTML='<div class="workspace-storage-summary"><h3>Current imported data</h3><p>Storage summary unavailable: '+esc(e.message)+'</p></div>';}
 }
 async function refreshProjectList(){
  window.ProjectBrowser?.dispose();
  clearProjectStoragePanel();window.StorageInventory.reset();
  $('project-list').innerHTML='<p>Calculating project sizes…</p>';
  await refreshWorkspaceStorage();
  const data=await api('/api/projects');
  const valid=new Set(data.projects.filter(p=>!p.active).map(p=>p.project_id));for(const id of [...selectedProjectIds])if(!valid.has(id))selectedProjectIds.delete(id);
  const browserProjects=data.projects.map(p=>({...p,size_summary:projectSizeSummary(p)}));
  const rows=browserProjects.map(p=>window.ProjectBrowser.card(p)).join('')||'<p>No saved projects in this data folder. Choose Browse a project folder to look elsewhere, or Save to create a named project.</p>';
  $('project-list').innerHTML=`<div class="project-bulk-toolbar"><button type="button" id="project-select-all">Select closed projects</button><button type="button" id="project-clear-selection">Clear</button><span id="project-selection-count"></span><button type="button" id="project-delete-selected" class="danger">Delete selected…</button></div>${data.purge?.running?`<div class="project-purge-status">Background cleanup is releasing up to <b>${esc(storageSize(data.purge.bytes))}</b> from ${Number(data.purge.jobs)||1} deletion job(s). You can keep working; free disk space rises as Windows removes the quarantined files.</div>`:''}${window.ProjectBrowser.layout(rows)}`;
  const updateSelection=()=>{const n=selectedProjectIds.size;const c=$('project-selection-count');if(c)c.textContent=n?`${n} selected`:'Nothing selected';const d=$('project-delete-selected');if(d)d.disabled=!n;};
  for(const b of $('project-list').querySelectorAll('.project-open'))b.onclick=()=>openNamedProject(b.dataset.project);
  for(const b of $('project-list').querySelectorAll('.project-delete'))b.onclick=()=>reviewDeleteProjects([b.dataset.deleteProject]);
  for(const c of $('project-list').querySelectorAll('[data-project-check]')){c.checked=selectedProjectIds.has(c.dataset.projectCheck);c.onchange=()=>{c.checked?selectedProjectIds.add(c.dataset.projectCheck):selectedProjectIds.delete(c.dataset.projectCheck);updateSelection();};}
  $('project-select-all').onclick=()=>{for(const p of data.projects)if(!p.active)selectedProjectIds.add(p.project_id);for(const c of $('project-list').querySelectorAll('[data-project-check]'))c.checked=!c.disabled;updateSelection();};
  $('project-clear-selection').onclick=()=>{selectedProjectIds.clear();for(const c of $('project-list').querySelectorAll('[data-project-check]'))c.checked=false;updateSelection();};
  $('project-delete-selected').onclick=()=>reviewDeleteProjects([...selectedProjectIds]);updateSelection();
  window.ProjectBrowser.mount(browserProjects,{openNamed:openNamedProject,openPath:path=>window.TunnelRibbon.openProjectPath(path),reload:refreshProjectList});
 }
 function projectStoragePanel(){
  let panel=$('project-storage-review');
  if(panel)return panel;
  panel=document.createElement('div');panel.id='project-storage-review';panel.className='project-storage-review';panel.hidden=true;
  $('project-list').insertAdjacentElement('afterend',panel);
  return panel;
 }
 function clearProjectStoragePanel(){storageReviewVersion++;const p=$('project-storage-review');if(p){p.hidden=true;p.replaceChildren();}}
 async function reviewDeleteProject(id){return reviewDeleteProjects([id]);}
 async function reviewDeleteProjects(ids){
  ids=[...new Set((ids||[]).filter(Boolean))];if(!ids.length)return;
  if(busy||projectTransition)return;
  const version=++storageReviewVersion;const panel=projectStoragePanel();panel.hidden=false;panel.textContent=`Calculating storage for ${ids.length===1?'project':ids.length+' projects'}…`;$('project-dialog-message').textContent='Calculating project storage…';
  try{
   const r=await api('/api/project-storage?'+new URLSearchParams({ids:ids.join(',')}));
   if(version!==storageReviewVersion)return;
   panel.replaceChildren();
   if(r.active){panel.textContent='Your currently open project is protected. Close it before reviewing it for deletion.';return;}
   if(!r.review_token){panel.textContent='File ownership could not be verified. Refresh the project list and review again.';return;}
   const title=document.createElement('h3');title.textContent=ids.length===1?'Delete project permanently':`Delete ${ids.length} projects permanently`;panel.append(title);
   const text=document.createElement('p');text.innerHTML=`Selected project data: <b>${storageSize(r.total_bytes)}</b><br>Will be released: <b>${storageSize(r.exclusive_bytes)}</b><br>Shared/protected and kept: <b>${storageSize(r.shared_bytes)}</b>`;panel.append(text);
   if(r.project_titles?.length){const names=document.createElement('p');names.className='tiny';names.textContent=r.project_titles.join(' · ');panel.append(names);}
   window.StorageInventory.previewRemoval(panel,r.sample_files,r.exclusive_files);
   const note=document.createElement('p');note.className='tiny';note.textContent='Files are first moved out of the live workspace using same-drive renames, so the projects disappear quickly. Windows then releases the physical disk space in the background. Data still referenced by an unselected project or startup recovery snapshot is protected.';panel.append(note);
   const actions=document.createElement('div');actions.className='report-actions';
   const cancel=document.createElement('button');cancel.textContent='Cancel';cancel.onclick=()=>clearProjectStoragePanel();
   const confirm=document.createElement('button');confirm.className='danger';confirm.textContent=`Delete ${ids.length===1?'project':ids.length+' projects'} & free ${storageSize(r.exclusive_bytes)}`;
   confirm.onclick=async()=>{
    if(version!==storageReviewVersion||busy||projectTransition)return;
    const label=ids.length===1?`project "${r.project_titles?.[0]||r.project_title}"`:`${ids.length} selected projects`;
    if(!window.confirm(`Permanently delete ${label} and release ${storageSize(r.exclusive_bytes)} of data used only by the selection?\n\nThe projects disappear immediately; physical disk cleanup continues in the background. This cannot be undone.`))return;
    projectTransition=true;
    try{
     setBusy(true);$('project-dialog-message').textContent='Removing projects from the workspace…';
     const out=await post('/api/project-action',{action:'delete_projects',ids,confirm:true,expected_bytes:r.exclusive_bytes,expected_token:r.review_token});
     for(const id of ids)selectedProjectIds.delete(id);clearProjectStoragePanel();
     if(r.active){await projectReady(false);await projectDialog('open');}else await refreshProjectList();
     const freeing=out.purge_bytes??r.exclusive_bytes;
     $('project-dialog-message').textContent=`Projects removed. ${storageSize(freeing)} is being released in the background.`;
     notice(`Projects removed · releasing ${storageSize(freeing)} in background.`);
    }catch(e){$('project-dialog-message').textContent=e.message;notice(e.message,true);}finally{projectTransition=false;setBusy(false);}
   };
   actions.append(cancel,confirm);panel.append(actions);$('project-dialog-message').textContent='Review the combined storage summary below before permanent deletion.';
  }catch(e){if(version!==storageReviewVersion)return;panel.textContent='Could not review project storage: '+e.message;$('project-dialog-message').textContent=e.message;notice(e.message,true);}
 }
 async function reviewOrphanCleanup(){
  if(busy||projectTransition)return;
  const version=++storageReviewVersion;const panel=projectStoragePanel();panel.hidden=false;panel.textContent='Scanning for unreferenced internal caches…';$('project-dialog-message').textContent='Scanning for unreferenced internal caches…';
  try{
   const r=await api('/api/project-orphans');
   if(version!==storageReviewVersion)return;
   panel.replaceChildren();
   if(!r.bytes){panel.textContent='No unreferenced Tunnel Studio internal cache data was found.';$('project-dialog-message').textContent='No unreferenced internal cache data was found.';return;}
   if(!r.review_token){panel.textContent='File ownership could not be verified. Review unused working files again.';return;}
   const title=document.createElement('h3');title.textContent='Clean unreferenced data';panel.append(title);
   const text=document.createElement('p');text.textContent=`${storageSize(r.bytes)} can be reclaimed from ${r.files} unreferenced internal file(s) and ${r.folders} abandoned generated folder(s).`;panel.append(text);
   window.StorageInventory.previewRemoval(panel,r.sample_files,r.files);
   const note=document.createElement('p');note.className='tiny';note.textContent='Only Tunnel Studio internal cache formats are eligible. Neutral exchange/source files such as E57, LAS/LAZ, PTS, PLY, XYZ, CSV and OBJ are never removed by this cleanup.';panel.append(note);
   const actions=document.createElement('div');actions.className='report-actions';
   const cancel=document.createElement('button');cancel.textContent='Cancel';cancel.onclick=()=>clearProjectStoragePanel();
   const confirm=document.createElement('button');confirm.className='danger';confirm.textContent='Clean & free '+storageSize(r.bytes);
   confirm.onclick=async()=>{
    if(version!==storageReviewVersion||busy||projectTransition)return;
    if(!window.confirm(`Permanently remove ${storageSize(r.bytes)} of unreferenced Tunnel Studio internal cache data?\n\nFiles still referenced by any project or recovery snapshot are protected.`))return;
    projectTransition=true;try{setBusy(true);$('project-dialog-message').textContent='Moving unreferenced caches out of the live workspace…';const out=await post('/api/project-action',{action:'clean_orphans',confirm:true,expected_bytes:r.bytes,expected_token:r.review_token});clearProjectStoragePanel();await refreshProjectList();const freeing=out.purge_bytes??out.bytes;$('project-dialog-message').textContent='Unreferenced data removed from the workspace. '+storageSize(freeing)+' is being released in the background.';notice('Storage cleanup started · releasing '+storageSize(freeing)+' in background.');}catch(e){$('project-dialog-message').textContent=e.message;notice(e.message,true);}finally{projectTransition=false;setBusy(false);}
   };
   actions.append(cancel,confirm);panel.append(actions);$('project-dialog-message').textContent='Review the cleanup summary below.';
  }catch(e){if(version!==storageReviewVersion)return;panel.textContent='Could not scan project storage: '+e.message;$('project-dialog-message').textContent=e.message;notice(e.message,true);}
 }
 window.StorageInventory.bind(reviewOrphanCleanup);
 $('storage-cleanup-open').onclick=async()=>{
  if(busy||projectTransition)return;
  if(await window.StorageCleanup.open({mode:'manual'})==='cleaned')await refreshProjectList();
 };
 $('project-dialog').addEventListener('close',()=>{window.ProjectBrowser?.dispose();clearProjectStoragePanel();selectedProjectIds.clear();});
 window.ProjectLocation?.bind(async () => {
  if (busy || projectTransition) return;
  projectTransition = true;
  try { await window.ProjectLocation.saveCopy(storePending, uiValues); }
  finally { projectTransition = false; }
 });
 async function projectDialog(mode,source){
  const dialog=$('project-dialog'),name=$('project-name');
  const saving=['save','save_as'].includes(mode),browsing=['open','storage'].includes(mode);
  dialogMode=mode;
  dialog.classList.toggle('project-browsing',mode==='open');
  window.ProjectLocation?.show(mode);
  $('project-dialog-title').textContent=({save:'Save project',save_as:'Save As',open:'Open project',storage:'Storage manager',export:'Export'})[mode];
  const intro=$('project-dialog-intro');
  intro.textContent=({
   save:'',save_as:'Save a separately named project in the current data folder.',
   open:'Choose a saved project to open.',storage:'See where project data is stored and manage disk space.',
   export:'Choose the file format and the scan to deliver.'
  })[mode];
  intro.hidden=!intro.textContent;
  $('project-save-fields').hidden=!saving;
  $('project-open-fields').hidden=!browsing;
  $('project-export-fields').hidden=mode!=='export';
  $('project-dialog-action').hidden=browsing;
  $('project-dialog-action').textContent=mode==='export'?'Save as…':mode==='save_as'?'Save project copy':'Save';
  $('project-dialog-message').textContent='';
  $('export-download').hidden=true;
  name.value=(state?.project_title&&state.project_title!=='Untitled project'?state.project_title:'New project')+(mode==='save_as'?' — copy':'');
  if(source){$('export-source').value=source;$('export-format').value='e57';}
  if(mode==='export'){window.ExportUI?.open(source);formatHelp();}
  if(!dialog.open)dialog.showModal();
  if(saving){name.focus();name.select();}
  if(browsing)await refreshProjectList();
 }
 const help={json:'Selected geometry and metadata, including coordinates and measurements, as structured JSON.',tstudio:'Complete editable Tunnel Studio project, including cached scans, target pairs, registration, geometry and point-edit history. Open in Tunnel Studio. Large scans produce large files.',e57:'Recommended neutral exchange for Leica 3DR and recent Register 360 versions that support unstructured E57. Double-precision XYZ, complete intensity and 8-bit RGB. No scanner setups, panoramas or row/column grids. Register 360 Auto Cloud / Auto Target are unavailable for unstructured imports.',las:'LAS 1.2 points for 3DR and supported Register 360 editions. XYZ quantised to 0.1 mm; intensity scaled to 16-bit; RGB 16-bit. No native Leica project data.',laz:'Compressed LAS for 3DR and other compatible readers. Same precision and attributes as LAS. For Register 360 prefer E57 or LAS; check your version.',pts:'ASCII point cloud for Leica exchange. XYZ in metres, optional intensity scaled to -2048…2047 and 8-bit RGB. Large text files. No scanner setup grid.',xyz:'Plain X Y Z coordinates, metres. No intensity or colour. Use custom ASCII import where supported.',csv:'CSV with E,N,H and complete intensity/RGB channels. Use 3DR/custom ASCII column mapping. Target control is a separate file.',ply:'ASCII PLY with double XYZ and available intensity/8-bit RGB. Useful for 3DR and other point-cloud/mesh tools. Use E57 for Register 360.',obj:'Polygon meshes/reference structures for 3DR and modelling software. No scan points, materials or tunnel calculation results.'};
 function formatHelp(){window.ExportUI?.changed();$('format-help').textContent=help[$('export-format').value];$('export-source-label').hidden=['obj','tstudio'].includes($('export-format').value);$('export-result').textContent='';$('export-download').hidden=true;}
 $('export-format').onchange=formatHelp;$('export-source').onchange=formatHelp;$('project-dialog-close').onclick=()=>$('project-dialog').close();$('project-clean-orphans').onclick=reviewOrphanCleanup;
 $('project-dialog').addEventListener('cancel',e=>{if(projectTransition)e.preventDefault();});
 $('project-name').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.isComposing){e.preventDefault();$('project-dialog-action').click();}
 });
 $('project-dialog-action').onclick=async()=>{
  if(dialogMode==='export'){await window.ExportUI?.start(storePending);return;}
  if(dialogMode!=='save'&&dialogMode!=='save_as')return;
  const field=$('project-name'),name=field.value.trim();
  if(!name){$('project-dialog-message').textContent='Enter a project name.';field.focus();return;}
  await saveProject(dialogMode,name);
 };
 $('export-download').onclick=()=>window.ExportUI?.saveAgain();
 async function openPortable(start){if(busy||projectTransition)return false;projectTransition=true;try{await storePending();setBusy(true);$('project-dialog-message').textContent='Checking and opening project…';await start();const job=await window.JobUI.waitReady(job=>{$('project-dialog-message').textContent=job.message||'Opening project…';},{skipSync:true});if(job?.cancelled)throw Error('Project opening was cancelled. The previous workspace remains active.');await projectReady();return true;}catch(error){$('project-dialog-message').textContent=error.message;notice(error.message,true);return false;}finally{projectTransition=false;setBusy(false);}}
 const openProjectPath=path=>openPortable(()=>post('/api/load',{kind:'project',path}));
 const openProjectFile=file=>openPortable(()=>api('/api/upload?'+new URLSearchParams({kind:'project',name:file.name}),{method:'POST',headers:{'X-Tunnel-Token':window.TUNNEL_TOKEN},body:file}));
 $('project-file').accept='.tstudio,.zip,.json';
 $('project-import-file').onclick=async()=>{if(busy||projectTransition)return;if(!window.pywebview?.api?.choose_large_file){$('project-file').click();return;}try{const paths=await window.pywebview.api.choose_large_file('project',false);if(paths?.length)await openProjectPath(paths[0]);}catch(error){notice(error.message,true);}};
 $('project-file').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{await openProjectFile(f);}finally{e.target.value='';}};
 function sync(){window.RegistrationPanel?.refresh();for(const [, ...commands] of groups[current])for(const c of commands){const b=$('ribbon-panel').querySelector(`[data-command="${c.id}"]`);if(!b)continue;const reason=availability(c);b.setAttribute('aria-disabled',!!reason);b.title=c.label+' — '+(reason||c.description);b.classList.toggle('selected-tool',!!c.target&&$(c.target)?.classList.contains('chosen'));}let label=$('ribbon-project-title');if(!label){label=document.createElement('span');label.id='ribbon-project-title';document.querySelector('header').append(label);}label.textContent=state?.project_title||'Untitled project';if(state?.project_id!==lastProjectId){lastProjectId=state?.project_id;for(const [id,value] of Object.entries(state?.ui||{}))if($(id))$(id).value=value;}
 $('project-dialog-action').disabled=busy||projectTransition;$('project-import-file').disabled=busy;if(dialogMode==='export')window.ExportUI?.sync();}

 document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key.toLowerCase()==='s'){e.preventDefault();if(!busy&&!document.querySelector('dialog[open]'))e.shiftKey?projectDialog('save_as'):saveProject();}if(e.ctrlKey&&e.key==='F1'){e.preventDefault();collapse.click();}if(e.ctrlKey&&!['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)&&!document.querySelector('dialog[open]')){if(e.key.toLowerCase()==='z'){e.preventDefault();$(e.shiftKey?'redo-edit':'undo-edit').click();}if(e.key.toLowerCase()==='y'){e.preventDefault();$('redo-edit').click();}}});
 // Main editing controls now live only in the top ribbon; no secondary workspace strip.
 new MutationObserver(sync).observe(document.querySelector('.edit-toolbar'),{subtree:true,attributes:true,attributeFilter:['disabled','class']});
 function releaseTool(){
  if(!openTool)return null;
  const section=openTool,origin=toolOrigin,next=toolNext,id=section.id.slice(4),host=document.createElement('div');host.id=section.id+'-window';host.className='detached-tool-host';document.body.append(host);
  const quick=$('tool-drawer-body').querySelector('#tool-quick-actions');if(quick){quick.id=section.id+'-quick-actions';host.append(quick);}host.append(section);openTool=null;toolOrigin=null;toolNext=null;$('tool-drawer').hidden=true;
  return {id:section.id,element:host,title:toolTitles[id]||'Tool',restore:()=>{if(next?.parentNode===origin)origin.insertBefore(section,next);else origin.append(section);quick?.remove();host.remove();section.classList.remove('active','drawer-active');viewTab(id,{refresh:false});}};
 }
 const regFlow=document.createElement('section');
 regFlow.id='tab-registration-flow';regFlow.className='tab';
 regFlow.innerHTML=`<div class="reg-card">
  <div class="tool-mode-tabs" role="group" aria-label="Registration mode">
   <button type="button" data-reg-flow-mode="targets">Targets</button>
   <button type="button" data-reg-flow-mode="cloud">Cloud-to-cloud</button>
   <button type="button" data-reg-flow-mode="control">Control</button>
  </div>
  <h3 id="reg-flow-selection"></h3><p id="reg-flow-help"></p>
  <button id="reg-flow-action" type="button" class="primary"></button>
  <details id="reg-flow-more"><summary>More registration tools</summary><div class="reg-actions" id="reg-flow-secondary"></div></details>
  <details><summary>Local diagnostics</summary>
   <label><input type="checkbox" id="reg-flow-log"> Record registration timings and navigation on this device</label>
   <p>Cloud reading and sampling, solving, and time from job completion notice to report display are recorded separately from navigation. Export only when needed.</p>
   <button id="reg-flow-export" type="button">Export diagnostics JSON</button>
  </details>
 </div>`;
 $('tab-registration').before(regFlow);
 const logKey='tunnelstudio.registrationDiagnostics';
 let regMode='targets',registrationSecondaryKey='',diagnosticRun=0;
 function diagnostics(){
  try{
   const value=JSON.parse(localStorage.getItem(logKey)||'null');
   if(value&&Array.isArray(value.timing)&&Array.isArray(value.navigation))return value;
  }catch(_error){}
  return {enabled:false,timing:[],navigation:[]};
 }
 function writeDiagnostics(value){
  try{localStorage.setItem(logKey,JSON.stringify(value));}
  catch(_error){return false;}
  return true;
 }
 function recordDiagnostic(stream,row){
  const value=diagnostics();if(!value.enabled)return;
  value[stream].push({...row,at:new Date().toISOString()});
  value[stream]=value[stream].slice(-250);
  writeDiagnostics(value);
 }
 const diagnosticEnabled=()=>diagnostics().enabled===true;
 window.RegistrationDiagnostics={
  enabled:diagnosticEnabled,
  begin(operation,mode=regMode){
   if(!diagnosticEnabled())return null;
   return {id:Date.now().toString(36)+'-'+(++diagnosticRun),operation,mode};
  },
  finish(run,outcome){if(run)recordDiagnostic('navigation',{...run,event:outcome});},
  report(run,phases,ready){
   if(!run)return;
   requestAnimationFrame(()=>requestAnimationFrame(()=>recordDiagnostic('timing',
    {...run,cloud_read_ms:phases?.cloud_read_ms??null,solve_ms:phases?.solve_ms??null,
     review_display_ms:Math.max(0,performance.now()-ready)})));
  }
 };
 $('reg-flow-log').checked=diagnosticEnabled();
 $('reg-flow-log').onchange=()=>{
  const value=diagnostics();value.enabled=$('reg-flow-log').checked;
  if(!writeDiagnostics(value)){notice('Local diagnostics storage is unavailable.',true);$('reg-flow-log').checked=false;}
 };
 $('reg-flow-export').onclick=()=>{
  const {timing,navigation}=diagnostics();
  const url=URL.createObjectURL(new Blob([JSON.stringify({schema_version:1,timing,navigation},null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='Tunnel_Studio_Registration_Diagnostics.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 };
 function registrationScope(){
  const job=window.JobUI,inventory=job?.inventory?.()||{};
  const bundle=inventory.bundles?.find(b=>b.id===window.WorkspaceUI?.selectedBundleId?.());
  if(bundle)return {state:'bundle',bundle};
  const selected=job?.selectedItems?.()||[];
  const scans=selected.map(id=>inventory.items?.find(x=>x.id===id)).filter(x=>x?.kind==='scan'&&!x.registered_merge);
  return {state:['none','single','pair'][scans.length]||'multiple',scans};
 }
 function focusRegistrationSelection(){
  $('job-list').querySelector('[data-bundle], [data-item], button, input')?.focus();
  notice('Select the required setup or bundle in Project Explorer.');
 }
 function openTargetTools(){
  registrationDrawerMode('search');
  return runRegistration('reg-pick');
 }
 function registrationAction(scope,id){
  if(id==='fit-targets')return openTargetTools();
  if(id==='match-targets')return window.ScanRegistrationUI.solveSelected();
  if(id==='optimise-targets')return window.BundleAdjustmentUI.optimise(scope.bundle.id);
  if(id==='refine-bundle')return window.BundleAdjustmentUI.refine(scope.bundle.id);
  if(id==='fit-control')return window.TargetUI.openBundle(scope.bundle.id);
  if(id==='import-control')return window.TargetUI.importControl();
  if(['choose-cloud-pair','choose-cloud-neighbour','refine-pair'].includes(id))return window.VisualRegistrationUI.open({cloud:true,fixed:scope.scans?.[0]?.id,moving:scope.scans?.[1]?.id});
  return focusRegistrationSelection();
 }
 function renderRegistrationPanel(){
  if(!regFlow.classList.contains('drawer-active'))return;
  const scope=registrationScope(),[action]=window.RibbonLayout.registrationActions(scope.state,regMode);
  const labels={none:'No setups selected',single:scope.scans?.[0]?.name,pair:scope.scans?.map(s=>s.name).join(' ↔ '),
   bundle:scope.bundle?scope.bundle.name+' · '+scope.bundle.scan_ids.length+' setups':'',multiple:'Multiple setups selected'};
  const help={targets:'Use measured target centres. Bundle optimisation uses saved Fit pairs.',
   cloud:'Set the rough pose, refine the overlapping surfaces and review before applying.',
   control:'Import and map control coordinates, then fit and review the selected bundle.'};
  $('reg-flow-selection').textContent=labels[scope.state];
  $('reg-flow-help').textContent=help[regMode];
  const button=$('reg-flow-action');
  button.dataset.registrationAction=action.id;button.textContent=action.label;button.disabled=busy;
  button.onclick=async()=>{try{if(!busy)await registrationAction(scope,action.id);}catch(e){notice(e.message,true);}};
  for(const b of regFlow.querySelectorAll('[data-reg-flow-mode]')){
   const active=b.dataset.regFlowMode===regMode;b.classList.toggle('chosen',active);b.setAttribute('aria-pressed',String(active));
  }
  const secondaryKey=regMode+':'+scope.state;
  if(secondaryKey===registrationSecondaryKey)return;
  registrationSecondaryKey=secondaryKey;
  const secondary=$('reg-flow-secondary');secondary.replaceChildren();
  const add=(label,run)=>{
   const b=document.createElement('button');b.type='button';b.textContent=label;
   b.onclick=()=>{if(!busy)Promise.resolve(run()).catch(e=>notice(e.message,true));};
   secondary.append(b);
  };
  if(regMode==='targets'){
   if(scope.state==='single'){
    add('Find survey targets',()=>runRegistration('reg-detect'));
    add('Manage targets',()=>window.TargetUI.openManager());
    add('Setup view',()=>$('job-setup-view').click());
   }
   add('Saved links and results',()=>window.ScanRegistrationUI.reviewPending());
  }else if(regMode==='cloud'){
   add('Visual alignment only',()=>window.VisualRegistrationUI.open());
   add('Advanced loop closure',()=>window.BundleAdjustmentUI.open());
  }else if(scope.state!=='none'){
   add('Import control CSV',()=>window.TargetUI.importControl());
   if(scope.state==='single')add('Single-scan control tools',async()=>{
    if(await window.JobUI.ensureRegistrationSource()===false)return;
    viewTab('registration',{skipPreview:true});registrationDrawerMode('match');
   });
  }
  $('reg-flow-more').hidden=!secondary.childElementCount;
 }
 function setRegistrationMode(mode){
  if(!mode||mode===regMode)return;
  recordDiagnostic('navigation',{event:'mode_switch',from:regMode,to:mode,selection:registrationScope().state});
  regMode=mode;renderRegistrationPanel();
 }
 for(const button of regFlow.querySelectorAll('[data-reg-flow-mode]'))button.onclick=()=>setRegistrationMode(button.dataset.regFlowMode);
 window.RegistrationPanel={
  open(mode){if(busy)return;setRegistrationMode(mode);if(openTool!==regFlow)viewTab('registration-flow');renderRegistrationPanel();},
  refresh:renderRegistrationPanel
 };
 window.TunnelRibbon={openMesh,registerMeasureGroup,exportProject:async()=>{await projectDialog('export');$('export-format').value='tstudio';$('export-format').dispatchEvent(new Event('change'));},openProjectPath,openProjectFile,releaseTool,sync,view:viewTab,activate:switchRibbon,export:(source)=>projectDialog('export',source),storage:()=>projectDialog('storage'),openImport,openLocalImport,closeTool,prepareTool,explainToolBlock,surfaceDrawerMode,registrationDrawerMode};switchRibbon('Home');document.body.classList.add('ribbon-ready','single-workspace','job-active','panel-hidden');closeTool();
})();
