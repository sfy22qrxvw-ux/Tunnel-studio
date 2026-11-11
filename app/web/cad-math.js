/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 const add=(a,b)=>a.map((v,i)=>v+b[i]), sub=(a,b)=>a.map((v,i)=>v-b[i]), mul=(a,s)=>a.map(v=>v*s);
 const dot=(a,b)=>a.reduce((n,v,i)=>n+v*b[i],0), cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
 const norm=a=>Math.hypot(...a),unit=a=>{const n=norm(a);if(n<1e-9)throw Error('Choose a non-zero direction.');return mul(a,1/n);};
 const worldFrame=()=>({name:'World',origin:[0,0,0],x:[1,0,0],y:[0,1,0],z:[0,0,1]});
 function toWorld(p,f,relative=false){return [0,1,2].map(i=>(relative?0:f.origin[i])+p[0]*f.x[i]+p[1]*f.y[i]+p[2]*f.z[i]);}
 function toLocal(p,f,relative=false){const d=relative?p:sub(p,f.origin);return [dot(d,f.x),dot(d,f.y),dot(d,f.z)];}
 function parse(text,f,last=null){
  let raw=String(text).trim(),relative=raw.startsWith('@'),world=raw.startsWith('#');if(relative||world)raw=raw.slice(1);
  if(relative&&!last)throw Error('A relative coordinate needs a previous point.');
  if(raw.includes('<')){
   if(!relative)throw Error('Polar entry uses @distance<angle or @distance<angle<elevation.');
   const a=raw.split('<').map(v=>v.trim());if(a.length<2||a.length>3||a.some(v=>!v||!Number.isFinite(Number(v))))throw Error('Use @distance<angle<elevation, with angles in degrees.');
   const [r,t,z=0]=a.map(Number),az=t*Math.PI/180,el=z*Math.PI/180;
   return add(last,toWorld([r*Math.cos(el)*Math.cos(az),r*Math.cos(el)*Math.sin(az),r*Math.sin(el)],f,true));
  }
  if(/[,;]\s*[,;]/.test(raw))throw Error('Coordinate components cannot be empty.');
  const tokens=raw.split(/[ ,;]+/).filter(Boolean);
  if(![2,3].includes(tokens.length)||tokens.some(v=>!/^[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?$/i.test(v)))throw Error('Enter X,Y,Z; @dX,dY,dZ for relative; or #E,N,H for world coordinates.');
  const p=tokens.map(Number);if(p.some(v=>!Number.isFinite(v)||Math.abs(v)>1e12))throw Error('Coordinates are outside the supported range.');
  if(p.length===2)p.push(relative?0:last?(world?last[2]:toLocal(last,f)[2]):0);
  const q=world?p:toWorld(p,f,relative);return relative?add(last,q):q;
 }
 function planePoint(pixel,view,f,base=null){
  const p=view.screenPoint(pixel),n=f.z,origin=base||f.origin,forward=view.basis.forward,den=dot(forward,n);
  if(Math.abs(den)<1e-7)return null;
  return add(p,mul(forward,dot(sub(origin,p),n)/den));
 }
 function visiblePlaneGrid(f,view,width,height,step){
  if(!(width>0&&height>0&&step>0)||Math.abs(dot(f.z,view.basis.forward))<.005)return null;
  const corners=[[0,0],[width,0],[width,height],[0,height]].map(pixel=>toLocal(planePoint(pixel,view,f),f));
  const lo=[0,1].map(i=>Math.min(...corners.map(p=>p[i]))),hi=[0,1].map(i=>Math.max(...corners.map(p=>p[i])));
  const ratio=Math.max(1,(hi[0]-lo[0])/(80*step),(hi[1]-lo[1])/(80*step));
  const scale=10**Math.floor(Math.log10(ratio)),spacing=step*[1,2,5,10].map(v=>v*scale).find(v=>v>=ratio);
  const lines=[];
  for(let i=0;i<2;i++){
   const first=Math.ceil(lo[i]/spacing),last=Math.floor(hi[i]/spacing);
   if(!Number.isSafeInteger(first)||!Number.isSafeInteger(last))continue;
   for(let n=first;n<=last;n++){
    const a=[...lo,0],b=[...hi,0];a[i]=b[i]=n*spacing;
    lines.push({a:toWorld(a,f),b:toWorld(b,f),major:n%5===0});
   }
  }
  return {spacing,lines};
 }
 function constrain(point,last,f,axis,ortho){
  if(!last)return point;const d=toLocal(sub(point,last),f,true);
  let index={x:0,y:1,z:2}[axis];if(index==null&&ortho)index=d.reduce((best,v,i)=>Math.abs(v)>Math.abs(d[best])?i:best,0);
  if(index!=null)return add(last,mul(f[['x','y','z'][index]],d[index]));
  if(axis==='xy')d[2]=0;if(axis==='xz')d[1]=0;if(axis==='yz')d[0]=0;
  return add(last,toWorld(d,f,true));
 }
 function segmentClosest(p,a,b){const d=sub(b,a),n=dot(d,d),t=n?Math.max(0,Math.min(1,dot(sub(p,a),d)/n)):0;return add(a,mul(d,t));}
 function profileSegment(a,b,radius){
  const dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy),r=Math.abs(radius),sign=Math.sign(radius);
  if(r<1e-10||length<1e-8||length>2*r+1e-8)return null;
  const h=sign*Math.sqrt(Math.max(0,r*r-length*length/4))/length;
  const centre=[(a[0]+b[0])/2-h*dy,(a[1]+b[1])/2+h*dx];
  const angle=Math.atan2(a[1]-centre[1],a[0]-centre[0]),sweep=sign*2*Math.asin(Math.min(1,length/(2*r)));
  const at=t=>[centre[0]+r*Math.cos(angle+sweep*t),centre[1]+r*Math.sin(angle+sweep*t)];
  const tau=2*Math.PI,atAngle=t=>{
   const delta=((sign*(t-angle)%tau)+tau)%tau;
   return delta<=Math.abs(sweep)+1e-9?at(delta/Math.abs(sweep)):null;
  };
  const closest=p=>atAngle(Math.atan2(p[1]-centre[1],p[0]-centre[0]))||
   (Math.hypot(p[0]-a[0],p[1]-a[1])<Math.hypot(p[0]-b[0],p[1]-b[1])?a.slice(0,2):b.slice(0,2));
  return {centre,at,atAngle,closest,sweep};
 }
 function segmentPixel(pixel,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],n=dx*dx+dy*dy,t=n?Math.max(0,Math.min(1,((pixel[0]-a[0])*dx+(pixel[1]-a[1])*dy)/n)):0;return {t,d:Math.hypot(pixel[0]-a[0]-t*dx,pixel[1]-a[1]-t*dy)};}
 function intersection(a,b,c,d){
  const u=sub(b,a),v=sub(d,c),w=sub(a,c),uu=dot(u,u),vv=dot(v,v),uv=dot(u,v),den=uu*vv-uv*uv;
  if(Math.abs(den)<1e-14)return null;
  const t=(uv*dot(v,w)-vv*dot(u,w))/den,s=(uu*dot(v,w)-uv*dot(u,w))/den;
  if(t<0||t>1||s<0||s>1)return null;const p=add(a,mul(u,t)),q=add(c,mul(v,s));return norm(sub(p,q))<1e-7?p:null;
 }
 function rayTriangle(origin,direction,a,b,c){const e1=sub(b,a),e2=sub(c,a),h=cross(direction,e2),det=dot(e1,h);if(Math.abs(det)<1e-12)return null;const s=sub(origin,a),u=dot(s,h)/det;if(u<0||u>1)return null;const q=cross(s,e1),v=dot(direction,q)/det;if(v<0||u+v>1)return null;const t=dot(e2,q)/det;return t>=0?t:null;}
 function frameFromPoints(origin,xPoint,yPoint,name='Working frame'){const x=unit(sub(xPoint,origin)),y0=sub(yPoint,origin),y=unit(sub(y0,mul(x,dot(y0,x))));return {name,origin,x,y,z:cross(x,y)};}
 // A pair fixes a midpoint, but its supporting face supplies the missing plane.
 // Subtract before averaging to retain survey-coordinate precision.
 const midpoint=(a,b)=>add(a,mul(sub(b,a),.5));
 function commonFaceFrame(o,a,b,forward){
  if(!o?.faces?.length||norm(sub(a,b))<1e-9)return null;
  const tolerance=Math.max(1e-7,norm(sub(a,b))*1e-8),planes=[];
  const triangleContains=(p,vertices)=>{
   const [v0,v1,v2]=vertices,u=sub(v1,v0),v=sub(v2,v0),w=sub(p,v0),n=cross(u,v),length=norm(n);
   if(length<1e-12||Math.abs(dot(w,n))/length>tolerance)return false;
   const uu=dot(u,u),uv=dot(u,v),vv=dot(v,v),wu=dot(w,u),wv=dot(w,v),den=uu*vv-uv*uv;
   if(den<=0)return false;const s=(vv*wu-uv*wv)/den,t=(uu*wv-uv*wu)/den,eps=tolerance/Math.max(1e-9,Math.min(norm(u),norm(v)));
   return s>=-eps&&t>=-eps&&s+t<=1+eps;
  };
  for(const face of o.faces){const v=face.map(i=>o.vertices[i]);if(!triangleContains(a,v))continue;
   const z=unit(cross(sub(v[1],v[0]),sub(v[2],v[0])));
   if(!planes.some(p=>Math.abs(dot(p.z,z))>1-1e-9))planes.push({origin:v[0],z,x:unit(sub(v[1],v[0])),faces:[],containsB:false});
  }
  for(const p of planes)for(const face of o.faces){const v=face.map(i=>o.vertices[i]);if(v.every(q=>Math.abs(dot(sub(q,p.origin),p.z))<=tolerance)){p.faces.push(face);p.containsB ||= triangleContains(b,v);}}
  const p=planes.filter(p=>p.containsB).sort((a,b)=>Math.abs(dot(b.z,forward))-Math.abs(dot(a.z,forward)))[0];if(!p)return null;
  const z=dot(p.z,forward)<0?mul(p.z,-1):p.z,x=p.x;
  return {frame:{name:'Face plane',origin:midpoint(a,b),x,y:cross(z,x),z},faces:p.faces};
 }
 function edges(o){
  const count=o.vertices.length;
  if(!o.faces?.length){
   const result=o.vertices.slice(1).map((v,i)=>[i,i+1]);
   if(o.closed&&count>2)result.push([count-1,0]);
   return result;
  }
  const found=new Map();
  for(const face of o.faces){
   const [p,q,r]=face.map(i=>o.vertices[i]),normal=unit(cross(sub(q,p),sub(r,p)));
   for(let i=0;i<3;i++){
    const a=face[i],b=face[(i+1)%3],key=Math.min(a,b)*count+Math.max(a,b);
    const row=found.get(key);
    if(!row)found.set(key,{edge:[a,b],normal,count:1,crease:false});
    else{row.count++;row.crease ||= Math.abs(dot(row.normal,normal))<.999;}
   }
  }
  return [...found.values()].filter(row=>row.count===1||row.crease).map(row=>row.edge);
 }
 const exports={add,sub,mul,dot,cross,norm,unit,worldFrame,toWorld,toLocal,parse,planePoint,visiblePlaneGrid,constrain,segmentClosest,segmentPixel,profileSegment,intersection,rayTriangle,frameFromPoints,midpoint,commonFaceFrame,edges};
 if(typeof module!=='undefined')module.exports=exports;else window.CadMath=exports;
})();
