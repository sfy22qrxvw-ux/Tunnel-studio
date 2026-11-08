# Copyright (c) 2026 Damian Gulczynski.
"""Full-density sector evidence for geometric ring-joint suggestions.

No regular grid of joints is fabricated. A station must have a coherent radial
change across independent angular sectors. Two streaming passes estimate a
clipped sector mean; slow profile variation is removed along each sector.
"""
from pathlib import Path
import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import find_peaks
import jobdata
from large_sections import frame

SECTORS=24
CHUNK=200000

def detect(meta,derived,axis,settings,progress=lambda _:None,clip=None):
    pitch=float(settings.get('pitch',1.8));width=float(settings.get('bin_width',.02));threshold=float(settings.get('threshold_mm',3))/1000;band=float(settings.get('band',.5));coverage=int(settings.get('minimum_sectors',8))
    if not np.isfinite([pitch,width,threshold,band]).all() or not .1<=pitch<=20 or not .002<=width<=.5 or not 0<threshold<=.5 or not threshold<=band<=5 or coverage not in (8,12,16):raise ValueError('Check ring width, bin width, signal, wall band and angular coverage. The wall band must include the signal threshold.')
    n=int(meta['count']);path=Path(derived)
    if not n or not path.is_file() or path.stat().st_size!=n*32:raise ValueError('Ring projection is incomplete. Run suggestions again to rebuild it.')
    info=np.memmap(path,'<f8','r',shape=(n,4));mask=np.memmap(meta['mask_file'],'u1','r',shape=(n,)) if meta.get('mask_file') else None
    xyz=np.memmap(meta['file'],'<f8','r',shape=(n,3))
    rows=np.asarray(axis.a,float);vectors=np.diff(rows[:,1:4],axis=0);normal=vectors/np.linalg.norm(vectors,axis=1)[:,None];right=np.cross(normal,[0.,0.,1.]);vertical=np.linalg.norm(right,axis=1)<1e-9;right[vertical]=np.cross(normal[vertical],[0.,1.,0.]);right/=np.linalg.norm(right,axis=1)[:,None];up=np.cross(right,normal)
    retained=0;lo=float('inf');hi=-float('inf')
    def batches(angles=True):
        for start in range(0,n,CHUNK):
            q=np.asarray(info[start:start+CHUNK]);points=np.asarray(xyz[start:start+len(q)]);use=np.isfinite(q[:,[0,3]]).all(1)&np.isfinite(points).all(1)
            if mask is not None:use&=mask[start:start+len(q)]==0
            if clip:use&=jobdata.selection_mask(xyz[start:start+len(q)],clip)
            q=q[use];points=points[use]
            if angles and len(q):
                si=np.clip(np.searchsorted(rows[:,0],q[:,0],side='right')-1,0,len(rows)-2);t=(q[:,0]-rows[si,0])/(rows[si+1,0]-rows[si,0]);centre=rows[si,1:4]+t[:,None]*vectors[si];delta=points-centre
                angular=np.mod(np.arctan2(np.einsum('ij,ij->i',delta,up[si]),np.einsum('ij,ij->i',delta,right[si])),2*np.pi)
            else:angular=np.zeros(len(q))
            yield start,np.c_[q[:,0],angular,q[:,3]]
    try:
        for start,q in batches(False):
            if len(q):lo=min(lo,float(q[:,0].min()));hi=max(hi,float(q[:,0].max()));retained+=len(q)
            progress(f'Ring range: {min(start+CHUNK,n):,} / {n:,} points')
        if not retained:raise ValueError('No retained points project onto this alignment. Check scan/alignment positioning and the limit box.')
        if settings.get('auto_range',False):start,end=lo,hi
        else:start,end=float(settings['start']),float(settings['end'])
        if not np.isfinite([start,end]).all() or end<=start:raise ValueError('Ring end chainage must be greater than its start.')
        start=max(start,float(axis.a[0,0]),lo);end=min(end,float(axis.a[-1,0]),hi)
        if end<=start:raise ValueError(f'This scan occupies Ch. {lo:.3f}–{hi:.3f} m. Choose Use scan extent or enter an overlapping range.')
        bins=int(np.ceil((end-start)/width))+1
        if bins>30000:raise ValueError('Choose a shorter ring-search range or a wider chainage bin (maximum 30,000 bins).')
        size=bins*SECTORS;count=np.zeros(size,np.int64);sums=np.zeros(size);squares=np.zeros(size);inrange=inband=0
        def cells(q):
            use=(q[:,0]>=start)&(q[:,0]<=end)&(abs(q[:,2])<=band)
            q=q[use];b=np.clip(((q[:,0]-start)/width).astype(np.int64),0,bins-1);a=(q[:,1]*SECTORS/(2*np.pi)).astype(np.int64)%SECTORS
            return b*SECTORS+a,q[:,2]
        for pos,q in batches():
            inrange+=int(((q[:,0]>=start)&(q[:,0]<=end)).sum());k,d=cells(q);inband+=len(k)
            count+=np.bincount(k,minlength=size);sums+=np.bincount(k,weights=d,minlength=size);squares+=np.bincount(k,weights=d*d,minlength=size)
            progress(f'Ring wall support: {min(pos+CHUNK,n):,} / {n:,} points')
        if not inband:raise ValueError(f'No retained points in the ring-search wall band. Increase Wall band or review the alignment radii. Scan range: {lo:.3f}–{hi:.3f} m.')
        mean=sums/np.maximum(count,1);sigma=np.sqrt(np.maximum(0,squares/np.maximum(count,1)-mean*mean));limit=np.maximum(.002,2.5*sigma);clipped=np.zeros(size);clean_count=np.zeros(size,np.int64)
        for pos,q in batches():
            k,d=cells(q);use=abs(d-mean[k])<=limit[k];k=k[use];d=d[use];clipped+=np.bincount(k,weights=d,minlength=size);clean_count+=np.bincount(k,minlength=size)
            progress(f'Ring radial changes: {min(pos+CHUNK,n):,} / {n:,} points')
        values=(clipped/np.maximum(clean_count,1)).reshape(bins,SECTORS);seen=clean_count.reshape(bins,SECTORS)>0
        baseline=np.zeros_like(values);supported=np.zeros_like(seen);window=max(5,min(1001,int(pitch*.5/width)|1));indices=np.arange(bins)
        for a in range(SECTORS):
            progress(f'Evaluating ring evidence · sector {a+1} / {SECTORS}')
            good=np.flatnonzero(seen[:,a])
            if len(good)<5:continue
            filled=np.interp(indices,good,values[good,a]);baseline[:,a]=median_filter(filled,size=window,mode='nearest')
            # Require measured context on BOTH sides, not one interpolated end.
            left=np.searchsorted(good,indices-2);right=np.searchsorted(good,indices+2)
            li=np.maximum(0,left-1);ri=np.minimum(len(good)-1,right)
            supported[:,a]=seen[:,a]&(good[li]<indices-1)&(good[ri]>indices+1)&(indices-good[li]<=window)&(good[ri]-indices<=window)
        residual=values-baseline;observed=supported.sum(1);positive=((residual>=threshold)&supported).sum(1);negative=((residual<=-threshold)&supported).sum(1);agree=np.maximum(positive,negative);required=np.maximum(6,np.ceil(observed*.6).astype(int));eligible=observed>=coverage;candidate=eligible&(agree>=required)
        signed=np.zeros(bins);signal=np.zeros(bins)
        for i in np.flatnonzero(candidate):
            sign=1 if positive[i]>=negative[i] else -1;v=residual[i,supported[i]&(residual[i]*sign>=threshold)];signed[i]=float(np.median(v));signal[i]=abs(signed[i])
        peaks,_=find_peaks(signal,height=threshold,distance=max(1,int(.45*pitch/width)))
        rows=[]
        for b in peaks:
            ch=min(end,start+(b+.5)*width);c,_,right,up,r=frame(axis,ch)
            rows.append(dict(id=f'J{len(rows)+1:03d}',chainage=float(ch),type='recess' if signed[b]>0 else 'ridge',signal_mm=float(signal[b]*1000),supported_sectors=int(agree[b]),observed_sectors=int(observed[b]),spacing_m=None if not rows else float(ch-rows[-1]['chainage']),status='unreviewed',resolution_m=width,centre=c.tolist(),right=right.tolist(),up=up.tolist(),radius=r))
        message=(f"{len(rows)} candidates from {inband:,} retained wall-band points." if rows else
            'No candidate passed the geometric evidence checks. '+('Too few angular sectors have support; widen Chainage bin or include more circumference.' if not eligible.any() else 'Try a lower signal threshold or review a visible joint in cross-section. Flush or intensity-only joints may not produce a radial change.'))
        return dict(candidates=rows,eligible_bins=int(eligible.sum()),total_bins=bins,bin_width=width,start=start,end=end,pitch=pitch,threshold_mm=threshold*1000,band=band,minimum_sectors=coverage,retained_projected_points=retained,points_in_range=inrange,points_in_band=inband,excluded_by_band=inrange-inband,max_observed_sectors=int(observed.max()),diagnostic=message,method='Clipped full-density sector means; longitudinal median baseline; coherent radial changes. Bin centres are suggestions, not survey accuracy.')
    finally:
        info._mmap.close()
        if mask is not None:mask._mmap.close()
        if xyz is not None:xyz._mmap.close()
