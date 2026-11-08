# Copyright (c) 2026 Damian Gulczynski.
"""Rigid target registration and experimental planar four-quadrant target fitting."""
import hashlib, json, math, uuid
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree
import pointio
from checker_evidence import CheckerEvidence, require_checker, PatchSample


def solve(rows, tolerance_mm=5, mode="rigid"):
    if mode not in ("rigid","vertical"):raise ValueError("Choose full 3D or keep vertical geometry.")
    if not isinstance(rows,list) or len(rows)>200: raise ValueError('Supply no more than 200 target pairs.')
    tol=float(tolerance_mm)
    if not math.isfinite(tol) or tol<=0: raise ValueError('Review limit must be positive millimetres.')
    ids=set(); usable=[]
    for row in rows:
        name=str(row.get('id','')).strip()
        if not name or name in ids: raise ValueError('Every target needs a unique, non-empty ID.')
        ids.add(name); role=row.get('role','fit')
        if role not in ('fit','check','off'): raise ValueError('Target role must be fit, check or off.')
        if role=='off': continue
        if any(v is None for v in row['source']+row['control']): raise ValueError(f'{name}: enter all six coordinates.')
        p=np.asarray(row['source'],float); q=np.asarray(row['control'],float)
        if p.shape!=(3,) or q.shape!=(3,) or not np.isfinite([p,q]).all(): raise ValueError(f'{name}: enter all six coordinates in metres.')
        usable.append({'id':name,'role':role,'source':p.tolist(),'control':q.tolist()})
    fit=[r for r in usable if r['role']=='fit']
    if len(fit)<(2 if mode=='vertical' else 3): raise ValueError('Use at least two horizontally separated fit targets for keep-vertical geometry.' if mode=='vertical' else 'Use at least 3 non-collinear fit targets. Four or more well spread targets are recommended.')
    p=np.array([r['source'] for r in fit]); q=np.array([r['control'] for r in fit]); pc=p.mean(0); qc=q.mean(0)
    geometry=[]
    for label,v in [('scan',p),('control',q)]:
        distances=np.linalg.norm(v[:,None]-v[None,:],axis=2); distances+=np.eye(len(v))*1e99
        if distances.min()<1e-6: raise ValueError(f'Duplicate {label} target positions cannot define independent observations.')
        if mode=='vertical':
            spread=np.linalg.norm(v[:,:2]-v[:,:2].mean(0),axis=1).max()
            if spread<1e-4:raise ValueError('Keep-vertical targets must be separated horizontally; a vertical target pair cannot determine heading.')
            geometry.append(1.);continue
        s=np.linalg.svd(v-v.mean(0),compute_uv=False)
        if s[0]<1e-5 or s[1]/s[0]<1e-3: raise ValueError(f'{label.title()} targets are collinear or nearly collinear. Add targets spread across the tunnel.')
        geometry.append(float(s[1]/s[0]))
    u,s,vt=np.linalg.svd((p-pc).T@(q-qc)); correction=np.eye(3); correction[2,2]=np.linalg.det(vt.T@u.T)
    rotation=vt.T@correction@u.T
    if mode=='vertical':
        pp=p-pc;qq=q-qc;angle=np.arctan2(np.sum(pp[:,0]*qq[:,1]-pp[:,1]*qq[:,0]),np.sum(pp[:,:2]*qq[:,:2]));c,s=np.cos(angle),np.sin(angle);rotation=np.array([[c,-s,0],[s,c,0],[0,0,1.]])
    translation=qc-rotation@pc
    residuals=[]
    for row in usable:
        transformed=rotation@(np.asarray(row['source'])-pc)+qc
        d=(transformed-np.asarray(row['control']))*1000
        residuals.append({**row,'transformed':transformed.tolist(),'residual_mm':d.tolist(),'error_mm':float(np.linalg.norm(d)),'within_limit':bool(np.linalg.norm(d)<=tol)})
    def stats(role):
        errors=[r['error_mm'] for r in residuals if r['role']==role]
        return {'count':len(errors),'rms_mm':float(np.sqrt(np.mean(np.square(errors)))) if errors else None,'max_mm':max(errors) if errors else None}
    fitstats=stats('fit'); checkstats=stats('check'); warnings=[]
    if mode=='vertical':warnings.append('Vertical direction is held fixed: both scans must already share the same up direction. Roll and pitch are not solved from these targets.')
    if len(fit)==2:warnings.append('Only two fit targets: add an independent check target. A small fit residual does not verify the assumed vertical direction.')
    if len(fit)==3: warnings.append('Only three fit targets: add independent check targets and additional fit observations.')
    if not checkstats['count']: warnings.append('No independent check target. Fit residuals alone do not verify external accuracy.')
    if min(geometry)<.05: warnings.append('Weak target spread: distribute targets across height, width and length.')
    if any(not r['within_limit'] for r in residuals): warnings.append('One or more residuals exceed your review limit. Check IDs, target centres, units and control before use.')
    matrix=np.eye(4); matrix[:3,:3]=rotation; matrix[:3,3]=translation
    payload={'rows':rows,'limit':tol,'mode':mode}; fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return {'id':uuid.uuid4().hex,'fingerprint':fingerprint,'rotation':rotation.tolist(),'translation':translation.tolist(),'source_centroid':pc.tolist(),'control_centroid':qc.tolist(),'matrix':matrix.tolist(),'scale':1.0,'units':'metres','mode':mode,'constraint':'Keep existing vertical direction (Z); solve heading and translation' if mode=='vertical' else 'Full 3D rotation and translation','method':('Equal-weight fixed-Z heading and translation fit. Scale fixed.' if mode=='vertical' else 'Equal-weight least-squares rigid 3D transform (SVD, proper rotation, fixed scale).')+' Output = R * input + t. Residual = transformed scan minus control.','fit':fitstats,'check':checkstats,'residuals':residuals,'tolerance_mm':tol,'warnings':warnings}


