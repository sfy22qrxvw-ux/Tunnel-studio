# Copyright (c) 2026 Damian Gulczynski.
"""Evidence-led lining features. Independent adaptations, not author code/weights.
See METHODS_0_14_3.txt. Coordinates in metres; all selected original IDs labelled.
"""
import time
from contextlib import ExitStack
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter, convolve
from scipy.signal import find_peaks
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
import tunnel_lining
from combined_denoising import SAMPLER_VERSION, sample_originals

PRESETS={'fast':dict(cell=.04,cap=80000,spacing=.04,trials=80),
         'detailed':dict(cell=.02,cap=160000,spacing=.02,trials=180),
         'more_detailed':dict(cell=.01,cap=280000,spacing=.01,trials=320)}
REFERENCES=['10.3390/rs14194791','10.1080/17538947.2025.2528627','10.3390/s26154901']

def bolt_allowances(settings):
    inward=settings.get('lining_inward_mm',60)/1000
    recess=settings.get('lining_recess_mm',250)/1000
    maximum=settings.get('lining_bolt_height_mm',0)/1000
    if settings.get('lining_bolt_kind','recess')=='raised':inward=max(inward,maximum)
    else:recess=max(recess,maximum)
    return inward,recess


def frame(points,m):
    q=np.asarray(points,float)-m['reference'];t=q@m['axis'];n=m['nodes']
    x=q@m['u']-np.interp(t,n[:,0],n[:,1]);y=q@m['v']-np.interp(t,n[:,0],n[:,2])
    return t,np.mod(np.arctan2(y,x),2*np.pi)

def world(t,a,m):
    t,a=np.broadcast_arrays(t,a);n=m['nodes'];j=np.minimum(m['nangle']-1,(np.mod(a,2*np.pi)*m['nangle']/(2*np.pi)).astype(int))
    radius=np.interp(t,n[:,0],n[:,3])+m['profile'][j]
    x=np.interp(t,n[:,0],n[:,1])+radius*np.cos(a);y=np.interp(t,n[:,0],n[:,2])+radius*np.sin(a)
    return m['reference']+t[...,None]*m['axis']+x[...,None]*m['u']+y[...,None]*m['v']

def ring_at(coef,a):return coef[0]+coef[1]*np.cos(a)+coef[2]*np.sin(a)
def angular_delta(a,b):return (a-b+np.pi)%(2*np.pi)-np.pi

def ridge_contrast(residual,band):
    """Candidate density on a seam compared with two parallel flank bands.

    The centre band is 2*band wide and the flanks together are 4*band wide.
    Uniform/broad candidate patches therefore score about one, rather than
    becoming arbitrarily many RANSAC seams. The four-cell pseudocount avoids
    claiming high contrast from a handful of isolated returns.
    """
    distance=np.abs(residual)
    centre=np.count_nonzero(distance<=band)
    flanks=np.count_nonzero((distance>=2*band)&(distance<=4*band))
    return 2.*centre/(flanks+4.)


