/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';

// Read-only inventory. All removals go through the existing reviewed transactions.
window.StorageInventory = (() => {
  const get = id => document.getElementById(id);
  const number = (value, digits = 0) => value.toLocaleString('en-GB', {maximumFractionDigits: digits});
  let request = null;
  let pageRequest = null;
  let reviewOrphans;

  function bytes(value) {
    if (!Number.isFinite(value) || value < 0) return 'Unavailable';
    for (const [unit, divisor] of [['TB', 1e12], ['GB', 1e9], ['MB', 1e6], ['KB', 1e3]]) {
      if (value >= divisor) return number(value / divisor, 2) + ' ' + unit;
    }
    return value + ' B';
  }

  function node(tag, value, className) {
    const element = document.createElement(tag);
    if (value != null) element.textContent = value;
    if (className) element.className = className;
    return element;
  }

  function button(label, action) {
    const b = node('button', label);
    b.type = 'button';
    b.onclick = action;
    return b;
  }

  function metric(label, value) {
    const box = node('div', null, 'inventory-metric');
    box.append(node('strong', value), node('span', label));
    return box;
  }

  function pathRow(label, value) {
    const p = node('p', null, 'inventory-path');
    p.append(node('b', label), node('code', value));
    return p;
  }

  function fileTable(files) {
    const table = node('table');
    const head = table.createTHead().insertRow();
    for (const text of ['File and location', 'Size', 'Used by / status']) {
      const th = node('th', text);
      th.scope = 'col';
      head.append(th);
    }
    const body = table.createTBody();
    for (const file of files) {
      const row = body.insertRow();
      const fileCell = row.insertCell();
      fileCell.append(node('b', file.name), node('small', file.category), node('code', file.path));
      row.insertCell().textContent = bytes(file.bytes);
      const owner = row.insertCell();
      if (file.owners?.length) owner.append(node('b', file.owners.join(' · ')));
      owner.append(node('small', file.protection));
    }
    return table;
  }

  function fullCatalogue(result) {
    const box=node('details',null,'inventory-files');
    box.id='storage-inventory-catalog';
    box.append(node('summary','Browse all file paths and project owners · '+number(result.catalog_files)));
    const label=node('label','Search filenames, paths or project names');
    const input=document.createElement('input');
    input.id='storage-catalog-query'; input.type='search'; input.maxLength=256;
    label.append(input);
    const controls=node('div',null,'inventory-actions');
    const lookup=button('Search',()=>load(0,input.value.trim())); lookup.id='storage-catalog-search';
    controls.append(label,lookup);
    const info=node('p',result.catalog_live_pages ? 'Limited temporary space. Each search or page reads the folder again.' :
      'Searches use this inspection. Scan again to include later changes.','tiny');
    const content=node('div',null,'inventory-scroll'); content.id='storage-catalog-table';
    const status=node('p'); status.id='storage-catalog-status';status.setAttribute('role','status');
    let offset=0,query='',total=0,loaded=false;
    const previous=button('Previous',()=>load(offset-50,query)),next=button('Next',()=>load(offset+50,query));
    previous.id='storage-catalog-prev';next.id='storage-catalog-next';
    previous.disabled=next.disabled=true;
    const pagination=node('div',null,'inventory-actions');
    pagination.append(previous,next);
    box.append(info,controls,status,content,pagination);
    async function load(start,search) {
      pageRequest?.abort();
      const current=new AbortController();pageRequest=current;
      lookup.disabled=true;previous.disabled=next.disabled=true;
      status.textContent='Finding matching files…';
      try{
        const params=new URLSearchParams({token:result.catalog_token,q:search,offset:start,limit:50});
        const data=await api('/api/storage-inventory-files?'+params,{signal:current.signal,cache:'no-store'});
        if(pageRequest!==current||!box.isConnected)return;
        offset=data.offset;query=search;total=data.total;
        status.textContent=total ? number(start+1)+'–'+number(start+data.files.length)+' of '+number(total)+' matching file paths' :
          'No matching files.';
        content.replaceChildren(fileTable(data.files));
        content.scrollTop=0;
        previous.disabled=offset===0;next.disabled=start+data.files.length>=total;
        loaded=true;
      }catch(error){
        if(pageRequest===current){status.textContent=error.message;content.replaceChildren();}
      }finally{
        if(pageRequest===current){pageRequest=null;lookup.disabled=false;}
      }
    }
    box.addEventListener('toggle',()=>{
      if(box.open&&!loaded&&box.isConnected)load(0,input.value.trim());
    });
    input.addEventListener('keydown',event=>{
      if(event.key==='Enter'){event.preventDefault();load(0,input.value.trim());}
    });
    return box;
  }

  function render(result) {
    const host = get('storage-inventory-results');
    const metrics = node('div', null, 'inventory-metrics');
    metrics.append(metric(result.complete ? 'Files in this data folder' : 'Files accounted for so far', bytes(result.used_bytes)),
      metric('Free on this drive', bytes(result.drive_free_bytes)));
    host.replaceChildren(metrics, pathRow('Project data folder', result.data_root),
      pathRow('Installed software · protected', result.program_root));
    const groups = node('ul', null, 'inventory-group-list');
    for (const g of result.groups || []) {
      if (!(g.files || g.bytes)) continue;
      const li = node('li');
      li.append(node('span', g.name), node('small', number(g.files) + (g.files === 1 ? ' file' : ' files')), node('b', bytes(g.bytes)));
      groups.append(li);
    }
    host.append(node('h4', 'What uses the space'), groups);
    if (result.by_type?.length) {
      const types = node('details', null, 'inventory-files');
      types.id = 'storage-inventory-types';
      types.append(node('summary', 'Space by file type'));
      const list = node('ul', null, 'inventory-group-list');
      for (const type of result.by_type) {
        const li = node('li');
        li.append(node('span', type.name), node('small', number(type.files)+' files'), node('b', bytes(type.bytes)));
        list.append(li);
      }
      types.append(list); host.append(types);
    }
    host.append(node('p', 'Sizes are file lengths. Shared files are counted once in the folder total.', 'tiny'));
    for (const msg of result.warnings || []) host.append(node('p', msg, 'inventory-warning'));
    if (!result.complete) host.append(node('p', 'Some files or references could not be checked. Review the notices before choosing any cleanup.', 'inventory-warning'));

    if (result.projects?.length) {
      const records = node('details', null, 'inventory-files');
      records.id = 'storage-inventory-projects';
      records.append(node('summary', 'Saved project records · ' + number(result.projects.length)));
      for (const project of result.projects) {
        records.append(pathRow(project.project_title + (project.active ? ' · open' : ''), project.path));
      }
      host.append(records);
    }

    const files = result.files || [];
    if (files.length) {
      const box = node('details', null, 'inventory-files');
      box.id = 'storage-inventory-files';
      box.append(node('summary', 'Largest files · ' + number(files.length) + ' shown of ' + number(result.file_count)));
      const scroll = node('div', null, 'inventory-scroll');
      scroll.append(fileTable(files));
      box.append(scroll);
      host.append(box);
    }
    if (result.catalog_token) host.append(fullCatalogue(result));
    const controls = node('div', null, 'inventory-actions');
    const projectButton = button('Review saved projects', () => {
      const list = get('project-list');
      list.scrollIntoView({block: 'start'});
      list.querySelector('button:not([disabled])')?.focus({preventScroll: true});
    });
    const orphans = button('Review unused working files…', () => reviewOrphans());
    orphans.id = 'storage-inventory-orphans';
    orphans.disabled = !result.complete || !(result.groups || []).some(g => g.key === 'orphans' && g.bytes > 0);
    controls.append(projectButton, orphans);
    host.append(controls);
    get('storage-inventory-status').textContent = result.complete ? 'Folder scan complete.' : 'Partial folder scan.';
  }

  function reset() {
    pageRequest?.abort();pageRequest=null;
    request?.abort();
    request = null;
    get('storage-inventory-results').replaceChildren();
    get('storage-inventory-status').textContent = '';
    const scan = get('storage-inventory-scan');
    scan.disabled = false;
    scan.textContent = 'Inspect disk usage';
  }

  async function scan() {
    if (request) return;
    pageRequest?.abort();pageRequest=null;
    const current = new AbortController();
    request = current;
    const b = get('storage-inventory-scan');
    b.disabled = true;
    get('storage-inventory-results').replaceChildren();
    get('storage-inventory-status').textContent = 'Reading file names, sizes and project references…';
    try {
      const data = await api('/api/storage-inventory', {signal: current.signal, cache: 'no-store'});
      if (request === current) render(data);
    } catch (error) {
      if (request === current) get('storage-inventory-status').textContent = 'Could not inspect this folder. ' + error.message;
    } finally {
      if (request === current) {
        request = null;
        b.textContent = 'Scan again';
        b.disabled = false;
      }
    }
  }

  function bind(action) {
    reviewOrphans = action;
    get('storage-inventory-scan').onclick = scan;
    get('project-dialog').addEventListener('close', reset);
  }

  function previewRemoval(host, sample, total) {
    if (!Array.isArray(sample) || !sample.length) return;
    const files = node('details', null, 'inventory-files');
    files.dataset.reviewFiles = '';
    files.append(node('summary', 'Files in this removal · ' + number(sample.length) +
      (total > sample.length ? ' shown of ' + number(total) : ' shown')));
    const list = node('ul', null, 'inventory-removal');
    for (const f of sample) {
      const item = node('li');
      item.append(node('code', f.path), node('strong', bytes(f.bytes)));
      list.append(item);
    }
    files.append(list);
    host.append(files);
  }

  return {bind, reset, bytes, previewRemoval};
})();
