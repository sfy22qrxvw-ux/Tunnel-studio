/* Direct RTC360 import; optional legacy E57/vendor conversion routes. */
(()=>{
 'use strict';
 let raw=null,exports=null,phase='idle',receipt=null,serial=0;
 const panel=document.createElement('section');panel.id='leica-bridge';panel.hidden=true;
 panel.innerHTML=`<h3>Import original scanner data</h3>
 <label>Import method<select id="leica-bridge-route"><option value="native">Direct from scanner folder — experimental</option><option value="existing">Use existing E57 exports</option><option value="convert">Convert with installed REGISTER 360 PLUS</option></select></label>
 <div id="leica-native-direct"><p>Tunnel Studio converts the selected setups itself. No Leica installation or E57 export is required. Each setup becomes an editable point cloud with intensity and available setup photographs.</p>
 <p><b>Experimental reconstruction:</b> factory calibration is not fully verified. Raw readings retain noise and overlap because Leica's validity and double-scan filtering are not reproduced. Check against trusted survey data before measurement use. Positioning can use saved scanner odometry where available, or leave each setup at its own origin. Saved odometry is an initial alignment, not survey control.</p>
 <div id="leica-native-scans"></div><p id="leica-native-space"></p></div>
 <div id="leica-bridge-convert"><p>Requires REGISTER 360 PLUS and a <b>Cyclone WORKFLOW licence</b>. BLK Edition is not supported. Close REGISTER 360 first. Conversion uses a separate local copy and keeps the original scanner files unchanged.</p>
 <label>REGISTER 360 executable<input id="leica-bridge-exe" placeholder="Full path to register360.exe"></label><button id="leica-bridge-exe-browse">Choose executable…</button>
 <p>The whole scanner folder is converted and automatically registered by Leica. All exported setups are then imported separately. Review that registration before using it.</p></div>
 <div id="leica-bridge-existing" hidden><p>Choose completed E57 exports from this job. No WORKFLOW licence is needed to import them. Setup names, positions and supported photographs come from the E57; filenames are not used to guess raw setup matches.</p>
 <label>E57 file or export folder<input id="leica-bridge-exports" placeholder="Paste the full E57 file or folder path"></label><button id="leica-bridge-file">Choose E57…</button><button id="leica-bridge-folder">Choose export folder…</button><button id="leica-bridge-inspect">List E57 setups</button>
 <div id="leica-bridge-scans"></div></div>
 <button id="leica-bridge-start" class="primary" disabled>Convert folder &amp; import</button><button id="leica-bridge-cancel" hidden>Cancel import</button><button id="leica-bridge-view" class="primary" hidden>View imported scans</button>
 <p id="leica-bridge-output" hidden></p><ul id="leica-bridge-warnings" hidden></ul>`;
 $('leica-feedback').before(panel);
 const status=(title,message,kind='info')=>{
  $('leica-feedback-title').textContent=title;$('leica-message').textContent=message;
  $('leica-feedback').dataset.kind=kind;$('leica-progress').hidden=kind!=='running';
 };
 const selected=()=>[...$('leica-bridge-scans').querySelectorAll('input:checked')].map(x=>x.dataset.e57Id);
 const nativeSelected=()=>[...$('leica-native-scans').querySelectorAll('input:checked')].filter(x=>x.dataset.available==='true').map(x=>x.dataset.rawId);
 const route=()=>$('leica-bridge-route').value;
 function nativeStatus(){
  const available=(raw?.setups||[]).filter(x=>x.native_available===true),chosen=nativeSelected().length;
  if(!available.length){
   status('Direct import unavailable for these setups','No setup passed the raw-reader checks. See the reason beside each setup below. You can also choose existing E57 exports or the installed Leica converter.','error');
  }else if(!chosen){
   status('Select a setup to import',`${available.length} supported setup${available.length===1?' is':'s are'} available. Tick at least one setup below.`);
  }else{
   const blocked=raw.setups.length-available.length;
   status(`${chosen} setup${chosen===1?'':'s'} ready for direct import`,`${blocked?`${blocked} other setup${blocked===1?' is':'s are'} unavailable; see the individual reasons below. `:''}Conversion uses the experimental raw reader. Review its calibration limitations before importing.`);
  }
 }
 function controls(){
  const running=['reading','importing','cancelling'].includes(phase),complete=phase==='complete';
  $('leica-bridge-convert').hidden=route()!=='convert';$('leica-bridge-existing').hidden=route()!=='existing';
  $('leica-native-direct').hidden=route()!=='native';
  for(const el of panel.querySelectorAll('input,select,button'))el.disabled=running;
  for(const el of $('leica-native-scans').querySelectorAll('input'))el.disabled=running||el.dataset.available!=='true';
  for(const id of ['leica-project-path','leica-folder-browse','leica-inspect'])$(id).disabled=running;
  $('leica-position-mode').parentElement.hidden=route()!=='native';
  $('leica-position-mode').disabled=running||complete||route()!=='native';
  const valid=route()==='native'?!!raw&&nativeSelected().length>0:route()==='existing'?!!exports&&selected().length>0:!!raw&&raw.setups.every(x=>!x.error&&!x.missing?.length)&&!!$('leica-bridge-exe').value.trim();
  $('leica-bridge-start').disabled=running||complete||!valid;
  $('leica-bridge-start').textContent=running?'Import in progress…':route()==='native'?'Convert & import selected setups':route()==='convert'?'Convert folder & import':'Import selected E57 setups';
  if(raw){const ids=nativeSelected(),bytes=raw.setups.filter(x=>ids.includes(x.id)).reduce((n,x)=>n+(x.cache_bytes_estimate||0),0);$('leica-native-space').textContent=ids.length?`Estimated working cache: ${fmt(bytes/1024**3,2)} GiB plus 512 MB free. All positive finite range readings are retained; the display uses a lighter preview.`:'No setups selected. A cache estimate will appear when a supported setup is selected.';}
  $('leica-bridge-start').title=route()==='native'&&!running&&!complete&&!valid?'Select a supported setup; any blocking reason is shown beside its name.':'';
  $('leica-bridge-cancel').hidden=!['importing','cancelling'].includes(phase);$('leica-bridge-cancel').disabled=phase==='cancelling';
  $('leica-bridge-view').hidden=!complete;$('leica-bridge-view').disabled=!complete;
 }
 function clearResult(){
  receipt=null;$('leica-bridge-view').hidden=true;$('leica-completed-list').hidden=true;$('leica-completed-list').replaceChildren();
  $('leica-bridge-warnings').hidden=true;$('leica-bridge-output').hidden=true;
 }
 function invalidate(){serial++;exports=null;phase='idle';clearResult();$('leica-bridge-scans').replaceChildren();controls();}
 async function choose(kind,folder=false){
  try{
   const bridge=window.pywebview?.api;
   if(!bridge){status('Paste the full local path','File selection is unavailable in browser mode. Paste the full path into the box.');return;}
   const result=folder?await bridge.choose_project_folder():await bridge.choose_large_file(kind,false);
   return Array.isArray(result)?result[0]:result;
  }catch(e){status('Could not choose the file or folder',e.message,'error');}
 }
 $('leica-bridge-exe-browse').onclick=async()=>{const p=await choose('leica_converter');if(p){$('leica-bridge-exe').value=p;phase='idle';clearResult();controls();}};
 $('leica-bridge-file').onclick=async()=>{const p=await choose('leica_e57');if(p){$('leica-bridge-exports').value=p;invalidate();await inspect();}};
 $('leica-bridge-folder').onclick=async()=>{const p=await choose('',true);if(p){$('leica-bridge-exports').value=p;invalidate();await inspect();}};
 $('leica-bridge-exe').oninput=()=>{phase='idle';clearResult();controls();};
 $('leica-bridge-exports').oninput=invalidate;
 $('leica-bridge-route').onchange=()=>{phase='idle';clearResult();controls();if(route()==='native'){nativeStatus();return;}status(route()==='convert'?'Ready to use the local Leica converter':'Choose your completed E57 exports',route()==='convert'?'The converter requires a WORKFLOW licence. It will report a licence error if that entitlement is unavailable.':'List the E57 setups, then select the ones to import.');};
 async function inspect(){
  if(['reading','importing','cancelling'].includes(phase))return;
  invalidate();const version=serial,path=$('leica-bridge-exports').value.trim();
  if(!path){status('Choose an E57 export','Paste an E57 file path or its containing folder.','error');return;}
  phase='reading';controls();status('Reading E57 setup headers…','Checking names, point counts and saved positions.','running');
  try{
   const result=await post('/api/leica-project',{action:'bridge_inspect_exports',exports_path:path});if(version!==serial)return;
   exports=result;phase='ready';
   $('leica-bridge-scans').innerHTML=result.scans.map(x=>`<label><input type="checkbox" data-e57-id="${esc(x.id)}" checked>${esc(x.name)} · ${fmt(x.count,0)} records</label>`).join('');
   for(const input of $('leica-bridge-scans').querySelectorAll('input'))input.onchange=controls;
   status(`${result.scans.length} E57 setup${result.scans.length===1?'':'s'} ready`,'Select the setups to import. Existing E57 coordinates will be preserved, including the saved pose.');
  }catch(e){if(version===serial){phase='error';status('Could not read the E57 exports',e.message,'error');}}
  finally{if(version===serial)controls();}
 }
 $('leica-bridge-inspect').onclick=inspect;
 $('leica-bridge-start').onclick=async()=>{
  if($('leica-bridge-start').disabled)return;
  const request=route()==='native'?{action:'native_import',mode:$('leica-position-mode').value,path:raw.root,setups:nativeSelected(),setup_stamps:Object.fromEntries(raw.setups.filter(x=>x.id).map(x=>[x.id,x.stamp]))}:{action:'bridge_import',route:route(),path:raw.root,raw_stamp:raw.stamp,converter:$('leica-bridge-exe').value, exports_path:$('leica-bridge-exports').value,scans:selected()};
  phase='importing';clearResult();controls();status('Starting Leica folder import…','Conversion progress will appear here.','running');
  try{
   await window.TargetUI.saveIfDirty();
   const started=await post('/api/leica-project',request);
   const job=await window.JobUI.waitReady(p=>{if(p.busy)status(phase==='cancelling'?'Cancelling import…':'Importing Leica setups…',p.message||'Working…','running');},{skipSync:true});
   if(job?.cancelled){phase='ready';status('Import cancelled','No setups from this import were committed. Original scanner files remain unchanged.');return;}
   const saved=(await api('/api/leica-project')).last_import;
   if(!started.import_id||saved?.id!==started.import_id)throw Error('This import could not be confirmed. Check Scans & models before trying again.');
   receipt=saved;phase='complete';
   status(`Imported successfully — ${saved.count} setup${saved.count===1?'':'s'}`,`${saved.position_notice?saved.position_notice+' ':''}${fmt(saved.point_count,0)} ${saved.mode==='rtc360_native_experimental'?'reconstructed points retained (experimental, unfiltered)':'valid points retained'}. Use View imported scans, then select a setup for cleaning or setup view.`,'success');
   $('leica-completed-list').innerHTML=saved.items.map(x=>`<li>${esc(x.name)} · ${fmt(x.count,0)} points</li>`).join('');$('leica-completed-list').hidden=false;
   if(saved.warnings?.length){$('leica-bridge-warnings').innerHTML=saved.warnings.map(x=>'<li>'+esc(x)+'</li>').join('');$('leica-bridge-warnings').hidden=false;}
   if(saved.conversion_folder){$('leica-bridge-output').textContent='Converted exports kept at: '+saved.conversion_folder;$('leica-bridge-output').hidden=false;}
   window.JobUI.sync({inventoryOnly:true}).catch(()=>{});
  }catch(e){phase='error';status('Leica folder import did not complete',e.message,'error');}
  finally{controls();}
 };
 $('leica-bridge-cancel').onclick=async()=>{
  if(phase!=='importing')return;phase='cancelling';controls();
  try{await post('/api/job-action',{action:'cancel'});status('Cancelling import…','Stopping the process started by this import.','running');}
  catch(e){phase='importing';controls();status('Cancellation was not confirmed',e.message,'error');}
 };
 $('leica-bridge-view').onclick=async()=>{
  if(!receipt)return;$('leica-project-dialog').close();window.TunnelRibbon?.view('job');
  try{await window.JobUI.sync();window.JobUI.selectScan(receipt.items[0]?.id);await window.JobUI.loadPreview();window.TargetUI.sync().catch(()=>{});}
  catch(e){notice('The imported scans were saved; the preview could not be opened: '+e.message,true);}
 };
 window.LeicaBridgeUI={
  controls,
  reset(){serial++;raw=null;exports=null;phase='idle';receipt=null;panel.hidden=true;$('leica-bridge-scans').replaceChildren();$('leica-native-scans').replaceChildren();$('leica-import').hidden=false;$('leica-position-mode').parentElement.hidden=false;$('leica-native-note').hidden=false;},
  present(info){
   raw=info;panel.hidden=false;phase='idle';clearResult();$('leica-import').hidden=true;$('leica-position-mode').parentElement.hidden=true;$('leica-native-note').hidden=true;
   $('leica-bridge-exe').value=info.converters?.length===1?info.converters[0]:$('leica-bridge-exe').value;
   if(info.cached_export_folder)$('leica-bridge-exports').value=info.cached_export_folder;
   $('leica-native-scans').innerHTML=info.setups.map(x=>`<label><input type="checkbox" data-raw-id="${esc(x.id||'')}" data-available="${x.native_available===true}" ${x.native_available?'checked':'disabled'}>${esc(x.name||'Unnamed setup')} · ${x.native_available?`${fmt(x.raster_slots,0)} raw readings${x.api_version?` · API ${esc(x.api_version)}`:''}`:esc(x.native_error||x.error||'Unsupported scanner layout')}</label>`).join('');
   for(const input of $('leica-native-scans').querySelectorAll('input'))input.onchange=()=>{controls();if(phase!=='complete')nativeStatus();};
   $('leica-bridge-route').value='native';controls();
   nativeStatus();
  }
 };
})();
