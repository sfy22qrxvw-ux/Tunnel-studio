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
 function constrain(point,last,f,axis,ortho){
  if(!last)return point;const d=toLocal(sub(point,last),f,true);
  let index={x:0,y:1,z:2}[axis];if(index==null&&ortho)index=d.reduce((best,v,i)=>Math.abs(v)>Math.abs(d[best])?i:best,0);
  if(index!=null)return add(last,mul(f[['x','y','z'][index]],d[index]));
  if(axis==='xy')d[2]=0;if(axis==='xz')d[1]=0;if(axis==='yz')d[0]=0;
  return add(last,toWorld(d,f,true));
 }
 function segmentClosest(p,a,b){const d=sub(b,a),n=dot(d,d),t=n?Math.max(0,Math.min(1,dot(sub(p,a),d)/n)):0;return add(a,mul(d,t));}
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
 function edges(o){if(!o.faces?.length){const r=o.vertices.slice(1).map((v,i)=>[i,i+1]);if(o.closed&&o.vertices.length>2)r.push([o.vertices.length-1,0]);return r;}const found=new Map();for(const f of o.faces)for(let i=0;i<3;i++){const a=f[i],b=f[(i+1)%3],key=a<b?a+':'+b:b+':'+a;const n=unit(cross(sub(o.vertices[f[1]],o.vertices[f[0]]),sub(o.vertices[f[2]],o.vertices[f[0]])));if(!found.has(key))found.set(key,{edge:[a,b],n,count:1,crease:false});else{const r=found.get(key);r.count++;r.crease ||= Math.abs(dot(r.n,n))<.999;}}
  return [...found.values()].filter(r=>r.count===1||r.crease).map(r=>r.edge);
 }
 const exports={add,sub,mul,dot,cross,norm,unit,worldFrame,toWorld,toLocal,parse,planePoint,constrain,segmentClosest,segmentPixel,intersection,rayTriangle,frameFromPoints,midpoint,commonFaceFrame,edges};
 if(typeof module!=='undefined')module.exports=exports;else window.CadMath=exports;
})();
