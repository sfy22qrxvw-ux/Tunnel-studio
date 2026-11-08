# Copyright (c) 2026 Damian Gulczynski.
"""Full-density longitudinal/unwrapped circular-design comparison."""
import math, base64, zlib, struct
import numpy as np
from core import CHUNK
from editing import mask_for


def calculate(meta,derived,axis,start,end,step=.25,degrees=5,band=.3,mode='extreme',power=2,selection=None,progress=lambda _:None):
    start,end,step,degrees,band=map(float,(start,end,step,degrees,band))
    if not all(map(math.isfinite,(start,end,step,degrees,band))) or end<=start or step<=0 or not 1<=degrees<=90 or band<=0: raise ValueError('Heatmap needs an increasing range and positive cell sizes / wall band.')
    axis.at(start);axis.at(end)
    requested_step,requested_degrees=step,degrees
    nx=int(math.ceil((end-start)/step)); ny=int(math.ceil(360/degrees)); degrees=360/ny
    requested_cells=nx*ny;resolution_adjusted=requested_cells>300000
    if resolution_adjusted:
        # Bound the output grid, not the source-point count or chainage range.
        # Every retained measurement still contributes to its effective cell.
        nx=max(1,300000//ny);step=(end-start)/nx
        progress(f'Full-range heatmap: effective longitudinal cell {step:.6g} m (requested {requested_step:.6g} m); all retained points remain included')
    if mode not in ('mean','extreme','nearest','idw'): raise ValueError('Choose mean or signed greatest absolute deviation.')
    power=float(power)
    if not math.isfinite(power) or not 0<power<=6: raise ValueError('IDW power must be greater than 0 and at most 6.')
    best_dist=np.full(nx*ny,np.inf); nearest=np.zeros(nx*ny); weights=np.zeros(nx*ny); weighted=np.zeros(nx*ny)
    counts=np.zeros(nx*ny,dtype=np.int64); sums=np.zeros(nx*ny); low=np.full(nx*ny,np.inf); high=np.full(nx*ny,-np.inf)
    d=np.memmap(derived,dtype='<f8',mode='r',shape=(meta['count'],4)); mask=mask_for(meta)
    positions=np.memmap(meta['file'],'<f8','r',shape=(meta['count'],3)) if selection else None
    try:
        for i in range(0,len(d),CHUNK):
            progress(f'Building full-density surface grid · {min(len(d),i+CHUNK):,}/{len(d):,} points')
            c=d[i:i+CHUNK]; use=np.isfinite(c[:,0])&(c[:,0]>=start)&(c[:,0]<=end)&(abs(c[:,3])<=band)
            if mask is not None: use &= mask[i:i+len(use)]==0
            if positions is not None:
                world=__import__('registration_pose').points(meta,positions[i:i+len(use)])
                use&=__import__('jobdata').selection_mask(world,selection)
            c=c[use]; x=np.minimum(nx-1,((c[:,0]-start)/step).astype(int)); y=np.floor(np.mod(np.degrees(np.arctan2(c[:,1],c[:,2])),360)/degrees).astype(int); ids=y*nx+x; values=c[:,3]*1000
            counts+=np.bincount(ids,minlength=nx*ny); sums+=np.bincount(ids,weights=values,minlength=nx*ny)
            np.minimum.at(low,ids,values); np.maximum.at(high,ids,values)
            if mode in ('nearest','idw'):
                angle=np.mod(np.degrees(np.arctan2(c[:,1],c[:,2])),360)
                cell_centres=(start+x*step+np.minimum(end,start+(x+1)*step))/2
                # Physical distance on the parameterised design wall (longitudinal / circumferential).
                radial=np.hypot(c[:,1],c[:,2])-c[:,3]
                ds=c[:,0]-cell_centres; da=np.radians(angle-(y+.5)*degrees)*radial
                distance2=ds*ds+da*da
                if mode=='idw':
                    w=1/np.maximum(distance2,1e-12)**(power/2)
                    weights+=np.bincount(ids,weights=w,minlength=nx*ny); weighted+=np.bincount(ids,weights=w*values,minlength=nx*ny)
                else:
                    order=np.lexsort((distance2,ids)); sorted_ids=ids[order]
                    first=np.r_[True,sorted_ids[1:]!=sorted_ids[:-1]] if len(order) else np.array([],bool)
                    chosen=order[first]; changed=distance2[chosen]<best_dist[ids[chosen]]; chosen=chosen[changed]
                    best_dist[ids[chosen]]=distance2[chosen];nearest[ids[chosen]]=values[chosen]
    finally:
        d._mmap.close()
        if mask is not None:mask._mmap.close()
        if positions is not None:positions._mmap.close()
    out=np.divide(sums,counts,out=np.zeros_like(sums),where=counts>0) if mode=='mean' else np.where(abs(low)>abs(high),low,high)
    if mode=='nearest': out=nearest
    if mode=='idw': out=np.divide(weighted,weights,out=np.zeros_like(weighted),where=weights>0)
    values=[float(v) if c else None for v,c in zip(out,counts)]
    return {'start':start,'end':end,'step':step,'degrees':degrees,'nx':nx,'ny':ny,'values':values,'counts':counts.tolist(),'band':band,'mode':mode,'points':int(counts.sum()),
        'requested_step':requested_step,'requested_degrees':requested_degrees,'requested_cells':requested_cells,'resolution_adjusted':resolution_adjusted,
        'resolution_note':f'Full requested chainage range; every retained point in the selected scope and wall band is aggregated. Effective cells: {step:.6g} m × {degrees:.6g}°; requested: {requested_step:.6g} m × {requested_degrees:.6g}°.',
        'angle_convention':'0° crown, 90° right wall, 180° invert, 270° left wall; looking along increasing chainage'}


def image_uri(h,limit):
    def chunk(tag,b):return struct.pack('>I',len(b))+tag+b+struct.pack('>I',zlib.crc32(tag+b)&0xffffffff)
    rgb=bytearray()
    for j in range(h['ny']):
        rgb.append(0)
        for v in h['values'][j*h['nx']:(j+1)*h['nx']]:
            if v is None: c=(223,228,232)
            else:
                base=np.array([235,245,240]); end=np.array([213,86,44] if v>=0 else [42,103,196]); t=min(1,abs(v)/limit); c=(base+(end-base)*t).astype(int)
            rgb.extend(int(x) for x in c)
    b=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',h['nx'],h['ny'],8,2,0,0,0))+chunk(b'IDAT',zlib.compress(bytes(rgb)))+chunk(b'IEND',b'')
    return 'data:image/png;base64,'+base64.b64encode(b).decode()
