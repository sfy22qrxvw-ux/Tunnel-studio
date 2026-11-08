# Copyright (c) 2026 Damian Gulczynski.
"""Reviewable tunnel-lining extraction by outward rays and box neighbourhoods.

Independent adaptation inspired by Xu et al. (2026), DOI
10.1016/j.measurement.2026.123609.  The paper's full method and author code were
not available for this implementation.  This module therefore makes no claim
of reproducing their algorithm or published results.

The implementation keeps original float64 coordinates and IDs.  A bounded
sample establishes a tunnel frame; every selected original point is then used
to build and query a disk-backed station/angle/radial box index.  Unsupported
boxes are retained rather than silently removed.
"""
from pathlib import Path
import math,shutil,tempfile,time
import numpy as np
import tunnel_lining

DOI='10.1016/j.measurement.2026.123609'
MAX_RAY_CELLS=3_000_000
HISTOGRAM_WINDOW_BYTES=32*1024*1024
POINT_BLOCK=25000


def _bounded_batches(batches):
    """Keep coordinate work bounded even when the caller uses large batches."""
    for ids,points,spec in batches():
        if len(ids)!=len(points):raise ValueError('Point IDs and coordinates do not match.')
        for start in range(0,len(ids),POINT_BLOCK):
            yield ids[start:start+POINT_BLOCK],points[start:start+POINT_BLOCK],spec


