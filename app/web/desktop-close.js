/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
window.TunnelDesktopClose = (() => {
  let pending = null;

  function request(ticket) {
    const bridge = window.pywebview?.api;
    const cleanup = window.StorageCleanup;
    if (!bridge?.finish_project_exit || typeof cleanup?.open !== 'function') return false;
    if (pending === ticket) return true;
    if (pending !== null) return false;
    pending = ticket;
    if (document.getElementById('storage-cleanup-dialog')?.open) {
      // A manual cleanup or project switch owns that review and its result.
      // Keep the application open and let the original operation complete.
      void complete(ticket, 'cancelled');
      return true;
    }
    // Return the handshake before the person chooses a response in the modal.
    Promise.resolve().then(() => cleanup.open({mode:'close'})).then(
      choice => complete(ticket, choice),
      () => complete(ticket, 'cancelled')
    );
    return true;
  }

  async function complete(ticket, choice) {
    if (pending !== ticket) return;
    pending = null;
    try {
      await window.pywebview.api.finish_project_exit(ticket,
        ['skipped','cleaned'].includes(choice) ? choice : 'cancelled');
    } catch (_) {
      // If the bridge went away, a later native X can use the shell fallback.
    }
  }
  return {request};
})();
