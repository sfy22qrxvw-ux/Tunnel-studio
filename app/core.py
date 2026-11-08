# Copyright (c) 2026 Damian Gulczynski.
"""Tunnel Studio 0.1 — explicit geometry and exchange-file readers.

Coordinates are E,N,H in metres. Alignment station labels come from the user.
No datum transformation is performed. Target registration is in registration.py.
"""
from pathlib import Path
import csv, math, re
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree

CHUNK = 100_000

def finite_number(value, label):
    x = float(value)
    if not math.isfinite(x):
        raise ValueError(f'{label} must be finite.')
    return x

def csv_rows(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        sample = f.read(8192); f.seek(0)
        try: dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error: dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        for row in reader:
            yield {str(k).strip().lower(): str(v).strip() for k,v in row.items() if k is not None}

def coordinate(row, key):
    aliases = {'e':('e','easting','x'), 'n':('n','northing','y'), 'h':('h','height','z','elevation')}
    for k in aliases[key]:
        if k in row: return finite_number(row[k], key)
    raise ValueError(f'Missing {key.upper()} coordinate column.')

class Alignment:
    def __init__(self, rows, name='alignment.csv'):
        if len(rows) < 2: raise ValueError('The alignment needs at least two rows.')
        self.name = name
        self.a = np.asarray([[finite_number(r['chainage'],'Chainage'),coordinate(r,'e'),coordinate(r,'n'),coordinate(r,'h'),finite_number(r['radius'],'Radius')] for r in rows],float)
        if np.any(np.diff(self.a[:,0]) <= 0): raise ValueError('Alignment chainages must increase strictly in file order; station equations are not supported.')
        if np.any(self.a[:,4] <= 0): raise ValueError('All design radii must be positive.')
        self.p = self.a[:-1,1:4]; self.d = np.diff(self.a[:,1:4],axis=0)
        self.l2 = (self.d[:,:2]**2).sum(axis=1)
        if np.any(self.l2 < 1e-12): raise ValueError('Consecutive alignment rows cannot have the same E/N position.')
        self.mid = self.p[:,:2] + self.d[:,:2]/2
        self.kd = cKDTree(self.mid)
        self.tree = self._tree(np.arange(len(self.d)))

    @classmethod
    def read(cls,path): return cls(list(csv_rows(path)),Path(path).name)

    def _tree(self,ids):
        p=self.p[ids,:2]; q=p+self.d[ids,:2]
        lo=np.minimum(p,q).min(axis=0); hi=np.maximum(p,q).max(axis=0)
        if len(ids)<=8: return (lo,hi,ids,None,None)
        k=np.argmax(hi-lo); order=ids[np.argsort(self.mid[ids,k])]; m=len(order)//2
        return (lo,hi,None,self._tree(order[:m]),self._tree(order[m:]))

    def at(self,chainage):
        s=finite_number(chainage,'Chainage')
        if s<self.a[0,0] or s>self.a[-1,0]: raise ValueError('Selected chainage is outside the alignment.')
        i=min(max(np.searchsorted(self.a[:,0],s,side='right')-1,0),len(self.d)-1)
        f=(s-self.a[i,0])/(self.a[i+1,0]-self.a[i,0])
        centre=self.p[i]+f*self.d[i]
        tangent=self.d[i]/np.linalg.norm(self.d[i])
        right=np.array([tangent[1],-tangent[0],0.]); right/=np.linalg.norm(right)
        up=np.cross(right,tangent)
        radius=self.a[i,4]+f*(self.a[i+1,4]-self.a[i,4])
        return centre,tangent,right,up,float(radius)

    def project(self,points):
        """Exact nearest segment of the supplied horizontal polyline, with BVH pruning.

        Equal-distance ties choose the earlier segment. End extrapolation is rejected.
        Labels interpolate between supplied station values, not spatial 3D lengths.
        """
        points=np.asarray(points,float); xy=points[:,:2]
        _,ix=self.kd.query(xy)
        def calc(ids,pt):
            v=pt-self.p[ids,:2]; raw=(v*self.d[ids,:2]).sum(axis=-1)/self.l2[ids]
            f=np.clip(raw,0,1); residual=v-f[...,None]*self.d[ids,:2]
            return (residual**2).sum(axis=-1),f,raw
        best,f,raw=calc(ix,xy)
        def visit(node,indices):
            nonlocal best,f,raw,ix
            lo,hi,ids,left,right=node
            v=np.maximum(np.maximum(lo-xy[indices],xy[indices]-hi),0)
            indices=indices[(v*v).sum(axis=1)<=best[indices]+1e-12]
            if not len(indices): return
            if ids is None:
                visit(left,indices); visit(right,indices); return
            ds,fs,rs=calc(ids[None,:],xy[indices,None,:])
            k=np.argmin(ds,axis=1); j=np.arange(len(indices)); nb=ds[j,k]; ni=ids[k]
            use=(nb<best[indices]-1e-12)|((np.abs(nb-best[indices])<=1e-12)&(ni<ix[indices]))
            dest=indices[use]; best[dest]=nb[use]; ix[dest]=ni[use]; f[dest]=fs[j,k][use]; raw[dest]=rs[j,k][use]
        visit(self.tree,np.arange(len(points)))
        s=self.a[ix,0]+f*(self.a[ix+1,0]-self.a[ix,0])
        valid=~(((ix==0)&(raw < -1e-9))|((ix==len(self.d)-1)&(raw > 1+1e-9)))
        centre=self.p[ix]+f[:,None]*self.d[ix]
        tangent=self.d[ix]/np.linalg.norm(self.d[ix],axis=1)[:,None]
        right=np.column_stack((tangent[:,1],-tangent[:,0],np.zeros(len(points))))
        right/=np.linalg.norm(right,axis=1)[:,None]
        up=np.cross(right,tangent); delta=points-centre
        u=(delta*right).sum(axis=1); v=(delta*up).sum(axis=1)
        radius=self.a[ix,4]+f*(self.a[ix+1,4]-self.a[ix,4])
        s[~valid]=np.nan
        return np.column_stack((s,u,v,np.hypot(u,v)-radius))

def point_chunks(path,progress=lambda x:None):
    from pointio import records
    for points, attributes in records(path,progress): yield points

from pointio import import_cloud

def project_cloud(meta,axis,destination,progress=lambda x:None,cancel=None):
    from contextlib import ExitStack
    import os
    import uuid
    import compute_runtime
    import registration_pose
    n=int(meta['count']);valid=0
    if compute_runtime.workers()>1 and len(range(0,n,CHUNK))>=4:
        from parallel_meshing import project_cloud_parallel
        projected=project_cloud_parallel(meta,axis,destination,progress,cancel)
        __import__('storage_cleanup').record_projection(meta,axis,destination)
        return projected
    def stamp(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    destination=Path(destination)
    temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
    if any(destination.resolve()==Path(meta[key]).resolve() for key in ('file','mask_file') if meta.get(key)):
        raise ValueError('Projection output must not replace its source cache.')
    try:
      with ExitStack() as files:
        source=files.enter_context(open(meta['file'],'rb'))
        excluded=files.enter_context(open(meta['mask_file'],'rb')) if meta.get('mask_file') else None
        inputs=[(meta['file'],source,n*24)]+([(meta['mask_file'],excluded,n)] if excluded else [])
        revisions=[]
        for path,stream,size in inputs:
            info=os.fstat(stream.fileno())
            if info.st_size!=size:raise ValueError('A projection source cache is incomplete.')
            revisions.append((path,stamp(info)))
        out=files.enter_context(open(temporary,'wb'))
        def chunks():
          for start in range(0,n,CHUNK):
            compute_runtime.check_cancel(cancel)
            count=min(CHUNK,n-start);cloud=np.fromfile(source,'<f8',count=count*3)
            if cloud.size!=count*3:raise ValueError('The coordinate cache is incomplete.')
            flags=np.fromfile(excluded,'u1',count=count) if excluded is not None else None
            if flags is not None and len(flags)!=count:raise ValueError('The projection source deletion mask is incomplete.')
            yield cloud.reshape(-1,3),flags
        def project_chunk(chunk):
            cloud,flags=chunk
            rows=axis.project(registration_pose.points(meta,cloud))
            use=np.isfinite(rows[:,0])
            if flags is not None:use&=flags==0
            return rows,int(use.sum())
        for ordinal,chunk in enumerate(chunks(),1):
            rows,kept=project_chunk(chunk)
            compute_runtime.check_cancel(cancel)
            valid+=kept
            # Keep a projection for every original row so Undo/restoration can
            # reuse it. Only the informational retained total uses the mask.
            rows.astype('<f8').tofile(out)
            total=(n+CHUNK-1)//CHUNK
            progress(f'Calculating chainages: {int(100*ordinal/max(1,total))}% · {ordinal:,}/{total:,} chunks done')
        if any(stamp(Path(path).stat())!=before for path,before in revisions):
            raise ValueError('A projection source changed while calculating chainages.')
      compute_runtime.check_cancel(cancel)
      os.replace(temporary,destination)
    finally:
      temporary.unlink(missing_ok=True)
    __import__('storage_cleanup').record_projection(meta,axis,destination)
    return str(destination),valid


def analyse_profile(uv,radius):
    """One equal-weight median observation per occupied 1-degree angular bin."""
    uv=np.asarray(uv,float); radii=np.linalg.norm(uv,axis=1)
    theta=np.mod(np.arctan2(uv[:,1],uv[:,0]),2*np.pi)
    bins=np.floor(theta*180/np.pi).astype(int); med=[]
    for b in np.unique(bins):
        use=bins==b; a=np.mean(theta[use]); r=np.median(radii[use]); med.append([r*np.cos(a),r*np.sin(a)])
    q=np.asarray(med); angles=np.sort(theta)
    gap=float(np.degrees(np.max(np.diff(np.r_[angles,angles[0]+2*np.pi])))) if len(angles) else 360.
    result={'fit_points':len(q),'largest_gap_deg':gap,'fit_valid':False}
    if len(q)<12 or gap>60+1e-8:
        result['quality']='Insufficient coverage: need at least 12 occupied angular bins and no angular gap over 60 degrees.'; return result
    try:
        centre=q.mean(axis=0); local=q-centre
        coef=np.linalg.lstsq(np.column_stack((2*local[:,0],2*local[:,1],np.ones(len(local)))),np.sum(local**2,axis=1),rcond=None)[0]
        c0=centre+coef[:2]; r0=np.sqrt(max(coef[2]+np.dot(coef[:2],coef[:2]),1e-12))
        circle=least_squares(lambda p:np.linalg.norm(q-p[:2],axis=1)-p[2],[*c0,r0],xtol=1e-12,ftol=1e-12,gtol=1e-12)
        cx,cy,r=circle.x
        cov=(q-circle.x[:2]).T@(q-circle.x[:2])/len(q); ev,vec=np.linalg.eigh(cov); order=np.argsort(ev)[::-1]
        a0,b0=np.sqrt(2*np.maximum(ev[order],1e-8)); ang0=math.atan2(vec[1,order[0]],vec[0,order[0]])
        def residual(p):
            x,y=q[:,0]-p[0],q[:,1]-p[1]; ca,sa=np.cos(p[4]),np.sin(p[4]); a,b=np.exp(p[2:4])
            # Normalised implicit ellipse residual, scaled to metres; not orthogonal-distance fitting.
            return (np.hypot((x*ca+y*sa)/a,(-x*sa+y*ca)/b)-1)*np.sqrt(a*b)
        ell=least_squares(residual,[cx,cy,np.log(a0),np.log(b0),ang0],max_nfev=150,xtol=1e-11,ftol=1e-11,gtol=1e-11,
            bounds=([-radius,-radius,np.log(radius*.25),np.log(radius*.25),-10],[radius,radius,np.log(radius*2),np.log(radius*2),10]))
        ex,ey,la,lb,ang=ell.x; a,b=np.exp([la,lb])
        if b>a: a,b=b,a; ang+=np.pi/2
        ang%=np.pi
        if not circle.success or not ell.success: raise ValueError('Fit did not converge.')
        def diameter(direction):
            t=np.deg2rad(direction)-ang
            return float(2/np.sqrt((np.cos(t)/a)**2+(np.sin(t)/b)**2))
        result.update(fit_valid=True,quality='Coverage gate passed. Project acceptance not assessed.',circle_centre=[float(cx),float(cy)],circle_radius=float(r),
            circle_rms_mm=float(np.sqrt(np.mean(circle.fun**2))*1000),ellipse_centre=[float(ex),float(ey)],a=float(a),b=float(b),angle_deg=float(np.degrees(ang)),
            major_diameter=float(2*a),minor_diameter=float(2*b),ovality_mm=float(2000*(a-b)),ovality_percent=float((a-b)/radius*100),
            horizontal_diameter=diameter(0),vertical_diameter=diameter(90),diagonal45=diameter(45),diagonal135=diameter(135),
            ellipse_residual_rms_mm=float(np.sqrt(np.mean(ell.fun**2))*1000))
    except (ValueError,np.linalg.LinAlgError) as e: result['quality']='Fit unavailable: '+str(e)
    return result

def slice_analysis(meta,derived,axis,station,width,band,tolerance):
    """Compatibility entry point; all section sizes use the bounded engine."""
    import large_sections
    work=Path(derived).parent if derived else Path(meta['file']).parent
    return large_sections.calculate(meta,derived,axis,station,width,band,tolerance,work)


def ring_checks(path,axis,tolerance=10):
    output=[]; seen=set()
    for row in csv_rows(path):
        rid=row.get('ring_id','')
        if not rid or rid in seen: raise ValueError('Each ring row needs a unique ring_id.')
        seen.add(rid); design=finite_number(row['design_chainage'],'Design chainage')
        axis.at(design)
        if row.get('measured_chainage'):
            measured=finite_number(row['measured_chainage'],'Measured chainage'); source='Supplied face chainage'
        else:
            p=np.array([[coordinate(row,k) for k in ('e','n','h')]]); measured=float(axis.project(p)[0,0]); source='Projected survey point'
            if not math.isfinite(measured): raise ValueError(f'{rid}: measured point projects beyond alignment ends.')
        difference=(measured-design)*1000
        output.append({'ring_id':rid,'design':design,'measured':measured,'creep_mm':difference,'source':source,'reference':row.get('reference','Not specified'),
            'outside':abs(difference)>tolerance})
    if not output: raise ValueError('The ring file has no data rows.')
    return output

def suggest_joints(meta,derived,axis,start,end,pitch=1.8,bin_width=.02,threshold_mm=3,band=.3,progress=lambda x:None):
    """Experimental geometry-only circumferential joint candidates; no ring IDs inferred.

    Local radial changes are measured independently in 24 angular sectors.
    A candidate needs geometric support in at least 12 sectors, with at least 16
    observed sectors. Missing data is not interpreted as a seam.
    """
    start,end,pitch,bin_width,threshold_mm,band=[finite_number(v,'Joint setting') for v in (start,end,pitch,bin_width,threshold_mm,band)]
    axis.at(start); axis.at(end)
    if end<=start or not .002<=bin_width<=.1 or pitch<=bin_width*10 or threshold_mm<=0 or band<=0:
        raise ValueError('Choose an increasing range, 0.002–0.1 m bins, positive threshold and ring width over ten bins.')
    from joint_detection import run
    return run(meta,derived,axis,start,end,pitch,bin_width,threshold_mm,band,progress)

def generate_demo(folder):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    rows=[{'chainage':1000+s,'e':200000+0.00025*s*s,'n':375000+s,'h':100,'radius':3.5} for s in np.arange(0,61,2)]
    axis=Alignment(rows,'DEMO alignment')
    with open(folder/'demo_alignment.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['chainage','e','n','h','radius']); w.writeheader(); w.writerows(rows)
    rng=np.random.default_rng(14); points=[]
    theta=np.linspace(0,2*np.pi,180,endpoint=False)
    joints=np.array([1000+i*1.8+(3*np.sin(i*.5)+i*.4)/1000 for i in range(1,31)])
    stations=np.unique(np.r_[np.arange(1000,1060.001,.05),np.concatenate([j+np.arange(-.10,.101,.008) for j in joints])])
    for s in stations:
        centre,t,right,up,r=axis.at(s); phi=.35; a=3.53; b=3.48
        x=a*np.cos(theta); y=b*np.sin(theta)
        u=x*np.cos(phi)-y*np.sin(phi)+.025; v=x*np.sin(phi)+y*np.cos(phi)-.012
        u+=rng.normal(0,.0007,len(theta)); v+=rng.normal(0,.0007,len(theta))
        recess=.012*np.exp(-.5*(np.min(abs(joints-s))/.014)**2)
        radial=np.hypot(u,v); u*=1+recess/radial; v*=1+recess/radial
        points.append(centre+u[:,None]*right+v[:,None]*up)
    p=np.concatenate(points)
    np.savetxt(folder/'demo_scan.xyz',p,fmt='%.6f')
    with open(folder/'demo_rings.csv','w',newline='') as f:
        w=csv.writer(f); w.writerow(['ring_id','design_chainage','measured_chainage','reference'])
        for i in range(1,31):
            s=1000+i*1.8; w.writerow([f'R{i:04}',f'{s:.3f}',f'{s+(3*np.sin(i*.5)+i*.4)/1000:.6f}','Leading ring face — synthetic example'])
    return folder
