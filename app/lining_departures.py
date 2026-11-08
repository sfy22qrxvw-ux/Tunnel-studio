"""Select measured points outside a lining band without a bolt-size filter.

Whole-tunnel checks stream original IDs against the supported lining reference.
Local checks fit a robust quadratic wall in a bounded neighbourhood. Distances
are fitting residuals, not survey control, defect probabilities or guarantees.
Points are never moved or reconstructed; unsupported areas remain retained.
"""
import time
import numpy as np
import tunnel_lining

NAMES={0:'Within the lining tolerance',1:'Raised above lining · into tunnel',2:'Recessed below surrounding lining',3:'Unassessed · insufficient reference support'}


def local_labels(points,seed,direction,cfg,progress):
    p=np.asarray(points,float)
    if p.ndim!=2 or p.shape[1]!=3 or len(p)<60 or not np.isfinite(p).all():raise ValueError('Use at least 60 finite measured wall points.')
    centre=p.mean(0);q=p-centre
    _,axes=np.linalg.eigh(q.T@q/max(1,len(q)));normal=axes[:,0]
    view=np.asarray(direction,float)
    if view.shape!=(3,) or not np.isfinite(view).all() or np.linalg.norm(view)<1e-12:
        raise ValueError('Pick the wall from inside the tunnel to set the lining side.')
    if normal@view<0:normal=-normal
    u=axes[:,2];v=np.cross(normal,u);local=q@np.column_stack([u,v,normal])
    scale=max(float(np.max(np.linalg.norm(local[:,:2],axis=1))),1e-6)
    x,y=(local[:,:2]/scale).T;X=np.c_[np.ones(len(p)),x,y,x*x,x*y,y*y]
    use=np.ones(len(p),bool);converged=False
    for iteration in range(15):
        if use.sum()<60:raise ValueError('Include more surrounding wall to fit a lining reference.')
        coef,_,rank,_=np.linalg.lstsq(X[use],local[use,2],rcond=None)
        if rank<6 or np.linalg.cond(X[use])>10000:raise ValueError('The wall sample does not span a usable surface patch.')
        residual=local[:,2]-X@coef;median=np.median(residual[use]);mad=1.4826*np.median(abs(residual[use]-median))
        next_use=abs(residual-median)<=max(.002,3.5*mad)
        progress(f'Fitting local lining · pass {iteration+1}/15 · {int(next_use.sum()):,} supporting points')
        if np.array_equal(use,next_use):converged=True;break
        use=next_use
    if use.sum()<max(60,len(p)*.4):raise ValueError('Too little consistent lining. Include more wall around the feature.')
    rms=float(np.sqrt(np.mean(residual[use]**2)));tolerance=cfg['min_height_mm']/1000
    labels=np.zeros(len(p),'<i4');labels[residual>tolerance]=1;labels[residual<-tolerance]=2
    groups=[dict(id=k,name=NAMES[k],points=int((labels==k).sum()),centre=p[labels==k].mean(0).tolist(),selected=False) for k in (1,2) if np.any(labels==k)]
    warnings=['The band is relative to a fitted measured wall. It can flag joints, recesses and scan artefacts. Review before removing or separating points.','No diameter, compactness or bolt-template filter is applied. No missing lining is reconstructed.']
    if not converged:warnings.append('The local reference reached its fitting pass limit. Review its residuals before use.')
    if tolerance<4*rms:warnings.append('The tolerance is small compared with the local reference residual. Review the wall points carefully.')
    stats=dict(converged=converged,reference_rms_mm=rms*1000,reference_points=int(use.sum()),tolerance_mm=tolerance*1000,raised_points=int((labels==1).sum()),recessed_points=int((labels==2).sum()),normal=normal.tolist())
    return labels,groups,stats,warnings


def run_batches(batches,ids_path,labels_path,settings,model,progress,cancel,total):
    started=time.monotonic();tolerance=float(settings['lining_depth_mm'])/1000
    counts=np.zeros(4,np.int64);sums=np.zeros((4,3));reps={k:[] for k in range(4)};written=0
    reference=np.asarray(model['reference'],float)
    with open(ids_path,'wb') as ids_out,open(labels_path,'wb') as labels_out:
        for ids,p,_ in batches():
            for start in range(0,len(ids),100000):
                if cancel():raise ValueError('Lining departure selection cancelled. Source points are unchanged.')
                ix=np.asarray(ids[start:start+100000],'<i8');q=np.asarray(p[start:start+100000],float)
                d,supported=tunnel_lining.deviations(q,model)
                labels=np.zeros(len(q),'<i4');labels[supported&(d < -tolerance)]=1;labels[supported&(d > tolerance)]=2;labels[~supported]=3
                ix.tofile(ids_out);labels.tofile(labels_out);counts+=np.bincount(labels,minlength=4)
                for j in range(3):sums[:,j]+=np.bincount(labels,weights=q[:,j]-reference[j],minlength=4)
                for k in np.unique(labels):reps[int(k)].extend((written+np.flatnonzero(labels==k)[:max(0,32-len(reps[int(k)]))]).tolist())
                written+=len(ix);progress(f'Selecting lining departures · {written:,}/{total:,} original points checked')
    if written!=total:raise ValueError('The source changed during lining departure selection.')
    rows=[dict(id=k,name=NAMES[k],points=int(counts[k]),centre=(sums[k]/counts[k]+reference).tolist(),automatic_keep=True,feature_kind='lining_departure' if k in (1,2) else 'background',departure_candidate=k in (1,2),preview_indices=reps[k],supervoxels=0,linearity=0.,planar_fraction=0.,overlap=0.) for k in range(4) if counts[k]]
    stats=dict(input_points=written,unique_points=None,duplicate_locations=None,supervoxels=0,expected_supervoxels=0,merge_passes=0,initial_edges=0,removed_edges=0,retained_edges=0,clusters=len(rows),kept_points=written,noise_points=0,noise_candidates=int(counts[1]+counts[2]),raised_points=int(counts[1]),recessed_points=int(counts[2]),unassessed_points=int(counts[3]),tolerance_mm=tolerance*1000,bolt_holes=0,bolt_fittings=0,ring_seams=0,longitudinal_seams=0,segment_candidates=0,seconds=round(time.monotonic()-started,3))
    warnings=['Every supported retained point outside the band is a candidate, including isolated points, joints and pockets. Review before removing or separating.','The reference follows measured lining, not a design surface or survey control. Unsupported areas remain retained. No diameter or bolt-shape filter is used.','Use one tunnel branch with sufficient walls and crown. A small wall crop uses the local picked-point scope. No missing lining is reconstructed.']
    return dict(kept=[k for k in range(4) if counts[k]],clusters=rows,features=[],lining_frame={k:np.asarray(model[k]).tolist() for k in ('reference','axis','u','v')},research_dois=[],detection=dict(status='candidates' if counts[1]+counts[2] else 'no_candidates',summary=f'{counts[1]:,} raised and {counts[2]:,} recessed points outside ±{tolerance*1000:g} mm. {counts[3]:,} points lack sufficient reference support and remain retained.'),warnings=warnings,statistics=stats)
