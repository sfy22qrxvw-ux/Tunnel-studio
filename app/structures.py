# Copyright (c) 2026 Damian Gulczynski.
"""Editable reference geometry, intentionally separate from the survey design."""
import math, uuid, json
from pathlib import Path
import numpy as np

def mesh(spec,axis):
    kind=spec.get('kind')
    if kind=='scan_mesh':
        data=json.loads(Path(spec['mesh_file']).read_text()); vertices=np.asarray(data['vertices'],float); faces=data['faces']
        return transform(vertices,spec),faces
    yaw=math.radians(float(spec.get('yaw',0)))
    c=np.array([float(spec.get(k,0)) for k in ('e','n','h')]); length=float(spec.get('length',1)); width=float(spec.get('width',1)); height=float(spec.get('height',1)); radius=float(spec.get('radius',1))
    if not np.isfinite(np.r_[c,yaw,length,width,height,radius]).all() or min(length,width,height,radius)<=0: raise ValueError('Structure dimensions must be positive finite numbers.')
    vertices=[]; faces=[]
    if kind in ('box','plane'):
        vertices=[[-length/2,-width/2,0],[length/2,-width/2,0],[length/2,width/2,0],[-length/2,width/2,0]]; faces=[[0,1,2,3]]
        if kind=='box':
            vertices += [[x,y,height] for x,y,z in vertices]; faces += [[4,7,6,5],[0,4,5,1],[1,5,6,2],[2,6,7,3],[3,7,4,0]]
    elif kind=='cylinder':
        for z in (0,height):
            vertices += [[radius*math.cos(t),radius*math.sin(t),z] for t in np.linspace(0,2*math.pi,48,endpoint=False)]
        faces=[list(range(47,-1,-1)),list(range(48,96))]+[[i,(i+1)%48,(i+1)%48+48,i+48] for i in range(48)]
    elif kind=='tunnel':
        start=float(spec['start']);end=float(spec['end']);step=float(spec.get('step',1));axis.at(start);axis.at(end)
        if not math.isfinite(step) or step<=0 or end<=start: raise ValueError('Tunnel needs increasing chainages and a positive interval.')
        n=int(math.ceil((end-start)/step))+1
        if n>1001: raise ValueError('At most 1001 tunnel sections. Increase the interval.')
        for ch in np.linspace(start,end,n):
            centre,t,right,up,r=axis.at(ch)
            for angle in np.linspace(0,2*math.pi,48,endpoint=False): vertices.append((centre+r*(math.cos(angle)*right+math.sin(angle)*up)).tolist())
        for j in range(n-1):
            for i in range(48): faces.append([j*48+i,j*48+(i+1)%48,(j+1)*48+(i+1)%48,(j+1)*48+i])
        return vertices,faces
    else: raise ValueError('Unknown structure type.')
    rotation=np.array([[math.cos(yaw),-math.sin(yaw),0],[math.sin(yaw),math.cos(yaw),0],[0,0,1]])
    return (np.asarray(vertices)@rotation.T+c).tolist(),faces

def transform(vertices,spec):
    shift=np.array([float(spec.get('tx',0)),float(spec.get('ty',0)),float(spec.get('tz',0))]); scale=float(spec.get('scale',1));yaw=math.radians(float(spec.get('rotation',0)))
    if not np.isfinite(np.r_[shift,scale,yaw]).all() or scale<=0:raise ValueError('Mesh scale must be positive; transforms must be finite.')
    pivot=vertices.mean(axis=0);rotation=np.array([[math.cos(yaw),-math.sin(yaw),0],[math.sin(yaw),math.cos(yaw),0],[0,0,1]])
    return ((vertices-pivot)@rotation.T*scale+pivot+shift).tolist()

def wire(spec,axis,origin):
    vertices,faces=mesh(spec,axis);edges=set()
    for face in faces:
        for a,b in zip(face,face[1:]+face[:1]):edges.add(tuple(sorted((a,b))))
    return [[vertices[a][i]-origin[i] for i in range(3)]+[vertices[b][i]-origin[i] for i in range(3)] for a,b in sorted(edges)]

def obj(objects,axis):
    out=['# Tunnel Studio 0.2 reference structures; coordinates E N H, metres'];offset=1
    for item in objects:
        vertices,faces=mesh(item,axis);out.append('o structure_'+item['id'])
        out += ['v '+' '.join(f'{n:.9f}' for n in p) for p in vertices]
        out += ['f '+' '.join(str(i+offset) for i in f) for f in faces];offset+=len(vertices)
    return '\n'.join(out)+'\n'


