# Copyright (c) 2026 Damian Gulczynski.
"""Profiles and profile transitions on the project's surveyed alignment."""
import math
import numpy as np
import cad_geometry as g
from core import Alignment



def trace_spec(axis, spec):
    """Copy the selected station range without changing the source alignment."""
    start = g.number(spec.get('start'), 'Start chainage')
    end = g.number(spec.get('end'), 'End chainage')
    if start >= end:
        raise ValueError('End chainage must be greater than start chainage.')
    projection = spec.get('projection')
    if projection not in ('plan', '3d'):
        raise ValueError('Choose Plan or 3D centreline.')
    first, last = axis.at(start)[0], axis.at(end)[0]
    i, j = np.searchsorted(axis.a[:, 0], [start, end], side='right')
    if j > i and axis.a[j-1, 0] == end:
        j -= 1
    if j - i + 2 > g.MAX_VERTICES:
        raise ValueError('This trace has too many vertices. Choose a shorter chainage range.')
    points = np.vstack((first, axis.a[i:j, 1:4], last))
    if projection == 'plan':
        points[:, 2] = 0
    return dict(kind='polyline', points=points.tolist(), closed=False)

def build(spec, resolve):
    rows=np.asarray(spec.get('alignment'),float)
    if rows.ndim!=2 or rows.shape[1]!=5 or len(rows)<2 or len(rows)>100000:
        raise ValueError('Load a valid project alignment before using Along alignment.')
    axis=Alignment([dict(chainage=s,e=x,n=y,h=z,radius=r) for s,x,y,z,r in rows])
    start=g.number(spec.get('start'),'Start chainage');end=g.number(spec.get('end',start),'End chainage')
    placing=spec['kind']=='alignment_profile'
    axis.at(start);axis.at(end)
    if not placing and end<=start:raise ValueError('End chainage must be greater than start chainage.')
    step=g.number(spec.get('step',1),'Profile spacing',True)
    horizontal=g.number(spec.get('horizontal',0),'Horizontal offset');vertical=g.number(spec.get('vertical',0),'Vertical offset')
    angle=math.radians(g.number(spec.get('rotation',0),'Profile rotation'))
    R=np.array([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])

    def profile(key):
        value=spec.get(key)
        if not value or value=='design_circle':
            t=np.arange(64)*2*np.pi/64
            return np.column_stack([np.cos(t),np.sin(t)]),True,True
        obj=value if isinstance(value,dict) else resolve(value)
        if obj is None:raise ValueError('Select an existing drawn profile.')
        p,closed=g.profile(obj)
        definition=obj.get('definition',{})
        if definition.get('frame'):
            origin,basis=g.matrix(definition['frame']);local=(p-origin)@basis
            if np.ptp(local[:,2])>1e-6:raise ValueError('The selected profile is not planar in its saved drawing frame.')
            xy=local[:,:2]
        else:
            _,_,xy=g.plane(p);xy=xy-xy.mean(0)
        spec[key]={k:obj[k] for k in ('vertices','closed','definition') if k in obj}
        return xy,closed,False

    first,closed,design=profile('profile');last,closed_end,design_end=profile('end_profile') if spec.get('end_profile') else (first,closed,design)
    if closed_end!=closed:raise ValueError('Transition profiles must both be open or both closed.')
    count=max(len(first),len(last))
    if count>512:raise ValueError('Use profiles with at most 512 vertices for an alignment model.')
    def resample(xy):return g.resample(np.column_stack([xy,np.zeros(len(xy))]),count,closed)[:,:2]
    if len(first)!=count:first=resample(first)
    if len(last)!=count:last=resample(last)
    if placing:stations=np.array([start])
    else:
        maximum=min(2048,g.MAX_VERTICES//count,(g.MAX_FACES-2*count)//(2*count)+1)
        corners=rows[(rows[:,0]>start)&(rows[:,0]<end),0]
        slots=maximum-len(corners)
        if slots<2:raise ValueError('This alignment has too many bends for one editable model. Create consecutive chainage ranges.')
        intervals=min(max(1,math.ceil((end-start)/step)),slots-1)
        stations=np.unique(np.r_[np.linspace(start,end,intervals+1),corners])
    loops=[]
    for station in stations:
        centre,tangent,right,up,radius=axis.at(float(station))
        fraction=0 if placing else (station-start)/(end-start)
        a=first*radius if design else first;b=last*radius if design_end else last
        xy=((1-fraction)*a+fraction*b)@R.T+[horizontal,vertical]
        vertices=centre+xy[:,0,None]*right+xy[:,1,None]*up
        loops.append(dict(vertices=vertices.tolist(),closed=closed))
    spec['actual_profile_count']=len(stations)
    spec['actual_max_spacing']=float(np.max(np.diff(stations))) if len(stations)>1 else 0
    if placing:
        centre_metadata = {}
        if design:
            centre_metadata['centre'] = (centre + horizontal * right + vertical * up).tolist()
        return g.geometry(loops[0]['vertices'], closed=closed, **centre_metadata)
    return g.loft(loops)
