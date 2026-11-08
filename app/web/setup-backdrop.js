/* Calibrated setup photographs drawn in the existing main WebGL viewport. */
(()=>{
 'use strict';
 const dot=(a,b)=>a.reduce((sum,v,i)=>sum+v*b[i],0),unit=a=>{const n=Math.hypot(...a);return a.map(v=>v/n);};
 const localRay=(f,u,v)=>{const c=[(u*(f.width-1)-f.cx)/f.fx,(v*(f.height-1)-f.cy)/f.fy,1];return unit([0,1,2].map(j=>f.rotation.reduce((sum,row,i)=>sum+row[j]*c[i],0)));};
 function faceAt(faces,ray){let best=null;for(const f of faces){const c=f.rotation.map(row=>dot(row,ray));if(c[2]<=0)continue;const u=(f.fx*c[0]/c[2]+f.cx)/(f.width-1),v=(f.fy*c[1]/c[2]+f.cy)/(f.height-1);if(u>=-1e-7&&u<=1+1e-7&&v>=-1e-7&&v<=1+1e-7&&(!best||c[2]>best.depth))best={face:f.index,u:Math.max(0,Math.min(1,u)),v:Math.max(0,Math.min(1,v)),depth:c[2]};}return best;}
 const cache=new Map(),MAX_BYTES=96*1024*1024;
 function keep(key,images){const bytes=images.reduce((sum,im)=>sum+(im.naturalWidth||im.width)*(im.naturalHeight||im.height)*4,0);cache.delete(key);cache.set(key,{images,bytes});while(cache.size>2||[...cache.values()].reduce((sum,x)=>sum+x.bytes,0)>MAX_BYTES)cache.delete(cache.keys().next().value);}
 class Backdrop{
  constructor(scene,view,orientation){
   if(!scene.gl||!Array.isArray(view.faces)||view.faces.length!==6)throw Error('The setup photographs have no supported 360° view.');
   this.scene=scene;this.gl=scene.gl;this.view=view;this.orientation=orientation;this.textures=[];this.destroyed=false;this.size=0;
   const gl=this.gl,shaders=[];try{
    const shader=(kind,source)=>{const s=gl.createShader(kind);shaders.push(s);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s)||'Photograph shader failed.');return s;};
    this.program=gl.createProgram();
    gl.attachShader(this.program,shader(gl.VERTEX_SHADER,'attribute vec3 position; attribute vec2 uv; uniform vec3 right; uniform vec3 up; uniform vec3 forward; uniform float tanHalf; uniform float aspect; varying vec2 coord; void main(){float z=dot(position,forward); gl_Position=vec4(dot(position,right)/(tanHalf*aspect),dot(position,up)/tanHalf,z*0.999,z); coord=uv;}'));
    gl.attachShader(this.program,shader(gl.FRAGMENT_SHADER,'precision mediump float; varying vec2 coord; uniform sampler2D photo; uniform float toneContrast; uniform float toneGamma; uniform float photoOpacity; void main(){vec3 colour=texture2D(photo,coord).rgb;gl_FragColor=vec4(pow(clamp((colour-0.5)*toneContrast+0.5,0.0,1.0),vec3(1.0/toneGamma)),photoOpacity);}'));
    gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error('Photograph renderer could not start.');
    this.position=gl.getAttribLocation(this.program,'position');this.uv=gl.getAttribLocation(this.program,'uv');this.uniforms={};for(const name of ['right','up','forward','tanHalf','aspect','photo','toneContrast','toneGamma','photoOpacity'])this.uniforms[name]=gl.getUniformLocation(this.program,name);
    const vertices=[];for(const f of view.faces)for(const [u,v] of [[0,0],[1,0],[1,1],[0,0],[1,1],[0,1]]){const ray=localRay(f,u,v),z=dot(f.rotation[2],ray),local=ray.map(x=>x/z);vertices.push(...orientation.map(row=>dot(row,local)),u,v);}
    this.buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.STATIC_DRAW);
   }catch(e){if(this.buffer)gl.deleteBuffer(this.buffer);if(this.program)gl.deleteProgram(this.program);throw e;}finally{for(const s of shaders)gl.deleteShader(s);}
  }
  ready(){return !this.destroyed&&this.textures.length===6;}
  async load(size=1024,onProgress=()=>{}){
   this.cancel();if(this.destroyed)throw new DOMException('Setup closed.','AbortError');
   const op={pending:new Set(),cancelled:false};this.operation=op;let completed=0;
   const aborted=()=>new DOMException('Photograph loading cancelled.','AbortError');
   op.abort=()=>{op.cancelled=true;for(const r of op.pending)r.reject(aborted());};
   const key=this.view.photo_key+'|'+size,stored=cache.get(key);let timer;
   try{
    onProgress(0,6);let images;
    if(stored){images=stored.images;cache.delete(key);cache.set(key,stored);onProgress(6,6);}
    else{
     timer=setTimeout(()=>{for(const r of op.pending)r.reject(Error('Photograph loading timed out. Retry or use Fast photo detail.'));},30000);
     images=await Promise.all(this.view.faces.map(face=>new Promise((resolve,reject)=>{
      const im=new Image(),request={im,reject};op.pending.add(request);
      im.onload=()=>{op.pending.delete(request);im.onload=im.onerror=null;onProgress(++completed,6);resolve(im);};
      im.onerror=()=>reject(Error('A setup photograph could not be loaded. Load photographs from the original Leica project folder, then retry.'));
      im.src='/api/setup-photo-face?'+new URLSearchParams({id:this.view.id,key:this.view.photo_key,face:face.index,size});
     })));
    }
    if(this.destroyed||op.cancelled||this.operation!==op)throw aborted();
    const gl=this.gl,textures=[];try{
     gl.activeTexture(gl.TEXTURE0);
     for(const im of images){const tex=gl.createTexture();textures.push(tex);gl.bindTexture(gl.TEXTURE_2D,tex);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,im);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);}
    }catch(e){for(const tex of textures)gl.deleteTexture(tex);throw e;}
    this.clearTextures();this.textures=textures;this.size=size;keep(key,images);this.scene.draw();return true;
   }finally{clearTimeout(timer);for(const r of op.pending){r.im.onload=r.im.onerror=null;r.im.src='';}op.pending.clear();if(this.operation===op)this.operation=null;}
  }
  cancel(){this.operation?.abort();}
  draw(basis,aspect,fov){
   const opacity=this.scene.setupPhotoOpacity??1;if(!this.ready()||opacity<=0)return;const gl=this.gl;if(opacity<1){gl.enable(gl.BLEND);gl.blendFuncSeparate(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA,gl.ONE,gl.ONE_MINUS_SRC_ALPHA);}else gl.disable(gl.BLEND);gl.disable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.depthMask(false);
   gl.useProgram(this.program);gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.enableVertexAttribArray(this.position);gl.vertexAttribPointer(this.position,3,gl.FLOAT,false,20,0);gl.enableVertexAttribArray(this.uv);gl.vertexAttribPointer(this.uv,2,gl.FLOAT,false,20,12);
   for(const name of ['right','up','forward'])gl.uniform3fv(this.uniforms[name],basis[name]);gl.uniform1f(this.uniforms.tanHalf,Math.tan(fov*Math.PI/360));gl.uniform1f(this.uniforms.aspect,aspect);gl.uniform1f(this.uniforms.toneContrast,this.scene.pointContrast||1);gl.uniform1f(this.uniforms.toneGamma,this.scene.pointGamma||1);gl.uniform1f(this.uniforms.photoOpacity,opacity);gl.uniform1i(this.uniforms.photo,0);gl.activeTexture(gl.TEXTURE0);
   for(let i=0;i<6;i++){gl.bindTexture(gl.TEXTURE_2D,this.textures[i]);gl.drawArrays(gl.TRIANGLES,i*6,6);}
   gl.disable(gl.BLEND);gl.disableVertexAttribArray(this.position);gl.disableVertexAttribArray(this.uv);gl.depthMask(true);gl.enable(gl.DEPTH_TEST);
  }
  hit(worldRay){return faceAt(this.view.faces,[0,1,2].map(i=>this.orientation.reduce((sum,row,j)=>sum+row[i]*worldRay[j],0)));}
  thumbnail(nx,ny){try{this.scene.draw();const input=this.scene.c,out=document.createElement('canvas');out.width=128;out.height=128;const c=out.getContext('2d');c.fillStyle='#151e23';c.fillRect(0,0,128,128);c.drawImage(input,nx*input.width-40,ny*input.height-40,80,80,0,0,128,128);c.strokeStyle='#ff4646';c.lineWidth=2;c.beginPath();c.moveTo(52,64);c.lineTo(76,64);c.moveTo(64,52);c.lineTo(64,76);c.stroke();return out.toDataURL('image/png');}catch(e){return null;}}
  clearTextures(){for(const t of this.textures)this.gl.deleteTexture(t);this.textures=[];}
  destroy(){this.destroyed=true;this.cancel();this.clearTextures();this.gl.deleteBuffer(this.buffer);this.gl.deleteProgram(this.program);}
 }
 window.SetupBackdrop={create:(...args)=>new Backdrop(...args),localRay,faceAt};
})();
