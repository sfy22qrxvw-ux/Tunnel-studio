# Copyright (c) 2026 Damian Gulczynski.
"""Robust circular-tunnel section fitting and dimensionally explicit comparison.

Camara, Wang & You (2024), doi:10.3390/s24227192, equations 3--8, 11--12.
Equation 4's second denominator is corrected to b. Fits use a signed Sampson
distance approximation in metres. No uncalibrated structural-health score.
"""
import math, copy
import numpy as np
from scipy.optimize import least_squares


def canonical(cx, cy, a, b, theta):
    if b > a:
        a, b, theta = b, a, theta+math.pi/2
    return dict(centre=[float(cx), float(cy)], a=float(a), b=float(b),
                angle_rad=float(theta % math.pi))


def matrix(e):
    t = e['angle_rad']; c, s = math.cos(t), math.sin(t)
    rot = np.array([[c, -s], [s, c]])
    return rot @ np.diag([1/e['a']**2, 1/e['b']**2]) @ rot.T


def residual(points, e):
    delta = np.asarray(points)-e['centre']; q = matrix(e)
    gradient = 2*delta@q
    return (np.einsum('ij,ij->i', delta@q, delta)-1)/np.maximum(np.linalg.norm(gradient, axis=1), 1e-15)


def conic(points):
    """Five-point ellipse hypothesis in centred, scaled coordinates."""
    p = np.asarray(points); origin = p.mean(0); scale = np.sqrt(np.mean((p-origin)**2))
    if scale < 1e-12:raise ValueError('Coincident ellipse points')
    x,y=((p-origin)/scale).T
    d=np.column_stack([x*x,x*y,y*y,x,y,np.ones(len(x))])
    _,singular,vh=np.linalg.svd(d,full_matrices=True)
    if singular[-1] < singular[0]*1e-10:raise ValueError('Degenerate ellipse hypothesis')
    a,b,c,d,e,f=vh[-1];q=np.array([[a,b/2],[b/2,c]]);linear=np.array([d,e])
    values,vectors=np.linalg.eigh(q)
    if values[1]<0:q=-q;linear=-linear;f=-f;values,vectors=np.linalg.eigh(q)
    if values[0]<=1e-12 or values[1]/values[0]>16:raise ValueError('Not a supported ellipse')
    centre=-.5*np.linalg.solve(q,linear);level=-(centre@q@centre+linear@centre+f)
    if level<=0:raise ValueError('Imaginary ellipse')
    axes=np.sqrt(level/values)*scale;centre=origin+centre*scale
    theta=math.atan2(vectors[1,0],vectors[0,0])
    return canonical(*centre,*axes,theta)


def coverage(points):
    angle=np.mod(np.arctan2(points[:,1],points[:,0]),2*math.pi)
    occupied=np.unique(np.floor(angle*180/math.pi).astype(int))
    gap=float(np.max(np.diff(np.r_[occupied,occupied[0]+360]))) if len(occupied) else 360.
    return len(occupied),gap


def _point_blocks(points,block=100000):
    """Do not fault an entire mapped section into process RSS during fit checks."""
    q=np.asarray(points);base=points;mapped=None
    while base is not None:
        if isinstance(base,np.memmap):mapped=base
        base=getattr(base,'base',None)
    if mapped is not None and q.dtype==np.dtype('<f8') and q.strides[1]==8 and q.strides[0]==mapped.strides[0] and q.strides[0]%8==0:
        width=q.strides[0]//8;offset=int(mapped.offset)+q.ctypes.data-mapped.ctypes.data
        with open(mapped.filename,'rb') as stream:
            stream.seek(offset)
            for start in range(0,len(q),block):
                n=min(block,len(q)-start);values=np.fromfile(stream,'<f8',count=n*width)
                if len(values)!=n*width:raise ValueError('Section points are incomplete.')
                yield values.reshape(-1,width)[:,:2]
    else:
        for start in range(0,len(q),block):yield q[start:start+block]


