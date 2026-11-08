/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const root=document.getElementById('satellite-content'),status=document.getElementById('satellite-status');let started=false,lastHTML='',lastStyles='',timer=null,queue=Promise.resolve(),drag=null,revision=0;
 const bridge=()=>window.pywebview?.api;
 function send(event){revision++;queue=queue.then(()=>bridge().dispatch(event)).then(r=>{if(r?.error)status.textContent=r.error;}).catch(e=>{status.textContent=e.message;});return queue;}
 function updateDOM(snapshot){
  if(snapshot.html!==lastHTML){const active=document.activeElement,key=active?.dataset.portalNode,selection=typeof active?.selectionStart==='number'?[active.selectionStart,active.selectionEnd]:null,values=key?{value:active.value,checked:active.checked}:null,scroll=[scrollX,scrollY];
   const details=new Map([...root.querySelectorAll('details')].map(d=>[d.dataset.portalNode,d.open]));root.innerHTML=snapshot.html;lastHTML=snapshot.html;
   for(const d of root.querySelectorAll('details'))if(details.has(d.dataset.portalNode))d.open=details.get(d.dataset.portalNode);
   const next=key?root.querySelector('[data-portal-node="'+key+'"]'):null;if(next){next.focus({preventScroll:true});if(selection&&next.setSelectionRange)try{next.setSelectionRange(...selection);}catch(_){}}window.scrollTo(...scroll);
  }
  for(const c of snapshot.canvases||[]){let el=root.querySelector('[data-portal-node="'+c.id+'"]');if(!el)continue;if(el.tagName==='CANVAS'){const img=document.createElement('img');for(const a of el.attributes)img.setAttribute(a.name,a.value);img.dataset.canvas='true';el.replaceWith(img);el=img;}el.src=c.url;el.width=c.width;el.height=c.height;}
 }
 async function installStyles(snapshot,signature){
  const old=[...document.querySelectorAll('[data-mirror-style]')],added=[],pending=[];const first=!lastHTML;
  if(first){root.hidden=true;document.body.style.setProperty('background',snapshot.theme==='dark'?'#0e1724':'#e9eff3','important');}
  try{
   for(const href of snapshot.styles||[]){
    const link=document.createElement('link');link.rel='stylesheet';link.href=href;link.dataset.mirrorStyle='';added.push(link);
    pending.push(new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Tool appearance could not load.')),8000);link.onload=()=>{clearTimeout(timer);resolve();};link.onerror=()=>{clearTimeout(timer);reject(Error('Tool appearance could not load.'));};}));
    document.head.insertBefore(link,document.getElementById('satellite-style'));
   }
   for(const content of snapshot.inlineStyles||[]){const style=document.createElement('style');style.textContent=content;style.dataset.mirrorStyle='';added.push(style);document.head.insertBefore(style,document.getElementById('satellite-style'));}
   await Promise.all(pending);old.forEach(el=>el.remove());lastStyles=signature;root.hidden=false;document.body.style.removeProperty('background');
  }catch(error){added.forEach(el=>el.remove());throw error;}
 }
 async function tick(){if(!started)return;try{await queue;const requestedRevision=revision,s=await bridge().snapshot(innerWidth,innerHeight);if(s.closed){status.textContent='This tool has closed. You can close this window.';return;}document.title='Tunnel Studio · '+s.title;document.getElementById('satellite-title').textContent=s.title;
   document.documentElement.dataset.theme=s.theme==='dark'?'dark':'light';document.documentElement.dataset.palette=s.palette||'navy';document.documentElement.dataset.themePreference=['light','dark','system'].includes(s.themePreference)?s.themePreference:'light';
   const styles=JSON.stringify([s.styles,s.inlineStyles]);if(styles!==lastStyles)await installStyles(s,styles);
   document.body.className=s.bodyClass+' satellite-window';if(!drag&&requestedRevision===revision)updateDOM(s);status.textContent='Shared workspace · changes apply immediately';
  }catch(e){status.textContent='Connection interrupted: '+e.message+' · retrying…';}finally{timer=setTimeout(tick,200);}}
 function init(){if(started||!bridge())return;started=true;document.getElementById('satellite-redock').onclick=()=>bridge().redock();tick();}window.addEventListener('pywebviewready',init);init();window.addEventListener('beforeunload',()=>{started=false;clearTimeout(timer);});
 root.addEventListener('click',e=>{const node=e.target.closest('[data-portal-node]');if(!node||node.closest('summary')||['INPUT','SELECT','TEXTAREA','OPTION'].includes(node.tagName)||node.dataset.canvas)return;e.preventDefault();send({type:'click',node:node.dataset.portalNode});});
 for(const type of ['input','change'])root.addEventListener(type,e=>{const node=e.target;if(node.dataset.portalNode)send({type,node:node.dataset.portalNode,value:node.value,checked:node.checked});});
 root.addEventListener('toggle',e=>{if(e.target.tagName==='DETAILS')send({type:'toggle',node:e.target.dataset.portalNode,open:e.target.open});},true);
 for(const type of ['pointerdown','pointermove','pointerup','dblclick','contextmenu','wheel'])root.addEventListener(type,e=>{const surface=e.target.closest('[data-canvas],svg,[data-portal-drag]'),node=drag||(surface?e.target.closest('[data-portal-node]'):null);if(!node)return;if(type==='pointermove'&&!drag)return;if(type==='contextmenu'||type==='wheel')e.preventDefault();const r=node.getBoundingClientRect();send({type,node:node.dataset.portalNode,x:(e.clientX-r.left)/Math.max(1,r.width),y:(e.clientY-r.top)/Math.max(1,r.height),button:e.button,buttons:e.buttons,ctrlKey:e.ctrlKey,shiftKey:e.shiftKey,altKey:e.altKey,metaKey:e.metaKey,deltaY:e.deltaY,deltaX:e.deltaX});if(type==='pointerdown'){drag=node;node.setPointerCapture(e.pointerId);}if(type==='pointerup')drag=null;},{passive:false});
 root.addEventListener('keydown',e=>{const node=e.target.closest('[data-portal-node]');if(!node)return;if(['INPUT','TEXTAREA','SELECT','BUTTON'].includes(node.tagName)&&!['Escape'].includes(e.key))return;e.preventDefault();send({type:'keydown',node:node.dataset.portalNode,key:e.key,ctrlKey:e.ctrlKey,shiftKey:e.shiftKey,altKey:e.altKey,metaKey:e.metaKey});});
})();
