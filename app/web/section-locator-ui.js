/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const dot=(a,b)=>a.reduce((sum,x,i)=>sum+x*b[i],0);
 function pathFrame(path,position){
  const s=path.stations,p=path.points;position=Math.max(s[0],Math.min(s.at(-1),position));let i=0;while(i<s.length-2&&s[i+1]<=position)i++;
  const h=s[i+1]-s[i],t=(position-s[i])/h,delta=p[i].map((v,j)=>p[i+1][j]-v);let centre,normal;
  if(path.smooth){const a=path.slopes[i].map(v=>h*v),b=path.slopes[i+1].map(v=>h*v);centre=p[i].map((v,j)=>v+(-2*t**3+3*t*t)*delta[j]+(t**3-2*t*t+t)*a[j]+(t**3-t*t)*b[j]);normal=delta.map((v,j)=>((-6*t*t+6*t)*v+(3*t*t-4*t+1)*a[j]+(3*t*t-2*t)*b[j])/h);}
  else{centre=p[i].map((v,j)=>v+t*delta[j]);normal=delta.map(v=>v/h);}
  const length=Math.hypot(...normal);return {centre,normal:normal.map(v=>v/length)};
 }
 class SectionLocator{
  constructor(canvas,onChange){
   this.canvas=canvas;this.onChange=onChange;this.background=document.createElement('canvas');this.background.width=canvas.width;this.background.height=canvas.height;this.locked=false;this.drag=null;
   canvas.addEventListener('contextmenu',e=>e.preventDefault());
   canvas.addEventListener('wheel',e=>{if(this.locked||!this.frame||this.drag)return;e.preventDefault();this.zoom(Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.008),this.pixel(e));},{passive:false});
   canvas.addEventListener('pointerdown',e=>{if(this.locked||!this.frame||![0,1,2].includes(e.button)||e.isPrimary===false)return;e.preventDefault();canvas.focus();this.drag={pointer:e.pointerId,position:this.position,pan:e.button!==0,x:e.clientX,y:e.clientY,mid:this.mid.slice()};canvas.setPointerCapture(e.pointerId);if(!this.drag.pan)this.move(e);});
   canvas.addEventListener('pointermove',e=>{if(this.drag?.pointer!==e.pointerId)return;if(this.drag.pan){const r=canvas.getBoundingClientRect();this.mid=[this.drag.mid[0]-(e.clientX-this.drag.x)*canvas.width/r.width/this.scale,this.drag.mid[1]+(e.clientY-this.drag.y)*canvas.height/r.height/this.scale];this.backgroundDraw();this.draw();}else this.move(e);});
   canvas.addEventListener('pointerup',e=>{if(this.drag?.pointer!==e.pointerId)return;if(this.drag.pan){this.drag=null;return;}this.move(e);this.drag=null;this.onChange(this.position,true);});
   for(const event of ['pointercancel','lostpointercapture'])canvas.addEventListener(event,e=>{if(this.drag?.pointer===e.pointerId)this.cancel();});
   canvas.addEventListener('keydown',e=>{if(e.key==='Escape'&&this.cancel()){e.preventDefault();e.stopPropagation();return;}if(this.locked||!this.frame)return;if(['+','=','-','0'].includes(e.key)){e.preventDefault();e.key==='0'?this.fit():this.zoom(e.key==='-'?1/1.3:1.3);return;}let value=this.position,step=(e.shiftKey?10:1)*Number(canvas.dataset.step||.05);if(['ArrowRight','ArrowUp'].includes(e.key))value+=step;else if(['ArrowLeft','ArrowDown'].includes(e.key))value-=step;else if(e.key==='Home')value=this.limits[0];else if(e.key==='End')value=this.limits[1];else return;e.preventDefault();this.setPosition(this.clamp(value));this.onChange(this.position,true);});
   window.addEventListener('blur',()=>this.cancel());this.clear('Loading scan overview…');
  }
  clear(text='Loading scan overview…'){this.frame=null;this.key=null;this.drag=null;const ctx=this.canvas.getContext('2d');ctx.clearRect(0,0,this.canvas.width,this.canvas.height);ctx.fillStyle='#10252d';ctx.fillRect(0,0,this.canvas.width,this.canvas.height);ctx.fillStyle='#d2e7ed';ctx.font='16px sans-serif';ctx.fillText(text,24,44);this.canvas.setAttribute('aria-disabled','true');}
  setData(data){this.data=data;this.frame=null;this.key=null;}
  configure(origin,normal,position,width,path=null){
   if(!this.data?.points?.length)return null;
   this.path=path;this.cut=path?pathFrame(path,position):null;if(this.cut)normal=this.cut.normal;
   const length=Math.hypot(...normal);if(!Number.isFinite(length)||length<1e-8){this.clear('Enter a valid cutting-plane normal.');return null;}
   const n=normal.map(v=>v/length),axes=Math.hypot(n[0],n[1])<.2?[0,2]:[0,1];
   const key=JSON.stringify([origin,path||n,axes]);this.frame={origin:[...origin],normal:n,axes};this.position=position;this.width=width;
   if(key!==this.key){this.key=key;this.prepare();}this.draw();return {limits:this.limits,view:axes[1]===1?'Top view · E / N':'Side view · E / H',normal:n};
  }
  prepare(){
   const {origin,normal,axes}=this.frame;const low=[Infinity,Infinity],high=[-Infinity,-Infinity];this.limits=[Infinity,-Infinity];
   this.points=this.data.points.map(p=>{const d=[0,1,2].map(i=>p[i]+this.data.origin[i]-origin[i]),value=dot(d,normal),xy=axes.map(i=>d[i]);this.limits=[Math.min(this.limits[0],value),Math.max(this.limits[1],value)];xy.forEach((v,i)=>{low[i]=Math.min(low[i],v);high[i]=Math.max(high[i],v);});return [...xy,...p.slice(3,6)];});
   this.pathSamples=[];if(this.path){this.limits=[this.path.stations[0],this.path.stations.at(-1)];for(let i=0;i<this.path.stations.length-1;i++)for(let j=0;j<12;j++){const s=this.path.stations[i]+(this.path.stations[i+1]-this.path.stations[i])*j/12;this.pathSamples.push([s,...axes.map(k=>pathFrame(this.path,s).centre[k]-origin[k])]);}this.pathSamples.push([this.path.stations.at(-1),...axes.map(k=>this.path.points.at(-1)[k]-origin[k])]);}
   this.mid=low.map((v,i)=>(v+high[i])/2);this.scale=Math.min((this.canvas.width-80)/Math.max(.1,high[0]-low[0]),(this.canvas.height-90)/Math.max(.1,high[1]-low[1]));
   this.fitMid=this.mid.slice();this.fitScale=this.scale;this.backgroundDraw();
  }
  backgroundDraw(){
   const {axes}=this.frame;
   const ctx=this.background.getContext('2d');ctx.fillStyle='#10252d';ctx.fillRect(0,0,this.canvas.width,this.canvas.height);
   for(const p of this.points){const xy=this.project(p);if(xy[0]<-2||xy[0]>this.canvas.width+2||xy[1]<-2||xy[1]>this.canvas.height+2)continue;ctx.fillStyle=`rgb(${p.slice(2,5).map(v=>Math.round(Math.max(0,Math.min(1,v))*255)).join(',')})`;ctx.fillRect(xy[0]-1,xy[1]-1,2,2);}
   ctx.fillStyle='#b4ccd3';ctx.font='14px sans-serif';ctx.fillText(axes[1]===1?'N ↑     E →':'H ↑     E →',20,this.canvas.height-18);
   this.canvas.setAttribute('aria-disabled',this.locked?'true':'false');
  }
  pixel(e){const r=this.canvas.getBoundingClientRect();return [(e.clientX-r.left)*this.canvas.width/r.width,(e.clientY-r.top)*this.canvas.height/r.height];}
  zoom(factor,anchor=[this.canvas.width/2,this.canvas.height/2]){if(this.locked||!this.frame||this.drag)return;const old=this.scale,next=Math.max(this.fitScale*.25,Math.min(this.fitScale*200,old*factor));this.mid[0]+=(anchor[0]-this.canvas.width/2)*(1/old-1/next);this.mid[1]-=(anchor[1]-this.canvas.height/2)*(1/old-1/next);this.scale=next;this.backgroundDraw();this.draw();}
  fit(){if(this.locked||!this.frame||this.drag)return;this.mid=this.fitMid.slice();this.scale=this.fitScale;this.backgroundDraw();this.draw();}
  project(p){return [this.canvas.width/2+(p[0]-this.mid[0])*this.scale,this.canvas.height/2-(p[1]-this.mid[1])*this.scale];}
  clamp(value){return Math.max(this.limits[0],Math.min(this.limits[1],value));}
  setPosition(position,width=this.width){this.position=position;this.width=width;if(this.path){this.cut=pathFrame(this.path,position);this.frame.normal=this.cut.normal;}this.draw();}
  setBusy(value){this.locked=value;this.canvas.setAttribute('aria-disabled',value||!this.frame?'true':'false');}
  move(e){
   const r=this.canvas.getBoundingClientRect(),x=(e.clientX-r.left)*this.canvas.width/r.width,y=(e.clientY-r.top)*this.canvas.height/r.height;
   const p=[this.mid[0]+(x-this.canvas.width/2)/this.scale,this.mid[1]-(y-this.canvas.height/2)/this.scale],n=this.frame.axes.map(i=>this.frame.normal[i]);
   let position=dot(p,n)/dot(n,n);
   if(this.pathSamples.length){let best=Infinity;for(let i=0;i<this.pathSamples.length-1;i++){const a=this.pathSamples[i],b=this.pathSamples[i+1],d=[b[1]-a[1],b[2]-a[2]],len=dot(d,d);if(len<1e-12)continue;const t=Math.max(0,Math.min(1,dot([p[0]-a[1],p[1]-a[2]],d)/len)),dist=Math.hypot(p[0]-a[1]-t*d[0],p[1]-a[2]-t*d[1]);if(dist<best){best=dist;position=a[0]+t*(b[0]-a[0]);}}}
   this.setPosition(this.clamp(position));this.onChange(this.position,false);
  }
  cancel(){if(!this.drag)return false;if(this.drag.pan){this.mid=this.drag.mid;this.drag=null;this.backgroundDraw();this.draw();return true;}const old=this.drag.position;this.drag=null;this.setPosition(old);this.onChange(old,false,true);return true;}
  draw(){
   if(!this.frame||!Number.isFinite(this.position))return;
   const ctx=this.canvas.getContext('2d'),n=this.frame.axes.map(i=>this.frame.normal[i]),length=Math.hypot(...n),centre=this.project(this.cut?this.frame.axes.map(i=>this.cut.centre[i]-this.frame.origin[i]):n.map(v=>v*this.position)),tangent=[-n[1]/length,-n[0]/length],extent=Math.hypot(this.canvas.width,this.canvas.height)*2;
   const line=()=>{ctx.beginPath();ctx.moveTo(centre[0]-tangent[0]*extent,centre[1]-tangent[1]*extent);ctx.lineTo(centre[0]+tangent[0]*extent,centre[1]+tangent[1]*extent);ctx.stroke();};
   ctx.drawImage(this.background,0,0);ctx.save();ctx.beginPath();ctx.rect(0,0,this.canvas.width,this.canvas.height);ctx.clip();if(this.pathSamples.length){ctx.beginPath();this.pathSamples.forEach((p,i)=>{const q=this.project(p.slice(1));if(i)ctx.lineTo(...q);else ctx.moveTo(...q);});ctx.strokeStyle='#55d7ce';ctx.lineWidth=2;ctx.stroke();}ctx.strokeStyle='#ffd75b33';ctx.lineWidth=Math.max(3,this.width*this.scale/length);line();ctx.strokeStyle='#ffe376';ctx.lineWidth=2;line();ctx.restore();
   // A large handle follows the cut at the centre of the visible scan span.
   const mid=this.project(this.mid),screenNormal=[n[0]/length,-n[1]/length],offset=dot([centre[0]-mid[0],centre[1]-mid[1]],screenNormal),handle=mid.map((v,i)=>v+offset*screenNormal[i]);
   ctx.beginPath();ctx.arc(handle[0],handle[1],9,0,Math.PI*2);ctx.fillStyle='#ffe376';ctx.fill();ctx.strokeStyle='#10252d';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#ffe376';ctx.font='15px sans-serif';ctx.fillText(`Slice ${this.position.toFixed(3)} m · drag line or click scan`,20,25);
   this.canvas.dataset.zoom=String(this.scale/this.fitScale);this.canvas.dataset.viewMid=JSON.stringify(this.mid);
   this.canvas.dataset.position=String(this.position);this.canvas.dataset.handleX=String(handle[0]);this.canvas.dataset.handleY=String(handle[1]);this.canvas.setAttribute('aria-valuemin',String(this.limits[0]));this.canvas.setAttribute('aria-valuemax',String(this.limits[1]));this.canvas.setAttribute('aria-valuenow',String(this.position));this.canvas.setAttribute('aria-valuetext',`Slice ${this.position.toFixed(3)} metres`);
  }
 }
 function movable(dialog,heading,resetButton){
  let drag=null,current=null;const placements=new Map(),fields=['left','top','right','bottom','margin','width','height'];
  function keepVisible(){if(!dialog.open||!dialog.style.left)return;const r=dialog.getBoundingClientRect();dialog.style.left=Math.max(8,Math.min(innerWidth-r.width-8,r.left))+'px';dialog.style.top=Math.max(8,Math.min(innerHeight-r.height-8,r.top))+'px';}
  function show(panel){if(current)placements.set(current,Object.fromEntries(fields.map(k=>[k,dialog.style[k]])));current=panel;for(const k of fields)dialog.style[k]='';const saved=placements.get(panel);if(saved)Object.assign(dialog.style,saved);keepVisible();}
  function reset(){for(const k of fields)dialog.style[k]='';placements.delete(current);}
  heading.addEventListener('pointerdown',e=>{if(e.button!==0||e.isPrimary===false||e.target.closest('button,input,select'))return;e.preventDefault();const r=dialog.getBoundingClientRect();drag={pointer:e.pointerId,x:e.clientX,y:e.clientY,left:r.left,top:r.top};dialog.style.margin='0';dialog.style.right='auto';dialog.style.bottom='auto';dialog.style.left=r.left+'px';dialog.style.top=r.top+'px';heading.setPointerCapture(e.pointerId);});
  heading.addEventListener('pointermove',e=>{if(drag?.pointer!==e.pointerId)return;dialog.style.left=drag.left+e.clientX-drag.x+'px';dialog.style.top=drag.top+e.clientY-drag.y+'px';keepVisible();});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])heading.addEventListener(event,()=>{drag=null;});
  window.addEventListener('resize',keepVisible);window.addEventListener('blur',()=>{drag=null;});resetButton.onclick=reset;return {show,reset};
 }
 window.SectionLocator=SectionLocator;window.sectionPathFrame=pathFrame;window.movableScanWindow=movable;
})();
