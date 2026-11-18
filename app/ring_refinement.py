"""Targeted measured 10 mm evidence for one missing joint between known rings."""
import time
import numpy as np


def refine(detector, batches_for_box, intensity_for_ids=None):
    import lining_features as lf
    d=detector
    width=float(d.s.get('lining_ring_width_m',0))
    d.refinement_checks=[]
    if width<=0 or d.cell<=.01001 or d.s.get('lining_task') not in ('all','seams','segments'):
        return
    for left,right in list(zip(d.rings[:-1],d.rings[1:])):
        lo,hi=left['station_m'],right['station_m'];span=hi-lo
        # Only bounded single-gap checks. Longer missing coverage stays unresolved.
        if not 1.5*width<span<2.5*width:continue
        start=time.monotonic();mid=(lo+hi)/2;pad=1.
        d.emit('4/5 Preparing bounded 10 mm ring-gap evidence')
        if np.ceil((span+2*pad+.02)/.01)*np.ceil(2*np.pi*d.m['radius']/.01)>2000000:
            d.refinement_checks.append(dict(bounds_m=[lo,hi],status='unresolved',reason='Fine gap exceeds bounded map budget',accepted_stations_m=[],input_points=0,seconds=0.))
            continue
        m=d.m;centre=np.asarray(m['reference'])+mid*np.asarray(m['axis'])
        nodes=np.asarray(m['nodes']);rad=m['radius']+max(lf.bolt_allowances(d.s))+.2
        cross=np.max(np.abs(nodes[:,1:3]),axis=0)+rad
        box=dict(mode='oriented_box',centre=centre.tolist(),axes=[np.asarray(m[k]).tolist() for k in ('axis','u','v')],half_size=[span/2+pad,*cross.tolist()])
        settings={**d.s,'lining_detail':'more_detailed','lining_task':'seams'}
        fine=lf.Detector(m,(lo-pad,hi+pad),settings,d.emit);fine.defer_ring_sequence=True
        fine.preserve_ring_alternatives=True
        total=0
        for ids,p,_ in batches_for_box(box):
            d.emit('4/5 Rechecking an unresolved ring gap at 10 mm · measured points only')
            t=(p-m['reference'])@m['axis'];use=(t>=lo-pad)&(t<=hi+pad)
            ids,p=ids[use],p[use];total+=len(ids)
            fine.accumulate(p,intensity_for_ids(ids) if intensity_for_ids else None)
        fine.analyse(ring_only=True)
        candidates=[q for q in fine.rings if lf.ring_width_matches(d.s,q['station_m']-lo) and lf.ring_width_matches(d.s,hi-q['station_m'])]
        accepted=[];preserved=assigned=0
        if len(candidates)==1:
            row=dict(candidates[0]);old=row.pop('id');row.pop('name',None);row.pop('status',None);row.pop('kind',None)
            row.update(evidence_cell_m=.01,automatic_refinement=True)
            if hasattr(d,'labels'):value=d.add(dict(row,kind='ring_seam',name='Refined ring joint',status='candidate'))
            else:value=d.add('ring_seam','Refined ring joint',**row)
            d.rings.append(value);accepted.append(value['station_m'])
            ii,jj=np.where(fine.grid_labels==old)
            t=fine.t0+(ii+.5)*fine.cell;a=(jj+.5)*2*np.pi/fine.na
            ri=np.floor((t-d.t0)/d.cell).astype(int);ci=np.minimum(d.na-1,(a*d.na/(2*np.pi)).astype(int));use=(ri>=0)&(ri<d.nt)
            # Several fine cells can map to one parent cell. Preserve every
            # existing owner (including bolt pockets and other seam labels).
            cells=np.unique(ri[use]*d.na+ci[use]);ri,ci=cells//d.na,cells%d.na
            previous=d.labels.lookup(ri,ci) if hasattr(d,'labels') else d.grid_labels[ri,ci]
            free=previous==0;preserved=int((~free).sum());assigned=int(free.sum())
            if hasattr(d,'labels'):d.labels.put(ri[free],ci[free],np.full(assigned,value['id'],dtype=np.int32))
            else:d.grid_labels[ri[free],ci[free]]=value['id']
        reason=('Unique measured trace supported by both neighbours' if accepted else
                'Multiple measured traces fit both neighbours; joint remains unresolved' if len(candidates)>1 else
                'No measured trace fits both neighbours')
        d.refinement_checks.append(dict(bounds_m=[lo,hi],input_points=total,accepted_stations_m=accepted,
            candidate_stations_m=[q['station_m'] for q in candidates],reason=reason,
            preserved_label_cells=preserved,assigned_label_cells=assigned,
            status='candidate' if accepted else 'unresolved',seconds=time.monotonic()-start))
        del fine
    d.rings.sort(key=lambda q:q['station_m'])
    for i,row in enumerate(d.rings,1):row['name']=f'Ring seam {i}'
