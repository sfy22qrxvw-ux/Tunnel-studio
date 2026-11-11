# Copyright (c) 2026 Damian Gulczynski.
"""Review radial departures from overlapping local circles along a curved route.

Five-metre fitting windows are the default. Each observation is projected onto
a three-dimensional centreline and expressed in its local perpendicular frame;
the fit never flattens a curved tunnel onto one fixed axis. Robust, spatially
balanced circle fits are blended between overlapping windows. Unsupported or
poorly circular windows remain unselected. Calibration uses bounded disk-backed
window samples; classification streams every retained original point in 100k
blocks with 64-bit identities. Neither source coordinates nor attributes change.

The reference is a fitted circular model, not a design check, survey control,
semantic service classifier, or reconstruction of missing lining. Real lining
that departs from a circle can be a candidate and must be reviewed.
"""
from pathlib import Path
import math,sqlite3,time
import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import least_squares
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
import compute_runtime,shape_matching,scan_geometry,tunnel_lining
from combined_denoising import sample_originals

DEFAULTS=dict(section_m=5.,inward_mm=20.,outward_mm=20.,axis_source='auto',respect_view=False)
WINDOW_SAMPLE=12000


def settings(raw=None):
    if raw is None:raw={}
    if not isinstance(raw,dict) or set(raw)-set(DEFAULTS):raise ValueError('Unknown local-circle setting.')
    out={**DEFAULTS,**raw}
    for name,lo,hi in [('section_m',.5,50),('inward_mm',1,2000),('outward_mm',1,2000)]:
        try:value=float(out[name])
        except (TypeError,ValueError):raise ValueError('Enter a finite number for '+name+'.')
        if not math.isfinite(value) or not lo<=value<=hi:raise ValueError(f'{name} must be between {lo:g} and {hi:g}.')
        out[name]=value
    if out['axis_source'] not in ('auto','project'):raise ValueError('Choose the scan-derived centreline or the loaded project alignment.')
    if type(out['respect_view']) is not bool:raise ValueError('View-filter restriction must be true or false.')
    return out


def emit(progress,cancel,message):
    if cancel():raise ValueError('Local-circle cleaning stopped. Source points and the previous review are unchanged.')
    progress(message)


class Curve:
    """Smooth 3D projection with a rotation-minimising perpendicular frame."""
    def __init__(self,centres,stations=None):
        p=np.asarray(centres,float)
        if p.ndim!=2 or p.shape[1]!=3 or len(p)<2 or not np.isfinite(p).all():raise ValueError('The centreline needs at least two finite 3D nodes.')
        step=np.linalg.norm(np.diff(p,axis=0),axis=1)
        if np.any(step<1e-7):raise ValueError('The centreline contains repeated consecutive nodes.')
        self.s=np.r_[0,np.cumsum(step)] if stations is None else np.asarray(stations,float)
        if self.s.shape!=(len(p),) or not np.isfinite(self.s).all() or np.any(np.diff(self.s)<=0):raise ValueError('Centreline stations must increase.')
        self.curve=CubicSpline(self.s,p,axis=0,bc_type='natural')
        self.grid=np.linspace(self.s[0],self.s[-1],max(2,int(math.ceil((self.s[-1]-self.s[0])/.25))+1))
        xyz=self.curve(self.grid);tangent=self.curve(self.grid,1);speed=np.linalg.norm(tangent,axis=1)
        if np.any(speed<.1):raise ValueError('The centreline folds or stops. Review its nodes before circle cleaning.')
        tangent/=speed[:,None];rights=np.empty_like(tangent)
        ref=np.eye(3)[np.argmin(abs(tangent[0]))];rights[0]=np.cross(ref,tangent[0]);rights[0]/=np.linalg.norm(rights[0])
        before,after=tangent[:-1],tangent[1:]
        dot=np.einsum('ij,ij->i',before,after)
        if (dot<.5).any():raise ValueError('The centreline has an abrupt turn. Use a reviewed alignment or one tunnel branch.')
        # The quaternion (a × b, 1 + a · b) represents the same minimum
        # rotation from a to b. Build all step rotations before transporting.
        rotations=Rotation.from_quat(np.c_[np.cross(before,after),1+dot]).as_matrix()
        for i,(rotation,b) in enumerate(zip(rotations,after),1):
            r=rotation@rights[i-1]
            r-=b*(r@b);rights[i]=r/np.linalg.norm(r)
        self.rights=rights;self.tree=cKDTree(xyz)

    def at(self,s):
        s=np.asarray(s,float);centre=self.curve(s);normal=self.curve(s,1);normal/=np.linalg.norm(normal,axis=-1,keepdims=True)
        right=np.stack([np.interp(s,self.grid,self.rights[:,j]) for j in range(3)],axis=-1)
        right-=normal*np.sum(right*normal,axis=-1,keepdims=True);right/=np.linalg.norm(right,axis=-1,keepdims=True)
        up=np.cross(right,normal)
        return centre,normal,right,up

    def project(self,p):
        p=np.asarray(p,float);_,ix=self.tree.query(p,workers=compute_runtime.workers());s=self.grid[ix].copy()
        for _ in range(4):
            c=self.curve(s);d=self.curve(s,1);dd=self.curve(s,2);delta=c-p
            denominator=np.sum(d*d,axis=1)+np.sum(delta*dd,axis=1)
            move=np.sum(delta*d,axis=1)/np.maximum(denominator,.05)
            s=np.clip(s-np.clip(move,-.5,.5),self.s[0],self.s[-1])
        c,n,r,u=self.at(s);delta=p-c
        valid=~(((s<=self.s[0]+1e-8)&(np.sum(delta*n,axis=1)<-.001))|((s>=self.s[-1]-1e-8)&(np.sum(delta*n,axis=1)>.001)))
        return s,np.c_[np.sum(delta*r,axis=1),np.sum(delta*u,axis=1)],valid


