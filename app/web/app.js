'use strict';
const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,n=2)=>Number.isFinite(x)?x.toLocaleString('en-GB',{minimumFractionDigits:n,maximumFractionDigits:n}):'—';
let state=null,view=null,result=null,busy=false,queryId=0,pollGeneration=0,pollTimer=null;
let selectSpec=null,editMode="orbit",hiddenBoxes=[],isolatedBox=null,clipRange=null,pickPoints=[];
let lastModalMessage='';
function showTunnelMessage(title,text,error=false,actionLabel=null,action=null){
 const d=$('message-dialog');if(!d)return;
 $('message-title').textContent=title||'Tunnel Studio';$('message-text').textContent=String(text||'');$('message-icon').textContent=error?'!':'i';d.classList.toggle('error',!!error);
 const b=$('message-action');b.hidden=!actionLabel;b.textContent=actionLabel||'Continue';b.onclick=()=>{d.close();if(action)action();};
 if(!d.open)d.showModal();
}
window.showTunnelMessage=showTunnelMessage;
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'error':'';if(error&&text&&text!==lastModalMessage){lastModalMessage=text;showTunnelMessage('Action needed',text,true);}}
$('message-close').onclick=()=>{$('message-dialog').close();setTimeout(()=>{lastModalMessage='';},250);};
$('operation-cancel').onclick=async()=>{const b=$('operation-cancel');if(!state?.job?.busy)return;b.disabled=true;b.textContent='Stopping…';try{await post('/api/cancel-operation',{});$('operation-progress-text').textContent='Stopping safely…';}catch(e){b.disabled=false;b.textContent='Stop';notice(e.message,true);}};
async function api(url,options={}){const r=await fetch(url,options);const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json','X-Tunnel-Token':window.TUNNEL_TOKEN},body:JSON.stringify(data)});
function jobStatusURL(){return state?'/api/status?during_job=1':'/api/status';}
function updateJobSnapshot(snapshot){
 if(state&&Object.keys(snapshot).length===1)state.job=snapshot.job;
 else state=snapshot;
 return snapshot.job;
}
function params(){return new URLSearchParams({station:$('station').value,width:$('width').value,band:$('band').value,tolerance:$('tolerance').value}).toString();}
function reportParams(){return new URLSearchParams({station:result.chainage,width:result.width,band:result.band,tolerance:result.tolerance_mm}).toString();}
function setBusy(v,fromPoll=false){if(v&&!fromPoll){pollGeneration++;clearTimeout(pollTimer);}busy=v;window.MeshResultsUI?.sync();refreshTools();window.TunnelRibbon?.sync();window.TargetUI?.busy(v);window.SurfaceUI?.busy(v);window.JobUI?.busy(v);window.HistoryUI?.sync();window.InspectionUI?.busy();window.FeatureSelectionUI?.busy?.();window.ShapeSelectionUI?.busy?.();window.ScanAppearanceUI?.busy?.();window.CircleCleanUI?.busy?.();window.HeatUI?.sync();window.RingCreepUI?.sync();for(const id of ['demo','path-import','analyse','apply','detect-joints','build-report','build-unwrapped-heat','export-clean','confirm-delete','clip-apply','mode-select','mode-pick','mode-distance','object-save','build-mesh','transform-mesh','reset-mesh-transform','backup-project'])$(id).disabled=v;document.querySelectorAll('.file-card input').forEach(x=>x.disabled=v);}
function durationText(seconds){seconds=Math.max(0,Math.round(seconds||0));const m=Math.floor(seconds/60),s=seconds%60;return m?`${m}m ${String(s).padStart(2,'0')}s`:`${s}s`;}
function updateOperationProgress(job){
 const wrap=$('operation-progress'),bar=$('operation-progress-bar'),text=$('operation-progress-text'),stop=$('operation-cancel');if(!wrap||!bar||!text)return;
 if(!job?.busy){wrap.hidden=true;wrap.classList.remove('indeterminate');bar.style.width='0%';if(stop){stop.disabled=false;stop.textContent='Stop';}return;}
 wrap.hidden=false;const msg=String(job.message||'Working…');let percent=null,m=msg.match(/([\d,]+)\s*\/\s*([\d,]+)(?:\s+(?:source\s+)?points|\s*points|\b)/i);if(m){const a=+m[1].replace(/,/g,''),b=+m[2].replace(/,/g,'');if(b>0)percent=Math.max(.1,Math.min(100,a/b*100));}if(percent===null){m=msg.match(/(\d+(?:\.\d+)?)\s*%/);if(m)percent=Math.max(.1,Math.min(100,+m[1]));}
 const elapsed=job.started_at?Math.max(0,Date.now()/1000-job.started_at):0;let timing=elapsed?` · ${durationText(elapsed)} elapsed`:'';
 if(percent!==null&&percent>1&&elapsed>2){const remain=elapsed*(100-percent)/percent;if(Number.isFinite(remain)&&remain>0)timing+=` · about ${durationText(remain)} remaining`;}
 else if(elapsed>2)timing+=' · estimating time remaining…';
 if(percent===null){wrap.classList.add('indeterminate');bar.style.width='28%';text.textContent=`${msg}${timing}`;}else{wrap.classList.remove('indeterminate');bar.style.left='0';bar.style.width=percent.toFixed(1)+'%';text.textContent=`${Math.round(percent)}% · ${msg}${timing}`;}
 if(stop){stop.disabled=false;stop.textContent='Stop';}
}
async function poll(){
 const generation=++pollGeneration;clearTimeout(pollTimer);const current=()=>generation===pollGeneration;
 try{const snapshot=await api(jobStatusURL());if(!current())return;updateJobSnapshot(snapshot);if(snapshot.version)meshEngineUI();setBusy(state.job.busy,true);updateOperationProgress(state.job);$('status-line').textContent=state.job.message;
  if(state.job.busy){notice(state.job.message);pollTimer=setTimeout(()=>{if(current())poll();},700);return;}
  refreshState();refreshTools();renderObjects();window.TunnelRibbon?.sync();
  // The main viewport becomes usable before optional tool panels refresh.
  await window.JobUI?.sync();if(!current())return;
  await window.TargetUI?.sync();if(!current())return;
  await window.SurfaceUI?.sync();if(!current())return;window.MeshResultsUI?.sync();syncAnalysisTools();
  if(state.job.error)notice(state.job.error,true);else notice(state.demo?'SYNTHETIC DEMO · Example geometry and measurements. Replace them with your own project files.':'Ready. Use the top ribbon to import, edit, register, analyse or model the data in this one 3D workspace.');
  if(state.cloud){
   const canvas=$('cloud-canvas');
   // The legacy analysis viewport is hidden in the shared 3D workspace. Do
   // not reread a giant scan and calculate an unrelated slice after each edit.
   if(canvas.clientWidth&&canvas.clientHeight){const next=await api('/api/overview');if(!current())return;view=next;scene.setData(view);scene.joints(state.joints);await loadObjects();if(!current())return;}
   if($('tab-section')?.classList.contains('active')||window.DetachedUI?.active?.('tab-section'))await analyse();
  }else{view=null;result=null;scene.clear();clearResults();}
 }catch(e){if(!current())return;setBusy(false,true);notice(e.message,true);}
}
function refreshState(){
 const revision=JSON.stringify([state.axis?.name,state.cloud?.name,state.cloud?.count,state.removed_points,state.edit_count]);if(lastAnalysisRevision!==revision){heatData=null;$('heat-canvas').getContext('2d').clearRect(0,0,$('heat-canvas').width,$('heat-canvas').height);$('heat-info').textContent='Generate a new heatmap for the current scan / edits.';lastAnalysisRevision=revision;}
 $('axis-name').textContent=state.axis?state.axis.name:'CSV · chainage, E, N, H, radius';
 $('cloud-name').textContent=state.cloud?`${state.cloud.name} · ${fmt(state.cloud.count,0)} points`:'Leica HSPCA · E57 · LAS/LAZ · PLY · PTS · XYZ/CSV';
 $('ring-name').textContent=state.rings.length?`${state.rings.length} ring-face observations`:'CSV · design and measured chainage';
 $('project-tag').textContent=state.demo?'SYNTHETIC DEMO':(state.axis||state.job_item_count)?'PROJECT DATA':'NO PROJECT';
 const chain=document.querySelector('.chainage-bar');if(chain)chain.hidden=!state.axis||document.body.classList.contains('job-active')||document.body.classList.contains('surface-active')||document.body.classList.contains('registration-active');
 if(state.axis){const a=state.axis;for(const id of ['station','chainage-slider']){$(id).min=a.start;$(id).max=a.end;}
  let s=+$('station').value;if(s<a.start||s>a.end)s=(a.start+a.end)/2;$('station').value=s;$('chainage-slider').value=s;
  $('start-label').textContent=fmt(a.start,3)+' m';$('end-label').textContent=fmt(a.end,3)+' m';}
 $('provenance').textContent=state.cloud?`${fmt(state.cloud.count,0)} full-precision points. ${Number.isFinite(state.valid_points)?fmt(state.cloud.count-state.valid_points,0)+' outside alignment ends.':'Alignment-end count unavailable for the current edits.'} ${state.cloud.invalid_points_skipped} non-finite records excluded on import. Current project saved locally.`:state.axis?'Alignment loaded. Add your registered point cloud.':'No project loaded. Start with the sample tunnel or your design CSV.';
 renderRings();renderJoints();initReportForm();
}
function clearResults(){for(const k of ['station','offset','ovality','count'])$('metric-'+k).textContent='—';$('profile-svg').innerHTML='';$('section-values').innerHTML='<p>Load a scan and choose a chainage.</p>';$('view-count').textContent='';$('cloud-empty').style.display='';}
async function analyse(){
 if(busy||!state?.cloud)return;const id=++queryId;$('analyse').disabled=true;window.WorkStatus?.show('Calculating cross-section…','Checking all retained slice points. Dense sections are processed in chunks.',true);
 let progressPending=false,progressActive=true;
 const progressTimer=setInterval(async()=>{
  if(progressPending||id!==queryId)return;
  progressPending=true;
  try{const {job}=await api('/api/status?job_only=1');if(progressActive&&id===queryId&&/^Large section:|^Balancing section fit:/.test(job?.message||''))window.WorkStatus?.show('Calculating cross-section…',job.message,true);}
  catch(_e){}finally{progressPending=false;}
 },800);
 try{const r=await api('/api/section?'+params());if(id!==queryId)return;result=r;renderResults();scene.section(r);window.WorkStatus?.show('Cross-section ready',`${fmt(r.kept_points,0)} retained points analysed · ${r.large_section?'processed in chunks':'full-density statistics'}.`);$('canvas-station').textContent='Chainage '+fmt(r.chainage,3)+' m';}
 catch(e){if(id===queryId){result=null;clearResults();window.WorkStatus?.show('Cross-section could not be calculated',e.message,false,true);notice(e.message,true);}}
 finally{progressActive=false;clearInterval(progressTimer);if(id===queryId)$('analyse').disabled=false;}
}
function renderResults(){const r=result;if(!r)return;
 $('metric-station').textContent=fmt(r.chainage,3);$('metric-offset').textContent=r.fit_valid?`${fmt(r.circle_centre[0]*1000,1)} / ${fmt(r.circle_centre[1]*1000,1)}`:'Fit withheld';
 $('metric-ovality').textContent=r.fit_valid?fmt(r.ovality_percent,3)+'%':'Fit withheld';$('metric-count').textContent=fmt(r.kept_points,0);
 $('metric-count-note').textContent=`${fmt(r.excluded_points,0)} excluded by wall band`;
 const rows=[['Design diameter',fmt(2*r.radius,4)+' m'],['Min / max radial',fmt(r.min_deviation_mm,1)+' / '+fmt(r.max_deviation_mm,1)+' mm'],['95th |radial deviation|',fmt(r.p95_abs_mm,1)+' mm'],['Outside radial limit',fmt(r.outside_tolerance,0)+' points']];
 if(r.fit_valid)rows.push(['Major / minor diameter',fmt(r.major_diameter,4)+' / '+fmt(r.minor_diameter,4)+' m'],['Horizontal / vertical',fmt(r.horizontal_diameter,4)+' / '+fmt(r.vertical_diameter,4)+' m'],['Ovality difference',fmt(r.ovality_mm,2)+' mm'],['Circle RMS residual',fmt(r.circle_rms_mm,2)+' mm'],['Ellipse residual RMS',fmt(r.ellipse_residual_rms_mm,2)+' mm']);
 rows.push(['Largest angular gap',fmt(r.largest_gap_deg,1)+'°'],['Balanced fit points',fmt(r.fit_points,0)]);
 $('section-values').innerHTML=rows.map(([k,v])=>`<div class="result-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')+`<div class="quality ${r.fit_valid?'':'warn'}">${esc(r.quality)}</div>`;
 renderProfile();$('view-count').textContent=view?`${fmt(view.sample_count,0)} / ${fmt(view.source_count,0)} points shown in 3D`:'';
 $('print-details').textContent=`Tunnel Studio ${state.version} · ${state.demo?'SYNTHETIC DEMO':'Project validation pending'} · Design: ${state.axis.name} · Scan: ${state.cloud.name} · Chainage ${r.chainage} m · Slice width ${r.width} m · Wall band ±${r.band} m · Radial limit ±${r.tolerance_mm} mm · Creep limit ±${$('creep-limit').value} mm. Ovality = 100(Dmajor−Dminor)/Ddesign; balanced 1° bins; normalised implicit ellipse fit. Right/up use the local section plane. Supplied alignment is interpolated as a polyline. Ring creep uses the declared face chainages. This report is not project approval.`;
}
function svgLine(x1,y1,x2,y2,color,width=1,dash=''){return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${width}" ${dash?`stroke-dasharray="${dash}"`:''}/>`;}
function renderProfile(){const r=result;if(!r)return;const mag=$('exaggerate').checked?10:1;const span=r.radius+Math.max(.6,r.band*mag*.55);const scale=230/span,cx=340,cy=278;const X=x=>cx+x*scale,Y=y=>cy-y*scale;let s='';
 for(let i=Math.ceil(-span);i<=span;i++){s+=svgLine(X(i),38,X(i),518,'#e5ecef');s+=svgLine(80,Y(i),600,Y(i),'#e5ecef');}
 s+=svgLine(75,cy,605,cy,'#b9cbd2',1,'4 4')+svgLine(cx,35,cx,520,'#b9cbd2',1,'4 4');
 s+=`<circle cx="${cx}" cy="${cy}" r="${r.radius*scale}" fill="none" stroke="#416faf" stroke-width="1.7"/>`;
 const stride=Math.max(1,Math.ceil(r.points.length/6000));
 for(let i=0;i<r.points.length;i+=stride){let [u,v,d]=r.points[i];const len=Math.hypot(u,v);if(mag!==1&&len>0){const k=(r.radius+(len-r.radius)*mag)/len;u*=k;v*=k;}const color=d>r.tolerance_mm?'#cb7855':d< -r.tolerance_mm?'#4c87ca':'#138f81';s+=`<circle cx="${X(u).toFixed(2)}" cy="${Y(v).toFixed(2)}" r="1.5" fill="${color}" opacity=".7"/>`;}
 if(r.fit_valid&&mag===1){let path='';for(let i=0;i<=180;i++){const t=i/180*Math.PI*2,a=r.angle_deg*Math.PI/180;const u=r.ellipse_centre[0]+r.a*Math.cos(t)*Math.cos(a)-r.b*Math.sin(t)*Math.sin(a),v=r.ellipse_centre[1]+r.a*Math.cos(t)*Math.sin(a)+r.b*Math.sin(t)*Math.cos(a);path+=(i?'L':'M')+X(u).toFixed(2)+','+Y(v).toFixed(2);}
  s+=`<path d="${path}" fill="none" stroke="#9a6ac1" stroke-width="1.6" stroke-dasharray="5 3"/>`;let x=X(r.circle_centre[0]),y=Y(r.circle_centre[1]);s+=svgLine(x-5,y,x+5,y,'#9a6ac1',2)+svgLine(x,y-5,x,y+5,'#9a6ac1',2);}
 s+=`<circle cx="${cx}" cy="${cy}" r="3" fill="#416faf"/><text x="347" y="32" fill="#647d87" font-size="12">Up +</text><text x="598" y="296" fill="#647d87" font-size="12">Right +</text><text x="24" y="548" fill="#647d87" font-size="11">1 m grid · plane normal to alignment · Ch. ${fmt(r.chainage,3)}</text>`;
 $('profile-svg').innerHTML=s;$('scale-note').textContent=mag===1?'True scale · ellipse through fitted centre':'Radial deviations ×10 · fit overlay hidden';
}
function renderRings(){window.RingCreepUI?.sync();const rings=[...(state?.rings||[])].sort((a,b)=>a.design-b.design),tol=+$('creep-limit').value;if(!(tol>0)){notice('Creep limit must be positive.',true);return;}
 $('ring-table').innerHTML=rings.length?rings.map(r=>`<tr data-station="${r.design}"><td><b>${esc(r.ring_id)}</b></td><td>${fmt(r.design,3)}</td><td>${fmt(r.measured,3)}</td><td>${r.creep_mm>=0?'+':''}${fmt(r.creep_mm,2)}</td><td><span class="pill ${Math.abs(r.creep_mm)>tol?'out':''}">${Math.abs(r.creep_mm)>tol?'Outside limit':'Within limit'}</span></td><td>${esc(r.reference)}<br><small>${esc(r.source)}</small></td></tr>`).join(''):'<tr><td colspan="6">Import ring-face observations to see the checks.</td></tr>';
 $('ring-table').querySelectorAll('tr[data-station]').forEach(row=>row.onclick=()=>{if(state?.axis&&state?.cloud){setStation(+row.dataset.station);analyse();}});
 if(!rings.length){$('creep-svg').innerHTML='<text x="500" y="130" text-anchor="middle" fill="#718993">No ring-face observations loaded</text>';return;}
 const min=Math.min(...rings.map(r=>r.design)),max=Math.max(...rings.map(r=>r.design)),lim=Math.max(tol*1.5,...rings.map(r=>Math.abs(r.creep_mm)*1.2));const X=x=>60+(x-min)/Math.max(max-min,1)*880,Y=y=>123-y/lim*90;
 let s='';for(const y of [-tol,0,tol])s+=svgLine(50,Y(y),960,Y(y),y?'#e5b795':'#beced4',1,y?'5 4':'')+`<text x="43" y="${Y(y)+4}" text-anchor="end" fill="#728991" font-size="11">${fmt(y,0)}</text>`;
 s+=`<path d="${rings.map((r,i)=>(i?'L':'M')+X(r.design)+','+Y(r.creep_mm)).join('')}" fill="none" stroke="#378c83" stroke-width="1.5"/>`;
 for(const r of rings)s+=`<circle cx="${X(r.design)}" cy="${Y(r.creep_mm)}" r="4" fill="${Math.abs(r.creep_mm)>tol?'#d98b5f':'#198a7b'}"><title>${esc(r.ring_id)}: ${fmt(r.creep_mm,2)} mm</title></circle>`;
 s+=`<text x="60" y="241" fill="#718993" font-size="11">${fmt(min,3)} m</text><text x="880" y="241" fill="#718993" font-size="11">${fmt(max,3)} m</text><text x="60" y="19" fill="#718993" font-size="11">Creep (mm) · selected limit ±${tol} mm</text>`;$('creep-svg').innerHTML=s;
}
function renderJoints(){const j=state?.joints;if(!j){$('joint-table').innerHTML='<tr><td colspan="7">Choose the range and expected ring width, then select Suggest joints.</td></tr>';return;}
 $('joint-summary').textContent=`${j.source_name?j.source_name+' · ':''}${j.candidates.length} geometric candidates. ${j.eligible_bins} of ${j.total_bins} bins have at least ${j.minimum_sectors||16} observed sectors. Range ${fmt(j.start,3)}–${fmt(j.end,3)} m. ${j.diagnostic||''} Position bins: ${fmt(j.bin_width*1000,1)} mm; this is not a statement of accuracy. Purple = suggested, green = kept. Click Inspect to see a section.`;
 $('joint-table').innerHTML=j.candidates.length?j.candidates.map(r=>`<tr data-joint-id="${esc(r.id)}"><td><b>${esc(r.id)}</b> · ${esc(r.type)}</td><td>${fmt(r.chainage,3)}</td><td>${fmt(r.signal_mm,1)} mm</td><td>${r.supported_sectors}/24 sectors</td><td>${fmt(r.spacing_m,3)} m</td><td>${esc(r.status)}</td><td><button data-show-joint="${esc(r.id)}">Show in 3D</button> <button data-inspect="${r.chainage}">Section</button> <button data-keep="${r.id}">Keep</button> <button data-reject="${r.id}">Reject</button></td></tr>`).join(''):'<tr><td colspan="7">No supported geometric candidates. Do not infer that the tunnel has no joints. Check point density, range, wall band and signal threshold.</td></tr>';
 for(const b of $('joint-table').querySelectorAll('[data-show-joint]'))b.onclick=()=>window.JointUI?.show(b.dataset.showJoint);for(const row of $('joint-table').querySelectorAll('[data-joint-id]'))row.onclick=e=>{if(!e.target.closest('button'))window.JointUI?.show(row.dataset.jointId);};window.JobUI?.draw();
 $('joint-table').querySelectorAll('[data-inspect]').forEach(b=>b.onclick=()=>{setStation(+b.dataset.inspect);analyse();document.querySelector('[data-tab="section"]').click();});
 for(const [key,status]of [['keep','kept'],['reject','rejected']])$('joint-table').querySelectorAll(`[data-${key}]`).forEach(b=>b.onclick=async()=>{try{await post('/api/review-joint',{id:b.dataset[key],status});state=await api('/api/status');renderJoints();scene.joints(state.joints);await window.JobUI?.sync();}catch(e){notice(e.message,true);}});
}
const DEFAULT_VIEWPORT_BG='#0b1b24';
function viewportBackgroundHex(){const raw=(localStorage.getItem('tunnelstudio.viewportBackground')||DEFAULT_VIEWPORT_BG).trim();return /^#[0-9a-fA-F]{6}$/.test(raw)?raw:DEFAULT_VIEWPORT_BG;}
function viewportBackgroundRgb(){const h=viewportBackgroundHex();return [parseInt(h.slice(1,3),16)/255,parseInt(h.slice(3,5),16)/255,parseInt(h.slice(5,7),16)/255];}
function setViewportCss(hex){document.documentElement.style.setProperty('--ts-viewport-bg',hex);}
setViewportCss(viewportBackgroundHex());
const VIEW_CLICK_THRESHOLD=4;
class CloudScene{
 constructor(canvas,{adaptive=false}={}){this.c=canvas;this.gl=canvas.getContext('webgl',{antialias:true,preserveDrawingBuffer:true});this.yaw=-.85;this.pitch=.37;this.scale=36;this.target=[0,0,0];this.data=null;this.sectionData=null;
  if(!this.gl){notice('3D requires WebGL. Try current Edge or Chrome with hardware acceleration enabled.',true);return;}
  const gl=this.gl;const shader=(type,src)=>{const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
  this.program=gl.createProgram();gl.attachShader(this.program,shader(gl.VERTEX_SHADER,'attribute vec3 pos;attribute vec3 colour;attribute float deletionFlag;uniform float pendingDelete;uniform vec3 target;uniform vec3 right;uniform vec3 up;uniform vec3 forward;uniform float scale;uniform float depthRange;uniform float aspect;uniform float perspective;uniform float tanHalf;uniform float nearPlane;uniform float farPlane;uniform float pointsize;uniform vec3 lodEye;uniform float lodDistanceScale;uniform float pointContrast;uniform float pointGamma;uniform float clipEnabled;uniform vec3 clipCentre;uniform vec3 clipAxis0;uniform vec3 clipAxis1;uniform vec3 clipAxis2;uniform vec3 clipHalf;varying vec3 c;void main(){vec3 d=pos-target;gl_Position=vec4(dot(d,right)/scale,dot(d,up)*aspect/scale,-dot(d,forward)/depthRange,1.0);if(perspective>0.5){float z=dot(d,forward);gl_Position=vec4(dot(d,right)/(tanHalf*aspect),dot(d,up)/tanHalf,((farPlane+nearPlane)*z-2.0*farPlane*nearPlane)/(farPlane-nearPlane),z);}vec3 q=pos-clipCentre;vec3 local=vec3(dot(q,clipAxis0),dot(q,clipAxis1),dot(q,clipAxis2));if(clipEnabled>0.5&&any(greaterThan(abs(local),clipHalf)))gl_Position=vec4(2.0,2.0,2.0,1.0);if(deletionFlag>2.5||(pendingDelete>0.5&&deletionFlag>1.5))gl_Position=vec4(2.0,2.0,2.0,1.0);gl_PointSize=pointsize;if(lodDistanceScale>0.0){float distanceM=length(pos-lodEye)*lodDistanceScale;gl_PointSize=max(1.0,pointsize*sqrt(20.0/max(20.0,distanceM)));}c=pow(clamp((colour-0.5)*pointContrast+0.5,0.0,1.0),vec3(1.0/pointGamma));if(deletionFlag>0.5&&deletionFlag<2.5)c=vec3(1.0,0.9,0.25);}'));gl.attachShader(this.program,shader(gl.FRAGMENT_SHADER,'precision mediump float;varying vec3 c;uniform float drawOpacity;void main(){gl_FragColor=vec4(c,drawOpacity);}'));gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
  this.uniformLocations=new Map();this.attributeLocations={pos:gl.getAttribLocation(this.program,'pos'),colour:gl.getAttribLocation(this.program,'colour'),deletionFlag:gl.getAttribLocation(this.program,'deletionFlag')};this.bufferRadii=new Map();
  if(adaptive){
   this.spatialPoints=new TunnelDisplayBuffers.SpatialPointDraw(gl,()=>this.draw(),(percent,error)=>this.onPointIndexProgress?.(percent,error));
   canvas.addEventListener('webglcontextlost',()=>{this.spatialPoints.reset();this.clearPointPages();});
  }
  this.buffer=gl.createBuffer();this.lines=gl.createBuffer();this.sectionBuffer=gl.createBuffer();this.jointBuffer=gl.createBuffer();this.objectBuffer=gl.createBuffer();this.objectCount=0;this.pointCount=0;this.lineCount=0;this.sectionCount=0;this.jointCount=0;
  let drag=null,contextUntil=0;
  this.cancelNavigation=()=>{if(drag){if(drag.moved)contextUntil=performance.now()+450;canvas.style.cursor=drag.cursor;}drag=null;};
  this.suppressContextMenu=()=>!!drag?.moved||performance.now()<contextUntil;
  canvas.addEventListener('pointerdown',e=>{
   if((editMode!=='orbit'&&!this.canNavigate?.(e))||e.isPrimary===false||![0,1,2].includes(e.button))return;
   this.cancelNavigation();contextUntil=0;
   canvas.setPointerCapture(e.pointerId);
   drag={id:e.pointerId,start:[e.clientX,e.clientY],last:[e.clientX,e.clientY],moved:false,editMode,cursor:canvas.style.cursor,pan:!this.setupCamera&&(e.button===1||e.button===2||e.shiftKey||window.__tunnelForcePan)};
  });
  for(const name of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(name,e=>{if(drag?.id===e.pointerId)this.cancelNavigation();});
  window.addEventListener('blur',this.cancelNavigation);
  canvas.addEventListener('pointermove',e=>{
   if(!drag||drag.id!==e.pointerId)return;
   if(editMode!==drag.editMode||!e.buttons){this.cancelNavigation();return;}
   // Admit the gesture once. Modifier/button flag changes cannot turn a pan
   // into an orbit halfway through; a right/middle chord can only promote pan.
   if(!this.setupCamera&&(e.buttons&6))drag.pan=true;
   if(!drag.moved&&Math.hypot(e.clientX-drag.start[0],e.clientY-drag.start[1])>VIEW_CLICK_THRESHOLD){drag.moved=true;this.onNavigationStart?.();}
   if(!drag.moved)return;
   const dx=e.clientX-drag.last[0],dy=e.clientY-drag.last[1];drag.last=[e.clientX,e.clientY];
   if(drag.pan){
    canvas.style.cursor='grabbing';
    const {right,up}=this.basis(),k=this.scale*2/canvas.clientWidth;
    this.target=this.target.map((v,i)=>v-dx*k*right[i]+dy*k*up[i]);
   }else{const fine=e.ctrlKey?.1:1,k=this.setupCamera?this.setupCamera.fov*Math.PI/180/Math.max(1,canvas.clientHeight):.007;this.yaw+=this.orbitHorizontal(dx)*k*fine;this.pitch=Math.max(-1.55,Math.min(1.55,this.pitch+dy*(this.setupCamera?k:.006)*fine));}
   this.navigationDraw();
  });
  canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('wheel',e=>{e.preventDefault();if(this.setupCamera)this.setupCamera.fov=Math.max(8,Math.min(105,this.setupCamera.fov*Math.exp(e.deltaY*.001)));else this.scale=Math.max(.02,Math.min(100000,this.scale*Math.exp(e.deltaY*.001)));this.navigationDraw();},{passive:false});new ResizeObserver(()=>{this.draw();this.onViewportResize?.();}).observe(canvas);
 }
 navigationDraw(){this.onNavigate?.();this.navigationUntil=performance.now()+180;clearTimeout(this.navigationTimer);this.navigationTimer=setTimeout(()=>{this.navigationUntil=0;this.draw();},200);if(this.navigationFrame)return;this.navigationFrame=requestAnimationFrame(()=>{this.navigationFrame=null;this.draw();});}
 orbitHorizontal(dx){return -dx;}
 basis(){const f=[Math.sin(this.yaw)*Math.cos(this.pitch),Math.cos(this.yaw)*Math.cos(this.pitch),Math.sin(this.pitch)],right=[Math.cos(this.yaw),-Math.sin(this.yaw),0],up=[-Math.sin(this.yaw)*Math.sin(this.pitch),-Math.cos(this.yaw)*Math.sin(this.pitch),Math.cos(this.pitch)];const frame={forward:f,right,up};if(this.setupCamera){const R=this.setupCamera.orientation;for(const key of Object.keys(frame))frame[key]=R.map(row=>row.reduce((sum,v,i)=>sum+v*frame[key][i],0));}return frame;}
 depthRange(){const t=this.cadTarget||this.target;return Math.max(1,Math.hypot(...t)+(this.geometryRadius||0)+Math.hypot(...this.target)+(this.cadRadius||0)+100);}
 projectLocal(point,b=this.basis()){
  const d=point.map((v,i)=>v-this.target[i]),dot=a=>d.reduce((sum,v,i)=>sum+v*a[i],0),z=dot(b.forward),aspect=this.c.clientWidth/Math.max(1,this.c.clientHeight);
  if(this.setupCamera){const c=this.setupCamera;if(z<c.near||z>c.far)return null;const t=Math.tan(c.fov*Math.PI/360),x=dot(b.right)/(z*t*aspect),y=dot(b.up)/(z*t);return Math.abs(x)<=1&&Math.abs(y)<=1?[x,y,z]:null;}
  return [dot(b.right)/this.scale,dot(b.up)*aspect/this.scale,z];
 }
 upload(buffer,values){this.gl.bindBuffer(this.gl.ARRAY_BUFFER,buffer);const packed=values instanceof Float32Array?values:new Float32Array(values);this.gl.bufferData(this.gl.ARRAY_BUFFER,packed,this.gl.STATIC_DRAW);let squared=0;for(let i=0;i<packed.length;i+=6){const x=packed[i],y=packed[i+1],z=packed[i+2],r=x*x+y*y+z*z;if(r>squared)squared=r;}this.bufferRadii.set(buffer,Math.sqrt(squared));this.geometryRadius=Math.max(0,...this.bufferRadii.values());if(buffer===this.buffer&&this.spatialPoints)this.spatialPoints.prepare(packed);}
 clearPointPages(){
  if(this.gl)for(const page of this.pointPages||[]){this.gl.deleteBuffer(page.buffer);this.bufferRadii.delete(page.buffer);}
  this.pointPages=[];this.pointPageBytes=0;this.pointPagesActive=false;this.inspectionId=null;
  this.geometryRadius=Math.max(0,...this.bufferRadii.values());
 }
 addPointPage(values){
  if(!this.gl)throw Error('WebGL is unavailable for pocket inspection.');
  if(!(values instanceof Float32Array)||values.length%6||values.length/6>100000)throw Error('Invalid pocket display page.');
  // A local graphics budget, independent of whole-cloud analysis/selection.
  if((this.pointPageBytes||0)+values.byteLength>192*1024*1024)throw Error('This group exceeds the local graphics memory budget. Inspect a smaller compact object; full-density selection and export remain available.');
  const buffer=this.gl.createBuffer();if(!buffer)throw Error('Graphics memory is unavailable for this pocket page.');
  try{this.upload(buffer,values);const error=this.gl.getError();if(error===this.gl.OUT_OF_MEMORY||this.gl.isContextLost())throw Error('Graphics memory became unavailable. Return to matches and retry this pocket.');}catch(error){this.gl.deleteBuffer(buffer);this.bufferRadii.delete(buffer);throw error;}
  (this.pointPages||(this.pointPages=[])).push({buffer,count:values.length/6,values});this.pointPageBytes=(this.pointPageBytes||0)+values.byteLength;this.pointPagesActive=true;this.draw();
 }
 setData(data){this.data=data;if(!this.gl)return;this.recolour();let lines=[];const put=(p,c)=>lines.push(...p,...c);const axis=data.axis;
  for(let i=1;i<axis.length;i++){put(axis[i-1].slice(0,3),[.65,.82,.86]);put(axis[i].slice(0,3),[.65,.82,.86]);}
  const stride=Math.max(1,Math.floor(axis.length/35));for(let i=0;i<axis.length;i+=stride){let p=axis[i],q=axis[Math.min(i+1,axis.length-1)],prev=axis[Math.max(i-1,0)];let d=i===axis.length-1?q.map((v,k)=>v-prev[k]):q.map((v,k)=>v-p[k]);const horizontal=Math.hypot(d[0],d[1]),length=Math.hypot(...d.slice(0,3));if(!horizontal||!length)continue;const right=[d[1]/horizontal,-d[0]/horizontal,0],up=[-d[0]*d[2]/horizontal/length,-d[1]*d[2]/horizontal/length,horizontal/length];for(let k=0;k<64;k++){for(const t of [k/64*2*Math.PI,(k+1)/64*2*Math.PI])put(p.slice(0,3).map((v,j)=>v+p[4]*(Math.cos(t)*right[j]+Math.sin(t)*up[j])),[.17,.33,.38]);}}
  this.upload(this.lines,lines);this.lineCount=lines.length/6;if(!this.initialised){this.reset();this.initialised=true;}else this.draw();$('cloud-empty').style.display='none';
 }
 recolour(){if(!this.gl||!this.data)return;const tol=Math.max(.01,+$('tolerance').value),band=Math.max(.001,+$('band').value)*1000;let values=[];
  for(const p of this.data.points){if(Math.abs(p[3])>band||!visiblePoint(p))continue;const t=Math.min(1,Math.abs(p[3])/tol),base=[.32,.70,.66],end=p[3]>0?[.96,.56,.34]:[.36,.63,.96];values.push(p[0],p[1],p[2],...(selectSpec&&insideBox(p,selectSpec)?[1,.9,.25]:base.map((v,i)=>v+(end[i]-v)*t)));}
  this.upload(this.buffer,values);this.pointCount=values.length/6;this.draw();
 }
 reset(){if(!this.data)return;const a=this.data.axis;let lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const p of a)for(let i=0;i<3;i++){lo[i]=Math.min(lo[i],p[i]-p[4]);hi[i]=Math.max(hi[i],p[i]+p[4]);}this.target=lo.map((v,i)=>(v+hi[i])/2);this.scale=Math.max(...hi.map((v,i)=>v-lo[i]))*.63;this.yaw=-.85;this.pitch=.37;this.draw();}
 section(r){this.sectionData=r;if(!this.gl||!this.data)return;const centre=r.centre.map((v,i)=>v-this.data.origin[i]),right=r.right,up=r.up,R=r.radius+Math.min(r.band,.5),colour=[.86,1,.48];let values=[];
  for(let k=0;k<96;k++)for(const t of [k/96*2*Math.PI,(k+1)/96*2*Math.PI])values.push(...centre.map((v,i)=>v+R*(Math.cos(t)*right[i]+Math.sin(t)*up[i])),...colour);
  for(const vec of [right,up])for(const sign of [-1,1])values.push(...centre.map((v,i)=>v+R*vec[i]*sign),...colour);
  this.upload(this.sectionBuffer,values);this.sectionCount=values.length/6;this.draw();
 }
 focus(){if(!this.sectionData||!this.data)return;this.target=this.sectionData.centre.map((v,i)=>v-this.data.origin[i]);this.scale=this.sectionData.radius*2;this.draw();}
 joints(report){if(!this.gl||!this.data)return;let values=[];for(const r of report?.candidates||[]){if(r.status==='rejected')continue;const centre=r.centre.map((v,i)=>v-this.data.origin[i]),colour=r.status==='kept'?[.3,.95,.6]:[.75,.47,.95],radius=r.radius+.035;for(let k=0;k<64;k++)for(const t of [k/64*2*Math.PI,(k+1)/64*2*Math.PI])values.push(...centre.map((v,i)=>v+radius*(Math.cos(t)*r.right[i]+Math.sin(t)*r.up[i])),...colour);}this.upload(this.jointBuffer,values);this.jointCount=values.length/6;this.draw();}
 clear(){this.clearPointPages();this.spatialPoints?.reset();this.initialised=false;this.data=null;this.pointCount=0;this.lineCount=0;this.sectionCount=0;this.jointCount=0;this.objectCount=0;this.draw();}
 draw(){const gl=this.gl;if(!gl||!this.c.clientWidth||!this.c.clientHeight)return;const dpr=Math.min(devicePixelRatio,2),w=Math.max(1,Math.round(this.c.clientWidth*dpr)),h=Math.max(1,Math.round(this.c.clientHeight*dpr));if(this.c.width!==w||this.c.height!==h){this.c.width=w;this.c.height=h;}gl.viewport(0,0,w,h);const bg=viewportBackgroundRgb();gl.clearColor(bg[0],bg[1],bg[2],1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);if(!this.modelOnly&&this.setupCamera&&this.setupBackdropVisible)this.setupBackdrop?.draw(this.basis(),w/h,this.setupCamera.fov);gl.enable(gl.DEPTH_TEST);gl.useProgram(this.program);const uniform=name=>{if(!this.uniformLocations.has(name))this.uniformLocations.set(name,gl.getUniformLocation(this.program,name));return this.uniformLocations.get(name);},basis=this.basis();for(const key of ['right','up','forward'])gl.uniform3fv(uniform(key),basis[key]);gl.uniform3fv(uniform('target'),this.target);gl.uniform1f(uniform('scale'),this.scale);gl.uniform1f(uniform('depthRange'),this.depthRange());gl.uniform1f(uniform('aspect'),w/h);gl.uniform1f(uniform('pointsize'),(+$('point-size').value||2)*dpr);
  const camera=this.setupCamera;gl.uniform1f(uniform('perspective'),camera?1:0);gl.uniform1f(uniform('tanHalf'),Math.tan((camera?.fov||70)*Math.PI/360));gl.uniform1f(uniform('nearPlane'),camera?.near||.03);gl.uniform1f(uniform('farPlane'),camera?.far||1000);
  if(this.liveClip){gl.uniform3fv(uniform('clipCentre'),this.liveClip.centre);gl.uniform3fv(uniform('clipHalf'),this.liveClip.half_size);this.liveClip.axes.forEach((a,i)=>gl.uniform3fv(uniform('clipAxis'+i),a));}
  const draw=(buffer,count,mode)=>{const isModel=[this.cadFacesBuffer,this.cadLinesBuffer,this.cadPointsBuffer,this.cadPreviewFacesBuffer,this.cadPreviewLinesBuffer].includes(buffer);if((this.cadHidden&&isModel)||(this.modelOnly&&!isModel))return;const opacity=buffer===this.buffer&&this.setupCamera?(this.pointOpacity??1):1;if(!count||opacity<=0)return;gl.uniform1f(uniform('drawOpacity'),opacity);if(opacity<1){gl.enable(gl.BLEND);gl.blendFuncSeparate(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA,gl.ONE,gl.ONE_MINUS_SRC_ALPHA);}else gl.disable(gl.BLEND);const isPage=!!this.pointPages?.some(p=>p.buffer===buffer),isCloud=buffer===this.buffer||isPage;
   const frame=isCloud&&!isPage&&this.spatialPoints?TunnelDisplayBuffers.viewFrame(basis,this.target,this.scale,w/h,camera,this.depthRange()):null;
   gl.uniform1f(uniform('lodDistanceScale'),frame?frame.distanceScale:0);
   if(frame)gl.uniform3fv(uniform('lodEye'),frame.eye);
   const moving=isCloud&&!isPage&&!this.spatialPoints?.grid&&!this.fullPointDetail&&!this.pointVertexIds&&performance.now()<(this.navigationUntil||0);
   const step=moving?Math.max(1,Math.min(10,Math.ceil(count/100000))):1;
   gl.uniform1f(uniform('pointContrast'),isCloud?(this.pointContrast||1):1);
   gl.uniform1f(uniform('pointGamma'),isCloud?(this.pointGamma||1):1);
   gl.uniform1f(uniform('clipEnabled'),isCloud&&this.liveClip?1:0);
   const deletion=this.attributeLocations.deletionFlag;
   const flagStream=isCloud&&!isPage&&this.deletionBuffer&&this.deletionBufferCount>=count;
   gl.uniform1f(uniform('pendingDelete'),flagStream&&this.pendingDelete?1:0);
   if(isCloud)this.cloudFallbackStep=this.spatialPoints?.grid?1:step;
   const bindPage=(firstVertex)=>{
    gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
    for(const [name,offset] of [['pos',0],['colour',12]]){
     const a=this.attributeLocations[name];gl.enableVertexAttribArray(a);
     gl.vertexAttribPointer(a,3,gl.FLOAT,false,24*step,firstVertex*24+offset);
    }
    if(flagStream){
     gl.bindBuffer(gl.ARRAY_BUFFER,this.deletionBuffer);gl.enableVertexAttribArray(deletion);
     gl.vertexAttribPointer(deletion,1,gl.UNSIGNED_BYTE,false,step,firstVertex);
    }else{gl.disableVertexAttribArray(deletion);gl.vertexAttrib1f(deletion,0);}
   };
   if(!frame||!this.spatialPoints.draw(frame,this.fullPointDetail,bindPage)){
    bindPage(0);gl.drawArrays(mode,0,Math.ceil(count/step));
   }
   gl.disable(gl.BLEND);};if(this.meshCount && this.meshMode!=='wire'){gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(1,1);draw(this.meshBuffer,this.meshCount,gl.TRIANGLES);gl.disable(gl.POLYGON_OFFSET_FILL);}if(this.meshMode==='wire'||this.meshMode==='both')draw(this.meshWireBuffer,this.meshWireCount,gl.LINES);if(this.cadTarget)gl.uniform3fv(uniform('target'),this.cadTarget);gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(1,1);draw(this.cadFacesBuffer,this.cadFacesCount,gl.TRIANGLES);draw(this.cadPreviewFacesBuffer,this.cadPreviewFacesCount,gl.TRIANGLES);gl.disable(gl.POLYGON_OFFSET_FILL);draw(this.cadLinesBuffer,this.cadLinesCount,gl.LINES);draw(this.cadPointsBuffer,this.cadPointsCount,gl.POINTS);draw(this.cadPreviewLinesBuffer,this.cadPreviewLinesCount,gl.LINES);gl.uniform3fv(uniform('target'),this.target);if(!this.hideCloud){if(this.pointCount)draw(this.buffer,this.gpuPointCount??this.pointCount,gl.POINTS);for(const page of this.pointPages||[])draw(page.buffer,page.count,gl.POINTS);}draw(this.lines,this.lineCount,gl.LINES);gl.disable(gl.DEPTH_TEST);draw(this.objectBuffer,this.objectCount,gl.LINES);draw(this.jointBuffer,this.jointCount,gl.LINES);draw(this.sectionBuffer,this.sectionCount,gl.LINES);
 }
}
const scene=new CloudScene($('cloud-canvas'),{adaptive:true});
function setStation(value){if(state?.axis)value=Math.max(state.axis.start,Math.min(state.axis.end,value));$('station').value=value.toFixed(3);$('chainage-slider').value=value;}
for(const tab of document.querySelectorAll('.tabs button'))tab.onclick=()=>{document.querySelectorAll('.tabs button,.tab').forEach(t=>t.classList.remove('active'));tab.classList.add('active');$('tab-'+tab.dataset.tab).classList.add('active');scene.draw();window.TargetUI?.draw();window.SurfaceUI?.draw();window.JobUI?.draw();};

function initialiseDisplaySettings(){
 const picker=$('viewport-bg'),value=$('viewport-bg-value'),reset=$('viewport-bg-reset');if(!picker||!value)return;
 const apply=(hex,save=true)=>{if(!/^#[0-9a-fA-F]{6}$/.test(hex))hex=DEFAULT_VIEWPORT_BG;hex=hex.toLowerCase();if(save)localStorage.setItem('tunnelstudio.viewportBackground',hex);setViewportCss(hex);picker.value=hex;value.textContent=hex.toUpperCase();scene.draw();window.JobUI?.draw();};
 apply(viewportBackgroundHex(),false);
 picker.oninput=()=>apply(picker.value);
 for(const b of document.querySelectorAll('[data-bg]'))b.onclick=()=>apply(b.dataset.bg);
 if(reset)reset.onclick=()=>{localStorage.removeItem('tunnelstudio.viewportBackground');apply(DEFAULT_VIEWPORT_BG,false);};
}
initialiseDisplaySettings();
$('demo').onclick=async()=>{resetEditingView();scene.initialised=false;try{setBusy(true);await post('/api/demo',{});poll();}catch(e){setBusy(false);notice(e.message,true);}};
for(const kind of ['axis','cloud','rings'])$(kind==='axis'?'axis-file':kind==='cloud'?'cloud-file':'rings-file').onchange=async e=>{
 const f=e.target.files[0];if(!f)return;if(kind==='cloud'&&f.size>150000000){e.target.value='';$('path-kind').value='cloud';showTunnelMessage('Use the large-file browser','This point cloud is large. Read it directly from disk instead of transferring it through the embedded WebView. This avoids the blank black/white window problem.',true,'Browse point cloud',()=>{document.body.classList.remove('panel-hidden');document.querySelector('.path-load').open=true;$('path-browse').click();});return;}if(kind!=='rings'){resetEditingView();scene.initialised=false;}try{setBusy(true);notice('Copying '+f.name+' to the local app…');await api('/api/upload?'+new URLSearchParams({kind,name:f.name}),{method:'POST',headers:{'X-Tunnel-Token':window.TUNNEL_TOKEN},body:f});poll();}catch(err){setBusy(false);notice(err.message,true);}finally{e.target.value='';}
};
async function browseLargeFile(){
 try{
  const bridge=window.pywebview?.api;
  if(!bridge?.choose_large_file){notice('Windows file picker is unavailable in browser fallback mode. Paste the full local path instead.',true);return;}
  const paths=await bridge.choose_large_file($('path-kind').value,false);
  if(paths&&paths.length)$('local-path').value=paths[0];
 }catch(e){notice('Could not open the Windows file picker. You can still paste the full local path.',true);}
}
$('path-browse').onclick=browseLargeFile;
$('path-import').onclick=async()=>{resetEditingView();scene.initialised=false;try{setBusy(true);await post('/api/load',{kind:$('path-kind').value,path:$('local-path').value});poll();}catch(e){setBusy(false);notice(e.message,true);}};
$('analyse').onclick=analyse;$('apply').onclick=()=>{scene.recolour();renderRings();analyse();};
$('station').onchange=()=>{const v=+$('station').value;if(Number.isFinite(v)){setStation(v);analyse();}};
let sliderTimer;$('chainage-slider').oninput=()=>{setStation(+$('chainage-slider').value);clearTimeout(sliderTimer);sliderTimer=setTimeout(analyse,220);};
$('previous').onclick=()=>{setStation(+$('station').value-.25);analyse();};$('next').onclick=()=>{setStation(+$('station').value+.25);analyse();};
$('exaggerate').onchange=renderProfile;$('creep-limit').onchange=renderRings;$('reset-view').onclick=()=>scene.reset();$('focus-view').onclick=()=>scene.focus();
$('export-section').onclick=()=>{if(result&&!busy)location.href='/api/export-section?'+reportParams();};$('export-report').onclick=()=>{if(result&&!busy)location.href='/api/report?'+reportParams();};$('export-rings').onclick=()=>{if(state?.rings.length&&!busy)location.href='/api/export-rings';};
$('print').onclick=()=>document.querySelector('[data-tab=reports]').click();
$('detect-joints').onclick=async()=>{try{setBusy(true);await post('/api/detect-joints',{scan_id:$('joint-source')?.value||null,start:+$('joint-start').value,end:+$('joint-end').value,pitch:+$('joint-pitch').value,bin_width:+$('joint-bin').value,threshold_mm:+$('joint-threshold').value,band:+$('joint-band').value,auto_range:$('joint-auto-range').checked,minimum_sectors:+$('joint-coverage').value});poll();}catch(e){setBusy(false);notice(e.message,true);}};
$('export-joints').onclick=()=>{if(state?.joints)location.href='/api/export-joints';};
// Editing controls are initialised before the first status request.
function cameraSpec(rect){return {origin:view.origin,target:scene.target.slice(),...scene.basis(),scale:scene.scale,aspect:scene.c.clientWidth/scene.c.clientHeight,rect,start:clipRange?clipRange[0]:state.axis.start,end:clipRange?clipRange[1]:state.axis.end,band:+$('band').value,hidden:hiddenBoxes.slice(),isolated:isolatedBox};}
function insideBox(p,s){const v=p.slice(0,3).map((n,i)=>n+view.origin[i]-s.origin[i]-s.target[i]),dot=a=>v.reduce((n,x,i)=>n+x*a[i],0),x=dot(s.right)/s.scale,y=dot(s.up)*s.aspect/s.scale,z=-dot(s.forward)/(s.scale*12);return x>=s.rect[0]&&x<=s.rect[2]&&y>=s.rect[1]&&y<=s.rect[3]&&Math.abs(z)<=1&&p[4]>=s.start&&p[4]<=s.end&&Math.abs(p[3])<=s.band*1000;}
function visiblePoint(p){return (!clipRange||(p[4]>=clipRange[0]&&p[4]<=clipRange[1]))&&!hiddenBoxes.some(s=>insideBox(p,s))&&(!isolatedBox||insideBox(p,isolatedBox));}
function bareSpec(s){const {hidden,isolated,...b}=s;return b;}
function resetEditingView(){selectSpec=null;hiddenBoxes=[];isolatedBox=null;clipRange=null;pickPoints=[];$('clip-enabled').checked=false;}
function refreshTools(){$('backup-info').textContent=state?.last_backup?'Last backup: '+state.last_backup:'';const has=!!state?.cloud,sel=has&&!!selectSpec&&state.selection_count>0&&!busy;
 for(const id of ['delete-selection','hide-selection','isolate-selection'])$(id).disabled=!sel;
 $('undo-edit').disabled=!has||busy||!state.edit_count;$('redo-edit').disabled=!has||busy||!state.redo_count;
 $('download-clean').disabled=busy||!state?.clean_ready;$('open-report').disabled=busy||!state?.report_ready;$('download-report').disabled=busy||!state?.report_ready;
 $('selection-status').textContent=(selectSpec?fmt(state?.selection_count||0,0)+' selected':'No selection')+' · '+fmt(state?.removed_points||0,0)+' excluded from analysis';
 $('report-status').textContent=state?.report_ready?'Report ready. Regenerate after changing report settings.':'Generate a report to enable viewing and download.';
}
function clearSelection(){selectSpec=null;scene.recolour();refreshTools();}
for(const [id,mode] of [['mode-orbit','orbit'],['mode-select','select'],['mode-pick','pick'],['mode-distance','distance']])$(id).onclick=()=>{editMode=mode;for(const k of ['mode-orbit','mode-select','mode-pick','mode-distance'])$(k).classList.toggle('chosen',k===id);scene.c.style.cursor=mode==='orbit'?'grab':'crosshair';if(mode==='distance')pickPoints=[];};
let boxStart=null;
scene.c.addEventListener('pointerdown',e=>{if(busy||!view||editMode==='orbit')return;scene.c.setPointerCapture(e.pointerId);const r=scene.c.getBoundingClientRect();boxStart=[e.clientX-r.left,e.clientY-r.top];if(editMode==='select'){$('selection-box').style.display='block';Object.assign($('selection-box').style,{left:boxStart[0]+'px',top:boxStart[1]+'px',width:'0px',height:'0px'});}});
scene.c.addEventListener('pointermove',e=>{if(!boxStart||editMode!=='select')return;const r=scene.c.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;Object.assign($('selection-box').style,{left:Math.min(x,boxStart[0])+'px',top:Math.min(y,boxStart[1])+'px',width:Math.abs(x-boxStart[0])+'px',height:Math.abs(y-boxStart[1])+'px'});});
scene.c.addEventListener('pointerup',async e=>{if(!boxStart)return;const r=scene.c.getBoundingClientRect(),end=[e.clientX-r.left,e.clientY-r.top],start=boxStart;boxStart=null;$('selection-box').style.display='none';
 if(editMode==='pick'||editMode==='distance'){inspectPoint(end);return;}if(editMode!=='select'||Math.abs(end[0]-start[0])<3||Math.abs(end[1]-start[1])<3)return;
 const X=x=>x/r.width*2-1,Y=y=>1-y/r.height*2;
 selectSpec=cameraSpec([X(Math.min(start[0],end[0])),Y(Math.max(start[1],end[1])),X(Math.max(start[0],end[0])),Y(Math.min(start[1],end[1]))]);
 try{setBusy(true);refreshTools();scene.recolour();await post('/api/select',selectSpec);poll();}catch(err){selectSpec=null;setBusy(false);notice(err.message,true);refreshTools();}
});
function inspectPoint(pixel){const s=cameraSpec([-1,-1,1,1]),basis=scene.basis();let best=null,bestDistance=100;
 for(const p of view.points){if(!visiblePoint(p)||Math.abs(p[3])>+$('band').value*1000)continue;const v=p.slice(0,3).map((n,i)=>n-scene.target[i]),dot=a=>v.reduce((n,x,i)=>n+x*a[i],0);if(Math.abs(dot(basis.forward)/(scene.scale*12))>1)continue;const x=(dot(basis.right)/scene.scale+1)/2*scene.c.clientWidth,y=(1-dot(basis.up)*s.aspect/scene.scale)/2*scene.c.clientHeight,d=(x-pixel[0])**2+(y-pixel[1])**2;if(d<bestDistance){best=p;bestDistance=d;}}
 if(!best){notice('No displayed point within 10 pixels. Zoom in or increase point size.');return;}
 const xyz=best.slice(0,3).map((n,i)=>n+view.origin[i]);
 if(editMode==='distance'){pickPoints.push(xyz);if(pickPoints.length>2)pickPoints=[xyz];if(pickPoints.length===2){const d=xyz.map((n,i)=>n-pickPoints[0][i]);$('pick-result').textContent='Sampled-point measurement: 3D '+fmt(Math.hypot(...d),4)+' m · horizontal '+fmt(Math.hypot(d[0],d[1]),4)+' m · ΔH '+fmt(d[2],4)+' m. Click for a new pair.';}else $('pick-result').textContent='First displayed point selected. Click the second point.';
 }else $('pick-result').textContent='Displayed point: E '+fmt(xyz[0],4)+' · N '+fmt(xyz[1],4)+' · H '+fmt(xyz[2],4)+' m · Ch. '+fmt(best[4],3)+' m · radial '+fmt(best[3],2)+' mm. Picking uses the 3D sample; verify survey-critical distances independently.';
}
$('clear-selection').onclick=clearSelection;
$('hide-selection').onclick=()=>{if(selectSpec){if(hiddenBoxes.length>=20){notice('Show all before making more than 20 hidden regions.',true);return;}hiddenBoxes.push(bareSpec(selectSpec));clearSelection();}};
$('isolate-selection').onclick=()=>{if(selectSpec){isolatedBox=bareSpec(selectSpec);clearSelection();}};
$('show-all').onclick=()=>{hiddenBoxes=[];isolatedBox=null;clearSelection();notice('Hidden points restored to view. Chainage and wall-band filters still apply. Deleted points require Undo.');};
$('point-size').oninput=()=>scene.draw();
$('view-top').onclick=()=>{scene.yaw=0;scene.pitch=Math.PI/2;scene.draw();};$('view-side').onclick=()=>{scene.yaw=Math.PI/2;scene.pitch=0;scene.draw();};$('view-end').onclick=()=>{if(!result)return;scene.focus();const right=result.right,up=result.up,f=[right[1]*up[2]-right[2]*up[1],right[2]*up[0]-right[0]*up[2],right[0]*up[1]-right[1]*up[0]];scene.yaw=Math.atan2(-f[0],-f[1]);scene.pitch=Math.asin(-f[2]);scene.draw();};
$('clip-apply').onclick=()=>{const a=+$('clip-start').value,b=+$('clip-end').value;if($('clip-enabled').checked&&(!Number.isFinite(a)||!Number.isFinite(b)||b<a)){notice('Enter an increasing chainage range.',true);return;}clipRange=$('clip-enabled').checked?[a,b]:null;clearSelection();};
$('delete-selection').onclick=()=>{if(!selectSpec||!state.selection_count)return;$('delete-summary').textContent=fmt(state.selection_count,0)+' full-density points will be excluded, including points behind the visible surface.';$('delete-reason').value='';$('delete-dialog').showModal();};
$('cancel-delete').onclick=()=>$('delete-dialog').close();
$('confirm-delete').onclick=async()=>{const reason=$('delete-reason').value.trim();if(!reason){$('delete-reason').focus();return;}try{$('delete-dialog').close();setBusy(true);await post('/api/delete-selection',{reason});selectSpec=null;poll();}catch(e){setBusy(false);notice(e.message,true);}};
for(const [id,path]of [['undo-edit','undo-edit'],['redo-edit','redo-edit'],['export-clean','export-clean']])$(id).onclick=async()=>{try{setBusy(true);await post('/api/'+path,{});selectSpec=null;poll();}catch(e){setBusy(false);notice(e.message,true);}};
$('download-clean').onclick=()=>location.href='/api/cleaned-cloud';
let formAxis=null,lastAnalysisRevision=null;
function initReportForm(){if(!state.axis||formAxis===state.axis.name)return;formAxis=state.axis.name;const v=state.report_settings||{};for(const [id,key,def]of [['report-project','project','Tunnel survey'],['report-reference','reference',''],['report-surveyor','surveyor',''],['report-date','survey_date',''],['report-notes','notes',''],['report-start','start',state.axis.start],['report-end','end',Math.min(state.axis.end,state.axis.start+18)],['report-step','step',1.8]])$(id).value=v[key]??def;$('clip-start').value=state.axis.start;$('clip-end').value=state.axis.end;$('heat-start').value=state.axis.start;$('heat-end').value=state.axis.end;$('mesh-start').value=state.axis.start;$('mesh-end').value=Math.min(state.axis.end,state.axis.start+10);}
$('report-current').onclick=()=>{if(result){$('report-start').value=result.chainage;$('report-end').value=result.chainage;}};
$('build-report').onclick=async()=>{if(!state?.cloud){notice('Load an alignment and scan first.',true);return;}const data={project:$('report-project').value,reference:$('report-reference').value,surveyor:$('report-surveyor').value,survey_date:$('report-date').value,notes:$('report-notes').value,start:+$('report-start').value,end:+$('report-end').value,step:+$('report-step').value,width:+$('width').value,band:+$('band').value,tolerance:+$('tolerance').value,creep:+$('creep-limit').value};try{setBusy(true);refreshTools();await post('/api/build-report',data);poll();}catch(e){setBusy(false);notice(e.message,true);}};
$('open-report').onclick=async()=>{try{await post('/api/open-report',{});}catch(e){notice(e.message,true);}};
$('download-report').onclick=()=>location.href='/api/download-report';

let heatData=null,heatLimit=25;
$('build-unwrapped-heat').onclick=async()=>{if(!state?.cloud)return;try{setBusy(true);notice('Calculating full-density heatmap…');heatLimit=+$('tolerance').value;if(!(heatLimit>0))throw Error('Radial limit must be positive.');if(!$('heat-start').value)$('heat-start').value=state.axis.start;if(!$('heat-end').value)$('heat-end').value=state.axis.end;heatData=await api('/api/heatmap?'+new URLSearchParams({start:$('heat-start').value,end:$('heat-end').value,step:$('heat-step').value,degrees:$('heat-degrees').value,band:$('band').value,mode:$('heat-mode').value}));drawHeat();notice('Heatmap ready. Click a cell to inspect that chainage.');}catch(e){heatData=null;notice(e.message,true);}finally{setBusy(false);}};
function drawHeat(){const h=heatData,c=$('heat-canvas');c.width=h.nx;c.height=h.ny;const ctx=c.getContext('2d'),im=ctx.createImageData(h.nx,h.ny);for(let i=0;i<h.values.length;i++){const v=h.values[i],base=[235,245,240],end=v>=0?[213,86,44]:[42,103,196],t=Math.min(1,Math.abs(v)/heatLimit),rgb=v===null?[223,228,232]:base.map((a,j)=>Math.round(a+(end[j]-a)*t));im.data.set([...rgb,255],i*4);}ctx.putImageData(im,0,0);$('heat-from').textContent=fmt(h.start,3);$('heat-to').textContent=fmt(h.end,3);$('heat-negative').textContent='≤ −'+fmt(heatLimit,1)+' mm';$('heat-positive').textContent='≥ +'+fmt(heatLimit,1)+' mm';$('heat-info').textContent=fmt(h.points,0)+' retained points · '+h.nx+' × '+h.ny+' cells · '+h.mode+' statistic. Hover to inspect.'+(h.resolution_note?' '+h.resolution_note:'');}
function heatCell(e){if(!heatData)return null;const r=$('heat-canvas').getBoundingClientRect(),h=heatData,x=Math.max(0,Math.min(h.nx-1,Math.floor((e.clientX-r.left)/r.width*h.nx))),y=Math.max(0,Math.min(h.ny-1,Math.floor((e.clientY-r.top)/r.height*h.ny)));return {x,y,i:y*h.nx+x,station:Math.min(h.end,h.start+(x+.5)*h.step)};}
$('heat-canvas').onmousemove=e=>{const q=heatCell(e);if(!q)return;const h=heatData;$('heat-info').textContent='Ch. '+fmt(q.station,3)+' m · angle '+fmt((q.y+.5)*h.degrees,1)+'° · '+(h.values[q.i]===null?'No retained data':fmt(h.values[q.i],2)+' mm · '+fmt(h.counts[q.i],0)+' points')+' · '+h.mode+(h.resolution_note?' · '+h.resolution_note:'');};
$('heat-canvas').onclick=e=>{const q=heatCell(e);if(q){setStation(q.station);analyse();document.querySelector('[data-tab=section]').click();}};
$('heat-export').onclick=()=>{if(!heatData)return;const h=heatData,rows=['chainage_start_m,chainage_end_m,angle_start_deg,angle_end_deg,radial_deviation_mm,point_count,statistic,wall_band_m'];for(let y=0;y<h.ny;y++)for(let x=0;x<h.nx;x++){const i=y*h.nx+x;rows.push([h.start+x*h.step,Math.min(h.end,h.start+(x+1)*h.step),y*h.degrees,(y+1)*h.degrees,h.values[i]??'',h.counts[i],h.mode,h.band].join(','));}const u=URL.createObjectURL(new Blob([rows.join('\n')],{type:'text/csv'})),a=document.createElement('a');a.href=u;a.download='radial_heatmap.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),30000);};

let objectId=null;
async function loadObjects(){const data=await api('/api/structures');if(!scene.gl)return;let values=[];for(const o of data.objects)for(const edge of o.wire)for(const p of [edge.slice(0,3),edge.slice(3,6)])values.push(...p,1,.72,.22);scene.upload(scene.objectBuffer,values);scene.objectCount=values.length/6;scene.draw();}
function renderObjects(){$('object-list').innerHTML=(state?.structures||[]).map(o=>`<div class="result-row"><b>${esc(o.name)} · ${esc(o.kind)}</b><span><button data-object-edit="${o.id}">Edit</button> <button data-object-toggle="${o.id}">${o.visible?'Hide':'Show'}</button> <button data-object-delete="${o.id}">Delete structure</button></span></div>`).join('')||'<p>No reference structures yet.</p>';document.querySelectorAll('[data-object-edit]').forEach(b=>b.onclick=()=>{const o=state.structures.find(o=>o.id===b.dataset.objectEdit);objectId=o.id;if(o.kind==='scan_mesh'){for(const k of ['tx','ty','tz','rotation','scale'])$('mesh-'+k).value=o[k];$('mesh-summary').textContent=o.name+' · '+fmt(o.summary.vertices,0)+' vertices · '+fmt(o.summary.triangles,0)+' triangles · reconstructed area '+fmt(o.summary.area_m2,2)+' m² before transforms · '+o.summary.method+' Snapshot: does not refresh when scan points change.';return;}for(const k of ['name','kind','e','n','h','length','width','height','radius','yaw','start','end','step'])$('object-'+k).value=o[k];modelFields();$('object-help').textContent='Editing '+o.name+'. Change dimensions or coordinates and Save.';});for(const [attr,action]of [['toggle','visibility'],['delete','delete']])document.querySelectorAll('[data-object-'+attr+']').forEach(b=>b.onclick=async()=>{if(busy)return;if(action==='delete'&&!confirm('Delete this reference structure? This does not delete scan points.'))return;try{setBusy(true);await post('/api/structure-action',{id:b.dataset[attr==='toggle'?'objectToggle':'objectDelete'],action});poll();}catch(e){setBusy(false);notice(e.message,true);}});}
$('object-place').onclick=()=>{if(!result)return;for(const [i,k]of ['e','n','h'].entries())$('object-'+k).value=result.centre[i].toFixed(4);$('object-start').value=result.chainage;$('object-end').value=Math.min(state.axis.end,result.chainage+10);};
$('object-new').onclick=()=>{objectId=null;$('object-name').value='New reference structure';$('object-help').textContent='Creating a new reference structure. Set its position and dimensions, then Save.';};
$('object-save').onclick=async()=>{if(!state?.axis)return;if(state.structures?.find(o=>o.id===objectId)?.kind==='scan_mesh'){notice('Use Apply mesh transform for this mesh, or New object for a primitive.',true);return;}const data={id:objectId};for(const k of ['name','kind'])data[k]=$('object-'+k).value;for(const k of ['e','n','h','length','width','height','radius','yaw','start','end','step'])data[k]=+$('object-'+k).value;try{setBusy(true);await post('/api/save-structure',data);poll();}catch(e){setBusy(false);notice(e.message,true);}};
$('object-export').onclick=()=>{if(state?.structures?.length&&!busy)location.href='/api/structures-obj';};

function meshPreset(){const w=$('mesh-workflow')?.value||'regular',help=$('mesh-workflow-help');let method='mean',step=Math.max(.005,(+$('mesh-step').value||100)/1000),degrees=Math.max(1,+$('mesh-degrees').value||2),edge=Math.max(.01,+$('mesh-edge').value||.5),smoothing=Math.max(0,+$('mesh-smoothing').value||0);if(w==='rough'){method='mean';step=Math.max(step,.25);degrees=Math.max(degrees,5);edge=Math.max(edge,.9);smoothing=0;if(help)help.textContent='Rough mesh uses larger cells to create stable topology and bridge small gaps before refinement.';}else if(w==='refine'){method='nearest';step=Math.min(step,.08);degrees=Math.min(degrees,2);edge=Math.min(edge,.45);if(help)help.textContent='Refine from cloud follows measured geometry more closely with finer cells and nearest-point reconstruction.';}else if(w==='interpolate'){method='idw';step=Math.min(step,.08);degrees=Math.min(degrees,2);edge=Math.min(edge,.5);smoothing=Math.max(smoothing,1);if(help)help.textContent='Refine with interpolation uses inverse-distance weighting to reduce small holes while retaining the measured tunnel shape.';}else{method='mean';if(help)help.textContent='Regular sampling creates an even tunnel grid from cell-averaged measurements. Start here for a normal lining.';}$('mesh-method').value=method;$('mesh-power').disabled=method!=='idw';return {method,step,degrees,edge,smoothing};}
$('build-mesh').onclick=async()=>{const item=window.JobUI?.selectedItem?.();if(!item||item.kind!=='scan'){showTunnelMessage('Select a scan first','Select the source scan in the Project Explorer, then press Create mesh again.',true);return;}const engine=$('mesh-engine')?.value||'pca_tunnel';try{setBusy(true);const body={source_id:item.id,name:$('mesh-name').value||'Scan mesh',engine,selection:$('mesh-use-selection')?.checked?window.JobUI?.currentSelection?.():null,quality:$('mesh-quality').value,preview:!!window.tunnelMeshPreview,step:(+$('mesh-step').value||100)/1000,degrees:+$('mesh-degrees').value||2,max_edge:Number($('mesh-edge').value),smoothing:+$('mesh-smoothing').value||0,alpha_radius:Number($('mesh-alpha-radius').value),poisson_depth:Number($('mesh-poisson-depth').value)||8,tunnel_gap:Number($('mesh-tunnel-gap').value),ball_radius:+$('mesh-ball-radius').value||0,sampling_distance:engine==='tunnel_surface'?0:(+$('mesh-sampling-distance').value||0),noise_gap:engine==='tunnel_surface'?0:(+$('mesh-noise-gap').value||0),hole_handling:$('mesh-hole-handling').value||'preserve',hole_size:+$('mesh-hole-size').value||.5};if(engine==='alignment_grid'){if(!state?.axis){setBusy(false);showTunnelMessage('Alignment required for this method only','Alignment-grid meshing follows tunnel chainage and angle. Choose Tunnel unwrap, Planar Delaunay, Convex Hull or Ball Pivoting to mesh point-cloud geometry without an alignment.',true,'Import alignment',()=>window.TunnelRibbon?.openImport?.('axis'));return;}const p=meshPreset();body.start=Number.isFinite(+$('mesh-start').value)&&$('mesh-start').value!==''?+$('mesh-start').value:state.axis.start;body.end=Number.isFinite(+$('mesh-end').value)&&$('mesh-end').value!==''?+$('mesh-end').value:state.axis.end;Object.assign(body,{step:p.step,degrees:p.degrees,band:+$('band').value,min_points:+$('mesh-min').value,max_edge:p.edge,method:p.method,power:+$('mesh-power').value,smoothing:p.smoothing,workflow:$('mesh-workflow')?.value||'regular'});}await post('/api/scan-mesh',body);window.JobUI?.setMeshDisplay?.('solid');poll();}catch(e){setBusy(false);notice(e.message,true);}};
function meshEngineUI(){const engine=$('mesh-engine')?.value||'pca_tunnel',help=$('mesh-engine-help'),align=$('mesh-alignment-options');const notes={tunnel_surface:'Joined lining surface using a shared-vertex radial grid. All retained measurements support the grid; a bounded robust radius estimate is used per observed cell. Small-gap interpolation stays within the stated limit. One approximately straight branch; ends remain open.',alpha_shape:'3D Delaunay tetrahedra are filtered by their circumradius; exposed faces form a triangle boundary. Working detail is managed automatically. Alpha radius controls bridging.',poisson:'Screened Poisson reconstructs an implicit surface from consistently oriented normals and extracts triangle faces. Depth controls detail/memory. May fill unmeasured areas.',auto_scan:'Auto chooses planar Delaunay, periodic tunnel Delaunay or Ball Pivoting. It reports an unsuitable geometry instead of silently creating a convex envelope.',pca_tunnel:'Recommended for one tunnel branch. Estimates the tunnel axis from selected points, unwraps the cylindrical surface and triangulates occupied cells in a periodic cylindrical domain.',watertight_tunnel:'Tunnel unwrap followed by capping simple planar boundary loops. Separate branches first. Inspect remaining open edges and intended portals/cross-passages.',marching_cubes:'Marching Cubes extracts triangles from an occupancy voxel field. It may thicken a scanned surface and smooth fine detail; optional scikit-image engine required. Inspect the output against the scan.',delaunay:'2D/planar triangulation on a best-fit plane. Best for walls, slabs, terrain patches and local near-planar areas.',convex_hull:'Creates a fast closed outer envelope. It fills concavities/openings, so use it for rough envelopes, not faithful cross-passage geometry.',ball_pivoting:'Shape-preserving Ball Pivoting for reasonably uniform point density. Requires optional Open3D; radius is sensitive to density and noise.',alignment_grid:'Design-aware tunnel grid using chainage and angle around the loaded alignment. This is the only method here that requires an alignment.'};if(help)help.textContent=notes[engine]||'';if($('tunnel-surface-options'))$('tunnel-surface-options').hidden=engine!=='tunnel_surface';for(const id of ['mesh-sampling-distance','mesh-noise-gap'])$(id).disabled=engine==='tunnel_surface';if($('mesh-full-support-help'))$('mesh-full-support-help').hidden=engine!=='tunnel_surface';for(const [id,kind] of [['mesh-alpha-options','alpha_shape'],['mesh-poisson-options','poisson'],['mesh-ball-options','ball_pivoting']])if($(id))$(id).hidden=engine!==kind;if($('mesh-angular-options'))$('mesh-angular-options').hidden=!['tunnel_surface','pca_tunnel','watertight_tunnel','alignment_grid'].includes(engine);$('build-mesh').textContent=engine==='tunnel_surface'?'Create Tunnel Surface':'Create mesh';if(align)align.hidden=engine!=='alignment_grid';const caps=state?.mesh_capabilities||{};for(const o of $('mesh-engine')?.options||[]){if(caps[o.value]){o.disabled=caps[o.value].available===false;o.title=caps[o.value].note||'';}}}
$('mesh-engine').onchange=meshEngineUI;meshEngineUI();
$('mesh-quality').onchange=()=>{$('mesh-step').value=({fast:200,balanced:100,fine:50})[$('mesh-quality').value];};
$('preview-mesh').onclick=async()=>{window.tunnelMeshPreview=true;try{await $('build-mesh').onclick();}finally{window.tunnelMeshPreview=false;}};
if($('mesh-workflow'))$('mesh-workflow').onchange=meshPreset;meshPreset();
for(const b of document.querySelectorAll('[data-mesh-refine]'))b.onclick=async()=>{const item=window.JobUI?.selectedItem?.();if(!item||item.kind!=='mesh'){showTunnelMessage('Select a mesh first','Choose a mesh item in the scene list, then run the refinement again.',true);return;}try{setBusy(true);await post('/api/mesh-refine',{source_id:item.id,operation:b.dataset.meshRefine,gap_min_mm:Number($('mesh-bolt-min').value),gap_max_mm:Number($('mesh-bolt-max').value),name:$('mesh-refine-name').value||item.name+' refined',smoothing:+$('mesh-refine-smoothing').value||2,hole_size:+$('mesh-refine-hole').value||.5,decimate_cell:+$('mesh-refine-cell').value||.03,target_triangles:Number($('mesh-refine-triangles')?.value)||50000});poll();}catch(e){setBusy(false);notice(e.message,true);}};
function syncAnalysisTools(){
 window.DesignUI?.sync();window.InspectionUI?.sync();
 const scans=window.JobUI?.scanItems?.()||[],html=scans.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
 for(const id of ['analysis-source','analysis-reference','analysis-qa-source']){const el=$(id);if(!el)continue;const old=el.value;el.innerHTML=html;if(scans.some(x=>x.id===old))el.value=old;}
 const selected=window.JobUI?.selectedItem?.();if(selected?.kind==='scan'){if($('analysis-source'))$('analysis-source').value=selected.id;if($('analysis-qa-source'))$('analysis-qa-source').value=selected.id;}
 const r=state?.analysis_tool_result;if(r&&$('analysis-result'))$('analysis-result').textContent=Object.entries(r).map(([k,v])=>`${k.replaceAll('_',' ')}: ${typeof v==='number'?Number(v).toLocaleString('en-GB',{maximumFractionDigits:3}):v}`).join('\n');
}
if($('analysis-cloud-compare'))$('analysis-cloud-compare').onclick=async()=>{const a=$('analysis-source').value,b=$('analysis-reference').value;if(!a||!b||a===b){showTunnelMessage('Choose two scans','Select different source and reference scans for Cloud vs Cloud.',true);return;}try{setBusy(true);await post('/api/analysis-tool',{action:'cloud_compare',source_id:a,reference_id:b,max_points:+$('analysis-max-points').value||200000,cutoff:+$('analysis-cutoff').value||0});poll();}catch(e){setBusy(false);notice(e.message,true);}};
if($('analysis-geometry-qa'))$('analysis-geometry-qa').onclick=async()=>{const a=$('analysis-qa-source').value;if(!a){showTunnelMessage('Choose a scan','Select a scan for Geometry QA.',true);return;}try{setBusy(true);await post('/api/analysis-tool',{action:'geometry_qa',source_id:a,max_points:+$('analysis-qa-points').value||30000,k:+$('analysis-k').value||12});poll();}catch(e){setBusy(false);notice(e.message,true);}};

$('transform-mesh').onclick=async()=>{if(!objectId){notice('Select Edit next to a scan mesh first.',true);return;}const data={id:objectId};for(const k of ['tx','ty','tz','rotation','scale'])data[k]=+$('mesh-'+k).value;try{setBusy(true);await post('/api/transform-mesh',data);poll();}catch(e){setBusy(false);notice(e.message,true);}};
$('reset-mesh-transform').onclick=()=>{for(const k of ['tx','ty','tz','rotation','scale'])$('mesh-'+k).value=k==='scale'?1:0;$('transform-mesh').click();};

function modelFields(){const k=$('object-kind').value;for(const key of ['e','n','h','length','width','height','radius','yaw','start','end','step']){const enabled=k==='tunnel'?['start','end','step'].includes(key):['e','n','h'].includes(key)||(k==='box'&&['length','width','height','yaw'].includes(key))||(k==='plane'&&['length','width','yaw'].includes(key))||(k==='cylinder'&&['height','radius'].includes(key));$('object-'+key).disabled=!enabled;}}
$('object-kind').onchange=modelFields;modelFields();
$('mesh-method').onchange=()=>$('mesh-power').disabled=$('mesh-method').value!=='idw';$('mesh-method').onchange();

$('about-open').onclick=()=>$('about-dialog').showModal();$('about-close').onclick=()=>$('about-dialog').close();
$('feedback-save').onclick=async()=>{try{const info=await api('/api/support-info'),text='TUNNEL STUDIO CUSTOMER FEEDBACK\n\n'+$('feedback-note').value+'\n\nDiagnostic details (review before sharing):\n'+JSON.stringify(info,null,2),u=URL.createObjectURL(new Blob([text],{type:'text/plain'})),a=document.createElement('a');a.href=u;a.download='Tunnel_Studio_feedback_'+info.version+'.txt';a.click();setTimeout(()=>URL.revokeObjectURL(u),30000);}catch(e){notice(e.message,true);}};
$('backup-project').onclick=async()=>{if(!state?.axis){$('backup-info').textContent='Load a project first.';return;}try{setBusy(true);$('backup-info').textContent='Copying the full working project. Large scans require extra disk space.';await post('/api/backup-project',{});poll();}catch(e){setBusy(false);$('backup-info').textContent=e.message;}};

function initialiseWorkspaceLayout(){
 const root=document.documentElement,split=$('shell-splitter');
 const saved=+localStorage.getItem('tunnelstudio.leftPanelWidth');if(saved>=180&&saved<=520)root.style.setProperty('--left-panel-width',saved+'px');
 if(split){let start=null;split.addEventListener('pointerdown',e=>{start=[e.clientX,document.querySelector('.shell aside')?.getBoundingClientRect().width||244];split.setPointerCapture(e.pointerId);});split.addEventListener('pointermove',e=>{if(!start)return;const w=Math.max(180,Math.min(520,start[1]+e.clientX-start[0]));root.style.setProperty('--left-panel-width',w+'px');localStorage.setItem('tunnelstudio.leftPanelWidth',w);scene.draw();window.JobUI?.draw();});split.addEventListener('pointerup',()=>start=null);}
 const nav=document.querySelector('.tabs');if(nav){
  const savedOrder=(localStorage.getItem('tunnelstudio.tabOrder')||'').split(',').filter(Boolean),buttons=[...nav.querySelectorAll('button[data-tab]')];
  for(const key of savedOrder){const b=buttons.find(x=>x.dataset.tab===key);if(b)nav.insertBefore(b,nav.querySelector('#view-count')||null);}
  let drag=null;for(const b of nav.querySelectorAll('button[data-tab]')){b.draggable=true;b.addEventListener('dragstart',()=>{drag=b;b.classList.add('dragging');});b.addEventListener('dragend',()=>{b.classList.remove('dragging');drag=null;localStorage.setItem('tunnelstudio.tabOrder',[...nav.querySelectorAll('button[data-tab]')].map(x=>x.dataset.tab).join(','));});b.addEventListener('dragover',e=>{if(!drag||drag===b)return;e.preventDefault();const r=b.getBoundingClientRect();nav.insertBefore(drag,e.clientX<r.left+r.width/2?b:b.nextSibling);});}
 }
}
initialiseWorkspaceLayout();
poll();

if($('analysis-full-cloud-compare'))$('analysis-full-cloud-compare').onclick=()=>window.InspectionUI?.open('cloud_cloud');
