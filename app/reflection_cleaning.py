# Copyright (c) 2026 Damian Gulczynski.
"""Conservative reflection candidates, with explicit independent evidence.

Original implementation: supported lining departure + approximate mirrored
geometry, scanner visibility and optional intensity evidence. Not a physical
return classifier or reproduction of a published detector. Every selected
original ID is classified; weak evidence remains retained for review.
"""
import time
import numpy as np
from scipy.spatial import cKDTree
import tunnel_lining
from combined_denoising import SAMPLER_VERSION, sample_originals


def scanner_origin(item):
    """Never substitute a bounding-box centre for an acquisition position."""
    if not item or not (item.get('meta',{}).get('has_scanner_pose') is True or item.get('meta',{}).get('source_format')=='hspca'):
        return None
    try:p=np.asarray(item.get('scan_origin'),float)
    except (TypeError,ValueError):return None
    return p if p.shape==(3,) and np.isfinite(p).all() else None




class Evidence:
    def __init__(self,sample,model,settings,origin=None,intensity_limits=None):
        self.model=model;self.origin=origin;self.intensity_limits=intensity_limits
        self.gap=float(settings.get('reflection_gap_mm',100))/1000
        self.match=float(settings.get('reflection_match_mm',100))/1000
        self.wall_band=min(.05,self.gap/2)
        d,known=tunnel_lining.deviations(sample,model)
        wall=sample[known&(np.abs(d)<=self.wall_band)]
        inside=sample[known&(d < -self.gap)]
        self.wall=cKDTree(wall-model['reference']) if len(wall)>=3 else None
        self.inside=cKDTree(inside-model['reference']) if len(inside)>=3 else None
        self.origin_valid=False
        if origin is not None:
            od,ok=tunnel_lining.deviations(origin[None,:],model)
            self.origin_valid=bool(ok[0] and od[0]<-self.gap)

    def classify(self,points,intensity=None):
        p=np.asarray(points,float);d,known,normal=tunnel_lining.deviations(p,self.model,True)
        # 0 = retained; 1 = multiple-cue candidate; 2 = weak evidence retained;
        # 3 = unsupported lining, retained. Low/high intensity alone never rejects.
        labels=np.zeros(len(p),np.int32);labels[~known]=3
        use=np.flatnonzero(known&(d>self.gap));labels[use]=2
        mirror=np.zeros(len(p),bool);blocked=np.zeros(len(p),bool);unusual=np.zeros(len(p),bool)
        if not len(use):return labels,mirror,blocked,unusual
        q=p[use];delta=d[use];ref=self.model['reference']
        foot=q-delta[:,None]*normal[use]
        supported=np.zeros(len(q),bool)
        if self.wall is not None:
            distance,_=self.wall.query(foot-ref,k=3,distance_upper_bound=self.match)
            supported=np.isfinite(distance).all(1)
        if self.inside is not None:
            reflected=q-2*delta[:,None]*normal[use]
            distance,_=self.inside.query(reflected-ref,k=3,distance_upper_bound=self.match)
            mirror[use]=supported&np.isfinite(distance).all(1)
        if self.origin_valid and self.wall is not None:
            # Cross the fitted envelope, then require measured wall returns at
            # that crossing. A missing wall at an opening is not an obstruction.
            lo=np.zeros(len(q));hi=np.ones(len(q));direction=q-self.origin
            for _ in range(14):
                mid=(lo+hi)*.5;probe=self.origin+mid[:,None]*direction
                sd,_=tunnel_lining.deviations(probe,self.model)
                outside=sd>0;hi=np.where(outside,mid,hi);lo=np.where(outside,lo,mid)
            crossing=self.origin+((lo+hi)*.5)[:,None]*direction
            _,valid=tunnel_lining.deviations(crossing,self.model)
            distance,_=self.wall.query(crossing-ref,k=3,distance_upper_bound=self.match)
            blocked[use]=valid&np.isfinite(distance).all(1)&(np.linalg.norm(q-crossing,axis=1)>self.gap)
        if intensity is not None and self.intensity_limits is not None:
            values=np.asarray(intensity,float);low,high=self.intensity_limits
            unusual=np.isfinite(values)&((values<low)|(values>high))
        # Require two independent additional cues; departure alone is not proof.
        strong=(mirror&blocked)|(unusual&(mirror|blocked))
        labels[strong&(labels==2)]=1
        return labels,mirror,blocked,unusual