class Samples:
    """Up to 12k actual, spatially balanced observations per distance bin."""
    def __init__(self,path,length):
        self.path=Path(path);self.length=length;self.db=sqlite3.connect(path);self.cut={}
        self.db.execute('PRAGMA cache_size=-32768');self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('CREATE TABLE p(b INTEGER,k INTEGER,a INTEGER,c INTEGER,h INTEGER,x REAL,y REAL,z REAL,PRIMARY KEY(b,k,a,c)) WITHOUT ROWID')
        self.db.execute('CREATE INDEX ranks ON p(b,h,k,a,c)')

    def add(self,p,s,uv):
        bins=np.floor(s/self.length).astype(np.int64)
        keys=np.c_[bins,np.floor(s/.20).astype(np.int64),np.floor(uv/.025).astype(np.int64)]
        # Choose an actual coordinate, never a voxel-centre replacement.
        order=np.lexsort((p[:,2],p[:,1],p[:,0],keys[:,3],keys[:,2],keys[:,1],keys[:,0]));keys=keys[order];p=p[order]
        use=np.r_[True,np.any(keys[1:]!=keys[:-1],axis=1)];keys=keys[use];p=p[use]
        h=keys.astype(np.uint64);rank=h[:,1]*np.uint64(73856093)^h[:,2]*np.uint64(19349663)^h[:,3]*np.uint64(83492791)
        rank^=rank>>np.uint64(30);rank*=np.uint64(0xbf58476d1ce4e5b9);rank^=rank>>np.uint64(27);rank*=np.uint64(0x94d049bb133111eb);rank^=rank>>np.uint64(31)
        rank=(rank&np.uint64(2**63-1)).astype(np.int64)
        for b in np.unique(keys[:,0]):
            take=(keys[:,0]==b)&(rank<=self.cut.get(int(b),2**63-1));k=keys[take];q=p[take];h=rank[take]
            self.db.executemany('''INSERT INTO p VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(b,k,a,c) DO UPDATE SET
                x=excluded.x,y=excluded.y,z=excluded.z WHERE (excluded.x,excluded.y,excluded.z)<(x,y,z)''',
                ((int(v[0]),int(v[1]),int(v[2]),int(v[3]),int(r),float(x[0]),float(x[1]),float(x[2])) for v,r,x in zip(k,h,q)))
            row=self.db.execute('SELECT h,k,a,c FROM p WHERE b=? ORDER BY h,k,a,c LIMIT 1 OFFSET ?',(int(b),WINDOW_SAMPLE-1)).fetchone()
            if row:
                self.cut[int(b)]=row[0]
                self.db.execute('DELETE FROM p WHERE b=? AND (h,k,a,c)>(?,?,?,?)',(int(b),*row))
        self.db.commit()

    def projected_window(self,station,length,guide,projected):
        first=int(math.floor(station/self.length))-2
        for b in tuple(projected):
            if b<first:del projected[b]
        window=[]
        for b in range(first,first+5):
            if b not in projected:
                rows=self.db.execute('SELECT x,y,z FROM p WHERE b=? ORDER BY k,a,c',(b,)).fetchall()
                if rows:
                    s,uv,valid=guide.project(np.asarray(rows,float))
                    projected[b]=(s[valid],uv[valid])
                else:
                    projected[b]=(np.empty(0),np.empty((0,2)))
            s,uv=projected[b]
            window.append(uv[abs(s-station)<=length/2+1e-9])
        return np.concatenate(window)

    def close(self):self.db.close()


