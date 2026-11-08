/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
window.ProjectShapefiles = (() => {
  let running = false, generation = 0;
  async function refresh(host) {
    const id = ++generation, list = host.querySelector('[data-gis-list]');
    if (!list) return;
    try {
      const data = await api('/api/project-shapefiles');
      if (id !== generation || host.hidden) return;
      list.replaceChildren();
      for (const r of data.resources) {
        const row = document.createElement('li');
        row.textContent = r.name + ' · ' + r.members + ' files · ' + r.folder;
        list.append(row);
      }
    } catch (error) { if (id === generation) list.textContent = error.message; }
  }
  function show(info) {
    const host = document.getElementById('workspace-shapefiles');
    if (!host) return;
    host.hidden = !info?.dedicated;
    if (host.hidden) { generation++; return; }
    if (host.childElementCount) { void refresh(host); return; }
    const heading = document.createElement('h3');
    heading.textContent = 'GIS shapefile sets';
    const help = document.createElement('p');
    help.textContent = 'Store the geometry, index, attributes and companion files together in GIS/Shapefiles. Each set is included in your portable project copy.';
    const label = document.createElement('label');
    label.textContent = 'Main .shp file';
    const path = document.createElement('input');
    path.type = 'text'; path.autocomplete = 'off'; path.spellcheck = false;
    path.placeholder = 'Full path to the .shp file';
    label.append(path);
    const choose = document.createElement('button'), copy = document.createElement('button');
    choose.type = copy.type = 'button'; choose.textContent = 'Browse…'; copy.textContent = 'Copy shapefile set';
    const list = document.createElement('ul');
    list.setAttribute('data-gis-list','');
    const stop = document.createElement('button'); stop.type='button'; stop.textContent='Stop copy'; stop.hidden=true;
    stop.onclick = async () => {
      try { await post('/api/cancel-operation',{}); }
      catch (error) { status.textContent=error.message; }
    };
    const status = document.createElement('p'); status.setAttribute('role','status');
    choose.onclick = async () => {
      const native = window.pywebview?.api;
      if (!native?.choose_large_file) { path.focus(); status.textContent = 'Enter the full path to the .shp file.'; return; }
      try {
        const selection = await native.choose_large_file('shapefile',false);
        if (selection?.length) path.value = selection[0];
      } catch (error) { status.textContent = error.message; }
    };
    copy.onclick = async () => {
      if (running) return;
      const source = path.value.trim();
      if (!source) { path.focus(); status.textContent = 'Choose the main .shp file before copying.'; return; }
      running = true; choose.disabled = copy.disabled = true;
      try {
        await post('/api/project-shapefile',{path:source});
        stop.hidden=false;
        const job = await window.JobUI.waitReady(job => { status.textContent = job.message; },{skipSync:true});
        if (!job.cancelled) { status.textContent = 'Shapefile set saved in this workspace’s GIS/Shapefiles folder.'; path.value = ''; await refresh(host); }
        else status.textContent = 'Shapefile copy cancelled.';
      } catch (error) { status.textContent = error.message; }
      finally { stop.hidden=true; running = false; choose.disabled = copy.disabled = false; }
    };
    host.append(heading,help,label,choose,copy,stop,status,list);
    void refresh(host);
  }
  return {show};
})();
