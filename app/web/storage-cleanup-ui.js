/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
window.StorageCleanup = (() => {
  const PAGE = 50;
  const title = {snapshots:'Old saved project records', caches:'Unreferenced working caches',
    exports:'Eligible exports', reports:'Generated reports', demo:'Sample project files',
    analysis:'Recalculable analysis caches', previews:'Regenerable previews'};
  const closeGroups = new Set(['exports','analysis','previews']);
  const dialog = document.createElement('dialog');
  dialog.id = 'storage-cleanup-dialog';
  dialog.setAttribute('aria-labelledby','storage-cleanup-title');
  // Only constant markup. File paths, names and descriptions are rendered as text.
  dialog.innerHTML = `
    <div class="dialog-heading"><h2 id="storage-cleanup-title">Choose files to clear</h2></div>
    <p id="storage-cleanup-intro"></p>
    <div id="storage-cleanup-filter"><label>File type <select id="storage-cleanup-category"></select></label></div>
    <p id="storage-cleanup-note" class="tiny"></p>
    <p id="storage-cleanup-status" role="status"></p>
    <div id="storage-cleanup-browser">
      <div id="storage-cleanup-count" class="cleanup-count"></div>
      <div id="storage-cleanup-list" class="inventory-scroll"></div>
      <div class="inventory-actions cleanup-page">
        <button id="storage-cleanup-prev" type="button">Previous</button>
        <span id="storage-cleanup-page"></span><button id="storage-cleanup-next" type="button">Next</button>
      </div>
    </div>
    <p id="storage-cleanup-selection" class="cleanup-count"></p>
    <div class="report-actions">
      <button type="button" id="storage-cleanup-cancel">Cancel</button>
      <button type="button" id="storage-cleanup-skip" hidden>Continue without cleanup</button>
      <button type="button" id="storage-cleanup-back" hidden>Change selection</button>
      <button type="button" id="storage-cleanup-retry" hidden>Refresh file list</button>
      <button type="button" id="storage-cleanup-review" class="primary">Review selected files…</button>
      <button type="button" id="storage-cleanup-apply" class="danger" hidden>Delete reviewed files</button>
      <button type="button" id="storage-cleanup-done" class="primary" hidden>Done</button>
    </div>`;
  document.body.appendChild(dialog);
  const el = name => document.getElementById('storage-cleanup-'+name);
  const format = value => window.StorageInventory.bytes(value);
  let session = null;

  function finish(reason) {
    if (!session || session.phase === 'applying') return;
    const old = session;
    session = null;
    dialog.close();
    el('list').replaceChildren();
    old.resolve(reason);
  }

  function controls() {
    const {phase,mode,selected,offset,total,receipt} = session;
    const edit = phase === 'list';
    const review = phase === 'review';
    const loading = phase === 'listing' || phase === 'reviewing';
    el('filter').hidden = !(edit || loading || phase === 'error');
    el('category').disabled = !edit;
    el('browser').hidden = !(edit || review || phase === 'applying');
    el('review').hidden = !edit;
    el('review').disabled = !edit || !selected.size;
    el('back').hidden = !review;
    el('apply').hidden = !review;
    el('apply').disabled = !review || !receipt?.review_token;
    el('retry').hidden = phase !== 'error';
    el('cancel').hidden = phase === 'done';
    el('cancel').disabled = phase === 'applying';
    el('skip').hidden = mode !== 'close' || phase === 'done';
    el('skip').disabled = phase === 'applying';
    el('done').hidden = phase !== 'done';
    el('prev').disabled = offset === 0 || !(edit || review);
    el('next').disabled = offset+PAGE >= total || !(edit || review);
    const bytes = [...selected.values()].reduce((sum,file)=>sum+file.bytes, 0);
    el('selection').textContent = (edit || review) ? selected.size+' selected · '+format(bytes) : '';
    el('note').textContent = review
      ? 'Only the listed files will be removed. Deleting a saved project record here keeps its scan, mesh and analysis files. You can review those caches separately.'
      : mode==='close'
        ? 'Shows eligible temporary exports and detached analysis or preview caches. Continue without cleanup to keep all files.'
        : 'Files are listed only when eligible for individual removal. Removing a project record here keeps its working data. Choose Delete project… in the project list to review the complete project and its exclusive data.';
  }

  function makeList(files, selectable) {
    const table = document.createElement('table');
    const header=table.createTHead().insertRow();
    for (const label of selectable ? ['Select','File and location','File size'] : ['File and location','File size']) {
      const th=document.createElement('th'); th.scope='col'; th.textContent=label; header.append(th);
    }
    const body=table.createTBody();
    for (const file of files) {
      const row=body.insertRow();
      if (selectable) {
        const input=document.createElement('input');
        input.type='checkbox'; input.value=file.id; input.dataset.cleanupFile='';
        input.checked=session.selected.has(file.id);
        input.setAttribute('aria-label','Select '+file.name);
        input.onchange=()=>{
          if (session?.phase!=='list') return;
          if (input.checked && session.selected.size >= session.limit) {
            input.checked=false;
            el('status').textContent='Review at most '+session.limit.toLocaleString('en-GB')+' files per cleanup.';
            return;
          }
          if (input.checked) session.selected.set(file.id,file);
          else session.selected.delete(file.id);
          controls();
        };
        row.insertCell().append(input);
      }
      const name=document.createElement('b'); name.textContent=file.name;
      const fullPath=document.createElement('code'); fullPath.textContent=file.path;
      const use=document.createElement('small');
      const changed=Date.parse(file.modified_at);
      const date=Number.isFinite(changed) ? 'Modified '+new Date(changed).toLocaleString('en-GB') : '';
      use.textContent=[title[file.category]||file.category,(file.owners||[]).join(' · '),date,file.protection].filter(Boolean).join(' · ');
      row.insertCell().append(name,fullPath,use);
      row.insertCell().textContent=format(file.bytes);
    }
    return table;
  }

  function renderPage(files) {
    const {phase,offset,total,bytes} = session;
    el('count').textContent = (phase==='review' ? 'Reviewed for deletion · ' : 'Eligible files · ')+
      total.toLocaleString('en-GB')+' files · '+format(bytes);
    el('page').textContent = total ? (offset+1)+'–'+Math.min(offset+PAGE,total)+' of '+total : '0 files';
    el('list').replaceChildren(makeList(files,phase==='list'));
    if (!total) el('list').textContent='No eligible files in this category.';
    el('list').scrollTop=0;
    controls();
  }

  function problem(current,error) {
    if (session!==current) return;
    current.phase='error';
    current.receipt=null;
    // Never carry an old receipt, replacement at the same path, or uncertain
    // apply result into a retry. A fresh selection and explicit review follow.
    current.selected.clear();
    current.offset=0;
    el('status').textContent=error.message+' Refresh to check what is still on disk.';
    controls();
  }

  function categoryOptions(current,groups) {
    const options=[new Option('All eligible files','all')];
    for (const g of groups) {
      if (g.key==='all'||(current.mode==='close'&&!closeGroups.has(g.key))) continue;
      options.push(new Option((g.name||title[g.key]||g.key)+' · '+g.files+' · '+format(g.bytes),g.key));
    }
    if (!options.some(o=>o.value===current.category)) options.push(new Option(title[current.category]||current.category,current.category));
    el('category').replaceChildren(...options);el('category').value=current.category;
  }

  async function list() {
    const current=session;
    if (!current) return;
    current.phase='listing'; current.receipt=null;
    controls(); el('status').textContent='Checking files and project references…';
    try {
      const r=await post('/api/storage-cleanup',{action:'list',mode:current.mode,category:current.category,offset:current.offset,limit:PAGE});
      if (session!==current) return;
      if (!Array.isArray(r.files)||!Number.isSafeInteger(r.total_files)) throw Error('The file listing is unavailable.');
      if (current.offset>0 && current.offset>=r.total_files) {current.offset=0;return list();}
      current.limit=r.selection_limit || 3000;
      current.phase='list'; current.total=r.total_files; current.bytes=r.total_bytes;
      categoryOptions(current,r.categories||[]);
      el('status').textContent='Select individual files to review.';
      renderPage(r.files);
    } catch(error){ problem(current,error); }
  }

  async function reviewSelection() {
    const current=session;
    if (current?.phase!=='list' || !current.selected.size) return;
    current.phase='reviewing'; controls(); el('status').textContent='Checking the selected files again…';
    try{
      const r=await post('/api/storage-cleanup',{action:'review',mode:current.mode,ids:[...current.selected.keys()]});
      if(session!==current)return;
      const ids=new Set(r.files?.map(f=>f.id));
      if (!r.review_token||r.file_count!==current.selected.size||ids.size!==r.file_count||
          !Array.isArray(r.files)||r.files.length!==r.file_count||[...current.selected.keys()].some(i=>!ids.has(i))) {
        throw Error('The reviewed list differs from your selection.');
      }
      current.selected=new Map(r.files.map(f=>[f.id,f]));
      current.receipt=r; current.phase='review'; current.offset=0; current.total=r.file_count; current.bytes=r.bytes;
      el('status').textContent='Check the exact paths and sizes before permanent removal.';
      el('apply').textContent='Delete '+r.file_count+' reviewed '+(r.file_count===1?'file':'files')+' · '+format(r.bytes);
      renderPage(r.files.slice(0,PAGE));
    }catch(error){problem(current,error);}
  }

  async function apply() {
    const current=session;
    if(current?.phase!=='review'||!current.receipt?.review_token)return;
    const receipt=current.receipt;
    current.phase='applying'; controls(); el('status').textContent='Removing the reviewed files…';
    try{
      const r=await post('/api/storage-cleanup',{action:'apply',mode:current.mode,
        ids:receipt.files.map(f=>f.id),expected_token:receipt.review_token,confirm:true});
      if(session!==current)return;
      if(!r.deleted) throw Error('The removal was not confirmed.');
      if(r.partial || r.files !== receipt.file_count || r.bytes !== receipt.bytes) {
        throw Error('The removal receipt differs from the reviewed files. Some files may already have been removed.');
      }
      current.phase='done'; current.selected.clear(); current.receipt=null;
      el('status').textContent='Removed '+r.files+' '+(r.files===1?'file':'files')+' · '+format(r.bytes)+
        (r.purge_pending?' queued for release on disk.':'.');
      controls(); el('done').focus();
    }catch(error){problem(current,error);}
  }

  function changePage(direction) {
    const current=session;
    if(!current||!['list','review'].includes(current.phase))return;
    const offset=current.offset+direction*PAGE;
    if(offset<0||offset>=current.total)return;
    current.offset=offset;
    if(current.phase==='list')list();
    else renderPage(current.receipt.files.slice(offset,offset+PAGE));
  }

  function open({mode='manual'}={}) {
    // Do not confuse a pending native-close caller with a Storage manager action.
    if(session)return session.mode===mode?session.promise:Promise.resolve('cancelled');
    if(!['manual','close'].includes(mode))return Promise.resolve('cancelled');
    let resolve;
    const promise=new Promise(done=>{resolve=done;});
    session={mode,resolve,promise,phase:'listing',category:'all',offset:0,total:0,bytes:0,selected:new Map(),receipt:null};
    el('title').textContent=mode==='close'?'Clear temporary files before closing?':'Choose files to clear';
    el('intro').textContent=mode==='close'?'Review unused temporary files for this workspace, or continue directly.':'Choose individual working files, old saved records, generated reports or exports to clear from this workspace.';
    el('cancel').textContent=mode==='close'?'Keep project open':'Cancel';
    el('done').textContent=mode==='close'?'Continue':'Done';
    dialog.showModal();list();
    return promise;
  }
  el('prev').onclick=()=>changePage(-1);el('next').onclick=()=>changePage(1);
  el('category').onchange=()=>{if(session?.phase==='list'){session.category=el('category').value;session.offset=0;list();}};
  el('review').onclick=reviewSelection;el('apply').onclick=apply;
  el('retry').onclick=list;
  el('back').onclick=()=>{session.offset=0;list();};
  el('skip').onclick=()=>finish('skipped');el('cancel').onclick=()=>finish('cancelled');
  el('done').onclick=()=>finish('cleaned');
  dialog.addEventListener('cancel',e=>{e.preventDefault();finish(session?.phase==='done'?'cleaned':'cancelled');});
  dialog.addEventListener('close',()=>{
    if(session && !dialog.open && session.phase!=='applying')finish(session.phase==='done'?'cleaned':'cancelled');
  });
  return {open, committing:()=>session?.phase==='applying'};
})();