def circle(q,radius_hint,band,rng):
    if len(q)<80:return None
    _,ix=np.unique(np.floor(q/.015).astype(np.int64),axis=0,return_index=True);q=q[np.sort(ix)]
    if len(q)<60:return None
    try:(centre,radius,_,_),_=tunnel_lining.circle_seed(q,.04,rng)
    except ValueError:return None
    if radius_hint and not .65*radius_hint<radius<1.45*radius_hint:return None
    shell=abs(np.linalg.norm(q-centre,axis=1)-radius)<.04
    if shell.sum()<60:return None
    for _ in range(3):
        used=q[shell];angle=np.mod(np.arctan2(used[:,1]-centre[1],used[:,0]-centre[0]),2*np.pi)
        bins=np.minimum(71,(angle*72/(2*np.pi)).astype(int));balanced=np.array([np.median(used[bins==b],axis=0) for b in np.unique(bins)])
        if len(balanced)<36:return None
        def residual(x):return np.linalg.norm(balanced-x[:2],axis=1)-x[2]
        fit=least_squares(residual,[*centre,radius],loss='soft_l1',f_scale=.003,max_nfev=60)
        if not fit.success:return None
        centre=fit.x[:2];radius=float(fit.x[2]);errors=np.linalg.norm(q-centre,axis=1)-radius
        med=np.median(errors[shell]);mad=1.4826*np.median(abs(errors[shell]-med))
        shell=abs(errors)<=max(.004,min(.03,3.5*mad))
        if shell.sum()<60:return None
    used=q[shell];errors=np.linalg.norm(used-centre,axis=1)-radius
    bins=np.minimum(71,(np.mod(np.arctan2(used[:,1]-centre[1],used[:,0]-centre[0]),2*np.pi)*72/(2*np.pi)).astype(int))
    rms=float(np.sqrt(np.mean(errors**2)));coverage=len(np.unique(bins))*5.
    supported=coverage>=240 and rms<=band*.35 and shell.mean()>=.45
    reason='Supported circular lining' if supported else 'Too little circular coverage' if coverage<240 else 'Circle residual is too large for the chosen keep band' if rms>band*.35 else 'Most measured shape does not support this circle'
    return dict(offset=centre,radius=radius,rms_mm=rms*1000,coverage_deg=coverage,support_points=int(shell.sum()),calibration_points=len(q),inlier_fraction=float(shell.mean()),supported=bool(supported),reason=reason)


def merge_stations(base,extra):
    # A fit station and a guide node can differ by floating-point roundoff.
    # Keep the original knot when both describe the same physical location.
    right=np.clip(np.searchsorted(base,extra),1,len(base)-1)
    clear=(abs(extra-base[right-1])>1e-6)&(abs(extra-base[right])>1e-6)
    return np.sort(np.r_[base,extra[clear]])


