/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 let selected=null,axisKey=null;
 function points(r){return Array.from({length:97},(_,i)=>{const a=i/96*Math.PI*2;return r.centre.map((v,j)=>v+r.radius*(Math.cos(a)*r.right[j]+Math.sin(a)*r.up[j]));});}
 function show(id){const r=state?.joints?.candidates?.find(x=>x.id===id);if(!r||r.status==='rejected')return;selected=id;window.JobUI.clearCleaningPreview();window.JobUI.focusWorld(points(r));window.JobUI.draw();for(const row of $('joint-table').querySelectorAll('[data-joint-id]'))row.classList.toggle('joint-selected',row.dataset.jointId===id);window.WorkStatus?.show('Ring candidate '+r.id,`Yellow ring · Ch. ${fmt(r.chainage,3)} m · ${fmt(r.signal_mm,1)} mm signal · review against the scan. Use the panel’s Float button if it covers the ring.`);}
 function draw(project){return (state?.joints?.candidates||[]).filter(r=>r.status!=='rejected'&&window.SceneObjectsUI?.visible('joint:'+r.id)!==false).map(r=>{const p=points(r).map(project).filter(Boolean),chosen=r.id===selected;return `<polyline data-joint="${esc(r.id)}" points="${p.map(q=>q.join(',')).join(' ')}" fill="none" stroke="${chosen?'#ffff55':r.status==='kept'?'#58eaa0':'#ba86f0'}" stroke-width="${chosen?5:2}"/>${chosen&&p.length?`<text x="${p[0][0]+8}" y="${p[0][1]-8}" fill="#ffff55" font-size="14">${esc(r.id)} · ${fmt(r.chainage,3)} m</text>`:''}`;}).join('');}
 function sync(preferCurrent=false){
  const select=$('joint-source');if(!select)return;const scans=window.JobUI?.scanItems?.()||[],current=window.JobUI?.selectedItem?.(),previous=select.value;
  select.innerHTML=scans.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('')+(state?.cloud?'<option value="">Current analysis scan</option>':'');
  const wanted=preferCurrent&&current?.kind==='scan'?current.id:select.dataset.ready?previous:current?.id;select.dataset.ready='yes';
  if([...select.options].some(o=>o.value===wanted))select.value=wanted;
  select.disabled=busy;
  const key=JSON.stringify(state?.axis);if(key!==axisKey){axisKey=key;if(state?.axis){$('joint-start').value=state.axis.start;$('joint-end').value=state.axis.end;}}
  for(const id of ['joint-start','joint-end'])$(id).disabled=busy||$('joint-auto-range').checked;
  $('joint-source-info').textContent=!state?.axis?'Import an alignment or review a scan-derived alignment and use it for chainage.':!select.options.length?'Import a scan first.':'Uses retained points from the chosen scan. Analysis preparation runs automatically when needed. Alignment: '+state.axis.name;
 }
 $('joint-auto-range').onchange=()=>sync();
 $('joint-source').onchange=()=>sync();
 window.JointUI={show,draw,sync};sync();
})();