def transform(points,solution):
    return (np.asarray(points)-solution['source_centroid'])@np.asarray(solution['rotation']).T+solution['control_centroid']


def transformed_cloud(meta,solution,destination,progress=lambda s:None):
    out=Path(destination); p,a=pointio.arrays(meta); lo=np.full(3,np.inf); hi=-lo
    try:
        with out.open('wb') as f:
            for i in range(0,len(p),pointio.CHUNK):
                q=transform(p[i:i+pointio.CHUNK],solution); q.astype('<f8').tofile(f)
                lo=np.minimum(lo,q.min(0)); hi=np.maximum(hi,q.max(0)); progress(f'Positioning scan: {min(i+len(q),len(p)):,} / {len(p):,}')
    except Exception: out.unlink(missing_ok=True); raise
    finally:
        p._mmap.close()
        if a is not None:a._mmap.close()
    result={**meta,'file':str(out),'name':Path(meta['name']).stem+' — registered','minimum':lo.tolist(),'maximum':hi.tolist(),'registration_id':solution['id']}
    for key in ('registration_rotation','registration_translation','virtual_registration','preview_file','preview_count','preview_stride','retained_properties','retained_summary'):
        result.pop(key,None)
    return result


def luminance(a,channel='intensity'):
    if a is None: raise ValueError('This scan has XYZ only. Re-import an E57/LAS/PLY/PTS with intensity or RGB, or enter measured target centres manually.')
    if channel=='auto':
        iv=np.asarray(a[:,0],float);valid_i=np.isfinite(iv)
        if valid_i.any():v=iv
        else:
            v=a[:,1:]@np.array([.2126,.7152,.0722]);v[~np.isfinite(a[:,1:]).all(1)]=np.nan
    elif channel=='rgb':
        v=a[:,1:]@np.array([.2126,.7152,.0722]); v[~np.isfinite(a[:,1:]).all(1)]=np.nan
    elif channel=='intensity': v=np.array(a[:,0],float)
    else: raise ValueError('Choose automatic, intensity or RGB.')
    return v