def run_batches(batches,ids_path,labels_path,settings,progress,cancel,selected_total,origin=None,intensity_for_ids=None):
    started=time.monotonic()
    def emit(message):tunnel_lining._emit(progress,cancel,message)
    emit('1/3 Learning supported lining for reflection review')
    sample,_=sample_originals(batches,.02,140000,progress,cancel)
    model=tunnel_lining.fit_model(sample,{**settings,'envelope_thickness':.05},progress,cancel)
    model=tunnel_lining.longitudinal_reference(sample,model)
    limits=None;values=np.empty(0);ranks=np.empty(0,np.uint64);intensity_count=0
    if intensity_for_ids is not None:
        for ids,_,_ in batches():
            emit('2/3 Calibrating intensity evidence')
            v=np.asarray(intensity_for_ids(ids),float);valid=np.isfinite(v);v=v[valid]
            rank=np.asarray(ids,dtype=np.uint64)[valid]
            rank=(rank^(rank>>np.uint64(30)))*np.uint64(0xbf58476d1ce4e5b9)
            rank=(rank^(rank>>np.uint64(27)))*np.uint64(0x94d049bb133111eb);rank^=rank>>np.uint64(31)
            values=np.r_[values,v];ranks=np.r_[ranks,rank]
            if len(values)>50000:
                chosen=np.argpartition(ranks,49999)[:50000];values=values[chosen];ranks=ranks[chosen]
        intensity_count=len(values)
        if intensity_count>=100:
            v=values;median=float(np.median(v));mad=float(np.median(np.abs(v-median)))
            scale=max(mad*1.4826,float(np.ptp(np.percentile(v,[10,90])))/2.56)
            if scale>max(1e-12,abs(median)*1e-9):limits=(median-6*scale,median+6*scale)
    detector=Evidence(sample,model,settings,origin,limits)
    counts=np.zeros(4,np.int64);sums=np.zeros((4,3));reps={i:[] for i in range(4)};written=0;cues=np.zeros(3,np.int64)
    with open(ids_path,'wb') as idout,open(labels_path,'wb') as labout:
        for ids,points,_ in batches():
            # Bound both neighbourhood and ray-crossing work and cancellation latency.
            for start in range(0,len(ids),25000):
                ix=np.asarray(ids[start:start+25000]);p=np.asarray(points[start:start+25000],float)
                emit(f'3/3 Reflection evidence · {written+len(ix):,}/{selected_total:,} original points')
                intensity=intensity_for_ids(ix) if intensity_for_ids is not None else None
                labels,mirror,blocked,unusual=detector.classify(p,intensity)
                cues+=np.array([mirror.sum(),blocked.sum(),(unusual&(labels!=0)).sum()],np.int64)
                ix.astype('<i8').tofile(idout);labels.astype('<i4').tofile(labout)
                for k in range(4):
                    use=labels==k;counts[k]+=use.sum();sums[k]+=p[use].sum(0)
                    reps[k].extend((written+np.flatnonzero(use)[:max(0,32-len(reps[k]))]).tolist())
                written+=len(ix)
    if written!=selected_total:raise ValueError('The source selection changed during reflection detection.')
    names=['Supported geometry · retained','Likely reflection artefacts · multiple cues · review','Outside lining / weak reflection evidence · retained','Uncertain lining support · retained']
    kept=[i for i in (0,2,3) if counts[i]]
    rows=[dict(id=k,name=names[k],points=int(counts[k]),centre=(sums[k]/counts[k]).tolist(),preview_indices=reps[k],automatic_keep=k in kept,reflection_candidate=k==1,evidence='Lining departure plus at least two of mirrored geometry, scanner obstruction and anomalous intensity' if k==1 else 'Insufficient evidence for removal',supervoxels=0,linearity=0.,planar_fraction=0.,overlap=0.) for k in range(4) if counts[k]]
    warnings=['Red groups are likely reflection artefacts, not confirmed reflections. Inspect openings, recesses, glazing and real objects before excluding them. The source is unchanged until you apply a reviewed result.',
              'Weak evidence and unsupported lining areas are retained. Low or high intensity alone never removes points. Full-waveform and multi-return classifications are not available in this detector.',
              'This detector assumes one supported tunnel branch. It may miss reflections on floors, equipment or areas without a reliable lining model. Original coordinates, intensity and colour are preserved.']
    if not detector.origin_valid:warnings.append('No usable scanner position inside this lining is available. Visibility evidence is disabled; missing pose data is never guessed.')
    if limits is None:warnings.append('No usable intensity variation is available. Intensity evidence is disabled.')
    stats=dict(input_points=written,unique_points=None,duplicate_locations=None,supervoxels=0,expected_supervoxels=0,merge_passes=0,initial_edges=0,removed_edges=0,retained_edges=0,clusters=len(rows),kept_points=int(counts[[0,2,3]].sum()),noise_points=0,noise_candidates=int(counts[1]),reflection_candidates=int(counts[1]),weak_evidence_retained=int(counts[2]),uncertain_retained=int(counts[3]),calibration_points=len(sample),mirror_supported_points=int(cues[0]),scanner_obstructed_points=int(cues[1]),intensity_cue_points=int(cues[2]),scanner_position_used=bool(detector.origin_valid),intensity_used=limits is not None,minimum_departure_mm=detector.gap*1000,geometry_match_mm=detector.match*1000,strategy='Supported lining departure plus multiple independent cues; weak evidence retained; review before removal',seconds=round(time.monotonic()-started,3))
    return dict(kept=kept,clusters=rows,statistics=stats,warnings=warnings,sampler_version=SAMPLER_VERSION)