def scan_mesh(meta,derived,axis,start,end,step,degrees,band,min_points,max_edge,method='mean',power=2,smoothing=0,selection=None,progress=lambda _:None):
    """Radial height-field reconstruction, only fully observed adjacent cells joined."""
    from heatmap import calculate
    if method not in ('mean','nearest','idw'): raise ValueError('Choose mean, nearest or IDW meshing.')
    smoothing=int(smoothing)
    if not 0<=smoothing<=10: raise ValueError('Smoothing must be 0–10 iterations.')
    requested_step=float(step);angle=float(degrees)
    if not math.isfinite(requested_step) or requested_step<=0:raise ValueError('Use positive longitudinal spacing in metres.')
    if not math.isfinite(angle) or not 1<=angle<=90:raise ValueError('Use angular spacing between 1 and 90 degrees.')
    cells=max(1,250000//int(math.ceil(360/angle)))
    step=max(requested_step,(float(end)-float(start))/cells)
    if step>requested_step:progress(f'Automatic longitudinal spacing {step:.4f} m for the full chainage range')
    h=calculate(meta,derived,axis,start,end,step,degrees,band,method,power,selection,progress)
    min_points=int(min_points);max_edge=float(max_edge)
    if min_points<1 or not math.isfinite(max_edge) or max_edge<=0: raise ValueError('Minimum points and maximum edge must be positive.')
    vertices=[]; ids=np.full((h['ny'],h['nx']),-1,int)
    for y in range(h['ny']):
        angle=math.radians((y+.5)*h['degrees'])
        for x in range(h['nx']):
            i=y*h['nx']+x
            if h['counts'][i]<min_points or h['values'][i] is None: continue
            lo=h['start']+x*h['step']; ch=(lo+min(h['end'],lo+h['step']))/2
            centre,t,right,up,r=axis.at(ch); radius=r+h['values'][i]/1000
            if radius<=0: continue
            ids[y,x]=len(vertices);vertices.append((centre+radius*(math.sin(angle)*right+math.cos(angle)*up)).tolist())
    v=np.asarray(vertices);faces=[]
    for y in range(h['ny']):
        for x in range(h['nx']-1):
            a,b,c,d=ids[y,x],ids[y,x+1],ids[(y+1)%h['ny'],x+1],ids[(y+1)%h['ny'],x]
            if min(a,b,c,d)<0:continue
            for face in ([int(a),int(b),int(c)],[int(a),int(c),int(d)]):
                if max(np.linalg.norm(v[face[j]]-v[face[(j+1)%3]]) for j in range(3))<=max_edge: faces.append(face)
    if not faces:raise ValueError('No mesh faces meet coverage / edge criteria. Check range, wall band, cell sizes and maximum edge.')
    if smoothing:
        adjacency=[set() for _ in range(len(v))]; edge_count={}
        for face in faces:
            for a,b in zip(face,face[1:]+face[:1]):
                adjacency[a].add(b);adjacency[b].add(a); edge=tuple(sorted((a,b)));edge_count[edge]=edge_count.get(edge,0)+1
        boundary=set(i for edge,count in edge_count.items() if count==1 for i in edge)
        for iteration in range(smoothing):
            nxt=v.copy()
            for i,neighbours in enumerate(adjacency):
                if i not in boundary and neighbours: nxt[i]=.7*v[i]+.3*v[list(neighbours)].mean(axis=0)
            v=nxt
        vertices=v.tolist()
    area=sum(np.linalg.norm(np.cross(v[b]-v[a],v[c]-v[a]))/2 for a,b,c in faces)
    return vertices,faces,{'vertices':len(vertices),'triangles':len(faces),'area_m2':float(area),'step_m':h['step'],'angular_cell_deg':h['degrees'],'min_points':min_points,'max_edge_m':max_edge,'source_points':h['points'],'method':f'{method} radial reconstruction within each occupied chainage-angle cell; IDW power {power}; Laplacian smoothing {smoothing} iterations, lambda 0.3, boundaries pinned. Adjacent observed cells only; no hole filling. Single radial surface, not general-purpose 3D reconstruction.'}
