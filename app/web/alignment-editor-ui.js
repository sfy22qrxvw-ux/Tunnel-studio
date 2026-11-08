/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 class AlignmentEditor{
  constructor(plan,elevation,label,onChange){
   this.views=[plan,elevation].map((canvas,i)=>({canvas,i,centre:[0,0],scale:1}));this.label=label;this.onChange=onChange;this.rows=[];this.cloud=[];this.selected=0;this.drag=null;this.busy=false;
   for(const v of this.views){const c=v.canvas;
    c.addEventListener('pointerdown',e=>{if(this.busy||!this.rows.length||e.button!==0||e.isPrimary===false)return;e.preventDefault();c.focus();const p=this.pixel(v,e),rect=c.getBoundingClientRect(),hit=this.rows.map((r,i)=>({i,d:Math.hypot(...this.project(v,r).map((x,k)=>x-p[k]))})).sort((a,b)=>a.d-b.d)[0];const node=hit.d<=12*c.width/rect.width&&!e.shiftKey?hit.i:null;if(node!==null)this.selected=node;this.drag={v,pointer:e.pointerId,pixel:p,node,rows:this.rows.map(r=>r.slice()),centre:v.centre.slice()};c.setPointerCapture(e.pointerId);this.draw();});
    c.addEventListener('pointermove',e=>{const d=this.drag;if(!d||d.v!==v||d.pointer!==e.pointerId)return;const p=this.pixel(v,e),dx=(p[0]-d.pixel[0])/v.scale,dy=-(p[1]-d.pixel[1])/v.scale;if(d.node===null)v.centre=[d.centre[0]-dx,d.centre[1]-dy];else{this.rows=d.rows.map(r=>r.slice());const r=this.rows[d.node];if(v.i===0){r[1]+=dx;r[2]+=dy;}else r[3]+=dy;this.rechain();this.changed();}this.draw();});
    c.addEventListener('pointerup',e=>{if(this.drag?.pointer===e.pointerId&&this.drag.v===v){const d=this.drag;this.drag=null;if(d.node!==null)this.changed('commit',d.rows);this.draw();}});
    for(const type of ['pointercancel','lostpointercapture'])c.addEventListener(type,e=>{if(this.drag?.pointer===e.pointerId&&this.drag.v===v)this.cancel();});
    c.addEventListener('wheel',e=>{if(this.busy||!this.rows.length||this.drag)return;e.preventDefault();const p=this.pixel(v,e),world=this.unproject(v,p),factor=Math.exp(-Math.max(-400,Math.min(400,e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?400:1)))*.002);v.scale=Math.max(v.fitScale/50,Math.min(v.fitScale*1000,v.scale*factor));v.centre=[world[0]-(p[0]-c.width/2)/v.scale,world[1]+(p[1]-c.height/2)/v.scale];this.draw();},{passive:false});
    c.addEventListener('keydown',e=>{if(e.key==='Escape'&&this.cancel()){e.preventDefault();e.stopPropagation();return;}if(this.busy||!this.rows.length)return;if(e.key==='0'||e.key==='Home'){e.preventDefault();this.fit();}else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const before=this.rows.map(r=>r.slice()),step=e.shiftKey ? .1 : .01,r=this.rows[this.selected];if(v.i===0){r[1]+=(e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0)*step;r[2]+=(e.key==='ArrowUp'?1:e.key==='ArrowDown'?-1:0)*step;}else r[3]+=(e.key==='ArrowUp'?1:e.key==='ArrowDown'?-1:0)*step;this.rechain();this.changed('commit',before);this.draw();}});
   }
   window.addEventListener('blur',()=>this.cancel());
  }
  setBusy(value){this.busy=value;if(value)this.cancel();}
  setData(rows,preview,refit=true){this.cancel();this.rows=rows.map(r=>r.slice());if(preview)this.backgroundKey=null;if(preview)this.cloud=(preview.points||[]).map(p=>p.map((x,i)=>x+preview.origin[i]));this.selected=Math.min(this.selected,rows.length-1);if(refit)this.fit();else this.draw();}
  rechain(){let ch=this.rows[0][0];for(let i=1;i<this.rows.length;i++){ch+=Math.hypot(...this.rows[i].slice(1,4).map((x,j)=>x-this.rows[i-1][j+1]));this.rows[i][0]=ch;}}
  changed(phase='move',before=null){this.onChange(this.rows.map(r=>r.slice()),{phase,before});}
  cancel(){if(!this.drag)return false;const d=this.drag;this.drag=null;d.v.centre=d.centre;if(d.node!==null){this.rows=d.rows;this.changed('cancel');}this.draw();return true;}
  pixel(v,e){const r=v.canvas.getBoundingClientRect();return [(e.clientX-r.left)*v.canvas.width/r.width,(e.clientY-r.top)*v.canvas.height/r.height];}
  coordinate(v,r){return v.i===0?[r[1],r[2]]:[r[0],r[3]];}
  project(v,r){const p=this.coordinate(v,r);return [v.canvas.width/2+(p[0]-v.centre[0])*v.scale,v.canvas.height/2-(p[1]-v.centre[1])*v.scale];}
  unproject(v,p){return [v.centre[0]+(p[0]-v.canvas.width/2)/v.scale,v.centre[1]-(p[1]-v.canvas.height/2)/v.scale];}
  background(){
   const key=JSON.stringify(this.rows);if(this.backgroundKey===key)return this.backgroundRows;
   this.backgroundKey=key;this.backgroundRows=this.cloud.map(p=>{let distance=Infinity,chainage=this.rows[0][0];for(let i=1;i<this.rows.length;i++){const a=this.rows[i-1],b=this.rows[i],dx=b[1]-a[1],dy=b[2]-a[2],dz=b[3]-a[3],qx=p[0]-a[1],qy=p[1]-a[2],qz=p[2]-a[3],length=dx*dx+dy*dy+dz*dz,t=Math.max(0,Math.min(1,(qx*dx+qy*dy+qz*dz)/Math.max(1e-20,length))),dist=(qx-t*dx)**2+(qy-t*dy)**2+(qz-t*dz)**2;if(dist<distance){distance=dist;chainage=a[0]+t*(b[0]-a[0]);}}return [chainage,...p];});return this.backgroundRows;
  }
  fit(){if(!this.rows.length)return;const background=this.background();for(const v of this.views){const lo=[Infinity,Infinity],hi=[-Infinity,-Infinity];for(const r of [...this.rows,...background])this.coordinate(v,r).forEach((x,i)=>{lo[i]=Math.min(lo[i],x);hi[i]=Math.max(hi[i],x);});v.centre=lo.map((x,i)=>(x+hi[i])/2);v.scale=v.fitScale=Math.min((v.canvas.width-90)/Math.max(.5,hi[0]-lo[0]),(v.canvas.height-90)/Math.max(.5,hi[1]-lo[1]));}this.draw();}
  draw(){if(!this.rows.length)return;const background=this.background();for(const v of this.views){const c=v.canvas,ctx=c.getContext('2d');ctx.fillStyle='#10252d';ctx.fillRect(0,0,c.width,c.height);ctx.fillStyle='#647e87';for(const r of background){const [x,y]=this.project(v,r);if(x>=0&&x<=c.width&&y>=0&&y<=c.height)ctx.fillRect(x,y,1.5,1.5);}ctx.strokeStyle='#f198d5';ctx.lineWidth=2;ctx.beginPath();this.rows.forEach((r,i)=>{const p=this.project(v,r);if(i)ctx.lineTo(...p);else ctx.moveTo(...p);});ctx.stroke();const positions=[];this.rows.forEach((r,i)=>{const p=this.project(v,r);positions.push(p);ctx.fillStyle=i===this.selected?'#ffd479':'#f8d6f0';ctx.beginPath();ctx.arc(...p,i===this.selected?6:4,0,Math.PI*2);ctx.fill();});ctx.fillStyle='#e0edf0';ctx.font='14px sans-serif';ctx.fillText(v.i===0?'Easting → / Northing ↑':'Chainage → / Height ↑ · drag vertically',15,23);c.dataset.nodePositions=JSON.stringify(positions);c.dataset.viewScale=String(v.scale);}
   const r=this.rows[this.selected];this.label.textContent=`Node ${this.selected+1} · E ${r[1].toFixed(3)} · N ${r[2].toFixed(3)} · H ${r[3].toFixed(3)} m. Drag a node; drag background to pan; wheel to zoom. Arrows: 0.01 m, Shift: 0.10 m. Escape cancels a drag.`;
  }
 }
 window.AlignmentEditor=AlignmentEditor;
})();
