# Copyright (c) 2026 Damian Gulczynski.
"""Reviewable section shape and selected-ring plane; never alter measured points.

Moments supply a stable local frame, not an unverified tunnel design. Ellipse
fitting reuses the robust angular-balanced implementation in section_research.
Ring-plane covariance/SVD follows the elementary plane fit described by Wang
et al. (2025), 10.3390/app15062926; automatic ring segmentation is not implied.
"""
import csv,math,copy
import numpy as np
import section_research,section_path


def ring_path(points):
    p=np.asarray(points,float)
    if len(p)<50 or p.ndim!=2 or p.shape[1]!=3 or not np.isfinite(p).all():raise ValueError('Select at least 50 points around one narrow, complete ring.')
    centre=p.mean(0);q=p-centre;ev,vec=np.linalg.eigh(q.T@q/len(q))
    if ev[1]<=1e-8 or ev[0]/ev[1]>.08 or ev[1]/ev[2]<.15:
        raise ValueError('Select one narrow ring around the tunnel, including both walls and the crown. This selection is too long or has insufficient in-plane spread.')
    normal=vec[:,0]
    if normal[np.argmax(abs(normal))]<0:normal=-normal
    uv=q@vec[:,1:];_,gap=section_research.coverage(uv)
    if gap>90:raise ValueError('The selected ring has a gap above 90 degrees. Include more of its circumference.')
    t=q@normal;lo,hi=float(t.min()),float(t.max())
    # A thin physical ring may still have nearly zero extent along its normal.
    span=max(.05,hi-lo);lo=min(lo,-span/2);hi=max(hi,span/2)
    path=section_path.model([[lo,*(centre+lo*normal)],[hi,*(centre+hi*normal)]],False,'Selected ring plane · covariance fit')
    path.update(plane_rms_mm=float(np.sqrt(max(0.,ev[0]))*1000),largest_gap_deg=gap,
        warning='Plane fitted to the selected ring. A skew ring need not be perpendicular to the tunnel axis; inspect the yellow plane.')
    return path


def outline_fit(points,threshold_mm=20):
    raw=np.asarray(points,float)
    if raw.ndim!=2 or raw.shape[1]!=2 or len(raw)<50 or not np.isfinite(raw).all():raise ValueError('At least 50 finite section points are needed.')
    lo,hi=np.quantile(raw,[.02,.98],axis=0);origin=(lo+hi)/2;size=float(np.max(hi-lo))
    if size<.04:raise ValueError('The section is too small to fit a tunnel outline.')
    # Density balance the moment frame. These are only calibration coordinates.
    cells=np.floor((raw-origin)/(size/400)).astype(np.int64);_,ix=np.unique(cells,axis=0,return_index=True);balanced=raw[ix]-origin
    ev,vec=np.linalg.eigh(np.cov(balanced.T))
    if ev[0]/max(ev[1],1e-15)<.05:raise ValueError('The section is nearly a line. Include both walls and the crown.')
    local=(raw-origin)@vec;radius=float(np.median(np.linalg.norm(balanced,axis=1)))
    fit=section_research.fit(local,radius,threshold_mm)
    centre=origin+vec@np.asarray(fit['centre']);direction=vec@np.array([math.cos(fit['angle_rad']),math.sin(fit['angle_rad'])])
    result={**fit,**section_research.canonical(*centre,fit['a'],fit['b'],math.atan2(direction[1],direction[0]))}
    angles=np.linspace(0,2*np.pi,361);t=result['angle_rad'];rot=np.array([[math.cos(t),-math.sin(t)],[math.sin(t),math.cos(t)]])
    result.update(outline=(np.c_[result['a']*np.cos(angles),result['b']*np.sin(angles)]@rot.T+centre).tolist(),
        major_diameter_m=2*result['a'],minor_diameter_m=2*result['b'],diameter_difference_mm=2000*(result['a']-result['b']),
        calibration_points=len(raw),reference_radius_source='estimated from section; no design radius supplied')
    return result


def analyse(section,threshold_mm=20,progress=lambda _:None):
    points=np.fromfile(section['preview_file'],'<f8').reshape(-1,5)
    fit=outline_fit(points[:,3:5],threshold_mm);count=valid_count=inside_count=0;sumd=sumsq=insq=0.;peak=0.
    def accumulate(rows):
        nonlocal count,valid_count,inside_count,sumd,sumsq,insq,peak
        uv=np.asarray(rows,float);delta=uv-fit['centre'];gradient=2*delta@section_research.matrix(fit)
        valid=np.linalg.norm(gradient,axis=1)>1e-9;d=section_research.residual(uv[valid],fit);inside=np.abs(d)<=float(threshold_mm)/1000
        count+=len(uv);valid_count+=len(d);inside_count+=int(inside.sum());sumd+=float(d.sum());sumsq+=float(d@d);insq+=float(d[inside]@d[inside])
        if len(d):peak=max(peak,float(np.max(abs(d))))
        progress(f'Checking ellipse against full-density section · {count:,}/{section["count"]:,} points')
    with open(section['file'],encoding='utf8',newline='') as src:
        reader=csv.reader(src);next(reader);rows=[]
        for row in reader:
            rows.append([float(row[3]),float(row[4])])
            if len(rows)>=50000:accumulate(rows);rows=[]
        if rows:accumulate(rows)
    if count!=section['count']:raise ValueError('The stored section changed. Create it again before fitting.')
    fit.update(full_density_points=count,residual_points=valid_count,undefined_residual_points=count-valid_count,
        full_density_inliers=inside_count,all_residual_rms_mm=1000*math.sqrt(sumsq/max(1,valid_count)),
        all_residual_mean_mm=1000*sumd/max(1,valid_count),inlier_residual_rms_mm=1000*math.sqrt(insq/max(1,inside_count)),
        maximum_absolute_residual_mm=peak*1000,
        warning='Descriptive ellipse, not design or deformation. Sampson residuals approximate distance; large departures are not exact geometric distances. All measured section points remain in the plot and CSV.')
    result=copy.deepcopy(section);result['shape']=fit;return result
