/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const panel=document.createElement('div');panel.id='job-work-status';panel.className='job-work-status';panel.hidden=true;panel.setAttribute('role','status');panel.setAttribute('aria-live','polite');panel.innerHTML='<span class="work-spinner" aria-hidden="true"></span><div><b id="job-work-title"></b><span id="job-work-detail"></span></div><button id="job-work-dismiss" title="Dismiss status">×</button>';$('job-canvas').parentElement.append(panel);$('job-work-dismiss').onclick=()=>{panel.hidden=true;};
 function show(title,detail,running=false,error=false){panel.hidden=false;panel.classList.toggle('running',running);panel.classList.toggle('error',error);$('job-work-title').textContent=title;$('job-work-detail').textContent=detail||'';$('job-work-dismiss').hidden=running;}
 window.WorkStatus={show};
})();
