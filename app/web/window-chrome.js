/* Keep the native caption aligned with the resolved application appearance. */
'use strict';
window.TunnelWindowChrome=(()=>{
 let pending=false,rerun=false;
 async function refresh(){
  const api=window.pywebview?.api;if(!api?.window_chrome_state)return;
  if(pending){rerun=true;return;}pending=true;
  try{const state=await api.window_chrome_state();document.documentElement.classList.toggle('native-caption',state?.active===true);await api.set_window_theme?.(document.documentElement.dataset.theme==='dark'?'dark':'light',document.documentElement.dataset.palette||'navy');}catch(_){/* Standard caption and browser recovery remain usable. */}finally{pending=false;if(rerun){rerun=false;refresh();}}
 }
 window.addEventListener('pywebviewready',refresh);
 window.addEventListener('tunnel-theme-change',refresh);
 new MutationObserver(refresh).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','data-palette']});
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',refresh,{once:true});else refresh();
 return {refresh};
})();
