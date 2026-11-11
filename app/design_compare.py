# Copyright (c) 2026 Damian Gulczynski.
"""Full retained-cloud comparison with circular alignment or triangle design.

Circular: t=clip((p-a).(b-a)/|b-a|^2,0,1), c=a+t(b-a),
          chainage=(1-t)s0+t*s1, R=(1-t)R0+t*R1, d=|p-c|-R.
Mesh: q=argmin over all reference triangles |p-q|; d=|p-q|.
Optional normal sign: d*=sign((p-q).n). This is an oriented-face side,
not a watertight inside/outside classification. No scan alignment is performed.
Statistics use every retained finite point admitted by the explicit band.
"""
from pathlib import Path
import csv, hashlib, json, math, uuid
import numpy as np
from scipy.spatial import cKDTree
import jobdata

RECORD=np.dtype([('point_id','<u8'),('xyz','<f8',(3,)),('deviation_m','<f8'),('chainage_m','<f8'),('status','u1')])


def fingerprint(meta):
    # Includes every constituent source, deletion mask and virtual pose.
    from inspection import fingerprint as current_revision
    return current_revision(meta)


def axis_fingerprint(rows):
    return hashlib.sha256(np.asarray(rows,dtype='<f8').tobytes()).hexdigest()


class CircularDesign:
    def __init__(self,rows):
        a=np.asarray(rows,dtype=float)
        if a.ndim!=2 or a.shape[1]!=5 or len(a)<2 or not np.isfinite(a).all():raise ValueError('Load a valid chainage/E/N/H/radius alignment.')
        lengths=np.linalg.norm(np.diff(a[:,1:4],axis=0),axis=1)
        if np.any(np.diff(a[:,0])<=0) or np.any(lengths<=1e-9) or np.any(a[:,4]<=0):raise ValueError('Design needs increasing chainages, distinct adjacent centres and positive radii.')
        # Subdivision bounds the exact segment search without changing the line.
        pieces=np.maximum(1,np.ceil(lengths/2).astype(int))
        if pieces.sum()>250000:raise ValueError('Use a shorter alignment for this comparison (at most 250,000 two-metre segments).')
        rows=[]
        for i,n in enumerate(pieces):
            t=np.arange(n)/n;rows.append(a[i]+t[:,None]*(a[i+1]-a[i]))
        self.a=np.vstack([*rows,a[-1:]])
        self.origin=self.a[0,1:4].copy();self.a[:,1:4]-=self.origin
        self.start=self.a[:-1,1:4];self.vector=np.diff(self.a[:,1:4],axis=0)
        self.length2=np.sum(self.vector**2,axis=1);self.mid=self.start+self.vector*.5
        self.tree=cKDTree(self.mid)
        self.bounds=self._bounds(np.arange(len(self.start)))

    def _bounds(self,ids):
        a,b=self.start[ids],self.a[ids+1,1:4]
        lo=np.minimum(a,b).min(axis=0);hi=np.maximum(a,b).max(axis=0)
        if len(ids)<=8:return lo,hi,np.sort(ids),None,None
        order=ids[np.argsort(self.mid[ids,np.argmax(hi-lo)])];middle=len(order)//2
        return lo,hi,None,self._bounds(order[:middle]),self._bounds(order[middle:])

    def _distances(self,p,ids):
        delta=p-self.start[ids];vector=self.vector[ids]
        raw=np.sum(delta*vector,axis=-1)/self.length2[ids];t=np.clip(raw,0,1)
        delta-=t[...,None]*vector
        return np.sum(delta**2,axis=-1),t,raw

    def _project_block(self,p):
        # The midpoint is a starting upper bound. Segment bounds below
        # verify the exact nearest result, including competing branches.
        _,idx=self.tree.query(p,eps=.5)
        best,t,raw=self._distances(p,idx)

        def visit(node,rows):
            lo,hi,ids,left,right=node
            q=p[rows]
            gap=np.maximum(np.maximum(lo-q,q-hi),0.)
            rows=rows[np.sum(gap**2,axis=1)<=best[rows]+1e-10]
            if not len(rows):return
            if ids is None:
                visit(left,rows);visit(right,rows);return
            d2,f,r=self._distances(p[rows,None,:],ids[None,:])
            minimum=d2.min(axis=1)
            column=np.argmax(d2<=minimum[:,None]+1e-20,axis=1)
            candidates=ids[column]
            use=(minimum<best[rows]-1e-20)|((abs(minimum-best[rows])<=1e-20)&(candidates<idx[rows]))
            dest=rows[use];selected=column[use]
            best[dest]=minimum[use];idx[dest]=candidates[use]
            t[dest]=f[use,selected];raw[dest]=r[use,selected]

        visit(self.bounds,np.arange(len(p)))
        beyond=((idx==0)&(raw < -1e-10))|((idx==len(self.start)-1)&(raw > 1+1e-10))
        return idx,t,best,~beyond

    def project(self,points):
        """Nearest 3D segment, clamped fraction, squared distance and end validity."""
        p=np.asarray(points,dtype=np.float64)-self.origin
        segments=np.empty(len(p),dtype=np.intp);fractions=np.empty(len(p));distance2=np.empty(len(p))
        valid=np.empty(len(p),dtype=bool)
        for start in range(0,len(p),25000):
            part=slice(start,start+25000)
            segments[part],fractions[part],distance2[part],valid[part]=self._project_block(p[part])
        return segments,fractions,distance2,valid

    def evaluate(self,points):
        idx,t,distance2,valid=self.project(points)
        radius=self.a[idx,4]*(1-t)+self.a[idx+1,4]*t
        station=self.a[idx,0]*(1-t)+self.a[idx+1,0]*t
        return np.sqrt(distance2)-radius,station,valid


