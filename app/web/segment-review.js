/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
// Panel overlays share the point viewer's projection. A camera change or a new
// review cancels any unfinished drawing before displaying the next frame.
window.TunnelSegmentReview = (() => {
  const strength = {low: 1, medium: 2, high: 3};
  const get = id => document.getElementById(id);
  const validPoint = p => Array.isArray(p) && p.length === 3 && p.every(Number.isFinite);
  let result = null, panels = [], hidden = new Set(), selected = new Set();
  let canvas = null, frame = 0, lastView = '', version = 0;
  const score = row => strength[row.segment_confidence] || 0;

  function vivid(id) {
    const h = (id * 0.618033988749895) % 1;
    const v = .95, s = .85, sector = Math.floor(h * 6), f = h * 6 - sector;
    const p = v * (1 - s), q = v * (1 - s * f), t = v * (1 - s * (1 - f));
    return [[v,t,p],[q,v,p],[p,v,t],[p,q,v],[t,p,v],[v,p,q]][sector];
  }
  function clear() {
    cancelAnimationFrame(frame); frame = 0; lastView = ''; version++;
    if (canvas) canvas.remove();
    canvas = null;
  }
  function configure(next, hiddenIds, selectedIds) {
    if (next !== result) {
      result = next;
      panels = (next?.clusters || []).filter(row => row.feature_kind === 'segment' && validPoint(row.label_centre));
    }
    hidden = hiddenIds; selected = selectedIds; lastView = ''; version++;
    if (!panels.length) clear();
  }
  function draw(project, width, height) {
    const jobCanvas = get('job-canvas'), scene = window.JobUI?.cadScene();
    const edges = get('surf-panel-boundaries')?.checked, numbers = get('surf-panel-numbers')?.checked;
    if (!panels.length || !scene || (!edges && !numbers) || width < 1 || height < 1 ||
        !jobCanvas?.isConnected || !window.SurfaceUI?.reviewActive() || window.JobUI?.previewOwner() !== 'review') {
      if (canvas || frame) clear();
      return;
    }
    const dpr = Math.min(2, window.devicePixelRatio || 1), origin = window.JobUI.cadOrigin();
    const view = [version,width,height,dpr,scene.scale,scene.yaw,scene.pitch,...scene.target,...origin,
      scene.setupCamera?.fov,edges,numbers].join('/');
    if (view === lastView) return;
    lastView = view; cancelAnimationFrame(frame);
    if (!canvas) {
      canvas = document.createElement('canvas'); canvas.id = 'job-panel-overlay'; canvas.setAttribute('aria-hidden','true');
      jobCanvas.parentElement.append(canvas);
    }
    const w = Math.round(width*dpr), h = Math.round(height*dpr);
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!ctx) { clear(); return; }
    ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,width,height);
    ctx.font = 'bold 12px system-ui,sans-serif'; ctx.textAlign='center'; ctx.textBaseline='middle';
    const occupied = new Set(), labels = [];
    let index = 0, shownEdges = 0, shownInferred = 0, drawnLabels = 0;
    canvas.dataset.complete = 'false';
    function paint() {
      frame = 0;
      if (lastView !== view) return;
      const deadline = performance.now()+5;
      do {
        const row = panels[index++];
        if (hidden.has(row.id)) continue;
        const centre = project(row.label_centre), lines = row.panel_outline;
        if (!Array.isArray(lines) || lines.length !== 4) continue;
        let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
        const paths = lines.map(edge => {
          if (!Array.isArray(edge)) return [];
          // Render curved joins with a bounded number of samples even on files
          // authored outside this version. Always retain the final point.
          const pixels = [], stride = Math.max(1, Math.ceil((edge.length-1)/32));
          for (let k=0; k<edge.length; k+=stride) pixels.push(validPoint(edge[k]) ? project(edge[k]) : null);
          if ((edge.length-1)%stride && edge.length) pixels.push(validPoint(edge.at(-1)) ? project(edge.at(-1)) : null);
          for (const p of pixels) if (p) { xmin=Math.min(xmin,p[0]); xmax=Math.max(xmax,p[0]); ymin=Math.min(ymin,p[1]); ymax=Math.max(ymax,p[1]); }
          return pixels;
        });
        if (xmax<0 || ymax<0 || xmin>width || ymin>height || xmax-xmin<2 && ymax-ymin<2) continue;
        if (edges) {
          for (const [k,pixels] of paths.entries()) {
            const inferred = row.boundary_sources?.[k] === 'inferred';
            ctx.strokeStyle = inferred ? '#ffe149' : '#f3ffff';
            ctx.lineWidth = selected.has(row.id) ? 2.3 : 1.35;
            ctx.setLineDash(inferred ? [5,4] : []); ctx.beginPath();
            let pen=false,hasLine=false;
            for (const p of pixels) {
              if (!p) { pen=false; continue; }
              if (pen) { ctx.lineTo(p[0],p[1]); hasLine=true; }
              else ctx.moveTo(p[0],p[1]);
              pen=true;
            }
            if (hasLine) { ctx.stroke(); shownEdges++; if(inferred) shownInferred++; }
          }
        }
        if (numbers && centre && centre[0]>=0 && centre[0]<=width && centre[1]>=0 && centre[1]<=height &&
            (selected.has(row.id) || Math.max(xmax-xmin,ymax-ymin)>=21)) {
          // Lighten distant views and avoid unreadable overprinted labels.
          const cell = `${Math.floor(centre[0]/34)},${Math.floor(centre[1]/20)}`;
          if (!occupied.has(cell)) { occupied.add(cell); labels.push([centre, row]); }
        }
      } while (index<panels.length && performance.now()<deadline);
      if (index<panels.length) { frame=requestAnimationFrame(paint); return; }
      ctx.setLineDash([]); ctx.lineWidth=3;
      for(const [p,row] of labels) {
        const label=String(row.panel_number || '?'); ctx.strokeStyle='#162026'; ctx.strokeText(label,p[0],p[1]);
        ctx.fillStyle=row.inferred?'#ffe149':'#fff'; ctx.fillText(label,p[0],p[1]); drawnLabels++;
      }
      canvas.dataset.edges=String(shownEdges); canvas.dataset.inferredEdges=String(shownInferred);
      canvas.dataset.labels=String(drawnLabels); canvas.dataset.complete='true';
    }
    frame=requestAnimationFrame(paint);
  }
  window.addEventListener('pagehide',clear);
  return {vivid, score, configure, draw, clear};
})();
