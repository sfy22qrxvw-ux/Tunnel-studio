# Copyright (c) 2026 Damian Gulczynski.
"""One spatially supported lining model, followed by full-density classification.

Calibration uses bounded, deterministic spatial cells, never a separate model
per input block. Output contains labels for original points, not fitted points.
"""
import math,time
import numpy as np
from scipy.optimize import least_squares
from scipy.ndimage import median_filter,distance_transform_edt


def _emit(progress,cancel,text):
    if cancel():raise ValueError('Tunnel cleaning cancelled. The source and previous result are unchanged.')
    progress(text)


def spatial_sample(batches,spacing,cap,progress,cancel):
    keys=np.empty((0,3),np.int64);total=0
    for ids,points,_ in batches():
        _emit(progress,cancel,f'Learning one tunnel shape · {total+len(points):,} selected points read')
        q=np.asarray(points,float)
        if not np.isfinite(q).all():raise ValueError('The scan contains nonfinite coordinates.')
        cells=np.unique(np.floor(q/spacing).astype(np.int64),axis=0)
        keys=np.unique(np.concatenate((keys,cells)),axis=0);total+=len(points)
        if len(keys)>cap:
            h=keys.astype(np.uint64)
            rank=(h[:,0]*np.uint64(73856093))^(h[:,1]*np.uint64(19349663))^(h[:,2]*np.uint64(83492791))
            # Mix all bits so adjacent grid cells have no preferred hash range.
            rank^=rank>>np.uint64(30);rank*=np.uint64(0xbf58476d1ce4e5b9);rank^=rank>>np.uint64(27);rank*=np.uint64(0x94d049bb133111eb);rank^=rank>>np.uint64(31)
            keys=keys[np.argsort(rank,kind='stable')[:cap]]
    keys=keys[np.lexsort(keys.T[::-1])] if len(keys) else keys
    return (keys.astype(float)+.5)*spacing,total


def basis(axis):
    axis=np.asarray(axis,float);axis/=np.linalg.norm(axis)
    ref=np.eye(3)[np.argmin(np.abs(axis))];u=np.cross(ref,axis);u/=np.linalg.norm(u);v=np.cross(axis,u)
    return u,v


def coverage(q,centre):
    angle=np.mod(np.arctan2(q[:,1]-centre[1],q[:,0]-centre[0]),2*np.pi)
    return len(np.unique(np.floor(angle/(2*np.pi)*36).astype(int)))/36


def circle_seed(q,tolerance,rng):
    if len(q)>5000:q=q[rng.choice(len(q),5000,replace=False)]
    lo,hi=np.quantile(q,[.01,.99],axis=0);span=hi-lo;best=None;score=-1
    for _ in range(260):
        a,b,c=q[rng.choice(len(q),3,replace=False)];m=2*np.array([b-a,c-a])
        if abs(np.linalg.det(m))<1e-7:continue
        centre=np.linalg.solve(m,np.array([b@b-a@a,c@c-a@a]));radius=float(np.linalg.norm(a-centre))
        if not .2<=radius<=min(50.,max(span)*1.5) or ((centre<lo-span*.25)|(centre>hi+span*.25)).any():continue
        residual=np.abs(np.linalg.norm(q-centre,axis=1)-radius);use=residual<tolerance
        if use.sum()<30:continue
        occupied=coverage(q[use],centre);value=occupied*math.sqrt(float(use.mean()))
        if value>score:score=value;best=(centre,radius,occupied,float(use.mean()))
    if best is None:raise ValueError('No supported tunnel outline was found. Select one branch with both walls and the crown visible, or use Remove stray points for a small wall patch.')
    return best,score


