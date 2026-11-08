# Copyright (c) 2026 Damian Gulczynski.
"""Full-density perpendicular sections with bounded reads and exact disk statistics.

Only the selected section is written to disk. The input tunnel can contain any
number of setups. Statistics and per-degree radial/angle medians use every
retained finite point; display and ellipse fitting use explicitly balanced bins.
"""
from project_data_layout import folder as asset_folder
from pathlib import Path
import hashlib,json,math,uuid,threading
import numpy as np
from scipy.optimize import least_squares
import jobdata,section_research,analysis_sources,inspection

CHUNK=100000
LOCK=threading.Lock()


def frame(axis,station):
    centre,normal,right,up,radius=axis.at(station)
    return centre,normal,right,up,radius


class _Values(inspection.Quantiles):
    """Exact quantile over one field in a bounded file interval."""
    def __init__(self,path,start,count,field,minimum,maximum,progress):
        self.path=Path(path);self.start=int(start);self.field=int(field);self.count=int(count)
        self.total_weight=float(count);self.minimum=float(minimum);self.maximum=float(maximum)
        self.weighted=False;self.progress=progress;self.cancel=lambda:False;self.histograms={};self.small=None
        if count<=self.LIMIT:
            with self.path.open('rb') as f:
                f.seek(self.start*16);q=np.fromfile(f,'<f8',count=count*2).reshape(-1,2)
            if len(q)!=count:raise ValueError('Section fit statistics are incomplete.')
            self.small=np.empty(count,inspection.STAT_RECORD);self.small['value']=q[:,field];self.small['weight']=1.
    def blocks(self,absolute=False):
        with self.path.open('rb') as f:
            f.seek(self.start*16)
            for start in range(0,self.count,self.LIMIT):
                self.progress('Balancing section fit: exact full-density medians')
                n=min(self.LIMIT,self.count-start);q=np.fromfile(f,'<f8',count=n*2)
                if len(q)!=n*2:raise ValueError('Section fit statistics are incomplete.')
                value=q.reshape(-1,2)[:,self.field]
                yield np.abs(value) if absolute else value,np.ones(n,dtype=np.float64)


def calculate(*args,**kwargs):
    with LOCK:return _calculate(*args,**kwargs)