def fit(points, design_radius, threshold_mm=20, trials=250, balanced_points=None):
    raw=np.asarray(points,dtype=np.float64)
    radius=float(design_radius);limit=float(threshold_mm)/1000
    if not (.02<radius<1000 and .0005<=limit<=.5):raise ValueError('Check design radius and fit threshold (0.5–500 mm).')
    if raw.ndim!=2 or raw.shape[1]!=2:raise ValueError('Section points must be local right/up coordinates.')
    if balanced_points is None:
        raw=raw[np.isfinite(raw).all(1)]
        if len(raw)<24:raise ValueError('At least 24 section points are needed.')
        bins=np.floor(np.mod(np.arctan2(raw[:,1],raw[:,0]),2*math.pi)*180/math.pi).astype(int)
        order=np.argsort(bins,kind='stable');starts=np.r_[0,np.flatnonzero(np.diff(bins[order]))+1,len(raw)]
        # Balance angular sampling without taking independent X/Y medians, which can
        # move the representative inward when a bin also contains interior objects.
        balanced=[]
        for a,b in zip(starts[:-1],starts[1:]):
            q=raw[order[a:b]];angle=np.mod(np.arctan2(q[:,1],q[:,0]),2*math.pi)
            phi=float(np.median(angle));r=float(np.median(np.linalg.norm(q,axis=1)))
            balanced.append([r*math.cos(phi),r*math.sin(phi)])
        balanced=np.asarray(balanced)
    else:
        balanced=np.asarray(balanced_points,dtype=np.float64)
        if balanced.ndim!=2 or balanced.shape[1]!=2 or not np.isfinite(balanced).all():
            raise ValueError('Invalid full-density balanced section bins.')
    _,gap=coverage(balanced)
    if len(balanced)<24 or gap>60:raise ValueError('Ellipse fit withheld: insufficient angular coverage or a gap above 60°.')
    rng=np.random.default_rng(1729);best=None;best_score=(-1,-np.inf)
    for _ in range(int(trials)):
        sample=balanced[rng.choice(len(balanced),5,replace=False)]
        try: e=conic(sample)
        except (ValueError,np.linalg.LinAlgError):continue
        if not (.5*radius<=e['b']<=e['a']<=1.5*radius) or np.linalg.norm(e['centre'])>.5*radius:continue
        dist=np.abs(residual(balanced,e));inside=dist<=limit
        score=(int(inside.sum()),-float(np.median(dist[inside])) if inside.any() else -np.inf)
        if score>best_score:best,best_score=(e,inside),score
    if best is None or best_score[0]<24:raise ValueError('No supported tunnel ellipse. Check the section, wall band and threshold.')
    e,inside=best
    for _ in range(3):
        q=balanced[inside]
        if len(q)<24:break
        def unpack(x):return dict(centre=x[:2],a=np.exp(x[2]),b=np.exp(x[3]),angle_rad=x[4])
        x=np.r_[e['centre'],np.log([e['a'],e['b']]),e['angle_rad']]
        lo=[-.5*radius,-.5*radius,math.log(.5*radius),math.log(.5*radius),-4*math.pi]
        hi=[.5*radius,.5*radius,math.log(1.5*radius),math.log(1.5*radius),4*math.pi]
        opt=least_squares(lambda z:residual(q,unpack(z)),x,bounds=(lo,hi),
                          loss='soft_l1',f_scale=max(limit/3,.0001),max_nfev=300)
        if not opt.success:raise ValueError('Ellipse optimisation did not converge. Adjust the selection or threshold.')
        fitted=unpack(opt.x);e=canonical(*fitted['centre'],fitted['a'],fitted['b'],fitted['angle_rad'])
        inside=np.abs(residual(balanced,e))<=limit
    support,gap=coverage(balanced[inside]) if inside.any() else (0,360.)
    if support<24 or gap>60:raise ValueError('Ellipse consensus does not cover the lining; result withheld.')
    input_points=inlier_points=0;sum_squared=0.
    for part in _point_blocks(points if balanced_points is not None else raw):
        part=part[np.isfinite(part).all(1)]
        d=residual(part,e);retained=np.abs(d)<=limit
        input_points+=len(part);inlier_points+=int(retained.sum());sum_squared+=float(np.dot(d[retained],d[retained]))
    if not inlier_points:raise ValueError('No points support the fitted section.')
    rms=math.sqrt(sum_squared/inlier_points)
    e.update(eccentricity=math.sqrt(max(0,1-(e['b']/e['a'])**2)),
             fit_rms_mm=1000*rms,fit_threshold_mm=1000*limit,
             input_points=input_points,inlier_points=inlier_points,
             fit_bins=int(inside.sum()),largest_gap_deg=gap,
             orientation_defined=bool(e['a']-e['b']>max(2*rms,.0005*radius)),
             residual_method='Signed Sampson distance approximation',
             method='RANSAC ellipse + robust least squares on 1-degree balanced points')
    return e


def ray_radius(e, angles, origin=None):
    """Crown=0°, right=90°; positive exit from a common origin inside ellipse."""
    origin=np.asarray(e['centre'] if origin is None else origin,float)
    angle=np.asarray(angles,float);direction=np.column_stack([np.sin(angle),np.cos(angle)])
    q=matrix(e);delta=origin-np.asarray(e['centre'])
    c=float(delta@q@delta-1)
    if c>=0:raise ValueError('Comparison origin lies outside the ellipse; a unique radial comparison is unavailable.')
    a=np.einsum('ij,ij->i',direction@q,direction);b=2*direction@q@delta
    discriminant=np.maximum(0,b*b-4*a*c)
    return (-b+np.sqrt(discriminant))/(2*a)


