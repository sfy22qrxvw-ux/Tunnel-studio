/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const dot=(a,b)=>a.reduce((n,v,i)=>n+v*b[i],0),add=(a,b)=>a.map((v,i)=>v+b[i]),sub=(a,b)=>a.map((v,i)=>v-b[i]),mul=(a,k)=>a.map(v=>v*k);
 const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],unit=a=>mul(a,1/Math.hypot(...a));
 const copy=x=>JSON.parse(JSON.stringify(x)),signs=i=>[0,1,2].map(j=>i&(1<<j)?1:-1),minimumSize=.001;
 function normalise(value){
  if(!value)return null;
  const box=value.mode==='oriented_box'?copy(value):{mode:'oriented_box',centre:value.minimum.map((v,i)=>(v+value.maximum[i])/2),half_size:value.minimum.map((v,i)=>(value.maximum[i]-v)/2),axes:[[1,0,0],[0,1,0],[0,0,1]]};
  if(box.centre?.length!==3||box.half_size?.length!==3||box.axes?.length!==3||box.axes.some(a=>a.length!==3)||![...box.centre,...box.half_size,...box.axes.flat()].every(Number.isFinite)||box.half_size.some(v=>v<=0))throw Error('Enter a valid limit box with positive dimensions.');
  if(box.axes.some((a,i)=>box.axes.some((b,j)=>Math.abs(dot(a,b)-(i===j?1:0))>1e-6))||dot(cross(box.axes[0],box.axes[1]),box.axes[2])<.999999)throw Error('The box orientation is invalid.');
  return {mode:'oriented_box',centre:box.centre,axes:box.axes,half_size:box.half_size};
 }
 function at(box,local){return box.axes.reduce((p,a,j)=>add(p,mul(a,local[j])),box.centre.slice());}
 function corners(value){const b=normalise(value);return Array.from({length:8},(_,i)=>at(b,signs(i).map((s,j)=>s*b.half_size[j])));}
 function contains(point,value){if(value.mode!=='oriented_box')return point.every((v,i)=>v>=value.minimum[i]&&v<=value.maximum[i]);const d=sub(point,value.centre);return value.axes.every((a,i)=>Math.abs(dot(d,a))<=value.half_size[i]+1e-8);}
 function face(value,axis,side,distance){const b=normalise(value),change=Math.max(minimumSize-2*b.half_size[axis],side*distance);b.half_size[axis]+=change/2;b.centre=add(b.centre,mul(b.axes[axis],side*change/2));return b;}
 function corner(value,index,delta){const b=normalise(value),s=signs(index),opposite=at(b,s.map((v,j)=>-v*b.half_size[j]));b.half_size=b.half_size.map((v,j)=>Math.max(minimumSize,2*v+s[j]*dot(delta,b.axes[j]))/2);b.centre=b.axes.reduce((p,a,j)=>add(p,mul(a,s[j]*b.half_size[j])),opposite);return b;}
 function rotate(value,axis,angle){const b=normalise(value),a=b.axes[axis],c=Math.cos(angle),s=Math.sin(angle);b.axes=b.axes.map(v=>add(add(mul(v,c),mul(cross(a,v),s)),mul(a,dot(a,v)*(1-c))));b.axes[0]=unit(b.axes[0]);b.axes[1]=unit(sub(b.axes[1],mul(b.axes[0],dot(b.axes[1],b.axes[0]))));b.axes[2]=unit(cross(b.axes[0],b.axes[1]));return b;}
 window.TunnelBox={normalise,corners,contains,face,corner,rotate};
 let editing=false,change=null,drag=null,view=null,box=null,svg=null,tapStart=null,lastTap=null;
 function openBox(event){
  const hit=event.target.closest('[data-limit-kind],[data-limit-open]');if(!hit||busy)return false;
  const id=hit.dataset.limitOpen;
  if(id&&window.JobUI?.selectedItem?.()?.id!==id)window.JobUI.selectScan(id);
  event.preventDefault();event.stopPropagation();window.ScanTools?.openLimit?.();return true;
 }
 const notify=(b,phase,detail='')=>change?.(copy(b),{phase,detail});
 function current(){return window.JobUI?.limitDraft?.()||box;}
 function begin(value,callback){cancel();box=normalise(value);change=callback;editing=true;window.JobUI.setMode('orbit');window.JobUI.setLimitPreview(box);}
 function end(){cancel();editing=false;change=null;window.JobUI?.setLimitPreview(null);}
 function cancel(){if(!drag)return false;const old=drag.start;drag=null;box=old;window.JobUI?.setLimitPreview(old);notify(old,'cancel','Move cancelled');return true;}
 function projectVector(vector){const k=view.width/(2*view.scale);return [dot(vector,view.right)*k,-dot(vector,view.up)*k];}
 function planeVector(x,y,centre,axis){
  const localX=(2*x/view.width-1)*view.scale,localY=(1-2*y/view.height)*view.scale/(view.width/view.height);
  const onView=add(view.target,add(mul(view.right,localX),mul(view.up,localY))),den=dot(view.forward,axis);
  if(Math.abs(den)<.15)return null;
  return sub(add(onView,mul(view.forward,dot(sub(centre,onView),axis)/den)),centre);
 }
 function update(e){
  if(!drag||drag.pointer!==e.pointerId)return;
  const d=drag,dx=e.clientX-d.x,dy=e.clientY-d.y,worldPerPixel=2*view.scale/view.width;
  if(Math.hypot(dx,dy)>3)d.moved=true;if(!d.moved)return;
  let next,detail;
  if(d.kind==='face'){
   const v=projectVector(d.start.axes[d.axis]),n=dot(v,v),distance=n>Math.pow(view.width/(2*view.scale)*.12,2)?(dx*v[0]+dy*v[1])/n:-dy*worldPerPixel;
   next=face(d.start,d.axis,d.side,distance);
   detail=`Face ${'XYZ'[d.axis]}${d.side>0?'+':'−'} · size ${(2*next.half_size[d.axis]).toFixed(3)} m`;
  }else if(d.kind==='corner'){
   next=corner(d.start,d.index,add(mul(view.right,dx*worldPerPixel),mul(view.up,-dy*worldPerPixel)));detail=`Corner ${d.index+1} · ${next.half_size.map(v=>(2*v).toFixed(3)).join(' × ')} m`;
  }else{
   const r=svg.getBoundingClientRect(),v=planeVector(e.clientX-r.left,e.clientY-r.top,d.start.centre,d.start.axes[d.axis]);let angle;
   if(v&&d.plane&&Math.hypot(...v)>1e-9&&Math.hypot(...d.plane)>1e-9)angle=Math.atan2(dot(d.start.axes[d.axis],cross(d.plane,v)),dot(d.plane,v));
   else{const tangent=projectVector(cross(d.start.axes[d.axis],sub(d.handle,d.start.centre))),length=Math.hypot(...tangent);angle=length>1e-6?(dx*tangent[0]+dy*tangent[1])/length*.01:dx*.01;}
   if(e.shiftKey)angle=Math.round(angle/(Math.PI/12))*Math.PI/12;
   next=rotate(d.start,d.axis,angle);detail=`Rotate around box ${'XYZ'[d.axis]} · ${(angle*180/Math.PI).toFixed(1)}°`;
  }
  box=next;window.JobUI.setLimitPreview(next);notify(next,'move',detail);
 }
 function bind(element){
  if(svg===element)return;svg=element;
  svg.addEventListener('dblclick',e=>{if(!editing)openBox(e);});
  svg.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)&&!editing)openBox(e);});
  svg.addEventListener('pointerdown',e=>{
   if(!editing&&!busy&&e.isPrimary!==false&&e.pointerType==='touch'&&e.target.closest('[data-limit-kind],[data-limit-open]')){
    tapStart={x:e.clientX,y:e.clientY,id:e.pointerId,owner:e.target.closest('[data-limit-open]')?.dataset.limitOpen||window.JobUI?.selectedItem?.()?.id};
   }
   const h=e.target.closest('[data-limit-kind]');if(!h||!editing||busy||e.button!==0||e.isPrimary===false)return;
   e.preventDefault();e.stopPropagation();const start=normalise(current()),axis=+h.dataset.limitAxis||0,r=svg.getBoundingClientRect();
   drag={kind:h.dataset.limitKind,axis,side:+h.dataset.limitSide,index:+h.dataset.limitIndex,pointer:e.pointerId,x:e.clientX,y:e.clientY,start,moved:false,handle:JSON.parse(h.dataset.limitWorld),plane:planeVector(e.clientX-r.left,e.clientY-r.top,start.centre,start.axes[axis])};
   svg.setPointerCapture(e.pointerId);window.JobUI.setMode('orbit');
  });
  svg.addEventListener('pointermove',e=>{if(tapStart&&Math.hypot(e.clientX-tapStart.x,e.clientY-tapStart.y)>8){tapStart=null;lastTap=null;}update(e);});
  svg.addEventListener('pointerup',e=>{
   if(!tapStart||e.pointerId!==tapStart.id)return;const tap=tapStart;tapStart=null;
   if(Math.hypot(e.clientX-tap.x,e.clientY-tap.y)>8){lastTap=null;return;}
   const now=Date.now();
   if(lastTap&&now-lastTap.time<450&&tap.owner===lastTap.owner&&Math.hypot(tap.x-lastTap.x,tap.y-lastTap.y)<24){lastTap=null;openBox(e);}
   else lastTap={...tap,time:now};
  });
  svg.addEventListener('pointerup',e=>{if(!drag||drag.pointer!==e.pointerId)return;update(e);const moved=drag.moved;drag=null;if(moved)notify(box,'commit','Box updated');});
  for(const type of ['pointercancel','lostpointercapture'])svg.addEventListener(type,e=>{tapStart=null;lastTap=null;if(drag?.pointer===e.pointerId)cancel();});
  window.addEventListener('blur',cancel);
 }
 function render(element,value,camera,project){
  bind(element);view=camera;box=value?normalise(value):null;const old=element.querySelector('.limit-handles');old?.remove();
  if(!box)return;
  const group=document.createElementNS('http://www.w3.org/2000/svg','g');group.classList.add('limit-handles');const points=corners(box),handles=[];
  for(let i=0;i<8;i++)handles.push({kind:'corner',index:i,world:points[i],title:editing?`Corner ${i+1}: drag to resize; opposite corner stays fixed`:`Corner ${i+1}: double-click to edit the box`});
  if(editing){
   for(let axis=0;axis<3;axis++)for(const side of [-1,1])handles.push({kind:'face',axis,side,world:at(box,[0,1,2].map(j=>j===axis?side*box.half_size[j]:0)),title:`Face ${'XYZ'[axis]}${side>0?'+':'−'}: drag to move this face; facing the camera, drag vertically for depth`});
   for(let i=0;i<8;i++)for(let axis=0;axis<3;axis++)if(!(i&(1<<axis)))handles.push({kind:'edge',axis,index:i,world:mul(add(points[i],points[i|(1<<axis)]),.5),title:`Edge: drag to rotate around box ${'XYZ'[axis]}; Shift snaps to 15 degrees`});
  }
  handles.sort((a,b)=>dot(sub(a.world,view.target),view.forward)-dot(sub(b.world,view.target),view.forward));
  const used=[];group.innerHTML=handles.map((h,index)=>{
   const p=project(h.world);if(!p)return '';let [x,y]=p;
   for(let n=0;n<9&&used.some(q=>Math.hypot(q[0]-x,q[1]-y)<16);n++){const angle=(index+n)*Math.PI/4;x=p[0]+Math.cos(angle)*(20+6*n);y=p[1]+Math.sin(angle)*(20+6*n);}used.push([x,y]);
   const attrs=`data-limit-kind="${h.kind}" data-limit-axis="${h.axis??0}" data-limit-side="${h.side??0}" data-limit-index="${h.index??0}" data-limit-world='${JSON.stringify(h.world)}' data-handle-x="${x}" data-handle-y="${y}" class="limit-handle ${h.kind} ${editing?'editing':''}" role="button" tabindex="0" aria-label="${h.title}"`;
   const shape=h.kind==='face'?`<circle ${attrs} cx="${x}" cy="${y}" r="7"><title>${h.title}</title></circle>`:h.kind==='edge'?`<path ${attrs} d="M ${x} ${y-6} L ${x+6} ${y} L ${x} ${y+6} L ${x-6} ${y} Z"><title>${h.title}</title></path>`:`<rect ${attrs} x="${x-5}" y="${y-5}" width="10" height="10" rx="1"><title>${h.title}</title></rect>`;
   return (Math.hypot(x-p[0],y-p[1])>1?`<line x1="${p[0]}" y1="${p[1]}" x2="${x}" y2="${y}" stroke="#8db6c1" stroke-dasharray="2 2"/>`:'')+shape;
  }).join('');element.append(group);
 }
 window.LimitBoxUI={begin,end,cancel,render,active:()=>editing,dragging:()=>!!drag};
})();
