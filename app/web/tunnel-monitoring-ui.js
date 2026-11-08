/* Survey epochs stay in their declared frame; monitoring never registers them. */
'use strict';
(()=>{
 const dialog=document.createElement('dialog');dialog.id='tunnel-monitoring-dialog';dialog.className='ts-analysis-dialog';
 const el=id=>document.getElementById('tm-'+id),fmt=v=>Number.isFinite(v)?v.toFixed(3):'—';
 const components={settlement:'Settlement',transverse:'Transverse',longitudinal:'Longitudinal'};
 const triggerColours=['#23994c','#e2a218','#ce3348','#514f9f','#168799','#ad386d','#525d69','#92622e'];
 let saved=null,report=null,chords=[],working=false,dirty=false,chartRows=[];
 let sectionTriggers={},triggersDirty=false;
 const pageSize=200;
 let measurementPage=0;
 dialog.innerHTML=`<header><div><h2>Geotechnical monitoring</h2><p>Saved survey epochs · Point displacement · Settlement · Chord convergence</p></div><button id="tm-close" aria-label="Close monitoring">×</button></header>
 <p>Use the same physical point IDs and an independently checked survey datum at every epoch. Coordinates are compared directly; no fit is applied between epochs.</p>
 <details id="tm-import-panel"><summary>Import survey epochs</summary><div class="ts-analysis-settings"><label>Survey datum / coordinate frame<input id="tm-datum" placeholder="e.g. Project control network and height datum" maxlength="200"></label><button id="tm-file">Choose CSV…</button><button id="tm-template">CSV template</button></div><input type="file" id="tm-file-input" accept=".csv,text/csv" hidden><p>Required columns: <code>epoch,ring_id,point_id,e,n,h</code>. E/N/H in metres. Use ISO dates or timestamps with a timezone. Optional uncertainty columns: <code>sigma_e_mm,sigma_n_mm,sigma_h_mm</code>.</p><textarea id="tm-csv" aria-label="Survey observations CSV" placeholder="epoch,ring_id,point_id,e,n,h"></textarea><button id="tm-import" class="primary">Import and save epochs</button><p id="tm-source">Entered CSV</p></details>
 <div class="ts-analysis-settings"><label>Baseline epoch<select id="tm-baseline"></select></label><label>Warning level (mm)<input id="tm-warning" type="number" min="0" step=".1" placeholder="Optional"></label><label>Alarm level (mm)<input id="tm-alarm" type="number" min="0" step=".1" placeholder="Optional"></label><button id="tm-save" class="primary">Save analysis settings</button></div>
 <p>Point review levels use the largest absolute E/N/H change. Chord levels use absolute convergence. Set project-approved values; this view does not send automated alarms.</p>
 <details><summary>Convergence chords</summary><p>Define two surveyed points on the same ring. Positive convergence means their chord has shortened since the baseline.</p><div class="ts-analysis-settings"><label>Ring<select id="tm-chord-ring"></select></label><label>Chord name<input id="tm-chord-name" placeholder="e.g. Horizontal diameter"></label><label>First point<select id="tm-a"></select></label><label>Second point<select id="tm-b"></select></label><label>Length<select id="tm-mode"><option value="3d">3D chord</option><option value="horizontal">Horizontal chord</option></select></label><button id="tm-add-chord">Add chord</button></div><div id="tm-chords"></div></details>
 <section class="ts-analysis-preview"><div class="ts-analysis-settings"><label>Ring<select id="tm-ring"></select></label><label>Metric<select id="tm-metric"><option value="settlement_mm">Settlement · positive down</option><option value="dE_mm">Easting change</option><option value="dN_mm">Northing change</option><option value="dH_mm">Height change · positive up</option><option value="displacement_mm">3D displacement magnitude</option><option value="convergence_mm">Chord convergence · positive closure</option><option value="settlement_rate_mm_day">Settlement rate · mm/day</option><option value="convergence_rate_mm_day">Convergence rate · mm/day</option></select></label><label>Point / chord<select id="tm-series"></select></label></div><canvas id="tm-chart" aria-label="Monitoring time series with gaps for missing observations"></canvas><p id="tm-chart-hint">Import epochs to view trends.</p></section>
 <details id="tm-graphics"><summary>Geotechnical section diagrams and trends</summary><p>Uses the section selected in Ring. Enter its heading bearing clockwise from grid north to calculate transverse and longitudinal movement.</p><div class="ts-analysis-settings"><label>Heading bearing (°)<input id="tm-heading" type="number" min="0" max="359.999999" step="any" placeholder="0 to under 360"></label><button id="tm-view-section">View selected section</button><button id="tm-export-section">Save section report · HTML</button></div><details id="tm-trigger-editor"><summary>Displacement trigger levels</summary>
 <p>Set the project’s trigger bands in millimetres for each chart. Bands apply to movement of the same magnitude in either direction. Each colour starts at its threshold and extends to the next threshold. These chart settings are separate from the point and chord review levels above.</p>
 <div class="ts-analysis-settings"><label>Movement component<select id="tm-trigger-component"><option value="settlement">Settlement</option><option value="transverse">Transverse</option><option value="longitudinal">Longitudinal</option></select></label><button id="tm-add-trigger">Add trigger level</button></div>
 <div id="tm-trigger-rows"></div><button id="tm-save-triggers" class="primary">Save trigger levels</button>
 <p id="tm-trigger-state" aria-live="polite"></p></details>
 <iframe id="tm-section-preview" title="Section monitoring vector diagrams and trends" sandbox hidden style="width:100%;height:min(64vh,620px);border:0;background:#fff"></iframe></details>
 <p>Untick a prism reading to exclude it from calculations and charts. Tick it again to restore it. Excluding a baseline reading removes comparisons for that prism until the reading is restored or another baseline is saved.</p>
 <div class="ts-analysis-table"><table><thead><tr><th>Epoch (UTC)</th><th>Ring / point or chord</th><th>Value</th><th>Review</th><th>Use observation</th></tr></thead><tbody id="tm-results"></tbody></table></div>
 <div class="ts-analysis-settings"><button id="tm-prev-measurements">Previous readings</button><span id="tm-measurement-page"></span><button id="tm-next-measurements">Next readings</button></div>
 <details><summary>Stored epochs</summary><div id="tm-epochs"></div></details><details><summary>Method and limitations</summary><div id="tm-notes"></div></details>
 <footer><p id="tm-status" role="status" aria-live="polite"></p><button id="tm-export-csv">Export all results · CSV</button><button id="tm-export-report">Report · HTML / print to PDF</button><button id="tm-export-chart">Save chart · PNG</button></footer>`;
 document.body.append(dialog);
 const message=(text,error=false)=>{el('status').textContent=text;el('status').classList.toggle('error',error);};
 function download(text,name,type){const url=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 function controls(){
  for(const x of dialog.querySelectorAll('button,input,select,textarea'))x.disabled=working;
  el('datum').disabled=working||!!saved?.epochs.length;
  for(const id of ['save','add-chord','export-csv','export-report','export-chart'])
   el(id).disabled=working||!saved?.epochs.length||(id.startsWith('export')&&dirty);
  for(const id of ['view-section','export-section'])
   el(id).disabled=working||!saved?.epochs.length||dirty||triggersDirty;
  el('add-trigger').disabled=working||!saved||triggerRows().length>=triggerColours.length;
  el('save-triggers').disabled=working||!triggersDirty;
  measurementControls();
 }
 function clearSection(){const preview=el('section-preview');preview.hidden=true;preview.removeAttribute('srcdoc');}
 function changed(){clearSection();dirty=true;controls();message('Settings changed. Save analysis settings to update the results.');}
 function triggerRows(){return sectionTriggers[el('trigger-component').value]||[];}
 function triggerChanged(){
  triggersDirty=true;clearSection();controls();
  el('trigger-state').textContent='Unsaved trigger changes. Save them to update the section charts.';
 }
 function renderTriggers(){
  const component=el('trigger-component').value,name=components[component],rows=triggerRows();
  const container=el('trigger-rows');
  container.innerHTML=rows.map((row,i)=>`<div class="ts-analysis-settings">
   <label>Name<input data-trigger-field="label" value="${esc(row.label)}" maxlength="40" aria-label="${name} trigger ${i+1} name"></label>
   <label>Starts at (mm)<input data-trigger-field="threshold_mm" type="number" min="0" step="any" value="${esc(String(row.threshold_mm))}" aria-label="${name} trigger ${i+1} threshold in millimetres"></label>
   <label>Colour<input data-trigger-field="colour" type="color" value="${esc(row.colour)}" aria-label="${name} trigger ${i+1} colour"></label>
   <button data-trigger-remove aria-label="Remove ${name.toLowerCase()} trigger ${i+1}">Remove</button></div>`).join('')
   ||'<p>No trigger bands configured for this component.</p>';
  [...container.children].filter(row=>row.classList.contains('ts-analysis-settings')).forEach((element,i)=>{
   for(const input of element.querySelectorAll('input'))input.oninput=()=>{
    rows[i][input.dataset.triggerField]=input.value;triggerChanged();
   };
   element.querySelector('[data-trigger-remove]').onclick=()=>{
    rows.splice(i,1);renderTriggers();triggerChanged();
   };
  });
 }
 el('trigger-component').onchange=()=>{renderTriggers();controls();};
 el('add-trigger').onclick=()=>{
  const component=el('trigger-component').value,rows=sectionTriggers[component]??=[];
  rows.push({label:'Trigger '+(rows.length+1),threshold_mm:'',colour:triggerColours[rows.length]});
  renderTriggers();triggerChanged();
  el('trigger-rows').lastElementChild.querySelector('input[type=number]').focus();
 };
 el('save-triggers').onclick=async()=>{
  working=true;controls();
  try{
   const value=await post('/api/tunnel-monitoring',{action:'configure_section_triggers',section_triggers:sectionTriggers});
   saved.section_triggers=value.data.section_triggers;
   sectionTriggers=structuredClone(saved.section_triggers);
   triggersDirty=false;renderTriggers();
   el('trigger-state').textContent='Trigger levels saved to this project.';
   message('Trigger levels saved. Reopen the section chart to see the updated bands.');
   await poll();
  }catch(e){el('trigger-state').textContent=e.message;message(e.message,true);}
  finally{working=false;controls();}
 };
 function renderChords(){el('chords').innerHTML=chords.map((c,i)=>`<p>${esc(c.ring_id)} · ${esc(c.id)}: ${esc(c.a)} ↔ ${esc(c.b)} (${esc(c.mode)}) <button data-tm-remove-chord="${i}">Remove</button></p>`).join('')||'<p>No chords defined.</p>';for(const b of el('chords').querySelectorAll('button'))b.onclick=()=>{chords.splice(+b.dataset.tmRemoveChord,1);renderChords();changed();};}
 function chordPoints(){const ring=el('chord-ring').value,points=[...new Set((saved?.epochs||[]).flatMap(e=>e.points.filter(p=>p.ring_id===ring).map(p=>p.point_id)))].sort();for(const id of ['a','b'])el(id).innerHTML=points.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');if(points.length>1)el('b').selectedIndex=1;}
 function load(value,keepHeading=false){clearSection();if(!keepHeading)el('heading').value='';saved=value.data;report=value.result;chords=structuredClone(saved.chords);dirty=false;el('datum').value=saved.datum;el('baseline').innerHTML=saved.epochs.map(e=>`<option value="${esc(e.id)}">${esc(e.timestamp)}</option>`).join('');el('baseline').value=saved.baseline;el('warning').value=saved.limits.warning_mm??'';el('alarm').value=saved.limits.alarm_mm??'';for(const id of ['ring','chord-ring']){const old=el(id).value;el(id).innerHTML=report.rings.map(r=>`<option value="${esc(r)}">${esc(r)}</option>`).join('');if(report.rings.includes(old))el(id).value=old;}el('epochs').innerHTML='<table><thead><tr><th>Epoch (UTC)</th><th>Observations</th><th></th></tr></thead><tbody>'+saved.epochs.map(e=>`<tr><td>${esc(e.timestamp)}${e.id===saved.baseline?' · baseline':''}</td><td>${e.points.length}</td><td><button data-tm-remove-epoch="${esc(e.id)}">Remove epoch</button></td></tr>`).join('')+'</tbody></table>';for(const b of el('epochs').querySelectorAll('button'))b.onclick=()=>removeEpoch(b.dataset.tmRemoveEpoch);el('notes').replaceChildren(...report.warnings.map(w=>{const p=document.createElement('p');p.textContent=w;return p;}));el('import-panel').open=!saved.epochs.length;renderChords();chordPoints();series();controls();}
 async function action(body){working=true;controls();try{load(await post('/api/tunnel-monitoring',body),body.action==='set_observation_use');await poll();return true;}catch(e){message(e.message,true);return false;}finally{working=false;controls();}}
 el('file').onclick=()=>el('file-input').click();el('file-input').onchange=async()=>{const file=el('file-input').files[0];if(!file)return;if(file.size>16*1024*1024){message('Choose a CSV no larger than 16 MB.',true);return;}el('csv').value=await file.text();el('source').textContent=file.name;message('CSV loaded for review. Import and save to add these observations.');};el('template').onclick=()=>download('epoch,ring_id,point_id,e,n,h,sigma_e_mm,sigma_n_mm,sigma_h_mm\r\n','TunnelStudio_Monitoring_Template.csv','text/csv');
 el('import').onclick=async()=>{if(await action({action:'import',csv:el('csv').value,datum:el('datum').value,source_name:el('source').textContent})){el('csv').value='';message('Epochs saved. Choose the baseline and define any convergence chords.');}};
 el('save').onclick=async()=>{if(await action({action:'configure',baseline:el('baseline').value,chords,limits:{warning_mm:el('warning').value,alarm_mm:el('alarm').value}}))message('Analysis settings saved. Results use the selected baseline and datum.');};
 for(const id of ['baseline','warning','alarm'])el(id).onchange=changed;
 el('chord-ring').onchange=chordPoints;el('add-chord').onclick=()=>{const c={ring_id:el('chord-ring').value,id:el('chord-name').value.trim(),a:el('a').value,b:el('b').value,mode:el('mode').value};if(!c.id||!c.a||!c.b||c.a===c.b){message('Name the chord and choose two different physical points.',true);return;}if(chords.some(x=>x.ring_id===c.ring_id&&x.id.toLowerCase()===c.id.toLowerCase())){message('That chord name already exists in this ring.',true);return;}chords.push(c);renderChords();changed();};
 async function removeEpoch(id){if(!confirm('Remove this epoch from the monitoring history? Workspace Undo can restore it. If it is the baseline, the earliest remaining epoch becomes the reference.'))return;if(await action({action:'remove_epoch',id}))message('Epoch removed from monitoring. Workspace Undo can restore it.');}
 function series(){const metric=el('metric').value,isChord=metric.startsWith('convergence'),rows=(isChord?report?.chord_series:report?.point_series)||[],field=isChord?'chord_id':'point_id',old=el('series').value;const names=[...new Set(rows.filter(r=>r.ring_id===el('ring').value).map(r=>r[field]))];el('series').innerHTML=names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');if(names.includes(old))el('series').value=old;draw();}
 function measurementControls(){
  el('prev-measurements').disabled=working||measurementPage===0;
  el('next-measurements').disabled=working||(measurementPage+1)*pageSize>=chartRows.length;
  for(const box of el('results').querySelectorAll('[data-observation]'))box.disabled=working||dirty;
 }
 function renderMeasurements(metric,field,unit){
  const start=measurementPage*pageSize,visible=chartRows.slice(start,start+pageSize);
  el('results').innerHTML=visible.map((row,index)=>{
   const observation=typeof row.observation_included==='boolean'
    ?`<input type="checkbox" data-observation="${index}" aria-label="Use ${esc(row.point_id)} observation at ${esc(row.epoch)}"${row.observation_included?' checked':''}>`
    :'—';
   return `<tr><td>${esc(row.epoch)}</td><td>${esc(row.ring_id)} / ${esc(row[field])}</td><td>${fmt(row[metric])} ${unit}</td><td>${esc(row.reason||row.status)}</td><td>${observation}</td></tr>`;
  }).join('');
  el('measurement-page').textContent=visible.length?`${start+1}–${start+visible.length} of ${chartRows.length}`:'';
  for(const box of el('results').querySelectorAll('[data-observation]'))box.onchange=async()=>{
   const index=box.dataset.observation,row=visible[Number(index)],included=box.checked;
   const ok=await action({action:'set_observation_use',epoch:row.epoch,ring_id:row.ring_id,point_id:row.point_id,included});
   if(ok)message(included?'Observation included. Monitoring results updated.':'Observation excluded. Tick it again to restore it.');
   else if(box.isConnected)box.checked=row.observation_included;
   el('results').querySelector(`[data-observation="${index}"]`)?.focus({preventScroll:true});
  };
  measurementControls();
 }
 el('prev-measurements').onclick=()=>{measurementPage--;draw();};
 el('next-measurements').onclick=()=>{measurementPage++;draw();};
 function draw(){const canvas=el('chart'),box=canvas.getBoundingClientRect();if(!box.width||!box.height)return;const ratio=window.devicePixelRatio||1;canvas.width=box.width*ratio;canvas.height=box.height*ratio;const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.fillStyle='#0b1b24';ctx.fillRect(0,0,box.width,box.height);const metric=el('metric').value,isChord=metric.startsWith('convergence'),field=isChord?'chord_id':'point_id',rows=(isChord?report?.chord_series:report?.point_series)||[];chartRows=rows.filter(r=>r.ring_id===el('ring').value&&r[field]===el('series').value);const rate=metric.endsWith('_day'),unit=rate?'mm/day':'mm';measurementPage=Math.min(measurementPage,Math.max(0,Math.ceil(chartRows.length/pageSize)-1));renderMeasurements(metric,field,unit);if(!chartRows.length){ctx.fillStyle='#c0d5df';ctx.font='14px system-ui';ctx.fillText('No series available for this selection',25,40);el('chart-hint').textContent='Import observations or define a convergence chord.';return;}const values=chartRows.map(r=>r[metric]).filter(Number.isFinite),times=chartRows.map(r=>Date.parse(r.epoch)),left=70,right=25,top=40,bottom=45,w=box.width-left-right,h=box.height-top-bottom;let ymin=values.reduce((a,b)=>Math.min(a,b),0),ymax=values.reduce((a,b)=>Math.max(a,b),0),range=Math.max(1,ymax-ymin);ymin-=range*.12;ymax+=range*.12;const xmin=times[0],xmax=Math.max(xmin+86400000,times[times.length-1]),x=t=>left+(t-xmin)/(xmax-xmin)*w,y=v=>top+(ymax-v)/(ymax-ymin)*h;ctx.font='11px system-ui';ctx.lineWidth=1;for(let i=0;i<5;i++){const v=ymin+(ymax-ymin)*i/4;ctx.strokeStyle='#28404c';ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(left+w,y(v));ctx.stroke();ctx.fillStyle='#a8c1ce';ctx.fillText(v.toFixed(2),5,y(v)+4);}ctx.strokeStyle='#7d9aa8';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(left,y(0));ctx.lineTo(left+w,y(0));ctx.stroke();ctx.setLineDash([]);for(const t of [...new Set([times[0],times[Math.floor(times.length/2)],times[times.length-1]])]){ctx.fillStyle='#b5cbd6';ctx.fillText(new Date(t).toISOString().slice(0,10),Math.max(left,Math.min(left+w-75,x(t)-35)),box.height-18);}ctx.fillStyle='#e2f7ff';ctx.font='13px system-ui';ctx.fillText(`${el('ring').value} / ${el('series').value} · ${el('metric').selectedOptions[0].text} (${unit})`,15,22);ctx.strokeStyle='#47d2c3';ctx.lineWidth=2;ctx.beginPath();let connected=false;chartRows.forEach((r,i)=>{if(!Number.isFinite(r[metric])){connected=false;return;}if(connected)ctx.lineTo(x(times[i]),y(r[metric]));else ctx.moveTo(x(times[i]),y(r[metric]));connected=true;});ctx.stroke();chartRows.forEach((r,i)=>{r._chartX=x(times[i]);if(Number.isFinite(r[metric])){ctx.beginPath();ctx.arc(x(times[i]),y(r[metric]),3,0,Math.PI*2);ctx.fillStyle='#47d2c3';ctx.fill();}});el('chart-hint').textContent=`Baseline ${report.baseline} · Datum ${report.datum} · Missing or excluded observations break the line. Hover for epoch values.`;}
 el('chart').onmousemove=e=>{if(!chartRows.length)return;const x=e.clientX-el('chart').getBoundingClientRect().left,r=chartRows.reduce((a,b)=>Math.abs(a._chartX-x)<Math.abs(b._chartX-x)?a:b),metric=el('metric').value;el('chart-hint').textContent=`${r.epoch} · ${fmt(r[metric])} ${metric.endsWith('_day')?'mm/day':'mm'} · ${r.reason||r.status}`;};el('ring').onchange=()=>{measurementPage=0;el('heading').value='';clearSection();series();};el('metric').onchange=()=>{measurementPage=0;series();};el('series').onchange=()=>{measurementPage=0;draw();};new ResizeObserver(draw).observe(el('chart'));
 for(const [id,format] of [['export-csv','csv'],['export-report','html']])el(id).onclick=async()=>{try{const out=await post('/api/tunnel-monitoring',{action:'export',format});download(out.text,out.filename,out.content_type);message(format==='html'?'Report prepared. Open it in a browser and Print → Save as PDF if needed.':'All monitoring results exported.');}catch(e){message(e.message,true);}};
 el('heading').oninput=clearSection;
 async function sectionReport(preview){
  const heading=el('heading'),bearing=heading.valueAsNumber;
  if(!Number.isFinite(bearing)||bearing<0||bearing>=360){message('Enter a heading bearing from 0° to under 360°.',true);heading.focus();return;}
  working=true;controls();try{
   const out=await post('/api/tunnel-monitoring',{action:'export',format:'section_html',ring_id:el('ring').value,heading_deg:bearing});
   if(preview){const frame=el('section-preview');frame.srcdoc=out.text;frame.hidden=false;}
   else download(out.text,out.filename,out.content_type);
   message(preview?'Showing vector views and displacement trends for the selected section.':'Section report saved. Open it in a browser to print or save as PDF.');
  }catch(e){message(e.message,true);}finally{working=false;controls();}
 }
 el('view-section').onclick=()=>sectionReport(true);el('export-section').onclick=()=>sectionReport(false);
 el('export-chart').onclick=()=>el('chart').toBlob(blob=>{if(blob)download(blob,'TunnelStudio_Monitoring_Trend.png','image/png');});
 function close(){if(!working&&(!triggersDirty||confirm('Close without saving changes to the trigger levels?')))dialog.close();}el('close').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
 window.TunnelMonitoringUI={open:async()=>{if(busy||working)return;try{if(window.TunnelRibbon?.prepareTool?.('measure')===false)return;dialog.showModal();measurementPage=0;load(await api('/api/tunnel-monitoring'));
 sectionTriggers=structuredClone(saved.section_triggers||{});triggersDirty=false;
 renderTriggers();controls();el('trigger-state').textContent='';message(saved.epochs.length?`${saved.epochs.length} saved epochs. Results are coordinate differences in the declared datum.`:'Import survey observations to start monitoring.');}catch(e){message(e.message,true);}}};
})();
