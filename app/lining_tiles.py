# Copyright (c) 2026 Damian Gulczynski.
"""Disk-backed lining evidence with bounded, overlapping analysis windows.

Only accumulators and labels are cached, never a second copy of the cloud.
Source coordinates/IDs and the existing short-section detector are unchanged.
"""
from collections import OrderedDict
from pathlib import Path
import math
import shutil
import tempfile

import numpy as np
import lining_features as lf
import tunnel_lining

EVIDENCE = np.dtype([('count','<i8'),('depth','<f8'),('isum','<f8'),('icount','<i8')])
# Working window budget; no selected-point count or tunnel length cap.
WINDOW_CELLS = 1000000
INTERVAL_CELLS = 2000000
MAP_BYTES = 8 * 1024 * 1024


class DiskRows:
    """At most two 8 MiB mappings, explicitly closed before file removal."""
    def __init__(self, path, rows, columns, dtype):
        self.path=Path(path);self.rows=rows;self.columns=columns;self.dtype=np.dtype(dtype)
        self.step=max(1,MAP_BYTES//(columns*self.dtype.itemsize));self.maps=OrderedDict()
        with self.path.open('wb') as f:f.truncate(rows*columns*self.dtype.itemsize)

    def block(self, number):
        if number in self.maps:
            value=self.maps.pop(number);self.maps[number]=value;return value
        while len(self.maps)>=2:
            _,old=self.maps.popitem(last=False);old.flush();old._mmap.close()
        start=number*self.step;size=min(self.step,self.rows-start)*self.columns
        value=np.memmap(self.path,dtype=self.dtype,mode='r+',offset=start*self.columns*self.dtype.itemsize,shape=(size,))
        self.maps[number]=value;return value

    def groups(self, rows, columns):
        blocks=rows//self.step
        for number in np.unique(blocks):
            use=np.flatnonzero(blocks==number)
            yield self.block(int(number)),use,(rows[use]-number*self.step)*self.columns+columns[use]

    def read(self, start, end):
        result=np.empty((end-start)*self.columns,dtype=self.dtype)
        pos=start
        while pos<end:
            number=pos//self.step;hi=min(end,(number+1)*self.step)
            block=self.block(number);a=(pos-number*self.step)*self.columns
            result[(pos-start)*self.columns:(hi-start)*self.columns]=block[a:a+(hi-pos)*self.columns]
            pos=hi
        return result.reshape(end-start,self.columns)

    def lookup(self, rows, columns):
        out=np.empty(len(rows),dtype=self.dtype)
        for block,use,k in self.groups(rows,columns):out[use]=block[k]
        return out

    def put(self, rows, columns, values):
        for block,use,k in self.groups(rows,columns):block[k]=values[use]

    def close(self):
        while self.maps:
            _,value=self.maps.popitem();value.flush();value._mmap.close()


class TiledDetector:
    def __init__(self, model, bounds, settings, emit, folder):
        self.m=model;self.s=settings;self.emit=emit
        self.cell=lf.PRESETS[settings.get('lining_detail','detailed')]['cell'];self.radius=model['radius']
        self.t0=bounds[0]-self.cell;self.na=int(np.ceil(2*np.pi*self.radius/self.cell))
        self.nt=int(np.ceil((bounds[1]-self.t0)/self.cell))+1;self.arc=2*np.pi*self.radius/self.na
        # The halo covers Gaussian support (0.8 m), ring-fit neighbourhood and
        # tilted rings. Bolt detection uses a separate angular/axial window if
        # a fine circumference leaves insufficient axial space.
        self.halo=math.ceil(max(1.3,settings.get('lining_bolt_max_mm',350)/1000+.8)/self.cell)
        self.window_rows=max(2*self.halo+16,WINDOW_CELLS//self.na)
        self.window_rows=min(self.nt,self.window_rows,2000000//self.na)
        if self.window_rows<=2*self.halo:
            raise ValueError('Tunnel circumference is too large for the selected map resolution.')
        self.core_rows=self.window_rows-2*self.halo
        required=self.nt*self.na*(EVIDENCE.itemsize+4)
        if required>shutil.disk_usage(folder).free-50_000_000:
            raise ValueError(f'Lining analysis needs about {required/1024**3:.2f} GiB of temporary disk space. Free disk space and retry the same selection.')
        self.temp=tempfile.TemporaryDirectory(prefix='lining_tiles_',dir=folder)
        self.evidence=self.labels=None
        try:
            self.evidence=DiskRows(Path(self.temp.name)/'evidence.bin',self.nt,self.na,EVIDENCE)
            self.labels=DiskRows(Path(self.temp.name)/'labels.bin',self.nt,self.na,'<i4')
        except BaseException:self.close();raise
        self.features=[];self.features_by_id={};self.rings=[];self.longitudinal=[];self.segments=[]
        self.kind_counts={};self.next_id=4;self.threshold=0.;self.seam_threshold=0.;self.intensity_used=False;self.thresholds=[];self.seam_thresholds=[];self.windows=0

    def close(self):
        for cache in (self.evidence,self.labels):
            if cache is not None:cache.close()
        self.temp.cleanup()

    def indices(self, points):
        t,a=lf.frame(points,self.m)
        return np.clip(((t-self.t0)/self.cell).astype(np.int64),0,self.nt-1),np.minimum(self.na-1,(a*self.na/(2*np.pi)).astype(np.int64))

    def accumulate(self, points, intensity=None):
        d,ok=tunnel_lining.deviations(points,self.m);rows,cols=self.indices(points)
        inward,recess=lf.bolt_allowances(self.s)
        use=ok&(d>=-inward)&(d<=recess)
        rows,cols,d=rows[use],cols[use],d[use]
        values=np.asarray(intensity,float)[use] if intensity is not None else None
        for block,ix,k in self.evidence.groups(rows,cols):
            np.add.at(block['count'],k,1);np.add.at(block['depth'],k,d[ix])
            if values is not None:
                valid=np.isfinite(values[ix]);np.add.at(block['isum'],k[valid],values[ix][valid]);np.add.at(block['icount'],k[valid],1)

    def window(self, start, end, *, features=False):
        self.emit(f'3/5 Analysing overlapping section · {self.t0+start*self.cell:.2f} to {self.t0+end*self.cell:.2f} m')
        detector=lf.Detector(self.m,(0,1),self.s,self.emit,grid=(self.t0+start*self.cell,end-start,self.na))
        evidence=self.evidence.read(start,end)
        for key in EVIDENCE.names:setattr(detector,key,evidence[key].copy())
        del evidence
        detector.analyse(features=features,ring_only=True)
        self.windows+=1;self.thresholds.append(float(detector.threshold));self.seam_thresholds.append(float(detector.seam_threshold));self.intensity_used|=detector.intensity_used
        return detector

    def add(self, row):
        value=dict(row,id=self.next_id);self.next_id+=1
        self.kind_counts[row['kind']]=self.kind_counts.get(row['kind'],0)+1
        if row['kind'] in ('bolt_hole','bolt_fitting'):value['name']=('Bolt recess' if row['kind']=='bolt_hole' else 'Raised fitting')+f" {self.kind_counts[row['kind']]}"
        self.features.append(value);self.features_by_id[value['id']]=value
        return value

    def copy_labels(self, detector, start, mapping):
        ii,jj=np.where(detector.grid_labels>0)
        if not len(ii):return
        translate=np.zeros(detector.next_id,np.int32)
        for old,new in mapping.items():translate[old]=new
        values=translate[detector.grid_labels[ii,jj]];use=values>0
        self.labels.put(ii[use]+start,jj[use],values[use])

    def analyse(self):
        self.evidence.close()
        task=self.s.get('lining_task','all')
        # Global ring boundaries are collected first. Between-ring seams and
        # segments are then fitted once per real interval, including intervals
        # crossing any processing boundary.
        for core in range(0,self.nt,self.core_rows):
            end=min(self.nt,core+self.core_rows);start=max(0,core-self.halo);stop=min(self.nt,end+self.halo)
            detector=self.window(start,stop,features=True);mapping={}
            for row in detector.features:
                at=(row['station_m']-self.t0)/self.cell
                if not core<=at<end:continue
                if row['kind']=='ring_seam' and any(abs(row['station_m']-r['station_m'])<.35 for r in self.rings):continue
                value=self.add(row);mapping[row['id']]=value['id']
                if value['kind']=='ring_seam':self.rings.append(value)
            self.copy_labels(detector,start,mapping)
            del detector
        self.rings.sort(key=lambda r:r['station_m'])
        for i,row in enumerate(self.rings,1):row['name']=f'Ring seam {i}'
        if task in ('all','segments','seams'):
            for interval,(left,right) in enumerate(zip(self.rings[:-1],self.rings[1:]),1):
                lo=left['station_m']-np.linalg.norm(left['coefficients'][1:])
                hi=right['station_m']+np.linalg.norm(right['coefficients'][1:])
                start=max(0,math.floor((lo-self.t0)/self.cell)-self.halo)
                stop=min(self.nt,math.ceil((hi-self.t0)/self.cell)+self.halo)
                if (stop-start)*self.na<=INTERVAL_CELLS:
                    detector=self.window(start,stop)
                    detector.grid_labels=self.labels.read(start,stop)
                    detector.rings=[left,right];detector.next_id=self.next_id
                    detector.between_seams()
                    if task in ('all','segments'):detector.segment()
                    for row in detector.features:
                        row['ring_interval']=interval
                        if row['kind']=='segment':row['name']=row['name'].replace('interval 1 ',f'interval {interval} ')
                        else:row['name']=f'Between-segment seam · ring {interval} · {row["id"]}'
                        self.features.append(row);self.features_by_id[row['id']]=row
                    self.next_id=detector.next_id;self.longitudinal.extend(detector.longitudinal);self.segments.extend(detector.segments)
                    # Only newly fitted longitudinal cells; preserve existing
                    # ring/bolt labels elsewhere in the window.
                    self.copy_labels(detector,start,{r['id']:r['id'] for r in detector.longitudinal})
                    del detector
                else:self.large_interval(left,right,interval,start,stop)
        self.threshold=float(np.median(self.thresholds)) if self.thresholds else 0.
        self.seam_threshold=float(np.median(self.seam_thresholds)) if self.seam_thresholds else 0.
        self.evidence.close();self.labels.close()
        self.emit(f'4/5 Joined {self.windows} bounded analysis windows · labelling every original point')

    def large_interval(self,left,right,interval,start,stop):
        """A wide interval is streamed too; no assumed maximum ring spacing.

        Hypotheses use a bounded deterministic sample; full measured candidate
        cells validate support, refine the fit and receive labels in chunks.
        """
        path=Path(self.temp.name)/'interval.bin';dtype=np.dtype([('t','<f8'),('a','<f8'),('i','<i8'),('j','<i8'),('label','<i4')])
        band=max(.025,1.5*self.cell);span=right['station_m']-left['station_m'];mid=(left['station_m']+right['station_m'])/2
        total=0
        with path.open('wb') as out:
            for core in range(start,stop,self.core_rows):
                end=min(stop,core+self.core_rows);a0=max(0,core-self.halo);b0=min(self.nt,end+self.halo)
                d=self.window(a0,b0);known=self.labels.read(a0,b0)
                ii,jj=np.where((d.seam_geom|d.dark)&(known==0));tt=d.t0+(ii+.5)*self.cell;aa=(jj+.5)*2*np.pi/self.na
                use=(ii+a0>=core)&(ii+a0<end)&(tt>lf.ring_at(left['coefficients'],aa)+2*band)&(tt<lf.ring_at(right['coefficients'],aa)-2*band)
                values=np.zeros(int(use.sum()),dtype=dtype);values['t']=tt[use];values['a']=aa[use];values['i']=ii[use]+a0;values['j']=jj[use]
                values.tofile(out);total+=len(values)
                del d,known
        if total<10:
            self._finish_interval(left,right,interval)
            path.unlink()
            return
        def chunks(writable=False):
            for first in range(0,total,25000):
                self.emit(f'4/5 Checking full seam support · ring interval {interval}')
                block=np.memmap(path,dtype=dtype,mode='r+' if writable else 'r',offset=first*dtype.itemsize,shape=(min(25000,total-first),))
                try:yield block
                finally:block._mmap.close()
        rng=np.random.default_rng(143);circ=2*np.pi*self.radius
        for attempt in range(24):
            sample=[];stride=max(1,math.ceil(total/20000));offset=0
            for b in chunks():
                take=np.arange((-offset)%stride,len(b),stride);take=take[b['label'][take]==0]
                sample.append(np.c_[b['t'][take],b['a'][take]]);offset+=len(b)
            q=np.concatenate(sample);del sample
            if len(q)<10:break
            tt=q[:,0];ss=q[:,1]*self.radius;best=None;bestscore=0
            for _ in range(lf.PRESETS[self.s.get('lining_detail','detailed')]['trials']):
                p,r=rng.choice(len(q),2,replace=False);dt=tt[r]-tt[p]
                if abs(dt)<span*.25:continue
                slope=lf.angular_delta(ss[r]/self.radius,ss[p]/self.radius)*self.radius/dt
                if abs(slope)>1:continue
                intercept=(ss[p]-slope*(tt[p]-mid))%circ
                residual=lf.angular_delta(q[:,1],(intercept+slope*(tt-mid))/self.radius)*self.radius
                hit=abs(residual)<=band
                bins=len(np.unique(np.floor((tt[hit]-left['station_m'])/span*10).astype(int)))
                score=bins+min(hit.sum(),1000)/1000
                if bins>=6 and hit.sum()>=8 and lf.ridge_contrast(residual,band)>=2. and score>bestscore:best=(slope,intercept);bestscore=score
            if best is None:break
            slope,intercept=best;stats=np.zeros(6);bins=set()
            for b in chunks():
                x=b['t']-mid;res=lf.angular_delta(b['a'],(intercept+slope*x)/self.radius)*self.radius
                use=(b['label']==0)&(abs(res)<=band);xx=x[use];yy=intercept+slope*xx+res[use]
                stats+=np.array([len(xx),xx.sum(),yy.sum(),xx@xx,xx@yy,yy@yy])
                bins.update(np.floor((b['t'][use]-left['station_m'])/span*10).astype(int).tolist())
            n,sx,sy,sxx,sxy,syy=stats;den=n*sxx-sx*sx
            if n<8 or len(bins)<6 or den<=1e-12:break
            slope=float((n*sxy-sx*sy)/den);raw_intercept=float((sy-slope*sx)/n);intercept=raw_intercept%circ
            if abs(slope)>1:break
            # Revalidate the refined curve against every measured candidate,
            # including prior fitted neighbours in the flank density. A
            # uniform/broad patch must not yield a succession of fake seams.
            centre=flanks=support=0;ssr=0.;bins=set()
            for b in chunks():
                residual=lf.angular_delta(b['a'],(intercept+slope*(b['t']-mid))/self.radius)*self.radius
                distance=abs(residual);centre+=int(np.count_nonzero(distance<=band))
                flanks+=int(np.count_nonzero((distance>=2*band)&(distance<=4*band)))
                use=(b['label']==0)&(distance<=band);support+=int(use.sum());ssr+=float(residual[use]@residual[use])
                bins.update(np.floor((b['t'][use]-left['station_m'])/span*10).astype(int).tolist())
            contrast=2.*centre/(flanks+4.)
            if support<8 or len(bins)<6 or contrast<2.:
                for b in chunks(True):
                    residual=lf.angular_delta(b['a'],(intercept+slope*(b['t']-mid))/self.radius)*self.radius
                    b['label'][(b['label']==0)&(abs(residual)<=band)]=-1
                continue
            rms=math.sqrt(ssr/support)
            row=self.add(dict(kind='longitudinal_seam',name=f'Between-segment seam · ring {interval} · {attempt+1}',status='candidate',ring_interval=interval,station_m=mid,arc_intercept_m=intercept,arc_slope=slope,coverage_fraction=min(1,len(bins)/10),support_cells=int(support),fit_rms_m=rms,ridge_contrast=float(contrast),evidence='Bounded hypotheses; full-density measured-cell validation, adjacent-flank contrast and streamed least-squares refinement'))
            ends=np.array([left['station_m'],right['station_m']])
            for _ in range(5):
                angles=(intercept+slope*(ends-mid))/self.radius;ends=np.array([lf.ring_at(left['coefficients'],angles[0]),lf.ring_at(right['coefficients'],angles[1])])
            ts=np.linspace(*ends,33);row['points']=lf.world(ts,(intercept+slope*(ts-mid))/self.radius,self.m).tolist();self.longitudinal.append(row)
            for b in chunks(True):
                residual=lf.angular_delta(b['a'],(intercept+slope*(b['t']-mid))/self.radius)*self.radius
                use=(b['label']==0)&(abs(residual)<=band);b['label'][use]=row['id'];self.labels.put(b['i'][use],b['j'][use],b['label'][use])
        self._finish_interval(left,right,interval)
        path.unlink()

    def _finish_interval(self,left,right,interval):
        if self.s.get('lining_task','all') in ('all','segments'):
            # Reuse the existing boundary/crossing checks with a tiny carrier.
            d=lf.Detector(self.m,(0,1),self.s,self.emit,grid=(self.t0,1,self.na));d.rings=[left,right];d.next_id=self.next_id
            local=[dict(r,ring_interval=1) for r in self.longitudinal if r['ring_interval']==interval];d.longitudinal=local;d.segment()
            for row in local:
                for key in ('segment_boundary_status','conflicting_boundary_ids'):
                    if key in row:self.features_by_id[row['id']][key]=row[key]
            for row in d.features:row['ring_interval']=interval;row['name']=row['name'].replace('interval 1 ',f'interval {interval} ');self.features.append(row);self.features_by_id[row['id']]=row
            self.next_id=d.next_id;self.segments.extend(d.segments)

    def classify(self, points):
        d,ok=tunnel_lining.deviations(points,self.m);rows,cols=self.indices(points)
        labels=np.zeros(len(points),np.int32);labels[~ok]=3
        inward,recess=lf.bolt_allowances(self.s)
        labels[ok&(d < -inward)]=1;labels[ok&(d>recess)]=2
        t,a=lf.frame(points,self.m);order=np.argsort(t);sorted_t=t[order]
        for row,left,right,first,second in self.segments:
            lo=left['station_m']-np.linalg.norm(left['coefficients'][1:]);hi=right['station_m']+np.linalg.norm(right['coefficients'][1:])
            ix=order[np.searchsorted(sorted_t,lo):np.searchsorted(sorted_t,hi,side='right')]
            if not len(ix):continue
            tt,aa=t[ix],a[ix];start=(first['arc_intercept_m']+first['arc_slope']*(tt-first['station_m']))/self.radius;end=(second['arc_intercept_m']+second['arc_slope']*(tt-second['station_m']))/self.radius
            use=(labels[ix]==0)&(tt>lf.ring_at(left['coefficients'],aa))&(tt<lf.ring_at(right['coefficients'],aa))&((aa-start)%(2*np.pi)<(end-start)%(2*np.pi))
            labels[ix[use]]=row['id']
        feature=self.labels.lookup(rows,cols);use=ok&(d>=-inward)&(d<=recess)&(feature>0)
        labels[use]=feature[use];return labels


def bounded_bolts(detector):
    """Bound dense connectivity searches in both station and periodic angle.

    Halo exceeds the largest accepted bolt diameter plus search radius. An
    accepted component owned by a core therefore cannot be clipped by a tile.
    """
    d=detector;maxdiam=d.s.get('lining_bolt_max_mm',350)/1000
    halo_m=maxdiam+4*max(d.cell,d.arc)
    hi=math.ceil(halo_m/d.cell);hj=math.ceil(halo_m/d.arc)
    si=max(1,math.floor(max(1.,maxdiam*2)/d.cell));sj=max(1,math.floor(max(1.,maxdiam*2)/d.arc))
    for ti in range(0,d.nt,si):
        te=min(d.nt,ti+si);a=max(0,ti-hi);b=min(d.nt,te+hi)
        for aj in range(d.na,2*d.na,sj):
            ae=min(2*d.na,aj+sj);cols=np.arange(aj-hj,ae+hj)
            ii,jj=np.where(getattr(d,'bolt_geom',d.geom)[a:b][:,cols%d.na]);ii+=a;jj=cols[jj]
            d.emit('3/5 Grouping dense bolt-feature evidence in overlapping windows')
            d.bolt_components(ii,jj,owner=(ti,te,aj,ae))
