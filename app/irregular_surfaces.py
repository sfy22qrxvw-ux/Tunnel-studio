# Copyright (c) 2026 Damian Gulczynski.
"""Reviewable irregular patches, measured against a learned tunnel lining.

No points are moved or deleted. A bounded spatial fit supplies a reference;
every original retained point contributes to classification and patch counts.
"""
import time
import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.ndimage import median_filter
import tunnel_lining


def thresholds(settings):
    sensitivity=float(settings.get('irregular_sensitivity',50))/100
    deviation=.12*10**(-sensitivity)
    return deviation,deviation*.5


def groups(keys,counts,minimum):
    """26-connected occupied cells; no bridges across empty cells."""
    if not len(keys):return np.empty(0,np.int32)
    pairs=cKDTree(keys).query_pairs(1.00001,p=np.inf,output_type='ndarray')
    graph=coo_matrix((np.ones(len(pairs),np.uint8),(pairs[:,0],pairs[:,1])),shape=(len(keys),len(keys))).tocsr()
    n,labels=connected_components(graph,directed=False)
    totals=np.bincount(labels,weights=counts,minlength=n)
    mapping=np.full(n,2,np.int32);mapping[totals>=minimum]=10+np.arange(np.count_nonzero(totals>=minimum),dtype=np.int32)
    return mapping[labels]


