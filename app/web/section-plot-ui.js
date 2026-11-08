/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 class SectionPlot{
  constructor(canvas,label){
   this.canvas=canvas;this.label=label;this.points=[];this.outline=[];this.centre=[0,0];this.scale=1;this.fitScale=1;this.drag=null;
   canvas.addEventListener('wheel',e=>{if(!this.points.length||this.drag)return;e.preventDefault();const p=this.pixel(e);this.zoom(Math.exp(-Math.max(-400,Math.min(400,e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?400:1)))*.002),p);},{passive:false});
   canvas.addEventListener('pointerdown',e=>{if(!this.points.length||e.button!==0||e.isPrimary===false)return;e.preventDefault();canvas.focus();this.drag={pointer:e.pointerId,pixel:this.pixel(e),centre:this.centre.slice()};canvas.setPointerCapture(e.pointerId);canvas.classList.add('panning');});
   canvas.addEventListener('pointermove',e=>{if(this.drag?.pointer!==e.pointerId)return;const p=this.pixel(e),d=this.drag;this.centre=[d.centre[0]-(p[0]-d.pixel[0])/this.scale,d.centre[1]+(p[1]-d.pixel[1])/this.scale];this.draw();});
   canvas.addEventListener('pointerup',e=>{if(this.drag?.pointer===e.pointerId){this.drag=null;canvas.classList.remove('panning');}});
   for(const type of ['pointercancel','lostpointercapture'])canvas.addEventListener(type,e=>{if(this.drag?.pointer===e.pointerId)this.cancel();});
   canvas.addEventListener('keydown',e=>{if(e.key==='Escape'&&this.cancel()){e.preventDefault();e.stopPropagation();return;}if(!this.points.length)return;if(['+','='].includes(e.key)){e.preventDefault();this.zoom(1.3);}else if(e.key==='-'){e.preventDefault();this.zoom(1/1.3);}else if(e.key==='0'||e.key==='Home'){e.preventDefault();this.fit();}else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const step=40/this.scale;this.centre[0]+=(e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0)*step;this.centre[1]+=(e.key==='ArrowUp'?1:e.key==='ArrowDown'?-1:0)*step;this.draw();}});
   window.addEventListener('blur',()=>this.cancel());
  }
  pixel(e){const r=this.canvas.getBoundingClientRect();return [(e.clientX-r.left)*this.canvas.width/r.width,(e.clientY-r.top)*this.canvas.height/r.height];}
  cancel(){if(!this.drag)return false;this.centre=this.drag.centre;this.drag=null;this.canvas.classList.remove('panning');this.draw();return true;}
  clear(){this.cancel();this.points=[];this.outline=[];this.key=null;this.drag=null;this.canvas.getContext('2d').clearRect(0,0,this.canvas.width,this.canvas.height);this.label.textContent='Wheel: zoom · drag: pan';}
  setSection(section){
   const key=JSON.stringify([section.source_id,section.normal,section.origin]);const refit=this.key!==key||!this.points.length;this.key=key;this.points=section.points||[];this.outline=section.shape?.outline||[];
   this.low=[Infinity,Infinity];this.high=[-Infinity,-Infinity];for(const p of this.points)for(let i=0;i<2;i++){this.low[i]=Math.min(this.low[i],p[i+3]);this.high[i]=Math.max(this.high[i],p[i+3]);}
   if(!this.points.length){this.clear();return;}
   this.fitScale=Math.min((this.canvas.width-100)/Math.max(.01,this.high[0]-this.low[0]),(this.canvas.height-100)/Math.max(.01,this.high[1]-this.low[1]));
   if(refit)this.fit();else this.draw();
  }
  fit(){if(!this.points.length)return;this.scale=this.fitScale;this.centre=this.low.map((v,i)=>(v+this.high[i])/2);this.draw();}
  zoom(factor,pixel=[this.canvas.width/2,this.canvas.height/2]){
   if(!this.points.length)return;const x=pixel[0]-this.canvas.width/2,y=pixel[1]-this.canvas.height/2,world=[this.centre[0]+x/this.scale,this.centre[1]-y/this.scale];
   this.scale=Math.max(this.fitScale/50,Math.min(this.fitScale*1000,this.scale*factor));this.centre=[world[0]-x/this.scale,world[1]+y/this.scale];this.draw();
  }
  draw(){
   if(!this.points.length)return;const ctx=this.canvas.getContext('2d'),w=this.canvas.width,h=this.canvas.height;ctx.fillStyle='#10252d';ctx.fillRect(0,0,w,h);ctx.fillStyle='#72dcca';
   for(const p of this.points){const x=w/2+(p[3]-this.centre[0])*this.scale,y=h/2-(p[4]-this.centre[1])*this.scale;if(x>=0&&x<=w&&y>=0&&y<=h)ctx.fillRect(x-1,y-1,2,2);}
   if(this.outline.length){ctx.strokeStyle='#ffcf70';ctx.lineWidth=2;ctx.beginPath();this.outline.forEach((p,i)=>{const x=w/2+(p[0]-this.centre[0])*this.scale,y=h/2-(p[1]-this.centre[1])*this.scale;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
   const raw=110/this.scale,pow=10**Math.floor(Math.log10(raw)),unit=raw/pow,step=(unit>=5?5:unit>=2?2:1)*pow,length=step*this.scale;
   ctx.fillStyle='#e0edf0';ctx.font='14px sans-serif';ctx.fillText('Section U / V · equal scale',20,25);ctx.fillRect(24,h-30,length,2);ctx.fillRect(24,h-34,2,8);ctx.fillRect(24+length,h-34,2,8);ctx.fillText(step<1?`${(step*1000).toPrecision(3)*1} mm`:`${step.toPrecision(3)*1} m`,24,h-42);
   this.label.textContent=`${Math.round(this.scale/this.fitScale*100)}% · Wheel: zoom · drag: pan · 0: fit`;
   this.canvas.dataset.outlinePoints=String(this.outline.length);this.canvas.dataset.viewScale=String(this.scale);this.canvas.dataset.viewCentre=JSON.stringify(this.centre);
  }
 }
 window.SectionPlot=SectionPlot;
})();