def fit_target(points, values, seed, diameter):
    """Fit a reviewable black/white four-quadrant survey target near ``seed``.

    Fit a robust plane and contrast transition, then require spatial evidence
    in all four quadrants. A partial pattern is not a confirmed printed marker.
    The caller must validate the fixed fitted frame against the original data.
    """
    p=np.asarray(points,float); v=np.asarray(values,float); seed=np.asarray(seed,float); d=float(diameter)
    finite=np.isfinite(v)&np.isfinite(p).all(1)
    radial=np.linalg.norm(p-seed,axis=1)
    keep=finite&(radial<d*1.15); p=p[keep]; v=v[keep]
    if len(p)<25: raise ValueError('Too few intensity/RGB points around this target. Zoom in, check the target size, or use the other display channel.')
    fit_input_points=len(p);fit_sampled=len(p)>30000
    if len(p)>30000:
        # keep a deterministic, spatially broad sample rather than the first points
        ix=np.linspace(0,len(p)-1,30000,dtype=int); p=p[ix]; v=v[ix]

    # Robust local plane: repeatedly trim points away from the dominant plane.
    mask=np.ones(len(p),bool); origin=np.median(p,axis=0); vt=None
    for _ in range(4):
        q=p[mask]
        if len(q)<25: break
        origin=np.median(q,axis=0)
        try: _,_,vt=np.linalg.svd(q-origin,full_matrices=False)
        except np.linalg.LinAlgError: raise ValueError('Could not estimate the local target plane. Pick closer to the target centre.')
        resid=(p-origin)@vt[2]
        med=float(np.median(resid[mask])); mad=float(np.median(np.abs(resid[mask]-med)))
        threshold=max(.0025,d*.045,3.5*1.4826*mad)
        new=np.abs(resid-med)<=threshold
        if new.sum()<25: break
        if np.array_equal(new,mask): mask=new; break
        mask=new
    if vt is None or mask.sum()<25:
        raise ValueError('Not enough points form a local planar target patch. Pick closer to the target centre or check the source scan density.')
    p=p[mask]; v=v[mask]; origin=np.mean(p,axis=0)
    _,_,vt=np.linalg.svd(p-origin,full_matrices=False)
    plane=(p-origin)@vt[2]; rms=float(np.sqrt(np.mean(plane**2)))
    quality_warnings=[]
    if rms>max(.005,d*.055):
        quality_warnings.append(f'Target plane RMS is {rms*1000:.1f} mm, above the suggested {max(.005,d*.055)*1000:.1f} mm. The fitted centre is included; inspect its residuals and delete it if unsuitable.')
    uv=(p-origin)@vt[:2].T

    # Robust contrast normalisation.  Works with signed/normalised E57 intensity.
    low,high=np.percentile(v,[5,95]); span=float(high-low)
    robust_scale=max(float(np.percentile(np.abs(v),90)),1e-9)
    if not np.isfinite(span) or span<=max(1e-12,robust_scale*1e-8):
        raise ValueError('No usable black/white contrast was found around this point. Try greyscale intensity, RGB, or check that the E57 contains intensity/colour.')
    val=np.clip((v-low)/span,0,1)*2-1

    seed_uv=(seed-origin)@vt[:2].T; best=None
    # Search wider than before so a click near an edge can still converge.
    for cx in seed_uv[0]+np.linspace(-d*.22,d*.22,7):
        for cy in seed_uv[1]+np.linspace(-d*.22,d*.22,7):
            xy=uv-[cx,cy]; inside=np.linalg.norm(xy,axis=1)<d*.56
            if inside.sum()<20: continue
            x,y=xy[inside].T; z=val[inside]
            for angle in np.linspace(0,np.pi/2,24,endpoint=False):
                u=x*np.cos(angle)+y*np.sin(angle); w=-x*np.sin(angle)+y*np.cos(angle)
                signs=np.sign(u*w); nz=signs!=0
                if nz.sum()<18: continue
                score=abs(float(np.mean(z[nz]*signs[nz])))
                # favour centres with observations on several quadrants
                quadrants=(u>=0).astype(int)*2+(w>=0).astype(int)
                counts=np.bincount(quadrants,minlength=4)
                coverage=(counts>1).sum()/4
                balance=(counts.min()/max(1,counts.max())) if coverage==1 else 0.0
                adjusted=score*(.50+.25*coverage+.25*np.sqrt(balance))
                if best is None or adjusted>best[0]: best=(adjusted,score,cx,cy,angle,1 if np.mean(z[nz]*signs[nz])>0 else -1)
    if best is None or best[1]<.30:
        raise ValueError('A flat patch was found, but its intensity/RGB pattern does not resemble a black-and-white survey target. Check target size/channel and pick closer to the centre.')
    _,_,cx,cy,angle,polarity=best
    use=np.linalg.norm(uv-[cx,cy],axis=1)<d*.56; xy=uv[use]; z=val[use]
    soft=max(d*.009,0.0004)
    def model(par):
        x,y=(xy-par[:2]).T; c,s=np.cos(par[2]),np.sin(par[2])
        return par[3]+par[4]*np.tanh((x*c+y*s)/soft)*np.tanh((-x*s+y*c)/soft)
    start=[cx,cy,angle,0,polarity*.85]
    try:
        result=least_squares(lambda par:model(par)-z,start,
            bounds=([cx-d*.10,cy-d*.10,angle-.35,-.6,-1.8],[cx+d*.10,cy+d*.10,angle+.35,.6,1.8]),
            loss='soft_l1',f_scale=.3,max_nfev=120)
        centre=result.x[:2]; angle=result.x[2]; polarity=np.sign(result.x[4]) or polarity
    except Exception:
        centre=np.array([cx,cy]); polarity=polarity
    xy=uv-centre; use=np.linalg.norm(xy,axis=1)<d*.56; x,y=xy[use].T; z=val[use]
    u=x*np.cos(angle)+y*np.sin(angle); w=-x*np.sin(angle)+y*np.cos(angle)
    signs=np.sign(u*w)*polarity; nz=signs!=0
    score=float(np.mean(z[nz]*signs[nz])) if nz.any() else 0.0
    quadrants=(u>=0).astype(int)*2+(w>=0).astype(int); counts=np.bincount(quadrants,minlength=4)
    observed=(counts>=2).sum(); balance=float(counts.min()/max(1,counts.max()))
    if score<.42 or observed<4 or balance<.08:
        raise ValueError('The local plane has contrast, but the checker pattern is not strong enough to accept as a survey target. Try the other intensity/RGB channel or adjust the target size.')
    centre3=origin+centre@vt[:2]
    frame={'centre':centre3.tolist(),'u':(np.cos(angle)*vt[0]+np.sin(angle)*vt[1]).tolist(),
           'w':(-np.sin(angle)*vt[0]+np.cos(angle)*vt[1]).tolist(),'normal':vt[2].tolist(),
           'diameter':d,'low':float(low),'high':float(high),'polarity':float(polarity),
           # The original-density pattern check must not turn a soft RMS
           # warning into a second, hidden rejection of the same noisy face.
           'plane_limit':max(.0025,d*.045,3.5*rms)}
    evidence=CheckerEvidence(frame);evidence.add(p,v)
    checked=require_checker(evidence)
    return {'source':centre3.tolist(),'normal':vt[2].tolist(),**checked,'_fit_frame':frame,
            'fit_input_points':fit_input_points,'fit_sampled':fit_sampled,'fit_point_limit':30000,
            'points':int(use.sum()),'diameter_m':d,'status':'accepted','usable':True,
            'quality':'warning' if quality_warnings else 'ok','quality_warnings':quality_warnings,
            'method':'Robust planar four-quadrant fit with spatial support and competing-edge checks. Pattern score is not a probability or centre uncertainty.'}



