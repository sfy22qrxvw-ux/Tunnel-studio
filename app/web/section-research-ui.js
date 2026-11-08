/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 let current=null,pending=false;
 const dialog=document.createElement('dialog');dialog.id='section-research-dialog';dialog.innerHTML=`<div class="dialog-heading"><h2>Robust section comparison</h2><button id="sr-close" aria-label="Close">×</button></div><p>Fit a circular-tunnel section and compare it with the design or a saved measured section. Set the chainage, slice width and wall band below.</p><div class="sr-controls"><label>Chainage · m<input id="sr-station" type="number" step="0.01" required></label><label>Slice width · m<input id="sr-width" type="number" min="0.001" max="5" step="0.01" required></label><label>Wall band ± · m<input id="sr-band" type="number" min="0.001" max="10" step="0.01" required></label><label>Fit threshold · mm<input id="sr-threshold" type="number" min="0.5" max="500" step="0.5" value="20"></label><button id="sr-run" class="primary">Fit / refresh</button><button id="sr-export">Export comparison CSV</button></div><p id="sr-status" role="status">Press Fit / refresh to calculate.</p><div class="sr-grid"><div><svg id="sr-profile" viewBox="0 0 520 520" role="img" aria-label="Raw section sample and robust fitted ellipses"></svg><p class="tiny">Points: orange · measured ellipse: teal · reference: dashed grey. True scale, local right/up coordinates.</p></div><div id="sr-values"></div></div><div class="sr-controls"><label>Reference name<input id="sr-name" maxlength="100" value="Baseline section"></label><button id="sr-save">Save current section as reference</button><button id="sr-clear">Use circular design</button></div><p class="tiny">Coverage and residuals describe this fit. A wall band or outlier threshold can exclude real defects. Compare epochs only in the same registered coordinates and section plane. Eccentricity is dimensionless; rotations are withheld for nearly circular fits.</p>`;document.body.append(dialog);
 const controls=()=>{const q=new URLSearchParams(params());const t=$('sr-threshold');if(!t.checkValidity()||!t.value)throw Error('Enter a fit threshold between 0.5 and 500 mm.');q.set('fit_threshold',t.value);for(const k of ['station','width','band']){const el=$('sr-'+k);if(!el.value||!el.checkValidity())throw Error('Check chainage, slice width and wall band.');q.set(k,el.value);}return q;};
 function locks(){for(const el of dialog.querySelectorAll('button,input'))if(el.id!=='sr-close')el.disabled=pending;$('sr-export').disabled=pending||!current;}
 function draw(r){const f=r.fit,c=r.comparison,ref=r.reference;const extent=Math.max(f.a,ref.a)+Math.max(Math.hypot(...f.centre),Math.hypot(...ref.centre))+.1,scale=235/extent,xy=p=>[260+p[0]*scale,260-p[1]*scale];
  function curve(e){const t=e.angle_rad,C=Math.cos(t),S=Math.sin(t),q=[];for(let i=0;i<=360;i++){const u=i*Math.PI/180,x=e.a*Math.cos(u),y=e.b*Math.sin(u);q.push(xy([e.centre[0]+C*x-S*y,e.centre[1]+S*x+C*y]).join(','));}return q.join(' ');}
  $('sr-profile').innerHTML=`<path d="M25 260H495M260 25V495" stroke="#d7e0e5"/><text x="460" y="253" font-size="12">+right</text><text x="267" y="30" font-size="12">+up</text>`+r.display_points.map(p=>{const [x,y]=xy(p);return `<circle cx="${x}" cy="${y}" r="1" fill="#b56f35" opacity=".5"/>`;}).join('')+`<polyline points="${curve(ref)}" fill="none" stroke="#697b88" stroke-width="2" stroke-dasharray="6 4"/><polyline points="${curve(f)}" fill="none" stroke="#087b75" stroke-width="2"/>`;
  const rows=[['Chainage',fmt(r.plane.chainage,3)+' m'],['Reference',r.reference_kind],['Semi-major / minor',fmt(f.a,5)+' / '+fmt(f.b,5)+' m'],['Fit RMS',fmt(f.fit_rms_mm,3)+' mm (approx.)'],['Fit support',fmt(f.inlier_points,0)+' / '+fmt(f.input_points,0)+' points'],['Largest coverage gap',fmt(f.largest_gap_deg,0)+'°'],['Eccentricity',fmt(f.eccentricity,6)],['Eccentricity change',fmt(c.eccentricity_change,6)],['Centre right / up',fmt(c.centre_right_mm,3)+' / '+fmt(c.centre_up_mm,3)+' mm'],['Centre movement',fmt(c.centre_displacement_mm,3)+' mm'],['Semi-major / minor change',fmt(c.semi_major_change_mm,3)+' / '+fmt(c.semi_minor_change_mm,3)+' mm'],['Orientation change',c.orientation_change_deg===null?'Undefined for circular reference/fit':fmt(c.orientation_change_deg,3)+'°'],['Common-origin radial change',fmt(c.radial_min_mm,3)+' to '+fmt(c.radial_max_mm,3)+' mm']];$('sr-values').innerHTML='<table>'+rows.map(([a,b])=>`<tr><th>${esc(a)}</th><td>${esc(b)}</td></tr>`).join('')+'</table>';$('sr-status').textContent=r.note;
 }
 async function run(){if(pending)return;pending=true;locks();try{current=await api('/api/section-research?'+controls());draw(current);}catch(e){current=null;$('sr-status').textContent=e.message;$('sr-profile').innerHTML='';$('sr-values').textContent='';}finally{pending=false;locks();}}
 async function reference(action){if(pending)return;pending=true;locks();try{const body={action,name:$('sr-name').value,...Object.fromEntries(controls())};await post('/api/section-reference',body);}catch(e){$('sr-status').textContent=e.message;return;}finally{pending=false;locks();}await run();}
 $('sr-close').onclick=()=>dialog.close();$('sr-run').onclick=run;$('sr-save').onclick=()=>reference('save');$('sr-clear').onclick=()=>reference('clear');$('sr-export').onclick=()=>{try{location.href='/api/section-research-csv?'+controls();}catch(e){$('sr-status').textContent=e.message;}};
 $('section-research-open').onclick=()=>{for(const k of ['station','width','band'])$('sr-'+k).value=$(k).value;dialog.showModal();run();};
})();

