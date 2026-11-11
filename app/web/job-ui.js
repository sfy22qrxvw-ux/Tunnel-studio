/* Copyright (c) 2026 Damian Gulczynski. */
'use strict';
(()=>{
 let data={items:[]},selected=null,loading=false,uploading=false,uploadController=null,stopQueue=false,signature='',previewRequested=true,packed=null,previewOrigin=[0,0,0],sourceInfo={sample:0,points:0,vertices:0};
 let pointOwners=[],pointPreviewComplete=true,inventoryTimer=null,pointRefineTimer=null,pointEditGeneration=0,previewEpoch=0,previewOperationBusy=false;
 let meshStatus={state:'empty',mode:'solid',displayed_triangles:0,total_triangles:0,items:[],error:''};
 let liveLimitEditing=false,previewRefreshPending=false,analysisRunning=false,fitPreviewPending=false;
 const previewIdleWaiters=[];
 const previewVisibleWaiters=[];
 let detailView=null,detailTimer=null,autoDetail=true,displayContrast=1,displayGamma=1,setupState=null,setupReturn=null,setupGeneration=0,setupDisplay='points',setupRayPick=null,setupBlendValue=.5;
 const setupPreviews=new Map();
 let normalOverview=null,setupOverview=null,meshPreview=null,setupPointsLoading=false,previewRequest=null;
 let cleanPointLabels=[],areaSelectionCallback=null,limitPreview=null,clickGesture=null;const deletedFolders=new Set(),expandedScans=new Set();
 let previewOwner=null,previewSource=null,previewResult=null;let deletedSelection=new Set(),deletedGeneration=0,deletedMode=false,deletedLoading=false,deletedPreviewGroups=[],deletedBounds=[];
 let targetSelection=new Set(),cleanSnapshot=null,selectedItems=new Set(),selectionAnchor=null,pendingPointDelete=null;
 let targetNameEdit=null,repeatPick=null,pendingRepeatPick=null,setupPickPreview=null;
 let polygonVertices=[],polygonCursor=null,polygonGesture=null,polygonFrame=null,brushPointer=null;
 let reviewBoxBoundaries=[];
 let jobMode='orbit',dragStart=null,lasso=[],selectionSpec=null,selectionBoundaryWorld=[],targetMarkers=[],pointPickCallback=null,pointPickDone='Scan point picked',worldPickCallback=null,displayMode=localStorage.getItem('tunnelstudio.displayMode')||'rgb',internalClipboard=null;
 const gb=n=>(n/1e9).toLocaleString('en-GB',{maximumFractionDigits:2})+' GB';
 const current=()=>data.items.find(x=>x.id===selected);
 const dot=(a,b)=>a.reduce((n,v,i)=>n+v*b[i],0);
 const scaleBar=document.createElement('div');scaleBar.id='job-scale';scaleBar.className='job-scale';scaleBar.hidden=true;scaleBar.setAttribute('role','img');scaleBar.innerHTML='<span class="job-scale-value"></span><div class="job-scale-rule"><i></i><i></i><i></i><i></i></div><span class="job-scale-caption">View-plane distance</span>';$('job-canvas').parentElement.append(scaleBar);
 function updateScale(scene){
  const width=scene.c.clientWidth;
  if(scene.setupCamera||!scene.gl||width<160||!Number.isFinite(scene.scale)||scene.scale<=0||!(scene.pointCount||scene.meshCount||scene.cadFacesCount||scene.cadLinesCount||scene.cadPointsCount)){scaleBar.hidden=true;return;}
  const view=scene.c.getBoundingClientRect(),drawer=$('tool-drawer'),dr=drawer&&!drawer.hidden?drawer.getBoundingClientRect():null;
  const covered=dr&&dr.right>=view.right-8&&dr.bottom>=view.bottom-70&&dr.left<view.right?Math.max(0,view.right-dr.left+12):0;
  const available=width-covered-28;if(available<100){scaleBar.hidden=true;return;}
  const metresPerPixel=2*scene.scale/width,target=Math.min(150,Math.max(70,width*.16),available),raw=target*metresPerPixel,power=10**Math.floor(Math.log10(raw));
  const metres=[1,2,5,10].filter(n=>n*power<=raw*(1+1e-12)).at(-1)*power,pixels=metres/metresPerPixel;
  const unit=metres<1?'mm':metres>=1000?'km':'m',value=metres*(unit==='mm'?1000:unit==='km'?.001:1),label=Number(value.toPrecision(6)).toLocaleString('en-GB',{maximumFractionDigits:6})+' '+unit;
  scaleBar.hidden=false;scaleBar.style.right=(14+covered)+'px';scaleBar.style.setProperty('--scale-pixels',pixels+'px');scaleBar.dataset.metres=String(metres);scaleBar.dataset.metresPerPixel=String(metresPerPixel);
  scaleBar.querySelector('.job-scale-value').textContent=label;scaleBar.setAttribute('aria-label','View-plane scale: '+label);scaleBar.title='Distance in the camera view plane. The bar updates with zoom and viewport size.';
 }
 function pointInPolygon(x,y,poly){let inside=false,j=poly.length-1;for(let i=0;i<poly.length;i++){const [xi,yi]=poly[i],[xj,yj]=poly[j];if(((yi>y)!==(yj>y))&&(x<(xj-xi)*(y-yi)/(yj-yi||1e-30)+xi))inside=!inside;j=i;}return inside;}
 function simplifyPath(points,maxVertices=120){if(points.length<=maxVertices)return points;const out=[],step=(points.length-1)/(maxVertices-1);for(let i=0;i<maxVertices;i++)out.push(points[Math.min(points.length-1,Math.round(i*step))]);return out;}
 function polygonBounds(poly){let minx=Infinity,miny=Infinity,maxx=-Infinity,maxy=-Infinity;for(const p of poly){minx=Math.min(minx,p[0]);maxx=Math.max(maxx,p[0]);miny=Math.min(miny,p[1]);maxy=Math.max(maxy,p[1]);}return [minx,miny,maxx,maxy];}
 const brushBounds=new WeakMap();
 function inBrush(x,y,spec){
  let bounds=brushBounds.get(spec);if(!bounds){const b=polygonBounds(spec.path);bounds=[b[0]-spec.radius,b[1]-spec.radius,b[2]+spec.radius,b[3]+spec.radius];brushBounds.set(spec,bounds);}
  if(x<bounds[0]||y<bounds[1]||x>bounds[2]||y>bounds[3])return false;
  const path=spec.path,r2=spec.radius**2;
  for(let i=0;i<path.length;i++){const a=path[i],b=path[Math.min(i+1,path.length-1)],dx=b[0]-a[0],dy=b[1]-a[1],l=dx*dx+dy*dy,t=l?Math.max(0,Math.min(1,((x-a[0])*dx+(y-a[1])*dy)/l)):0;if((x-a[0]-t*dx)**2+(y-a[1]-t*dy)**2<=r2+1e-14)return true;}
  return false;
 }
 function pixelBrushToSpec(points){const r=$('job-canvas').getBoundingClientRect(),radius=Math.max(2,Math.min(150,+$('job-brush-radius').value||20)),b=polygonBounds(points),rectangle=[[b[0]-radius,b[1]-radius],[b[2]+radius,b[1]-radius],[b[2]+radius,b[3]+radius],[b[0]-radius,b[3]+radius]],spec=pixelPolygonToSpec(rectangle);delete spec.polygon;spec.mode='screen_brush';spec.path=simplifyPath(points,512).map(([x,y])=>[2*x/r.width-1,(1-2*y/r.height)/spec.aspect]);spec.radius=2*radius/r.width;return {spec,rectangle};}
 function drawBrushOverlay(points,radius){const svg=$('job-selection-overlay'),r=$('job-canvas').getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${Math.max(1,r.width)} ${Math.max(1,r.height)}`);svg.innerHTML=points.length===1?`<circle cx="${points[0][0]}" cy="${points[0][1]}" r="${radius}" fill="#ffe56e55" stroke="#ffe56e"/>`:points.length?`<polyline points="${points.map(p=>p.join(',')).join(' ')}" fill="none" stroke="#ffe56e66" stroke-width="${2*radius}" stroke-linecap="round" stroke-linejoin="round"/>`:'';}
 function selectedBySpec(world,spec){if(!spec)return false;if(spec.clip_box&&!window.TunnelBox.contains(world,spec.clip_box))return false;if(['box','oriented_box'].includes(spec.mode)){const use=window.TunnelBox.contains(world,spec);return spec.invert?!use:use;}const d=world.map((v,i)=>v-spec.target[i]),x=dot(d,spec.right)/spec.scale,y=dot(d,spec.up)*spec.aspect/spec.scale,z=spec.forward?dot(d,spec.forward):0;const depthOk=!spec.forward||!Number.isFinite(spec.depth_min)||!Number.isFinite(spec.depth_max)||(z>=spec.depth_min&&z<=spec.depth_max);const use=depthOk&&(spec.mode==='screen_brush'?inBrush(x,y/spec.aspect,spec):pointInPolygon(x,y,spec.polygon));return spec.invert?!use:use;}
 function pixelPolygonToSpec(points){const displayed=jobScene.pointValues||packed;const r=$('job-canvas').getBoundingClientRect(),basis=jobScene.basis(),clean=simplifyPath(points),polygon=clean.map(([x,y])=>[2*x/r.width-1,1-2*y/r.height]),bounds=polygonBounds(polygon);let dmin=Infinity,dmax=-Infinity,count=0;for(const displayed of [jobScene.pointValues||packed,...(jobScene.pointPages||[]).map(p=>p.values)])if(displayed){for(let i=0;i<displayed.length;i+=6){const d=[displayed[i]-jobScene.target[0],displayed[i+1]-jobScene.target[1],displayed[i+2]-jobScene.target[2]],x=dot(d,basis.right)/jobScene.scale,y=dot(d,basis.up)*(r.width/Math.max(1,r.height))/jobScene.scale;if(x<bounds[0]||x>bounds[2]||y<bounds[1]||y>bounds[3]||!pointInPolygon(x,y,polygon))continue;const z=dot(d,basis.forward);dmin=Math.min(dmin,z);dmax=Math.max(dmax,z);count++;}}if(!count)throw Error('No displayed points are inside that selection. Draw around visible scan points.');for(const item of (deletedMode?deletedBounds:data.items.filter(x=>x.kind==='scan'&&x.visible&&x.minimum&&x.maximum))){for(let bits=0;bits<8;bits++){const corner=[0,1,2].map(j=>((bits>>j)&1?item.maximum[j]:item.minimum[j])-previewOrigin[j]-jobScene.target[j]),depth=dot(corner,basis.forward);dmin=Math.min(dmin,depth);dmax=Math.max(dmax,depth);}}const pad=Math.max(jobScene.scale*.003,(dmax-dmin)*.03,0.01);return {mode:'screen_polygon',target:jobScene.target.map((v,i)=>v+previewOrigin[i]),right:basis.right,up:basis.up,forward:basis.forward,scale:jobScene.scale,aspect:r.width/Math.max(1,r.height),polygon,depth_min:dmin-pad,depth_max:dmax+pad,invert:false};}
 function specToPixels(spec){if(!spec||spec.mode!=='screen_polygon')return [];const r=$('job-canvas').getBoundingClientRect();return spec.polygon.map(([x,y])=>[(x+1)*r.width/2,(1-y)*r.height/2]);}
 function selectionWorldBoundary(spec){if(!spec||!['screen_polygon','screen_brush'].includes(spec.mode))return [];const z=(Number(spec.depth_min)+Number(spec.depth_max))/2,brush=spec.mode==='screen_brush';return (brush?spec.path:spec.polygon).map(([x,y])=>spec.target.map((v,i)=>v+spec.right[i]*x*spec.scale+spec.up[i]*y*spec.scale/(brush?1:spec.aspect)+(spec.forward?.[i]||0)*z));}
 function worldToPixel(world){if(!world?.every(Number.isFinite))return null;const p=jobScene.projectLocal(world.map((v,i)=>v-previewOrigin[i]));return p?[(p[0]+1)*canvas.clientWidth/2,(1-p[1])*canvas.clientHeight/2]:null;}
 function drawStoredSelection(){drawLimitBox();if(polygonFrame&&polygonVertices.length){drawPolygon();return;}if(dragStart&&jobMode==='brush'){drawBrushOverlay(lasso,+$('job-brush-radius').value);return;}if(dragStart&&jobMode==='lasso'){drawSelectionOverlay(lasso,false);return;}if(selectionSpec?.mode==='screen_brush'&&selectionBoundaryWorld.length){drawBrushOverlay(selectionBoundaryWorld.map(worldToPixel).filter(Boolean),selectionSpec.radius*selectionSpec.scale/jobScene.scale*$('job-canvas').clientWidth/2);return;}if(!selectionBoundaryWorld.length){drawSelectionOverlay([]);return;}drawSelectionOverlay(selectionBoundaryWorld.map(worldToPixel).filter(Boolean),true);}
 function displayedSelectionCount(){const displayed=jobScene.pointValues||packed;if(!selectionSpec||!displayed)return 0;let n=0;for(const displayed of [jobScene.pointValues||packed,...(jobScene.pointPages||[]).map(p=>p.values)])if(displayed)for(let i=0;i<displayed.length;i+=6){const p=[displayed[i]+previewOrigin[0],displayed[i+1]+previewOrigin[1],displayed[i+2]+previewOrigin[2]];if(selectedBySpec(p,selectionSpec))n++;}return n;}
 function drawSelectionOverlay(points=[],closed=true){
  const svg=$('job-selection-overlay');if(!svg)return;
  const r=$("job-canvas").getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${Math.max(1,r.width)} ${Math.max(1,r.height)}`);
  const boxes=previewOwner==='shape'?reviewBoxBoundaries.map(boundary=>{
   const pixels=boundary.map(worldToPixel);if(pixels.some(point=>!point))return '';
   return `<polygon points="${pixels.map(p=>p.join(',')).join(' ')}" fill="#ffe56e18" stroke="#ffe56e" stroke-dasharray="5 4" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  }).join(''):'';
  svg.innerHTML=boxes+(points.length?`<polyline points="${points.map(p=>p.join(',')).join(' ')}${closed?' '+points[0].join(','):''}" fill="${closed?'#ffe56e18':'none'}" stroke="#ffe56e" stroke-width="2" vector-effect="non-scaling-stroke"/>`:'');
 }
 function setReviewBoxes(specs){reviewBoxBoundaries=specs.map(selectionWorldBoundary);drawStoredSelection();}
 class JobScene extends CloudScene{
  basis(){
   const frame=super.basis();
   // In orthographic mode positive depth is towards the camera. The screen
   // right vector must be evaluated from that side of the scene.
   if(!this.setupCamera)frame.right=frame.right.map(v=>-v);
   return frame;
  }
  orbitHorizontal(dx){return this.setupCamera?-dx:dx;}
  setMesh(values,origin){if(!this.gl)return;this.meshValues=values;this.meshOrigin=origin;if(!this.meshBuffer)this.meshBuffer=this.gl.createBuffer();this.upload(this.meshBuffer,values);this.meshCount=values.length/6;this.meshMode=$('job-mesh-display').value;this.updateMeshWire();this.draw();}
  updateMeshWire(){this.meshWireCount=0;if(!this.gl||!this.meshValues?.length||this.meshMode==='solid')return;if(!this.meshWireBuffer)this.meshWireBuffer=this.gl.createBuffer();const v=this.meshValues,w=new Float32Array(v.length*2);let k=0;for(let i=0;i<v.length;i+=18)for(const j of [0,6,6,12,12,0]){w[k++]=v[i+j];w[k++]=v[i+j+1];w[k++]=v[i+j+2];w[k++]=.16;w[k++]=.30;w[k++]=.37;}this.upload(this.meshWireBuffer,w);this.meshWireCount=w.length/6;}
  clear(){this.meshCount=0;this.meshWireCount=0;this.meshValues=null;this.pointValues=null;this.renderOwners=null;this.gpuPointValues=null;this.gpuPointCount=0;this.gpuFlags=null;this.pointVertexIds=null;this.uploadedPointValues=null;if(this.deletionBuffer){this.gl.deleteBuffer(this.deletionBuffer);this.deletionBuffer=null;}super.clear();$('job-mesh-info').textContent='';publishMeshPreview();}
  setPacked(values,origin,owners=null,edit=null){
   this.clearPointPages();
   const reuse=edit&&this.pointValues===edit.points&&this.gpuPointValues&&edit.flags?.length===this.pointValues.length/6,previousIds=this.pointVertexIds;
   // Keep ownership ranges alongside the displayed point buffer. Mesh vertices
   // stay in original for fitting/picking but never enter the GL_POINTS buffer.
   this.original=values;this.origin=origin;this.renderOwners=owners;this.pointValues=values;this.data={packed:true};this.pointOwnerRanges=[];this.packedScanRanges=null;
   if(owners){const scans=new Set(data.items.filter(x=>x.kind==='scan').map(x=>x.id)),ranges=owners.filter(row=>scans.has(row[2])),count=ranges.reduce((n,[a,b])=>n+b-a,0);let offset=0;
    if(count!==values.length/6){this.pointValues=new Float32Array(count*6);this.packedScanRanges=[];}
    for(const [a,b,id] of ranges){if(this.packedScanRanges)this.packedScanRanges.push([a,b,offset]);if(this.pointValues!==values)this.pointValues.set(values.subarray(a*6,b*6),offset*6);this.pointOwnerRanges.push([offset,offset+b-a,id]);offset+=b-a;}
   }else if(setupState&&!cleanSnapshot)this.pointOwnerRanges=[[0,values.length/6,setupState.id]];
   if(reuse){
    // CPU rows stay compact for picking and the next exact selection. Their
    // GPU vertex identities stay stable, so an edit only uploads small flags.
    const ids=new Uint32Array(this.pointValues.length/6);let next=0;
    for(let row=0;row<edit.flags.length;row++){const gpu=previousIds?previousIds[row]:row;if(edit.flags[row])this.gpuFlags[gpu]=3;else{ids[next++]=gpu;this.gpuFlags[gpu]=0;}}
    this.pointVertexIds=ids;this.flagsDirty=true;
   }else{this.gpuPointValues=this.pointValues;this.gpuPointCount=this.pointValues.length/6;this.pointVertexIds=null;this.gpuFlags=new Uint8Array(this.gpuPointCount);this.flagsDirty=true;}
   this.previewGeneration=(this.previewGeneration||0)+1;
   // After a committed edit, only publication of a fresh authoritative preview
   // releases the temporary hide. A failed refresh keeps the deleted display
   // points hidden until Retry, rather than flashing them back into the view.
   if(pendingPointDelete?.committed&&this.previewGeneration>pendingPointDelete.previewGeneration){pendingPointDelete=null;this.pendingDelete=false;}
   this.renderSelection();if(values.length&&!this.initialised&&this.c.clientWidth){this.fit();this.initialised=true;}
   if(hoverPixel&&window.MeasureTools?.hoverEnabled())queuePointHover();
  }
  gpuRowForPreview(row){
   // The inventory buffer can include mesh vertices interspersed with scans.
   // GPU scan rows exclude them; after local edits a second mapping preserves
   // the original vertex identities until the authoritative detail refresh.
   if(this.packedScanRanges){
    let low=0,high=this.packedScanRanges.length;
    while(low<high){const m=(low+high)>>>1;if(this.packedScanRanges[m][1]<=row)low=m+1;else high=m;}
    const r=this.packedScanRanges[low];if(!r||row<r[0])return -1;row+=r[2]-r[0];
   }
   return this.pointVertexIds?this.pointVertexIds[row]:row;
  }
  visiblePreviewRow(row,allowMesh=false){
   const gpu=this.gpuRowForPreview(row);
   if(gpu<0)return allowMesh;
   if(gpu===undefined||gpu%(this.cloudFallbackStep||1))return false;
   const flag=this.gpuFlags?.[gpu]||0;
   return flag<=2.5&&!(this.pendingDelete&&flag>1.5)&&(!this.spatialPoints||this.spatialPoints.contains(gpu));
  }
  renderSelection(){
   if(!this.gl||!this.original)return;const values=this.pointValues||this.original,flagSpec=pendingPointDelete?.selection||selectionSpec;
   // Unchanged display arrays can stay in GPU memory while controls/markers
   // refresh. Clear-selection and result recolouring used to upload twice.
   if(!selectionSpec&&!flagSpec&&!this.flagsDirty&&!this.selectionHighlightCount&&!this.deletionFlagCount&&this.uploadedPointValues===this.gpuPointValues){this.deletionFlags=null;this.pointCount=values.length/6;this.markers(targetMarkers);this.draw();drawStoredSelection();return;}
   const flags=flagSpec?new Uint8Array(values.length/6):null,eligible=new Set(pendingPointDelete?.ids||editableScans().map(x=>x.id)),ranges=this.pointOwnerRanges||[];let ownerIndex=0,selectedCount=0,highlightedCount=0;
   for(let row=0;row<values.length/6;row++){
    const gpu=this.pointVertexIds?this.pointVertexIds[row]:row;let highlighted=false,flagged=false;
    if(selectionSpec||flagSpec){const i=row*6,p=[values[i]+this.origin[0],values[i+1]+this.origin[1],values[i+2]+this.origin[2]];while(ownerIndex<ranges.length&&row>=ranges[ownerIndex][1])ownerIndex++;
     highlighted=selectedBySpec(p,selectionSpec);if(highlighted)highlightedCount++;
     const inside=flagSpec===selectionSpec?highlighted:selectedBySpec(p,flagSpec);flagged=inside&&!cleanSnapshot&&!deletedMode&&ranges[ownerIndex]&&row>=ranges[ownerIndex][0]&&eligible.has(ranges[ownerIndex][2]);
     if(flagged){flags[row]=1;selectedCount++;}
    }
    // 0 normal, 1 highlighted locked/result point, 2 editable selection,
    // 3 committed deletion. Previously deleted GPU rows are absent here.
    this.gpuFlags[gpu]=flagged?2:highlighted?1:0;
   }
   const old=this.deletionBuffer;this.deletionBuffer=this.gl.createBuffer();this.gl.bindBuffer(this.gl.ARRAY_BUFFER,this.deletionBuffer);this.gl.bufferData(this.gl.ARRAY_BUFFER,this.gpuFlags,this.gl.STATIC_DRAW);if(old)this.gl.deleteBuffer(old);
   this.deletionBufferCount=this.gpuFlags.length;this.deletionFlagCount=selectedCount;this.deletionFlags=flags;this.selectionHighlightCount=highlightedCount;this.flagsDirty=false;
   if(this.uploadedPointValues!==this.gpuPointValues){this.upload(this.buffer,this.gpuPointValues);this.uploadedPointValues=this.gpuPointValues;}
   this.pointCount=values.length/6;this.markers(targetMarkers);this.draw();drawStoredSelection();
  }

  fit(){if(this.setupCamera){this.yaw=0;this.pitch=0;this.setupCamera.fov=70;this.draw();return;}if(!this.original?.length)return;let lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(let i=0;i<this.original.length;i+=6)for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],this.original[i+j]);hi[j]=Math.max(hi[j],this.original[i+j]);}this.target=lo.map((v,j)=>(v+hi[j])/2);this.yaw=-.8;this.pitch=.4;const b=this.basis(),aspect=this.c.clientWidth/Math.max(1,this.c.clientHeight);let extent=.3;for(let i=0;i<this.original.length;i+=6){const x=this.original[i]-this.target[0],y=this.original[i+1]-this.target[1],z=this.original[i+2]-this.target[2];extent=Math.max(extent,Math.abs(x*b.right[0]+y*b.right[1]+z*b.right[2]),Math.abs(x*b.up[0]+y*b.up[1]+z*b.up[2])*aspect);}this.scale=extent*1.12;this.draw();drawStoredSelection();}
  markers(markers=[]){
   targetMarkers=markers||[];if(!this.origin)return;
   this.markerLabels=targetMarkers.filter(m=>m?.source?.every(Number.isFinite)&&m.status!=='rejected'&&window.SceneObjectsUI?.visible('target:'+m.key)!==false)
    .map(m=>({id:m.id,key:m.key,scanId:m.scanId,scanName:m.scanName,status:m.status,p:m.source.map((v,i)=>v-this.origin[i])}));
  }
  draw(){this.fullPointDetail=!!(previewOwner==='review'||previewOwner==='feature'||previewOwner==='shape'||deletedMode||selectionSpec||this.pendingDelete||this.pointVertexIds);this.pointOpacity=setupState&&setupDisplay==='both'?setupBlendValue:1;this.setupPhotoOpacity=setupPointsLoading?1:setupState&&setupDisplay==='both'?1-setupBlendValue:1;this.pointContrast=cleanSnapshot?1:displayContrast;this.pointGamma=cleanSnapshot?1:displayGamma;const box=!this.pointPagesActive&&!deletedMode&&liveLimitEditing&&$('limit-hide-outside')?.checked&&(limitPreview||current()?.limit_box);this.liveClip=box?window.TunnelBox.normalise(box):null;if(this.liveClip)this.liveClip={...this.liveClip,centre:this.liveClip.centre.map((v,i)=>v-previewOrigin[i])};window.CadUI?.beforeDraw();const old=$('point-size').value;$('point-size').value=$('job-point-size').value;super.draw();$('point-size').value=old;this.drawLabels();drawStoredSelection();drawSetupStations();window.CadUI?.afterDraw();window.MeasureTools?.draw?.();updateScale(this);window.SurfaceUI?.drawSegmentOverlay?.(worldToPixel,this.c.clientWidth,this.c.clientHeight);}
  drawLabels(){
   drawSetupPickPreview();
   const layer=$('job-marker-labels');if(!layer||!this.markerLabels)return;
   const labels=this.labelButtons??=new Map(),visible=new Set();
   if(targetNameEdit&&!targetOwnerVisible(targetNameEdit.scanId))closeTargetName();
   for(const m of this.markerLabels){
    const p=this.projectLocal(m.p);if(!p)continue;
    const x=(p[0]+1)*this.c.clientWidth/2,y=(1-p[1])*this.c.clientHeight/2;
    if(x<0||x>=this.c.clientWidth||y<0||y>=this.c.clientHeight)continue;
    const key=targetLabelKey(m);visible.add(key);
    let node=labels.get(key);
    if(!node){
     node=document.createElement('div');node.className='job-target-marker';
     const button=document.createElement('button');button.type='button';button.className='job-target-name';
     button.onclick=()=>openTargetName(button.targetMarker);
     node.append(button);labels.set(key,node);layer.append(node);
    }
    const button=node.firstElementChild,left=x>this.c.clientWidth*.7;
    button.targetMarker=m;
    if(button.textContent!==m.id)button.textContent=m.id;
    button.title='Rename '+m.id+' · '+m.scanName;
    button.disabled=busy;
    node.classList.toggle('target-selected',m.scanId===current()?.id&&targetSelection.has(m.key));
    node.classList.toggle('target-unreviewed',m.status!=='accepted');
    node.classList.toggle('target-label-left',left);
    node.style.left=x+'px';node.style.top=y+'px';
    if(targetNameEdit&&targetLabelKey(targetNameEdit)===key){
     targetNameEdit.form.style.left=Math.min(Math.max(4,x+7),Math.max(4,this.c.clientWidth-252))+'px';
     targetNameEdit.form.style.top=Math.min(Math.max(4,y-10),Math.max(4,this.c.clientHeight-75))+'px';
    }
   }
   for(const [key,button] of labels)if(!visible.has(key)){button.remove();labels.delete(key);}
   if(targetNameEdit&&!visible.has(targetLabelKey(targetNameEdit)))closeTargetName();
  }

 }
 const previewBadge=document.createElement('div');previewBadge.id='job-preview-status';previewBadge.className='job-preview-status';previewBadge.hidden=true;previewBadge.setAttribute('role','status');previewBadge.setAttribute('aria-live','polite');
 previewBadge.innerHTML='<span class="preview-spinner" aria-hidden="true"></span><span id="job-preview-status-text"></span><button id="job-preview-retry" hidden>Retry preview</button>';
 $('job-canvas').parentElement.append(previewBadge);
 let previewLayoutQueued=false;
 const previewPanels=[document.querySelector('.job-sidebar'),$('tool-drawer'),$('job-cad-nav')].filter(Boolean);
 function layoutPreviewBadge(){
  previewLayoutQueued=false;if(previewBadge.hidden)return;
  const view=$('job-canvas').parentElement.getBoundingClientRect(),margin=10,right=Math.max(margin,view.width-margin),bottom=Math.max(margin,view.height-margin);
  const clip=(n,a,b)=>Math.max(a,Math.min(b,n)),boxes=[];
  for(const panel of previewPanels){const style=getComputedStyle(panel),r=panel.getBoundingClientRect();if(panel.hidden||style.display==='none'||style.visibility==='hidden'||!r.width||!r.height||r.right<=view.left||r.left>=view.right||r.bottom<=view.top||r.top>=view.bottom)continue;boxes.push({left:clip(r.left-view.left-10,margin,right),right:clip(r.right-view.left+10,margin,right),top:clip(r.top-view.top-10,margin,bottom),bottom:clip(r.bottom-view.top+10,margin,bottom)});}
  const xs=[...new Set([margin,right,...boxes.flatMap(b=>[b.left,b.right])])].sort((a,b)=>a-b),ys=[...new Set([margin,bottom,...boxes.flatMap(b=>[b.top,b.bottom])])].sort((a,b)=>a-b),spaces=[];
  for(let a=0;a<xs.length;a++)for(let b=a+1;b<xs.length;b++)for(let c=0;c<ys.length;c++)for(let d=c+1;d<ys.length;d++){
   const r={left:xs[a],right:xs[b],top:ys[c],bottom:ys[d]},width=r.right-r.left,height=r.bottom-r.top;
   if(width<Math.min(150,right-margin)||height<Math.min(96,bottom-margin)||boxes.some(x=>r.left<x.right&&r.right>x.left&&r.top<x.bottom&&r.bottom>x.top))continue;
   spaces.push({...r,width,height,wide:width>=Math.min(480,right-margin)&&height>=Math.min(92,bottom-margin)});
  }
  spaces.sort((a,b)=>Number(b.wide)-Number(a.wide)||a.top-b.top||b.width-a.width||b.height-a.height);
  const free=spaces[0]||{left:margin,top:margin,width:Math.max(80,right-margin),height:Math.max(44,bottom-margin)},width=Math.min(840,free.width),height=Math.min(200,free.height),narrow=width<340,retry=$('job-preview-retry'),label=$('job-preview-status-text');
  Object.assign(previewBadge.style,{left:free.left+'px',top:free.top+'px',width:'max-content',maxWidth:width+'px',maxHeight:height+'px',boxSizing:'border-box',zIndex:spaces.length?'9':'100',alignItems:'flex-start',flexWrap:narrow?'wrap':'nowrap',overflow:'hidden',pointerEvents:'auto'});
  Object.assign(label.style,{minWidth:'0',flex:narrow?'1 1 calc(100% - 24px)':'1 1 180px',overflowWrap:'anywhere',overflow:'auto',maxHeight:Math.max(18,height-24-(narrow&&!retry.hidden?46:0))+'px'});label.tabIndex=0;
  Object.assign(retry.style,{flex:'none',maxWidth:'100%',whiteSpace:narrow?'normal':'nowrap',marginLeft:narrow?'auto':''});
 }
 function schedulePreviewLayout(){if(!previewBadge.hidden&&!previewLayoutQueued){previewLayoutQueued=true;requestAnimationFrame(layoutPreviewBadge);}}
 if(window.ResizeObserver){const observer=new ResizeObserver(schedulePreviewLayout);for(const panel of [...previewPanels,$('job-canvas').parentElement])observer.observe(panel);}
 const previewLayoutObserver=new MutationObserver(schedulePreviewLayout);for(const panel of previewPanels)previewLayoutObserver.observe(panel,{attributes:true,attributeFilter:['class','style','hidden']});previewLayoutObserver.observe(document.body,{attributes:true,attributeFilter:['class']});window.addEventListener('resize',schedulePreviewLayout);
 let previewClock=null,previewStarted=0;
 function previewFeedback(kind,text=''){
  clearInterval(previewClock);previewClock=null;previewBadge.dataset.state=kind;previewBadge.hidden=kind==='ready';$('job-canvas').setAttribute('aria-busy',kind==='loading'?'true':'false');$('job-preview-retry').hidden=kind!=='error';
  if(kind==='loading'){
   if(!previewStarted)previewStarted=performance.now();
   const render=()=>{$('job-preview-status-text').textContent=text+' · '+Math.floor((performance.now()-previewStarted)/1000)+' s elapsed';};render();previewClock=setInterval(render,1000);
  }else{previewStarted=0;$('job-preview-status-text').textContent=text;}
  schedulePreviewLayout();
 }
 function emptyPreviewText(){return !data.items.length?'No scans or models in this project. Import a scan to begin.':!setupState&&!data.items.some(x=>x.visible)?'No items are visible. Click a grey bulb in Scans & models to show an item.':'No points visible. Check the limit box and hidden regions, then retry the preview.';}
 function meshPreviewState(){return JSON.parse(JSON.stringify(meshStatus));}
 function publishMeshPreview(value={}){
  meshStatus={state:'empty',displayed_triangles:0,total_triangles:0,items:[],error:'',...value,mode:$('job-mesh-display').value};
  window.dispatchEvent(new CustomEvent('tunnel-mesh-preview',{detail:meshPreviewState()}));
 }
 function visibleMeshes(){return setupState?[]:data.items.filter(x=>x.visible&&x.kind==='mesh');}
 function pendingMeshState(state='loading',error=''){
  const items=visibleMeshes().map(x=>({id:x.id,name:x.name,state,displayed_triangles:0,total_triangles:Number(x.triangles)||0,simplified:false,error}));
  return {state:items.length?state:'empty',displayed_triangles:0,total_triangles:items.reduce((n,x)=>n+x.total_triangles,0),items,error:items.length?error:''};
 }
 function finishPreviewFeedback(){
  const failed=['partial','error'].includes(meshStatus.state);
  previewFeedback(failed?'error':'ready',failed?(meshStatus.state==='partial'?'Some surfaces could not be displayed. ':'Solid surface preview unavailable. ')+meshStatus.error:'');
  if(!jobScene.pointCount&&!jobScene.meshCount&&!jobScene.setupBackdrop?.ready()&&!cleanSnapshot){$('job-empty').hidden=false;$('job-empty').textContent=failed?'Triangle faces could not be displayed. Read the preview message above, then retry.':emptyPreviewText();}
 }
 function retryMeshPreview(){clearPreviewCaches();signature='';previewRequested=true;if(loading){previewRefreshPending=true;return Promise.resolve(false);}return sync();}
 $('job-preview-retry').onclick=retryMeshPreview;
 const jobScene=new JobScene($('job-canvas'),{adaptive:true});
 const indexStatus=document.createElement('span'),indexMessage=document.createElement('span'),stopIndex=document.createElement('button');
 indexStatus.hidden=true;indexMessage.setAttribute('role','status');stopIndex.type='button';stopIndex.textContent='Stop';
 stopIndex.title='Stop optimising this preview';
 indexStatus.append(indexMessage,document.createTextNode(' '),stopIndex);$('job-preview-info').after(indexStatus);
 stopIndex.onclick=()=>{jobScene.spatialPoints?.reset();jobScene.draw();};
 jobScene.onPointIndexProgress=(percent,error)=>{
  if(error){notice('Using the standard point preview. '+error);return;}
  indexStatus.hidden=percent===null||percent===100;
  if(!indexStatus.hidden)indexMessage.textContent='Organising visible point detail · '+percent+'%';
 };
 function setMeshDisplay(mode){if(!['solid','both','wire'].includes(mode))return false;$('job-mesh-display').value=mode;jobScene.meshMode=mode;jobScene.updateMeshWire();jobScene.draw();publishMeshPreview(meshStatus);return true;}
 $('job-mesh-display').onchange=()=>setMeshDisplay($('job-mesh-display').value);
 let featurePickGeneration=0;
 function cancelFeatureSeed(){featurePickGeneration++;if(pointPickCallback){pointPickCallback=null;setMode('orbit');}}
 function clearFeaturePreview(){if(previewOwner==='feature')clearCleaningPreview('feature');jobScene.draw();}
 function showFeaturePreview(result){
  if(!result?.preview?.points)return;
  const p=result.preview,orientation={yaw:jobScene.yaw,pitch:jobScene.pitch};
  analysisPreview({...p,result_id:result.id,sample_count:p.preview_count||p.points.length,used:result.statistics?.local_points||p.count},'feature');
  jobScene.yaw=orientation.yaw;jobScene.pitch=orientation.pitch;
  $('job-review-legend').hidden=true;
  $('job-preview-info').textContent=`LOCAL ${result.mode==='bolts'?'BOLT / FITTING':'REGION'} REVIEW · orange selected / cyan candidate / grey surrounding · ${fmt(p.preview_count||p.points.length,0)} displayed of ${fmt(result.statistics.local_points,0)} local points · ${fmt(result.statistics.selected_points,0)} original points selected.`;
  busyUI(busy);jobScene.draw();
 }
 function pickFeatureSeed(callback,options={}){
  clearFeaturePreview();
  if(window.CadUI?.modelSpace?.()&&window.CadUI.enter('Clean',{openTool:false})===false)return false;
  const generation=++featurePickGeneration;
  return armPointPick(async world=>{
   if(generation!==featurePickGeneration)return;
   try{
    const screen=worldToPixel(world),sample=screen?nearestDisplayedPoint(screen,true):null;
    if(!sample||options.source_id&&sample.source_id!==options.source_id)throw Error('Pick a visible retained point on the selected source scan. Hide other scans with their bulbs if they cover it.');
    let direction=setupState?jobScene.target.map((v,i)=>v+previewOrigin[i]-world[i]):jobScene.basis().forward.slice();const length=Math.hypot(...direction);if(!(length>0))throw Error('The viewing direction could not be determined.');direction=direction.map(v=>v/length);
    const cad=await api('/api/cad');setBusy(true);await post('/api/cad-pick',{...sample,revision:cad.revision,context:cad.context});
    const job=await waitReady(null,{skipSync:true});if(job.cancelled)return;
    const recovered=await api('/api/cad-pick');if(!recovered.point)throw Error('No original retained point was recovered. Load detail and pick again.');
    if(generation===featurePickGeneration)await callback({...recovered,view_direction:direction});
   }catch(error){notice(error.message,true);}finally{setBusy(false);}
  },{prompt:options.prompt||'Pick a seed on the selected scan',done:'Original seed point recovered'});
 }
 function sceneSnapshot(){return {contentKey:previewContentKey(),complete:pointPreviewComplete,view:detailView,packed,origin:previewOrigin.slice(),mesh:jobScene.meshValues,owners:jobScene.renderOwners,meshState:meshPreviewState(),info:$('job-preview-info').textContent};}
 function clearCleaningPreview(owner=null){if(owner&&previewOwner!==owner)return;deletedMode=false;previewOwner=null;previewSource=null;previewResult=null;window.HeatUI?.hideLegend();window.InspectionUI?.hideLegend();$('job-review-legend').hidden=true;if($('job-back-deleted'))$('job-back-deleted').hidden=true;if($('job-restore-selection'))$('job-restore-selection').hidden=true;if($('job-export-deleted-selection'))$('job-export-deleted-selection').hidden=true;if(!cleanSnapshot)return;const world=jobScene.target.map((v,i)=>v+previewOrigin[i]),old=cleanSnapshot;cleanSnapshot=null;pointPreviewComplete=old.complete!==false;packed=old.packed;pointOwners=old.owners||[];previewOrigin=old.origin;jobScene.target=world.map((v,i)=>v-previewOrigin[i]);jobScene.setPacked(packed||new Float32Array(),previewOrigin,old.owners);jobScene.setMesh(old.mesh||new Float32Array(),previewOrigin);publishMeshPreview(old.meshState);$('job-preview-info').textContent=old.info;$('job-empty').hidden=!!packed?.length;clearSelection();if(!pointPreviewComplete){detailView=setupState?setupDetail():old.view?{target:jobScene.target.map((v,i)=>v+previewOrigin[i]),...jobScene.basis(),scale:jobScene.scale,aspect:canvas.clientWidth/Math.max(1,canvas.clientHeight)}:null;signature='';previewRequested=true;previewFeedback('loading','Restoring scan detail');if(loading)previewRefreshPending=true;else queueMicrotask(sync);}else finishPreviewFeedback();updatePointPreviewInfo();}
 function analysisPreview(p,owner='design'){
  const sameLayer=previewOwner===owner&&previewResult===(p.result_id||p.id||null),base=cleanSnapshot||sceneSnapshot();
  stopSceneLoadForOverlay();deletedMode=false;
  if(previewOwner!==owner){window.HeatUI?.hideLegend();window.InspectionUI?.hideLegend();}
  $('job-review-legend').hidden=true;if($('job-back-deleted'))$('job-back-deleted').hidden=true;$('job-restore-selection').hidden=true;
  previewOwner=owner;previewSource=p.source_id||current()?.id;previewResult=p.result_id||p.id||null;
  cleanSnapshot=base;publishMeshPreview();clearSelection(false);
  const world=jobScene.target.map((v,i)=>v+previewOrigin[i]);packed=window.TunnelDisplayBuffers.packed(p);previewOrigin=p.origin.slice();
  jobScene.target=world.map((v,i)=>v-previewOrigin[i]);jobScene.setMesh(new Float32Array(),previewOrigin);jobScene.setPacked(packed,previewOrigin);if(!sameLayer)jobScene.fit();else jobScene.draw();
  $('job-empty').hidden=!!packed.length;
  $('job-preview-info').textContent=p.colour_mode==='ramp'?`3D DEVIATION · continuous colouring · scale ${p.signed?'±':'0 to '}${p.tolerance_mm} mm · ${fmt(p.sample_count,0)} displayed of ${fmt(p.used,0)} compared points.`:`CLOUD VS DESIGN · ${p.signed?'blue below / ':''}green within / red above ${p.tolerance_mm} mm tolerance · ${fmt(p.sample_count,0)} displayed of ${fmt(p.used,0)} compared points.`;
  if(owner==='inspection')$('job-preview-info').textContent=`COMPARE / INSPECT · ${p.mode_label||'Object comparison'} · ${p.signed?'oriented':'unsigned'} distances · ${fmt(p.sample_count,0)} displayed of ${fmt(p.used,0)} compared samples. ${p.colour_mode==='ramp'?'Continuous colour scale':'Tolerance bands'} ${p.tolerance_mm} mm.`;
  if(p.reference_label)$('job-preview-info').textContent=p.reference_label+' · '+(p.reference_name||'')+' · '+$('job-preview-info').textContent;
 }
 function stopSceneLoadForOverlay(){clearTimeout(detailTimer);previewRequest?.controller.abort();previewRefreshPending=false;previewFeedback('ready');}
 function cleaningPreview(p,kept,options={}){
  if(deletedMode)return;
  stopSceneLoadForOverlay();
  if(!p?.points)return;if(previewOwner&&previewOwner!=='review')clearCleaningPreview();previewOwner='review';const keep=new Set(kept),hidden=new Set(options.hidden||[]),chosen=new Set(options.selected||[]),only=options.onlySelected?new Set(options.onlySelected):null,first=!cleanSnapshot,world=jobScene.target.map((v,i)=>v+previewOrigin[i]);
  if(first){cleanSnapshot=sceneSnapshot();publishMeshPreview();}
  const values=[];cleanPointLabels=[];
  for(const q of p.points){const use=keep.has(q[3]);if(hidden.has(q[3])||(only&&!only.has(q[3])))continue;if(options.isolate&&options.inspected!==null&&q[3]!==options.inspected)continue;if(use?!options.showKept:q[3]===-1?!options.showNoise:!options.showOther)continue;
   const c=chosen.has(q[3])?[1,85/255,221/255]:use?(options.colours?.[q[3]]||[.32,.82,.64]):[1,.25,.25];values.push(...q.slice(0,3),...c);cleanPointLabels.push(q[3]);}
  packed=new Float32Array(values);previewOrigin=p.origin.slice();jobScene.target=world.map((v,i)=>v-previewOrigin[i]);jobScene.setMesh(new Float32Array(),previewOrigin);jobScene.setPacked(packed,previewOrigin);
  if(first){clearSelection();setMode('orbit');jobScene.fit();}
  $('job-review-legend').hidden=false;const legend=$('job-review-legend').querySelectorAll('span');if(legend.length>=3){legend[1].innerHTML='<i class="surface-dot kept"></i>'+(options.classification&&options.colours?(options.colourMode==='rings'?'Ring colours / grey unresolved':'Cluster colours / grey unresolved'):options.colours?'Feature colours / retained':'Tunnel / keep');legend[2].innerHTML='<i class="surface-dot removed"></i>'+(options.colours?'Excluded':'Noise / remove');}
  $('job-empty').hidden=!!packed.length;if(!packed.length)$('job-empty').textContent='No review points visible · choose Show all clusters';
  $('job-preview-info').textContent=`${options.classification&&options.colours?'TUNNEL CLASSIFICATION · '+(options.colourMode==='rings'?'rings':'clusters')+' / grey unresolved / red excluded / magenta selected':options.colours?'LINING FEATURES · coloured groups / red excluded / magenta selected':'CLEANING PREVIEW · green tunnel / red noise or unwanted / magenta selected'} · ${fmt(packed.length/6,0)} displayed of ${fmt(p.source_count,0)} processed points. Click clusters to select; Box/Lasso edits part of a cluster.`;busyUI(busy);
 }
 function focusCleaningCluster(p,id,centre){
  const points=p?.points?.filter(q=>q[3]===id)||[];
  if(!points.length){if(centre?.every(Number.isFinite)){jobScene.target=centre.map((v,i)=>v-previewOrigin[i]);jobScene.scale=Math.max(.25,jobScene.scale*.15);jobScene.draw();}notice('This cluster is absent from the display sample. Its full-density count and Keep/Remove controls remain available.',true);return false;}
  const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const q of points)for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],q[j]);hi[j]=Math.max(hi[j],q[j]);}
  const mid=lo.map((v,j)=>(v+hi[j])/2);jobScene.target=mid.map((v,j)=>v+p.origin[j]-previewOrigin[j]);
  const b=jobScene.basis(),aspect=canvas.clientWidth/Math.max(1,canvas.clientHeight);let extent=.05;
  for(const q of points){const d=q.slice(0,3).map((v,j)=>v-mid[j]);extent=Math.max(extent,Math.abs(dot(d,b.right)),Math.abs(dot(d,b.up))*aspect);}
  jobScene.scale=extent*1.2;jobScene.draw();return true;
 }
 function nearestCleaningCluster(pixel){
  const b=jobScene.basis(),r=canvas.getBoundingClientRect();let found=null,distance=144,depth=-Infinity;
  for(let i=0;i<cleanPointLabels.length;i++){const j=i*6,d=[packed[j]-jobScene.target[0],packed[j+1]-jobScene.target[1],packed[j+2]-jobScene.target[2]],x=(dot(d,b.right)/jobScene.scale+1)*r.width/2,y=(1-dot(d,b.up)*r.width/Math.max(1,r.height)/jobScene.scale)*r.height/2,dd=(x-pixel[0])**2+(y-pixel[1])**2,z=dot(d,b.forward);
   if(dd<distance-1||(dd<=distance&&z>depth)){found=cleanPointLabels[i];distance=dd;depth=z;}}
  return found;
 }
 function density(){return Math.max(1,Math.min(100,+$('job-density').value||35));}
 function densityUI(){const d=density();$('job-density-value').textContent=d+'%';if($('ribbon-density'))$('ribbon-density').value=d;if($('ribbon-density-value'))$('ribbon-density-value').textContent=d+'%';$('job-density-value').title=d>80?'High detail uses more GPU/browser memory. Full survey data remains on disk.':'Percentage controls display detail only; processing still uses full-density data.';}
 function mergeParent(x){return data.items.find(p=>p.id===x.merge_parent&&p.registered_merge);}
 function registrationDetails(x){
  if(x.registered_merge){
   const changed=x.merge_source_changes,edited=changed?.point_edits?.length||0,removed=changed?.removed?.length||0;
   const title=x.merge_stale?'Archived result — registration has changed':x.merge_archived?'Previous registered result':changed?'Saved registered cloud':'Registration complete';
   const note=changed?`<p>${edited?`${edited} original setup${edited===1?' has':'s have'} point edits. `:''}${removed?`${removed} original setup${removed===1?' was':'s were'} removed. `:''}This cloud and its saved results keep the data used when it was created. Whole-bundle analysis uses the current source setups.</p>`:'';
   return `<p><b>${title}</b> · ${(x.merge_source_ids||[]).length} setups combined. The stored cloud is independent. Its remaining source setups are listed in their bundle.</p>${note}`;
  }
  const r=x.registration_summary;if(x.kind!=='scan'||!r)return '';
  const names=(r.targets||[]).map(t=>esc(t.name)+(t.role==='off'?' (excluded)':'')).join(', ')||'No named targets saved.';
  if(mergeParent(x))return `<section class="scan-registration-summary"><details class="scan-target-list"><summary>Targets · ${(r.targets||[]).length}</summary><p>${names}</p></details></section>`;
  const links=(r.links||[]).map(l=>{
   const pairs=(l.matches||[]).map(m=>`<li>${esc(m.target)} ↔ ${esc(m.other_target)} <small>${esc(({fit:'Fit',check:'Check',off:'Excluded',leica:'Leica recorded pair'})[m.role]||m.role)}${Number.isFinite(m.error_mm)?' · '+fmt(m.error_mm,3)+' mm':''}</small></li>`).join('');
   const missing=l.pairs_recorded?'No target pairs recorded for this link.':'Target pairs were not recorded by the older import.';
   const shared=!l.pairs_recorded&&l.shared_names?.length?`<p>Shared names (use in this link unconfirmed): ${l.shared_names.map(esc).join(', ')}</p>`:'';
   return `<div class="scan-link-summary"><button data-detail-link="${esc(l.id)}" title="Review this registration link">${esc(l.other_name)}</button><p>${esc(l.status||'Saved link')}${Number.isFinite(l.rms_mm)?' · Fit RMS '+fmt(l.rms_mm,3)+' mm':''}</p>${l.method?`<p>${esc(l.method)}</p>`:''}<b>${l.kind==='leica'?'Leica saved target pairs':'Targets used'}</b>${pairs?'<ul>'+pairs+'</ul>':'<p>'+missing+'</p>'}${shared}${l.kind==='leica'?'<p>No independently recomputed target residuals.</p>':''}</div>`;
  }).join('')||'<p>No registration links saved for this scan.</p>';
  return `<section class="scan-registration-summary"><h4>Linked scans · ${(r.links||[]).length}</h4>${links}<details class="scan-target-list" ${(r.targets||[]).length<=12?'open':''}><summary>Targets · ${(r.targets||[]).length}</summary><p>${names}</p></details></section>`;
 }
 function bindDetailLinks(el){for(const b of el.querySelectorAll('[data-detail-colour]'))b.onclick=()=>window.ScanAppearanceUI?.open(b.dataset.detailColour);for(const b of el.querySelectorAll('[data-detail-setup]'))b.onclick=()=>window.ScanRegistrationUI?.openSetup(b.dataset.detailSetup);for(const b of el.querySelectorAll('[data-detail-link]'))b.onclick=()=>window.ScanRegistrationUI?.openLinks(b.dataset.detailLink);}
 function details(){if(window.SceneObjectsUI?.selected())return;const x=current(),el=$('job-item-details');if(!x){el.textContent='Select an item to see its information.';return;}const count=x.kind==='scan'?`${x.count_exact===false?(x.metadata_pending?'Checking remaining-point count…':'Remaining-point count unavailable'):fmt(x.count,0)+' remaining points · '+fmt(x.excluded_count||0,0)+' excluded'}<br>${fmt(x.original_count??x.count,0)} original points · ${fmt((x.deleted_groups||[]).reduce((n,g)=>n+g.count,0),0)} in Deleted points`:`${fmt(x.vertices,0)} vertices · ${fmt(x.triangles,0)} triangles`,type=x.kind==='scan'?(x.cleaned?'Tunnel scan · kept cleaning result':'Point-cloud scan'):'Reference mesh',bounds=x.minimum&&x.maximum?`<br>${x.bounds_exact===false?'Original scan extent (remaining points are within this)':'Extent'} E/X ${fmt(x.minimum[0],3)}–${fmt(x.maximum[0],3)} · N/Y ${fmt(x.minimum[1],3)}–${fmt(x.maximum[1],3)} · H/Z ${fmt(x.minimum[2],3)}–${fmt(x.maximum[2],3)}`:'';el.innerHTML=`<b>${esc(x.name)}</b>${x.experimental_reconstruction?'<p class="tiny"><b>Experimental raw reconstruction</b><br>Factory calibration is unverified. Check against trusted survey data before measurement use.</p>':''}${x.has_setup_view?`<div><button data-detail-setup="${esc(x.id)}">Go to setup view</button></div>`:''}<br>Type: ${type}${x.rtc360_setup_id?`<br>Scanner label: ${esc(x.rtc360_original_name??'Not recorded')}<br>Setup ID: ${esc(x.rtc360_setup_id)}`:''}<br>${esc(x.position_status||'')}<br>${count}<br>${x.rgb_available?'RGB colour available':'No complete RGB triplet'}${x.intensity_available?' · intensity available':''}<br>${x.edit_count||0} point edits · ${x.hidden_regions||0} hidden regions${x.isolated?' · view isolated':''}<br>Original file: ${gb(x.source_bytes||0)} · working cache: ${gb(x.cache_bytes||0)}${bounds}${x.mesh_summary?`<hr><b>${esc(x.mesh_summary.method||'Triangle mesh')}</b><br>${fmt(x.mesh_summary.boundary_edges,0)} boundary edges · ${fmt(x.mesh_summary.nonmanifold_edges,0)} non-manifold edges<br>Surface area ${fmt(x.mesh_summary.area_m2,3)} m²${x.mesh_summary.triangle_quality_mean!=null?'<br>Triangle Q mean / minimum: '+fmt(x.mesh_summary.triangle_quality_mean,3)+' / '+fmt(x.mesh_summary.triangle_quality_min,3)+' (1 = equilateral)':''}<br>Sampled mesh-to-cloud RMS ${fmt(x.mesh_summary.mesh_sample_to_cloud_rms_m,4)} m${x.mesh_summary.reconstructed_triangles?'<br>'+fmt(x.mesh_summary.reconstructed_triangles,0)+' reconstructed triangles · orange in the full mesh view':''}<br>${esc(x.mesh_summary.warning||'')}<br><small>${esc(x.mesh_summary.topology_note||'')}</small>`:''}`;const summary=registrationDetails(x),split=el.innerHTML.indexOf('<br>Type:');if(summary&&split>=0){const html=el.innerHTML;el.innerHTML=html.slice(0,split)+summary+'<details class="scan-point-properties"><summary>Point properties</summary>'+html.slice(split+4)+'</details>';}if(x.kind==='scan')el.insertAdjacentHTML('afterbegin',`<p><button data-detail-colour="${esc(x.id)}">Scan colour…</button> ${esc(x.display_mode||'Follow global display')}</p>`);bindDetailLinks(el);}
 const infoDialog=document.createElement('dialog');infoDialog.id='scene-info-dialog';infoDialog.innerHTML='<div class="dialog-heading"><h2>Scan / model information</h2><button id="scene-info-close" aria-label="Close">×</button></div><div id="scene-info-body"></div>';document.body.append(infoDialog);$('scene-info-close').onclick=()=>infoDialog.close();const listMenu=document.createElement('div');listMenu.id='scene-list-menu';listMenu.className='job-context-menu';listMenu.hidden=true;document.body.append(listMenu);document.addEventListener('pointerdown',e=>{if(!listMenu.contains(e.target))listMenu.hidden=true;});document.addEventListener('keydown',e=>{if(e.key==='Escape')listMenu.hidden=true;});
 function chooseItem(id,event={}){if(busy)return;if(repeatPick&&id!==repeatPick.scanId)setMode('orbit');deletedGeneration++;const owner=mergeParent(data.items.find(x=>x.id===id)||{});if(owner)expandedScans.add(owner.id);if(setupState&&setupState.id!==id){if(data.items.find(x=>x.id===id)?.registered_merge)window.SetupCameraUI?.exit(false);else{window.SetupCameraUI?.open(id);return;}}if(!event.ctrlKey&&!event.metaKey)window.SceneObjectsUI?.clearSelected();window.WorkspaceUI?.clearBundleSelection?.();clearCleaningPreview();if(event.shiftKey&&selectionAnchor){const a=data.items.findIndex(x=>x.id===selectionAnchor),b=data.items.findIndex(x=>x.id===id);if(!event.ctrlKey&&!event.metaKey)selectedItems.clear();for(const x of data.items.slice(Math.min(a,b),Math.max(a,b)+1))selectedItems.add(x.id);}else if(event.ctrlKey||event.metaKey){selectedItems.has(id)?selectedItems.delete(id):selectedItems.add(id);selectionAnchor=id;}else{selectedItems=new Set([id]);selectionAnchor=id;}selected=selectedItems.has(id)?id:[...selectedItems].at(-1)||null;window.TargetUI?.selectTargets([]);clearSelection();table();busyUI(busy);}
 function showDetails(){details();const d=$('scene-info-dialog');$('scene-info-body').innerHTML=$('job-item-details').innerHTML;bindDetailLinks($('scene-info-body'));d.showModal();}
 async function compactSelected(){return window.PermanentPointsUI?.open(current()?.id);}
 async function removeSelected(){const references=window.SceneObjectsUI?.selectedIds()||[],ids=[...selectedItems,...(window.CadUI?.selectedIds()||[]),...references.filter(id=>window.SceneObjectsUI.find(id)?.removable)];if(ids.length)return window.ExplorerDeleteUI?.open({ids});if(references.length)return false;const bundle=window.WorkspaceUI?.selectedBundleId?.();if(bundle)return window.ExplorerDeleteUI?.open({bundle_ids:[bundle]});return false;}

 function sceneMenu(e,id){if(window.WorkspaceUI)return window.WorkspaceUI.itemMenu(e,id);e.preventDefault();if(busy)return;if(!selectedItems.has(id))chooseItem(id);else selected=id;const menu=$('scene-list-menu'),x=current();const entries=[['More information',showDetails],['Rename',()=>$('job-rename').click()],[x?.visible?'Hide item':'Show item',()=>action({action:'visibility',id})],['Isolate selection',()=>$('job-isolate').click()],['Show all items',()=>$('job-show-all').click()],['Duplicate item',()=>action({action:'duplicate',id})]];if(x?.has_setup_view)entries.push(['Go to setup view',()=>window.ScanRegistrationUI?.openSetup(x.id)]);if(x?.kind==='scan')entries.push(['Clean Tunnel',()=>$('job-surface').click()],['Create mesh',()=>window.TunnelRibbon.view('structures')],['Registration',()=>$('job-register').click()],['Survey analysis',()=>$('job-analyse').click()]);entries.push([`Delete selected (${selectedItems.size})`,removeSelected]);menu.innerHTML=entries.map(([label],i)=>`<button data-scene-command="${i}">${esc(label)}</button>`).join('');for(const b of menu.querySelectorAll('button'))b.onclick=()=>{menu.hidden=true;entries[+b.dataset.sceneCommand][1]();};menu.hidden=false;menu.style.left=Math.max(4,Math.min(innerWidth-230,e.clientX))+'px';menu.style.top=Math.max(4,Math.min(innerHeight-entries.length*33-15,e.clientY))+'px';}
 function deletedRows(){const groups=data.items.filter(x=>x.kind==='scan').flatMap(x=>(x.deleted_groups||[]).map(g=>({...g,owner:x.id,source:x.name})));const valid=new Set(groups.map(g=>g.owner+'|'+g.id));deletedSelection=new Set([...deletedSelection].filter(k=>valid.has(k)));return `<tr class="deleted-row" id="job-deleted-root"><td colspan="3"><details data-deleted-folder="all" ${deletedFolders.has('all')?'open':''}><summary data-view-all-deleted>Deleted points (${fmt(groups.reduce((n,g)=>n+g.count,0),0)})</summary>${data.items.some(x=>x.kind==='scan'&&(x.excluded_count>0||(x.deleted_groups||[]).some(g=>g.archived&&g.count>0)))?'<button data-permanent-delete title="Review permanently removing excluded points from a working cloud">Permanently remove…</button>':''}${groups.length?`<button data-show-all-deleted>Show all deleted points</button><p class="tiny">Tick groups to view together. Draw a box, lasso, polygon or brush in the orange view, then Restore selected points. Cleaning exclusions restore as a separate scan.</p>`+groups.map(g=>`<div class="deleted-group"><label><input type="checkbox" data-select-deleted="${esc(g.owner+'|'+g.id)}" ${deletedSelection.has(g.owner+'|'+g.id)?'checked':''}><b>${esc(g.name)}</b></label><small>Source: ${esc(g.source)} · ${fmt(g.count,0)} points</small><button data-preview-deleted="${esc(g.id)}" data-owner="${esc(g.owner)}">View</button><button data-restore-deleted="${esc(g.id)}" data-owner="${esc(g.owner)}">${g.archived?'Restore as scan':'Restore'}</button></div>`).join(''):'<small>No deleted points. Moved points remain in their separated scan.</small>'}</details></td></tr>`;}


 async function viewDeleted(id,group,all=false,keepCamera=false){
  if(busy)return;if(window.CadUI?.modelSpace()&&!window.CadUI.enter('Edit'))return;
  window.TunnelRibbon?.closeTool();if(setupState)window.SetupCameraUI?.exit(false);
  stopSceneLoadForOverlay();deletedMode=true;
  if(all)deletedSelection=new Set(data.items.flatMap(x=>(x.deleted_groups||[]).map(g=>x.id+'|'+g.id)));
  else if(id&&group)deletedSelection=new Set([id+'|'+group]);
  const choices=[...deletedSelection].map(k=>k.split('|')),generation=++deletedGeneration;
  const world=jobScene.target.map((v,i)=>v+previewOrigin[i]);deletedLoading=true;busyUI(busy);
  try{
   const p=await post('/api/deleted-preview',{groups:choices});if(generation!==deletedGeneration)return;
   clearCleaningPreview();deletedMode=true;previewOwner='deleted';deletedPreviewGroups=choices;deletedBounds=p.bounds||[];
   cleanSnapshot=sceneSnapshot();publishMeshPreview();previewFeedback('ready');packed=new Float32Array(p.points.flat());
   previewOrigin=p.origin;jobScene.setMesh(new Float32Array(),previewOrigin);jobScene.setPacked(packed,previewOrigin);
   clearSelection();if(keepCamera){jobScene.target=world.map((v,i)=>v-previewOrigin[i]);jobScene.draw();}else{jobScene.fit();setMode('box');}
   $('job-empty').hidden=!!packed.length;
   if(!packed.length)$('job-empty').textContent='No deleted points remain in these groups. Use Back to scan to view restored points.';
   $('job-preview-info').textContent=`DELETED POINTS · ${p.name} · ${fmt(p.count,0)} source points · Draw an area, then Restore selected points. Selection includes full-density points through the view. Cleaning exclusions restore as a separate scan.`;
   $('job-back-deleted').hidden=false;$('job-restore-selection').hidden=false;$('job-export-deleted-selection').hidden=false;table();
  }catch(e){if(!cleanSnapshot)deletedMode=false;notice(e.message,true);}
  finally{if(generation===deletedGeneration){deletedLoading=false;busyUI(busy);}}
 }
 async function restoreDeletedSelection(){
  if(busy||deletedLoading||!deletedMode||!selectionSpec||!deletedPreviewGroups.length)return false;
  const groups=deletedPreviewGroups.map(pair=>pair.slice()),selection=JSON.parse(JSON.stringify(selectionSpec));
  if(cleanSnapshot)cleanSnapshot.complete=false;
  const ok=await action({action:'restore_deleted_selection',groups,selection},null,{onProgress:job=>window.WorkStatus?.show('Restoring selected points',job.message,!!job.busy)});
  if(!ok)return false;
  await viewDeleted(null,null,false,true);
  window.WorkStatus?.show('Selected points restored','Unselected points stay deleted. Back to scan shows the restored points. Undo reverses this restoration.');return true;
 }
 $('job-restore-selection').onclick=restoreDeletedSelection;
 function exportDeletedSelection(){
  if(busy||deletedLoading||!deletedMode||!selectionSpec||!deletedPreviewGroups.length)return false;
  const groups=deletedPreviewGroups.map(pair=>pair.slice());
  window.ExportUI.openHighlight({ids:[...new Set(groups.map(pair=>pair[0]))],deleted_groups:groups,selection:JSON.parse(JSON.stringify(selectionSpec))});return true;
 }
 $('job-export-deleted-selection').onclick=exportDeletedSelection;



 function connectionRow(x){
  if(x.kind!=='scan'||!expandedScans.has(x.id))return '';
  const links=x.registration_summary?.links||[];
  return `<tr class="scan-connections-row" id="scan-connections-${esc(x.id)}"><td colspan="3"><div class="scan-connections" aria-label="Connections for ${esc(x.name)}">${links.length?'<ul>'+links.map(l=>`<li><button data-scan-connection="${esc(l.id)}">${esc(l.other_name)}</button><small>${esc(l.status||'Saved connection')}${Number.isFinite(l.rms_mm)?' · RMS '+fmt(l.rms_mm,3)+' mm':''}</small><details><summary>Target pairs (${(l.matches||[]).length})</summary>${l.pairs_recorded===false?'<p>Target pairs were not recorded for this connection.</p>':'<ul>'+(l.matches||[]).map(m=>'<li>'+esc(m.target)+' ↔ '+esc(m.other_target)+' · '+esc(m.role==='off'?'Excluded':m.role==='check'?'Check':m.role==='leica'?'Recorded':'Fit')+'</li>').join('')+'</ul>'}</details></li>`).join('')+'</ul>':'<small>No connections yet. Use Match two scans to add one.</small>'}</div></td></tr>`;
 }
 function itemRow(x,depth=0){
  const children=data.items.filter(c=>c.merge_parent===x.id&&c.id!==x.id&&!(data.bundles||[]).some(b=>b.id===c.bundle_id)),expandable=children.length>0||(x.kind==='scan'&&!x.registered_merge);
  const expanded=expandedScans.has(x.id),label=x.registered_merge?'original setups':'connections';
  const countLabel=x.kind==='scan'?(x.count_exact===false?'Up to ':'')+fmt(x.count,0)+' points'+(x.metadata_pending?' · counting…':''):fmt(x.triangles,0)+' triangles';
  const row=`<tr data-scene-item="${esc(x.id)}" tabindex="0" aria-selected="${selectedItems.has(x.id)}" class="explorer-object-row ${selectedItems.has(x.id)?'job-selected':''} ${depth?'merged-setup-row':''}"><td class="explorer-select"><input type="checkbox" aria-label="Select ${esc(x.name)}" data-item="${esc(x.id)}" ${selectedItems.has(x.id)?'checked':''}></td><td class="explorer-visibility"><button class="visibility-toggle" data-visible="${esc(x.id)}" ${x.merge_stale?'disabled':''} aria-pressed="${!!x.visible}" aria-label="${x.visible?'Hide':'Show'} ${esc(x.name)}" title="${x.visible?'Visible · click to hide':'Hidden · click to show'}">${window.ToolIcons.bulb(!!x.visible)}</button></td><td class="explorer-item" style="--explorer-depth:${Math.min(depth,3)}"><div class="explorer-item-line">${expandable?`<button class="scan-expand" data-scan-expand="${esc(x.id)}" aria-expanded="${expanded}" ${x.registered_merge?'':`aria-controls="scan-connections-${esc(x.id)}"`} aria-label="${expanded?'Hide':'Show'} ${label} for ${esc(x.name)}">${expanded?'▾':'▸'}</button>`:'<span class="explorer-expander-placeholder" aria-hidden="true"></span>'}<b class="explorer-item-name" title="${esc(x.name)}">${esc(x.name)}</b>${x.kind==='scan'?`<button type="button" class="scan-colour-swatch" data-scan-appearance="${esc(x.id)}" aria-label="Scan colour for ${esc(x.name)}" title="Scan colour · ${esc(x.display_mode||'global')}" style="--scan-swatch:${/^#[0-9a-f]{6}$/i.test(x.display_colour||'')?x.display_colour:'#5e8494'}">◐</button>`:''}</div><small class="explorer-item-meta" title="${esc(countLabel)}${x.excluded_count?' · '+fmt(x.excluded_count,0)+' excluded points':''}"><span class="explorer-item-count">${depth&&!x.registered_merge?'Setup · ':''}${countLabel}</span>${x.excluded_count?`<span class="explorer-item-status">${fmt(x.excluded_count,0)} deleted</span>`:''}</small>${x.registered_merge?`<span class="job-cleaned-badge">${x.merge_archived?'Previous result':'Registered cloud'}</span>`:x.cleaned?'<span class="job-cleaned-badge">Tunnel · kept points</span>':''}</td></tr>`;
  return row+connectionRow(x)+(expanded&&depth<3?children.map(c=>itemRow(c,depth+1)).join(''):'');
 }
 function table(){selectedItems=new Set([...selectedItems].filter(id=>data.items.some(x=>x.id===id)));if(selected&&!selectedItems.size)selectedItems.add(selected);$('job-list').innerHTML=(window.WorkspaceUI?window.WorkspaceUI.tree(data,itemRow):data.items.filter(x=>!mergeParent(x)).map(x=>itemRow(x)).join(''))||'<tr><td colspan="3">Use Import to add scans or models.</td></tr>';$('job-list').insertAdjacentHTML('beforeend',deletedRows());
  for(const id of expandedScans)if(!data.items.some(x=>x.id===id&&x.kind==='scan'))expandedScans.delete(id);
  for(const b of $('job-list').querySelectorAll('[data-scan-expand]'))b.onclick=e=>{e.stopPropagation();const id=b.dataset.scanExpand;expandedScans.has(id)?expandedScans.delete(id):expandedScans.add(id);table();busyUI(busy);[...$('job-list').querySelectorAll('[data-scan-expand]')].find(el=>el.dataset.scanExpand===id)?.focus();};
  for(const b of $('job-list').querySelectorAll('[data-scan-connection]'))b.onclick=()=>window.ScanRegistrationUI?.openLinks(b.dataset.scanConnection);
  for(const tr of $('job-list').querySelectorAll('[data-scene-item]')){tr.onclick=e=>{if(!e.target.closest('input,button'))chooseItem(tr.dataset.sceneItem,e);};tr.onkeydown=e=>{if(e.target===tr&&['Enter',' '].includes(e.key)){e.preventDefault();chooseItem(tr.dataset.sceneItem,e);}if(e.key==='ContextMenu'||e.shiftKey&&e.key==='F10'){e.preventDefault();const r=tr.getBoundingClientRect();sceneMenu({preventDefault(){},clientX:r.left,clientY:r.bottom},tr.dataset.sceneItem);}};tr.oncontextmenu=e=>sceneMenu(e,tr.dataset.sceneItem);}for(const r of $('job-list').querySelectorAll('[data-item]'))r.onchange=()=>chooseItem(r.dataset.item,{ctrlKey:true});for(const b of $('job-list').querySelectorAll('[data-scan-appearance]'))b.onclick=()=>window.ScanAppearanceUI?.open(b.dataset.scanAppearance);for(const b of $('job-list').querySelectorAll('[data-visible]'))b.onclick=()=>action({action:'visibility',id:b.dataset.visible});for(const el of $('job-list').querySelectorAll('[data-deleted-folder]'))el.ontoggle=()=>{el.open?deletedFolders.add(el.dataset.deletedFolder):deletedFolders.delete(el.dataset.deletedFolder);};for(const b of $('job-list').querySelectorAll('[data-view-all-deleted],[data-show-all-deleted]'))b.onclick=()=>viewDeleted(null,null,true);for(const c of $('job-list').querySelectorAll('[data-select-deleted]'))c.onchange=()=>{c.checked?deletedSelection.add(c.dataset.selectDeleted):deletedSelection.delete(c.dataset.selectDeleted);viewDeleted();};for(const b of $('job-list').querySelectorAll('[data-preview-deleted]'))b.onclick=()=>viewDeleted(b.dataset.owner,b.dataset.previewDeleted);for(const b of $('job-list').querySelectorAll('[data-restore-deleted]'))b.onclick=()=>{clearCleaningPreview();$('job-back-deleted').hidden=true;action({action:'restore_deleted',id:b.dataset.owner,group_id:b.dataset.restoreDeleted});};$('job-stats').textContent=`${data.items.filter(x=>x.kind==='scan'&&!mergeParent(x)&&!x.merge_archived).length} clouds · ${data.items.filter(x=>mergeParent(x)&&!x.registered_merge).length} original setups · ${data.mesh_count||0} models · ${selectedItems.size} selected`;$('job-storage').textContent=`Project data: ${data.data_folder||''} · ${gb(data.free_bytes||0)} free`;details();window.WorkspaceUI?.bindTree(data);window.SceneObjectsUI?.render(data.objects);window.CadUI?.observe(data);refreshTargetMarkers();window.HeatUI?.sync();window.MeshResultsUI?.sync();window.RegistrationPanel?.refresh();}

 function previewBlocking(){return loading&&!packed?.length;}
 function busyUI(v){if(v&&!previewOperationBusy){previewEpoch++;previewRequest?.controller.abort();clearTimeout(detailTimer);clearTimeout(pointRefineTimer);}previewOperationBusy=!!v;const blocked=v||previewBlocking()||uploading||deletedLoading,x=current();for(const el of $('tab-job').querySelectorAll('button,input,textarea,select'))if(!['job-cancel','job-fit','job-top','job-side','job-point-size','setup-camera-blend'].includes(el.id))el.disabled=blocked;for(const id of ['job-register','job-surface','job-analyse'])$(id).disabled=blocked||x?.kind!=='scan';for(const id of ['job-rename','job-isolate'])$(id).disabled=blocked||!x;$('job-remove').disabled=blocked||(!x&&!window.SceneObjectsUI?.canRemove()&&!window.CadUI?.selectedIds().length&&!window.WorkspaceUI?.selectedBundleId?.());if($('job-setup-view'))$('job-setup-view').disabled=blocked||!x?.has_setup_view;const review=window.SurfaceUI?.reviewActive?.(),clustersSelected=review&&window.SurfaceUI?.selectedClusters?.().length>0,sel=(!!selectionSpec&&x?.kind==='scan')||clustersSelected||(previewOwner==='circle'&&window.CircleCleanUI?.removableCount?.()>0)||(previewOwner==='shape'&&window.ShapeSelectionUI?.removableCount?.()>0)||(previewOwner==='feature'&&window.FeatureSelectionUI?.removableCount?.()>0);for(const id of ['job-hide-selection','job-isolate-selection','job-delete-selection','job-delete-points-sidebar','job-separate-selection','job-invert-selection'])if($(id))$(id).disabled=blocked||!sel;if(deletedMode){for(const id of ['job-hide-selection','job-isolate-selection','job-clear-view','job-delete-selection','job-delete-points-sidebar','job-separate-selection','job-remove'])if($(id))$(id).disabled=true;$('job-invert-selection').disabled=blocked||!selectionSpec;}$('job-restore-selection').disabled=blocked||!deletedMode||!selectionSpec||!deletedPreviewGroups.length;$('job-export-deleted-selection').disabled=$('job-restore-selection').disabled;$('job-undo-edit').disabled=blocked||!(state?.history?.undo_count??x?.edit_count);$('job-redo-edit').disabled=blocked||!(state?.history?.redo_count??x?.redo_count);$('job-cancel').disabled=!(uploading||(state?.job?.busy&&state.job.kind==='job'));$('job-analyse').textContent=analysisRunning?'Preparing…':'Use for analysis';if(v)$('job-progress').textContent=state?.job?.message||'Working…';window.TunnelRibbon?.sync();window.ScanTools?.sync();window.JointUI?.sync();window.SetupCameraUI?.controls();polygonUI();}
 function previewContentKey(){return JSON.stringify([data.items.map(x=>[x.id,x.kind,x.visible,x.display_mode,x.display_colour,x.edit_count,x.redo_count,x.geometry_revision,x.setup_revision,x.original_count??x.count,x.vertices,x.triangles,x.deleted_groups,x.hidden_regions,x.isolated,x.minimum,x.maximum,x.limit_hide_outside===false?null:x.limit_box]),density(),displayMode,liveLimitEditing]);}
 function overviewKey(){return previewContentKey()+'|'+(setupState?setupState.id+'|'+setupState.key:'scene');}
 function clearPreviewCaches(){normalOverview=null;setupOverview=null;meshPreview=null;setupPreviews.clear();}
 function showPointBuffer(b,detailed=false,stage='complete'){
  if(b.byteLength<56)throw Error('3D preview response was incomplete.');
  const dv=new DataView(b),version=dv.getUint32(4,true);if(String.fromCharCode(...new Uint8Array(b,0,4))!=='TSPV'||![1,2].includes(version))throw Error('Unknown 3D preview format.');
  const info={sample:Math.round(dv.getFloat64(32,true)),points:Math.round(dv.getFloat64(40,true)),vertices:Math.round(dv.getFloat64(48,true))};
  if(!Number.isSafeInteger(info.sample)||info.sample<0||info.sample>1000000||(version===1?b.byteLength!==56+info.sample*24:b.byteLength<56+info.sample*24)||b.byteLength-(56+info.sample*24)>4*1024*1024)throw Error('Invalid point preview length.');
  const origin=[dv.getFloat64(8,true),dv.getFloat64(16,true),dv.getFloat64(24,true)];if(!origin.every(Number.isFinite))throw Error('Invalid point preview origin.');
  const owners=version===2?JSON.parse(new TextDecoder().decode(new Uint8Array(b,56+info.sample*24))):null;
  if(owners!==null){let end=0;const ids=new Set(data.items.map(x=>x.id));if(!Array.isArray(owners)||owners.some(row=>{if(!Array.isArray(row)||row.length!==3||!Number.isSafeInteger(row[0])||!Number.isSafeInteger(row[1])||row[0]!==end||row[1]<row[0]||row[1]>info.sample||!ids.has(row[2]))return true;end=row[1];return false;})||end!==info.sample)throw Error('Invalid preview ownership. Refresh the 3D view.');}
  else if(info.vertices&&info.sample)throw Error('Model ownership is missing from this preview. Reload the current Tunnel Studio version.');
  if(setupState&&owners?.some(row=>row[2]!==setupState.id))throw Error('Preview belongs to a different setup. Reopen this setup.');
  const world=jobScene.initialised?jobScene.target.map((v,i)=>v+previewOrigin[i]):null;
  previewOrigin=origin;sourceInfo={...info,detailed,stage};if(world)jobScene.target=world.map((v,i)=>v-previewOrigin[i]);
  pointOwners=owners||[];pointPreviewComplete=stage!=='overview';packed=new Float32Array(b,56,info.sample*6);
  const alreadyFitted=jobScene.initialised;jobScene.setPacked(packed,previewOrigin,owners);
  if(fitPreviewPending&&info.sample){if(alreadyFitted)jobScene.fit();fitPreviewPending=false;}
  $('job-empty').hidden=!!info.sample||!!jobScene.setupBackdrop?.ready();if(!info.sample)$('job-empty').textContent=emptyPreviewText();
  updatePointPreviewInfo();
  if(info.sample)for(const resolve of previewVisibleWaiters.splice(0))resolve();
 }
 function updatePointPreviewInfo(){
  if(previewOwner||cleanSnapshot||deletedMode||!packed?.length||setupState&&setupDisplay==='photo')return;
  const scans=data.items.filter(x=>x.kind==='scan'&&(setupState?x.id===setupState.id:x.visible!==false));
  const exact=scans.every(x=>x.count_exact!==false),count=scans.reduce((n,x)=>n+(Number.isFinite(x.count)?x.count:0),0);
  const countText=exact?`${fmt(count,0)} retained points`:`up to ${fmt(count,0)} points · ${scans.some(x=>x.metadata_pending)?'checking remaining count':'remaining count unavailable'}`;
  const {detailed,stage}=sourceInfo;
  $('job-preview-info').textContent=setupState?`SETUP · ${setupState.name} only · ${fmt(jobScene.pointCount,0)} preview points / ${countText} · ${detailed?'Detailed view':'Whole setup preview'} · Drag to look around · Wheel to zoom`:`${stage==='overview'?'Overview · detail is loading · ':detailed?'Detailed view · ':'Whole scene · '}${fmt(jobScene.pointCount,0)} preview points / ${countText} · display setting ${density()}%${visibleMeshes().length?' · '+visibleMeshes().length+' triangle model'+(visibleMeshes().length===1?'':'s'):''}. Display mode: ${displayMode.replace('-', ' ')}.`;
 }
 function scheduleInventoryRefresh(){
  clearTimeout(inventoryTimer);inventoryTimer=null;
  if(!data.items.some(x=>x.metadata_pending))return;
  inventoryTimer=setTimeout(()=>{inventoryTimer=null;if(busy||loading){scheduleInventoryRefresh();return;}sync({inventoryOnly:true});},750);
 }
 function showMeshCache(){
  if(setupState||meshPreview?.key!==previewContentKey()){jobScene.setMesh(new Float32Array(),previewOrigin);$('job-mesh-info').textContent='';if(setupState)publishMeshPreview();return false;}
  const offset=meshPreview.origin.map((v,i)=>v-previewOrigin[i]);let values=meshPreview.values;
  if(offset.some(v=>v!==0)){values=new Float32Array(values);for(let i=0;i<values.length;i+=6)for(let j=0;j<3;j++)values[i+j]+=offset[j];}
  if(jobScene.meshValues!==values)jobScene.setMesh(values,previewOrigin);$('job-mesh-info').textContent=meshPreview.info;publishMeshPreview(meshPreview.status);if(jobScene.meshCount)$('job-empty').hidden=true;return true;
 }
 function restoreOverview(){
  const saved=setupState?setupOverview:normalOverview;if(!saved||saved.key!==overviewKey())return false;
  showPointBuffer(saved.buffer,false,saved.complete===false?'overview':'complete');showMeshCache();if(saved.complete===false)previewFeedback('loading','Overview available · increasing detail');else finishPreviewFeedback();return true;
 }
 function readMeshPreview(buffer){
  if(buffer.byteLength<40||String.fromCharCode(...new Uint8Array(buffer,0,4))!=='TSMF')throw Error('Incomplete triangle preview. Retry the preview.');
  const view=new DataView(buffer),version=view.getUint32(4,true),origin=[view.getFloat64(8,true),view.getFloat64(16,true),view.getFloat64(24,true)],count=view.getUint32(32,true),headerTotal=view.getUint32(36,true),end=40+count*72;
  if(![1,2].includes(version)||!origin.every(Number.isFinite)||count>300000||headerTotal<count||buffer.byteLength<end||(version===1?buffer.byteLength!==end:buffer.byteLength===end||buffer.byteLength-end>4*1024*1024))throw Error('Invalid triangle preview response. Retry the preview.');
  let items=[],total=headerTotal;
  if(version===2){
   const metadata=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,end))),expected=new Set(visibleMeshes().map(x=>x.id));
   const countOK=n=>Number.isSafeInteger(n)&&n>=0;
   if(!metadata||!Array.isArray(metadata.items)||metadata.items.length!==expected.size||!countOK(metadata.total_triangles)||metadata.displayed_triangles!==count||Math.min(metadata.total_triangles,4294967295)!==headerTotal)throw Error('Invalid mesh preview counts. Retry the preview.');
   let displayed=0,full=0;
   for(const row of metadata.items){
    if(!row||!expected.delete(row.id)||typeof row.name!=='string'||row.name.length>2048||typeof row.error!=='string'||row.error.length>4096||!['ready','error'].includes(row.state)||!countOK(row.total_triangles)||!countOK(row.displayed_triangles)||row.displayed_triangles>row.total_triangles||typeof row.simplified!=='boolean'||(row.state==='error'&&(row.displayed_triangles||!row.error))||(row.state==='ready'&&(!row.displayed_triangles||row.error))||row.simplified!==(row.state==='ready'&&row.displayed_triangles<row.total_triangles))throw Error('Invalid per-model preview result. Retry the preview.');
    displayed+=row.displayed_triangles;full+=row.total_triangles;
   }
   if(displayed!==count||full!==metadata.total_triangles||expected.size)throw Error('Mesh preview totals do not match its models. Retry the preview.');
   items=metadata.items;total=metadata.total_triangles;
  }
  const failures=items.filter(x=>x.state==='error'),error=failures.length?(failures.length>1?failures.length+' models affected. ':'')+failures[0].name+': '+failures[0].error:total&&!count?'No triangle faces were returned. Retry the preview.':'';
  const status={state:error?(count?'partial':'error'):count?'ready':'empty',displayed_triangles:count,total_triangles:total,items,error};
  const values=new Float32Array(buffer,40,count*18);if(!values.every(Number.isFinite))throw Error('Invalid triangle coordinates. Retry the preview.');
  return {values,origin,status,info:total?`${count.toLocaleString()} displayed / ${total.toLocaleString()} full triangles${failures.length?' · '+failures.length+' model preview'+(failures.length===1?'':'s')+' unavailable':count<total?' · connected simplified preview; export uses the full mesh':' · full mesh displayed'}`:''};
 }
 async function loadBinary(){
  if(deletedMode||(cleanSnapshot&&cleanSnapshot.contentKey===previewContentKey()))return;
  if(!data.items.length){packed=null;pointOwners=[];targetMarkers=[];jobScene.setPacked(new Float32Array(),previewOrigin);jobScene.setMesh(new Float32Array(),previewOrigin);jobScene.original=null;jobScene.clear();$('job-empty').hidden=false;$('job-empty').textContent='New project · import a scan or create geometry';$('job-preview-info').textContent='No scans loaded';return;}
  if(setupState&&setupDisplay==='photo')return;
  clearCleaningPreview();const requestedLive=liveLimitEditing;
  const requestedSetup=setupState?setupState.id+'|'+setupState.key:'',requestedView=JSON.stringify(detailView),query={owners:'1',detail:density(),mode:displayMode,live_limit:requestedLive?'1':'0'};if(detailView)query.view=requestedView;if(setupState){query.setup_id=setupState.id;query.setup_key=setupState.key;}
  const request={controller:new AbortController(),view:requestedView,epoch:previewEpoch};previewRequest=request;
  try{
  const contentKey=previewContentKey(),wholeKey=overviewKey(),cacheKey=requestedSetup?JSON.stringify([contentKey,query]):null;
  const currentRequest=()=>request.epoch===previewEpoch&&!busy&&!deletedMode&&!previewOwner&&!cleanSnapshot&&requestedLive===liveLimitEditing&&requestedView===JSON.stringify(detailView)&&requestedSetup===(setupState?setupState.id+'|'+setupState.key:'')&&contentKey===previewContentKey();
  let b=cacheKey?setupPreviews.get(cacheKey):!detailView&&normalOverview?.key===wholeKey&&normalOverview.complete!==false?normalOverview.buffer:null;
  const needsOverview=setupState?!packed?.length:detailView||data.items.some(x=>x.visible&&x.kind==='scan'&&x.count>2000000);
  if(!b&&needsOverview){
   previewFeedback('loading',detailView?'Loading measured points in this view':'Opening measured overview');
   const saved=setupState?setupOverview:normalOverview;
   let qb=!detailView&&saved?.key===wholeKey&&saved.complete===false?saved.buffer:null;
   if(!qb){const quick=await fetch('/api/job-preview-bin?'+new URLSearchParams({...query,quick:'1'}),{signal:request.controller.signal});if(quick.ok)qb=await quick.arrayBuffer();}
   if(qb&&currentRequest()){
    // A temporarily empty coarse frustum must not blank a useful loaded view.
    if(qb.byteLength>=56&&(new DataView(qb).getFloat64(32,true)>0||!packed?.length))showPointBuffer(qb,!!detailView,'overview');
    if(!detailView){const overview={key:wholeKey,buffer:qb,complete:false};if(setupState)setupOverview=overview;else normalOverview=overview;}
    busyUI(busy);previewFeedback('loading','Measured points ready · increasing detail in the background');await new Promise(resolve=>requestAnimationFrame(resolve));
   }
  }
  if(!b){previewFeedback('loading',setupState?'Loading points · '+setupState.name+' only':normalOverview?.key===wholeKey?'Overview ready · refining the visible scans':'Loading measured points · '+data.items.filter(x=>x.visible).length+' visible items');const r=await fetch('/api/job-preview-bin?'+new URLSearchParams(query),{signal:request.controller.signal});if(!r.ok){let d={};try{d=await r.json();}catch(_e){}throw Error(d.error||'Could not load 3D preview.');}b=await r.arrayBuffer();}
  else if(cacheKey){setupPreviews.delete(cacheKey);setupPreviews.set(cacheKey,b);}
  if(!currentRequest()){if(!previewOwner&&!cleanSnapshot&&!deletedMode)previewRefreshPending=true;return;}
  previewFeedback('loading','Drawing point preview');
  showPointBuffer(b,!!detailView);
  if(cacheKey){setupPreviews.set(cacheKey,b);while(setupPreviews.size>3||[...setupPreviews.values()].reduce((sum,x)=>sum+x.byteLength,0)>64*1024*1024)setupPreviews.delete(setupPreviews.keys().next().value);}
  if(!detailView&&b.byteLength<=32*1024*1024){const saved={key:wholeKey,buffer:b,complete:true};if(setupState)setupOverview=saved;else normalOverview=saved;}
  if(setupState){publishMeshPreview();window.SetupCameraUI?.loaded(setupState.id);return;}
  if(showMeshCache())return;
  if(!visibleMeshes().length){publishMeshPreview();return;}
  publishMeshPreview(pendingMeshState());previewFeedback('loading','Preparing connected solid surfaces');
  $('job-mesh-info').textContent='Preparing connected solid preview…';
  try{
   const mr=await fetch('/api/job-mesh-preview-bin',{signal:request.controller.signal});
   if(!mr.ok){let err={};try{err=await mr.json();}catch(_e){}throw Error(err.error||'Could not load triangle faces. Refresh the 3D view.');}
   const mb=await mr.arrayBuffer();if(!currentRequest()){if(!previewOwner&&!cleanSnapshot&&!deletedMode)previewRefreshPending=true;return;}
   meshPreview={key:contentKey,...readMeshPreview(mb)};showMeshCache();
  }catch(e){
   // Scan points remain usable; a failed mesh is never substituted with points.
   if(e.name==='AbortError')throw e;
   if(currentRequest()){jobScene.setMesh(new Float32Array(),previewOrigin);publishMeshPreview(pendingMeshState('error',e.message));$('job-mesh-info').textContent='Solid preview unavailable: '+e.message;}
  }
  }finally{if(previewRequest===request)previewRequest=null;}
 }
 function viewSignature(){return JSON.stringify([setupState?.id,setupState?.key,setupDisplay,detailView,liveLimitEditing,data.items.map(x=>[x.id,x.visible,x.display_mode,x.display_colour,x.edit_count,x.geometry_revision,x.original_count??x.count,x.vertices,x.triangles,x.hidden_regions,x.isolated,x.limit_hide_outside===false?null:x.limit_box]),density(),displayMode]);}
 async function sync(options={}){
  if(loading){if(options.retainDisplay){await new Promise(resolve=>previewIdleWaiters.push(resolve));return sync(options);}previewRefreshPending=true;return;}if(busy&&!options.allowBusy)return;loading=true;
  const previousContent=previewContentKey();
  if(!options.inventoryOnly&&previewRequested&&!packed?.length&&!(setupState&&setupDisplay==='photo'))previewFeedback('loading',options.setupEntry?'Opening measured setup overview':'Reading project inventory');
  try{
   if(!options.setupEntry){
    const previous=new Set(data.items.map(x=>x.id));data=await api('/api/job');
    if(setupState){const x=data.items.find(x=>x.id===setupState.id);if(!x||(x.setup_revision&&x.setup_revision!==setupState.key)){window.SetupCameraUI?.invalidated();leaveSetup(false);}}
    const added=data.items.filter(x=>(x.kind==='mesh'||x.cleaned||x.registered_merge)&&!previous.has(x.id));
    if(added.length&&previous.size){selected=added.at(-1).id;selectedItems=new Set([selected]);previewRequested=true;}
    if(!window.SceneObjectsUI?.selected()&&!window.CadUI?.selectedIds?.({includeInactive:true}).length&&!data.items.some(x=>x.id===selected))selected=data.items.find(x=>!mergeParent(x)&&!x.merge_archived)?.id||null;
    table();window.ScanRegistrationUI?.refresh?.().catch(()=>{});
   }
   if(options.inventoryOnly){if(options.retainDisplay){signature=viewSignature();previewRefreshPending=false;updatePointPreviewInfo();}else if(previewRequested&&previousContent!==previewContentKey())previewRefreshPending=true;else updatePointPreviewInfo();return;}
   const key=viewSignature();
   if(key!==signature){
    if(previewRequested){await loadBinary();signature=key;}
    else{signature=key;packed=null;jobScene.clear();$('job-preview-info').textContent='Import complete. Click Refresh 3D to display the point preview.';$('job-empty').hidden=false;$('job-empty').textContent='Import complete · preview paused · click Refresh 3D';}
   }
   if(!previewRefreshPending)finishPreviewFeedback();
   $('job-progress').textContent=state?.job?.error||(['partial','error'].includes(meshStatus.state)?'Surface preview needs attention. '+meshStatus.error:'Ready · imported coordinates preserved.');
  }catch(e){
   if(e.name==='AbortError'){if(!previewOwner&&!cleanSnapshot&&!deletedMode)previewRefreshPending=true;}
   else{previewFeedback('error',(packed?.length?'Overview remains available · detail could not load: ':'Preview could not load: ')+e.message);notice(e.message,true);$('job-progress').textContent=e.message;if(!packed?.length){$('job-empty').hidden=false;$('job-empty').textContent='3D preview unavailable. Use Retry preview above.';}}
  }finally{
   loading=false;busyUI(busy);jobScene.markers(targetMarkers);jobScene.draw();scheduleInventoryRefresh();
   for(const resolve of previewIdleWaiters.splice(0))resolve();
   if(previewRefreshPending){previewRefreshPending=false;signature='';queueMicrotask(sync);}
   else if(!detailView&&packed?.length&&!cleanSnapshot)scheduleDetail();
  }
 }
 async function action(body,after=null,options={}){
  if(busy||uploading)return false;
  if(!['register','analyse','surface','rename','delete_selection_many'].includes(body.action)){
   clearPreviewCaches();detailView=null;signature='';previewRequest?.controller.abort();
  }
  const preparing=body.action==='analyse',name=current()?.name||'Scan';
  if(preparing){
   analysisRunning=true;
   window.WorkStatus?.show('Preparing scan for analysis…',name+' · reading scan chainages',true);
   updateOperationProgress({busy:true,message:'Preparing scan for analysis…'});
  }
  try{
   if(body.action==='register')await window.TargetUI?.saveIfDirty?.();
   setBusy(true);await post('/api/job-action',body);
   const completed=await waitReady(options.onProgress||null,options);
   if(completed?.cancelled)return false;
   if(preparing){
    refreshState();
    const range=state.analysis_range;
    const extent=range?` · Ch. ${fmt(range.start,3)}–${fmt(range.end,3)} m`:'';
    window.WorkStatus?.show('Scan ready for analysis',`${name} · ${fmt(state.valid_points||0,0)} points${extent}. Open Design section or Heatmap.`);
   }
   if(after)window.TunnelRibbon?.view(after);
   return true;
  }catch(e){
   setBusy(false);updateOperationProgress({busy:false});
   if(preparing)window.WorkStatus?.show('Could not prepare scan',e.message,false,true);
   notice(e.message,true);$('job-progress').textContent=e.message;return false;
  }finally{
   if(preparing){analysisRunning=false;$('job-analyse').textContent='Use for analysis';}
  }
 }
 async function ensureRegistrationSource(){const x=current();if(!x||x.kind!=='scan'){if(state?.registration_ready)return true;showTunnelMessage('Select a scan','Choose a point-cloud scan in the left object explorer first.',true);return false;}if(state?.registration_ready&&data.active_registration===x.id)return true;await window.TargetUI?.saveIfDirty?.();return await action({action:'register',id:selected});}
 function setDisplayMode(mode){if(!['rgb','intensity-gray','intensity-hue','bundle','elevation'].includes(mode))mode='rgb';displayMode=mode;localStorage.setItem('tunnelstudio.displayMode',mode);previewRequested=true;signature='';sync();}
 function getDisplayMode(){return displayMode;}
 async function waitReady(onProgress,options={}){while(true){const s=await api(jobStatusURL());updateJobSnapshot(s);updateOperationProgress(s.job);onProgress?.(s.job);if(analysisRunning&&s.job.busy)window.WorkStatus?.show('Preparing scan for analysis…',s.job.message,true);if(!s.job.busy){if(!options.keepBusy)setBusy(false);if(!options.skipSync)await sync();if(s.job.error&&!options.ignoreErrors)throw Error(s.job.error);return s.job;}busyUI(true);$('job-progress').textContent=s.job.message;await new Promise(r=>setTimeout(r,options.pollMs||600));}}
 let importDestination=null;
 async function importPaths(paths,bundle_id=window.WorkspaceUI?.activeBundle?.()){if(!paths?.length)return;previewRequested=true;fitPreviewPending=true;signature='';const ok=await action({action:'import_paths',paths,bundle_id});if(!ok)return false;await window.DesignUI?.sync();window.WorkStatus?.show('Import complete','Full-density source points are available. The measured overview opens automatically.');return true;}
 async function uploadFiles(files,kind,options={}){
  if(!files.length)return false;const bundle_id=options.bundle_id??importDestination??window.WorkspaceUI?.activeBundle?.();importDestination=null;
  if(files.some(f=>f.size>150000000)){window.TunnelRibbon?.openLocalImport(kind==='job_mesh'?'model':'scan','This selection includes a file larger than 150 MB. Paste its full path to read it directly from disk.');return false;}
  uploading=true;stopQueue=false;previewRequested=false;signature='';busyUI(true);
  try{for(let i=0;i<files.length;i++){
   if(stopQueue)return false;const f=files[i];setBusy(true);$('job-progress').textContent=`${options.queueLabel||'Transferring '+(i+1)+'/'+files.length}: ${f.name}`;
   uploadController=new AbortController();await api('/api/upload?'+new URLSearchParams({kind,name:f.name,...(bundle_id?{bundle_id}:{})}),{method:'POST',headers:{'X-Tunnel-Token':window.TUNNEL_TOKEN},body:f,signal:uploadController.signal});uploadController=null;
   const done=await waitReady();if(done?.cancelled)return false;
  }return true;}catch(e){if(e.name!=='AbortError')notice(e.message,true);return false;}finally{uploadController=null;uploading=false;previewRequested=true;fitPreviewPending=true;signature='';setBusy(false);await sync();await window.DesignUI?.sync();}
 }
 $('job-import-scans').onclick=()=>$('job-scan-files').click();$('job-import-mesh').onclick=()=>$('job-mesh-files').click();$('job-scan-files').onchange=e=>{const f=Array.from(e.target.files);e.target.value='';uploadFiles(f,'job_scan');};$('job-mesh-files').onchange=e=>{const f=Array.from(e.target.files);e.target.value='';uploadFiles(f,'job_mesh');};
 $('job-browse-paths').onclick=()=>window.TunnelRibbon?.openImport?.('scan');$('job-import-paths').onclick=()=>{const paths=$('job-paths').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);if(paths.length)importPaths(paths);};$('job-cancel').onclick=()=>{const transferring=!!uploadController;if(uploading){stopQueue=true;uploadController?.abort();}return post('/api/cancel-operation',{}).catch(e=>{if(!transferring)notice(e.message,true);});};
 $('job-register').onclick=()=>action({action:'register',id:selected},'registration');$('job-surface').onclick=openSurface;
 async function openSurface(options={}){
  if(busy||uploading)return false;
  const item=current();if(!item||item.kind!=='scan'){showTunnelMessage('Select a scan first','Select a point-cloud scan in the scene list, then open Clean Tunnel.',true);return false;}
  // Capture before any preview refresh or drawer change can clear the selection.
  const limit=item.limit_box;
  const selectedArea=options.whole===true||!selectionSpec?null:JSON.parse(JSON.stringify(selectionSpec));
  if(selectedArea&&limit)selectedArea.clip_box=limit;
  const area=selectedArea||(options.whole===true?{mode:'full'}:limit?{mode:'limit_box'}:null);
  if(window.CadUI?.modelSpace?.()&&!window.CadUI.enter('Clean',{openTool:false}))return false;
  const ok=await action({action:'surface',id:item.id,selection:selectedArea});if(!ok)return false;
  const ready=await window.SurfaceUI?.selectSource('separate',area);if(ready===false)return false;
  window.TunnelRibbon?.view('surface',{refresh:false});window.TunnelRibbon?.surfaceDrawerMode('setup');return true;
 }
 $('job-analyse').onclick=()=>{if(!state?.axis){showTunnelMessage('Alignment required','The scan can be viewed and edited without an alignment. Alignment is only required for chainage/design analysis.',true,'Import alignment',()=>window.TunnelRibbon?.openImport?.('axis'));return;}if(confirm('Confirm this scan is already positioned in the alignment coordinate system and uses metres.'))action({action:'analyse',id:selected,position_confirmed:true});};
 $('job-keep-active').onclick=()=>action({action:'keep_active',source:'survey'});$('job-keep-positioned').onclick=()=>action({action:'keep_active',source:'positioned'});$('job-rename').onclick=()=>{const x=current();if(!x)return;const name=prompt('Item name',x.name);if(name!==null)action({action:'rename',id:x.id,name});};$('job-remove').onclick=removeSelected;$('job-delete-points-sidebar').onclick=()=>$('job-delete-selection').click();
 $('job-isolate').onclick=async()=>{if(await action({action:'isolate_items',ids:[...selectedItems]}))jobScene.fit();};$('job-show-all').onclick=()=>action({action:'show_all'});$('job-fit').onclick=fitWholeScene;$('job-top').onclick=()=>setView('top');$('job-side').onclick=()=>setView('front');
 async function loadPreview({northUp=false,fitIds=[]}={}){
  if(busy||uploading)return false;
  if(window.CadUI?.modelSpace?.()&&!window.CadUI.enter('Edit',{openTool:false})){
   notice('Finish or cancel the modelling command before opening the scan preview.',true);return false;
  }
  if(setupState)await window.SetupCameraUI?.exit(false);
  previewRequest?.controller.abort();
  // Wait for the current inventory/preview response instead of losing this request.
  while(loading)await new Promise(resolve=>previewIdleWaiters.push(resolve));
  if(busy||uploading)return false;
  clearCleaningPreview();clearPreviewCaches();detailView=null;previewRequested=true;signature='';fitPreviewPending=true;
  $('job-empty').textContent='Preparing 3D preview…';
  await sync();
  if(northUp&&packed?.length){
   setView('top');
   const ids=new Set(fitIds);
   const bounds=data.items.filter(item=>ids.has(item.id)&&item.minimum&&item.maximum)
    .flatMap(item=>[item.minimum,item.maximum]);
   if(bounds.length)focusWorld(bounds,false);else fitWholeScene();
  }
  return !!packed?.length;
 }
 async function projectOpened(){
  pendingPointDelete=null;jobScene.pendingDelete=false;
  window.WorkspaceUI?.clearBundleSelection();
  selected=null;selectedItems.clear();targetSelection.clear();window.SceneObjectsUI?.clearSelected();clearSelection();
  if(setupState)await window.SetupCameraUI?.exit(false);
  previewRequest?.controller.abort();while(loading)await new Promise(resolve=>previewIdleWaiters.push(resolve));
  clearCleaningPreview();clearPreviewCaches();detailView=null;signature='';previewRequested=true;fitPreviewPending=true;
  packed=null;pointOwners=[];jobScene.clear();$('job-empty').hidden=false;$('job-empty').textContent='Opening project · preparing 3D points…';previewFeedback('loading','Opening project');
  // The next poll supplies the new inventory; never reuse a previous project's camera fit.
  if(window.CadUI?.modelSpace?.())window.CadUI.enter('Edit',{openTool:false});
 }
 $('job-load-preview').onclick=loadPreview;$('job-density').oninput=()=>{densityUI();if(setupState)localStorage.setItem('tunnelstudio.setupDensity',String(density()));clearTimeout(window.__jobDensityTimer);window.__jobDensityTimer=setTimeout(()=>{previewRequested=true;signature='';sync();},260);};$('job-point-size').oninput=()=>jobScene.draw();densityUI();
 function isPicking(){return jobMode==='pick'||jobMode==='worldpick';}
 function setMode(mode){
  if(setupState&&['box','lasso','brush','polygon','worldpick','pan'].includes(mode)){notice('Return to normal 3D for area selection, limit-box editing or panning. In setup view, drag to look around.',true);return false;}
  if(!deletedMode&&['box','lasso','brush','polygon'].includes(mode)&&window.SurfaceUI?.reviewActive?.())window.SurfaceUI.selectClusters([]);
  if(['box','lasso','brush','polygon','pick','worldpick'].includes(mode))window.TargetUI?.selectTargets([]);
  if(mode==='polygon'&&!packed?.length){showTunnelMessage('Load the 3D preview','Load scan points before drawing a polygon.',true);return false;}
  if(mode!=='brush'&&brushPointer!==null){brushPointer=null;dragStart=null;lasso=[];}
  const resume=polygonFrame&&['polygon','orbit','pan'].includes(mode);
  if(!resume){resetPolygon();if(mode==='polygon')clearSelection();else drawStoredSelection();}
  clickGesture=null;jobScene.cancelNavigation?.();
  if(mode!=='pick'){pointPickCallback=null;setupRayPick=null;repeatPick=null;}if(mode!=='worldpick')worldPickCallback=null;
  jobMode=mode;window.__tunnelForcePan=mode==='pan';
  editMode=['orbit','pan','pick','worldpick'].includes(mode)?'orbit':'job-select';
  for(const [id,m] of [['job-mode-orbit','orbit'],['job-mode-select','box'],['job-mode-polygon','polygon'],['job-mode-lasso','lasso'],['job-mode-brush','brush']])if($(id))$(id).classList.toggle('chosen',mode===m);
  for(const b of document.querySelectorAll('[data-job-nav]'))b.classList.toggle('active',b.dataset.jobNav===mode);
  $('job-canvas').style.cursor=mode==='orbit'?'grab':mode==='pan'?'move':'crosshair';
  const hint=$('job-pick-hint');hint.hidden=!isPicking()&&!['polygon','brush'].includes(mode);polygonUI();if(mode==='brush'){hint.hidden=false;hint.textContent='Brush: drag one stroke through the scan depth. A new stroke replaces the selection. Radius is in screen pixels; Escape clears it.';}
  if(isPicking()){hideMenu();hint.textContent=setupState?'Pick a cloud point · Drag to look around · Wheel to zoom · Escape to cancel':`${mode==='worldpick'?'Pick inside the tunnel':'Pick a scan point'} · Drag to rotate · Shift or right-drag to pan · Wheel to zoom · Click to choose · Escape to cancel`;}
  if(mode!=='pick')window.SetupCameraUI?.pickModeEnded();
 }
 function armPointPick(callback,options={}){if(!packed?.length){showTunnelMessage('3D preview required','Load the 3D preview before picking a scan point.',true);return false;}setMode('pick');setupRayPick=null;pointPickCallback=callback;repeatPick=options.repeat?{scanId:setupState?.id||current()?.id}:null;pointPickDone=options.done||'Scan point picked';if(options.prompt)$('job-pick-hint').textContent=options.prompt+(setupState?' · Drag to look around · Wheel to zoom · Escape to cancel':' · Drag to rotate · Shift or right-drag to pan · Wheel to zoom · Escape to cancel');$('job-selection-info').textContent=$('job-pick-hint').textContent;return true;}
 async function deliverPick(callback,...args){
  const session=repeatPick;
  if(!session){setMode('orbit');callback(...args);return;}
  if(session.scanId!==(setupState?.id||current()?.id)){setMode('orbit');return;}
  if(pendingRepeatPick===session||targetNameEdit)return;
  pendingRepeatPick=session;
  try{await callback(...args);}
  catch(error){notice(error.message,true);}
  finally{if(pendingRepeatPick===session)pendingRepeatPick=null;}
 }
 const canvas=$('job-canvas'),selBox=$('job-selection-box');
 let snapIndex=null,snapBuilding=null,snapGeneration=0,hoverPixel=null,hoverSerial=0,hoverFrame=null,hoverWorking=false;
 function snapFrame(){return JSON.stringify([jobScene.target,jobScene.yaw,jobScene.pitch,jobScene.scale,canvas.clientWidth,canvas.clientHeight,jobScene.workPlaneView,jobScene.setupCamera,previewOrigin,pointEditGeneration,jobScene.cloudFallbackStep,jobScene.spatialPoints?.revision,jobScene.pendingDelete,jobScene.liveClip,jobScene.hideCloud,setupDisplay,setupBlendValue,data.items.filter(x=>x.kind==='scan'&&x.visible!==false).map(x=>x.id)]);}
 function validSnap(index,key){return !cleanSnapshot&&!deletedMode&&index&&index.key===key&&index.values===packed&&index.owners===pointOwners&&index.flags===jobScene.gpuFlags;}
 async function screenPointIndex(){
  if(!packed?.length||cleanSnapshot||deletedMode||jobScene.hideCloud||(setupState&&setupDisplay==='both'&&setupBlendValue===0))return null;
  const key=snapFrame();if(validSnap(snapIndex,key))return snapIndex;if(validSnap(snapBuilding,key))return snapBuilding.promise;
  const generation=++snapGeneration,values=packed,owners=pointOwners,flags=jobScene.gpuFlags,b=jobScene.basis(),target=jobScene.target.slice(),camera=jobScene.setupCamera?{...jobScene.setupCamera}:null,scale=jobScene.scale,width=canvas.clientWidth,height=canvas.clientHeight,aspect=width/Math.max(1,height),clip=jobScene.liveClip;
  const n=values.length/6,cols=Math.ceil(width/18)+1,rows=Math.ceil(height/18)+1,index={key,values,owners,flags,cols,rows,heads:new Int32Array(cols*rows).fill(-1),next:new Int32Array(n).fill(-1),x:new Float32Array(n),y:new Float32Array(n),depth:new Float32Array(n)};
  const scans=new Set(data.items.filter(x=>x.kind==='scan'&&x.visible!==false).map(x=>x.id)),ranges=owners.filter(x=>scans.has(x[2])),tan=camera?Math.tan(camera.fov*Math.PI/360):0;
  const building={key,values,owners,flags};snapBuilding=building;
  building.promise=(async()=>{
   let deadline=performance.now()+5;
   for(const [start,end] of ranges)for(let i=start;i<end;i++){
    if((i&2047)===0&&performance.now()>deadline){await new Promise(requestAnimationFrame);if(generation!==snapGeneration||key!==snapFrame()||values!==packed)return null;deadline=performance.now()+5;}
    if(!jobScene.visiblePreviewRow(i))continue;
    const at=i*6,px=values[at],py=values[at+1],pz=values[at+2];if(clip&&!window.TunnelBox.contains([px,py,pz],clip))continue;
    const dx=px-target[0],dy=py-target[1],dz=pz-target[2],project=a=>dx*a[0]+dy*a[1]+dz*a[2],z=project(b.forward);
    if(camera&&(z<camera.near||z>camera.far))continue;
    const x=(project(b.right)/(camera?z*tan*aspect:scale)+1)*width/2,y=(1-project(b.up)/(camera?z*tan:scale/aspect))*height/2;
    if(!Number.isFinite(x+y+z)||x<0||x>width||y<0||y>height)continue;
    const cell=Math.floor(y/18)*cols+Math.floor(x/18);index.x[i]=x;index.y[i]=y;index.depth[i]=camera?z:-z;index.next[i]=index.heads[cell];index.heads[cell]=i;
   }
   if(generation!==snapGeneration||key!==snapFrame()||values!==packed)return null;snapIndex=index;return index;
  })().finally(()=>{if(snapBuilding===building)snapBuilding=null;});return building.promise;
 }
 function indexedSnap(index,pixel){
  if(!index)return null;let chosen=-1,rank=[Infinity,Infinity,Infinity];const cx=Math.floor(pixel[0]/18),cy=Math.floor(pixel[1]/18);
  for(let y=Math.max(0,cy-1);y<=Math.min(index.rows-1,cy+1);y++)for(let x=Math.max(0,cx-1);x<=Math.min(index.cols-1,cx+1);x++)for(let i=index.heads[y*index.cols+x];i>=0;i=index.next[i]){
   const distance=Math.hypot(index.x[i]-pixel[0],index.y[i]-pixel[1]);if(distance>18)continue;const next=[Math.floor(distance*4),index.depth[i],distance];
   if(next[0]<rank[0]||next[0]===rank[0]&&(next[1]<rank[1]||next[1]===rank[1]&&next[2]<rank[2])){chosen=i;rank=next;}
  }
  if(chosen<0)return null;const source_id=pointOwners.find(([a,b])=>chosen>=a&&chosen<b)?.[2],item=data.items.find(x=>x.id===source_id);
  return item?{point:[0,1,2].map(j=>packed[chosen*6+j]+previewOrigin[j]),source_id,source_revision:item.geometry_revision}:null;
 }
 function cadCloudHoverPoint(pixel){
  if(cleanSnapshot||deletedMode||jobScene.hideCloud||!packed?.length)return null;
  return validSnap(snapIndex,snapFrame())?indexedSnap(snapIndex,pixel):undefined;
 }
 function clearPointHover(){hoverPixel=null;hoverSerial++;window.MeasureTools?.hoverPoint(null);}
 function queuePointHover(){
  if(hoverFrame||hoverWorking)return;hoverFrame=requestAnimationFrame(async()=>{hoverFrame=null;if(!hoverPixel||busy||!window.MeasureTools?.hoverEnabled())return;hoverWorking=true;const serial=hoverSerial;let retryIndex=false;
   try{const index=await screenPointIndex();retryIndex=!index&&!!packed?.length&&!cleanSnapshot&&!deletedMode&&!jobScene.hideCloud&&!(setupState&&setupDisplay==='both'&&setupBlendValue===0);if(serial===hoverSerial&&hoverPixel&&window.MeasureTools?.hoverEnabled())window.MeasureTools.hoverPoint(indexedSnap(index,hoverPixel));}
   finally{hoverWorking=false;if((serial!==hoverSerial||retryIndex)&&hoverPixel)queuePointHover();}
  });
 }
 canvas.addEventListener('pointermove',event=>{if(event.buttons||busy||!window.MeasureTools?.hoverEnabled()){clearPointHover();return;}const r=canvas.getBoundingClientRect();hoverPixel=[event.clientX-r.left,event.clientY-r.top];hoverSerial++;queuePointHover();});
 for(const event of ['pointerleave','pointerdown','pointercancel','wheel'])canvas.addEventListener(event,clearPointHover,{passive:true});
 function nearestDisplayedPoint(pixel,withSource=false,maxPoints=Infinity){if(!packed?.length||jobScene.hideCloud||(setupState&&setupDisplay==='both'&&setupBlendValue===0))return null;const b=jobScene.basis(),r=canvas.getBoundingClientRect();let best=null,bestIndex=-1,rank=[Infinity,Infinity,Infinity];const scans=withSource?new Set(data.items.filter(x=>x.kind==='scan'&&x.visible!==false).map(x=>x.id)):null,ranges=withSource?pointOwners.filter(row=>scans.has(row[2])):[[0,packed.length/6]],step=Math.max(1,Math.ceil(ranges.reduce((n,[a,b])=>n+b-a,0)/maxPoints));for(const [start,end] of ranges)for(let i=start*6;i<end*6;i+=6*step){if(!jobScene.visiblePreviewRow(i/6))continue;const local=[packed[i],packed[i+1],packed[i+2]];if(jobScene.liveClip&&!window.TunnelBox.contains(local,jobScene.liveClip))continue;const p=jobScene.projectLocal(local,b);if(!p)continue;const dx=(p[0]+1)*r.width/2-pixel[0],dy=(1-p[1])*r.height/2-pixel[1],distance=Math.hypot(dx,dy);if(distance>18)continue;const next=[Math.floor(distance*4),setupState?p[2]:-p[2],distance];if(next[0]<rank[0]||next[0]===rank[0]&&(next[1]<rank[1]||next[1]===rank[1]&&next[2]<rank[2])){best=[packed[i]+previewOrigin[0],packed[i+1]+previewOrigin[1],packed[i+2]+previewOrigin[2]];bestIndex=i/6;rank=next;}}if(withSource){const source_id=pointOwners.find(([a,b])=>bestIndex>=a&&bestIndex<b)?.[2],item=data.items.find(x=>x.id===source_id);return best&&item?{point:best,source_id,source_revision:item.geometry_revision}:null;}return best;}

 function worldAtPixel(pixel){const r=$('job-canvas').getBoundingClientRect(),b=jobScene.basis(),x=2*pixel[0]/Math.max(1,r.width)-1,y=1-2*pixel[1]/Math.max(1,r.height),aspect=r.width/Math.max(1,r.height);const local=jobScene.target.map((v,i)=>v+b.right[i]*x*jobScene.scale+b.up[i]*y*jobScene.scale/aspect);return local.map((v,i)=>v+previewOrigin[i]);}
 function armWorldPick(callback){if(setupState){setMode('worldpick');return false;}if(!packed?.length){showTunnelMessage('3D preview required','Load the 3D preview before picking a tunnel interior point.',true);return false;}setMode('worldpick');worldPickCallback=callback;$('job-selection-info').textContent=$('job-pick-hint').textContent;return true;}
 function clearSelection(render=true){resetPolygon();selectionSpec=null;selectionBoundaryWorld=[];brushPointer=null;dragStart=null;lasso=[];$('job-selection-box').style.display='none';if(render)jobScene.renderSelection();drawSelectionOverlay([]);areaSelectionCallback=null;if(!deletedMode)window.SurfaceUI?.selectionChanged?.(null);$('job-selection-info').textContent='No area selected';busyUI(busy);}
 function finishSelection(points,frozen=null,boundary=null){if(points.length<3)return;try{selectionSpec=frozen||pixelPolygonToSpec(points);selectionBoundaryWorld=boundary||selectionWorldBoundary(selectionSpec);const count=displayedSelectionCount();if(!count&&!frozen){selectionSpec=null;selectionBoundaryWorld=[];drawSelectionOverlay([]);notice('No displayed points are inside that selection. Zoom in or increase display detail.',true);return;}jobScene.renderSelection();$('job-selection-info').textContent=`${fmt(count,0)} displayed points selected · ${deletedMode?'Restore selected points returns only this area from the displayed groups':'full-density editing follows this screen shape through the scan depth'}`;busyUI(busy);if(!deletedMode)window.SurfaceUI?.selectionChanged?.(selectionSpec);if(areaSelectionCallback){const cb=areaSelectionCallback;areaSelectionCallback=null;const box=selectedBounds(selectionSpec);setMode('orbit');cb(JSON.parse(JSON.stringify(selectionSpec)),box);}}catch(e){selectionSpec=null;selectionBoundaryWorld=[];drawSelectionOverlay([]);notice(e.message||'Could not create that selection.',true);}}
 $('job-mode-orbit').onclick=()=>setMode('orbit');$('job-mode-select').onclick=()=>setMode('box');if($('job-mode-polygon'))$('job-mode-polygon').onclick=()=>setMode('polygon');if($('job-mode-lasso'))$('job-mode-lasso').onclick=()=>setMode('lasso');$('job-clear-selection').onclick=clearSelection;if($('job-invert-selection'))$('job-invert-selection').onclick=()=>{if(!selectionSpec)return;selectionSpec.invert=!selectionSpec.invert;jobScene.renderSelection();$('job-selection-info').textContent=`Selection ${selectionSpec.invert?'inverted':'restored'} · ${fmt(displayedSelectionCount(),0)} displayed points`;};
 $('job-mode-brush').onclick=()=>setMode('brush');
 $('job-brush-radius').value=localStorage.getItem('tunnelstudio.brushRadius')||'20';
 $('job-brush-radius').onchange=()=>{const value=Math.max(2,Math.min(150,+$('job-brush-radius').value||20));$('job-brush-radius').value=value;localStorage.setItem('tunnelstudio.brushRadius',String(value));};
 canvas.addEventListener('pointerdown',e=>{if(jobMode!=='brush'||busy||!packed||e.button!==0||e.isPrimary===false||brushPointer!==null)return;brushPointer=e.pointerId;const r=canvas.getBoundingClientRect();dragStart=[e.clientX-r.left,e.clientY-r.top];lasso=[dragStart];canvas.setPointerCapture(e.pointerId);drawBrushOverlay(lasso,+$('job-brush-radius').value);});
 canvas.addEventListener('pointermove',e=>{if(jobMode!=='brush'||!dragStart||e.pointerId!==brushPointer)return;const r=canvas.getBoundingClientRect(),p=[e.clientX-r.left,e.clientY-r.top];if(Math.hypot(p[0]-lasso.at(-1)[0],p[1]-lasso.at(-1)[1])>=2){lasso.push(p);if(lasso.length>512)lasso=simplifyPath(lasso,256);drawBrushOverlay(lasso,+$('job-brush-radius').value);}});
 canvas.addEventListener('pointerup',e=>{if(jobMode!=='brush'||!dragStart||e.pointerId!==brushPointer)return;const r=canvas.getBoundingClientRect(),points=[...lasso,[e.clientX-r.left,e.clientY-r.top]];brushPointer=null;dragStart=null;lasso=[];if(canvas.hasPointerCapture?.(e.pointerId))canvas.releasePointerCapture(e.pointerId);try{const made=pixelBrushToSpec(points);finishSelection(made.rectangle,made.spec);}catch(error){drawStoredSelection();notice(error.message,true);}});
 for(const type of ['pointercancel','lostpointercapture'])canvas.addEventListener(type,e=>{if(brushPointer!==e.pointerId)return;brushPointer=null;dragStart=null;lasso=[];drawStoredSelection();});
 function cancelActiveBrush(){if(brushPointer===null)return;brushPointer=null;dragStart=null;lasso=[];drawStoredSelection();}
 canvas.addEventListener('wheel',cancelActiveBrush,{capture:true,passive:true});window.addEventListener('blur',cancelActiveBrush);

 // Corners live in the plane of the initial view, not in screen pixels.
 // Pan, zoom and rotation project that same plane; each new click intersects it.
 jobScene.canNavigate=e=>['polygon','cad'].includes(jobMode)&&(e.button===1||e.button===2||(e.buttons&6)||e.shiftKey||e.altKey);
 function resetPolygon(){polygonVertices=[];polygonCursor=null;polygonGesture=null;polygonFrame=null;}
 function startPolygon(){
  const r=canvas.getBoundingClientRect(),basis=jobScene.basis();
  polygonFrame={target:jobScene.target.map((v,i)=>v+previewOrigin[i]),...basis,scale:jobScene.scale,aspect:r.width/Math.max(1,r.height)};
  let lo=Infinity,hi=-Infinity;
  for(const item of (deletedMode?deletedBounds:data.items.filter(x=>x.kind==='scan'&&x.visible&&!x.locked))){
   if(!item.minimum?.every(Number.isFinite)||!item.maximum?.every(Number.isFinite))continue;
   for(let k=0;k<8;k++){const p=[0,1,2].map(i=>(k&(1<<i)?item.maximum:item.minimum)[i]-polygonFrame.target[i]),z=dot(p,basis.forward);lo=Math.min(lo,z);hi=Math.max(hi,z);}
  }
  if(!Number.isFinite(lo))for(let i=0;i<(packed?.length||0);i+=6){const z=dot([0,1,2].map(j=>packed[i+j]+previewOrigin[j]-polygonFrame.target[j]),basis.forward);lo=Math.min(lo,z);hi=Math.max(hi,z);}
  polygonFrame.depth_min=lo-.01;polygonFrame.depth_max=hi+.01;
 }
 function polygonWorld(p){return polygonFrame.target.map((v,i)=>v+polygonFrame.right[i]*p[0]*polygonFrame.scale+polygonFrame.up[i]*p[1]*polygonFrame.scale/polygonFrame.aspect);}
 function polygonAtPixel(pixel){
  const origin=worldAtPixel(pixel),direction=jobScene.basis().forward,denominator=dot(direction,polygonFrame.forward);
  if(Math.abs(denominator)<.04)return null;
  const distance=dot(polygonFrame.target.map((v,i)=>v-origin[i]),polygonFrame.forward)/denominator;
  const delta=origin.map((v,i)=>v+direction[i]*distance-polygonFrame.target[i]);
  return [dot(delta,polygonFrame.right)/polygonFrame.scale,dot(delta,polygonFrame.up)*polygonFrame.aspect/polygonFrame.scale];
 }
 function polygonUI(message){
  const active=jobMode==='polygon'||!!polygonFrame,n=polygonVertices.length;
  for(const id of ['job-polygon-finish','job-polygon-back'])if($(id))$(id).hidden=!active;
  if($('job-polygon-finish'))$('job-polygon-finish').disabled=busy||previewBlocking()||n<3;
  if($('job-polygon-back'))$('job-polygon-back').disabled=busy||previewBlocking()||!n;
  if(!active){$('job-pick-hint').hidden=!isPicking();return;}
  if(active){$('job-pick-hint').hidden=false;$('job-pick-hint').textContent=message||(jobMode==='polygon'?`Polygon · ${n} corners · Click to add · Wheel zoom · Shift/right-drag pan · Alt-drag rotate · Double-click/Enter finish · Backspace undo · Esc cancel`:`Polygon paused · ${n} corners kept · Move the view, then choose Polygon select to continue · Enter to finish · Esc cancel`);}
 }
 function drawPolygon(){
  const vertices=polygonVertices.map(p=>worldToPixel(polygonWorld(p))),cursor=polygonCursor?worldToPixel(polygonWorld(polygonCursor)):null;
  drawSelectionOverlay(cursor?[...vertices,cursor]:vertices,false);
  $('job-selection-overlay').insertAdjacentHTML('beforeend',vertices.map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="${i?3.5:5}" fill="${i?'#ffe56e':'#63e2ba'}" stroke="#253338" stroke-width="1.5"/>`).join(''));
 }
 function polygonError(points){
  const area=Math.abs(points.reduce((n,p,i)=>{const q=points[(i+1)%points.length];return n+p[0]*q[1]-q[0]*p[1];},0))/2;
  if(area<1e-12)return 'The polygon needs an area. Add corners around the points.';
  const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
  for(let i=0;i<points.length;i++)for(let j=i+2;j<points.length;j++){
   if(i===0&&j===points.length-1)continue;
   const a=points[i],b=points[(i+1)%points.length],c=points[j],d=points[(j+1)%points.length];
   const overlaps=Math.max(Math.min(a[0],b[0]),Math.min(c[0],d[0]))<=Math.min(Math.max(a[0],b[0]),Math.max(c[0],d[0]))&&Math.max(Math.min(a[1],b[1]),Math.min(c[1],d[1]))<=Math.min(Math.max(a[1],b[1]),Math.max(c[1],d[1]));
   if(overlaps&&cross(a,b,c)*cross(a,b,d)<=0&&cross(c,d,a)*cross(c,d,b)<=0)return 'Polygon edges cross. Undo a corner or press Escape and draw again.';
  }return '';
 }
 function finishPolygon(){
  if(!polygonFrame||busy||previewBlocking()){if(jobMode==='polygon')polygonUI('Place at least three corners before finishing the polygon.');return;}
  if(polygonVertices.length<3){polygonUI('Place at least three corners before finishing the polygon.');return;}
  const error=polygonError(polygonVertices);if(error){polygonUI(error);return;}
  const points=polygonVertices.slice(),boundary=points.map(polygonWorld),spec={mode:'screen_polygon',...polygonFrame,polygon:points,invert:false};
  resetPolygon();finishSelection(points,spec,boundary);
  if(selectionSpec){setMode('orbit');$('job-selection-info').textContent=`Polygon ready · ${fmt(displayedSelectionCount(),0)} displayed points · Full-density selection through the original polygon plane · ${deletedMode?'Restore selected points returns this area':'Delete selected points to move them to Deleted points'}`;}else polygonUI();
 }
 function undoPolygonCorner(){if(!polygonFrame||busy)return;polygonVertices.pop();polygonCursor=null;if(!polygonVertices.length){polygonFrame=null;drawSelectionOverlay([]);}else drawPolygon();polygonUI();}
 $('job-polygon-finish').onclick=finishPolygon;$('job-polygon-back').onclick=undoPolygonCorner;
 canvas.addEventListener('pointerdown',e=>{
  if(jobMode!=='polygon'||busy||previewBlocking()||!packed?.length||e.button!==0||e.buttons!==1||e.isPrimary===false||e.shiftKey||e.altKey)return;
  polygonGesture={id:e.pointerId,x:e.clientX,y:e.clientY,dragged:false};canvas.setPointerCapture(e.pointerId);e.preventDefault();
 });
 canvas.addEventListener('pointermove',e=>{
  if(jobMode!=='polygon')return;
  if(polygonGesture?.id===e.pointerId)polygonGesture.dragged ||= e.shiftKey||e.altKey||e.buttons!==1||Math.hypot(e.clientX-polygonGesture.x,e.clientY-polygonGesture.y)>VIEW_CLICK_THRESHOLD;
  if(!polygonFrame)return;const r=canvas.getBoundingClientRect();polygonCursor=e.buttons&&!polygonGesture?null:polygonAtPixel([e.clientX-r.left,e.clientY-r.top]);drawPolygon();
 });
 canvas.addEventListener('pointerup',e=>{
  const g=polygonGesture;if(!g||g.id!==e.pointerId)return;polygonGesture=null;
  if(jobMode!=='polygon'||busy||previewBlocking()||g.dragged||e.button!==0||e.buttons||e.shiftKey||e.altKey||Math.hypot(e.clientX-g.x,e.clientY-g.y)>VIEW_CLICK_THRESHOLD)return;
  const r=canvas.getBoundingClientRect(),pixel=[e.clientX-r.left,e.clientY-r.top];if(pixel[0]<0||pixel[1]<0||pixel[0]>r.width||pixel[1]>r.height)return;
  if(polygonVertices.length>=120){polygonUI('Maximum 120 corners. Finish the polygon or undo a corner.');return;}
  if(!polygonFrame)startPolygon();const p=polygonAtPixel(pixel);
  if(!p){polygonUI('The polygon plane is edge-on. Rotate towards its face to add another corner.');return;}
  const previous=polygonVertices.length?worldToPixel(polygonWorld(polygonVertices.at(-1))):null;
  if(previous&&Math.hypot(pixel[0]-previous[0],pixel[1]-previous[1])<4)return;
  const first=polygonVertices.length?worldToPixel(polygonWorld(polygonVertices[0])):null;
  if(polygonVertices.length>=3&&Math.hypot(pixel[0]-first[0],pixel[1]-first[1])<7){finishPolygon();return;}
  polygonVertices.push(p);polygonCursor=null;drawPolygon();polygonUI();
 });
 canvas.addEventListener('dblclick',e=>{if(jobMode==='polygon'&&e.button===0&&!e.shiftKey&&!e.altKey){e.preventDefault();e.stopPropagation();finishPolygon();}});
 for(const event of ['pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>{polygonGesture=null;});
 window.addEventListener('blur',()=>{polygonGesture=null;});
 // Track the whole gesture: returning to the starting position is still a drag.
 canvas.addEventListener('pointerdown',e=>{
  clickGesture=!busy&&e.isPrimary!==false&&e.button===0&&!e.shiftKey&&['orbit','pan','pick','worldpick'].includes(jobMode)
   ?{id:e.pointerId,mode:jobMode,x:e.clientX,y:e.clientY,dragged:false}:null;
 });
 canvas.addEventListener('pointermove',e=>{
  if(clickGesture&&clickGesture.id===e.pointerId)clickGesture.dragged ||= e.shiftKey||e.buttons!==1||Math.hypot(e.clientX-clickGesture.x,e.clientY-clickGesture.y)>VIEW_CLICK_THRESHOLD;
 });
 for(const name of ['pointercancel','lostpointercapture'])canvas.addEventListener(name,e=>{if(clickGesture?.id===e.pointerId)clickGesture=null;});
 window.addEventListener('blur',()=>{clickGesture=null;});
 canvas.addEventListener('wheel',()=>{if(clickGesture)clickGesture.dragged=true;},{passive:true});
 canvas.addEventListener('pointerup',e=>{
  const g=clickGesture;if(!g||g.id!==e.pointerId)return;clickGesture=null;
  if(busy||g.mode!==jobMode||g.dragged||e.button!==0||e.shiftKey||Math.hypot(e.clientX-g.x,e.clientY-g.y)>VIEW_CLICK_THRESHOLD)return;
  const r=canvas.getBoundingClientRect(),p=[e.clientX-r.left,e.clientY-r.top];
  if(p[0]<0||p[1]<0||p[0]>r.width||p[1]>r.height)return;
  if(jobMode==='pick'&&repeatPick&&(pendingRepeatPick===repeatPick||targetNameEdit))return;
  if(jobMode==='worldpick'&&worldPickCallback){
   const cb=worldPickCallback,world=worldAtPixel(p);setMode('orbit');
   $('job-selection-info').textContent='Tunnel interior point picked';cb(world);return;
  }
  if(jobMode==='pick'&&setupRayPick){
   const b=jobScene.basis(),t=Math.tan(jobScene.setupCamera.fov*Math.PI/360),nx=p[0]/r.width,ny=p[1]/r.height;
   const ray=[0,1,2].map(i=>b.forward[i]+(2*nx-1)*t*(r.width/r.height)*b.right[i]+(1-2*ny)*t*b.up[i]);
   const hit=jobScene.setupBackdrop?.hit(ray);if(!hit){notice('No photograph covers this direction. Try another point.');return;}
   const thumbnail=jobScene.setupBackdrop.thumbnail(nx,ny);
   const previewPoint=setupState.origin.map((v,i)=>v+ray[i]);
   void deliverPick(setupRayPick,{...hit,thumbnail,previewPoint});return;
  }
  if(jobMode==='pick'&&pointPickCallback){
   const sample=cleanSnapshot||deletedMode?null:validSnap(snapIndex,snapFrame())?indexedSnap(snapIndex,p):nearestDisplayedPoint(p,true),world=sample?.point;
   if(!world){notice('No displayed scan point is close enough to the cursor. Increase display detail or zoom closer.',true);return;}
   $('job-selection-info').textContent=pointPickDone;void deliverPick(pointPickCallback,world,sample);return;
  }
  if(!['orbit','pan'].includes(jobMode))return;
  if(previewOwner==='shape'&&window.ShapeSelectionUI?.pickMatch(p))return;
  if(!setupState&&window.SceneObjectsUI?.pickMeasurement(p,worldToPixel,e))return;
  if(cleanSnapshot&&window.SurfaceUI?.reviewActive?.()){
   const id=nearestCleaningCluster(p);if(id!==null)window.SurfaceUI.selectClusters([id],e.ctrlKey||e.metaKey);return;
  }
  const m=nearestTarget(p);if(m)selectScanTarget(m,e.ctrlKey||e.metaKey);
 });
 canvas.addEventListener('pointerdown',e=>{if(!['box','lasso','target-box'].includes(jobMode)||busy||!packed)return;const r=canvas.getBoundingClientRect(),p=[e.clientX-r.left,e.clientY-r.top];dragStart=p;canvas.setPointerCapture(e.pointerId);if(jobMode==='box'||jobMode==='target-box'){selBox.style.display='block';Object.assign(selBox.style,{left:p[0]+'px',top:p[1]+'px',width:'0px',height:'0px'});}else{lasso=[p];drawSelectionOverlay(lasso,false);}});canvas.addEventListener('pointermove',e=>{if(!dragStart||!['box','lasso','target-box'].includes(jobMode))return;const r=canvas.getBoundingClientRect(),p=[e.clientX-r.left,e.clientY-r.top];if(jobMode==='box'||jobMode==='target-box'){Object.assign(selBox.style,{left:Math.min(p[0],dragStart[0])+'px',top:Math.min(p[1],dragStart[1])+'px',width:Math.abs(p[0]-dragStart[0])+'px',height:Math.abs(p[1]-dragStart[1])+'px'});}else if(!lasso.length||Math.hypot(p[0]-lasso.at(-1)[0],p[1]-lasso.at(-1)[1])>4){lasso.push(p);if(lasso.length>500)lasso=simplifyPath(lasso,250);drawSelectionOverlay(lasso,false);}});canvas.addEventListener('pointerup',e=>{if(!dragStart||!['box','lasso','target-box'].includes(jobMode))return;const r=canvas.getBoundingClientRect(),end=[e.clientX-r.left,e.clientY-r.top],start=dragStart;dragStart=null;selBox.style.display='none';if(jobMode==='box'||jobMode==='target-box'){if(Math.abs(end[0]-start[0])<4||Math.abs(end[1]-start[1])<4)return;if(jobMode==='target-box'){const keys=targetMarkers.filter(m=>m.status!=='rejected').filter(m=>{const p=projectTargetMarker(m);return p&&p[0]>=Math.min(start[0],end[0])&&p[0]<=Math.max(start[0],end[0])&&p[1]>=Math.min(start[1],end[1])&&p[1]<=Math.max(start[1],end[1]);}).map(m=>m.key);selectScanTargets(keys,e.ctrlKey||e.metaKey);return;}finishSelection([[Math.min(start[0],end[0]),Math.min(start[1],end[1])],[Math.max(start[0],end[0]),Math.min(start[1],end[1])],[Math.max(start[0],end[0]),Math.max(start[1],end[1])],[Math.min(start[0],end[0]),Math.max(start[1],end[1])]]);}else{lasso.push(end);const pts=lasso.slice();lasso=[];if(pts.length>=3)finishSelection(pts);else drawSelectionOverlay([]);}});
 function editableScans(){const visible=data.items.filter(x=>x.kind==='scan'&&x.visible&&!x.locked);return visible.length?visible:(current()?.kind==='scan'&&!current()?.locked?[current()]:[]);}
 async function multiAction(name,extra={},useSelection=true){
  if(deletedMode){notice('Use Restore selected points in the deleted-point view, or Back to scan to edit retained points.');return false;}
  if(busy||uploading)return false;
  if(cleanSnapshot){
   if(previewOwner==='circle'){if(name==='delete_selection')return window.CircleCleanUI?.removeSelected();notice('Use local-circle review to remove or separate reviewed departures.');return false;}
   if(previewOwner==='shape'){if(name==='delete_selection')return window.ShapeSelectionUI?.removeSelected();notice('Use the shape review to remove or separate matching points, or Restore normal view before editing the scan.');return false;}
   if(previewOwner==='feature'){if(name==='delete_selection')return window.FeatureSelectionUI?.removeSelected();notice('Use the local feature review to refine this selection, or Restore normal view to edit the scan.',true);return false;}
   if(window.SurfaceUI?.reviewActive?.()){if(name==='delete_selection')return selectionSpec?window.SurfaceUI.markSelection(false):window.SurfaceUI.deleteSelectedGroups();if(['hide_selection','isolate_selection','clear_view'].includes(name)){window.SurfaceUI.clusterView(name==='hide_selection'?'hide':name==='isolate_selection'?'isolate':'all');return false;}}
   showTunnelMessage('Cleaning preview','Use the Review controls or restore the normal view before editing scene scans.',false);return false;
  }
  const targets=editableScans(),capturedSelection=selectionSpec?JSON.parse(JSON.stringify(selectionSpec)):null;
  if(useSelection&&!capturedSelection||!targets.length)return false;
  previewRequested=true;signature='';const history_group=Date.now().toString(36)+Math.random().toString(36).slice(2),timed=['delete_selection','separate_selection','copy_selection'].includes(name),started=performance.now();let completed=false,deletion=null;
  const title=name==='delete_selection'?'Deleting selected points':name==='separate_selection'?'Separating selected points':'Copying selected points';
  try{
   if(name==='delete_selection'){
    // Selection flags were made during the existing highlight pass. Deleting
    // only flips a GPU uniform: no second per-display-point walk or XYZ edit.
    deletion={committed:false,previewGeneration:jobScene.previewGeneration||0,selection:capturedSelection,ids:targets.map(x=>x.id),generation:++pointEditGeneration,packed,points:jobScene.pointValues,origin:previewOrigin.slice(),owners:pointOwners.map(x=>x.slice()),scanIds:new Set(data.items.filter(x=>x.kind==='scan').map(x=>x.id)),flags:jobScene.deletionFlags,flagCount:jobScene.deletionFlagCount||0,setup:setupState?.id||null,meshKey:previewContentKey()};pendingPointDelete=deletion;jobScene.pendingDelete=!!jobScene.deletionFlagCount;jobScene.draw();
    window.WorkStatus?.show('Points hidden · saving deletion',(targets.length+' scan'+(targets.length===1?'':'s'))+' · saving the exact original-point selection…',true);
    const onProgress=job=>window.WorkStatus?.show('Saving point deletion',job.message||'Updating exact point masks…',!!job.busy);
    const ok=await action({action:'delete_selection_many',ids:targets.map(x=>x.id),selection:capturedSelection,history_group,...extra},null,{skipSync:true,onProgress,keepBusy:true,pollMs:100});
    if(!ok)return false;completed=true;if(pendingPointDelete===deletion)pendingPointDelete.committed=true;await finishPointDeletion(deletion);return true;
   }
   for(let index=0;index<targets.length;index++){
    const t=targets[index],body={action:name,id:t.id,history_group,...extra};if(useSelection)body.selection=capturedSelection;
    const onProgress=timed?job=>window.WorkStatus?.show(title,`${t.name} · scan ${index+1} of ${targets.length} · ${job.message||'Checking original points…'}`,!!job.busy):null;
    if(timed)window.WorkStatus?.show(title,`${t.name} · checking original retained points`,true);
    const ok=await action(body,null,{skipSync:true,onProgress});if(!ok)return false;
   }
   completed=true;return true;
  }finally{
   if(name==='delete_selection'){
    if(!completed&&pendingPointDelete===deletion){pendingPointDelete=null;jobScene.pendingDelete=false;jobScene.renderSelection();}
    setBusy(false);
    if(!completed){signature=viewSignature();finishPreviewFeedback();}
    if(completed)schedulePointDeletionDetail(deletion);
   }else{
    if(timed&&completed)window.WorkStatus?.show('Edit saved · refreshing view','Point changes are saved. Loading the current display…',true);
    await sync();
   }
   if(timed)window.WorkStatus?.show(completed?'Point edit complete':'Point edit stopped',completed?`${((performance.now()-started)/1000).toFixed(1)} s · exact point changes saved · Undo restores the edit.`:(state?.job?.message||'The last completed edit remains available in Undo.'),false,!completed&&!state?.job?.cancelled);
  }
 }
 function compactDeletedDisplay(edit){
  // This is a display-only compaction after the exact server edit has committed.
  // Reuse the highlight mask already computed before Delete; original XYZ and
  // full-density mask decisions remain entirely on the server.
  const snapshot=cleanSnapshot?.packed===edit?.packed?cleanSnapshot:null;
  if(!edit||(!snapshot&&(packed!==edit.packed||previewOwner||cleanSnapshot||deletedMode||(setupState?.id||null)!==edit.setup))||!edit.owners.length||!edit.flags)return false;
  const expected=edit.owners.reduce((n,[a,b,id])=>n+(edit.scanIds.has(id)?b-a:0),0);
  if(edit.flags.length!==expected)return false;
  const eligible=new Set(edit.ids),values=new Float32Array(edit.packed.length),owners=[];let write=0,scanRow=0;
  for(const [start,end,id] of edit.owners){
   const first=write/6,isScan=edit.scanIds.has(id),remove=isScan&&eligible.has(id);
   for(let row=start;row<end;row++){
    const flag=isScan?edit.flags[scanRow++]:0;
    if(remove&&flag)continue;
    const offset=row*6;for(let j=0;j<6;j++)values[write++]=edit.packed[offset+j];
   }
   owners.push([first,write/6,id]);
  }
  pendingPointDelete=null;jobScene.pendingDelete=false;
  if(snapshot){snapshot.packed=values.subarray(0,write);snapshot.owners=owners;snapshot.complete=false;jobScene.draw();return true;}
  clearSelection(false);
  packed=values.subarray(0,write);pointOwners=owners;pointPreviewComplete=false;
  sourceInfo={...sourceInfo,sample:write/6,stage:'edited'};
  jobScene.setPacked(packed,previewOrigin,owners,edit);$('job-empty').hidden=!!jobScene.pointCount||!!jobScene.meshCount;
  if(!jobScene.pointCount&&!jobScene.meshCount)$('job-empty').textContent='Selected points deleted · preparing remaining point detail';
  updatePointPreviewInfo();return true;
 }
 async function finishPointDeletion(edit){
  previewEpoch++;previewRequest?.controller.abort();clearTimeout(detailTimer);clearTimeout(pointRefineTimer);previewRefreshPending=false;
  // Scan masks changed, but connected triangle models and the current camera
  // did not. Invalidate old point samples without requesting the whole scene.
  normalOverview=null;setupOverview=null;setupPreviews.clear();
  if(!compactDeletedDisplay(edit)&&packed!==edit.packed){pendingPointDelete=null;jobScene.pendingDelete=false;jobScene.draw();}
  await sync({inventoryOnly:true,retainDisplay:true,allowBusy:true});
  if(meshPreview?.key===edit.meshKey)meshPreview={...meshPreview,key:previewContentKey()};
  updatePointPreviewInfo();finishPreviewFeedback();
 }
 function schedulePointDeletionDetail(edit){
  clearTimeout(pointRefineTimer);
  const content=previewContentKey(),setup=setupState?.id||null;
  pointRefineTimer=setTimeout(()=>{
   pointRefineTimer=null;
   if(!edit||edit.generation!==pointEditGeneration||busy||uploading||previewOwner||cleanSnapshot||deletedMode||content!==previewContentKey()||setup!==(setupState?.id||null))return;
   signature='';previewRequested=true;
   if(loading)previewRefreshPending=true;else sync();
  },350);
 }
 const selAction=(name,extra={})=>multiAction(name,extra,true);
 $('job-hide-selection').onclick=()=>selAction('hide_selection');$('job-isolate-selection').onclick=()=>selAction('isolate_selection');$('job-clear-view').onclick=()=>multiAction('clear_view',{},false);$('job-delete-selection').onclick=()=>{if(previewOwner==='circle'){window.CircleCleanUI?.removeSelected();return;}if(previewOwner==='shape'){window.ShapeSelectionUI?.removeSelected();return;}if(previewOwner==='feature'){window.FeatureSelectionUI?.removeSelected();return;}if(window.SurfaceUI?.reviewActive?.()){selAction('delete_selection');return;}if(selectionSpec)selAction('delete_selection',{reason:'Main 3D workspace selection'});};$('job-separate-selection').onclick=()=>{if(selectionSpec&&confirm('Move selected points to new scans and remove them from the source working scans? Undo remains available.'))selAction('separate_selection');};$('job-undo-edit').onclick=()=>window.HistoryUI?.apply(false);$('job-redo-edit').onclick=()=>window.HistoryUI?.apply(true);
 function setView(name){if(setupState&&name!=='fit'){notice('Return to normal 3D to use plan, elevation or isometric views.',true);return;}if(name==='fit'){if(!fitScanArea())fitWholeScene();return;}window.CadUI?.releaseViewLock();if(name==='top'){const model=window.CadUI?.modelSpace();jobScene.pitch=model?-Math.PI/2:Math.PI/2;jobScene.yaw=model?0:Math.PI;}else if(name==='front'){jobScene.pitch=0;jobScene.yaw=Math.PI;}else if(name==='right'){jobScene.pitch=0;jobScene.yaw=Math.PI/2;}else if(name==='iso'){jobScene.pitch=.62;jobScene.yaw=-.78;}jobScene.onNavigate?.();jobScene.draw();}
 function faceWorkPlane(normal){
  if(setupState)return;
  const [x,y,z]=normal;
  jobScene.pitch=Math.atan2(z,Math.hypot(x,y));
  jobScene.yaw=Math.hypot(x,y)<1e-8?Math.PI:Math.atan2(x,y);
 }
 for(const b of document.querySelectorAll('[data-job-nav]'))b.onclick=()=>{const n=b.dataset.jobNav;if(n==='orbit'||n==='pan'){if(n==='orbit')window.CadUI?.releaseViewLock();setMode(n);jobScene.draw();}else setView(n);};
 function measurePoint(){return armPointPick(p=>showTunnelMessage('Point coordinates',`E / X: ${p[0].toFixed(4)} m\nN / Y: ${p[1].toFixed(4)} m\nH / Z: ${p[2].toFixed(4)} m\nPicked from the displayed scan points.`,false),{prompt:'Point XYZ · click a scan point'});}
 function measureDistance(){const source=current()?.id;return armPointPick(a=>{armPointPick(async q=>{try{const saved=await post('/api/scene-object',{action:'add_measurement',source_id:source,points:[a,q]});await sync();window.SceneObjectsUI.select(saved.id);$('job-selection-info').textContent='Distance saved · click the line to select · Delete removes it';window.WorkStatus?.show('Distance saved',`${Math.hypot(...q.map((v,i)=>v-a[i])).toFixed(4)} m · selectable line in Geometry & references`);}catch(e){notice(e.message,true);}},{prompt:'2-point distance · first point stored · click second point'});},{prompt:'2-point distance · click first point'});}

 function selectionStats(){const displayed=jobScene.pointValues||packed;if(window.WorkspaceUI)return window.WorkspaceUI.fullStats();if(!selectionSpec||!displayed?.length){showTunnelMessage('No selection','Draw a box, polygon or lasso around points in the main 3D view first.',true);return;}let lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity],n=0;for(let i=0;i<displayed.length;i+=6){const p=[displayed[i]+previewOrigin[0],displayed[i+1]+previewOrigin[1],displayed[i+2]+previewOrigin[2]];if(!selectedBySpec(p,selectionSpec))continue;n++;for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],p[j]);hi[j]=Math.max(hi[j],p[j]);}}if(!n){showTunnelMessage('No displayed points selected','This selection currently contains no displayed points. Draw a new selection or load more detail.',true);return;}showTunnelMessage('Selection statistics',`${n.toLocaleString('en-GB')} displayed points\nΔE/X ${(hi[0]-lo[0]).toFixed(3)} m\nΔN/Y ${(hi[1]-lo[1]).toFixed(3)} m\nΔH/Z ${(hi[2]-lo[2]).toFixed(3)} m\nFull-density tools use the corresponding 3D selection volume.`,false);}
 function projectTargetMarker(marker){return worldToPixel(marker.source);}
 function drawSetupPickPreview(){
  const pin=$('setup-pick-preview');if(!pin)return;
  const pick=setupPickPreview,pixel=pick&&pick.id===setupState?.id?worldToPixel(pick.world):null;
  const width=canvas.clientWidth,height=canvas.clientHeight;
  pin.hidden=!pixel||pixel[0]<0||pixel[0]>=width||pixel[1]<0||pixel[1]>=height;
  if(pin.hidden)return;
  pin.style.left=pixel[0]+'px';pin.style.top=pixel[1]+'px';
  pin.classList.toggle('setup-pick-left',pixel[0]>width*.65);
  pin.classList.toggle('setup-pick-above',pixel[1]>height-84);
 }
 function showSetupPick(id,point,measured=false){
  if(id!==setupState?.id||point?.length!==3||!point.every(Number.isFinite))return;
  let pin=$('setup-pick-preview');
  if(!pin){
   pin=document.createElement('div');pin.id='setup-pick-preview';
   const label=document.createElement('span');label.setAttribute('role','status');pin.append(label);
   $('job-marker-labels').append(pin);
  }
  const world=measured?targetWorld(data.items.find(x=>x.id===id),point):point;
  setupPickPreview={id,world};
  pin.classList.toggle('setup-pick-measured',measured);
  pin.firstElementChild.textContent=measured?'Picked point · Scan XYZ (m)\n'+point.map((v,i)=>'XYZ'[i]+' '+fmt(v,6)).join('\n'):'Reading measured XYZ…';
  drawSetupPickPreview();
 }
 function clearSetupPick(){setupPickPreview=null;drawSetupPickPreview();}
 function closeTargetName(){
  targetNameEdit?.form.remove();targetNameEdit=null;
 }
 function openTargetName(marker){
  if(busy||targetNameEdit?.saving||!targetOwnerVisible(marker.scanId))return;
  closeTargetName();
  const form=document.createElement('form');form.className='job-target-editor';
  form.innerHTML='<input aria-label="Target name" maxlength="60" required><button type="submit">Save</button><button type="button" aria-label="Cancel rename">×</button><small role="status"></small>';
  const [input,save,cancel,message]=form.children;
  const editor={...marker,form,saving:false};targetNameEdit=editor;
  input.value=marker.id;
  const dismiss=()=>{if(!editor.saving&&targetNameEdit===editor){closeTargetName();jobScene.labelButtons?.get(targetLabelKey(marker))?.firstElementChild.focus();}};
  cancel.onclick=dismiss;
  form.onkeydown=e=>{e.stopPropagation();if(e.key==='Escape'){e.preventDefault();dismiss();}};
  form.onsubmit=async e=>{
   e.preventDefault();const name=input.value.trim();
   if(editor.saving||busy||!targetOwnerVisible(marker.scanId))return;
   if(!name){message.textContent='Enter a target name.';return;}
   if(name===marker.id){dismiss();return;}
   editor.saving=input.disabled=save.disabled=cancel.disabled=true;
   try{
    await window.TargetUI.renameMarker(marker.scanId,marker.key,name);
    if(targetNameEdit===editor)closeTargetName();
   }catch(error){message.textContent=error.message;}
   finally{
    editor.saving=input.disabled=save.disabled=cancel.disabled=false;
    if(targetNameEdit===editor)input.focus();
   }
  };
  $('job-marker-labels').append(form);jobScene.drawLabels();input.focus();input.select();
 }
 function nearestTarget(pixel,maxPx=22){let best=null,bd=maxPx*maxPx;for(const m of targetMarkers){if(m.status==='rejected')continue;const p=projectTargetMarker(m);if(!p)continue;const d=(p[0]-pixel[0])**2+(p[1]-pixel[1])**2;if(d<bd){best=m;bd=d;}}return best;}

 const menu=$('job-context-menu');function hideMenu(){if(menu)menu.hidden=true;}function showMenu(e){if(window.WorkspaceUI)return window.WorkspaceUI.viewportMenu(e);if(!menu)return;const r=canvas.getBoundingClientRect(),px=[e.clientX-r.left,e.clientY-r.top],target=nearestTarget(px),x=current(),buttons=[];const add=(label,fn,danger=false)=>buttons.push({label,fn,danger});if(cleanSnapshot&&window.SurfaceUI?.reviewActive?.()){const id=nearestCleaningCluster(px);if(id!==null&&!window.SurfaceUI.selectedClusters().includes(id))window.SurfaceUI.selectClusters([id]);add('Keep selected clusters',()=>window.SurfaceUI.markClusters(true));add('Remove selected clusters',()=>window.SurfaceUI.deleteSelectedGroups(),true);add('Hide selected clusters',()=>window.SurfaceUI.clusterView('hide'));add('Show all clusters',()=>window.SurfaceUI.clusterView('all'));if(selectionSpec){add('Keep selected points',()=>window.SurfaceUI.markSelection(true));add('Remove selected points',()=>window.SurfaceUI.markSelection(false),true);}add('Continue to Output',()=>window.SurfaceUI.continueToOutput());}else if(target){add('Select target',()=>selectScanTarget(target));add('Delete selected targets',async()=>{await window.ScanRegistrationUI?.activateTargets(current().id);if(!targetSelection.has(target.key))window.TargetUI?.selectTargets([target.key]);window.TargetUI?.deleteSelectedTargets();},true);add('Targets window',()=>window.TargetUI?.openManager());}else{add('Fit / zoom to item',()=>jobScene.fit());if(x){add(x.visible?'Hide item':'Show item',()=>action({action:'visibility',id:x.id}));add('Isolate item',()=>$('job-isolate')?.click());add('Rename',()=>$('job-rename')?.click());add('Duplicate',()=>action({action:'duplicate',id:x.id}));if(selectionSpec){add('Hide selected points',()=>selAction('hide_selection'));add('Isolate selected points',()=>selAction('isolate_selection'));add('Separate selection to new scan',()=>selAction('separate_selection'));add('Delete selected points',()=>selAction('delete_selection',{reason:'Context-menu delete'}),true);}if(x.kind==='scan')add('Extract surface',openSurface);add('Create mesh',()=>window.TunnelRibbon?.view?.('structures'));add('Delete selected items…',()=>$('job-remove')?.click(),true);}}menu.innerHTML=buttons.map((b,i)=>`<button data-menu-index="${i}" class="${b.danger?'danger':''}">${esc(b.label)}</button>`).join('');for(const b of menu.querySelectorAll('[data-menu-index]'))b.onclick=()=>{const f=buttons[+b.dataset.menuIndex]?.fn;hideMenu();f?.();};menu.hidden=false;menu.style.left=Math.min(r.width-210,Math.max(4,px[0]))+'px';menu.style.top=Math.min(r.height-40*buttons.length-8,Math.max(4,px[1]))+'px';}
 jobScene.onNavigationStart=hideMenu;
 canvas.addEventListener('contextmenu',e=>{e.preventDefault();if(!jobScene.suppressContextMenu?.()&&!isPicking()&&jobMode!=='polygon'&&!polygonFrame)showMenu(e);});document.addEventListener('pointerdown',e=>{if(menu&&!menu.hidden&&!menu.contains(e.target))hideMenu();});
 document.addEventListener('keydown',async e=>{
  // A modal owns Escape while open. Otherwise Escape cancels the scene selection
  // even when a settings input still has focus after the user changed a value.
  if(e.key==='Escape'){
   if(e.defaultPrevented||document.querySelector('dialog[open]'))return;
   e.preventDefault();pointPickCallback=null;worldPickCallback=null;areaSelectionCallback=null;clickGesture=null;
   clearSelection();if(window.SurfaceUI?.reviewActive?.())window.SurfaceUI.selectClusters([]);hideMenu();setMode('orbit');return;
  }
  if(e.defaultPrevented||document.querySelector('dialog[open]'))return;
  const tag=document.activeElement?.tagName;if(['INPUT','TEXTAREA','SELECT'].includes(tag)||document.activeElement?.isContentEditable)return;if((jobMode==='polygon'||polygonFrame)&&['Enter','Backspace','Delete'].includes(e.key)){e.preventDefault();if(e.key==='Enter')finishPolygon();else if(e.key==='Backspace')undoPolygonCorner();else polygonUI('Finish the polygon before deleting points. Escape cancels.');return;}const k=e.key.toLowerCase();if(deletedMode&&(e.key==='Delete'||(e.ctrlKey||e.metaKey)&&['c','v'].includes(k))){e.preventDefault();notice('Use Restore selected points, or Back to scan before editing retained points.');return;}if(e.key==='Delete'&&window.SceneObjectsUI?.selected()){e.preventDefault();if(!busy)await window.SceneObjectsUI.removeSelected();return;}if(previewOwner==='circle'&&e.key==='Delete'){e.preventDefault();if(!busy)window.CircleCleanUI?.removeSelected();return;}if(previewOwner==='shape'&&e.key==='Delete'){e.preventDefault();if(!busy)window.ShapeSelectionUI?.removeSelected();return;}if(previewOwner==='feature'&&e.key==='Delete'){e.preventDefault();if(!busy)window.FeatureSelectionUI?.removeSelected();return;}if(cleanSnapshot&&window.SurfaceUI?.reviewActive?.()){if(e.key==='Delete'){e.preventDefault();if(!busy){if(selectionSpec)await window.SurfaceUI.markSelection(false);else await window.SurfaceUI.deleteSelectedGroups();}return;}if((e.ctrlKey||e.metaKey)&&['z','y'].includes(k)){e.preventDefault();showTunnelMessage('Cleaning review','Use Restore automatic suggestion in Review to reset the cleaning decisions.',false);return;}}if((e.ctrlKey||e.metaKey)&&k==='z'){e.preventDefault();if(e.shiftKey)$('job-redo-edit')?.click();else $('job-undo-edit')?.click();return;}if((e.ctrlKey||e.metaKey)&&k==='y'){e.preventDefault();$('job-redo-edit')?.click();return;}if((e.ctrlKey||e.metaKey)&&k==='c'){e.preventDefault();internalClipboard=selectionSpec?{kind:'selection',selection:JSON.parse(JSON.stringify(selectionSpec))}:{kind:'item',id:selected};notice('Copied to Tunnel Studio clipboard.');return;}if((e.ctrlKey||e.metaKey)&&k==='v'){e.preventDefault();if(internalClipboard?.kind==='selection'){const old=selectionSpec;selectionSpec=JSON.parse(JSON.stringify(internalClipboard.selection));selectionBoundaryWorld=selectionWorldBoundary(selectionSpec);await selAction('copy_selection');selectionSpec=old;selectionBoundaryWorld=selectionWorldBoundary(selectionSpec);}else if(internalClipboard?.kind==='item'){await action({action:'duplicate',id:internalClipboard.id});}return;}if(e.key==='Delete'){if(window.SceneObjectsUI?.selected()){e.preventDefault();await window.SceneObjectsUI.removeSelected();return;}if(targetSelection.size){e.preventDefault();await window.TargetUI?.deleteSelectedTargets();return;}if(selectionSpec){e.preventDefault();await selAction('delete_selection',{reason:'Delete key'});}else{await removeSelected();}}
 });
 $('job-maximise').onclick=()=>{document.body.classList.toggle('job-maximised');$('job-maximise').textContent=document.body.classList.contains('job-maximised')?'Restore layout':'Maximise 3D';setTimeout(()=>jobScene.draw(),80);};$('job-collapse-sidebar').onclick=()=>{document.body.classList.toggle('job-sidebar-collapsed');$('job-collapse-sidebar').textContent=document.body.classList.contains('job-sidebar-collapsed')?(document.body.classList.contains('job-sidebar-left')?'›':'‹'):(document.body.classList.contains('job-sidebar-left')?'‹':'›');setTimeout(()=>jobScene.draw(),50);};
 const splitter=$('job-splitter'),sidebar=document.querySelector('.job-sidebar'),sideHead=$('job-sidehead');let splitStart=null,floatDrag=null;function sideDock(mode){document.body.classList.remove('job-sidebar-left','job-sidebar-floating');if(mode==='left')document.body.classList.add('job-sidebar-left');if(mode==='float')document.body.classList.add('job-sidebar-floating');$('job-collapse-sidebar').textContent=document.body.classList.contains('job-sidebar-collapsed')?(mode==='left'?'›':'‹'):(mode==='left'?'‹':'›');localStorage.setItem('tunnelstudio.jobSidebarDock',mode);if(sidebar&&mode!=='float'){sidebar.style.left='';sidebar.style.top='';sidebar.style.right='';sidebar.style.width='';sidebar.style.height='';}setTimeout(()=>jobScene.draw(),40);}splitter.onpointerdown=e=>{splitStart=[e.clientX,sidebar.getBoundingClientRect().width];splitter.setPointerCapture(e.pointerId);};splitter.onpointermove=e=>{if(!splitStart)return;const dir=document.body.classList.contains('job-sidebar-left')?1:-1,w=Math.max(210,Math.min(Math.min(700,Math.max(240,innerWidth-340)),splitStart[1]+dir*(e.clientX-splitStart[0])));document.documentElement.style.setProperty('--job-panel-width',w+'px');localStorage.setItem('tunnelstudio.jobPanelWidth',w);jobScene.draw();};splitter.onpointerup=()=>splitStart=null;const saved=+localStorage.getItem('tunnelstudio.jobPanelWidth');if(saved>=210&&saved<=700)document.documentElement.style.setProperty('--job-panel-width',saved+'px');sideDock(localStorage.getItem('tunnelstudio.compactWorkspace')?localStorage.getItem('tunnelstudio.jobSidebarDock')||'left':'left');localStorage.setItem('tunnelstudio.compactWorkspace','1');$('job-sidebar-left').onclick=()=>sideDock('left');$('job-sidebar-right').onclick=()=>sideDock('right');$('job-sidebar-float').onclick=()=>sideDock('float');if(sideHead){sideHead.addEventListener('pointerdown',e=>{if(!document.body.classList.contains('job-sidebar-floating')||e.target.closest('button'))return;const r=sidebar.getBoundingClientRect();floatDrag=[e.clientX,e.clientY,r.left,r.top];sideHead.setPointerCapture(e.pointerId);});sideHead.addEventListener('pointermove',e=>{if(!floatDrag)return;const host=$('tab-job').getBoundingClientRect(),w=sidebar.getBoundingClientRect().width,h=sidebar.getBoundingClientRect().height,left=Math.max(4,Math.min(host.width-w-4,floatDrag[2]-host.left+e.clientX-floatDrag[0])),top=Math.max(4,Math.min(host.height-h-4,floatDrag[3]-host.top+e.clientY-floatDrag[1]));sidebar.style.left=left+'px';sidebar.style.top=top+'px';sidebar.style.right='auto';});sideHead.addEventListener('pointerup',()=>floatDrag=null);}
 function bundleSetups(){
  if(setupState)return [];
  const id=window.WorkspaceUI?.selectedBundleId?.();
  const ids=new Set(data.bundles?.find(b=>b.id===id)?.scan_ids||[]);
  return data.items.filter(x=>ids.has(x.id)&&!x.merge_archived);
 }
 function targetOwners(){
  if(deletedMode)return [];
  const bundle=bundleSetups();
  if(bundle.length)return bundle;
  const x=current();
  return x?.kind==='scan'&&(x.visible!==false||setupState?.id===x.id)?[x]:[];
 }
 function targetOwnerVisible(id){return targetOwners().some(x=>x.id===id);}
 function targetLabelKey(marker){return JSON.stringify([marker.scanId,marker.key]);}
 function refreshTargetMarkers(){
  targetMarkers=targetOwners().flatMap(x=>(x.registration_summary?.markers||[]).map(m=>({...m,scanId:x.id,scanName:x.name})));
  jobScene.markers(targetMarkers);jobScene.draw();
 }
 function targets(candidates=[],rows=[],sourceId=null){
  const byCandidate=new Map(rows.filter(r=>r.candidate_id).map(r=>[r.candidate_id,r]));
  const merged=candidates.map(c=>{
   const row=byCandidate.get(c.id);
   return {...c,id:row?.id||c.name||c.id,key:'candidate:'+c.id,role:row?(row.role||'fit'):null};
  });
  for(const r of rows)if(r.source?.every(Number.isFinite)&&!merged.some(c=>c.id===r.id||c.key==='candidate:'+r.candidate_id))
   merged.push({id:r.id,key:'row:'+r.id,source:r.source,status:'accepted',role:r.role||'fit'});
  const id=sourceId||data.active_registration||selected,x=data.items.find(x=>x.id===id);
  if(x){for(const marker of merged)if(marker.source?.every(Number.isFinite))marker.source=targetWorld(x,marker.source);x.registration_summary??={links:[]};x.registration_summary.markers=merged;x.registration_summary.targets=rows.map(r=>({name:r.id,role:r.role||'fit'}));}
  refreshTargetMarkers();details();
 }
 async function selectScanTargets(keys,append=false){const x=current();if(!x||x.kind!=='scan')return;try{await window.ScanRegistrationUI?.activateTargets(x.id);if(current()?.id!==x.id)return;window.TargetUI?.selectTargets(keys,append);window.TunnelRibbon?.view('targets');}catch(e){notice(e.message,true);}}

 function selectScanTarget(marker,append=false){
  if(marker.scanId&&marker.scanId!==current()?.id)chooseItem(marker.scanId);
  return selectScanTargets([marker.key],append);
 }
 function highlightTargets(keys){targetSelection=new Set(keys);jobScene.markers(targetMarkers);jobScene.draw();}
 function targetWorld(x,p){const R=x?.registration_rotation,t=x?.registration_translation||[0,0,0];return R?R.map((row,j)=>row.reduce((sum,v,k)=>sum+v*p[k],t[j])):p.slice();}
 function focusTarget(source,scale){source=targetWorld(data.items.find(x=>x.id===data.active_registration)||current(),source);if(setupState){lookAtSetupPoint(source);return;}jobScene.target=source.map((v,i)=>v-previewOrigin[i]);jobScene.scale=scale;jobScene.draw();loadViewDetail(true);}

 function useWholePreview(){
  detailView=null;signature='';previewRequested=true;
  if(previewRequest&&previewRequest.view!=='null')previewRequest.controller.abort();
  const restored=restoreOverview();if(loading)previewRefreshPending=true;else if(!restored)sync();return restored;
 }
 function scanFitCorners(scan){
  let {minimum,maximum}=scan;
  if(minimum?.length!==3||maximum?.length!==3||!minimum.every(Number.isFinite)||!maximum.every(Number.isFinite))return [];
  if(scan.limit_box){
   const corners=window.TunnelBox.corners(scan.limit_box);
   minimum=minimum.map((v,i)=>Math.max(v,Math.min(...corners.map(p=>p[i]))));
   maximum=maximum.map((v,i)=>Math.min(v,Math.max(...corners.map(p=>p[i]))));
   if(corners.length&&corners.every(p=>p.every((v,i)=>v>=minimum[i]&&v<=maximum[i])))return corners;
  }
  if(minimum.some((v,i)=>v>maximum[i]))return [];
  return Array.from({length:8},(_,k)=>minimum.map((v,i)=>k&(1<<i)?maximum[i]:v));
 }
 function fitScanArea(){
  if(setupState||window.CadUI?.modelSpace?.()&&!window.CadUI.cloudReference())return false;
  if(selectionSpec){
   try{
    const bounds=selectedBounds(selectionSpec);
    focusWorld(window.TunnelBox.corners(bounds));
    fitPreviewPending=false;jobScene.initialised=true;return true;
   }catch(e){notice('The highlighted points are outside the current preview.',true);return true;}
  }
  if(previewOwner&&jobScene.original?.length){jobScene.fit();return true;}
  const visible=data.items.filter(x=>x.kind==='scan'&&x.visible!==false&&!x.merge_archived&&!x.merge_stale);
  const chosen=visible.filter(x=>selectedItems.has(x.id)||x.id===selected);
  const scans=chosen.length?chosen:visible;
  if(!scans.length)return false;
  const points=scans.flatMap(scanFitCorners);
  if(!points.length){notice('The current scan area has no bounds to fit.',true);return true;}
  focusWorld(points);fitPreviewPending=false;jobScene.initialised=true;
  return true;
 }
 function fitWholeScene(){
  if(window.CadUI?.modelSpace?.()&&!window.CadUI.cloudReference()){const models=window.CadUI.visibleBounds();if(models.length)focusWorld(models);return;}

  if(setupState){jobScene.fit();if(detailView)useWholePreview();return;}
  const models=window.CadUI?.visibleBounds?.()||[];if(models.length){focusWorld([...models,...data.items.filter(x=>x.visible&&x.minimum&&x.maximum).flatMap(x=>[x.minimum,x.maximum])]);return;}
  if(detailView){if(useWholePreview())jobScene.fit();else jobScene.initialised=false;}else jobScene.fit();
 }
 function viewStillCovered(view){
  const b=jobScene.basis();if(['right','up','forward'].some(k=>dot(b[k],view[k])<.9995))return false;
  if(setupState){const tangent=Math.tan(jobScene.setupCamera.fov*Math.PI/360),aspect=canvas.clientWidth/Math.max(1,canvas.clientHeight);return tangent<=view.scale*1.05&&tangent*aspect<=view.scale*view.aspect*1.05;}
  const d=jobScene.target.map((v,i)=>v+previewOrigin[i]-view.target[i]),aspect=canvas.clientWidth/Math.max(1,canvas.clientHeight);
  return Math.abs(dot(d,view.right))+jobScene.scale<=view.scale*1.13&&Math.abs(dot(d,view.up))+jobScene.scale/aspect<=view.scale/view.aspect*1.13&&Math.abs(dot(d,view.forward))<view.scale;
 }
 // Fine buffers include a whole-scene sample. Keep those measured points in
 // GPU memory while navigating instead of repeatedly reverting/re-uploading
 // the sparse overview. Coalesce movement into one current-view request.
 jobScene.onNavigate=()=>{clearTimeout(detailTimer);scheduleDetail();};
 jobScene.onViewportResize=scheduleDetail;
 function canLoadReviewDetail(){return previewOwner==='shape'&&['orbit','pan'].includes(jobMode)&&!busy;}
 function loadViewDetail(force=false){
  if(jobMode==='polygon'||polygonFrame)return;
  if(busy||liveLimitEditing||!jobScene.initialised)return;
  if(cleanSnapshot){
   if(canLoadReviewDetail()&&(autoDetail||force))window.ShapeSelectionUI?.loadViewDetail(viewportFrame(),viewStillCovered,force);
   return;
  }
  if(setupState){if((!force&&!autoDetail)||setupDisplay==='photo')return;if(!force&&detailView&&viewStillCovered(detailView))return;const next=setupDetail();if(!force&&JSON.stringify(next)===JSON.stringify(detailView))return;detailView=next;signature='';previewRequested=true;previewRequest?.controller.abort();if(loading)previewRefreshPending=true;else sync();return;}
  const visible=data.items.filter(x=>x.visible&&x.kind==='scan');if(!visible.length)return;
  const extent=Math.max(...[0,1,2].map(j=>Math.max(...visible.map(x=>x.maximum?.[j]??0))-Math.min(...visible.map(x=>x.minimum?.[j]??0))));
  const enabled=force||(autoDetail&&jobScene.scale<extent*.25);
  if(!force&&enabled&&detailView&&jobScene.scale>=detailView.scale*.6&&viewStillCovered(detailView))return;
  const next=enabled?viewportFrame():null;
  if(!force&&JSON.stringify(next)===JSON.stringify(detailView))return;detailView=next;signature='';previewRequested=true;previewRequest?.controller.abort();
  if(next)$('job-preview-info').textContent='Loading more detail in the current view · whole scan preview remains available…';else if(restoreOverview())return;
  if(loading)previewRefreshPending=true;else sync();
 }
 function viewportFrame(){return {target:jobScene.target.map((v,i)=>v+previewOrigin[i]),...jobScene.basis(),scale:jobScene.scale,aspect:canvas.clientWidth/Math.max(1,canvas.clientHeight)};}
 function scheduleDetail(){clearTimeout(detailTimer);if(jobMode==='polygon'||polygonFrame||cleanSnapshot&&previewOwner!=='shape'||deletedMode)return;detailTimer=setTimeout(()=>loadViewDetail(),350);}
 for(const event of ['wheel','pointerup'])canvas.addEventListener(event,scheduleDetail,{passive:true});
 function setDisplayEnhancement(contrast=1,gamma=1){displayContrast=Math.max(.25,Math.min(3,+contrast||1));displayGamma=Math.max(.25,Math.min(3,+gamma||1));for(const [name,value] of [['contrast',displayContrast],['gamma',displayGamma]]){for(const prefix of ['reg','ribbon']){const input=$(prefix+'-'+name),output=$(prefix+'-'+name+'-value');if(input)input.value=value;if(output)output.textContent=Math.round(value*100)+'%';}}jobScene.pointContrast=cleanSnapshot?1:displayContrast;jobScene.pointGamma=cleanSnapshot?1:displayGamma;jobScene.draw();}


 function selectedBounds(spec){
  const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];
  for(const displayed of [jobScene.pointValues||packed,...(jobScene.pointPages||[]).map(p=>p.values)]){
   for(let i=0;i<(displayed?.length||0);i+=6){
    const p=[0,1,2].map(j=>displayed[i+j]+previewOrigin[j]);
    if(selectedBySpec(p,spec))for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],p[j]);hi[j]=Math.max(hi[j],p[j]);}
   }
  }
  if(!lo.every(Number.isFinite))throw Error('Draw around visible scan points.');
  const pad=Math.max(.001,...hi.map((v,j)=>(v-lo[j])*.005));
  return {minimum:lo.map(v=>v-pad),maximum:hi.map(v=>v+pad)};
 }
 function armAreaSelection(callback,mode='box',reviewOwner=null){
  if(setupState){setMode('box');return false;}
  if(reviewOwner&&previewOwner!==reviewOwner)return false;
  if(!packed?.length){showTunnelMessage('Load the 3D preview','Load the scan preview first, then draw a box.',true);return false;}
  if(!reviewOwner)clearCleaningPreview();
  clearSelection();setMode(mode);areaSelectionCallback=callback;
  $('job-selection-info').textContent=reviewOwner?'Drag a box around the displayed matches.':'Draw a rectangle around the visible points. You can adjust the resulting X/Y/Z box limits.';
  return true;
 }
 function cancelAreaSelection(callback){if(areaSelectionCallback===callback){clearSelection();setMode('orbit');}}
 async function setLimitBox(box,hideOutside=true){const ids=data.items.filter(x=>x.kind==='scan'&&x.visible).map(x=>x.id);if(!ids.length)return false;clearCleaningPreview();const ok=await action({action:'limit_box',ids,box,hide_outside:hideOutside});if(ok){limitPreview=null;clearSelection();drawLimitBox();}return ok;}
 async function setLimitVisibility(enabled){const ids=data.items.filter(x=>x.kind==='scan'&&x.limit_box).map(x=>x.id);if(!ids.length)return false;return action({action:'limit_visibility',ids,hide_outside:enabled});}
 async function clearLimitBoxes(){const ids=data.items.filter(x=>x.kind==='scan'&&x.limit_box).map(x=>x.id);if(!ids.length){limitPreview=null;drawLimitBox();return true;}return action({action:'limit_box',ids,box:null});}
 let estimateLine=[],estimateObjectId=null;
 function refreshLimitPreview(enabled){if(setupState&&enabled){notice('Return to normal 3D to edit the limit box.',true);return;}if(liveLimitEditing===enabled)return;liveLimitEditing=enabled;previewRequested=true;signature='';jobScene.draw();if(loading)previewRefreshPending=true;else sync();}
 function focusWorld(points,refine=true){if(setupState){if(points?.length)lookAtSetupPoint(points[0]);return;}if(!points?.length)return;const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const p of points)for(let j=0;j<3;j++){lo[j]=Math.min(lo[j],p[j]);hi[j]=Math.max(hi[j],p[j]);}if(!lo.every(Number.isFinite))return;const centre=lo.map((v,j)=>(v+hi[j])/2);jobScene.target=centre.map((v,j)=>v-previewOrigin[j]);const basis=jobScene.basis(),aspect=$('job-canvas').clientWidth/Math.max(1,$('job-canvas').clientHeight);let extent=.25;for(const p of points){const d=p.map((v,j)=>v-centre[j]);extent=Math.max(extent,Math.abs(dot(d,basis.right)),Math.abs(dot(d,basis.up))*aspect);}jobScene.scale=extent*1.3;if(refine)jobScene.onNavigate?.();else{fitPreviewPending=false;jobScene.initialised=true;}jobScene.draw();}

 function geometryOverlay(rows=[],id=null){estimateObjectId=id;estimateLine=rows.map(r=>r.slice(1,4));drawLimitBox();}
 function drawBundleTargetRays(){
  if(deletedMode||previewOwner)return '';
  const lines=[];
  for(const setup of bundleSetups()){
   if(!setup.has_setup_view)continue;
   const start=worldToPixel(setup.scan_origin);
   if(!start)continue;
   for(const target of setup.registration_summary?.markers||[]){
    if(target.role!=='fit'||target.status==='rejected')continue;
    const end=worldToPixel(target.source);
    if(end)lines.push(`<line class="bundle-fit-target-ray" x1="${start[0]}" y1="${start[1]}" x2="${end[0]}" y2="${end[1]}"/>`);
   }
  }
  return lines.join('');
 }
 function drawLimitBox(){const svg=$('job-limit-overlay');if(!svg)return;if(setupState){svg.replaceChildren();return;}const r=$('job-canvas').getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${Math.max(1,r.width)} ${Math.max(1,r.height)}`);const boxId=current()?'limit:'+current().id:null,box=limitPreview||(window.SceneObjectsUI?.visible(boxId)!==false?current()?.limit_box:null),lines=[];if(box){const corners=window.TunnelBox.corners(box);for(let i=0;i<8;i++)for(let j=0;j<3;j++)if(!(i&(1<<j))){const a=worldToPixel(corners[i]),b=worldToPixel(corners[i|(1<<j)]);if(a&&b)lines.push(`<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#59dbff" stroke-width="2"/>${!setupState?`<line data-limit-open="${esc(current()?.id||'')}" class="limit-outline-hit" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="transparent" stroke-width="14" style="pointer-events:stroke;touch-action:none"><title>Double-click or double-tap to edit this limit box</title></line>`:''}`);}}lines.push(drawBundleTargetRays(),window.SceneObjectsUI?.draw(worldToPixel,{id:estimateObjectId,points:estimateLine},box?boxId:null)||'');lines.push(window.JointUI?.draw(worldToPixel)||'');if(estimateLine.length&&!window.SceneObjectsUI?.has(estimateObjectId)){const pixels=estimateLine.map(worldToPixel).filter(Boolean);lines.push(`<polyline points="${pixels.map(p=>p.join(',')).join(' ')}" fill="none" stroke="#ff7bee" stroke-width="3"/>`);}lines.push(window.TunnelDesigner?.overlay(worldToPixel)||'');lines.push(window.GeometryToolsUI?.draw(worldToPixel)||'');svg.innerHTML=lines.join('');if(!setupState)window.LimitBoxUI?.render(svg,box,{width:r.width,height:r.height,scale:jobScene.scale,target:jobScene.target.map((v,i)=>v+previewOrigin[i]),...jobScene.basis()},worldToPixel);}
 const limitSvg=document.createElementNS('http://www.w3.org/2000/svg','svg');limitSvg.id='job-limit-overlay';Object.assign(limitSvg.style,{position:'absolute',inset:'0',width:'100%',height:'100%',pointerEvents:'none',zIndex:3});$('job-selection-overlay').parentElement.append(limitSvg);
 const backDeleted=document.createElement('button');backDeleted.id='job-back-deleted';backDeleted.textContent='Back to scan';backDeleted.hidden=true;$('job-preview-info').after(backDeleted);backDeleted.onclick=()=>{deletedGeneration++;deletedLoading=false;clearCleaningPreview();backDeleted.hidden=true;};
 function setupDetail(){return {projection:'perspective',target:setupState.origin.slice(),...jobScene.basis(),scale:Math.tan(jobScene.setupCamera.fov*Math.PI/360),aspect:canvas.clientWidth/Math.max(1,canvas.clientHeight),near:setupState.near,far:setupState.far};}
 function lookAtSetupPoint(point){const d=point.map((v,i)=>v-setupState.origin[i]),R=setupState.orientation,q=[0,1,2].map(i=>R.reduce((n,row,j)=>n+row[i]*d[j],0)),length=Math.hypot(...q);if(length<.03)return;jobScene.yaw=Math.atan2(q[0],q[1]);jobScene.pitch=Math.asin(q[2]/length);jobScene.onNavigate?.();jobScene.draw();}
 function clearSetupBackdrop(){jobScene.setupBackdrop?.destroy();jobScene.setupBackdrop=null;jobScene.setupBackdropVisible=false;}
 async function loadSetupPhotos(view,size=1024,onProgress=()=>{}){
  if(!setupState||setupState.id!==view.id||setupState.key!==view.key)throw Error('The setup changed. Reopen it before loading photographs.');
  if(!jobScene.setupBackdrop||jobScene.setupBackdrop.view.photo_key!==view.photo_key){clearSetupBackdrop();jobScene.setupBackdrop=window.SetupBackdrop.create(jobScene,view,setupState.orientation);}
  const backdrop=jobScene.setupBackdrop;if(!backdrop.ready()||backdrop.size!==size)await backdrop.load(size,onProgress);else onProgress(6,6);
  if(backdrop!==jobScene.setupBackdrop)return false;jobScene.setupBackdropVisible=setupDisplay!=='points';$('job-empty').hidden=true;$('job-preview-info').textContent='SETUP · '+setupState.name+' · Scanner photographs · Pick point retrieves measured XYZ';jobScene.draw();return true;
 }
 async function setSetupDisplay(mode){
  if(!setupState||!['photo','points','both'].includes(mode))return false;
  const generation=setupGeneration;setMode('orbit');setupDisplay=mode;
  if(mode!=='photo'&&detailView&&!viewStillCovered(detailView))useWholePreview();
  setupPointsLoading=mode!=='photo'&&!packed?.length;jobScene.hideCloud=mode==='photo';jobScene.setupBackdropVisible=mode!=='points'||setupPointsLoading;jobScene.draw();
  try{
   if(mode!=='photo'){
    if(loading&&!packed?.length)await firstVisiblePreview(new Promise(resolve=>previewIdleWaiters.push(resolve)),generation);if(generation!==setupGeneration)return false;
    if(!packed?.length){detailView=setupDetail();signature='';previewRequested=true;await firstVisiblePreview(sync({setupEntry:true}),generation);}
    if(generation!==setupGeneration)return false;if(!packed?.length)throw Error('No retained cloud points are visible. Check the limit box or use Photographs.');
   }
   $('job-empty').hidden=!!packed?.length||!!jobScene.setupBackdrop?.ready();return true;
  }finally{if(generation===setupGeneration){setupPointsLoading=false;jobScene.hideCloud=setupDisplay==='photo';jobScene.setupBackdropVisible=setupDisplay!=='points';jobScene.draw();scheduleDetail();}}
 }
 function setSetupBlend(value){const v=Number(value);if(!setupState||!Number.isFinite(v))return false;setupBlendValue=Math.max(0,Math.min(1,v));setMode('orbit');jobScene.draw();return true;}
 function armPhotoPick(callback,options={}){if(!setupState||!jobScene.setupBackdrop?.ready())return false;setMode('pick');pointPickCallback=null;setupRayPick=callback;repeatPick=options.repeat?{scanId:setupState.id}:null;$('job-pick-hint').textContent=options.repeat?'Click targets to fit · Click a name to rename · Esc to stop':'Pick a photograph surface · XYZ comes from retained scan points · Drag to look around';return true;}
 function drawSetupStations(){
  let layer=$('setup-stations');if(!layer){layer=document.createElement('div');layer.id='setup-stations';$('job-canvas').parentElement.append(layer);}
  const records=[];
  if(setupState){
   if(setupState.showStations!==false)for(const neighbour of setupState.neighbours||[]){
    const item=data.items.find(x=>x.id===neighbour.id),position=item?.scan_origin||neighbour.origin;
    if(!item||!position?.every(Number.isFinite))continue;
    const pixel=worldToPixel(position);
    if(pixel)records.push({id:item.id,name:item.name,pixel,distance:Math.hypot(...position.map((v,i)=>v-setupState.origin[i]))});
   }
  }else if(!deletedMode){
   for(const item of bundleSetups()){
    if(!item.has_setup_view||!item.scan_origin?.every(Number.isFinite))continue;
    const pixel=worldToPixel(item.scan_origin);
    if(pixel)records.push({id:item.id,name:item.name,pixel});
   }
  }
  const nodes=new Map([...layer.children].map(button=>[button.dataset.setup,button])),live=new Set();
  for(const n of records){
   const [x,y]=n.pixel;
   if(x<0||y<0||x>=canvas.clientWidth||y>=canvas.clientHeight)continue;
   let button=nodes.get(n.id);
   if(!button){
    button=document.createElement('button');button.dataset.setup=n.id;button.className='setup-station';
    button.innerHTML='<i aria-hidden="true"></i><span></span>';
    for(const event of ['pointerdown','pointerup','click'])button.addEventListener(event,e=>e.stopPropagation());
    button.ondblclick=e=>{e.preventDefault();e.stopPropagation();window.ScanRegistrationUI.openSetup(n.id);};
    button.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();window.ScanRegistrationUI.openSetup(n.id);}};
    layer.append(button);
   }
   live.add(n.id);button.classList.toggle('bundle-station',!setupState);
   button.style.left=x+'px';button.style.top=y+'px';
   button.querySelector('span').textContent=n.name+(n.distance===undefined?'':' · '+fmt(n.distance,1)+' m');
   button.title='Double-click to open '+n.name;button.setAttribute('aria-label',button.title);
  }
  for(const button of [...layer.children])if(!live.has(button.dataset.setup))button.remove();
 }
 async function enterSetup(frame,options={}){
  if(!jobScene.gl)throw Error('The main 3D viewport requires WebGL.');if(window.CadUI?.modelSpace?.()&&!window.CadUI.enter('Edit',{openTool:false}))throw Error('Finish or cancel the modelling command before opening setup view.');const generation=++setupGeneration;clearCleaningPreview();clearSelection();setMode('orbit');liveLimitEditing=false;clearTimeout(detailTimer);
  if(!setupReturn)setupReturn={target:jobScene.target.map((v,i)=>v+previewOrigin[i]),yaw:jobScene.yaw,pitch:jobScene.pitch,scale:jobScene.scale,initialised:jobScene.initialised,density:$('job-density').value};
  previewRequest?.controller.abort();$('job-density').value=localStorage.getItem('tunnelstudio.setupDensity')||'100';densityUI();
  clearSetupBackdrop();setupOverview=null;setupPointsLoading=false;setupDisplay=options.loadPoints===false?'photo':'points';jobScene.hideCloud=setupDisplay==='photo';setupState={...frame,showStations:$('setup-show-stations')?.checked===true};jobScene.setupCamera={orientation:frame.orientation,fov:70,near:frame.near,far:frame.far};jobScene.target=frame.origin.map((v,i)=>v-previewOrigin[i]);jobScene.yaw=0;jobScene.pitch=0;jobScene.initialised=true;chooseItem(frame.id);
  packed=new Float32Array();jobScene.setMesh(packed,previewOrigin);jobScene.setPacked(packed,previewOrigin);publishMeshPreview();detailView=options.loadPoints===false?null:setupDetail();signature='';previewRequested=true;$('job-empty').hidden=false;$('job-empty').textContent='Loading points from '+frame.name+'…';
  if(options.loadPoints===false){$('job-empty').textContent='Loading scanner photographs…';jobScene.draw();return true;}
  while(loading&&generation===setupGeneration)await new Promise(resolve=>setTimeout(resolve,30));if(generation!==setupGeneration)return false;await firstVisiblePreview(sync({setupEntry:true}),generation);if(generation!==setupGeneration)return false;if(!packed?.length)throw Error('No retained points are visible for this setup. Check its limit box or hidden regions.');return true;
 }
 async function firstVisiblePreview(completion,generation){
  let notify;const visible=new Promise(resolve=>{notify=resolve;previewVisibleWaiters.push(resolve);});
  try{await Promise.race([completion,visible]);return generation===setupGeneration&&!!packed?.length;}
  finally{const i=previewVisibleWaiters.indexOf(notify);if(i>=0)previewVisibleWaiters.splice(i,1);}
 }
 function leaveSetup(refresh=true){
  if(!setupState)return;setupGeneration++;clearSetupBackdrop();clearSetupPick();setupState=null;setupOverview=null;setupPointsLoading=false;setupDisplay='points';jobScene.hideCloud=false;jobScene.setupCamera=null;const saved=setupReturn;setupReturn=null;clearTimeout(detailTimer);detailView=null;setMode('orbit');clearSelection();
  if(saved){$('job-density').value=saved.density||'35';densityUI();jobScene.target=saved.target.map((v,i)=>v-previewOrigin[i]);jobScene.yaw=saved.yaw;jobScene.pitch=saved.pitch;jobScene.scale=saved.scale;jobScene.initialised=saved.initialised;}
  signature='';previewRequested=true;jobScene.draw();if(refresh){if(loading)previewRefreshPending=true;else return sync();}
 }
 function highlightRequest(){
  if(deletedMode)return selectionSpec&&deletedPreviewGroups.length?{ids:[...new Set(deletedPreviewGroups.map(pair=>pair[0]))],deleted_groups:deletedPreviewGroups.map(pair=>pair.slice()),selection:JSON.parse(JSON.stringify(selectionSpec))}:null;
  if(previewOwner==='review')return window.SurfaceUI?.exportHighlight?.();
  if(!selectionSpec)return null;
  const scans=data.items.filter(x=>x.kind==='scan'&&x.visible!==false&&(!setupState||x.id===setupState.id)&&(!previewSource||x.id===previewSource));
  return {ids:scans.map(x=>x.id),selection:JSON.parse(JSON.stringify(selectionSpec))};
 }
 function pickedItem(e){
  if(cleanSnapshot)return null;
  const r=canvas.getBoundingClientRect(),px=[e.clientX-r.left,e.clientY-r.top];let nearest=-1,distance=144,depth=-Infinity;
  const basis=jobScene.basis();
  for(let i=0;i<(packed?.length||0);i+=6){if(!jobScene.visiblePreviewRow(i/6,true))continue;const local=[packed[i],packed[i+1],packed[i+2]],p=jobScene.projectLocal(local);if(!p)continue;const d=((p[0]+1)*r.width/2-px[0])**2+((1-p[1])*r.height/2-px[1])**2,z=dot(local.map((v,j)=>v-jobScene.target[j]),basis.forward);if(d<distance-.1||(d<=distance&&z>depth)){distance=d;depth=z;nearest=i/6;}}
  return pointOwners.find(([a,b])=>nearest>=a&&nearest<b)?.[2]||null;
 }
 async function refreshAppearance(){const overlay=!!cleanSnapshot;await sync(overlay?{inventoryOnly:true,retainDisplay:true}:{});if(cleanSnapshot){cleanSnapshot.contentKey=previewContentKey();cleanSnapshot.complete=false;}const snapshot=await api('/api/status');if(state)state.history=snapshot.history;window.HistoryUI?.sync();}
 window.JobUI={targetOwnerVisible,showSetupPick,clearSetupPick,refreshAppearance,exportDeletedSelection,restoreDeletedSelection,meshPreviewState,setMeshDisplay,retryMeshPreview,pickFeatureSeed,cancelFeatureSeed,showFeaturePreview,clearFeaturePreview,loadPreview,projectOpened,setImportDestination:id=>importDestination=id,cadScene:()=>jobScene,cadOrigin:()=>previewOrigin,cadScreenPoint:worldAtPixel,cadCloudPoint:pixel=>{const hit=cadCloudHoverPoint(pixel);return hit===undefined?nearestDisplayedPoint(pixel,true):hit;},cadCloudHoverPoint,prepareCadCloudSnap:screenPointIndex,previewNearbyPoint:pixel=>cleanSnapshot?null:nearestDisplayedPoint(pixel,true,50000),selectedTargets:()=>[...targetSelection],invalidateAnalysis:(result,owner=null)=>{if((owner?[owner]:['heatmap','design']).includes(previewOwner)&&previewResult&&(!result||result.stale||result.id!==previewResult))clearCleaningPreview();},inventory:()=>data,refreshTree:table,action,openSurface,infoDialog,showDetails,activateItem:id=>{if(!selectedItems.has(id))chooseItem(id);else{selected=id;details();window.MeshResultsUI?.sync();}},expandScans:ids=>{for(const id of ids)expandedScans.add(id);table();},chooseItem,highlightRequest,pickedItem,previewOwner:()=>previewOwner,reviewAt:e=>nearestCleaningCluster([e.clientX-canvas.getBoundingClientRect().left,e.clientY-canvas.getBoundingClientRect().top]),targetAt:e=>nearestTarget([e.clientX-canvas.getBoundingClientRect().left,e.clientY-canvas.getBoundingClientRect().top]),selectScanTarget,selectScanTargets,selAction,enterSetup,leaveSetup,setSetupDisplay,setSetupBlend,setupBlend:()=>setupBlendValue,setupLayers:()=>({display:setupDisplay,points:!!packed?.length,photos:!!jobScene.setupBackdrop?.ready()}),loadSetupPhotos,armPhotoPick,clearSetupBackdrop,showSetupStations:value=>{if(setupState){setupState.showStations=!!value;drawSetupStations();}},setupCamera:()=>setupState?{id:setupState.id,eye:jobScene.target.map((v,i)=>v+previewOrigin[i]),fov:jobScene.setupCamera.fov,...jobScene.basis()}:null,projectWorld:worldToPixel,loadViewDetail,setDisplayEnhancement,displayEnhancement:()=>({contrast:displayContrast,gamma:displayGamma}),scheduleDetail,canLoadReviewDetail,setAutoDetail:value=>{autoDetail=!!value;loadViewDetail();},focusWorld,fitScanArea,faceWorkPlane,selectScan:id=>{if(data.items.some(x=>x.id===id))chooseItem(id);},selectGeometry:(preserveItems=false)=>{if(!preserveItems){selected=null;selectedItems.clear();}targetSelection.clear();window.TargetUI?.selectTargets([]);clearSelection();refreshTargetMarkers();table();busyUI(busy);},beginLiveLimit:()=>refreshLimitPreview(true),endLiveLimit:()=>refreshLimitPreview(false),armAreaSelection,cancelAreaSelection,setReviewBoxes,selectionContains:selectedBySpec,limitBox:()=>current()?.limit_hide_outside!==false?current()?.limit_box||null:null,limitDraft:()=>limitPreview||current()?.limit_box||null,setLimitVisibility,setLimitPreview:box=>{limitPreview=box;jobScene.draw();},setLimitBox,clearLimitBoxes,geometryOverlay,waitReady,selectedItems:()=>[...selectedItems],removeSelected,compactSelected,focusCleaningCluster,cleaningPreview,clearCleaningPreview,sync,busy:busyUI,draw:()=>jobScene.draw(),importPaths,uploadFiles,highlightTargets,focusTarget,currentSelection:()=>selectionSpec?JSON.parse(JSON.stringify(selectionSpec)):null,targets,clearSelection,selectedItem:()=>current(),meshItems:()=>data.items.filter(x=>x.kind==='mesh').map(x=>({id:x.id,name:x.name,visible:x.visible})),analysisPreview,scanItems:()=>data.items.filter(x=>x.kind==='scan'&&!x.merge_archived&&!x.merge_stale).map(x=>({id:x.id,name:x.name,visible:x.visible,has_setup_view:x.has_setup_view,geometry_revision:x.geometry_revision,limit_box:x.limit_box,limit_hide_outside:x.limit_hide_outside})),ensureRegistrationSource,armPointPick,armWorldPick,setDisplayMode,displayMode:getDisplayMode,measurePoint,measureDistance,selectionStats,setMode};sync();
})();