def fit_model(sample,settings,progress=lambda _:None,cancel=lambda:False):
    if len(sample)<100:raise ValueError('Too little spatial coverage to learn the lining. Select a longer piece including the tunnel walls and crown.')
    thickness=float(settings.get('envelope_thickness',.08));origin=np.median(sample,axis=0);q=sample-origin
    # Trim only for estimating the initial directions. The fitted profile and
    # classification still see the entire selected area.
    low,high=np.quantile(q,[.005,.995],axis=0);central=q[((q>=low)&(q<=high)).all(1)]
    _,vectors=np.linalg.eigh(np.cov(central.T));rng=np.random.default_rng(137);best=None
    _emit(progress,cancel,'Finding a supported tunnel axis and cross-sectional outline')
    trial=q if len(q)<=18000 else q[rng.choice(len(q),18000,replace=False)]
    for axis in vectors.T[::-1]:
        if axis[np.argmax(abs(axis))]<0:axis=-axis
        u,v=basis(axis);cross=np.c_[trial@u,trial@v]
        try:seed,score=circle_seed(cross,max(.12,thickness*1.5),rng)
        except ValueError:continue
        if best is None or score>best[0]:best=(score,axis,u,v,seed)
    if best is None or best[4][2]<.5 or best[4][3]<.08:
        raise ValueError('The selection does not contain a supported tunnel outline. Include both walls and the crown, or use Remove stray points for a small patch.')
    _,axis,u,v,(centre,radius,_,_)=best
    # Robust cylinder refinement supplies a common frame; the later measured
    # radial profile, including a flat floor, is not forced to this circle.
    initial=np.r_[centre,radius,0.,0.];near=np.abs(np.hypot(trial@u-centre[0],trial@v-centre[1])-radius)<max(.3,radius*.12);fit=trial[near]
    def residual(x):
        a=axis+x[3]*u+x[4]*v;a=a/np.linalg.norm(a);d=fit-x[0]*u-x[1]*v
        return np.linalg.norm(d-(d@a)[:,None]*a,axis=1)-x[2]
    solved=least_squares(residual,initial,loss='soft_l1',f_scale=.035,max_nfev=45,bounds=([centre[0]-radius,centre[1]-radius,radius*.65,-.4,-.4],[centre[0]+radius,centre[1]+radius,radius*1.4,.4,.4])).x
    reference=origin+solved[0]*u+solved[1]*v;axis=axis+solved[3]*u+solved[4]*v;axis/=np.linalg.norm(axis);u,v=basis(axis);radius=float(solved[2])
    local=sample-reference;t=local@axis;xy=np.c_[local@u,local@v]
    pitch=max(.5,min(2.,float(settings.get('resolution',.2))*5));t0=float(t.min());nstation=int(np.ceil((t.max()-t0)/pitch))+1
    if nstation>10000:raise ValueError('Select a shorter tunnel branch (the current extent is too long for lining calibration).')
    stations=t0+np.arange(nstation)*pitch;nodes=[]
    for i,station in enumerate(stations):
        if i%15==0:_emit(progress,cancel,f'Following tunnel sections · {i+1}/{nstation}')
        z=xy[np.abs(t-station)<=max(.65,pitch)];r=np.linalg.norm(z,axis=1);z=z[np.abs(r-radius)<max(.35,radius*.18)]
        if len(z)<35 or coverage(z,[0,0])<.5:continue
        def error(x):return np.linalg.norm(z-x[:2],axis=1)-x[2]
        solved2=least_squares(error,[0,0,radius],loss='soft_l1',f_scale=.035,max_nfev=25,bounds=([-radius*.4,-radius*.4,radius*.7],[radius*.4,radius*.4,radius*1.3])).x
        good=np.abs(error(solved2))<max(.10,thickness*1.5)
        if good.sum()<25 or coverage(z[good],solved2[:2])<.5:continue
        nodes.append([station,*solved2])
    if len(nodes)<2:raise ValueError('The lining has insufficient support across neighbouring sections. Select one branch including more of its walls and crown.')
    nodes=np.asarray(nodes);nodes[:,1:]=median_filter(nodes[:,1:],size=(3,1),mode='nearest')
    cx=np.interp(t,nodes[:,0],nodes[:,1]);cy=np.interp(t,nodes[:,0],nodes[:,2]);rr=np.interp(t,nodes[:,0],nodes[:,3]);xy=xy-np.c_[cx,cy];radial=np.linalg.norm(xy,axis=1);offset=radial-rr
    angle=np.mod(np.arctan2(xy[:,1],xy[:,0]),2*np.pi);nangle=max(24,int(np.ceil(360/float(settings.get('envelope_angle_deg',3.)))))
    ab=np.minimum(nangle-1,(angle/(2*np.pi)*nangle).astype(int));tb=np.clip(np.rint((t-t0)/pitch).astype(int),0,nstation-1)
    # A single angular reference across neighbouring sections stops an
    # occluding object from declaring itself to be the outer tunnel surface.
    profile=np.full(nangle,np.nan);radial_step=max(.02,min(.06,thickness*.5))
    in_extent=(t>=nodes[0,0]-pitch)&(t<=nodes[-1,0]+pitch)
    for j in range(nangle):
        values=offset[(ab==j)&in_extent&(offset>-radius*.45)&(offset<radius*.25)]
        if len(values)<5:continue
        edges=np.arange(-radius*.45,radius*.25+radial_step,radial_step);hist,_=np.histogram(values,bins=edges);smooth=np.convolve(hist,[1,2,1],'same');peaks=np.flatnonzero(smooth>=smooth.max()*.4)
        # Prefer a supported outer mode, not an isolated outermost return.
        k=int(peaks[-1]);at=(edges[k]+edges[k+1])/2;close=values[np.abs(values-at)<=radial_step*1.5];profile[j]=float(np.median(close))
    known=np.flatnonzero(np.isfinite(profile))
    if len(known)<nangle*.5:raise ValueError('Too much of the tunnel circumference is missing to separate lining safely. Enlarge the area or clean stray points only.')
    profile=np.interp(np.arange(nangle),np.r_[known-nangle,known,known+nangle],np.tile(profile[known],3));profile=median_filter(profile,size=max(3,int(round(12/(360/nangle)))|1),mode='wrap')
    # A dense foreground pipe must not drag the local reference inwards.
    # Limit local calibration to the surrounding longitudinal wall profile;
    # missing support stays uncertain instead of being fitted to the pipe.
    target=np.full((nstation,nangle),np.nan);accepted=np.abs(offset-profile[ab])<=.06
    keys=tb*nangle+ab;order=np.argsort(keys[accepted],kind='stable');chosen=np.flatnonzero(accepted)[order];ks=keys[chosen];splits=np.r_[0,np.flatnonzero(np.diff(ks))+1,len(ks)]
    quantile=float(settings.get('envelope_quantile',.70))
    for start,end in zip(splits[:-1],splits[1:]):
        if end-start<3:continue
        ids=chosen[start:end];key=int(keys[ids[0]]);value=float(np.quantile(offset[ids],quantile));target[key//nangle,key%nangle]=value
    # Bounded gap support in station/angle space; wrap around the angular seam.
    repeated=np.tile(target,(1,3));missing=~np.isfinite(repeated)
    distance,nearest=distance_transform_edt(missing,sampling=[1.,1.],return_indices=True)
    filled=repeated[tuple(nearest)];supported=distance<=2.5;filled=np.where(supported,filled,np.nan)[:,nangle:2*nangle]
    supported=supported[:,nangle:2*nangle]
    safe=np.where(np.isfinite(filled),filled,np.tile(profile,(nstation,1)));safe=median_filter(np.tile(safe,(1,3)),size=(3,3),mode='nearest')[:,nangle:2*nangle]
    return dict(reference=reference,axis=axis,u=u,v=v,nodes=nodes,profile=profile,target=safe,supported=supported,t0=t0,pitch=pitch,nangle=nangle,thickness=thickness,sample_points=len(sample),radius=radius)


def deviations(points,model,with_radial=False):
    p=np.asarray(points,float)-model['reference'];t=p@model['axis'];nodes=model['nodes'];cx=np.interp(t,nodes[:,0],nodes[:,1]);cy=np.interp(t,nodes[:,0],nodes[:,2]);radius=np.interp(t,nodes[:,0],nodes[:,3])
    x=p@model['u']-cx;y=p@model['v']-cy;radial=np.hypot(x,y);a=np.mod(np.arctan2(y,x),2*np.pi);ab=np.clip((a/(2*np.pi)*model['nangle']).astype(int),0,model['nangle']-1)
    tb=np.clip(np.rint((t-model['t0'])/model['pitch']).astype(int),0,len(model['target'])-1);target=radius+model['target'][tb,ab];d=radial-target
    supported=model['supported'][tb,ab]&(t>=nodes[0,0]-model['pitch'])&(t<=nodes[-1,0]+model['pitch'])
    # Never bridge a long interval without fitted section support.
    k=np.clip(np.searchsorted(nodes[:,0],t),1,len(nodes)-1);supported&=(nodes[k,0]-nodes[k-1,0])<=model['pitch']*3.1
    if with_radial:
        unit=(x/np.maximum(radial,1e-12))[:,None]*model['u']+(y/np.maximum(radial,1e-12))[:,None]*model['v']
        return d,supported,unit
    return d,supported


def longitudinal_reference(sample,model):
    """Equal-weight sections stop dense local remnants becoming the reference.

    Retain the angular floor profile and fitted section centres. Used by optional reflection and interior review; standard extraction is unchanged.
    """
    model=dict(model);q=sample-model['reference'];t=q@model['axis'];nodes=model['nodes']
    x=q@model['u']-np.interp(t,nodes[:,0],nodes[:,1]);y=q@model['v']-np.interp(t,nodes[:,0],nodes[:,2])
    radius=np.interp(t,nodes[:,0],nodes[:,3]);offset=np.hypot(x,y)-radius
    angle=np.mod(np.arctan2(y,x),2*np.pi);na=model['nangle'];nt=len(model['target'])
    ab=np.minimum(na-1,(angle/(2*np.pi)*na).astype(int));tb=np.clip(np.rint((t-model['t0'])/model['pitch']).astype(int),0,nt-1)
    keys=tb*na+ab;order=np.argsort(keys,kind='stable');splits=np.r_[0,np.flatnonzero(np.diff(keys[order]))+1,len(order)]
    cells=np.full((nt,na),np.nan)
    for lo,hi in zip(splits[:-1],splits[1:]):
        if hi-lo>=3:
            ids=order[lo:hi];cells.flat[keys[ids[0]]]=np.median(offset[ids])
    profile=model['profile'].copy();valid=np.zeros(na,bool)
    for j in range(na):
        values=cells[:,j];values=values[np.isfinite(values)]
        if len(values)>=3:profile[j]=np.median(values);valid[j]=True
    model['target']=np.tile(profile,(nt,1));on=valid[ab]&(abs(offset-profile[ab])<=.05)
    count=np.bincount(keys[on],minlength=nt*na).reshape(nt,na)
    supported=count>=3;distance=distance_transform_edt(~np.tile(supported,(1,3)))
    model['supported']=(distance[:,na:2*na]<=2.5)&valid[None,:]
    model['profile']=profile;model['profile_supported']=valid
    return model


def classify(points,model,interior_band=None):
    d,supported=deviations(points,model)
    labels=np.where(d < -model['thickness'],1,2).astype(np.int32);labels[np.abs(d)<=model['thickness']]=0;labels[~supported]=3
    if interior_band is not None:
        # A local angular bin can sit beyond a flat floor. Do not tighten it on
        # its own: require agreement from the section-balanced reference and
        # respect the maximum displacement introduced by voxel calibration.
        band=max(float(interior_band),float(model.get('sample_uncertainty',0)))
        extra=np.zeros(len(labels),bool)
        # Small support gaps can retain deep floating points. Extend assessment
        # only a little, within fitted sections and well clear of the lining.
        if 'interior_support' not in model:
            n=model['nangle'];dist=distance_transform_edt(~np.tile(model['supported'],(1,3)))
            model['interior_support']=dist[:,n:2*n]<=4
        q=np.asarray(points,float)-model['reference'];t=q@model['axis'];nodes=model['nodes']
        x=q@model['u']-np.interp(t,nodes[:,0],nodes[:,1]);y=q@model['v']-np.interp(t,nodes[:,0],nodes[:,2])
        ab=np.minimum(model['nangle']-1,(np.mod(np.arctan2(y,x),2*np.pi)/(2*np.pi)*model['nangle']).astype(int));tb=np.clip(np.rint((t-model['t0'])/model['pitch']).astype(int),0,len(model['target'])-1)
        k=np.clip(np.searchsorted(nodes[:,0],t),1,len(nodes)-1)
        within=(t>=nodes[0,0])&(t<=nodes[-1,0])&((nodes[k,0]-nodes[k-1,0])<=model['pitch']*3.1)
        extra|=(labels==3)&within&model['interior_support'][tb,ab]&(d < -max(.2,2*model['thickness']))
        reference=model.get('interior_reference')
        if reference is not None:
            inward,_=deviations(points,reference)
            # Radial distance varies across an angular bin on a flat floor or
            # another sloping profile. Include that variation in the keep band.
            profile=reference['profile'];angular_error=.5*np.maximum(abs(profile-np.roll(profile,1)),abs(profile-np.roll(profile,-1)))
            local_band=np.maximum(band,float(model.get('sample_uncertainty',0))+angular_error[ab])
            extra|=(labels==0)&within&reference['profile_supported'][ab]&(d < -local_band)&(inward < -local_band)
            extra|=np.isin(labels,[0,3])&within&reference['profile_supported'][ab]&(inward < -max(.15,2*model['thickness'],float(interior_band)))
        labels[extra]=4
    return labels


def run_batches(batches,ids_path,labels_path,settings,progress,cancel):
    started=time.monotonic();spacing=max(.02,min(.075,float(settings.get('resolution',.2))*.25));sample,total=spatial_sample(batches,spacing,140000,progress,cancel)
    model=fit_model(sample,settings,progress,cancel);counts=np.zeros(5,np.int64);sums=np.zeros((5,3));written=0
    interior_band=float(settings.get('interior_band_mm',30))/1000 if settings.get('interior_cleanup')=='thorough' else None
    if interior_band is not None:
        model['interior_reference']=longitudinal_reference(sample,model)
        model['sample_uncertainty']=spacing*math.sqrt(3)/2
    with open(ids_path,'wb') as idsout,open(labels_path,'wb') as labout:
        for ids,points,_ in batches():
            _emit(progress,cancel,f'Checking original points against the tunnel · {written+len(ids):,}/{total:,}')
            labels=classify(points,model,interior_band);np.asarray(ids,dtype='<i8').tofile(idsout);labels.astype('<i4').tofile(labout);written+=len(ids)
            for k in range(5):
                keep=labels==k;counts[k]+=int(keep.sum());sums[k]+=np.asarray(points)[keep].sum(0)
    if written!=total:raise ValueError('The scan changed during cleaning. Please run again.')
    if counts[0]<50:raise ValueError('Too few original points match the supported tunnel lining. Enlarge the selected tunnel branch or review the lining tolerance.')
    names=['Tunnel lining','Interior objects / points away from lining','Outside lining / stray points','Uncertain lining area · retained','Extra interior candidates · review']
    rows=[dict(id=k,name=names[k],points=int(counts[k]),supervoxels=0,centre=(sums[k]/counts[k]).tolist(),linearity=0.,planar_fraction=0.,overlap=0.,automatic_keep=k in (0,3)) for k in range(5) if counts[k]]
    return dict(kept=[k for k in (0,3) if counts[k]],clusters=rows,statistics=dict(input_points=total,unique_points=None,duplicate_locations=None,supervoxels=0,expected_supervoxels=0,merge_passes=0,initial_edges=0,removed_edges=0,retained_edges=0,clusters=len(rows),kept_points=int(counts[0]+counts[3]),noise_points=0,noise_candidates=int(counts[1]+counts[2]+counts[4]),extra_interior_candidates=int(counts[4]),interior_cleanup=settings.get('interior_cleanup','standard'),interior_band_mm=interior_band*1000 if interior_band is not None else None,interior_calibration_guard_mm=model.get('sample_uncertainty',0)*1000 if interior_band is not None else None,spacing_m=spacing,calibration_points=len(sample),fitted_sections=len(model['nodes']),reference_radius_m=model['radius'],strategy='one supported tunnel model; every original point classified',seconds=round(time.monotonic()-started,3)),warnings=(['More thorough interior cleanup checks extra inward points against two lining references and widens the keep band for calibration uncertainty and sloping profiles. Inspect the separate Extra interior candidates group; real fittings and near-wall details can be included.'] if interior_band is not None else [])+['The tunnel shape is learned once from a bounded spatial sample; every retained point in the processing area is classified at its original coordinates.','Review red points, especially openings, supported protrusions and areas labelled Uncertain lining area (retained). This models one approximately circular tunnel branch, including supported floor profiles; it is not a design or tolerance check.'])
