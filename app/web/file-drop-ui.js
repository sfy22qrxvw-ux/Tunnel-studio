/* Local file drops share the normal import and guarded project-open workflows. */
(()=>{
 'use strict';
 const point=new Set(['hspca','e57','las','laz','ply','pts','xyz','txt','csv']),mesh=new Set(['obj','zip']);
 const ext=name=>String(name).split(/[\\/]/).at(-1).split('.').at(-1).toLowerCase();
 const name=value=>String(value).split(/[\\/]/).at(-1);
 const queue=[],dropTickets=[],pending=new Map();let working=false,native=false,depth=0,nextTicket=0;
 const overlay=document.createElement('div');overlay.id='file-drop-overlay';overlay.hidden=true;overlay.setAttribute('aria-hidden','true');
 overlay.textContent='Drop scans or meshes to import · Drop one .tstudio file to open a project';
 Object.assign(overlay.style,{position:'fixed',inset:'12px',zIndex:'10000',pointerEvents:'none',border:'3px dashed #56b8fa',borderRadius:'12px',padding:'28px',background:'rgba(12,30,45,.92)',color:'#fff',fontSize:'20px',textAlign:'center'});document.body.append(overlay);
 const message=(title,text,error=true)=>window.showTunnelMessage?.(title,text,error);
 function stopQueue(){queue.length=0;for(const ticket of pending.values())clearTimeout(ticket.timer);pending.clear();dropTickets.length=0;}
 const hasFiles=e=>Array.from(e.dataTransfer?.types||[]).includes('Files')||!!e.dataTransfer?.files?.length;
 function hide(){depth=0;overlay.hidden=true;}
 function classify(entries,paths){
  if(!entries.length)return null;
  const projects=entries.filter(v=>ext(paths?v:v.name)==='tstudio'),cad=entries.filter(v=>['dxf','json'].includes(ext(paths?v:v.name)));
  if(projects.length){if(entries.length!==1){message('Drop one project at a time','A project replaces the open workspace. Drop the .tstudio file on its own, then import additional scans or models.');return null;}return {kind:'project',entries,paths};}
  if(cad.length){if(paths&&entries.length===1)return {kind:'cad',entries,paths};message('Editable model import','Drop one local DXF or model JSON file in the desktop application so you can choose its units and destination. Use Import → Editable model in browser mode.');return null;}
  const unsupported=entries.filter(v=>!point.has(ext(paths?v:v.name))&&!mesh.has(ext(paths?v:v.name)));
  if(unsupported.length){message('Unsupported dropped files','Nothing from this drop was imported. Unsupported: '+unsupported.map(v=>name(paths?v:v.name)).join(', ')+'. Supported: E57, LAS/LAZ, PLY, PTS, XYZ, TXT, CSV, HSPCA, OBJ, supported mesh ZIP and .tstudio.');return null;}
  return {kind:'data',entries,paths,bundle:window.WorkspaceUI?.activeBundle?.()};
 }
 async function drain(){
  if(working)return;working=true;
  try{while(queue.length){const task=queue.shift();
   try{
    await window.JobUI.waitReady(null,{ignoreErrors:true});
    if(task.kind==='project'){
     const ok=task.paths?await window.TunnelRibbon.openProjectPath(task.entries[0]):await window.TunnelRibbon.openProjectFile(task.entries[0]);
     if(ok===false){stopQueue();break;}
    }else if(task.kind==='cad')window.CadUI.importDialog(task.entries[0],task.bundle);
    else{
     window.TunnelRibbon?.view?.('job');
     if(task.paths){if(await window.JobUI.importPaths(task.entries,task.bundle)===false){stopQueue();break;}}
     else for(let i=0;i<task.entries.length;i++){
      const file=task.entries[i],kind=mesh.has(ext(file.name))?'job_mesh':'job_scan';
      const ok=await window.JobUI.uploadFiles([file],kind,{bundle_id:task.bundle,queueLabel:`Dropped file ${i+1}/${task.entries.length}`});
      if(ok===false){stopQueue();return;}
     }
    }
   }catch(error){stopQueue();message('File drop could not finish',error.message||String(error));break;}
  }}finally{working=false;}
 }
 function enqueue(entries,paths){const task=classify(Array.from(entries||[]),paths);if(!task)return Promise.resolve(false);queue.push(task);return drain();}
 function flushTickets(){let result=Promise.resolve();while(dropTickets[0]?.ready){const ticket=dropTickets.shift();result=enqueue(ticket.paths||ticket.files,!!ticket.paths);}return result;}
 function fallback(ticket){if(!pending.has(ticket.id))return;pending.delete(ticket.id);clearTimeout(ticket.timer);ticket.ready=true;return flushTickets();}
 document.addEventListener('dragenter',e=>{if(!hasFiles(e))return;e.preventDefault();depth++;overlay.hidden=false;});
 document.addEventListener('dragover',e=>{if(!hasFiles(e))return;e.preventDefault();e.dataTransfer.dropEffect='copy';overlay.hidden=false;});
 document.addEventListener('dragleave',e=>{if(!hasFiles(e)&&!depth)return;depth=Math.max(0,depth-1);if(!depth)overlay.hidden=true;});
 document.addEventListener('drop',e=>{
  if(!hasFiles(e))return;e.preventDefault();hide();const files=Array.from(e.dataTransfer.files||[]);if(!files.length)return;
  // Capture runs before pywebview's body listener; one ticket prevents duplicate imports.
  if(native){const id=String(++nextTicket),ticket={id,files,ready:false};e.tunnelDropId=id;pending.set(id,ticket);dropTickets.push(ticket);ticket.timer=setTimeout(()=>fallback(ticket),3000);}
  else enqueue(files,false);
 },true);
 window.addEventListener('blur',hide);
 window.FileDropUI={nativeReady:()=>{native=true;},nativeDrop:(paths,id)=>{const ticket=pending.get(String(id));if(!ticket)return Promise.resolve(false);pending.delete(ticket.id);clearTimeout(ticket.timer);if(paths?.length===ticket.files.length&&paths.every((p,i)=>name(p)===ticket.files[i].name))ticket.paths=paths;ticket.ready=true;return flushTickets();},enqueuePaths:paths=>enqueue(paths,true),enqueueFiles:files=>enqueue(files,false),isWorking:()=>working,queued:()=>queue.length};
})();
