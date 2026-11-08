/* Copyright (c) 2026 Damian Gulczynski. Size the explorer to visible content. */
'use strict';
(()=>{
 const sidebar=document.querySelector('.job-sidebar'),list=sidebar?.querySelector('.job-table'),table=list?.querySelector('table'),host=sidebar?.closest('.job-layout');
 if(!sidebar||!list||!table||!host)return;
 let queued=false,managed=false,lastHeight=-1;
 const px=value=>Number.parseFloat(value)||0;
 const visible=element=>!element.hidden&&getComputedStyle(element).display!=='none';
 const options=document.createElement('button');options.id='explorer-layout-options';options.type='button';options.textContent='⋯';options.title='Explorer layout options';options.setAttribute('aria-label','Explorer layout options');options.setAttribute('aria-haspopup','menu');options.setAttribute('aria-expanded','false');
 sidebar.querySelector('.job-sidehead-actions').insertBefore(options,document.getElementById('job-collapse-sidebar'));
 const menu=document.createElement('div');menu.className='explorer-layout-menu';menu.id='explorer-layout-menu';menu.hidden=true;menu.setAttribute('role','menu');menu.setAttribute('aria-label','Explorer layout');sidebar.append(menu);options.setAttribute('aria-controls',menu.id);
 const entries=[['Dock left','job-sidebar-left'],['Dock right','job-sidebar-right'],['Float within workspace','job-sidebar-float'],['Open in separate window','explorer-detach-native']];
 for(const [label,id]of entries){const button=document.createElement('button');button.type='button';button.textContent=label;button.dataset.explorerLayout=id;button.setAttribute('role','menuitem');button.onclick=()=>{hideMenu();document.getElementById(id)?.click();schedule();};menu.append(button);}
 function hideMenu(){menu.hidden=true;options.setAttribute('aria-expanded','false');}
 options.onclick=()=>{if(!menu.hidden){hideMenu();return;}for(const button of menu.querySelectorAll('button'))button.disabled=!!document.getElementById(button.dataset.explorerLayout)?.disabled;menu.hidden=false;options.setAttribute('aria-expanded','true');const a=options.getBoundingClientRect(),b=menu.getBoundingClientRect();menu.style.left=Math.max(6,Math.min(innerWidth-b.width-6,a.left))+'px';menu.style.top=Math.max(6,Math.min(innerHeight-b.height-6,a.bottom+4))+'px';menu.querySelector('button')?.focus();};
 menu.onkeydown=event=>{const buttons=[...menu.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement);if(event.key==='Escape'){event.preventDefault();hideMenu();options.focus();}else if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();buttons[(index+(event.key==='ArrowDown'?1:buttons.length-1)+buttons.length)%buttons.length]?.focus();}else if(event.key==='Tab')hideMenu();};
 document.addEventListener('pointerdown',event=>{if(!menu.hidden&&!menu.contains(event.target)&&!options.contains(event.target))hideMenu();});
 function boxSpace(style){return px(style.paddingTop)+px(style.paddingBottom)+px(style.borderTopWidth)+px(style.borderBottomWidth);}
 function decorate(){
  const help=sidebar.querySelector('.scene-list-help');
  if(help&&help.textContent!=='Select items · bulbs show or hide'){
   help.textContent='Select items · bulbs show or hide';
   help.title='Click an item to select it. Ctrl / Shift or checkboxes select several. Yellow bulbs show objects; grey bulbs hide them. Right-click an item for tools and information.';
  }
  const heads=table.querySelectorAll('thead th');
  if(heads.length===3){heads[0].setAttribute('aria-label','Select');if(heads[1].textContent!=='Show')heads[1].textContent='Show';heads[1].title='Yellow bulb means visible; grey means hidden. Visibility is separate from selection.';if(heads[2].textContent!=='Item')heads[2].textContent='Item';}
  for(const name of table.querySelectorAll('[data-scene-item] b,[data-cad-row] b,[data-geometry-id] b,.bundle-heading b,.deleted-group b'))if(name.title!==name.textContent)name.title=name.textContent;
  const exportLabel=sidebar.querySelector('#export-highlighted .ts-tool-label');if(exportLabel&&exportLabel.textContent!=='Export')exportLabel.textContent='Export';
 }
 function contentHeight(element){
  if(element===menu)return 0;
  const style=getComputedStyle(element),extra=px(style.marginTop)+px(style.marginBottom);
  if(element===list)return table.getBoundingClientRect().height+boxSpace(style)+extra;
  if(element.id==='cad-properties'){
   const rows=[...element.children].filter(visible),rect=element.getBoundingClientRect();
   const end=rows.reduce((v,row)=>Math.max(v,row.getBoundingClientRect().bottom+px(getComputedStyle(row).marginBottom)),rect.top+px(style.paddingTop));
   return Math.max(100,end-rect.top+px(style.paddingBottom)+px(style.borderBottomWidth))+extra;
  }
  return element.getBoundingClientRect().height+extra;
 }
 function update(){
  queued=false;decorate();
  const docked=sidebar.parentElement===host&&!document.body.classList.contains('job-sidebar-floating')&&!sidebar.classList.contains('detached-source');
  host.dataset.explorerDetached=String(sidebar.classList.contains('detached-source'));
  if(!docked){if(managed){if(!sidebar.classList.contains('detached-source'))sidebar.style.height='';managed=false;lastHeight=-1;}return;}
  if(!visible(sidebar)||!host.getBoundingClientRect().height)return;
  const bounds=host.getBoundingClientRect(),viewport=host.querySelector('.job-view')?.getBoundingClientRect(),top=sidebar.getBoundingClientRect().top,available=Math.max(0,Math.min(bounds.bottom,viewport?.bottom??bounds.bottom,innerHeight-6)-top);
  const style=getComputedStyle(sidebar),natural=[...sidebar.children].filter(visible).reduce((n,child)=>n+contentHeight(child),0)+boxSpace(style);
  const target=Math.floor(Math.min(Math.max(38,natural),available));
  sidebar.dataset.contentHeight=String(Math.ceil(natural));sidebar.dataset.heightCapped=String(natural>available+1);host.style.setProperty('--ts-explorer-height',target+'px');
  if(target>0&&(Math.abs(target-lastHeight)>1||sidebar.style.height!==target+'px')){lastHeight=target;sidebar.style.height=target+'px';managed=true;}
 }
 function schedule(){if(!queued){queued=true;requestAnimationFrame(update);}}
 const mutations=new MutationObserver(schedule);mutations.observe(sidebar,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['hidden','class','open']});
 // Docking/native-window code can reset inline height without changing the
 // table or viewport bounds. Observe only this node's style; update writes a
 // height only when it differs, so its own change settles on the next frame.
 new MutationObserver(schedule).observe(sidebar,{attributes:true,attributeFilter:['style']});
 new MutationObserver(schedule).observe(document.body,{attributes:true,attributeFilter:['class']});
 if(window.ResizeObserver){const sizes=new ResizeObserver(schedule);sizes.observe(host);sizes.observe(table);const viewport=host.querySelector('.job-view');if(viewport)sizes.observe(viewport);const properties=document.getElementById('cad-properties');if(properties)sizes.observe(properties);}
 sidebar.addEventListener('toggle',schedule,true);window.addEventListener('resize',schedule);document.fonts?.ready.then(schedule);schedule();
 window.ExplorerUI={refresh:schedule};
})();