def detection_summary(detector,task,occupied=None):
    """Explain absent/partial geometry without presenting a completed layout."""
    rows=[row for row,*_ in detector.segments if occupied is None or occupied[row['id']]>0]
    bands={band:sum(row['segment_confidence']==band for row in rows) for band in ('high','medium','low')}
    rings=len(detector.rings);joints=len(detector.longitudinal);panels=len(rows)
    pockets=sum(row['kind']=='bolt_hole' for row in detector.features)
    fittings=sum(row['kind']=='bolt_fitting' for row in detector.features)
    ambiguous=sum(row.get('segment_boundary_status')=='ambiguous' for row in detector.longitudinal)
    intervals=max(0,rings-1)
    supported=len({row['ring_interval'] for row in rows})
    unoccupied=len(detector.segments)-panels
    result=dict(task=task,unoccupied_panel_sectors=unoccupied,segment_confidence_counts=bands,ring_boundaries=rings,longitudinal_boundaries=joints,segment_candidates=panels,
                bolt_recess_candidates=pockets,raised_fitting_candidates=fittings,observed_ring_intervals=intervals,
                intervals_with_segments=supported,ambiguous_segment_boundaries=ambiguous)
    if task in ('all','segments'):
        if not panels:
            reason=('The proposed sectors contain no classified lining points.' if unoccupied else
                    'At least two supported ring boundaries are needed.' if rings<2 else
                    'No between-segment boundaries passed the evidence checks.' if not joints else
                    'The detected boundaries do not enclose unambiguous panels.')
            result.update(status='no_segments',summary=f'No supported segment layout: {rings} ring-boundary and {joints} between-segment candidates. {reason} Original points remain available for review.')
        else:
            incomplete=bool(ambiguous or unoccupied or supported<intervals)
            summary=f'{panels} panels in {supported} of {intervals} observed ring intervals. {bands["high"]} measured on all sides, {bands["medium"]} with one inferred side, {bands["low"]} with two inferred sides.'
            if unoccupied:summary+=f' {unoccupied} empty sector(s) have been omitted.'
            if ambiguous or supported<intervals:summary+=' Other intervals or conflicting boundaries remain unassigned.'
            result.update(status='partial' if incomplete else 'candidates',summary=summary)
    elif task=='bolts':
        number=fittings if detector.s.get('lining_bolt_kind','recess')=='raised' else pockets
        feature='raised fitting' if detector.s.get('lining_bolt_kind','recess')=='raised' else 'recessed bolt pocket'
        result.update(status='candidates' if number else 'no_candidates',summary=f'{number} compact {feature} candidates. Review the measured point groups before removal; they are geometric candidates, not identified hardware.' if number else f'No supported {feature} candidates met the depth/height, size and measured-support checks. Review scan spacing and local roughness; no bolt layout was assumed.')
    elif task=='seams':
        result.update(status='candidates' if rings or joints else 'no_candidates',summary=f'{rings} ring-boundary and {joints} between-segment candidates. Missing or insufficiently supported joints remain undetected.')
    else:result.update(status='review',summary='Review lining support and non-lining candidates before applying exclusions.')
    return result