def _calculate(meta,derived,axis,station,width,band,tolerance,work,progress=lambda _:None):
    station,width,band,tolerance=map(float,(station,width,band,tolerance))
    if not np.isfinite([station,width,band,tolerance]).all() or not 0<width<=5 or not 0<band<=10 or tolerance<=0:
        raise ValueError('Use a slice thickness above 0 and at most 5 m, a wall band above 0 and at most 10 m, and a positive tolerance.')
    centre,normal,right,up,radius=frame(axis,station);n=int(meta['count']);work=asset_folder(work,'analysis');work.mkdir(parents=True,exist_ok=True)
    revision=inspection.fingerprint(meta)
    inputs=['streamed-section-v2',revision,axis.a.tolist(),station,width,band,tolerance]
    ident=hashlib.sha256(json.dumps(inputs).encode()).hexdigest()[:28]
    path=work/(ident+'.large-section');report_path=path.with_suffix('.large-section.json')
    if report_path.is_file() and path.is_file():
        report=json.loads(report_path.read_text())
        if path.stat().st_size==report['kept_points']*24:
            q=np.memmap(path,'<f8','r',shape=(report['kept_points'],3));return report,q[:,:2],q[:,2]
    # Reserve one working block, not an entire-tunnel XYZ-sized allocation for
    # what may be a very thin slice. Additional disk is checked as rows arrive.
    jobdata.room(work,CHUNK*56)
    token=uuid.uuid4().hex
    temp=work/(token+'.section.tmp');angle_path=work/(token+'.section-angles.tmp');stats_path=work/(token+'.section-stats.tmp')
    report_temp=work/(token+'.section-report.tmp')
    counts=np.zeros(360,np.int64);count=excluded=outside=source_points=0
    extrema=np.full((360,4),np.nan)
    minimum=float('inf');maximum=-float('inf');total=total2=0.
    counters=dict(retained_points=0,deleted_points=0,invalid_points=0)
    try:
        with temp.open('xb') as out,stats_path.open('xb') as stats:
            for _,points in analysis_sources.blocks(meta,counters,lambda message:progress('Large section: '+message),block=CHUNK):
                delta=points-centre;selected=np.abs(delta@normal)<=width/2+1e-9;delta=delta[selected]
                source_points+=len(delta)
                uv=np.column_stack((delta@right,delta@up));rad=np.linalg.norm(uv,axis=1)
                dev=(rad-radius)*1000;admit=np.abs(dev)<=band*1000+1e-6
                excluded+=int((~admit).sum());uv=uv[admit];rad=rad[admit];dev=dev[admit]
                if len(dev):
                    jobdata.room(work,len(dev)*56)
                    np.column_stack((uv,dev)).astype('<f8').tofile(out)
                    records=np.empty(len(dev),inspection.STAT_RECORD);records['value']=dev;records['weight']=1.;records.tofile(stats)
                    phi=np.mod(np.arctan2(uv[:,1],uv[:,0]),2*math.pi)
                    bins=np.floor(phi*180/math.pi).astype(int)%360
                    counts+=np.bincount(bins,minlength=360)
                    for b in np.unique(bins):
                        use=bins==b;now=[rad[use].min(),rad[use].max(),phi[use].min(),phi[use].max()]
                        if np.isnan(extrema[b,0]):extrema[b]=now
                        else:extrema[b]=[min(extrema[b,0],now[0]),max(extrema[b,1],now[1]),min(extrema[b,2],now[2]),max(extrema[b,3],now[3])]
                    count+=len(dev);outside+=int((np.abs(dev)>tolerance+1e-6).sum())
                    minimum=min(minimum,float(dev.min()));maximum=max(maximum,float(dev.max()));total+=float(dev.sum());total2+=float(dev@dev)
                checked=counters['retained_points']+counters['deleted_points']
                progress(f'Large section: {checked:,} / {n:,} source points · {count:,} retained in slice')
        if not count:
            report=dict(chainage=station,width=width,band=band,tolerance_mm=tolerance,
                centre=centre.tolist(),right=right.tolist(),up=up.tolist(),radius=radius,
                source_points=source_points,source_count=n,kept_points=0,excluded_points=excluded,
                fit_valid=False,quality='No wall points in this slice.',points=[],displayed_points=0,
                large_section=True,balanced_points=[])
            return report,np.empty((0,2)),np.empty(0)
        quantiles=inspection.Quantiles(stats_path,count,count,minimum,maximum,False,progress,lambda:False)
        p95=quantiles.percentile(95,absolute=True)
        jobdata.room(work,count*16)
        offset=np.r_[0,np.cumsum(counts)];cursor=offset[:-1].copy()
        with temp.open('rb') as src,angle_path.open('x+b') as out:
            out.truncate(count*16)
            for start in range(0,count,CHUNK):
                q=np.fromfile(src,'<f8',count=min(CHUNK,count-start)*3).reshape(-1,3)
                uv=q[:,:2];phi=np.mod(np.arctan2(uv[:,1],uv[:,0]),2*math.pi);rad=np.linalg.norm(uv,axis=1)
                bins=np.floor(phi*180/math.pi).astype(int)%360
                for b in np.unique(bins):
                    use=bins==b;values=np.column_stack((rad[use],phi[use])).astype('<f8')
                    out.seek(int(cursor[b])*16);values.tofile(out);cursor[b]+=len(values)
                progress(f'Balancing section fit: {start+len(q):,} / {count:,} retained points')
        balanced=[]
        for b in np.flatnonzero(counts):
            start=int(offset[b]);size=int(counts[b]);lo,hi,alo,ahi=extrema[b]
            r=_Values(angle_path,start,size,0,lo,hi,progress).percentile(50)
            phi=_Values(angle_path,start,size,1,alo,ahi,progress).percentile(50)
            balanced.append([r*math.cos(phi),r*math.sin(phi)])
        balanced=np.asarray(balanced);occupied=np.flatnonzero(counts)
        gap=float(np.max(np.diff(np.r_[occupied,occupied[0]+360])))
        q=np.memmap(temp,'<f8','r',shape=(count,3))
        try:
            sample=np.array(q[::max(1,math.ceil(count/18000))],copy=True).tolist()
            report=dict(chainage=station,width=width,band=band,tolerance_mm=tolerance,centre=centre.tolist(),right=right.tolist(),up=up.tolist(),radius=radius,
                source_points=source_points,source_count=n,kept_points=count,excluded_points=excluded,
                min_deviation_mm=minimum,max_deviation_mm=maximum,p95_abs_mm=p95,outside_tolerance=outside,
                mean_deviation_mm=total/count,radial_rms_mm=math.sqrt(max(0.,total2/count)),
                fit_valid=False,fit_points=len(balanced),largest_gap_deg=gap,points=sample,displayed_points=len(sample),large_section=True,
                balanced_points=balanced.tolist(),fit_method='Robust ellipse on exact full-density 1-degree radial/angle medians',
                quality='Full-density radial statistics; fit withheld for insufficient angular coverage.')
            if len(balanced)>=24 and gap<=60:
                try:
                    fit=section_research.fit(q[:,:2],radius,20,balanced_points=balanced)
                    circle=least_squares(lambda v:np.linalg.norm(balanced-v[:2],axis=1)-v[2],[0.,0.,radius])
                    if not circle.success:raise ValueError('Circle fit did not converge.')
                    a,b,theta=fit['a'],fit['b'],fit['angle_rad']
                    def diameter(angle):return 2/math.sqrt((math.cos(angle-theta)/a)**2+(math.sin(angle-theta)/b)**2)
                    report.update(fit_valid=True,circle_centre=circle.x[:2].tolist(),circle_radius=float(circle.x[2]),circle_rms_mm=float(np.sqrt(np.mean(circle.fun**2))*1000),
                        ellipse_centre=fit['centre'],a=a,b=b,angle_deg=math.degrees(theta),major_diameter=2*a,minor_diameter=2*b,
                        horizontal_diameter=diameter(0),vertical_diameter=diameter(math.pi/2),diagonal45=diameter(math.pi/4),diagonal135=diameter(3*math.pi/4),
                        ovality_mm=2000*(a-b),ovality_percent=100*(a-b)/radius,ellipse_residual_rms_mm=fit['fit_rms_mm'],
                        quality='Full-density radial statistics. Robust ellipse on exact 1-degree median bins; 20 mm fit threshold. Review lining support.')
                except (ValueError,np.linalg.LinAlgError) as error:report['quality']='Full-density radial statistics; '+str(error)
        finally:q._mmap.close()
        if inspection.fingerprint(meta)!=revision:raise ValueError('A source scan changed during section analysis. Run it again.')
        report_temp.write_text(json.dumps(report),encoding='utf8');temp.replace(path);report_temp.replace(report_path)
        progress(f'Large section complete · {count:,} retained points')
        q=np.memmap(path,'<f8','r',shape=(count,3));return report,q[:,:2],q[:,2]
    finally:
        for temporary in (temp,angle_path,stats_path,report_temp):temporary.unlink(missing_ok=True)


def section(s,legacy,station,width,band,tolerance):
    # Enter the bounded engine immediately, without first accumulating two
    # million points or choosing a different plane based on slice density.
    return calculate(s.STATE['cloud'],s.STATE.get('derived'),s.STATE['axis'],station,width,band,tolerance,s.WORK,s.progress)