class MeshDesign:
    def __init__(self,meta,signed=False):
        # Importing mesh_geometry also enables the optional engine folder.
        import mesh_geometry
        try:import open3d as o3d
        except ImportError as e:raise ValueError('Design-mesh comparison needs Open3D. Reinstall the complete current Tunnel Studio installer.') from e
        v=np.memmap(meta['vertices_file'],'<f8','r',shape=(int(meta['vertices']),3))
        f=np.memmap(meta['faces_file'],'<u4','r',shape=(int(meta['triangles']),3))
        if not len(v) or not len(f) or f.max()>=len(v) or not np.isfinite(v).all():raise ValueError('Design mesh contains invalid geometry.')
        self.origin=(v.min(0)+v.max(0))/2;local=v-self.origin
        self.precision_mm=float(np.spacing(np.float32(np.max(np.abs(local)))))*1000
        self.o3d=o3d;self.signed=signed
        self.scene=o3d.t.geometry.RaycastingScene(nthreads=2)
        self.scene.add_triangles(o3d.core.Tensor(local.astype(np.float32)),o3d.core.Tensor(np.array(f,dtype=np.uint32)))

    def evaluate(self,points):
        local=np.asarray(points)-self.origin
        query_step_mm=float(np.spacing(np.float32(np.max(np.abs(local)))))*1000
        if query_step_mm>getattr(self,'query_precision_limit_mm',float('inf')):
            raise ValueError('Source points are too far from the design origin for this tolerance. Check units/positioning or compare a smaller section.')
        hit=self.scene.compute_closest_points(self.o3d.core.Tensor(local.astype(np.float32)),nthreads=2)
        closest=hit['points'].numpy().astype(float);delta=local-closest;distance=np.linalg.norm(delta,axis=1)
        if self.signed:
            side=np.einsum('ij,ij->i',delta,hit['primitive_normals'].numpy())
            # A tangential displacement at an edge has no stable normal-side sign.
            ambiguous=(np.abs(side)<1e-10)&(distance>1e-8)
            distance*=np.where(side<0,-1.,1.);distance[ambiguous]=np.nan
        return distance,np.full(len(points),np.nan),np.isfinite(distance)