class Detector:
    def __init__(self,model,bounds,settings,emit,*,grid=None):
        self.m=model;self.s=settings;self.emit=emit;self.preset=PRESETS[settings.get('lining_detail','detailed')]
        self.cell=self.preset['cell'];self.radius=model['radius'];self.t0=bounds[0]-self.cell
        self.na=int(np.ceil(2*np.pi*self.radius/self.cell));self.nt=int(np.ceil((bounds[1]-self.t0)/self.cell))+1
        if grid is not None:self.t0,self.nt,self.na=grid
        if self.na*self.nt>2000000:raise ValueError('Internal lining window exceeds its working-memory budget.')
        self.shape=(self.nt,self.na);self.arc=2*np.pi*self.radius/self.na
        self.count=np.zeros(self.shape,np.int64);self.depth=np.zeros(self.shape);self.isum=np.zeros(self.shape);self.icount=np.zeros(self.shape,np.int64)
        self.features=[];self.rings=[];self.longitudinal=[];self.segments=[];self.grid_labels=np.zeros(self.shape,np.int32)
        self.next_id=4;self.features_by_id={}
    def indices(self,p):
        t,a=frame(p,self.m);return np.clip(((t-self.t0)/self.cell).astype(int),0,self.nt-1),np.minimum(self.na-1,(a*self.na/(2*np.pi)).astype(int))
    def accumulate(self,p,intensity=None):
        d,ok=tunnel_lining.deviations(p,self.m);i,j=self.indices(p)
        # Preserve deep recesses; internal facilities cannot contribute seam evidence.
        inward,recess=bolt_allowances(self.s)
        use=ok&(d>=-inward)&(d<=recess)
        k=i[use]*self.na+j[use];np.add.at(self.count.ravel(),k,1);np.add.at(self.depth.ravel(),k,d[use])
        if intensity is not None:
            v=np.asarray(intensity,float);use&=np.isfinite(v);k=i[use]*self.na+j[use]
            np.add.at(self.isum.ravel(),k,v[use]);np.add.at(self.icount.ravel(),k,1)
    def add(self,kind,name,**kw):
        row=dict(id=self.next_id,kind=kind,name=name,status='candidate',**kw);self.next_id+=1;self.features.append(row);self.features_by_id[row['id']]=row;return row
    def analyse(self,*,features=True,ring_only=False):
        self.emit('3/5 Measuring local wall departures and intensity evidence')
        valid=self.count>0;self.depth=np.divide(self.depth,np.maximum(self.count,1));mode=('nearest','wrap')
        sigma=(.20/self.cell,.20/self.arc)
        weight=gaussian_filter(valid.astype(float),sigma,mode=mode)
        base=gaussian_filter(np.clip(self.depth,-.02,.02)*valid,sigma,mode=mode)/np.maximum(weight,1e-10)
        self.residual=self.depth-base
        quiet=self.residual[valid&(abs(self.residual)<.025)]
        mad=1.4826*np.median(abs(quiet-np.median(quiet))) if len(quiet) else .01
        self.threshold=max(self.s.get('lining_depth_mm',12)/1000,4*mad)
        self.geom=valid&(weight>.04)&(self.residual>self.threshold)
        self.bolt_sign=-1 if self.s.get('lining_bolt_kind','recess')=='raised' else 1
        feature_depth=self.residual*self.bolt_sign
        self.bolt_geom=valid&(weight>.04)&(feature_depth>self.threshold)
        maximum=self.s.get('lining_bolt_height_mm',0)/1000
        if maximum:self.bolt_geom&=feature_depth<=maximum
        # Shallow lining joints and deep bolt pockets are different features.
        # Keep their user thresholds separate; both retain the same measured
        # four-MAD noise gate and require local occupancy support.
        self.seam_threshold=max(self.s.get('lining_seam_depth_mm',4)/1000,4*mad)
        self.seam_geom=valid&(weight>.04)&(self.residual>self.seam_threshold)
        iv=self.icount>0;intensity=self.isum/np.maximum(self.icount,1)
        iw=gaussian_filter(iv.astype(float),sigma,mode=mode)
        avg=gaussian_filter(intensity*iv,sigma,mode=mode)/np.maximum(iw,1e-10)
        diff=intensity-avg;good=diff[iv];scale=1.4826*np.median(abs(good-np.median(good))) if len(good) else 0
        # Quantized/flat lining intensities can have zero MAD even with a
        # strongly contrasting measured seam. Zero MAD is not evidence that
        # intensity is absent. A small relative contrast floor keeps such
        # seams usable while constant values (including smoothing roundoff)
        # still produce no candidates. All contrasts remain two-sided below.
        measured=intensity[iv]
        span=float(np.ptp(measured)) if len(measured) else 0.
        numerical=max(1e-12,float(np.max(abs(measured)))*np.finfo(float).eps*64) if len(measured) else 1e-12
        usable=span>numerical
        scale=max(float(scale),span/200.,numerical) if usable else 0.
        # Require measured neighbours on BOTH sides of a dark feature. Missing
        # cells and occlusions never become seam evidence.
        self.dark=np.zeros(self.shape,bool)
        if usable:
            step=max(2,round(.10/self.cell));astep=max(2,round(.10/self.arc))
            axial=np.zeros(self.shape,bool)
            if self.nt>2*step:axial[step:-step]=iv[:-2*step]&iv[2*step:]&(intensity[step:-step]<intensity[:-2*step]-3*scale)&(intensity[step:-step]<intensity[2*step:]-3*scale)
            around=np.roll(iv,astep,1)&np.roll(iv,-astep,1)&(intensity<np.roll(intensity,astep,1)-3*scale)&(intensity<np.roll(intensity,-astep,1)-3*scale)
            self.dark=iv&(iw>.04)&(diff < -3*scale)&(axial|around)
        self.intensity_used=bool(usable)
        if not features:return
        task=self.s.get('lining_task','all')
        if task!='non_lining':
            if task in ('all','bolts'):self.bolts()
            if task in ('all','seams','segments'):self.seams(ring_only=ring_only)
            if task in ('all','segments') and not ring_only:self.segment()
    def bolts(self):
        self.emit('3/5 Grouping compact '+('raised fittings' if self.s.get('lining_bolt_kind','recess')=='raised' else 'recessed bolt pockets')+' · density connectivity')
        # Euclidean DBSCAN of measured cell centres; radius follows observed
        # map occupancy so a fine map does not split sparsely sampled seams.
        if np.count_nonzero(self.bolt_geom)*3>450000:
            from lining_tiles import bounded_bolts
            return bounded_bolts(self)
        ii,jj=np.where(np.tile(self.bolt_geom,(1,3)))
        self.bolt_components(ii,jj)
    def bolt_components(self,ii,jj,owner=None):
        if len(ii)<3:return
        q=np.c_[ii*self.cell,jj*self.arc];tree=cKDTree(q)
        eps=max(self.cell,self.arc)*min(4.,1.8/np.sqrt(max(.05,float((self.count>0).mean()))))
        core=tree.query_ball_point(q,eps,return_length=True)>=3;ci=np.flatnonzero(core)
        if not len(ci):return
        ct=cKDTree(q[ci]);pairs=ct.query_pairs(eps,output_type='ndarray')
        graph=coo_matrix((np.ones(len(pairs)),(pairs[:,0],pairs[:,1])),shape=(len(ci),len(ci)))
        _,groups=connected_components(graph,directed=False)
        distance,nearest=ct.query(q);assigned=np.where(distance<=eps,groups[nearest],-1)
        maxdiam=self.s.get('lining_bolt_max_mm',350)/1000
        order=np.argsort(assigned,kind='stable');cuts=np.r_[0,np.flatnonzero(np.diff(assigned[order]))+1,len(order)]
        for number,(lo,hi) in enumerate(zip(cuts[:-1],cuts[1:])):
            if number%100==0:self.emit('3/5 Checking bolt-feature size and compactness')
            ids=order[lo:hi]
            if assigned[ids[0]]<0:continue
            iii,jjj=ii[ids],jj[ids]
            if not self.na<=np.mean(jjj)<2*self.na:continue
            # Local names keep the downstream coordinates simple.
            ti,aj=iii,jjj
            t=(ti+.5)*self.cell+self.t0;s=(aj-self.na+.5)*self.arc
            width=np.ptp(t)+self.cell;height=np.ptp(s)+self.arc
            if len(ti)<3 or max(width,height)>maxdiam or min(width,height)<self.cell or max(width,height)/max(min(width,height),1e-9)>3.5:continue
            fill=len(ti)*self.cell*self.arc/(width*height)
            if fill<.08:continue
            if self.count[ti,aj%self.na].sum()<self.s.get('lining_bolt_min_points',0):continue
            # Gaussian mean shift within the detected component, not a design template.
            q=np.c_[t,s];c=np.median(q,axis=0);band=max(width,height)/2
            for _ in range(12):
                w=np.exp(-np.sum((q-c)**2,axis=1)/(2*band*band));new=(q*w[:,None]).sum(0)/w.sum()
                if np.linalg.norm(new-c)<1e-6:break
                c=new
            if owner is not None:
                ti0,ti1,aj0,aj1=owner
                if not (ti0<=((c[0]-self.t0)/self.cell-.5)<ti1 and aj0<=c[1]/self.arc+self.na-.5<aj1):continue
            raised=self.s.get('lining_bolt_kind','recess')=='raised'
            kind='bolt_fitting' if raised else 'bolt_hole';label='Raised fitting' if raised else 'Bolt recess'
            a=float(c[1]/self.radius%(2*np.pi));row=self.add(kind,f'{label} {sum(f['kind']==kind for f in self.features)+1}',station_m=float(c[0]),angle_deg=float(np.degrees(a)),width_m=float(width),height_m=float(height),depth_m=float(abs(np.median(self.residual[ti,aj%self.na]))),feature_side='raised' if raised else 'recess',support_cells=len(ti),centre=world(c[0],a,self.m).tolist(),centre_kind='Projected lining centre; point_centroid is the measured group centroid',evidence='Compact measured '+('bump towards the tunnel interior' if raised else 'pocket behind the surrounding lining')+'; density-connected measured cells; Gaussian mean-shift centre')
            self.grid_labels[ti,aj%self.na]=row['id']
    def seams(self,*,ring_only=False):
        self.emit('4/5 Locating ring and between-segment seams')
        candidate=(self.seam_geom|self.dark)&(self.grid_labels==0);ii,jj=np.where(candidate)
        if len(ii)<12:return
        t=self.t0+(ii+.5)*self.cell;a=(jj+.5)*2*np.pi/self.na
        # Each angular sector contributes at most one vote per station bin.
        coarse=36;sector=np.minimum(coarse-1,(a*coarse/(2*np.pi)).astype(int))
        votes=np.zeros((self.nt,coarse),bool);votes[ii,sector]=True
        score=convolve(votes.astype(float),np.ones((max(1,round(.08/self.cell)),1)),mode='constant').clip(0,1).sum(1)/coarse
        peaks,_=find_peaks(score,height=.25,distance=max(1,int(.35/self.cell)),prominence=.10)
        rng=np.random.default_rng(143);band=max(.025,1.5*self.cell)
        for ix in peaks[np.argsort(score[peaks])[::-1]]:
            self.emit('4/5 Fitting supported circumferential seam')
            seed=self.t0+(ix+.5)*self.cell;use=abs(t-seed)<.35
            tt=t[use];aa=a[use]
            if len(tt)<12:continue
            # t = c + b cos(theta) + d sin(theta): tilted ring plane in cylinder frame.
            X=np.c_[np.ones(len(aa)),np.cos(aa),np.sin(aa)];best=None;bestscore=0
            for _ in range(self.preset['trials']):
                take=rng.choice(len(tt),3,replace=False)
                if np.linalg.cond(X[take])>100:continue
                coef=np.linalg.solve(X[take],tt[take])
                if np.linalg.norm(coef[1:])>.45:continue
                residual=tt-X@coef;inside=abs(residual)<=band;coverage=len(np.unique((aa[inside]*coarse/(2*np.pi)).astype(int)))/coarse
                if ridge_contrast(residual,band)<2.:continue
                value=coverage+min(1,inside.sum()/max(1,len(tt)))*.1
                if coverage>=.40 and value>bestscore:bestscore=value;best=(coef,inside,coverage)
            if best is None:continue
            coef,inside,coverage=best;coef=np.linalg.lstsq(X[inside],tt[inside],rcond=None)[0]
            residual=tt-X@coef;inside=abs(residual)<=band
            coverage=len(np.unique((aa[inside]*coarse/(2*np.pi)).astype(int)))/coarse
            contrast=ridge_contrast(residual,band)
            if np.linalg.norm(coef[1:])>.45 or coverage<.40 or inside.sum()<12 or contrast<2.:continue
            if any(abs(coef[0]-r['coefficients'][0])<.35 for r in self.rings):continue
            residual=residual[inside]
            row=self.add('ring_seam',f'Ring seam {len(self.rings)+1}',coefficients=coef.tolist(),station_m=float(coef[0]),coverage_fraction=float(coverage),fit_rms_m=float(np.sqrt(np.mean(residual**2))),support_cells=int(inside.sum()),ridge_contrast=float(contrast),evidence='Measured recess / local intensity valley across circumference; robust tilted-plane fit with evidence above adjacent flanks')
            angles=np.linspace(0,2*np.pi,129);row['points']=world(ring_at(coef,angles),angles,self.m).tolist();self.rings.append(row)
            chosen=abs(t-ring_at(coef,a))<=band;self.grid_labels[ii[chosen],jj[chosen]]=row['id']
        self.rings.sort(key=lambda r:r['station_m'])
        for number,r in enumerate(self.rings,1):r['name']=f'Ring seam {number}'
        if not ring_only:self.between_seams(ii,jj,t,a,rng,band)
    def between_seams(self,ii=None,jj=None,t=None,a=None,rng=None,band=None):
        if ii is None:
            ii,jj=np.where((self.seam_geom|self.dark)&(self.grid_labels==0))
            t=self.t0+(ii+.5)*self.cell;a=(jj+.5)*2*np.pi/self.na
            rng=np.random.default_rng(143);band=max(.025,1.5*self.cell)
        for rindex,(left,right) in enumerate(zip(self.rings[:-1],self.rings[1:]),1):
            lo=ring_at(left['coefficients'],a);hi=ring_at(right['coefficients'],a)
            use=(t>lo+band*2)&(t<hi-band*2)&(self.grid_labels[ii,jj]==0)
            ix=np.flatnonzero(use);span=right['station_m']-left['station_m'];mid=(right['station_m']+left['station_m'])/2
            if span<.4 or len(ix)<10:continue
            pool_t=t[ix].copy();pool_a=a[ix].copy()
            # Bounded RANSAC on periodically unwrapped arc coordinates detects
            # sloping key-segment joints, independently within each ring.
            for attempt in range(24):
                if len(ix)<10:break
                self.emit(f'4/5 Between-segment seams · ring interval {rindex}')
                tt=t[ix];ss=a[ix]*self.radius;best=None;bestscore=0
                for _ in range(self.preset['trials']):
                    p,q=rng.choice(len(ix),2,replace=False);dt=tt[q]-tt[p]
                    if abs(dt)<span*.25:continue
                    delta=angular_delta(ss[q]/self.radius,ss[p]/self.radius)*self.radius;slope=delta/dt
                    if abs(slope)>1.:continue
                    intercept=(ss[p]-slope*(tt[p]-mid))%(2*np.pi*self.radius)
                    residual=angular_delta(ss/self.radius,(intercept+slope*(tt-mid))/self.radius)*self.radius
                    hit=abs(residual)<=band;nbins=len(np.unique(np.floor((tt[hit]-left['station_m'])/span*10).astype(int)))
                    if nbins<6 or hit.sum()<8 or ridge_contrast(residual,band)<2.:continue
                    value=nbins+min(hit.sum(),1000)/1000
                    if value>bestscore:bestscore=value;best=(slope,intercept,hit)
                if best is None:break
                slope,intercept,hit=best;xx=tt[hit]-mid;yy=angular_delta(a[ix[hit]],intercept/self.radius)*self.radius+intercept
                fit=np.polyfit(xx,yy,1);slope=float(fit[0]);intercept=float(fit[1]%(2*np.pi*self.radius))
                residual=angular_delta(a[ix],(intercept+slope*(tt-mid))/self.radius)*self.radius
                hit=abs(residual)<=band
                nbins=len(np.unique(np.floor((tt[hit]-left['station_m'])/span*10).astype(int)))
                contrast=ridge_contrast(angular_delta(pool_a,(intercept+slope*(pool_t-mid))/self.radius)*self.radius,band)
                if abs(slope)>1. or nbins<6 or hit.sum()<8 or contrast<2.:
                    ix=ix[~hit];continue
                row=self.add('longitudinal_seam',f'Between-segment seam · ring {rindex} · {len([r for r in self.longitudinal if r["ring_interval"]==rindex])+1}',ring_interval=rindex,station_m=float(mid),arc_intercept_m=intercept,arc_slope=slope,coverage_fraction=min(1.,nbins/10),support_cells=int(hit.sum()),fit_rms_m=float(np.sqrt(np.mean(residual[hit]**2))),ridge_contrast=float(contrast),evidence='Measured recess / intensity valley supported along ring; robust inclined line fit with evidence above adjacent flanks')
                # Stop at actual tilted ring intersections.
                ends=np.array([left['station_m'],right['station_m']],float)
                for _ in range(5):
                    angles=(intercept+slope*(ends-mid))/self.radius
                    ends=np.array([ring_at(left['coefficients'],angles[0]),ring_at(right['coefficients'],angles[1])])
                ts=np.linspace(*ends,33);row['points']=world(ts,(intercept+slope*(ts-mid))/self.radius,self.m).tolist();self.longitudinal.append(row)
                chosen=ix[hit];self.grid_labels[ii[chosen],jj[chosen]]=row['id'];ix=ix[~hit]
    def segment(self):
        from segment_panels import build
        build(self, world, ring_at)
    def classify(self,p):
        d,ok=tunnel_lining.deviations(p,self.m);i,j=self.indices(p)
        labels=np.zeros(len(p),np.int32);labels[~ok]=3
        inward,recess=bolt_allowances(self.s)
        labels[ok&(d < -inward)]=1;labels[ok&(d>recess)]=2
        t,a=frame(p,self.m);lining=labels==0
        for row,left,right,first,second in self.segments:
            lo=ring_at(left['coefficients'],a);hi=ring_at(right['coefficients'],a)
            start=(first['arc_intercept_m']+first['arc_slope']*(t-first['station_m']))/self.radius
            end=(second['arc_intercept_m']+second['arc_slope']*(t-second['station_m']))/self.radius
            use=lining&(t>lo)&(t<hi)&((a-start)%(2*np.pi)<(end-start)%(2*np.pi));labels[use]=row['id']
        feature=self.grid_labels[i,j];use=lining&(feature>0);labels[use]=feature[use]
        return labels


