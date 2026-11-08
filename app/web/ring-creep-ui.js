/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const panel=$('tab-creep');
 panel.insertAdjacentHTML('afterbegin',`<div class="reg-card"><h3>Calculate ring creep</h3><p>Enter the design and measured chainage of the <b>same ring face</b>, in metres. Creep = (measured − design) × 1000 mm. Positive is ahead along increasing chainage. Chainage observations do not require a loaded scan or alignment.</p><div class="reg-actions"><button id="creep-add">Add ring face</button><button id="creep-import">Import observations…</button><button id="creep-joints">Use kept joint suggestions</button><button id="creep-reset">Reload saved results</button></div><p class="tiny">Joint suggestions supply estimated measured chainages only. Enter the corresponding design chainages and review the ring IDs before calculating.</p><div class="table-scroll"><table><thead><tr><th>Ring ID</th><th>Design · m</th><th>Measured · m</th><th>Face reference</th><th></th></tr></thead><tbody id="creep-inputs"></tbody></table></div><div class="reg-actions"><button id="creep-calculate" class="primary">Calculate &amp; save creep</button><label>Display limit ±mm<input id="creep-check-limit" type="number" min="0.01" step="1" value="20"></label></div><p id="creep-message" role="status">Add ring faces or import a CSV with ring_id, design_chainage_m, measured_chainage_m.</p></div>`);
 let dirty=false,signature=null,saving=false;
 const msg=(s,error=false)=>{$('creep-message').textContent=s;$('creep-message').classList.toggle('error',error);};
 function row(r={}){const tr=document.createElement('tr');tr.dataset.source=r.source||'Entered chainages';
  tr.innerHTML=`<td><input data-field="ring_id" aria-label="Ring ID" value="${esc(r.ring_id??'')}" maxlength="100"></td><td><input data-field="design" aria-label="Design chainage in metres" type="number" step="any" value="${esc(r.design??'')}"></td><td><input data-field="measured" aria-label="Measured chainage in metres" type="number" step="any" value="${esc(r.measured??'')}"></td><td><input data-field="reference" aria-label="Face reference" value="${esc(r.reference??'Ring face')}" maxlength="200"></td><td><button type="button" aria-label="Remove ring face">×</button></td>`;
  tr.querySelector('button').onclick=()=>{tr.remove();changed();};tr.oninput=changed;$('creep-inputs').append(tr);return tr;
 }
 function changed(){dirty=true;msg('Measurements changed. Calculate & save to update the results below.');$('export-rings').disabled=true;}
 function load(){const rows=state?.rings||[];$('creep-inputs').replaceChildren();rows.forEach(row);if(!rows.length)row();signature=JSON.stringify(rows);dirty=false;}
 function values(){return [...$('creep-inputs').children].map(tr=>Object.fromEntries([...tr.querySelectorAll('[data-field]')].map(x=>[x.dataset.field,x.value]).concat([['source',tr.dataset.source]])));}
 function sync(){
  const key=JSON.stringify(state?.rings||[]);if(!dirty&&!saving&&key!==signature)load();
  const block=busy||saving;for(const el of panel.querySelectorAll('input,button'))el.disabled=block;
  $('creep-joints').disabled=block||!(state?.joints?.candidates||[]).some(x=>x.status==='kept');
  $('export-rings').disabled=block||dirty||!state?.rings?.length;
  if(document.activeElement!==$('creep-check-limit'))$('creep-check-limit').value=$('creep-limit').value;
 }
 $('creep-add').onclick=()=>{row().querySelector('input').focus();changed();};
 $('creep-reset').onclick=()=>{if(dirty&&!confirm('Discard unsaved ring-face edits and reload the saved results?'))return;load();msg('Saved results loaded.');sync();};
 $('creep-import').onclick=()=>{if(dirty&&!confirm('Import ring observations and replace unsaved edits?'))return;dirty=false;window.TunnelRibbon?.openImport('rings');};
 $('creep-joints').onclick=()=>{
  const kept=(state?.joints?.candidates||[]).filter(x=>x.status==='kept').sort((a,b)=>a.chainage-b.chainage);
  if(!kept.length){msg('Keep and review joint suggestions first.',true);return;}
  if($('creep-inputs').children.length===1&&values()[0].ring_id===''&&values()[0].design===''&&values()[0].measured==='')$('creep-inputs').replaceChildren();
  const present=new Set(values().map(r=>r.source));let added=0;
  for(const x of kept){const source=`Joint ${x.id} · ${state.joints.source_name||state.joints.source_id||'scan'} · estimated at ${x.resolution_m} m bins`;
   if(present.has(source))continue;row({ring_id:String(x.id),measured:x.chainage,reference:'Review ring face',source});added++;
  }
  changed();msg(added?`${added} estimated joint positions added. Enter design chainages and verify IDs/faces before calculating.`:'These kept joint suggestions are already in the table.');
 };
 $('creep-check-limit').onchange=()=>{const v=+$('creep-check-limit').value;if(!(v>0&&Number.isFinite(v))){msg('Enter a positive display limit in millimetres.',true);return;}$('creep-limit').value=v;renderRings();};
 $('creep-calculate').onclick=async()=>{
  if(busy||saving)return;saving=true;sync();msg('Calculating ring-face creep…');
  try{const result=await post('/api/ring-creep',{rows:values()});state.rings=result.rings;dirty=false;saving=false;load();renderRings();msg(`Saved ${result.rings.length} ring-face results. Positive = ahead; negative = behind design chainage.`);}
  catch(e){msg('Creep was not calculated: '+e.message,true);}
  finally{saving=false;sync();}
 };
 window.RingCreepUI={sync};sync();
})();