def fit(samples,guide,stations,cfg,progress,cancel,radius_hint):
    rng=np.random.default_rng(215);length=cfg['section_m'];band=min(cfg['inward_mm'],cfg['outward_mm'])/1000
    quality=[]
    # Refinement affects the reference only, never the measured coordinates.
    for iteration in range(4):
        quality=[];new=[];usable=[];projected={}
        working=stations if iteration==3 else merge_stations(stations,np.arange(stations[0],stations[-1],min(1.,length/2)))
        support_length=length if iteration==3 else min(1.,length)
        for i,station in enumerate(working):
            emit(progress,cancel,f'Fitting local circles · pass {iteration+1}/4 · window {i+1}/{len(working)}')
            c,n,r,u=guide.at(station)
            uv=samples.projected_window(station,support_length,guide,projected)
            info=circle(uv,radius_hint,band,rng)
            if info is None:info=dict(offset=np.zeros(2),radius=radius_hint,rms_mm=None,coverage_deg=0.,support_points=0,calibration_points=0,inlier_fraction=0.,supported=False,reason='Insufficient circular wall evidence')
            info['station_m']=float(station);quality.append(info)
            seed_ok=info['coverage_deg']>=200 and info['inlier_fraction']>=.45 and info['rms_mm'] is not None and info['rms_mm']<40
            shift=info['offset']
            if iteration<3 and len(uv)>=120 and (not seed_ok or info['rms_mm']>2 or info['coverage_deg']<280):
                # Before the direction has converged, an oblique circular
                # tube cuts an ellipse. Its robust bilateral midpoint can
                # guide the centre without claiming a circular fit. Missing
                # half-walls fail the bilateral-width requirement.
                lo,hi=np.quantile(uv,[.02,.98],axis=0);width=hi-lo
                if width.min()>1.4*radius_hint and width.max()<3.5*radius_hint:
                    shift=(lo+hi)/2;seed_ok=True
            new.append(c+shift[0]*r+shift[1]*u if seed_ok else c);usable.append(seed_ok)
        if iteration<3 and sum(usable)>=2:
            # Missing fitted centres remain on the guide and are not promoted
            # to evidence. Final support still requires neighbouring good fits.
            # Preserve the guide's finer bend geometry. Replacing it with
            # widely spaced fit centres would flatten the first/last bend and
            # create false millimetre departures near a window boundary.
            knots=merge_stations(guide.s,working);old=guide.at(knots)[0]
            delta=np.asarray(new)-guide.at(working)[0]
            # A local fit has no evidence for extrapolating a displacement
            # along unmeasured alignment. Hold the end corrections constant.
            local=np.clip(knots,working[0],working[-1])
            correction=CubicSpline(working,delta,axis=0,bc_type='natural')(local)
            guide=Curve(old+correction,knots)
    offsets=np.array([q['offset'] for q in quality]);radii=np.array([q['radius'] for q in quality]);supported=np.array([q['supported'] for q in quality],bool)
    for q in quality:
        c,n,r,u=guide.at(q['station_m']);q.update(centre=(c+q['offset'][0]*r+q['offset'][1]*u).tolist(),normal=n.tolist(),right=r.tolist(),up=u.tolist(),radius_m=q['radius'])
        q['offset']=q['offset'].tolist()
    return dict(guide=guide,stations=np.asarray(stations),offsets=offsets,radii=radii,supported=supported,sections=quality)


def classify(p,model,cfg):
    s,uv,inside=model['guide'].project(p);stations=model['stations']
    k=np.clip(np.searchsorted(stations,s,side='right')-1,0,len(stations)-2)
    supported=inside&(s>=stations[0])&(s<=stations[-1])&model['supported'][k]&model['supported'][k+1]
    offset=np.c_[np.interp(s,stations,model['offsets'][:,0]),np.interp(s,stations,model['offsets'][:,1])]
    radius=np.interp(s,stations,model['radii']);d=np.linalg.norm(uv-offset,axis=1)-radius
    labels=np.zeros(len(p),'<i4');labels[d < -cfg['inward_mm']/1000]=1;labels[d > cfg['outward_mm']/1000]=2;labels[~supported]=3
    return labels,s,d