def run(source,work,mode='alignment',axis_rows=None,reference=None,tolerance_mm=25,band_m=.5,progress=lambda s:None,cancel=lambda:False,*,scope=None):
    tolerance=float(tolerance_mm)/1000;band=float(band_m)
    if not np.isfinite([tolerance,band]).all() or tolerance<=0 or band<0 or (band and band<tolerance):raise ValueError('Tolerance must be positive. Comparison band must be zero (off) or at least the tolerance.')
    if mode=='alignment':design=CircularDesign(axis_rows);signed=True;design_name='Circular alignment radial deviation'
    elif mode in ('mesh_unsigned','mesh_normal'):
        if reference is None:raise ValueError('Choose an imported design mesh.')
        design=MeshDesign(reference,mode=='mesh_normal');signed=mode=='mesh_normal';design_name='Design mesh oriented-face distance' if signed else 'Design mesh unsigned distance'
        design.query_precision_limit_mm=float(tolerance_mm)/10
        if design.precision_mm>tolerance_mm/10:raise ValueError('Design extent is too large for the selected millimetre tolerance. Split the reference mesh into a smaller section.')
    else:raise ValueError('Choose circular alignment or design-mesh comparison.')
    import analysis_sources
    from inspection import Quantiles, STAT_RECORD
    from project_data_layout import folder
    n=int(source['count']);work=folder(work,'analysis');work.mkdir(parents=True,exist_ok=True)
    row_bytes=RECORD.itemsize+STAT_RECORD.itemsize
    if scope is None:jobdata.room(work,n*row_bytes)
    area_label=analysis_sources.scope_label(scope)
    ident=uuid.uuid4().hex;path=work/(ident+'.design-results')
    temporary=work/(ident+'.design-results.tmp');statsfile=work/(ident+'.design-stats.tmp')
    used=excluded=outside=negative=positive=within=0;total=total2=0.;count=0
    minimum=math.inf;maximum=-math.inf
    bounds=np.array([[np.inf]*3,[-np.inf]*3])
    counters=dict(retained_points=0,deleted_points=0,invalid_points=0)
    source_revision=fingerprint(source);reference_revision=fingerprint(reference) if reference else None
    try:
        with temporary.open('xb') as output,statsfile.open('xb') as stats:
            for ids,p in analysis_sources.blocks(source,counters,progress,cancel,25000,scope=scope):
                if cancel():raise jobdata.Cancelled('Design comparison cancelled.')
                if scope:
                    jobdata.room(work,len(p)*row_bytes)
                    bounds[0]=np.minimum(bounds[0],p.min(axis=0))
                    bounds[1]=np.maximum(bounds[1],p.max(axis=0))
                d,ch,valid=design.evaluate(p);valid&=np.isfinite(d);admitted=valid & ((np.abs(d)<=band+1e-9) if band else True)
                record=np.empty(len(p),dtype=RECORD);record['point_id']=ids;record['xyz']=p;record['deviation_m']=d;record['chainage_m']=ch;record['status']=np.where(~valid,2,np.where(admitted,0,1));record.tofile(output);count+=len(p)
                values=d[admitted]
                rows=np.empty(len(values),STAT_RECORD);rows['value']=values;rows['weight']=1.;rows.tofile(stats)
                used+=len(values);outside+=int((~valid).sum());excluded+=int((valid&~admitted).sum())
                total+=float(values.sum());total2+=float(np.dot(values,values));within+=int((np.abs(values)<=tolerance+1e-9).sum());negative+=int((values < -tolerance-1e-9).sum());positive+=int((values > tolerance+1e-9).sum())
                if len(values):minimum=min(minimum,float(values.min()));maximum=max(maximum,float(values.max()))
                if scope:progress(f'Cloud vs Design · {area_label} · {count:,} evaluated · {used:,} included')
                else:progress(f'Cloud vs Design: {counters["retained_points"]+counters["deleted_points"]:,} / {n:,} source points · {used:,} included')
        if scope and not count:raise ValueError('The processing area contains no retained points. Adjust the saved limit box or highlight.')
        if not used:raise ValueError('No retained points fall within this design comparison. Check units, positioning, alignment extent and comparison band.')
        quantiles=Quantiles(statsfile,used,used,minimum,maximum,False,progress,cancel)
        median=quantiles.percentile(50);p95=quantiles.percentile(95);p95abs=quantiles.percentile(95,absolute=True)
        if cancel():raise jobdata.Cancelled('Design comparison cancelled.')
        if fingerprint(source)!=source_revision or (reference and fingerprint(reference)!=reference_revision):
            raise ValueError('An input changed during the comparison. Run it again.')
        temporary.replace(path)
        return dict(id=ident,mode=mode,mode_label=design_name,results_file=str(path),record_count=count,source_count=n,
            source_ranges=analysis_sources.source_ranges(source),processing_scope=scope,processing_area=area_label,
            effective_bounds=bounds.tolist() if scope else None,
            **counters,used=used,excluded_by_band=excluded,outside_design_or_ambiguous=outside,
            within_tolerance=within,within_percent=100*within/used,below_tolerance=negative,above_tolerance=positive,
            min_mm=minimum*1000,median_mm=median*1000,p95_mm=p95*1000,max_mm=maximum*1000,
            mean_mm=total/used*1000,rms_mm=math.sqrt(max(0.,total2/used))*1000,p95_absolute_mm=p95abs*1000,
            tolerance_mm=tolerance*1000,band_m=band,signed=signed,mesh_coordinate_step_mm=getattr(design,'precision_mm',None),
            source_fingerprint=source_revision,reference_fingerprint=reference_revision,
            axis_fingerprint=axis_fingerprint(axis_rows) if mode=='alignment' else None,
            note=('Positive outside / negative inside the circular design. Nearest 3D centreline-segment radial deviation, not shortest distance to a curved or tapered surface.' if mode=='alignment' else
                  'Sign follows nearest triangle winding; flip inconsistent normals before use. Open-edge tangential signs are excluded.' if signed else 'Unsigned nearest distance to the full triangle design. No inside/outside classification.'),
            statistics_note='Every retained point in the processing area is evaluated in its registered position. The 3D display uses a sample.')
    except BaseException:
        path.unlink(missing_ok=True);raise
    finally:
        temporary.unlink(missing_ok=True);statsfile.unlink(missing_ok=True)