def _free_sphere_fit(points):
    """Algebraic sphere fit in local coordinates, used only as a diagnostic/seed."""
    p=np.asarray(points,float)
    if p.ndim!=2 or p.shape[1]!=3 or len(p)<4: raise ValueError('At least four points are required for a sphere fit.')
    origin=np.mean(p,axis=0);q=p-origin
    a=np.column_stack((2*q,np.ones(len(q))))
    b=np.einsum('ij,ij->i',q,q)
    x,_,rank,_=np.linalg.lstsq(a,b,rcond=None)
    if rank<4: raise ValueError('The selected points do not contain enough 3D curvature for a sphere.')
    radius2=float(np.dot(x[:3],x[:3])+x[3])
    if not np.isfinite(radius2) or radius2<=0: raise ValueError('The local sphere fit is unstable.')
    return origin+x[:3],math.sqrt(radius2)

def fit_sphere_target(points,seed,diameter):
    """Robust fixed-radius survey-sphere centre from a picked local neighbourhood.

    The nominal target diameter is treated as known.  A free-radius fit is reported
    only as a diagnostic and never silently changes the survey target size.
    """
    p=np.asarray(points,float);seed=np.asarray(seed,float);d=float(diameter);r=d/2
    if p.ndim!=2 or p.shape[1]!=3 or len(p)<40: raise ValueError('Not enough measured points near this sphere.')
    if seed.shape!=(3,) or not np.isfinite(seed).all(): raise ValueError('Enter a valid approximate sphere point.')
    if not .03<=d<=2: raise ValueError('Sphere diameter must be between 0.03 and 2 m.')
    p=p[np.isfinite(p).all(1)]
    if len(p)<40: raise ValueError('Not enough finite measured points near this sphere.')
    dist=np.linalg.norm(p-seed,axis=1);near=p[dist<=max(d*.42,.035)]
    starts=[]
    if len(near)>=20:
        local=near-np.mean(near,axis=0)
        _,_,vt=np.linalg.svd(local,full_matrices=False);normal=vt[-1]
        starts.extend([seed+r*normal,seed-r*normal])
    try:
        c0,r0=_free_sphere_fit(p)
        if np.isfinite(c0).all():starts.insert(0,c0)
    except ValueError:r0=float('nan')
    starts.extend([seed+[r,0,0],seed+[-r,0,0],seed+[0,r,0],seed+[0,-r,0],seed+[0,0,r],seed+[0,0,-r]])
    best=None
    scale=max(.0015,d*.012)
    for c0 in starts:
        try:
            res=least_squares(lambda c:np.linalg.norm(p-c,axis=1)-r,np.asarray(c0,float),loss='soft_l1',f_scale=scale,max_nfev=100)
        except Exception:continue
        residual=np.abs(np.linalg.norm(p-res.x,axis=1)-r)
        med=float(np.median(residual));mad=float(np.median(np.abs(residual-med)))
        threshold=min(max(.0015,d*.012,med+3.5*1.4826*mad),d*.06)
        inliers=residual<=threshold
        if inliers.sum()<30:continue
        score=(int(inliers.sum()),-float(np.median(residual[inliers])))
        if best is None or score>best[0]:best=(score,res.x,inliers,threshold)
    if best is None:raise ValueError('The picked neighbourhood does not support a sphere of the entered diameter. Pick closer to the sphere surface or check the diameter.')
    _,centre,inliers,threshold=best
    q=p[inliers]
    # Refit only the supported sphere points, keeping the nominal radius fixed.
    res=least_squares(lambda c:np.linalg.norm(q-c,axis=1)-r,centre,loss='soft_l1',f_scale=max(.001,d*.008),max_nfev=100)
    if not res.success or not np.isfinite(res.x).all():
        raise ValueError('The sphere centre fit did not converge to finite coordinates. Adjust the pick or sphere diameter.')
    centre=res.x;radial=np.linalg.norm(q-centre,axis=1);residual=radial-r
    rms=float(np.sqrt(np.mean(residual**2)));median=float(np.median(np.abs(residual)))
    try:free_c,free_r=_free_sphere_fit(q)
    except ValueError:free_c,free_r=centre,float('nan')
    directions=(q-centre)/np.maximum(radial[:,None],1e-12)
    # Direction covariance rejects a nearly planar/ring-shaped accidental fit.
    evals=np.linalg.eigvalsh(np.cov(directions.T));evals=np.sort(np.maximum(evals,0))
    coverage=float(evals[0]/max(evals[-1],1e-12))
    octants=((directions[:,0]>=0).astype(int)*4+(directions[:,1]>=0).astype(int)*2+(directions[:,2]>=0).astype(int))
    octant_count=int(np.count_nonzero(np.bincount(octants,minlength=8)>=2))
    # Plane comparison uses the same inliers.  A real sphere patch should show
    # measurable curvature rather than merely fitting a flat wall.
    origin=q.mean(0);_,_,vt=np.linalg.svd(q-origin,full_matrices=False);plane=np.abs((q-origin)@vt[-1]);plane_rms=float(np.sqrt(np.mean(plane**2)))
    radius_error=abs(free_r-r) if np.isfinite(free_r) else float('inf')
    quality_warnings=[]
    if rms>max(.004,d*.025):
        quality_warnings.append(f'Sphere RMS is {rms*1000:.1f} mm, above the suggested {max(.004,d*.025)*1000:.1f} mm. The fitted centre is included; inspect its residuals and delete it if unsuitable.')
    if np.isfinite(free_r) and radius_error>max(.012,d*.12):
        quality_warnings.append(f'Free-radius curvature suggests a {free_r*2000:.1f} mm diameter; the centre was fitted using your entered {d*1000:.1f} mm diameter.')
    if octant_count<3 or plane_rms<max(rms*1.25,.0015):raise ValueError('The selected points look too flat or too narrowly observed for a reliable sphere centre.')
    support=float(len(q)/len(p));shape=max(0,min(1,plane_rms/max(plane_rms+rms,1e-12)));score=float(max(0,min(1,support*.55+shape*.30+min(octant_count/6,1)*.15)))
    return {'source':centre.tolist(),'target_type':'sphere','diameter_m':d,'nominal_radius_m':r,'fitted_radius_m':float(free_r) if np.isfinite(free_r) else None,
            'radius_error_mm':float(radius_error*1000) if np.isfinite(radius_error) else None,'sphere_rms_mm':rms*1000,'sphere_median_mm':median*1000,
            'plane_rms_mm':plane_rms*1000,'support_points':int(len(q)),'neighbourhood_points':int(len(p)),'direction_octants':octant_count,'direction_coverage':coverage,
            'score':score,'status':'accepted','usable':True,'quality':'warning' if quality_warnings else 'ok',
            'quality_warnings':quality_warnings,'method':'Known-diameter robust sphere fit. Nominal diameter is fixed; free-radius result is diagnostic only.'}

