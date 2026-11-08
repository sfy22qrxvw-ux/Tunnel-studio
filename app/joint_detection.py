"""Full-range joint evidence at the requested chainage resolution.

The large 24-sector histograms live on temporary disk. Source reads, histogram
updates and median baselines use bounded arrays. The final one-dimensional
SciPy peak calculation and returned candidates scale with the requested bin
count; neither imposes a source-point or chainage-bin cutoff.
"""
from contextlib import ExitStack
from pathlib import Path
import math
import shutil
import tempfile

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import find_peaks

POINT_BLOCK=100000
BIN_BLOCK=32768
SECTORS=24


def _mapped(stack,path,dtype,shape):
    size=int(np.prod(shape,dtype=np.int64))*np.dtype(dtype).itemsize
    # Logical zeros without filling a potentially very large temporary array.
    from mask_storage import copy_mask
    copy_mask(None,path,size,lambda _:None)
    result=np.memmap(path,dtype,mode='r+',shape=shape)
    stack.callback(result._mmap.close)
    return result


def _aggregate(meta,derived,sums,counts,bins,start,end,bin_width,band,progress):
    total=int(meta['count']);kept=0
    path=Path(derived)
    if path.stat().st_size!=total*32:
        raise ValueError('The alignment projection does not match its source point count.')
    with ExitStack() as stack:
        stream=stack.enter_context(path.open('rb'))
        mask=stack.enter_context(Path(meta['mask_file']).open('rb')) if meta.get('mask_file') else None
        if mask is not None and Path(meta['mask_file']).stat().st_size!=total:
            raise ValueError('The source exclusion mask does not match its point count.')
        for offset in range(0,total,POINT_BLOCK):
            size=min(POINT_BLOCK,total-offset)
            rows=np.fromfile(stream,'<f8',count=size*4)
            if rows.size!=size*4:raise ValueError('The alignment projection is incomplete.')
            rows=rows.reshape(size,4)
            use=np.isfinite(rows).all(1)&(rows[:,0]>=start)&(rows[:,0]<end)&(np.abs(rows[:,3])<=band)
            if mask is not None:
                flags=np.fromfile(mask,'u1',count=size)
                if len(flags)!=size:raise ValueError('The source exclusion mask is incomplete.')
                use&=flags==0
            rows=rows[use];kept+=len(rows)
            if len(rows):
                chainage=np.minimum(bins-1,np.floor((rows[:,0]-start)/bin_width).astype(np.int64))
                sector=np.floor(np.mod(np.arctan2(rows[:,2],rows[:,1]),2*np.pi)*SECTORS/(2*np.pi)).astype(np.int64)
                ids=sector*bins+chainage
                # A source block updates only its touched cells. A bincount
                # spanning the entire tunnel would allocate B×24 per block.
                touched,inverse=np.unique(ids,return_inverse=True)
                counts.reshape(-1)[touched]+=np.bincount(inverse)
                sums.reshape(-1)[touched]+=np.bincount(inverse,weights=rows[:,3]*1000)
            progress(f'Finding joint evidence: {offset+size:,} / {total:,} points · full requested range')
    return kept


def _detrend(folder,sums,counts,residuals,bins,width,progress):
    halo=width//2
    for sector in range(SECTORS):
        idpath=folder/'observed.ids';valuepath=folder/'observed.values';observations=0
        with idpath.open('wb') as idsout,valuepath.open('wb') as valuesout:
            for first in range(0,bins,BIN_BLOCK):
                last=min(bins,first+BIN_BLOCK);observed=counts[sector,first:last]>0
                ids=np.flatnonzero(observed).astype('<i8')+first
                values=np.asarray(sums[sector,first:last][observed]/counts[sector,first:last][observed],'<f8')
                ids.tofile(idsout);values.tofile(valuesout);observations+=len(ids)
                residuals[first:last,sector]=np.nan
                progress(f'Reading joint sector {sector+1}/{SECTORS}: {last:,}/{bins:,} chainage bins')
        if observations<5:continue
        with ExitStack() as stack:
            ids=np.memmap(idpath,'<i8','r',shape=(observations,));stack.callback(ids._mmap.close)
            values=np.memmap(valuepath,'<f8','r',shape=(observations,));stack.callback(values._mmap.close)
            for first in range(0,bins,BIN_BLOCK):
                last=min(bins,first+BIN_BLOCK)
                low=max(0,first-halo);high=min(bins,last+halo)
                # Include the nearest observed bin on each side, even across a
                # long unobserved span. Interpolation then matches the complete
                # profile, and the halo preserves the global median baseline.
                a=max(0,int(np.searchsorted(ids,low,side='left'))-1)
                b=min(observations,int(np.searchsorted(ids,high-1,side='right'))+1)
                interpolated=np.interp(np.arange(low,high),ids[a:b],values[a:b])
                baseline=median_filter(interpolated,size=width,mode='nearest')[first-low:last-low]
                observed=counts[sector,first:last]>0
                part=np.full(last-first,np.nan)
                part[observed]=sums[sector,first:last][observed]/counts[sector,first:last][observed]-baseline[observed]
                residuals[first:last,sector]=part
                progress(f'Fitting joint sector {sector+1}/{SECTORS}: {last:,}/{bins:,} chainage bins')