def run(batches,folder,cfg,progress=lambda _:None,cancel=lambda:False,axis_rows=None,prepared_guide=None):
    cfg=settings(cfg);folder=Path(folder);started=time.monotonic();source=lambda:shape_matching.pieces(batches)
    guide=prepared_guide;hint=None
    if guide is None and cfg['axis_source']=='project':
        a=np.asarray(axis_rows,float)
        if a.ndim!=2 or a.shape[1]<4 or len(a)<2:raise ValueError('Load a project alignment, or choose Scan-derived centreline.')
        guide=Curve(a[:,1:4]);hint=float(np.median(a[:,4])) if a.shape[1]>4 else None
    bounds=np.array([[np.inf]*3,[-np.inf]*3])
    def measure_area():
        for part in source():
            p=part[1]
            bounds[0]=np.minimum(bounds[0],p.min(axis=0))
            bounds[1]=np.maximum(bounds[1],p.max(axis=0))
            yield part
    emit(progress,cancel,'1/3 Preparing the processing area for local circles')
    sample,total=sample_originals(measure_area,.04,180000,lambda _:None,cancel)
    if total<100:raise ValueError(f'Only {total:,} retained points are in the processing area. Include at least 100 measured points on the tunnel lining.')
    emit(progress,cancel,f'2/3 Fitting the lining · {total:,} retained points in area')
    if guide is None:
        estimate=scan_geometry.estimate({'name':'Selected registered cloud'},None,interval=min(1.,cfg['section_m']/2),smoothing=1,
            progress=lambda m:emit(progress,cancel,'2/3 '+m),prepared=(sample,total))
        a=np.asarray(estimate['rows']);p=a[:,1:4];hint=float(np.median(a[:,4]))
        # Extrapolation is only an initial guide; unsupported end fits stay
        # unselected. Real observations determine all final circle references.
        first=(p[1]-p[0])/np.linalg.norm(p[1]-p[0]);last=(p[-1]-p[-2])/np.linalg.norm(p[-1]-p[-2])
        guide=Curve(np.vstack([p[0]-cfg['section_m']*first,p,p[-1]+cfg['section_m']*last]))
    if hint is None:
        _,uv,valid=guide.project(sample);hint=float(np.median(np.linalg.norm(uv[valid],axis=1))) if valid.any() else 1.
    cache=Samples(folder/'circle_samples.sqlite',cfg['section_m']);low=np.inf;high=-np.inf;read=0
    try:
        for ids,p,_ in source():
            emit(progress,cancel,f'2/3 Collecting local fitting windows · {read+len(p):,}/{total:,} area points')
            s,uv,valid=guide.project(p)
            if valid.any():cache.add(p[valid],s[valid],uv[valid]);low=min(low,float(s[valid].min()));high=max(high,float(s[valid].max()))
            read+=len(p)
        if read!=total:raise ValueError('The retained source changed during local-circle calibration.')
        if not np.isfinite([low,high]).all() or high-low<cfg['section_m']*.25:raise ValueError('Too little tunnel length follows this centreline. Choose the correct alignment or a longer branch.')
        step=cfg['section_m']/2
        stations=np.arange(math.floor(low/step)*step,math.ceil(high/step)*step+step*.1,step)
        stations=np.unique(np.clip(stations,guide.s[0],guide.s[-1]))
        if len(stations)<2:raise ValueError('Too few local windows along the centreline.')
        model=fit(cache,guide,stations,cfg,lambda m:progress('2/3 '+m),cancel,hint)
        ids_path=folder/'candidate_ids.bin';labels_path=folder/'candidate_labels.bin';flags_path=folder/'selected.bin';db_path=folder/'review.sqlite'
        db=sqlite3.connect(db_path);db.execute(shape_matching.MATCH_SCHEMA);groups={};counts=np.zeros(4,np.int64);read=matched=0
        # The preview is bounded, whereas exact candidate identities are streamed.
        preview=[];preview_labels=[];preview_indices=[];preview_stations=[];reservoir_rank=np.empty(0,np.uint64)
        try:
            with ids_path.open('wb') as out,labels_path.open('wb') as lab,flags_path.open('wb') as flags:
                for ids,p,_ in source():
                    emit(progress,cancel,f'3/3 Checking each original point against local circles · {read+len(p):,}/{total:,} area points')
                    label,s,_=classify(p,model,cfg);counts+=np.bincount(label,minlength=4);take=np.flatnonzero((label==1)|(label==2))
                    section=np.maximum(0,np.floor((s[take]-low)/cfg['section_m']).astype(np.int64));gid=(section*2+label[take]).astype('<i4')
                    np.asarray(ids[take],'<i8').tofile(out);gid.tofile(lab);np.zeros(len(take),'u1').tofile(flags)
                    keys,num=np.unique(gid,return_counts=True)
                    for k,n in zip(keys,num):groups[int(k)]=groups.get(int(k),0)+int(n)
                    rank=np.asarray(ids[take],np.uint64)*np.uint64(0x9e3779b97f4a7c15);rank^=rank>>np.uint64(29)
                    if len(reservoir_rank)<30000 or len(rank):
                        points=np.vstack([np.asarray(preview,float).reshape(-1,3),p[take]]);labs=np.r_[preview_labels,gid].astype('<i4');ix=np.r_[preview_indices,np.arange(len(take))+matched].astype('<i8');all_rank=np.r_[reservoir_rank,rank]
                        ps=np.r_[preview_stations,s[take]]
                        keep=np.argsort(all_rank,kind='stable')[:30000];preview=points[keep];preview_labels=labs[keep];preview_indices=ix[keep];preview_stations=ps[keep];reservoir_rank=all_rank[keep]
                    matched+=len(take);read+=len(p)
            if read!=total:raise ValueError('The retained source changed during local-circle classification.')
            for gid,n in sorted(groups.items()):
                section=(gid-1)//2;side=1 if gid%2==0 else -1
                db.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?)',(gid,low+(section+.5)*cfg['section_m'],0.,1.,side,n,0))
            db.commit()
        finally:db.close()
        context=sample[::max(1,math.ceil(len(sample)/30000))][:30000]
        context_status,context_station,_=classify(context,model,cfg);background=(context_status==0)|(context_status==3)
        context=context[background];context_status=context_status[background];context_station=context_station[background]
        np.savez(folder/'preview.npz',context=context,context_status=context_status,context_station=context_station,point_station=np.asarray(preview_stations,float),points=np.asarray(preview,float).reshape(-1,3),labels=np.asarray(preview_labels,'<i4'),indices=np.asarray(preview_indices,'<i8'))
        sections=model['sections'];good=sum(q['supported'] for q in sections)
        warnings=['The reference follows local best-fit circles, not a design surface or survey control. Real departures, joints and non-circular lining can be candidates; review them before removal.',
                  'Fitting windows overlap by 50% and their circle centres/radii are blended. Points outside poorly supported windows remain unselected.',
                  'Restore good lining removed by earlier cleaning before fitting. This operation does not reconstruct missing measurements.']
        if high-low<2*cfg['section_m']:
            warnings.append(f'Measured lining spans {high-low:.2f} m along the guide with {cfg["section_m"]:g} m fitting windows. These fits share much of the same data. Review their coverage and RMS. Classification requires two supported neighbouring fits.')
        if counts[1]+counts[2]>total*.25:warnings.append('More than a quarter of the retained scan lies outside the requested band. Inspect the circles and tolerances before selecting any candidates.')
        if good<len(sections):warnings.append(f'{len(sections)-good} fitting windows failed circularity, coverage or residual checks; affected points remain unselected.')
        if not good:warnings.append('No local circle meets the chosen band and wall-coverage checks. Restore missing lining, widen the tolerance, or review the centreline. All source points remain unselected.')
        return dict(count=matched,point_ids_file=str(ids_path),labels_file=str(labels_path),selected_file=str(flags_path),review_file=str(db_path),preview_file=str(folder/'preview.npz'),circle_sections=sections,
                    statistics=dict(input_points=total,effective_bounds=bounds.tolist(),guide_range_m=[low,high],
                        candidate_points=matched,selected_points=0,inward_points=int(counts[1]),outward_points=int(counts[2]),within_points=int(counts[0]),unassessed_points=int(counts[3]),section_length_m=cfg['section_m'],overlap_percent=50,fit_windows=len(sections),supported_windows=good,calibration_points=len(sample),seconds=round(time.monotonic()-started,3)),
                    warnings=warnings,method='Overlapping local best-fit circles in moving perpendicular centreline frames',research_dois=[])
    finally:
        cache.close()
        for suffix in ('','-journal','-wal','-shm'):(folder/('circle_samples.sqlite'+suffix)).unlink(missing_ok=True)