def detect_sphere(meta,diameter,seed,progress=lambda s:None):
    if seed is None:raise ValueError('Sphere fitting is assisted: pick near the sphere in the main 3D view, then fit the known diameter.')
    from preview_index import selection_blocks
    d=float(diameter);p,_=pointio.arrays(meta);mask=np.memmap(meta['mask_file'],'u1','r',shape=(len(p),)) if meta.get('mask_file') else None
    sample=PatchSample(90000);centre=np.asarray(seed,float);radius=d*1.35
    # The persistent scan index is conservative only: it selects original-row
    # blocks whose AABB intersects the local target box. Every original point
    # is still checked against the exact spherical neighbourhood below.
    box={'mode':'box','minimum':centre-radius,'maximum':centre+radius,'invert':False}
    blocks=list(selection_blocks(meta,box,progress))
    visited=0
    for start,end in blocks:
        q=np.asarray(p[start:end]);keep=np.isfinite(q).all(1)
        if mask is not None:keep &= mask[start:end]==0
        ids=np.flatnonzero(keep)
        if len(ids):
            qq=q[ids];inside=np.linalg.norm(qq-centre,axis=1)<=radius
            if inside.any():
                chosen=ids[inside];sample.add(q[chosen],np.zeros((len(chosen),4),np.float32),start+chosen)
        visited += len(q);progress(f'Reading indexed sphere neighbourhood: {visited:,} candidate points')
    if mask is not None:mask._mmap.close()
    if not sample.available:raise ValueError('No retained scan points near that sphere pick.')
    result=fit_sphere_target(sample.points,seed,d);result.update(id='T001',available_patch_points=sample.available,gathered_patch_points=len(sample.points),patch_truncated=sample.available>len(sample.points))
    return {'candidates':[result],'searched_points':len(p),'candidate_points_read':visited,'candidate_blocks':len(blocks),'target_type':'sphere','diameter_m':d,'sampled':sample.available>len(sample.points),
            'message':f'Known-diameter sphere fit used {len(sample.points):,} local measured points ({sample.available:,} available). The centre is included for registration; its residuals remain available for inspection.'}