def run_batches(batches,ids_path,labels_path,settings,progress,cancel):
    started=time.monotonic();deviation,roughness=thresholds(settings)
    pitch=float(settings.get('irregular_patch_size',.20));minimum=int(settings.get('irregular_min_points',30))
    def emit(message):
        if cancel():raise ValueError('Irregular surface detection cancelled. The scan and previous result are unchanged.')
        progress(message)
    sample,total=tunnel_lining.spatial_sample(batches,max(.02,min(.06,pitch/4)),140000,progress,cancel)
    model=tunnel_lining.fit_model(sample,settings,progress,cancel)
    # A cleaning envelope can follow a local recess. For anomaly review, learn
    # the radial reference over a broader longitudinal neighbourhood instead.
    # Keep the angular profile (including the floor); never alter scan points.
    window=max(5,int(np.ceil(4*pitch/model['pitch']))|1)
    model['target']=median_filter(model['target'],size=(window,1),mode='nearest')
    reference=model['reference'];grid={};seen=0
    # Moment statistics are accumulated over original points, never just the
    # displayed sample. Separate categories retain unsupported/small features.
    for ids,points,_ in batches():
        emit(f'Finding local deviations and roughness · {seen+len(ids):,}/{total:,} points')
        p=np.asarray(points,float);d,support,radial=tunnel_lining.deviations(p,model,True)
        # Group on the reference surface, so radial scatter is not split into
        # separate depth cells before measuring its roughness.
        cells,inv=np.unique(np.floor((p-reference-d[:,None]*radial)/pitch).astype(np.int64),axis=0,return_inverse=True)
        n=len(cells);neg=support&(d < -deviation);pos=support&(d > deviation)
        local=support&(abs(d)<=max(.5,3*deviation))
        moments=np.c_[np.bincount(inv,minlength=n),np.bincount(inv,weights=support,minlength=n),np.bincount(inv,weights=neg,minlength=n),np.bincount(inv,weights=pos,minlength=n),np.bincount(inv,weights=local,minlength=n),np.bincount(inv,weights=np.where(local,d,0),minlength=n),np.bincount(inv,weights=np.where(local,d*d,0),minlength=n)]
        for key,value in zip(map(tuple,cells),moments):
            if key in grid:grid[key]+=value
            else:grid[key]=value
        if len(grid)>250000:raise ValueError('This area needs too many surface patches. Select a shorter tunnel branch or increase Patch size, then try again.')
        seen+=len(ids)
    if seen!=total:raise ValueError('The source changed during detection. Run it again.')
    keys=np.array(sorted(grid),np.int64);values=np.array([grid[tuple(k)] for k in keys]);del grid
    local_n=np.maximum(1,values[:,4]);spread=np.sqrt(np.maximum(0,values[:,6]/local_n-(values[:,5]/local_n)**2))
    rough=(values[:,4]>=6)&(spread>roughness)
    candidates=values[:,2]+values[:,3]+np.where(rough,values[:,1]-values[:,2]-values[:,3],0)
    chosen=candidates>0
    emit(f'Grouping neighbouring irregular patches · {int(chosen.sum()):,} occupied cells')
    labels=groups(keys[chosen],candidates[chosen],minimum)
    lookup={tuple(k):(int(label),bool(r)) for k,label,r in zip(keys[chosen],labels,rough[chosen])}
    counts={};sums={};kinds={};peak={};representatives={};written=0
    with open(ids_path,'wb') as idout,open(labels_path,'wb') as labout:
        for ids,points,_ in batches():
            emit(f'Writing selectable patches · {written+len(ids):,}/{total:,} points')
            p=np.asarray(points,float);d,support,radial=tunnel_lining.deviations(p,model,True)
            cells,inv=np.unique(np.floor((p-reference-d[:,None]*radial)/pitch).astype(np.int64),axis=0,return_inverse=True)
            mapped=np.array([lookup.get(tuple(k),(0,False)) for k in cells]);lab=mapped[inv,0].astype(np.int32);rough_point=mapped[inv,1].astype(bool)
            flags=support&((abs(d)>deviation)|rough_point);lab[~flags]=0;lab[~support]=1
            np.asarray(ids,'<i8').tofile(idout);lab.astype('<i4').tofile(labout);written+=len(ids)
            for k in np.unique(lab):
                use=lab==k;k=int(k);count=int(use.sum());counts[k]=counts.get(k,0)+count;sums[k]=sums.get(k,np.zeros(3))+(p[use]-reference).sum(0)
                kinds[k]=kinds.get(k,np.zeros(3,np.int64))+[np.count_nonzero(d[use]<-deviation),np.count_nonzero(d[use]>deviation),np.count_nonzero(rough_point[use])]
                peak[k]=max(peak.get(k,0),float(np.max(abs(d[use]))))
                if k>=10 and k not in representatives:
                    positions=np.flatnonzero(use)
                    representatives[k]=(written-len(ids)+positions[np.linspace(0,len(positions)-1,min(32,len(positions)),dtype=int)]).tolist()
    if written!=total:raise ValueError('The source changed during detection. Run it again.')
    rows=[]
    for k,count in sorted(counts.items()):
        if k<10:name={0:'No flagged irregularity',1:'Unassessed · insufficient lining support',2:'Small candidates · below minimum patch count'}[k]
        else:
            n=kinds[k];parts=[name for name,num in zip(['protrusion','recess','roughness'],n) if num>=max(1,count*.15)]
            name='Patch '+str(k-9)+' · '+' / '.join(parts or ['irregular surface'])
        rows.append(dict(id=k,name=name,points=count,centre=(sums[k]/count+reference).tolist(),automatic_keep=k<10,irregular_candidate=k>=10,peak_radial_deviation_m=peak[k],supervoxels=0,linearity=0.,planar_fraction=0.,overlap=0.))
        if k>=10:rows[-1]['preview_indices']=representatives[k]
    kept=[k for k in counts if k<10];candidate_count=sum(n for k,n in counts.items() if k>=10)
    emit(f'Detection ready · {sum(k>=10 for k in counts)} candidate patches · source scan unchanged')
    return dict(kept=kept,clusters=rows,statistics=dict(input_points=total,unique_points=None,duplicate_locations=None,supervoxels=len(keys),expected_supervoxels=len(keys),merge_passes=0,initial_edges=0,removed_edges=0,retained_edges=0,clusters=len(rows),kept_points=total-candidate_count,noise_points=0,noise_candidates=candidate_count,irregular_patches=sum(k>=10 for k in counts),unassessed_points=counts.get(1,0),deviation_threshold_m=deviation,roughness_threshold_m=roughness,patch_size_m=pitch,calibration_points=len(sample),spacing_m=pitch,strategy='supported lining deviations and full-density local roughness; connected spatial patches',seconds=round(time.monotonic()-started,3)),warnings=['Red patches are candidates, not confirmed defects. Joints, fittings, openings and scan noise can also be flagged. Hide is a preview action; Delete selected patches changes the source reversibly.','Use one approximately circular tunnel branch with enough walls and crown. Green means retained, including unassessed areas and patches below the minimum count. Detection is not a design comparison and cannot guarantee every irregularity.'])