def compare(measured, reference):
    a,b=measured['a'],measured['b'];a0,b0=reference['a'],reference['b']
    degrees=np.arange(360,dtype=float);angles=np.deg2rad(degrees)
    shape=ray_radius(measured,angles)-ray_radius(reference,angles)
    origin=reference['centre']
    radial=ray_radius(measured,angles,origin)-ray_radius(reference,angles,origin)
    shift=np.asarray(measured['centre'])-origin
    theta=(measured['angle_rad']-reference['angle_rad']+math.pi/2)%math.pi-math.pi/2
    defined=measured.get('orientation_defined',True) and reference.get('orientation_defined',True)
    return dict(eccentricity_change=math.sqrt(max(0,1-(b/a)**2))-math.sqrt(max(0,1-(b0/a0)**2)),
                centre_right_mm=float(1000*shift[0]),centre_up_mm=float(1000*shift[1]),
                centre_displacement_mm=float(1000*np.linalg.norm(shift)),
                semi_major_change_mm=float(1000*(a-a0)),semi_minor_change_mm=float(1000*(b-b0)),
                absolute_semi_axis_change_mm=float(1000*(abs(a-a0)+abs(b-b0))),
                orientation_change_deg=math.degrees(theta) if defined else None,
                radial_min_mm=float(1000*radial.min()),radial_max_mm=float(1000*radial.max()),
                radial_curve=np.column_stack([degrees,radial*1000,shape*1000]).tolist(),
                angle_convention='0 crown, 90 right, 180 invert, 270 left; looking along increasing chainage',
                radial_method='Common reference-centre ray intersection, including centre displacement')


def _reference_settings(report, threshold_mm, reference):
    """Check comparable geometry/filtering before an expensive section fit.

    Different epochs may legitimately use different source files and masks.
    Their plane and numerical fitting/filtering settings must still agree;
    changing those settings can otherwise look like measured deformation.
    """
    recreate='Clear and recreate the reference with consistent section settings.'
    plane=reference.get('plane');saved_fit=reference.get('fit')
    if not isinstance(plane,dict) or not isinstance(saved_fit,dict):
        raise ValueError('The saved section reference is incomplete. '+recreate)
    fields=(('slice width',report.get('width'),plane.get('width'),'m'),
            ('wall band',report.get('band'),reference.get('wall_band_m'),'m'),
            ('design radius',report.get('radius'),reference.get('design_radius'),'m'),
            ('fit threshold',threshold_mm,saved_fit.get('fit_threshold_mm'),'mm'))
    if any(saved is None for _,_,saved,_ in fields):
        raise ValueError('This saved section reference does not record all required comparison settings '
                         '(slice width, wall band, design radius and fit threshold). '+recreate)
    mismatches=[]
    for label,current,saved,unit in fields:
        values=[]
        for value,kind in ((current,'Current section'),(saved,'Saved section reference')):
            try:
                if isinstance(value,(bool,np.bool_)):raise ValueError
                number=float(value)
                if not math.isfinite(number) or number<=0:raise ValueError
            except (TypeError,ValueError,OverflowError):
                raise ValueError(f'{kind} has an invalid {label}; use a finite positive value. '+recreate) from None
            values.append(number)
        if not math.isclose(*values,rel_tol=0,abs_tol=1e-9):
            mismatches.append(f'{label} {values[1]:.12g} {unit}')
    for field in ('centre','right','up'):
        try:
            current=np.asarray(report[field],dtype=float);saved=np.asarray(plane[field],dtype=float)
            if current.shape!=(3,) or saved.shape!=(3,) or not np.isfinite(current).all() or not np.isfinite(saved).all():
                raise ValueError
        except (KeyError,TypeError,ValueError):
            raise ValueError('The current or saved section plane is incomplete or invalid. '+recreate) from None
        if not np.allclose(current,saved,atol=1e-7,rtol=0):
            raise ValueError('Saved reference uses a different section plane. Return to that chainage or clear the reference.')
    if mismatches:
        raise ValueError('Use the saved reference settings: '+', '.join(mismatches)+
                         '. Different filtering or fitting settings can appear as deformation. '+recreate)


def evaluate(report, points, threshold_mm=20, reference=None):
    if reference is not None:
        if not isinstance(reference,dict):
            raise ValueError('The saved section reference is invalid. Clear and recreate it with consistent section settings.')
        _reference_settings(report,threshold_mm,reference)
    measured=fit(points,report['radius'],threshold_mm,balanced_points=report.get('balanced_points'))
    design=canonical(0,0,report['radius'],report['radius'],0);design['orientation_defined']=False
    ref=design;kind='Circular design'
    if reference is not None:
        ref=reference['fit'];kind='Saved section: '+reference.get('name','Baseline section')
    result=dict(fit=measured,reference=ref,reference_kind=kind,comparison=compare(measured,ref),
                plane={k:copy.deepcopy(report[k]) for k in ('centre','right','up','chainage','width')},
                design_radius=report['radius'],wall_band_m=report['band'],
                points_outside_wall_band=report.get('excluded_points',0),
                note='Fit uses retained section points inside the selected wall band. Cleaning and fitting thresholds can exclude real defects; inspect the plotted points.')
    p=np.asarray(points);step=max(1,math.ceil(len(p)/8000));result['display_points']=p[::step].tolist()
    return result
