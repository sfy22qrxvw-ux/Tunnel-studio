/* Copyright (c) 2026 Damian Gulczynski. Application appearance preference. */
'use strict';
(()=>{
 const key='tunnelstudio.theme',paletteKey='tunnelstudio.palette',choices=['light','dark','system'];
 const palettes=window.TUNNEL_PALETTES||{navy:'Navy & teal'};
 const validPalette=value=>Object.hasOwn(palettes,value);
 const system=window.matchMedia?.('(prefers-color-scheme: dark)');
 let preference='light',palette='navy',remembered=true,revision=0,native=null,nativeLoading=false,nativeSaving=0,lastQueuedRevision=-1,nativeQueue=Promise.resolve();
 try{const saved=localStorage.getItem(key),savedPalette=localStorage.getItem(paletteKey);if(choices.includes(saved))preference=saved;if(validPalette(savedPalette))palette=savedPalette;}catch(_){remembered=false;}
 const resolve=()=>preference==='system'?(system?.matches?'dark':'light'):preference;
 function syncControls(){
  document.querySelectorAll('input[name="appearance-theme"]').forEach(input=>{input.checked=input.value===preference;});
  document.querySelectorAll('input[name="appearance-palette"]').forEach(input=>{input.checked=input.value===palette;});
  const status=document.getElementById('theme-status');
  if(status){const name=resolve()==='dark'?'Dark':'Light',saved=nativeSaving?' Saving appearance…':nativeLoading?' Loading appearance…':remembered?' Saved on this PC.':' Applied for this session.';status.textContent=name+' · '+palettes[palette]+(preference==='system'?' · following Windows':'')+'.'+saved;}
  const quick=document.getElementById('appearance-quick');if(quick){const dark=resolve()==='dark';quick.setAttribute('aria-pressed',String(dark));quick.title=dark?'Switch to light appearance':'Switch to dark appearance';quick.innerHTML=dark?'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></svg>':'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 14A9 9 0 0 1 10 3.5 9 9 0 1 0 20.5 14Z"/></svg>';}
 }
 function apply(){
  const resolved=resolve();document.documentElement.dataset.theme=resolved;document.documentElement.dataset.themePreference=preference;document.documentElement.dataset.palette=palette;
  syncControls();window.dispatchEvent(new CustomEvent('tunnel-theme-change',{detail:{preference,resolved,palette}}));
 }
 function cache(){try{localStorage.setItem(key,preference);localStorage.setItem(paletteKey,palette);return true;}catch(_){return false;}}
 function saveNative(){
  if(!native||lastQueuedRevision===revision)return nativeQueue;
  const value=preference,colour=palette,version=revision;lastQueuedRevision=version;nativeSaving++;syncControls();
  // Native calls may finish out of order. Keep the saved choice in the order in
  // which the user made it, and let only the latest choice update its status.
  nativeQueue=nativeQueue.then(()=>native.set_theme_preference(value,colour)).then(result=>{
   if(result?.error||result?.saved!==true||result.theme!==value)throw Error(result?.error||'Appearance was not saved.');
   if(revision===version)remembered=true;
  }).catch(()=>{if(revision===version)remembered=false;}).finally(()=>{nativeSaving--;syncControls();});
  return nativeQueue;
 }
 function connectNative(){
  const api=window.pywebview?.api;
  if(native||typeof api?.get_theme_preference!=='function'||typeof api?.set_theme_preference!=='function')return;
  native=api;
  // A choice already made in this window takes priority over a late bridge.
  if(revision){saveNative();return;}
  nativeLoading=true;syncControls();
  Promise.resolve().then(()=>native.get_theme_preference()).then(result=>{
   if(result?.error)throw Error(result.error);
   if(revision){saveNative();return;}
   if(choices.includes(result?.theme)){preference=result.theme;palette=validPalette(result.palette)?result.palette:'navy';remembered=true;cache();apply();}
   else saveNative();
  }).catch(()=>{if(!revision)remembered=false;}).finally(()=>{nativeLoading=false;syncControls();});
 }
 function remember(){revision++;remembered=cache();apply();connectNative();if(native)saveNative();return true;}
 function setPreference(value){
  if(!choices.includes(value))return false;
  preference=value;return remember();
 }
 function setPalette(value){if(!validPalette(value))return false;palette=value;return remember();}
 function bind(){
  const host=document.querySelector('header .header-right')||document.querySelector('body>header');if(host&&!document.getElementById('appearance-quick')){const quick=document.createElement('button');quick.id='appearance-quick';quick.type='button';quick.setAttribute('aria-label','Toggle dark appearance');quick.onclick=()=>setPreference(resolve()==='dark'?'light':'dark');host.append(quick);}
  document.querySelectorAll('input[name="appearance-theme"]').forEach(input=>input.addEventListener('change',()=>{if(input.checked)setPreference(input.value);}));
  document.querySelectorAll('input[name="appearance-palette"]').forEach(input=>input.addEventListener('change',()=>{if(input.checked)setPalette(input.value);}));syncControls();
 }
 const systemChanged=()=>{if(preference==='system')apply();};
 if(system?.addEventListener)system.addEventListener('change',systemChanged);else system?.addListener?.(systemChanged);
 window.addEventListener('storage',event=>{
  if(event.key===key)preference=choices.includes(event.newValue)?event.newValue:'light';
  else if(event.key===paletteKey)palette=validPalette(event.newValue)?event.newValue:'navy';
  else return;
  revision++;remembered=true;apply();if(native)saveNative();
 });
 window.addEventListener('pywebviewready',connectNative);
 window.ThemeUI={setPreference,setPalette,getPreference:()=>preference,getPalette:()=>palette,resolvedTheme:resolve,whenSaved:()=>nativeQueue};
 apply();
 connectNative();
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bind,{once:true});else bind();
})();
