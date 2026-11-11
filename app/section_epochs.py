# Copyright (c) 2026 Damian Gulczynski.
"""Read retained scan points in common alignment slabs for one or two survey epochs."""
import uuid
import numpy as np

import analysis_sources
import compute_runtime
import jobdata
from section_fitting import MAX_SAMPLE, fit_circle_section, fit_ellipse_section, convergence_analysis

MAX_STATIONS = 32


def available(s):
    axis=s.STATE.get('axis')
    sources=[dict(id=x['id'], name=x['name']) for x in s.STATE.get('job_items', [])
             if x.get('kind')=='scan' and not x.get('merge_archived') and not x.get('merge_stale')]
    return dict(sources=sources, alignment=None if axis is None else dict(name=axis.name,
                start=float(axis.a[0,0]), end=float(axis.a[-1,0])), result=s.STATE.get('scan_section_fits'))


def _read_stations(item, frames, width, band, datum, progress, cancel):
    rng=np.random.default_rng(13271)
    buckets=[dict(points=np.empty((0,3)), keys=np.empty(0), total=0) for _ in frames]
    clip=item.get('limit_box')
    scope=None
    if clip:
        scope=dict(mode='limit',selection=None,limits=[jobdata.selection_spec({'mode':'box',**clip})])
    for _, q in analysis_sources.blocks(item['meta'], progress=progress, cancel=cancel,scope=scope):
        if not len(q):continue
        lo,hi=q.min(axis=0),q.max(axis=0)
        for bucket, (_, frame, radius) in zip(buckets,frames):
            compute_runtime.check_cancel(cancel)
            origin=frame['origin']; extent=radius+band+width/2
            if np.any(origin+extent < lo) or np.any(origin-extent > hi):continue
            delta=q-origin; normal=delta @ frame['normal']
            idx=np.flatnonzero(abs(normal) <= width/2)
            if not len(idx):continue
            radial=np.hypot(delta[idx] @ frame['right'], delta[idx] @ frame['up'])
            points=q[idx[np.abs(radial-radius) <= band]]
            bucket['total']+=len(points)
            if not len(points):continue
            keys=np.r_[bucket['keys'],rng.random(len(points))]
            retained=np.concatenate((bucket['points'], points))
            if len(keys)>MAX_SAMPLE:
                keep=np.argpartition(keys, -MAX_SAMPLE)[-MAX_SAMPLE:]
                keys,retained=keys[keep],retained[keep]
            bucket.update(points=retained,keys=keys)
    records=[]
    for (ch,frame,radius),bucket in zip(frames,buckets):
        compute_runtime.check_cancel(cancel)
        prefix=f"{item['name']} · chainage {ch:g} m"
        def report(message):progress(prefix+' · '+message)
        rec=dict(chainage=ch,datum=datum,frame={k:v.tolist() for k,v in frame.items()},
                 width_m=width,band_m=band,design_radius_m=radius,
                 total_points=bucket['total'],sample_points=len(bucket['points']))
        if len(bucket['points'])<24:
            rec['error']='Fewer than 24 points in this section and wall band.'
        else:
            for label,fun in (('circle',fit_circle_section), ('ellipse',fit_ellipse_section)):
                try:
                    rec[label]=fun(bucket['points'],report,cancel,frame=frame)
                except (jobdata.Cancelled,compute_runtime.ProcessingCancelled):
                    raise
                except (ValueError,np.linalg.LinAlgError) as err:
                    compute_runtime.check_cancel(cancel)
                    rec['error']=f'{label.capitalize()} fit withheld. {err}'
                    break
        records.append(rec)
    return records


def run(s, body, progress, cancel=compute_runtime.cancelled):
    from scan_geometry import section_item, section_revision
    compute_runtime.check_cancel(cancel)
    axis=s.STATE.get('axis'); second_id=body.get('comparison_id')
    if axis is None: raise ValueError('Load a reviewed alignment before fitting sections by chainage.')
    first=section_item(s,body.get('id')); second=section_item(s,second_id) if second_id else None
    items=[first,second] if second else [first]
    if any(i.get('kind')!='scan' or i.get('merge_archived') or i.get('merge_stale') or i.get('analysis_collection') for i in items):
        raise ValueError('Select one current scan or registered cloud per epoch.')
    if second:
        sources_a = {first['id'], *first.get('merge_source_ids', [])}
        sources_b = {second['id'], *second.get('merge_source_ids', [])}
        if sources_a & sources_b or analysis_sources.overlap(first['meta'], second['meta']):
            raise ValueError('Choose independently observed scans for the two epochs.')
    if second and body.get('common_datum') is not True:
        raise ValueError('Confirm the two epochs share the same checked survey datum.')
    datum=str(body.get('datum') or '').strip()[:160]
    if second and not datum:
        raise ValueError('Enter the common survey datum or control network for this comparison.')
    stations=body.get('stations');width=float(body.get('width',.1));band=float(body.get('band',.3))
    if not isinstance(stations,list) or not 1<=len(stations)<=MAX_STATIONS:
        raise ValueError('Enter 1 to 32 distinct chainages.')
    if not .001<=width<=5 or not .001<=band<=10:
        raise ValueError('Use a 0.001–5 m slice width and a 0.001–10 m radial wall band.')
    stations=sorted(float(ch) for ch in stations)
    if not np.isfinite(stations).all() or len({round(x,6) for x in stations})!=len(stations):
        raise ValueError('Chainages must be distinct finite values in metres.')
    frames=[]
    for ch in stations:
        origin,normal,right,up,radius=axis.at(ch)
        frames.append((ch,dict(origin=origin,normal=normal,right=right,up=up),radius))
    stamps=[section_revision(s,x,None) for x in items]
    sets=[_read_stations(x,frames,width,band,datum,progress,cancel) for x in items]
    comparison=convergence_analysis(*sets,progress=progress,cancel=cancel) if second else None
    for item,stamp in zip(items,stamps):
        if stamp!=section_revision(s,section_item(s,item['id']),None):
            raise ValueError('The source or alignment changed. Run the section fitting again.')
    compute_runtime.check_cancel(cancel)
    return dict(request_id=str(body.get('request_id') or uuid.uuid4().hex)[:96],
                axis_name=axis.name,datum=datum,width_m=width,band_m=band,
                source_a=first['name'],source_b=second['name'] if second else None,
                source_ids=[x['id'] for x in items],source_revisions=stamps,
                sections_a=sets[0],sections_b=sets[1] if second else [],comparison=comparison,
                warnings=['Each diameter is fitted to retained lining points in the stated slice and wall band. The largest samples use 32,000 points per section.',
                          'Positive chord closure is shortening, measured along a line through each fitted ellipse centre. Angles increase from local right towards crown when looking along increasing chainage.',
                          'Datum errors can appear as displacement. Each RMS describes fit residuals in the inlier sample.'])