def run_batches(batches,ids_path,labels_path,settings,progress,cancel,selected_total,intensity_for_ids=None):
    started=time.monotonic()
    def emit(msg):tunnel_lining._emit(progress,cancel,msg)
    cfg=PRESETS[settings.get('lining_detail','detailed')];emit('1/5 Learning a supported lining reference')
    sample,_=sample_originals(batches,cfg['spacing'],cfg['cap'],progress,cancel)
    m=tunnel_lining.fit_model(sample,{**settings,'envelope_thickness':.04,'envelope_angle_deg':1.5,'resolution':.2},progress,cancel)
    m=tunnel_lining.longitudinal_reference(sample,m)
    if settings.get('lining_task')=='departures':
        result=__import__('lining_departures').run_batches(batches,ids_path,labels_path,settings,m,progress,cancel,selected_total)
        result['sampler_version']=SAMPLER_VERSION
        return result
    lo=np.inf;hi=-np.inf
    for _,p,_ in batches():
        emit('2/5 Measuring analysis extent');t,_=frame(p,m);lo=min(lo,t.min());hi=max(hi,t.max())
    with ExitStack() as resources:
        na=int(np.ceil(2*np.pi*m['radius']/cfg['cell']))
        nt=int(np.ceil((hi-lo+cfg['cell'])/cfg['cell']))+1
        if na*nt>2000000:
            from lining_tiles import TiledDetector
            detector=TiledDetector(m,(lo,hi),settings,emit,Path(ids_path).parent)
            resources.callback(detector.close)
        else:detector=Detector(m,(lo,hi),settings,emit)
        for ids,p,_ in batches():
            for start in range(0,len(ids),25000):
                emit('2/5 Unwrapping all original points');ix=ids[start:start+25000];detector.accumulate(p[start:start+25000],intensity_for_ids(ix) if intensity_for_ids else None)
        detector.analyse();n=detector.next_id;counts=np.zeros(n,np.int64);sums=np.zeros((n,3));reps={i:[] for i in range(n)};written=0
        with open(ids_path,'wb') as out,open(labels_path,'wb') as labs:
            for ids,p,_ in batches():
                for start in range(0,len(ids),25000):
                    ix=np.asarray(ids[start:start+25000]);q=np.asarray(p[start:start+25000],float);emit(f'5/5 Labelling original points · {written+len(ix):,}/{selected_total:,}')
                    labels=detector.classify(q);ix.astype('<i8').tofile(out);labels.astype('<i4').tofile(labs)
                    counts+=np.bincount(labels,minlength=n)
                    for axis in range(3):sums[:,axis]+=np.bincount(labels,weights=q[:,axis],minlength=n)
                    for k in np.unique(labels):reps[k].extend((written+np.flatnonzero(labels==k)[:max(0,16-len(reps[k]))]).tolist())
                    written+=len(ix)
        if written!=selected_total:raise ValueError('The scan selection changed during lining analysis.')
        cleaning=settings.get('lining_task')=='non_lining';names=['Lining / unassigned regions','Interior non-lining candidates','Outside recess allowance · review','Uncertain support · retained']
        kept=[i for i in range(n) if counts[i] and (not cleaning or i not in (1,2))];rows=[]
        for k in range(n):
            if not counts[k]:continue
            feature=detector.features_by_id.get(k,{})
            rows.append(dict(id=k,name=feature.get('name',names[k] if k<4 else str(k)),feature_kind=feature.get('kind','background'),points=int(counts[k]),centre=(sums[k]/counts[k]).tolist(),preview_indices=reps[k],automatic_keep=k in kept,supervoxels=0,linearity=0.,planar_fraction=0.,overlap=0.))
            if feature.get('kind')=='segment':
                for key in ('panel_number','ring_interval','inferred','segment_confidence','confidence_score','label_centre','boundary_sources'):
                    rows[-1][key]=feature[key]
                rows[-1]['panel_outline']=feature.pop('panel_outline')
            if feature:feature['original_points']=int(counts[k]);feature['point_centroid']=(sums[k]/counts[k]).tolist()
        features=[feature for feature in detector.features if feature['kind']!='segment' or counts[feature['id']]>0]
        warnings=['Candidate features require review on the original scan. No published accuracy or field validation is claimed.',
                  'One near-circular tunnel branch with walls and crown is required. Station is a local axis coordinate, not design chainage; ring numbers are local interval numbers.',
                  'Seam curves are fitted guides, including across gaps. Coverage and fit RMS describe observed support, not surveying accuracy. Sector edges labelled inferred use the configured layout. High/medium/low describe boundary evidence. Check panel counts and offset against the installed ring design.',
                  'Full-density IDs and source attributes are preserved. Non-lining removal occurs only after reviewed apply; detection tasks retain all points by default.',
                  'The PointNet++ weights and project-specific bolt templates from the papers are not supplied. This is an independent geometric adaptation, not a reproduction of those trained or template-dependent stages.']
        if not detector.intensity_used:warnings.append('No usable intensity contrast: geometry-only detection is active.')
        if not detector.rings and settings.get('lining_task') in ('all','seams','segments'):warnings.append('No sufficiently supported ring seams found. No ring or segment layout was invented; try finer detail and check depth, spacing and scan coverage.')
        detection=detection_summary(detector,settings.get('lining_task','all'),counts)
        if detection['status'] not in ('candidates','review'):warnings.append(detection['summary'])
        lining_frame = {k:np.asarray(m[k]).tolist() for k in ('reference','axis','u','v')}
        lining_frame.update(nodes=m['nodes'].tolist(), radius_m=float(m['radius']))
        return dict(kept=kept,clusters=rows,features=features,detection=detection,lining_frame=lining_frame,research_dois=REFERENCES,warnings=warnings,sampler_version=SAMPLER_VERSION,
                    statistics=dict(input_points=written,unique_points=None,duplicate_locations=None,supervoxels=0,expected_supervoxels=0,merge_passes=0,initial_edges=0,removed_edges=0,retained_edges=0,clusters=len(rows),kept_points=int(counts[kept].sum()),noise_points=0,noise_candidates=int(counts[[1,2]].sum()),calibration_points=len(sample),map_cell_m=detector.cell,map_cells=int(detector.na*detector.nt),detail=settings.get('lining_detail','detailed'),depth_threshold_mm=detector.threshold*1000,seam_depth_threshold_mm=detector.seam_threshold*1000,intensity_used=detector.intensity_used,bolt_holes=sum(r['kind']=='bolt_hole' for r in features),bolt_fittings=sum(r['kind']=='bolt_fitting' for r in features),ring_seams=len(detector.rings),longitudinal_seams=len(detector.longitudinal),segment_candidates=detection['segment_candidates'],ambiguous_segment_boundaries=detection['ambiguous_segment_boundaries'],intervals_with_segments=detection['intervals_with_segments'],observed_ring_intervals=detection['observed_ring_intervals'],uncertain_retained=int(counts[3]),seconds=round(time.monotonic()-started,3)))