def _result_blocks(result):
    """Read owned bounded blocks; CSV export must not retain a whole-file mapping."""
    count=int(result['record_count']);path=Path(result['results_file'])
    if count<1 or path.stat().st_size!=count*RECORD.itemsize:
        raise ValueError('Design result data is missing or incomplete. Run the comparison again.')
    with path.open('rb') as stream:
        for start in range(0,count,100000):
            expected=min(100000,count-start);rows=np.fromfile(stream,RECORD,count=expected)
            if len(rows)!=expected:
                raise ValueError('Design result data changed during reading. Run the comparison again.')
            yield rows


def preview(result, packed=False):
    r=np.memmap(result['results_file'],RECORD,'r',shape=(result['record_count'],))
    try:
        step=max(1,math.ceil(len(r)/100000));q=np.array(r[::step],copy=True)
    finally:r._mmap.close()
    q=q[q['status']==0]
    if not len(q):
        # A narrow admitted band can miss the global stride; take bounded valid records.
        parts=[];remaining=100000
        for rows in _result_blocks(result):
            part=rows[rows['status']==0][:remaining]
            if len(part):parts.append(part);remaining-=len(part)
            if not remaining:break
        if not parts:raise ValueError('This design result has no included points to display.')
        q=np.concatenate(parts)
    origin=(q['xyz'].min(0)+q['xyz'].max(0))/2;dev=q['deviation_m']*1000;t=result['tolerance_mm']
    colours=np.tile([.27,.8,.53],(len(q),1));colours[dev>t+1e-6]=[.96,.28,.2];colours[dev < -t-1e-6]=[.22,.53,1.]
    points=np.column_stack([q['xyz']-origin,colours])
    return dict(origin=origin.tolist(),points=points.astype('<f4') if packed else points.tolist(),sample_count=len(q),used=result['used'],signed=result['signed'],tolerance_mm=t,deviations_mm=dev if packed else dev.tolist(),result_id=result['id'],reference_label=result.get('reference_label','Design reference'),reference_name=result.get('reference_name',''),
                processing_area=result.get('processing_area'),effective_bounds=result.get('effective_bounds'),record_count=result['record_count'],source_count=result.get('source_count'))


def export_csv(result,destination):
    with Path(destination).open('w',encoding='utf-8',newline='') as out:
        w=csv.writer(out);w.writerow(['source_point_index_0based','E_m','N_m','H_m','deviation_mm','chainage_m','status','mode','tolerance_mm','band_m','reference_kind','reference_name','source_scan_id','source_scan_name','original_scan_point_index_0based'])
        for rows in _result_blocks(result):
            for row in rows:
                source_row=next((q for q in result.get('source_ranges',[]) if q['start']<=int(row['point_id'])<q['start']+q['count']),None)
                w.writerow([int(row['point_id']),*map(lambda v:format(v,'.9f'),row['xyz']),
                    format(row['deviation_m']*1000,'.6f') if np.isfinite(row['deviation_m']) else '',
                    format(row['chainage_m'],'.6f') if np.isfinite(row['chainage_m']) else '',
                    ['included','excluded_by_band','outside_design_or_ambiguous'][int(row['status'])],result['mode'],result['tolerance_mm'],result['band_m'],result.get('reference_kind',''),result.get('reference_name',''),
                    (source_row.get('id') or result.get('source_id','')) if source_row else '',source_row['name'] if source_row else '',
                    int(row['point_id'])-source_row['start'] if source_row else ''])
    return destination
