/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const dialog=document.createElement('dialog');dialog.id='scan-appearance-dialog';
 dialog.innerHTML=`<div class="dialog-heading"><h2 id="scan-appearance-title">Scan colour</h2><button id="scan-appearance-close" aria-label="Close scan colour">×</button></div><p>Choose this scan's display style. Other scans keep their own settings. Measured RGB, intensity, exported attributes and coordinates are preserved.</p><label>Display style<select id="scan-appearance-mode"><option value="global">Follow global Scan colour setting</option><option value="solid">One chosen colour</option><option value="rgb">Measured RGB</option><option value="intensity-gray">Greyscale intensity</option><option value="intensity-hue">Colour intensity</option><option value="elevation">Elevation colours</option><option value="bundle">Automatic scan colour</option></select></label><label id="scan-appearance-colour-row">Chosen colour<input id="scan-appearance-colour" type="color" value="#00bcd4"></label><div id="scan-appearance-presets" class="feature-actions"></div><p id="scan-appearance-status" role="status"></p><div class="feature-actions"><button id="scan-appearance-global">Use global setting</button><button id="scan-appearance-apply" class="primary">Apply to this scan</button></div><p class="tiny">Workspace Undo restores the previous style. Analysis and pocket-review colours retain their meaning.</p>`;
 document.body.append(dialog);let scan=null,saving=false;
 const el=id=>document.getElementById('scan-appearance-'+id);
 function controls(){const locked=saving||!!busy;el('colour').disabled=locked||el('mode').value!=='solid';for(const id of ['mode','apply','global','close'])el(id).disabled=locked;el('presets').querySelectorAll('button').forEach(b=>b.disabled=locked);}
 for(const colour of ['#00bcd4','#ff7043','#8bd450','#ab69e8','#ffcc33','#ed4a99','#ffffff']){
  const b=document.createElement('button');b.type='button';b.className='scan-colour-preset';b.style.setProperty('--scan-swatch',colour);b.title=colour;b.setAttribute('aria-label','Choose '+colour);b.textContent='●';b.onclick=()=>{el('mode').value='solid';el('colour').value=colour;controls();};el('presets').append(b);
 }
 function open(id){
  if(saving||busy)return;scan=window.JobUI.inventory().items.find(x=>x.id===id&&x.kind==='scan');
  if(!scan)return notice('Choose a point-cloud scan first.',true);
  el('title').textContent=scan.name+' · Scan colour';el('mode').value=scan.display_mode||'global';
  if(!el('mode').value)el('mode').value='global';
  el('colour').value=/^#[0-9a-f]{6}$/i.test(scan.display_colour||'')?scan.display_colour:'#00bcd4';el('status').textContent='';controls();if(!dialog.open)dialog.showModal();
 }
 async function apply(mode=el('mode').value){
  if(saving||busy||!scan)return;
  saving=true;setBusy(true);controls();el('status').textContent='Saving this scan’s display style…';
  try{
   await post('/api/scan-appearance',{action:'set',id:scan.id,mode,colour:el('colour').value});
   setBusy(false);await window.JobUI.refreshAppearance();dialog.close();notice('Scan colour saved. Other scan styles are unchanged.');
  }catch(e){el('status').textContent=e.message;}
  finally{saving=false;setBusy(false);controls();}
 }
 el('mode').onchange=controls;el('apply').onclick=()=>apply();el('global').onclick=()=>apply('global');el('close').onclick=()=>dialog.close();
 dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault();});window.ScanAppearanceUI={open,busy:controls};
})();