def _visit_neighbourhoods(p, a, centres, radii, visit, progress, label, mask=None):
    """Visit independent overlapping balls. No nearest-seed ownership."""
    centres=np.asarray(centres,float);radii=np.broadcast_to(radii,(len(centres),))
    for start in range(0,len(p),pointio.CHUNK):
        q=np.asarray(p[start:start+pointio.CHUNK]);attrs=np.asarray(a[start:start+len(q)])
        keep=np.isfinite(q).all(1)
        if mask is not None:keep &= mask[start:start+len(q)]==0
        good=np.flatnonzero(keep)
        if not len(good):continue
        q=q[good];attrs=attrs[good];tree=cKDTree(q)
        lo,hi=q.min(0),q.max(0)
        eligible=np.linalg.norm(np.maximum(np.maximum(lo-centres,centres-hi),0),axis=1)<=radii
        for ci in np.flatnonzero(eligible):
            ix=np.asarray(tree.query_ball_point(centres[ci],float(radii[ci]),return_sorted=True),dtype=np.int64)
            if len(ix):visit(int(ci),q[ix],attrs[ix],start+good[ix])
        progress(f'{label}: {min(start+pointio.CHUNK,len(p)):,} / {len(p):,} source points')


def _validate_candidates(p,a,candidates,progress,mask=None):
    if not candidates:return []
    from target_thumbnail import TargetThumbnail
    evidence=[CheckerEvidence(c['_fit_frame']) for c in candidates]
    thumbnails=[TargetThumbnail(c['_fit_frame']) for c in candidates]
    def add(j,pts,attrs,ids):
        values=luminance(attrs,candidates[j]['channel'])
        evidence[j].add(pts,values)
        thumbnails[j].add(pts,values)
    _visit_neighbourhoods(p,a,[c['source'] for c in candidates],
                         [c['diameter_m']*.5 for c in candidates],add,progress,
                         'Checking fitted patterns against original-density neighbourhoods',mask)
    accepted=[]
    for candidate,ev,thumbnail in zip(candidates,evidence,thumbnails):
        candidate.pop('_fit_frame',None)
        try:checked=require_checker(ev)
        except ValueError:continue
        candidate.update(checked,validation_full_density=True,thumbnail=thumbnail.result())
        accepted.append(candidate)
    return accepted


