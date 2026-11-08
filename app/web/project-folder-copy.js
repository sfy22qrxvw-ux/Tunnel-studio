/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
window.ProjectFolderCopy = (() => {
  const el = id => document.getElementById(id);
  let bound = false, pending = false;
  const fmt = bytes => bytes >= 1e9 ? (bytes/1e9).toFixed(2)+' GB' : Number(bytes).toLocaleString()+' bytes';
  function show() {
    const host = el('workspace-project-copy');
    if (!host || bound) return;
    host.innerHTML = '<fieldset><legend>Organise the open project</legend><p>Use the project name and Save in folder above. This copies the current project and its linked assets into separate cloud, mesh, analysis and GIS folders. After verification you can open the copy on the next launch.</p><button type="button" id="wf-copy-review">Review project copy…</button> <button type="button" id="wf-copy-stop" hidden>Stop copy</button><p id="wf-copy-status" role="status"></p></fieldset>';
    bound = true;
    const status = text => { el('wf-copy-status').textContent = text; };
    el('wf-copy-stop').onclick = async () => {
      try { await post('/api/cancel-operation',{}); } catch (error) { status(error.message); }
    };
    el('wf-copy-review').onclick = async () => {
      if (pending) return;
      pending = true;
      el('wf-copy-review').disabled = true;
      const input={parent:el('wf-parent').value.trim(),name:el('wf-name').value.trim()};
      try {
        status('Checking files and available space…');
        const review = await post('/api/project-folder-copy',{...input,action:'review'});
        const detail = review.files.toLocaleString()+' linked files · '+fmt(review.bytes)+' to copy · '+fmt(review.free)+' free on destination drive';
        status(detail);
        if (!review.enough) { status(detail+'. Choose a drive with enough free space.'); return; }
        if (!confirm('Copy the open project to '+review.destination+'?\n\n'+detail+'\n\nThe original stays in its current folder. Restart to use the verified copy.')) return;
        const job=await post('/api/project-folder-copy',{...input,action:'copy',token:review.token});
        if (!job.started) throw Error('Copy could not start.');
        el('wf-copy-stop').hidden=false;
        const result=await window.JobUI.waitReady(job=>status(job.message),{skipSync:true});
        if (result.cancelled) {status('Copy cancelled. Your original project is still available.');return;}
        const receipt=await post('/api/project-folder-copy',{action:'receipt'});
        if (receipt.id!==job.id) throw Error('Please check the destination before using the copy.');
        await window.WorkspaceFolders.refresh();
        status('Verified '+receipt.files.toLocaleString()+' files at '+receipt.destination+'. Restart Tunnel Studio and use Open project → '+receipt.project_title+'. Your original is still in its previous location.');
      } catch (error) { status(error.message); }
      finally { pending=false;el('wf-copy-review').disabled=false;el('wf-copy-stop').hidden=true; }
    };
  }
  return {show};
})();