def run(meta,derived,axis,start,end,pitch,bin_width,threshold_mm,band,progress):
    bins=int(math.ceil((end-start)/bin_width))
    width=max(5,int(min(pitch*.45,.6)/bin_width)//2*2+1)
    # 3 sector histograms, 2 observation profiles and four compact score arrays.
    needed=bins*(SECTORS*24+16+11)
    with tempfile.TemporaryDirectory(prefix='tunnel-joints-') as scratch:
        folder=Path(scratch)
        if shutil.disk_usage(folder).free<needed+512*1024*1024:
            raise ValueError('Not enough temporary disk space for joint detection at the requested bin width. Free temporary-drive space and run the full range again.')
        with ExitStack() as stack:
            sums=_mapped(stack,folder/'sums','<f8',(SECTORS,bins))
            counts=_mapped(stack,folder/'counts','<i8',(SECTORS,bins))
            residuals=_mapped(stack,folder/'residuals','<f8',(bins,SECTORS))
            included=_aggregate(meta,derived,sums,counts,bins,start,end,bin_width,band,progress)
            _detrend(folder,sums,counts,residuals,bins,width,progress)
            score=_mapped(stack,folder/'score','<f8',(bins,))
            observed=_mapped(stack,folder/'observed','u1',(bins,))
            positive=_mapped(stack,folder/'positive','u1',(bins,))
            negative=_mapped(stack,folder/'negative','u1',(bins,))
            eligible=0
            for first in range(0,bins,BIN_BLOCK):
                last=min(bins,first+BIN_BLOCK);part=np.asarray(residuals[first:last])
                seen=np.isfinite(part).sum(1);pos=(part>=threshold_mm).sum(1);neg=(part<=-threshold_mm).sum(1)
                observed[first:last]=seen;positive[first:last]=pos;negative[first:last]=neg
                supported=(seen>=16)&(np.maximum(pos,neg)>=12)
                values=np.zeros(last-first)
                if supported.any():values[supported]=np.abs(np.nanmedian(part[supported],axis=1))
                score[first:last]=values;eligible+=int((seen>=16).sum())
                progress(f'Checking joint support: {last:,}/{bins:,} chainage bins')
            progress('Resolving joint peaks across the complete requested chainage range')
            peaks,_=find_peaks(score,height=threshold_mm,distance=max(1,int(pitch*.45/bin_width)),prominence=threshold_mm*.4)
            suggestions=[]
            previous=None
            for number,index in enumerate(peaks):
                station=start+(int(index)+.5)*bin_width
                centre,t,right,up,radius=axis.at(station)
                suggestions.append(dict(id=f'J{number+1:03}',chainage=float(station),signal_mm=float(score[index]),
                    supported_sectors=int(max(positive[index],negative[index])),observed_sectors=int(observed[index]),
                    type='Recess' if positive[index]>=negative[index] else 'Ridge',status='unreviewed',
                    spacing_m=float(station-previous) if previous is not None else None,resolution_m=bin_width,
                    centre=centre.tolist(),right=right.tolist(),up=up.tolist(),radius=radius))
                previous=station
                if number%1000==0:progress(f'Preparing joint candidates: {number+1:,}/{len(peaks):,}')
            stride=max(1,int(math.ceil(bins/2000)));indices=np.arange(0,bins,stride,dtype=np.int64)
            signal=np.column_stack((start+(indices+.5)*bin_width,score[indices])).tolist()
            progress('Joint candidates ready at the requested bin width over the full range')
            return dict(candidates=suggestions,start=start,end=end,pitch=pitch,bin_width=bin_width,
                threshold_mm=threshold_mm,band=band,eligible_bins=eligible,total_bins=bins,signal=signal,
                retained_points_in_scope=included,chainage_bin_width_preserved=True,input_point_limit=None,
                method='Geometry only: 24 angular sectors, local median baseline, at least 16 observed and 12 supporting sectors. Bin-centre positions are suggestions, not fitted ring-face planes.')