class BoxHistogram:
    """Station rows with at most two bounded mappings, closed on every exit."""
    def __init__(self,path,stations,angles,radial):
        from lining_tiles import DiskRows
        self.shape=(stations,angles,radial)
        self.rows=DiskRows(path,stations,angles*radial,'<u4')

    def add(self,station,angular,radial):
        columns=self.shape[1]*self.shape[2]
        flat=station*columns+angular*self.shape[2]+radial
        unique,count=np.unique(flat,return_counts=True)
        for block,use,index in self.rows.groups(unique//columns,unique%columns):
            # Saturation preserves support rather than wrapping a very dense
            # cell back to zero after more than 2**32 coincident observations.
            values=block[index].astype(np.uint64)+count[use].astype(np.uint64)
            block[index]=np.minimum(values,np.iinfo(np.uint32).max).astype(np.uint32)

    def close(self):
        self.rows.close()

    def surface(self,centres,minimum,progress,cancel):
        ns,na,nr=self.shape
        # One station halo on both sides makes the 3x3 support identical at
        # every tile boundary. Angular support remains periodic.
        window_rows=max(3,HISTOGRAM_WINDOW_BYTES//(na*nr*4))
        core_rows=window_rows-2
        target=np.full((ns,na),np.nan,np.float32)
        support=np.zeros((ns,na),np.uint32)
        for core in range(0,ns,core_rows):
            end=min(ns,core+core_rows);start=max(0,core-1);stop=min(ns,end+1)
            _emit(progress,cancel,f'3/4 Finding supported ray surface · station boxes {core+1:,}–{end:,}/{ns:,}')
            hist=self.rows.read(start,stop).reshape(stop-start,na,nr)
            local,counts=_surface_from_histogram(hist,centres,minimum,progress,cancel)
            target[core:end]=local[core-start:end-start]
            support[core:end]=counts[core-start:end-start]
        return target,support


def _emit(progress,cancel,message):
    if cancel():raise ValueError('Ray/box extraction cancelled. Previous results and scan points are unchanged.')
    progress(message)


def _coordinates(points,model,station0,station_step,nstation,nangle):
    """Return station/angle cells and residual to the fitted lining."""
    p=np.asarray(points,float);q=p-model['reference'];t=q@model['axis'];nodes=model['nodes']
    cx=np.interp(t,nodes[:,0],nodes[:,1]);cy=np.interp(t,nodes[:,0],nodes[:,2]);radius=np.interp(t,nodes[:,0],nodes[:,3])
    x=q@model['u']-cx;y=q@model['v']-cy
    angle=np.mod(np.arctan2(y,x),2*np.pi)
    # Use the robust fitted section radius as the broad search origin. A dense
    # local pipe can pull either a local or angular profile inward; the ray
    # histogram itself must decide the outer supported surface. Non-circular
    # floor/profile variation remains available inside the configured radial
    # search range and is learned independently in each ray box.
    residual=np.hypot(x,y)-radius
    station=np.floor((t-station0)/station_step).astype(np.int64)
    angular=np.minimum(nangle-1,np.floor(angle/(2*np.pi)*nangle).astype(np.int64))
    k=np.clip(np.searchsorted(nodes[:,0],t),1,len(nodes)-1)
    supported=(t>=nodes[0,0]-model['pitch'])&(t<=nodes[-1,0]+model['pitch'])&((nodes[k,0]-nodes[k-1,0])<=model['pitch']*3.1)
    inside=(station>=0)&(station<nstation)&supported
    return station,angular,residual,inside


def _neighbour_sum(plane):
    """3 x 3 station/angle box sum, wrapping only around tunnel angle."""
    p=np.asarray(plane,dtype=np.uint64);out=np.zeros_like(p,dtype=np.uint64)
    for ds in (-1,0,1):
        if ds<0:source=p[1:];target=out[:-1]
        elif ds>0:source=p[:-1];target=out[1:]
        else:source=p;target=out
        target+=source+np.roll(source,1,axis=1)+np.roll(source,-1,axis=1)
    return out


def _surface_from_histogram(hist,centres,minimum,progress,cancel):
    """Choose the outermost locally supported radial mode for each ray box."""
    cells=hist.shape[:2];peak=np.zeros(cells,np.uint32)
    for k in range(hist.shape[2]):
        if k%10==0:_emit(progress,cancel,f'Finding supported ray modes · pass 1/2 · radial box {k+1}/{hist.shape[2]}')
        local=_neighbour_sum(hist[:,:,k]);peak=np.maximum(peak,np.minimum(local,np.iinfo(np.uint32).max).astype(np.uint32))
    target=np.full(cells,np.nan,np.float32);support=np.zeros(cells,np.uint32)
    # A deliberately low relative prominence lets sparse wall returns survive
    # behind a much denser pipe. Absolute neighbouring-box support remains
    # mandatory; outward/reflection candidates are still exposed for review.
    required=np.maximum(minimum,np.ceil(peak*.03)).astype(np.uint32)
    for k in range(hist.shape[2]-1,-1,-1):
        if k%10==0:_emit(progress,cancel,f'Finding supported ray modes · pass 2/2 · radial box {hist.shape[2]-k}/{hist.shape[2]}')
        own=np.asarray(hist[:,:,k]);local=_neighbour_sum(own);take=np.isnan(target)&(own>0)&(local>=required)
        target[take]=centres[k];support[take]=np.minimum(local[take],np.iinfo(np.uint32).max).astype(np.uint32)
    return target,support


def run_batches(batches,ids_path,labels_path,settings,progress,cancel):
    started=time.monotonic();sample_spacing=max(.02,min(.075,float(settings.get('resolution',.2))*.25))
    original_batches=batches
    batches=lambda:_bounded_batches(original_batches)
    sample,total=tunnel_lining.spatial_sample(batches,sample_spacing,160000,progress,cancel)
    _emit(progress,cancel,'1/4 Fitting a supported tunnel frame for radial projection')
    model=tunnel_lining.fit_model(sample,settings,progress,cancel)
    station_step=float(settings.get('ray_station_m',.25));nangle=max(12,int(math.ceil(360/float(settings.get('ray_angle_deg',3.)))))
    station0=float(model['nodes'][0,0]-model['pitch']);station1=float(model['nodes'][-1,0]+model['pitch'])
    nstation=max(1,int(math.ceil((station1-station0)/station_step)))
    if nstation*nangle>MAX_RAY_CELLS:
        nstation=max(1,MAX_RAY_CELLS//nangle);station_step=(station1-station0)/nstation
    inward=float(settings.get('ray_inward_mm',1200))/1000;outward=float(settings.get('ray_outward_mm',300))/1000
    depth=float(settings.get('ray_depth_mm',20))/1000;nbins=max(2,int(math.ceil((inward+outward)/depth)))
    edges=np.linspace(-inward,outward,nbins+1);depth=float(edges[1]-edges[0]);centres=((edges[:-1]+edges[1:])/2).astype(np.float32)
    minimum=int(settings.get('ray_min_points',4));keep=float(settings.get('ray_keep_mm',60))/1000
    work=Path(ids_path).parent
    needed=nstation*nangle*nbins*4+total*12
    if needed>shutil.disk_usage(work).free-50_000_000:
        raise ValueError(f'Ray/box extraction needs about {needed/1024**3:.2f} GiB of temporary disk space. Free disk space or increase the station, angle or radial box size.')
    with tempfile.TemporaryDirectory(prefix='ray_box_',dir=work) as folder:
        histogram_path=Path(folder)/'counts.u32'
        hist=BoxHistogram(histogram_path,nstation,nangle,nbins)
        indexed=0
        try:
            for _,points,_ in batches():
                _emit(progress,cancel,f'2/4 Indexing full-density points into ray boxes · {indexed+len(points):,}/{total:,}')
                station,angular,residual,inside=_coordinates(points,model,station0,station_step,nstation,nangle)
                radial=np.floor((residual+inward)/depth).astype(np.int64);use=inside&(residual>=-inward)&(residual<outward)&(radial>=0)&(radial<nbins)
                if np.any(use):hist.add(station[use],angular[use],radial[use])
                indexed+=len(points)
            if indexed!=total:raise ValueError('The scan selection changed during ray/box extraction. Run it again.')
            target,support=hist.surface(centres,minimum,progress,cancel)
        finally:
            hist.close()
        supported_boxes=int(np.isfinite(target).sum());counts=np.zeros(4,np.int64);sums=np.zeros((4,3));written=0
        with open(ids_path,'wb') as idsout,open(labels_path,'wb') as labout:
            for ids,points,_ in batches():
                _emit(progress,cancel,f'4/4 Classifying original points against ray surface · {written+len(ids):,}/{total:,}')
                station,angular,residual,inside=_coordinates(points,model,station0,station_step,nstation,nangle)
                labels=np.full(len(points),3,np.int32);valid=inside.copy();valid[inside]&=np.isfinite(target[station[inside],angular[inside]])
                shell=np.zeros(len(points),float);shell[valid]=residual[valid]-target[station[valid],angular[valid]]
                labels[valid&(np.abs(shell)<=keep)]=0
                labels[valid&(shell < -keep)]=1
                labels[valid&(shell > keep)]=2
                np.asarray(ids,dtype='<i8').tofile(idsout);labels.astype('<i4').tofile(labout);written+=len(ids)
                for k in range(4):
                    use=labels==k;counts[k]+=int(use.sum());sums[k]+=np.asarray(points)[use].sum(0)
    if written!=total:raise ValueError('The scan selection changed during ray/box extraction. Run it again.')
    if counts[0]<50:raise ValueError('Too few original points match the ray-supported lining. Include both walls and crown of one branch, or use the existing Clean tunnel method.')
    names=['Ray-supported tunnel lining','Interior objects before lining · review','Outside surface / possible reflections · review','Unsupported ray boxes · retained']
    rows=[dict(id=k,name=names[k],points=int(counts[k]),supervoxels=0,centre=(sums[k]/counts[k]).tolist(),linearity=0.,planar_fraction=0.,overlap=0.,automatic_keep=k in (0,3)) for k in range(4) if counts[k]]
    kept=[k for k in (0,3) if counts[k]]
    return dict(kept=kept,clusters=rows,research_dois=[DOI],statistics=dict(input_points=total,unique_points=None,duplicate_locations=None,supervoxels=0,expected_supervoxels=0,merge_passes=0,initial_edges=0,removed_edges=0,retained_edges=0,clusters=len(rows),kept_points=int(counts[0]+counts[3]),noise_points=0,noise_candidates=int(counts[1]+counts[2]),calibration_points=len(sample),fitted_sections=len(model['nodes']),reference_radius_m=model['radius'],ray_station_m=station_step,ray_angle_deg=360/nangle,ray_depth_mm=depth*1000,ray_keep_mm=keep*1000,ray_boxes=nstation*nangle,supported_ray_boxes=supported_boxes,unsupported_points=int(counts[3]),box_histogram_bins=nbins,strategy='outward radial modes with 3x3 station/angle box support; all original points classified',seconds=round(time.monotonic()-started,3)),warnings=[
        'Independent adaptation inspired by DOI '+DOI+'; the full paper and author code were unavailable, so this is not a reproduction and published accuracy or speed is not claimed.',
        'Green unsupported boxes are deliberately retained. Red groups are review candidates, not confirmed equipment or errors.',
        'Inspect recesses, openings, junctions, rough SCL and possible outward reflections. Use one tunnel branch and do not use the cleaned result for deformation decisions without independent control and section checks.',
        'Every selected original point and ID was classified at float64 coordinates; bounded file mappings and overlapping histogram windows limit working memory.'
    ])
