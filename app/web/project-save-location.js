/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(() => {
  const field = id => document.getElementById(id);
  const message = text => { field('project-dialog-message').textContent = text; };
  let generation = 0;
  let choosing = false;
  let running = false;
  let workFolder = '';
  let revealing = false;

  async function show(mode) {
    const current = ++generation;
    const saves = mode === 'save' || mode === 'save_as';
    const visible = saves || mode === 'storage' || mode === 'open';
    field('project-location-fields').hidden = !visible;
    field('project-portable-fields').hidden = !saves;
    field('project-copy-path-field').hidden = true;
    field('project-copy-path').value = '';
    workFolder = '';
    field('project-data-copy').disabled = true;
    if (!visible) return;
    field('project-data-location').textContent = 'Checking location…';
    field('project-record-location').textContent = '';
    field('project-data-capacity').textContent = '';
    try {
      const location = await api('/api/project-location');
      if (generation !== current) return;
      workFolder = location.work_folder;
      field('project-data-location').textContent = workFolder;
      field('project-data-copy').disabled = !workFolder;
      field('project-record-location').textContent = location.project_file || `First named save will use ${location.project_folder}`;
      if (location.free_bytes != null) {
        field('project-data-capacity').textContent = `${(location.free_bytes / 1e9).toLocaleString(undefined, {maximumFractionDigits: 1})} GB free on the working drive`;
      }
    } catch (error) {
      if (generation === current) field('project-data-location').textContent = error.message;
    }
  }

  function enterPath(text) {
    field('project-copy-path-field').hidden = false;
    field('project-copy-path').focus();
    message(text);
  }

  async function destination(name) {
    if (!field('project-copy-path-field').hidden) {
      const path = field('project-copy-path').value.trim();
      if (!path) enterPath('Enter the full folder and filename for your portable project.');
      return path;
    }
    const chooser = window.pywebview?.api;
    if (!chooser?.choose_export_file) {
      enterPath('Enter the destination path and choose Save portable copy in… again.');
      return '';
    }
    const filename = (name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim() || 'Tunnel Studio project') + '.tstudio';
    const result = await chooser.choose_export_file(filename, 'tstudio');
    if (result?.error) {
      enterPath(result.error + ' You can also enter the destination path.');
      return '';
    }
    if (!result?.path) message('Folder choice cancelled.');
    return result?.path || '';
  }

  async function saveCopy(storePending, uiValues) {
    if (choosing || running) return;
    choosing = true;
    field('project-copy-in').disabled = true;
    field('project-copy-manual').disabled = true;
    field('project-dialog-close').disabled = true;
    try {
      const name = field('project-name').value.trim();
      const path = await destination(name);
      if (!path) return;
      await storePending();
      const request = {action: 'save_portable_in', name, ui: uiValues(), destination: path};
      let reply = await post('/api/project-action', request);
      if (reply.needs_overwrite) {
        if (!window.confirm(`Replace this portable project after the new copy finishes?\n\n${reply.destination}`)) {
          message('Save cancelled.');
          return;
        }
        reply = await post('/api/project-action', {...request, destination: reply.destination, replace_version: reply.replace_version});
      }
      if (!reply.started) throw Error('The destination changed. Choose the file again.');
      running = true;
      setBusy(true);
      field('project-copy-stop').hidden = false;
      field('project-copy-stop').disabled = false;
      const job = await window.JobUI.waitReady(job => message(job.message), {skipSync: true, keepBusy: true});
      if (job.cancelled) { message('Portable save cancelled.'); return; }
      const location = await api('/api/project-location');
      if (location.last_copy?.id !== reply.id) throw Error('The save receipt is unavailable. Check the chosen destination.');
      message('Portable project saved to ' + location.last_copy.destination);
    } catch (error) {
      message(error.message);
    } finally {
      if (running) setBusy(false);
      running = false;
      choosing = false;
      field('project-copy-stop').hidden = true;
      field('project-copy-in').disabled = false;
      field('project-copy-manual').disabled = false;
      field('project-dialog-close').disabled = false;
    }
  }

  function bind(save) {
    field('project-copy-in').onclick = save;
    field('project-copy-manual').onclick = () => enterPath('Enter a full path including the .tstudio filename.');
    field('project-copy-stop').onclick = async () => {
      field('project-copy-stop').disabled = true;
      try { await post('/api/cancel-operation', {}); }
      catch (error) { if (running) message(error.message); }
    };
    field('project-dialog').addEventListener('cancel', event => {
      if (choosing || running) event.preventDefault();
    });
    field('project-data-copy').onclick = async () => {
      if (!workFolder) return;
      try {
        await navigator.clipboard.writeText(workFolder);
        message('Copied the working data folder path.');
      } catch {
        const range = document.createRange();
        range.selectNodeContents(field('project-data-location'));
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        message('The folder path is selected. Press Ctrl+C to copy it.');
      }
    };
    field('project-data-folder').onclick = async () => {
      if (revealing) return;
      revealing = true;
      field('project-data-folder').disabled = true;
      try {
        let opened = false;
        // In WebView the native API may arrive after the Open Project dialog.
        const native = window.pywebview?.api;
        if (typeof native?.open_project_data_folder === 'function') {
          try { opened = (await native.open_project_data_folder())?.opened === true; }
          catch { /* The authenticated local backend can also open Explorer. */ }
        }
        if (!opened) opened = (await post('/api/open-data-folder', {}))?.opened === true;
        if (!opened) throw Error('Explorer could not open the working folder.');
        message('Asked File Explorer to open the working data folder.');
      } catch (error) {
        message(error.message + (workFolder ? ' You can also use Copy path.' : ''));
      } finally {
        revealing = false;
        field('project-data-folder').disabled = false;
      }
    };
  }

  window.ProjectLocation = {show, bind, saveCopy};
})();
