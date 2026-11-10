/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
window.WorkspaceFolders=(()=>{
  const dialog=document.createElement('dialog');
  dialog.id='workspace-folders-dialog';
  dialog.innerHTML=`<div class="dialog-heading"><h2>Project folders</h2><button id="wf-close" aria-label="Close">×</button></div>
  <p id="wf-current" class="project-path"></p><p id="wf-next" class="project-path" hidden></p>
  <fieldset><legend>Change where you work</legend>
    <label>Data folder<select id="wf-target"></select></label>
    <button id="wf-select" type="button">Use after restart</button>
  </fieldset>
  <details id="wf-new-folder"><summary>Create a separate data folder</summary>
  <fieldset>
    <label>Project name<input id="wf-name" maxlength="100" placeholder="e.g. Northbound tunnel"></label>
    <label>Save in<input id="wf-parent" spellcheck="false" placeholder="Choose a parent folder or enter the full path"></label>
    <button id="wf-browse" type="button">Choose parent folder…</button>
    <p id="wf-preview" class="project-path"></p>
    <button id="wf-create" type="button">Create empty folder for next launch</button>
  </fieldset>
  <section id="workspace-project-copy"></section>
  </details>
  <details id="wf-storage-details"><summary>Storage details</summary>
    <p id="wf-categories" class="tiny"></p><p id="wf-config" class="tiny project-path"></p>
    <section id="workspace-shapefiles" hidden></section>
  </details>
  <p id="wf-message" role="status"></p>`;
  document.body.append(dialog);
  const id=k=>document.getElementById(k);
  const message=text=>{id('wf-message').textContent=text;};
  let info=null,busy=false,requestId=0;
  const buttons=['wf-browse','wf-create','wf-target','wf-name','wf-parent'];
  function selectState(){
    id('wf-select').disabled=busy || !info || id('wf-target').value===info.next_id;
  }
  function setBusy(value){
    busy=value;
    for(const k of buttons)id(k).disabled=value;
    selectState();
  }
  function preview(){
    const parent=id('wf-parent').value.trim(),name=id('wf-name').value.trim();
    const slash=parent.includes('\\') || /^[A-Za-z]:/.test(parent)?'\\':'/';
    id('wf-preview').textContent=parent && name ? 'New project folder: '+parent.replace(/[\\/]+$/,'')+slash+name:'';
  }
  function render(data){
    info=data;
    id('wf-current').textContent='Using '+data.current_root;
    id('wf-categories').textContent='Dedicated project folders contain '+data.categories.join(' · ')+'.';
    id('wf-config').textContent='Folder settings file: '+data.configuration_file;
    const list=id('wf-target');
    list.replaceChildren();
    const entries=[{id:'shared',name:'Default data folder',path:data.shared_root},...data.folders];
    for(const entry of entries){
      const opt=document.createElement('option'); opt.value=entry.id;
      opt.textContent=entry.name+' · '+entry.path;list.append(opt);
    }
    list.value=data.next_id;
    const next=entries.find(e=>e.id===data.next_id);
    id('wf-next').hidden=!next || next.path===data.current_root;
    id('wf-next').textContent=next?'After restart: '+next.path:'';
    selectState();
    window.ProjectFolderCopy?.show(data);
    window.ProjectShapefiles?.show(data);
  }
  async function open(){
    if(busy)return;
    info=null;id('wf-current').textContent='Reading project folders…';id('wf-next').hidden=true;
    id('wf-new-folder').open=id('wf-storage-details').open=false;
    id('wf-name').value='';id('wf-parent').value='';preview();message('');
    const token=++requestId;
    if(!dialog.open)dialog.showModal();
    setBusy(true);
    try{
      const data=await api('/api/workspace-folders');
      if(token!==requestId || !dialog.open)return;
      render(data);message(data.notice);
    }catch(e){if(token===requestId)message(e.message);}
    finally{if(token===requestId)setBusy(false);}
  }
  async function update(body){
    if(busy || !info)return;
    setBusy(true);
    try{
      render(await post('/api/workspace-folders',body));
      message('Folder selected. Restart Tunnel Studio to switch. Existing projects stay in their current folders.');
    }catch(e){message(e.message);}
    finally{setBusy(false);}
  }
  id('wf-browse').onclick=async()=>{
    const chooser=window.pywebview?.api?.choose_project_folder;
    if(!chooser){id('wf-parent').focus();message('Enter the parent folder path above, then select Create folder for next launch.');return;}
    setBusy(true);
    try{
      const path=await window.pywebview.api.choose_project_folder();
      if(path){id('wf-parent').value=path;preview();}
    }catch(e){message(e.message);}
    finally{setBusy(false);}
  };
  id('wf-name').oninput=id('wf-parent').oninput=preview;
  id('wf-target').onchange=selectState;
  id('wf-create').onclick=()=>update({action:'create',parent:id('wf-parent').value.trim(),name:id('wf-name').value.trim()});
  id('wf-select').onclick=()=>update({action:'select',id:id('wf-target').value});
  id('wf-close').onclick=()=>{if(!busy)dialog.close();};
  dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
  dialog.addEventListener('close',()=>{requestId++;});
  id('workspace-folders-open').onclick=()=>open();
  document.addEventListener('DOMContentLoaded',async()=>{
    try{
      const data=await api('/api/workspace-folders');
      if(data.notice)showTunnelMessage('Project drive unavailable',data.notice,true);
    }catch{/* The explicit dialog reports any read error. */}
  });
  return {open,refresh:async()=>{if(dialog.open)render(await api('/api/workspace-folders'));}};
})();
