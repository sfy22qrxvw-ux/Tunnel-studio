/* Point cloud and calibrated photographs share the main scanner camera. */
(()=>{
 'use strict';
 let frame=null,photo=null,photoSize=0,picked=null,epoch=0,opening=false,picking=false,saving=false,changing=false,attaching=false,fitting=false,pendingPoint=0,activation=Promise.resolve(),preferred='auto',blending=false,pendingBlend=null,lastMixed=50;
 const bar=document.createElement('section');bar.id='setup-camera-bar';bar.hidden=true;bar.setAttribute('aria-label','Scanner setup view');
 bar.innerHTML=`<div class="setup-camera-actions"><b>3D setup view</b><label>Setup <select id="setup-camera-choice"></select></label><label>Display <select id="setup-camera-display"><option value="photo">Photographs</option><option value="points">Point cloud</option><option value="both">Both</option></select></label><label class="setup-blend" id="setup-blend-control"><span>Images</span><input type="range" id="setup-camera-blend" min="0" max="100" step="1" value="50" aria-label="Image and point visibility balance"><span>Points</span><output id="setup-camera-blend-value" for="setup-camera-blend"></output></label><label>Photo detail <select id="setup-photo-detail"><option value="1024">Fast</option><option value="2048">Detailed</option></select></label><button id="setup-camera-pick">Pick point</button><button id="setup-camera-detail">Refresh point detail</button><label><input type="checkbox" id="setup-show-stations"> Setup spheres</label><button id="setup-camera-exit">Return to normal 3D</button></div><p id="setup-camera-status" role="status"></p><div id="setup-camera-target" hidden><span id="setup-camera-coordinates"></span><label>Target name <input id="setup-camera-name" maxlength="60" placeholder="For example: dam1"></label><button id="setup-camera-save">Save target</button><button id="setup-camera-fit">Fit target centre</button></div><details id="setup-photo-attach"><summary>Load scanner photographs from the Leica project folder</summary><p>Use the original main folder containing SetupsFolder. Existing scan points and positions are kept.</p><div class="setup-photo-folder"><input id="setup-photo-path" aria-label="Original Leica project folder" placeholder="Paste the full Leica project folder path"><button id="setup-photo-browse">Choose folder…</button><button id="setup-photo-attach-button">Load photographs</button></div></details>`;
 document.querySelector('.job-view').before(bar);
 const mode=()=>$('setup-camera-display').value;
 function blendUI(){const value=mode()==='photo'?0:mode()==='points'?100:Math.round(window.JobUI.setupBlend()*100),text='Images '+(100-value)+'% · Points '+value+'%';$('setup-camera-blend').value=value;$('setup-camera-blend').setAttribute('aria-valuetext',text);$('setup-camera-blend-value').textContent=photo?(100-value)+'% / '+value+'%':'Photographs unavailable';$('setup-blend-control').title=photo?'Drag towards Images or Points. The display switches automatically.':'Load scanner photographs to blend them with points.';}
 const blocking=()=>opening||picking||saving||changing||attaching||fitting||pendingPoint||busy;
 const working=()=>blocking()||blending;
 function controls(){
  const blocked=working();bar.setAttribute('aria-busy',blocked?'true':'false');
  for(const id of ['setup-camera-choice','setup-camera-display','setup-camera-pick','setup-photo-path','setup-photo-browse','setup-photo-attach-button','setup-show-stations'])$(id).disabled=blocked||!frame;
  $('setup-photo-detail').disabled=blocked||!photo||mode()==='points';$('setup-camera-detail').disabled=blocked||!frame||mode()==='photo';
  for(const id of ['setup-camera-name','setup-camera-save','setup-camera-fit'])$(id).disabled=blocked||!picked;
  for(const option of $('setup-camera-display').options)option.disabled=option.value!=='points'&&!photo;
  $('setup-camera-blend').disabled=blocking()||!frame||!photo;blendUI();$('setup-camera-exit').disabled=false;
 }
 function status(text){$('setup-camera-status').textContent=text;}
 function ready(){if(!frame)return;const text=mode()==='points'?'Only this setup’s point cloud is shown.':'Scanner photographs'+(mode()==='both'?' with cloud points.':'.');status(frame.name+' · '+text+' Drag to look around; wheel to zoom. Point detail updates automatically.'+($('setup-show-stations').checked?' Double-click a setup sphere to move there.':'')+(frame.photo_notice?' '+frame.photo_notice:photo?'':' Photographs are not attached. Use Load scanner photographs below.'));}
 async function loadPhotos(generation){const source=frame,view=photo,size=+$('setup-photo-detail').value;if(!view)throw Error('No photographs are attached to this setup.');const loaded=await window.JobUI.loadSetupPhotos(view,size,(n,total)=>{if(generation===epoch)status('Loading photographs for '+source.name+' · '+n+' / '+total+' faces…');});if(generation===epoch&&loaded)photoSize=size;return loaded;}
 async function open(id){
  if(working()){notice('Wait for the current setup operation to finish before switching.');return false;}
  const x=id?window.JobUI.scanItems().find(x=>x.id===id):window.JobUI.selectedItem();
  if(!x||x.has_setup_view===false||(x.kind&&x.kind!=='scan')){notice('Select an individual scan with a known scanner position.',true);return false;}
  const generation=++epoch;opening=true;blending=false;pendingBlend=null;picked=null;photo=null;photoSize=0;$('setup-camera-target').hidden=true;bar.hidden=false;
  $('setup-camera-choice').innerHTML=window.JobUI.scanItems().filter(x=>x.has_setup_view).map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');$('setup-camera-choice').value=x.id;
  if($('scene-info-dialog')?.open)$('scene-info-dialog').close();window.TunnelRibbon?.view('job');status('Opening '+x.name+' in the main 3D view…');controls();
  try{
   const active=activation.then(()=>generation===epoch?window.ScanRegistrationUI.activateTargets(x.id):undefined);activation=active.catch(()=>{});await active;if(generation!==epoch)return false;
   const result=await post('/api/setup-camera',{action:'open',id:x.id});if(generation!==epoch)return false;
   if(result.id!==x.id||!result.key||result.origin?.length!==3||result.orientation?.length!==3)throw Error('No valid scanner position was returned for this setup.');frame=result;
   let photoError='';if(frame.has_setup_photos){try{const p=await post('/api/setup-photos',{action:'open',id:x.id});if(generation!==epoch)return false;if(p.available&&p.id===x.id&&p.key===frame.key)photo=p;else photoError=p.message||'Photographs are not available for this setup.';}catch(e){photoError=e.message;}}
   const display=photo?(preferred==='auto'?'photo':preferred):'points';$('setup-camera-display').value=display;
   if(!await window.JobUI.enterSetup(frame,{loadPoints:display==='points'}))throw Error('Setup view was interrupted. Select the setup again.');if(generation!==epoch)return false;
   if(display!=='points'){
    try{await loadPhotos(generation);if(generation!==epoch)return false;await window.JobUI.setSetupDisplay(display);}
    catch(e){if(generation!==epoch)return false;photoError=e.message;$('setup-camera-display').value='points';await window.JobUI.setSetupDisplay('points');}
   }
   if(generation!==epoch)return false;window.JobUI.showSetupStations($('setup-show-stations').checked);ready();if(photoError)status('Photographs could not load: '+photoError+' Showing the point cloud. Choose Photographs to retry or load them from the project folder.');return true;
  }catch(e){if(generation===epoch){frame=null;photo=null;picked=null;await window.JobUI.leaveSetup();status('Setup view could not open: '+e.message+' Select a setup to retry.');}return false;}
  finally{if(generation===epoch){opening=false;controls();}}
 }
 async function pickedPoint(value,kind='cloud',fitTarget=false){
  if(!frame||working())return;const source=frame,generation=epoch;picking=true;picked=null;pendingPoint++;$('setup-camera-target').hidden=true;status('Reading the original measured point in '+source.name+'…');controls();
  try{
   await post(kind==='photo'?'/api/setup-photos':'/api/setup-camera',{action:'pick',id:source.id,key:source.key,...(kind==='photo'?{face:value.face,u:value.u,v:value.v}:{point:value})});
   if(generation!==epoch)await post('/api/cancel-operation',{});
   const job=await window.JobUI.waitReady(p=>{if(generation===epoch&&p.busy)status(p.message||'Reading the measured point…');},{skipSync:true});if(generation!==epoch)return;if(job?.cancelled)throw Error('Point lookup cancelled.');
   const result=await api(kind==='photo'?'/api/setup-photo-pick':'/api/setup-cloud-pick');if(generation!==epoch)return;
   if(result.scan_id!==source.id||result.key!==source.key||!result.token||result.point?.length!==3||!result.point.every(Number.isFinite))throw Error('No measured point was returned for this setup. Pick again.');
   picked={...result,pick_source:kind,thumbnail:kind==='photo'?value.thumbnail:null};$('setup-camera-coordinates').textContent='X / Y / Z: '+result.point.map(v=>fmt(v,6)).join(' / ');$('setup-camera-target').hidden=false;status('Measured point selected in '+source.name+'. Name it to save a manual target, or fit the checker centre.');
  }catch(e){if(generation===epoch)status(e.message);}
  finally{pendingPoint--;picking=false;controls();}
  if(fitTarget&&picked&&generation===epoch)await fitPickedTarget();
 }
 async function exit(refresh=true){epoch++;opening=false;changing=false;fitting=false;blending=false;pendingBlend=null;frame=null;photo=null;picked=null;bar.hidden=true;$('setup-camera-target').hidden=true;await window.JobUI.leaveSetup(refresh);}
 $('setup-camera-choice').onchange=()=>open($('setup-camera-choice').value);
 $('setup-camera-exit').onclick=()=>exit();
 $('setup-camera-display').onchange=async()=>{
  if(!frame||working())return;const generation=epoch,display=mode();changing=true;picked=null;$('setup-camera-target').hidden=true;controls();
  try{if(display==='both')window.JobUI.setSetupBlend(lastMixed/100);if(display!=='points')await loadPhotos(generation);if(generation!==epoch)return;if(!await window.JobUI.setSetupDisplay(display))return;preferred=display;ready();}
  catch(e){if(generation===epoch){$('setup-camera-display').value='points';await window.JobUI.setSetupDisplay('points');status('Photograph view could not load: '+e.message+' Showing the point cloud.');}}
  finally{if(generation===epoch){changing=false;controls();}}
 };
 $('setup-photo-detail').onchange=async()=>{if(!photo||working())return;const generation=epoch;changing=true;controls();try{await loadPhotos(generation);if(generation===epoch)ready();}catch(e){if(generation===epoch){if(photoSize)$('setup-photo-detail').value=String(photoSize);status(e.message+' The previous ready photographs are kept.');}}finally{if(generation===epoch){changing=false;controls();}}};
 // Keep range input enabled while its one background load is in progress.
 // Coalesce movement to the latest requested value instead of starting a load
 // for each pointer event. Return remains usable and epoch guards late work.
 async function moveBlend(){
  if(!frame||blocking()||!photo)return;
  const percent=Math.max(0,Math.min(100,+$('setup-camera-blend').value)),display=percent===0?'photo':percent===100?'points':'both';
  if(percent>0&&percent<100)lastMixed=percent;
  pendingBlend={percent,display};$('setup-camera-display').value=display;
  window.JobUI.setSetupBlend(percent/100);blendUI();
  if(blending)return;
  const generation=epoch;blending=true;controls();
  try{
   while(pendingBlend&&generation===epoch){
    const request=pendingBlend;pendingBlend=null;
    if(request.display!=='points'&&!window.JobUI.setupLayers().photos){status('Loading photographs for the blend…');await loadPhotos(generation);}
    if(generation!==epoch)return;if(pendingBlend)continue;
    if(request.display!=='photo'&&!window.JobUI.setupLayers().points)status('Loading the point preview once for the blend… You can keep adjusting the slider.');
    const layers=window.JobUI.setupLayers();
    if(layers.display!==request.display||(request.display!=='photo'&&!layers.points)){if(!await window.JobUI.setSetupDisplay(request.display)||generation!==epoch)return;}
    if(!pendingBlend){preferred=request.display;ready();}
   }
  }catch(e){
   if(generation!==epoch)return;pendingBlend=null;
   const layers=window.JobUI.setupLayers(),fallback=layers.photos?'photo':'points';
   $('setup-camera-display').value=fallback;preferred=fallback;
   try{await window.JobUI.setSetupDisplay(fallback);}catch(_){}
   if(generation===epoch)status('Could not prepare the blend: '+e.message+' '+(layers.photos?'Showing photographs; move the slider to retry.':layers.points?'Showing points; move the slider to retry.':'Load photographs or refresh the point preview, then retry.'));
  }finally{if(generation===epoch){blending=false;controls();}}
 }
 $('setup-camera-blend').oninput=moveBlend;
 $('setup-camera-blend').onchange=()=>{const percent=+$('setup-camera-blend').value,shown=mode()==='photo'?0:mode()==='points'?100:Math.round(window.JobUI.setupBlend()*100);if(percent!==shown)return moveBlend();};
 $('setup-show-stations').onchange=()=>window.JobUI.showSetupStations($('setup-show-stations').checked);
 function armPick(fitTarget=false){
  if(!frame)return false;
  if(working()){status('Wait for the current setup operation before picking a target.');return false;}
  const usePhoto=mode()==='photo'||mode()==='both'&&window.JobUI.setupBlend()===0;
  const armed=usePhoto?window.JobUI.armPhotoPick(value=>pickedPoint(value,'photo',fitTarget)):
   window.JobUI.armPointPick(point=>pickedPoint(point,'cloud',fitTarget),{prompt:'Pick a point in '+frame.name,done:'Reading measured cloud point…'});
  if(armed&&fitTarget)status('Pick near the black-and-white target centre in '+frame.name+'. The measured scan point will seed full-density target fitting.');
  return armed;
 }
 $('setup-camera-pick').onclick=()=>armPick();
 $('setup-camera-detail').onclick=()=>window.JobUI.loadViewDetail(true);
 $('setup-camera-save').onclick=async()=>{
  if(!picked||!frame||working())return;const source=frame,point=picked,generation=epoch,name=$('setup-camera-name').value.trim();
  if(!name){status('Enter a target name. Use the same name for this target in the other setups.');$('setup-camera-name').focus();return;}
  saving=true;controls();
  try{await window.TargetUI.saveIfDirty();const saved=await post('/api/setup-view',{action:'save_target',pick_source:point.pick_source,id:source.id,key:source.key,token:point.token,name,...(point.thumbnail?{thumbnail:point.thumbnail}:{})});if(generation===epoch){picked=null;$('setup-camera-target').hidden=true;status('Saved '+saved.name+' in '+source.name+'. Pick the next target or choose another setup.');}await window.TargetUI.sync({skipPreview:true});await window.JobUI.sync({inventoryOnly:true});}
  catch(e){if(generation===epoch)status(e.message);else notice('Target save: '+e.message,true);}
  finally{saving=false;controls();}
 };
 async function fitPickedTarget(){
  if(!picked||!frame||working())return;
  const source=frame,point=picked.point,generation=epoch;fitting=true;controls();
  try{
   await window.ScanRegistrationUI.activateTargets(source.id);if(generation!==epoch)return;
   window.JobUI.selectScan(source.id);window.TunnelRibbon.view('registration',{skipPreview:true});await window.TargetUI.fitPoint(point);
  }catch(e){if(generation===epoch)status(e.message);}
  finally{if(generation===epoch){fitting=false;controls();}}
 }
 $('setup-camera-fit').onclick=fitPickedTarget;
 $('setup-photo-browse').onclick=async()=>{try{const bridge=window.pywebview?.api;if(!bridge?.choose_project_folder){status('Paste the original Leica project folder path below, then click Load photographs.');$('setup-photo-path').focus();return;}const path=await bridge.choose_project_folder();if(path)$('setup-photo-path').value=path;}catch(e){status(e.message);}};
 $('setup-photo-attach-button').onclick=async()=>{
  if(!frame||working())return;const path=$('setup-photo-path').value.trim();if(!path){status('Choose or paste the original main Leica project folder.');$('setup-photo-path').focus();return;}
  const id=frame.id,generation=epoch;attaching=true;controls();status('Attaching photographs to the matching setups…');
  try{const result=await post('/api/setup-photos',{action:'attach',path});if(generation!==epoch)return;await exit(false);await window.JobUI.sync({inventoryOnly:true});attaching=false;preferred='photo';await open(id);status('Photographs attached: '+result.loaded.join(', ')+'. '+(result.missing?.length?'Unavailable: '+result.missing.join('; '):'Drag to look around in this viewport.'));}
  catch(e){if(generation===epoch)status('Could not load photographs: '+e.message);}
  finally{attaching=false;controls();}
 };
 window.SetupCameraUI={open,exit,pickTarget:()=>armPick(true),controls,loaded:id=>{if(frame?.id===id&&!working())ready();},invalidated:()=>{epoch++;frame=null;photo=null;picked=null;opening=false;changing=false;fitting=false;blending=false;pendingBlend=null;bar.hidden=true;notice('The setup changed. Reopen its setup view to use the updated position.',true);}};
})();
