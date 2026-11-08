/* Six calibrated JPEG faces. No point-cloud buffers or point preview requests. */
(() => {
 'use strict';
 const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0),unit=a=>{const n=Math.hypot(...a);return a.map(v=>v/n);};
 const localRay=(face,u,v)=>{const c=[(u*(face.width-1)-face.cx)/face.fx,(v*(face.height-1)-face.cy)/face.fy,1];return unit([0,1,2].map(j=>face.rotation.reduce((s,row,i)=>s+row[j]*c[i],0)));};
 const faceAt=(faces,ray)=>{let best=null;for(const face of faces){const c=face.rotation.map(row=>dot(row,ray));if(c[2]<=0)continue;const u=(face.fx*c[0]/c[2]+face.cx)/(face.width-1),v=(face.fy*c[1]/c[2]+face.cy)/(face.height-1);if(u>=-1e-7&&u<=1+1e-7&&v>=-1e-7&&v<=1+1e-7&&(!best||c[2]>best.depth))best={face:face.index,u:Math.max(0,Math.min(1,u)),v:Math.max(0,Math.min(1,v)),depth:c[2]};}return best;};
 class Viewer {
  constructor(port,view,onPick){
   this.port=port;this.view=view;this.onPick=onPick;this.yaw=0;this.pitch=0;this.fov=70;this.size=1024;this.faces=[];this.destroyed=false;this.serial=0;this.raf=0;this.images=[];
   port.replaceChildren();this.canvas=document.createElement('canvas');this.canvas.id='setup-photo-canvas';this.canvas.setAttribute('aria-label','Photograph view from the selected scanner position');port.append(this.canvas);
   try{this.gl=this.canvas.getContext('webgl',{alpha:false,antialias:false,preserveDrawingBuffer:true});if(this.gl)this.initGL();}catch(e){if(this.gl){if(this.buffer)this.gl.deleteBuffer(this.buffer);if(this.program)this.gl.deleteProgram(this.program);}this.gl=null;}
   if(!this.gl)this.fallback();
   this.canvas.onwebglcontextlost=e=>{e.preventDefault();this.destroyTextures();this.gl=null;this.fallback();this.drawFallback();};
   this.canvas.onwheel=e=>{e.preventDefault();this.fov=Math.max(8,Math.min(105,this.fov*Math.exp(e.deltaY*.001)));this.schedule();};
   this.canvas.onpointerdown=e=>{if(e.button!==0||e.isPrimary===false||this.drag)return;this.drag={id:e.pointerId,x:e.clientX,y:e.clientY,last:[e.clientX,e.clientY],moved:false};this.canvas.setPointerCapture(e.pointerId);};
   this.canvas.onpointermove=e=>{const d=this.drag;if(!d||d.id!==e.pointerId)return;d.moved ||= Math.hypot(e.clientX-d.x,e.clientY-d.y)>4;if(d.moved){const scale=this.fov/Math.max(1,this.canvas.clientHeight)*Math.PI/180;this.yaw-=(e.clientX-d.last[0])*scale;this.pitch=Math.max(-Math.PI/2+.001,Math.min(Math.PI/2-.001,this.pitch+(e.clientY-d.last[1])*scale));this.schedule();}d.last=[e.clientX,e.clientY];};
   this.canvas.onpointercancel=()=>this.drag=null;
   this.canvas.onpointerup=e=>{const d=this.drag;if(!d||d.id!==e.pointerId)return;this.drag=null;if(d.moved||this.destroyed||this.loading||!this.images.length)return;const r=this.canvas.getBoundingClientRect(),nx=(e.clientX-r.left)/r.width,ny=(e.clientY-r.top)/r.height;if(nx<0||nx>1||ny<0||ny>1)return;const hit=this.hit(nx,ny);if(hit)this.onPick({...hit,thumbnail:this.thumbnail(nx,ny)});};
   this.observer=new ResizeObserver(()=>this.schedule());this.observer.observe(port);
  }
  initGL(){
   const gl=this.gl,shaders=[],shader=(kind,source)=>{const s=gl.createShader(kind);shaders.push(s);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
   try{
   const vs=shader(gl.VERTEX_SHADER,'attribute vec3 position; attribute vec2 uv; uniform vec3 right; uniform vec3 up; uniform vec3 forward; uniform float tanHalf; uniform float aspect; varying vec2 coord; void main(){float z=dot(position,forward); gl_Position=vec4(dot(position,right)/(tanHalf*aspect),dot(position,up)/tanHalf,1.0002*z-0.020002,z); coord=uv;}');
   const fs=shader(gl.FRAGMENT_SHADER,'precision mediump float; varying vec2 coord; uniform sampler2D photo; void main(){gl_FragColor=texture2D(photo,coord);}');
   this.program=gl.createProgram();gl.attachShader(this.program,vs);gl.attachShader(this.program,fs);gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error('Photograph renderer could not start.');
   }finally{for(const s of shaders)gl.deleteShader(s);}
   this.uniforms={};for(const n of ['right','up','forward','tanHalf','aspect','photo'])this.uniforms[n]=gl.getUniformLocation(this.program,n);this.position=gl.getAttribLocation(this.program,'position');this.uv=gl.getAttribLocation(this.program,'uv');
   this.buffer=gl.createBuffer();const vertices=[];for(const face of this.view.faces)for(const [u,v] of [[0,0],[1,0],[1,1],[0,0],[1,1],[0,1]]){const ray=localRay(face,u,v),cameraZ=dot(face.rotation[2],ray);vertices.push(...ray.map(x=>x/cameraZ),u,v);}gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.STATIC_DRAW);
   gl.clearColor(.025,.04,.05,1);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);
  }
  fallback(){
   this.canvas.hidden=true;if(this.fallbackBox)return;this.fallbackBox=document.createElement('div');this.fallbackBox.className='setup-photo-fallback';const note=document.createElement('p');note.textContent='Photographs · face view (360° graphics unavailable)';this.faceChoice=document.createElement('select');this.faceChoice.setAttribute('aria-label','Photograph direction');for(const f of this.view.faces){const o=document.createElement('option');o.value=f.index;o.textContent='Direction '+(f.index+1);this.faceChoice.append(o);}this.faceChoice.onchange=()=>this.drawFallback();this.faceImage=document.createElement('img');this.faceImage.draggable=false;this.faceImage.alt='Selected setup photograph';this.faceImage.onclick=e=>{if(this.loading)return;const r=this.faceImage.getBoundingClientRect(),u=(e.clientX-r.left)/r.width,v=(e.clientY-r.top)/r.height;if(u>=0&&u<=1&&v>=0&&v<=1)this.onPick({face:+this.faceChoice.value,u,v,thumbnail:null});};this.fallbackBox.append(note,this.faceChoice,this.faceImage);this.port.append(this.fallbackBox);
  }
  basis(){const c=Math.cos(this.pitch),s=Math.sin(this.pitch),cy=Math.cos(this.yaw),sy=Math.sin(this.yaw);return {forward:[c*cy,c*sy,s],right:[sy,-cy,0],up:[-s*cy,-s*sy,c]};}
  hit(nx,ny){const b=this.basis(),t=Math.tan(this.fov*Math.PI/360),aspect=this.canvas.clientWidth/Math.max(1,this.canvas.clientHeight);return faceAt(this.view.faces,unit([0,1,2].map(i=>b.forward[i]+(nx*2-1)*t*aspect*b.right[i]+(1-ny*2)*t*b.up[i])));}
  async load(size=1024,onProgress=()=>{}){
   this.cancelLoad();if(this.destroyed)throw new DOMException('Setup view closed.','AbortError');
   const op={serial:++this.serial,pending:new Set(),cancelled:false};this.loadOperation=op;this.loading=true;let completed=0;
   const abortError=()=>new DOMException('Photograph loading cancelled.','AbortError');
   op.abort=()=>{op.cancelled=true;for(const request of op.pending)request.reject(abortError());};
   const timer=setTimeout(()=>{for(const request of op.pending)request.reject(Error('Photograph loading timed out. Retry this setup or choose Fast detail.'));},30000);
   try{
    onProgress(0,this.view.faces.length);
    const results=await Promise.all(this.view.faces.map(face=>new Promise((resolve,reject)=>{
     const im=new Image(),request={im,reject};op.pending.add(request);
     im.onload=()=>{op.pending.delete(request);im.onload=im.onerror=null;onProgress(++completed,this.view.faces.length);resolve(im);};
     im.onerror=()=>reject(Error('A setup photograph could not be loaded. Retry or load photographs from the original Leica folder.'));
     im.src='/api/setup-photo-face?'+new URLSearchParams({id:this.view.id,key:this.view.photo_key,face:face.index,size});
    })));
    if(this.destroyed||op.cancelled||this.loadOperation!==op)throw abortError();
    const textures=[],gl=this.gl;
    try{if(gl)for(const im of results){const tex=gl.createTexture();textures.push(tex);gl.bindTexture(gl.TEXTURE_2D,tex);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,im);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);}}
    catch(e){if(gl)for(const tex of textures)gl.deleteTexture(tex);throw e;}
    this.destroyTextures();this.faces=textures;this.images=results;this.size=size;this.drawFallback();this.schedule();
   }finally{
    clearTimeout(timer);for(const {im} of op.pending){im.onload=im.onerror=null;im.src='';}op.pending.clear();
    if(this.loadOperation===op){this.loadOperation=null;this.loading=false;}
   }
  }
  cancelLoad(){this.loadOperation?.abort();}
  drawFallback(){if(this.fallbackBox&&this.images.length)this.faceImage.src=this.images[+this.faceChoice.value].src;}
  schedule(){if(this.destroyed||this.raf)return;this.raf=requestAnimationFrame(()=>{this.raf=0;this.draw();});}
  draw(){
   if(!this.gl||this.destroyed||this.faces.length!==6)return;const gl=this.gl,dpr=Math.min(window.devicePixelRatio||1,1.5),w=Math.max(1,Math.round(this.canvas.clientWidth*dpr)),h=Math.max(1,Math.round(this.canvas.clientHeight*dpr));if(this.canvas.width!==w||this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h;}
   gl.viewport(0,0,w,h);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(this.program);gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.enableVertexAttribArray(this.position);gl.vertexAttribPointer(this.position,3,gl.FLOAT,false,20,0);gl.enableVertexAttribArray(this.uv);gl.vertexAttribPointer(this.uv,2,gl.FLOAT,false,20,12);const b=this.basis();for(const n of ['right','up','forward'])gl.uniform3fv(this.uniforms[n],b[n]);gl.uniform1f(this.uniforms.tanHalf,Math.tan(this.fov*Math.PI/360));gl.uniform1f(this.uniforms.aspect,w/h);gl.uniform1i(this.uniforms.photo,0);gl.activeTexture(gl.TEXTURE0);for(let i=0;i<6;i++){gl.bindTexture(gl.TEXTURE_2D,this.faces[i]);gl.drawArrays(gl.TRIANGLES,i*6,6);}
  }
  thumbnail(nx,ny){try{this.draw();const out=document.createElement('canvas');out.width=128;out.height=128;const c=out.getContext('2d');c.fillStyle='#151e23';c.fillRect(0,0,128,128);c.drawImage(this.canvas,nx*this.canvas.width-40,ny*this.canvas.height-40,80,80,0,0,128,128);c.strokeStyle='#ff4646';c.lineWidth=2;c.beginPath();c.moveTo(52,64);c.lineTo(60,64);c.moveTo(68,64);c.lineTo(76,64);c.moveTo(64,52);c.lineTo(64,60);c.moveTo(64,68);c.lineTo(64,76);c.stroke();return out.toDataURL('image/png');}catch(e){return null;}}
  reset(){this.yaw=0;this.pitch=0;this.fov=70;this.schedule();}
  destroyTextures(){if(this.gl)for(const t of this.faces)this.gl.deleteTexture(t);this.faces=[];}
  destroy(){this.destroyed=true;this.cancelLoad();this.serial++;if(this.raf)cancelAnimationFrame(this.raf);this.observer?.disconnect?.();this.destroyTextures();if(this.gl){this.gl.deleteBuffer(this.buffer);this.gl.deleteProgram(this.program);}this.images=[];this.port.replaceChildren();}
 }
 window.SetupPhotoViewer={create:(...args)=>new Viewer(...args),localRay,faceAt};
})();