// Ring dimensions and two-epoch changes in fixed alignment planes.
(() => {
 const dialog = document.createElement('dialog');
 dialog.className = 'ts-analysis-dialog'; dialog.id = 'scan-section-fits';
 dialog.innerHTML = `<header><div><h2>Section dimensions and convergence</h2><p id="sf-axis"></p></div><button type="button" id="sf-close" aria-label="Close">×</button></header>
 <div class="ts-analysis-settings"><label>Baseline scan<select id="sf-a"></select></label>
 <label>Later scan<select id="sf-b"></select></label></div>
 <div id="sf-frame" hidden><label>Common survey datum<input id="sf-datum" maxlength="160" placeholder="Control network and height datum"></label>
 <label><input id="sf-common" type="checkbox">These scans represent separate survey epochs and I have checked both against this common control.</label></div>
 <div class="ts-analysis-settings"><label>Chainages (m), separated by commas<input id="sf-stations" placeholder="100, 102.5, 105" maxlength="500"></label>
 <label>Slice thickness (m)<input type="number" id="sf-width" value="0.10" min="0.001" max="5" step="any"></label>
 <label>Wall band ± (m)<input type="number" id="sf-band" value="0.30" min="0.001" max="10" step="any"></label></div>
 <p>The band selects lining points around the active alignment’s circular design radius. Choose a clean branch and review the fit support at each chainage.</p>
 <footer><button id="sf-run" class="primary">Fit sections</button><button id="sf-stop" disabled>Stop</button><p role="status" aria-live="polite" id="sf-status"></p></footer><section id="sf-report" aria-label="Fitted section report"></section>`;
 document.body.append(dialog);
 const el = key => document.getElementById('sf-' + key);
 let pending = false, loading = false, available = null, runStarted = false;
 function lock(){
   for(const input of dialog.querySelectorAll('input,select')) input.disabled = pending || loading;
   el('close').disabled = pending;
   el('stop').disabled = !runStarted;
   el('run').disabled = pending || loading || !available?.alignment || !available?.sources.length;
   el('frame').hidden = !el('b').value;
 }
 function text(parent, tag, value){const child = document.createElement(tag); child.textContent = value; parent.append(child); return child;}
 function table(host, headings, rows){
   const wrap = document.createElement('div'); wrap.className = 'ts-analysis-table';host.append(wrap);
   const t = document.createElement('table'); wrap.append(t);
   const tr = t.createTHead().insertRow(); for(const label of headings) text(tr, 'th', label);
   const body = t.createTBody(); for(const row of rows){const entry = body.insertRow();for(const v of row)text(entry,'td',v);}
 }
 const fmt = (v, decimals=2) => Number.isFinite(v) ? v.toFixed(decimals) : '—';
 function render(result){
   const host = el('report'); host.replaceChildren(); if(!result)return;
   text(host,'h3','Saved section results');
   text(host,'p',[result.axis_name, result.datum, `Slice ${result.width_m} m`, `Wall band ±${result.band_m} m`].filter(Boolean).join(' · '));
   for(const [name, rows] of [[result.source_a,result.sections_a],[result.source_b,result.sections_b]]){
     if(!name)continue;
     text(host,'h4',name);
     table(host,['Chainage (m)','Circle diameter (m)','Ellipse major / minor (m)','Circle / ellipse RMS (mm)','Fit sample / band points','Review'],rows.map(r=>[
       fmt(r.chainage,3),fmt(2*r.circle?.radius_m,4),[r.ellipse?.major_radius_m,r.ellipse?.minor_radius_m].map(x=>fmt(2*x,4)).join(' / '),
       [r.circle?.rms_mm,r.ellipse?.rms_mm].map(x=>fmt(x)).join(' / '),r.sample_points.toLocaleString()+' / '+r.total_points.toLocaleString(),
       r.error || `Ellipse ${r.ellipse.inliers.toLocaleString()} inliers · largest gap ${fmt(r.ellipse.largest_gap_deg,1)}°`]));
   }
   if(result.comparison){
     text(host,'h4','Change from baseline (mm)');
     text(host,'p','Positive closure means the fitted diameter shortened. Compare closure with the fit residuals above. Centre movement is positive towards the right wall or upwards.');
     table(host,['Chainage (m)','Circle closure','0° (horizontal)','30°','90° (vertical)','150°','Centre right','Centre up','Review'],result.comparison.rows.map(r=>[
       fmt(r.chainage,3),fmt(r.circle_diameter_closure_mm),...[0,30,90,150].map(k=>fmt(r.chord_closure_mm?.[k])),fmt(r.centre_right_mm),fmt(r.centre_up_mm),r.error||'']));
   }
   for(const note of result.warnings)text(host,'p',note);
 }
 async function open(){
   if(pending||loading||dialog.open)return;
   loading=true;available=null;el('report').replaceChildren();el('status').textContent='Reading scans and active alignment…';
   dialog.showModal();lock();
   try{
     const data=await api('/api/scan-section-fits'); if(!dialog.open)return;available=data;
     for(const id of ['a','b']){el(id).replaceChildren();if(id==='b')el(id).add(new Option('Single epoch only',''));for(const s of data.sources)el(id).add(new Option(s.name,s.id));}
     const current=window.JobUI?.selectedItem()?.id;if(data.sources.some(s=>s.id===current))el('a').value=current;
     el('b').value='';el('common').checked=false;el('datum').value='';
     el('axis').textContent=data.alignment ? `${data.alignment.name} · ${data.alignment.start}–${data.alignment.end} m` : 'Load a reviewed alignment to measure sections at fixed chainages.';
     el('stations').value=data.alignment ? String(data.alignment.start) : '';
     render(data.result);el('status').textContent=data.sources.length?'Choose the chainages and epoch scans.':'Import a scan first.';
   }catch(error){el('status').textContent=error.message;}finally{loading=false;lock();}
 }
 el('b').onchange=()=>{el('common').checked=false;lock();};el('a').onchange=()=>{el('common').checked=false;};
 el('run').onclick=async()=>{
   if(pending||loading)return;
   try{
     const tokens=el('stations').value.trim().split(/[\s,]+/),stations=tokens.map(Number);
     if(!tokens[0]||stations.length>32||!stations.every(Number.isFinite))throw Error('Enter 1 to 32 chainages in metres.');
     if(!el('width').checkValidity()||!el('band').checkValidity()||!el('width').value||!el('band').value)throw Error('Check the slice thickness and wall band.');
     const second=el('b').value,datum=el('datum').value.trim();
     if(second===el('a').value)throw Error('Choose two independently observed scans.');
     if(second&&(!datum||!el('common').checked))throw Error('Enter and confirm the common survey datum.');
     pending=true;lock();el('report').replaceChildren();el('status').textContent='Starting section fits…';
     const request=crypto.randomUUID();
     await post('/api/scan-geometry',{action:'section_fits',request_id:request,id:el('a').value,comparison_id:second||null,common_datum:el('common').checked,datum,stations,width:Number(el('width').value),band:Number(el('band').value)});
     runStarted=true;lock();
     const job=await window.JobUI.waitReady(j=>{el('status').textContent=j.message||'Fitting sections…';},{skipSync:true});
     runStarted=false;lock();
     if(job.cancelled){el('status').textContent='Section fitting cancelled.';return;}
     const result=(await api('/api/scan-section-fits')).result;
     if(!result||result.request_id!==request)throw Error('The project or section result changed. Reopen this tool.');
     render(result);el('status').textContent='Section results saved in this project.';
   }catch(error){el('status').textContent=error.message;}finally{runStarted=false;pending=false;lock();}
 };
 el('stop').onclick=async()=>{if(!runStarted)return;el('stop').disabled=true;try{await post('/api/job-action',{action:'cancel'});}catch(e){el('status').textContent=e.message;el('stop').disabled=false;}};
 dialog.oncancel=e=>{if(pending)e.preventDefault();};el('close').onclick=()=>dialog.close();
 window.SectionFitsUI={open};
})();