def detect(meta,diameter=None,channel='auto',seed=None,progress=lambda s:None,target_type='bw_auto'):
    if target_type=='sphere':
        return detect_sphere(meta,diameter,seed,progress)
    if target_type not in ('bw_auto','bw_known'):
        raise ValueError('Choose Sphere, B&W known size, or B&W automatic target fitting.')
    if target_type=='bw_auto' or diameter is None or diameter=='auto':
        return detect_automatic(meta,channel,seed,progress)
    d=float(diameter)
    if not .03<=d<=2:raise ValueError('Target width / diameter must be between 0.03 and 2 m.')
    channels=['intensity','rgb'] if channel=='auto' else [channel]
    if any(ch not in ('intensity','rgb') for ch in channels):raise ValueError('Choose automatic, intensity or RGB.')
    p,a=pointio.arrays(meta)
    if a is None:luminance(None)
    if seed is not None:
        seed=np.asarray(seed,float)
        if seed.shape!=(3,) or not np.isfinite(seed).all():raise ValueError('Enter a valid approximate target centre.')
        sample=PatchSample(35000)
        _visit_neighbourhoods(p,a,[seed],d*1.15,lambda j,q,attrs,ids:sample.add(q,attrs,ids),
                             progress,'Reading picked target neighbourhood')
        if not sample.available:raise ValueError('No scan points near that coordinate.')
        fitted=[];last_error='No usable intensity or RGB.'
        for ch in channels:
            try:
                c=fit_target(sample.points,luminance(sample.attributes,ch),seed,d)
                c.update(channel=ch,available_patch_points=sample.available,
                         gathered_patch_points=len(sample.points),patch_truncated=sample.available>len(sample.points))
                fitted.append(c)
            except ValueError as err:last_error=str(err)
        accepted=_validate_candidates(p,a,fitted,progress)
        if not accepted:
            raise ValueError(last_error+' No centre passed all four-quadrant checks on the original data. No arbitrary point was substituted.')
        candidate=max(accepted,key=lambda c:c['score']);candidate['id']='T001'
        return {'candidates':[candidate],'sampled':bool(candidate.get('fit_sampled') or candidate['patch_truncated']),
                'searched_points':len(p),'patch_point_limit':35000,'fit_point_limit':30000,
                'message':'Picked target fit uses a bounded sample; the fixed pattern is checked against every finite original point in its validation neighbourhood. The fitted centre is included for registration with its quality measurements.'}

    raw_candidates=[];cell_size=max(d*.42,.01);nomination_sampled=False;nomination_points=0
    for start in range(0,len(p),pointio.CHUNK):
        q=np.asarray(p[start:start+pointio.CHUNK]);attrs=np.asarray(a[start:start+len(q)])
        local_step=max(1,int(math.ceil(len(q)/60000)));qs=q[::local_step];aa=attrs[::local_step]
        nomination_sampled |= local_step>1;nomination_points+=len(qs)
        for ch in channels:
            vals=luminance(aa,ch)
            finite=np.isfinite(vals)&np.isfinite(qs).all(1);qq=qs[finite];vv=vals[finite]
            if len(qq)<20:continue
            lo,hi=np.percentile(vv,[3,97]);span=float(hi-lo)
            if not np.isfinite(span) or span<=1e-12:continue
            nv=np.clip((vv-lo)/span,0,1)
            cells=np.floor(qq/cell_size).astype('i8');_,inv=np.unique(cells,axis=0,return_inverse=True);counts=np.bincount(inv)
            mean=np.bincount(inv,weights=nv)/np.maximum(counts,1);variance=np.bincount(inv,weights=nv**2)/np.maximum(counts,1)-mean**2
            centres=np.column_stack([np.bincount(inv,weights=qq[:,k])/np.maximum(counts,1) for k in range(3)])
            cand=np.flatnonzero((counts>=10)&(variance>=.025));cand=cand[np.argsort(variance[cand])[::-1]][:28]
            for cell in cand:raw_candidates.append((float(variance[cell]),centres[cell].copy(),ch))
        progress(f'Searching target contrast: {min(start+pointio.CHUNK,len(p)):,} / {len(p):,} points · {len(raw_candidates):,} textured patches')
    if not raw_candidates:
        return {'candidates':[],'sampled':nomination_sampled,'whole_scan':True,'searched_points':len(p),
                'nomination_points':nomination_points,'channels':channels,
                'message':'No contrast patches were nominated. Nomination is sampled and can miss targets; try Pick & fit centre, the other channel, or check the printed size.'}
    raw_candidates.sort(key=lambda x:x[0],reverse=True)
    nominations=[]
    for score,centre,ch in raw_candidates:
        if any(np.linalg.norm(centre-old[1])<d*.48 for old in nominations):continue
        nominations.append((score,centre,ch))
        if len(nominations)>=500:break
    # Keep total working memory bounded without favouring the first scan rows.
    patch_limit=min(35000,max(2000,2000000//len(nominations)))
    samples=[PatchSample(patch_limit) for _ in nominations]
    _visit_neighbourhoods(p,a,[x[1] for x in nominations],d*1.15,
                         lambda j,q,attrs,ids:samples[j].add(q,attrs,ids),progress,
                         'Reading overlapping target neighbourhoods')
    fitted=[]
    for j,(nom,sample) in enumerate(zip(nominations,samples)):
        if j%5==0:progress(f'Fitting four-quadrant patterns: {j+1}/{len(nominations)} patches')
        if not sample.available:continue
        for ch in [nom[2]]+[ch for ch in channels if ch!=nom[2]]:
            try:c=fit_target(sample.points,luminance(sample.attributes,ch),nom[1],d)
            except ValueError:continue
            c.update(channel=ch,available_patch_points=sample.available,gathered_patch_points=len(sample.points),
                     patch_truncated=sample.available>len(sample.points))
            fitted.append(c)
    tested=len(fitted);accepted=_validate_candidates(p,a,fitted,progress)
    found=[]
    for candidate in sorted(accepted,key=lambda c:c['score'],reverse=True):
        if any(np.linalg.norm(np.asarray(candidate['source'])-old['source'])<d*.6 for old in found):continue
        candidate.update(id=f'T{len(found)+1:03}',status='accepted');found.append(candidate)
        if len(found)>=100:break
    return {'candidates':found,'sampled':bool(nomination_sampled or any(s.available>len(s.points) for s in samples)
                                            or any(c.get('fit_sampled') for c in found)),
            'whole_scan':True,'searched_points':len(p),'nomination_points':nomination_points,'channels':channels,
            'nominated_patches':len(nominations),'nomination_limit':500,'patch_point_limit':patch_limit,
            'fit_point_limit':30000,'full_density_patterns_checked':tested,
            'neighbourhood_assignment':'Independent overlapping balls; deterministic priority sampling for fitting',
            'message':f'Traversed {len(p):,} source points; nominated {len(nominations)} patches from {nomination_points:,} sampled points. Up to {patch_limit:,} points gathered per fit, at most 30,000 fitted. Checked {tested} fitted patterns against their original-density neighbourhoods; {len(found)} target centres are included. Nomination can still miss targets. Display detail does not control this search.'}


def detect_automatic(meta,channel='auto',seed=None,progress=lambda s:None):
    from target_search import SCALES, nominate, quick_pattern
    channels=['intensity','rgb'] if channel=='auto' else [channel]
    if any(ch not in ('intensity','rgb') for ch in channels):raise ValueError('Choose automatic, intensity or RGB.')
    p,a=pointio.arrays(meta)
    if a is None:luminance(None)
    mask=np.memmap(meta['mask_file'],'u1','r',shape=(len(p),)) if meta.get('mask_file') else None
    if seed is not None:
        seed=np.asarray(seed,float)
        if seed.shape!=(3,) or not np.isfinite(seed).all():raise ValueError('Enter a valid approximate centre.')
        nominations=[(1.,seed,ch,d,None) for d in SCALES for ch in channels]
        searched=len(p);limited=False
    else:
        nominations,searched,limited=nominate(p,a,channels,luminance,pointio.CHUNK,progress,mask)
    if not nominations:
        return {'candidates':[],'automatic_scale':True,'searched_points':searched,'channels':channels,
                'message':'No usable intensity / colour neighbourhoods were found. Check that this is the correct scan and inspect it in Intensity view. No target size is required.'}
    # Group identical spatial windows across channels, keep overlapping balls.
    unique=[];lookup={}
    for _,centre,ch,d,_region in nominations:
        key=(*centre,d)
        if key not in lookup:lookup[key]=len(unique);unique.append((centre,d))
    limit=min(35000,max(2500,2400000//len(unique)))
    samples=[PatchSample(limit) for _ in unique]
    _visit_neighbourhoods(p,a,[x[0] for x in unique],[x[1]*1.15 for x in unique],
        lambda j,q,attrs,ids:samples[j].add(q,attrs,ids),progress,'Reading original target neighbourhoods',mask)
    fitted=[];screened=0
    for i,(_,centre,ch,d,_region) in enumerate(nominations):
        if i%5==0:progress(f'Finding black-and-white crossings: {i+1} / {len(nominations)} local patterns · {len(fitted)} fits')
        sample=samples[lookup[(*centre,d)]]
        if sample.available<25:continue
        values=luminance(sample.attributes,ch)
        proposal=quick_pattern(sample.points,values,centre,d)
        if proposal is None:continue
        screened+=1
        try:c=fit_target(sample.points,values,proposal[1],d)
        except ValueError:continue
        c.update(channel=ch,automatic_scale=True,support_scale_m=d,
                 available_patch_points=sample.available,gathered_patch_points=len(sample.points),
                 patch_truncated=sample.available>len(sample.points))
        # Keep independent scales so a weak local crossing can be corroborated.
        same=next((old for old in fitted if old['diameter_m']==d and old['channel']==ch and np.linalg.norm(np.asarray(c['source'])-old['source']) < d*.25),None)
        if same:
            if c['score']*np.sqrt(c['validation_points']) > same['score']*np.sqrt(same['validation_points']):fitted.remove(same)
            else:continue
        fitted.append(c)
    accepted=_validate_candidates(p,a,fitted,progress,mask)
    found=[]
    for c in sorted(accepted,key=lambda c:c['score'],reverse=True):
        stable=any(old is not c and 1.2<=max(c['diameter_m'],old['diameter_m'])/min(c['diameter_m'],old['diameter_m'])<=3
                   and np.linalg.norm(np.asarray(c['source'])-old['source'])<min(c['diameter_m'],old['diameter_m'])*.12
                   and abs(np.dot(c['normal'],old['normal']))>.95 for old in accepted)
        # A weak checker at just one tiny scale is often a seam or texture.
        # Clear four-quadrant targets can pass at one scale; weaker patterns
        # need a consistent centre at another independently validated scale.
        if not stable and c['score']<.75:continue
        c['scale_corroborated']=stable
        if any(np.linalg.norm(np.asarray(c['source'])-old['source']) < min(c['diameter_m'],old['diameter_m'])*.4 for old in found):continue
        c.update(id=f'T{len(found)+1:03}',status='accepted');found.append(c)
    message=(f'{len(found)} target centre(s) included. Automatic scales; no printed size required. '
             f'Visited {searched:,} original points; tested {len(nominations)} local patterns with adaptive contrast. '
             'Every accepted crossing was checked on original-density points. Inspect the marked centres.')
    if limited:message+=' The local-pattern budget was reached; use Pick & fit centre for missed areas.'
    if not found:message+=' Try Pick & fit centre in a dense intensity view; no arbitrary centre has been substituted.'
    return {'candidates':found,'automatic_scale':True,'searched_points':searched,'nomination_points':searched,
            'nomination_sampled':False,'nominated_patches':len(nominations),'nomination_limit':900,
            'search_limited':limited,'channels':channels,'support_scales_m':list(SCALES),
            'patch_point_limit':limit,'fit_point_limit':30000,'screened_patterns':screened,
            'sampled':any(s.available>len(s.points) for s in samples),'message':message}



def preview(meta,solution=None,channel='intensity'):
    p,a=pointio.arrays(meta); stride=max(1,math.ceil(len(p)/80000)); pts=np.array(p[::stride])
    if solution: pts=transform(pts,solution)
    origin=(pts.min(0)+pts.max(0))/2; value=np.full(len(pts),.6)
    if a is not None:
        v=luminance(a[::stride],channel); valid=np.isfinite(v)
        if valid.any():
            low,high=np.percentile(v[valid],[2,98]); value[valid]=.12+.88*np.clip((v[valid]-low)/max(high-low,1e-9),0,1)
    return {'origin':origin.tolist(),'points':np.column_stack((pts-origin,value)).tolist(),'count':len(p),'stride':stride}
