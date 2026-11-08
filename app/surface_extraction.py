# Copyright (c) 2026 Damian Gulczynski.
"""Independent SMRG research implementation, Song et al. (2026).
DOI 10.1038/s41598-026-73755-6. See SURFACE_METHOD_0_4_0.txt for explicit
interpretations of incomplete/ambiguous details. No author code or model weights.
All geometry uses metres and local float64 coordinates. No silent subsampling.
"""
from collections import deque
from dataclasses import dataclass,asdict
import math,time
import numpy as np
from scipy.spatial import cKDTree,ConvexHull,QhullError
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import minimum_spanning_tree


@dataclass
class Settings:
    resolution:float=.20
    normal_neighbours:int=15
    alpha_normal:float=1.0
    alpha_space:float=.4
    alpha_offset:float=.6
    dbscan_factor:float=2.5
    dbscan_min:int=3
    edge_passes:int=3
    linearity:float=.8
    residual_k:float=2.0
    angle_deg:float=20.0
    offset:float=.1
    curvature:float=.05
    overlap:float=.5
    repair:bool=True
    graph_mode:str='boundary'
    convexity_mode:str='printed'
    max_points:int=300000
    interior:object=None
    method:str='gusac'
    gusac_distance_mm:float=20.
    gusac_sector_deg:float=5.
    gusac_window_m:float=1.
    gusac_normal_deg:float=35.
    gusac_min_support:int=20
    tmatch_radius_mm:float=15.
    tmatch_min_points:int=30
    tmatch_ring_width_m:float=0.
    noise_radius:float=.10
    noise_neighbours:int=12
    noise_minimum:int=3
    noise_sigma:float=3.0
    envelope_angle_deg:float=3.0
    envelope_quantile:float=.70
    envelope_thickness:float=.08
    denoise_shape:str='measured'
    denoise_distance_mm:float=20.
    interior_cleanup:str='standard'
    interior_band_mm:float=30.
    reflection_gap_mm:float=100.
    reflection_match_mm:float=100.
    lining_sector_count:int=6
    lining_sector_offset_deg:float=0.
    lining_task:str='all'
    lining_detail:str='detailed'
    lining_depth_mm:float=12.
    lining_seam_depth_mm:float=4.
    lining_inward_mm:float=60.
    lining_recess_mm:float=250.
    lining_bolt_max_mm:float=350.
    lining_bolt_kind:str='recess'
    lining_bolt_height_mm:float=0.
    lining_bolt_min_points:int=0
    irregular_sensitivity:float=50.
    irregular_patch_size:float=.20
    irregular_min_points:int=30
    ray_station_m:float=.25
    ray_angle_deg:float=3.
    ray_depth_mm:float=20.
    ray_keep_mm:float=60.
    ray_min_points:int=4
    ray_inward_mm:float=1200.
    ray_outward_mm:float=300.

    @classmethod
    def read(cls,values=None):
        values=values or {}; names=set(cls.__dataclass_fields__)
        if set(values)-names: raise ValueError('Unknown surface extraction setting.')
        c=cls(**values)
        limits={'resolution':(.02,2),'normal_neighbours':(5,40),'alpha_normal':(0,5),'alpha_space':(.01,5),'alpha_offset':(0,5),'dbscan_factor':(1,5),'dbscan_min':(3,30),'edge_passes':(0,8),'linearity':(.1,.99),'residual_k':(.1,6),'angle_deg':(1,85),'offset':(.001,1),'curvature':(0,.333333),'overlap':(0,1),'max_points':(1000,750000),'envelope_angle_deg':(1,15),'envelope_quantile':(.55,.99),'envelope_thickness':(.01,1.0)}
        limits.update(noise_radius=(.005,2),noise_neighbours=(3,64),noise_minimum=(1,64),noise_sigma=(.5,10))
        limits.update(denoise_distance_mm=(1,500),reflection_gap_mm=(20,2000),reflection_match_mm=(10,500),interior_band_mm=(5,200))
        limits.update(lining_bolt_height_mm=(0,1000),lining_bolt_min_points=(0,100000))
        if c.lining_bolt_kind not in ('recess','raised'):raise ValueError('Choose recessed bolt pockets or raised fittings.')
        limits.update(lining_sector_count=(0,24),lining_sector_offset_deg=(-360,360))
        limits.update(lining_depth_mm=(2,100),lining_seam_depth_mm=(2,100),lining_inward_mm=(10,500),lining_recess_mm=(50,1000),lining_bolt_max_mm=(50,800))
        limits.update(gusac_distance_mm=(1,200),gusac_sector_deg=(1,15),gusac_window_m=(.1,5),gusac_normal_deg=(1,85),gusac_min_support=(6,2000),tmatch_radius_mm=(2,200),tmatch_min_points=(3,1000000),tmatch_ring_width_m=(0,10))
        if c.lining_task not in ('all','bolts','seams','segments','non_lining','departures'):raise ValueError('Choose a lining analysis task.')
        if c.lining_detail not in ('fast','detailed','more_detailed'):raise ValueError('Choose Fast, Detailed or More detailed.')
        limits.update(irregular_sensitivity=(0,100),irregular_patch_size=(.05,2),irregular_min_points=(3,100000))
        limits.update(ray_station_m=(.05,5),ray_angle_deg=(.5,30),ray_depth_mm=(2,100),ray_keep_mm=(5,500),ray_min_points=(1,1000),ray_inward_mm=(100,5000),ray_outward_mm=(20,2000))
        for name,(lo,hi) in limits.items():
            try:value=float(getattr(c,name))
            except (TypeError,ValueError):raise ValueError(f'{name} must be a number.')
            if not math.isfinite(value) or not lo<=value<=hi: raise ValueError(f'{name} must be between {lo} and {hi}.')
            if name in ('lining_bolt_min_points','lining_sector_count','normal_neighbours','dbscan_min','edge_passes','max_points','noise_neighbours','noise_minimum','irregular_min_points','ray_min_points','gusac_min_support','tmatch_min_points'):
                if int(value)!=value:raise ValueError(f'{name} must be a whole number.')
                value=int(value)
            setattr(c,name,value)
        if c.lining_bolt_height_mm and c.lining_bolt_height_mm<=c.lining_depth_mm:raise ValueError('Maximum feature depth/height must exceed the minimum depth/height.')
        if c.lining_sector_count==1:raise ValueError('Choose 2–24 sectors per ring, or 0 for measured seams only.')
        if c.lining_depth_mm>=c.lining_recess_mm:raise ValueError('Recess allowance must exceed the feature depth threshold.')
        if c.method not in ('gusac','tmatch','noise_clean','tunnel_envelope','ray_box','smrg','irregular','combined_denoise','reflection','lining_features'):raise ValueError('Choose GuSAC cleaning, TMatch clustering, or an existing cleaning method.')
        if c.interior_cleanup not in ('standard','thorough'):raise ValueError('Choose standard or more thorough interior cleanup.')
        if c.denoise_shape not in ('measured','cylinder'):raise ValueError('Choose measured lining or a straight circular tunnel.')
        if c.noise_minimum>c.noise_neighbours:raise ValueError('Minimum support cannot exceed the neighbour count.')
        if c.graph_mode not in ('boundary','centroid'):raise ValueError('Choose boundary or centroid graph construction.')
        if c.convexity_mode not in ('printed','symmetric'):raise ValueError('Choose printed or symmetric convexity.')
        if not isinstance(c.repair,bool):raise ValueError('Repair must be true or false.')
        if c.interior is not None:
            a=np.asarray(c.interior,float)
            if a.shape!=(3,) or not np.isfinite(a).all():raise ValueError('Interior reference requires three finite coordinates.')
            c.interior=a.tolist()
        return c


def metric(a,b,na,nb,c):
    delta=b-a
    return c.alpha_normal*(1-np.clip(np.abs(np.sum(na*nb,axis=-1)),0,1))+c.alpha_space*np.linalg.norm(delta,axis=-1)/c.resolution+c.alpha_offset*(np.abs(np.sum(na*delta,axis=-1))+np.abs(np.sum(nb*delta,axis=-1)))/2


def pca(points):
    centred=points-points.mean(axis=0);cov=centred.T@centred/max(1,len(points));ev,vec=np.linalg.eigh(cov)
    return np.maximum(ev,0),vec


def features(points,labels,spacing):
    count=np.bincount(labels);m=len(count)
    centre=np.column_stack([np.bincount(labels,weights=points[:,j],minlength=m)/count for j in range(3)])
    delta=points-centre[labels];cov=np.empty((m,3,3))
    for i in range(3):
        for j in range(i,3):cov[:,i,j]=cov[:,j,i]=np.bincount(labels,weights=delta[:,i]*delta[:,j],minlength=m)/count
    ev,vec=np.linalg.eigh(cov);ev=np.maximum(ev,0);total=np.maximum(ev.sum(1),1e-30);large=np.maximum(ev[:,2],1e-30)
    root=np.sqrt(ev);den=np.maximum(root[:,2],1e-15);e1=(root[:,2]-root[:,1])/den;e2=(root[:,1]-root[:,0])/den;e3=root[:,0]/den
    return {'centre':centre,'normal':vec[:,:,0],'curvature':ev[:,0]/total,'linearity':(ev[:,2]-ev[:,1])/large,'planarity':ev[:,1]/total,'low':(e2>e1)&(e2>e3),'spacing':np.bincount(labels,weights=spacing)/count,'count':count}


def unique_edges(a,b):
    use=(a>=0)&(b>=0)&(a!=b);a=a[use];b=b[use]
    if not len(a):return np.empty((0,2),dtype=np.int32)
    return np.unique(np.sort(np.column_stack((a,b)),axis=1),axis=0).astype(np.int32)


def agglomerate(points,normals,neighbours,spacing,c,emit):
    n=len(points);cells=np.floor((points-points.min(0))/c.resolution).astype(np.int64)
    expected=max(1,len(np.unique(cells,axis=0)));parent=np.arange(n,dtype=np.int32);sizes=np.ones(n,dtype=np.int64)
    edges=unique_edges(np.repeat(np.arange(n),neighbours.shape[1]),neighbours.ravel());active=n;epsilon=max(float(np.median(spacing)),1e-12);rounds=0
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    while active>expected and epsilon<=1e20:
        emit(f'2/6 Supervoxels: {active:,} groups; target {expected:,}; merge pass {rounds+1}')
        while np.any(parent!=parent[parent]):parent=parent[parent]
        pairs=unique_edges(parent[edges[:,0]],parent[edges[:,1]])
        if not len(pairs):break
        # Symmetric graph, directed loss uses size of the absorbed neighbour.
        a=pairs[:,0];b=pairs[:,1];swap=sizes[a]<sizes[b];aa=np.where(swap,b,a);bb=np.where(swap,a,b)
        costs=metric(points[aa],points[bb],normals[aa],normals[bb],c)*sizes[bb]
        candidates=np.flatnonzero(costs<epsilon);candidates=candidates[np.argsort(costs[candidates],kind='stable')]
        for iteration,k in enumerate(candidates):
            if iteration%10000==0:emit(f'2/6 Merging supervoxels: {active:,} remaining')
            ra=root(int(aa[k]));rb=root(int(bb[k]))
            if ra==rb:continue
            if sizes[ra]<sizes[rb]:ra,rb=rb,ra
            loss=float(metric(points[ra],points[rb],normals[ra],normals[rb],c))*sizes[rb]
            if loss<epsilon:
                parent[rb]=ra;sizes[ra]+=sizes[rb];active-=1
                if active<=expected:break
        epsilon*=2;rounds+=1
    while np.any(parent!=parent[parent]):parent=parent[parent]
    labels=parent.copy()
    for iteration in range(c.edge_passes):
        emit(f'2/6 Boundary refinement {iteration+1}/{c.edge_passes}')
        updated=labels.copy()
        for start in range(0,n,8192):
            stop=min(n,start+8192);p=points[start:stop,None,:];pn=normals[start:stop,None,:];options=labels[neighbours[start:stop]]
            score=metric(p,points[options],pn,normals[options],c);best=np.argmin(score,axis=1);own=metric(points[start:stop],points[labels[start:stop]],normals[start:stop],normals[labels[start:stop]],c)
            use=score[np.arange(stop-start),best]<own-1e-12;new=options[np.arange(stop-start),best];updated[start:stop][use]=new[use]
        if np.array_equal(updated,labels):break
        labels=updated
    _,labels=np.unique(labels,return_inverse=True)
    return labels.astype(np.int32),expected,rounds


def split_density(points,labels,spacing,c,emit):
    result=np.full(len(points),-1,dtype=np.int32);groups=np.argsort(labels,kind='stable');counts=np.bincount(labels);starts=np.r_[0,np.cumsum(counts)];nextlabel=0
    for group in range(len(counts)):
        if group%100==0:emit(f'2/6 Density splitting: {group:,}/{len(counts):,} supervoxels')
        ids=groups[starts[group]:starts[group+1]];q=points[ids]
        if len(ids)<c.dbscan_min:continue
        eps=max(float(np.median(spacing[ids]))*c.dbscan_factor,1e-10);tree=cKDTree(q);counts_local=tree.query_ball_point(q,eps,return_length=True);core=counts_local>=c.dbscan_min;local=np.full(len(ids),-1,dtype=np.int32)
        for seed in np.flatnonzero(core):
            if local[seed]>=0:continue
            local[seed]=nextlabel;queue=deque([int(seed)])
            processed=0
            while queue:
                processed+=1
                if processed%2048==0:emit(f'2/6 Density splitting: {group:,}/{len(counts):,} supervoxels; {processed:,} local points')
                i=queue.popleft();near=np.asarray(tree.query_ball_point(q[i],eps),dtype=int);new=near[local[near]<0];local[new]=nextlabel
                queue.extend(new[core[new]].tolist())
            nextlabel+=1
        result[ids]=local
    return result


def adjacency(points,pointlabels,knn,dist,f,c,emit):
    emit('3/6 Building adaptive neighbourhood graph')
    if c.graph_mode=='boundary':
        # Explicit practical interpretation: point-boundary contact at 2*d, rather than
        # overlapping tiny spheres around much more widely spaced supervoxel centroids.
        a=np.repeat(pointlabels,knn.shape[1]);b=pointlabels[knn.ravel()];d=dist.ravel();valid=(a>=0)&(b>=0)&(a!=b)
        a=a[valid];b=b[valid];d=d[valid];keep=d<=2*np.maximum(f['spacing'][a],f['spacing'][b])+1e-12
        edges=unique_edges(a[keep],b[keep])
    else:
        tree=cKDTree(points);shared={};pairs=[]
        for i,centre in enumerate(f['centre']):
            for q in tree.query_ball_point(centre,2*f['spacing'][i]):shared.setdefault(q,[]).append(i)
        for ids in shared.values():
            for i in range(len(ids)):
                for j in range(i+1,len(ids)):pairs.append((ids[i],ids[j]))
        edges=np.unique(np.asarray(pairs,dtype=np.int32).reshape(-1,2),axis=0) if pairs else np.empty((0,2),np.int32)
    return edges


def graph_lists(edges,n):
    graph=[[] for _ in range(n)]
    for a,b in edges:graph[int(a)].append(int(b));graph[int(b)].append(int(a))
    return graph


def orient_normals(f,edges,interior):
    normals=f['normal'].copy();m=len(normals)
    if len(edges):
        weights=1-np.clip(np.abs(np.sum(normals[edges[:,0]]*normals[edges[:,1]],axis=1)),0,1)+1e-8
        mat=coo_matrix((np.r_[weights,weights],(np.r_[edges[:,0],edges[:,1]],np.r_[edges[:,1],edges[:,0]])),shape=(m,m)).tocsr()
        mst=minimum_spanning_tree(mat).tocoo();graph=graph_lists(np.c_[mst.row,mst.col],m)
    else:graph=[[] for _ in range(m)]
    visited=np.zeros(m,bool);order=np.argsort(np.linalg.norm(f['centre']-interior,axis=1))[::-1]
    for seed in order:
        if visited[seed]:continue
        if np.dot(normals[seed],f['centre'][seed]-interior)<0:normals[seed]*=-1
        visited[seed]=True;stack=[int(seed)]
        while stack:
            i=stack.pop()
            for j in graph[i]:
                if visited[j]:continue
                if np.dot(normals[i],normals[j])<0:normals[j]*=-1
                visited[j]=True;stack.append(j)
    f['normal']=normals


def prune_graph(f,edges,c,emit):
    emit('3/6 Rejecting cross-structure links with PCA and MAD residuals')
    graph=graph_lists(edges,len(f['centre']));remove=set();centres=f['centre']
    for i,near in enumerate(graph):
        if i%1000==0:emit(f'3/6 Checking adjacency fits: {i:,}/{len(graph):,}')
        if len(near)<3:continue
        local=centres[[i,*near]];ev,vec=pca(local);delta=local[1:]-local.mean(0)
        if (ev[2]-ev[1])/max(ev[2],1e-30)>=c.linearity:
            axis=vec[:,2];residual=np.linalg.norm(delta-(delta@axis)[:,None]*axis,axis=1)
        else:residual=np.abs(delta@vec[:,0])
        med=float(np.median(residual));mad=float(np.median(np.abs(residual-med)));threshold=med+c.residual_k*1.4826*mad+1e-12
        for j in np.asarray(near)[residual>threshold]:remove.add((min(i,int(j)),max(i,int(j))))
    keep=np.array([tuple(e) not in remove for e in edges],bool)
    return edges[keep],len(remove)


def growth_allowed(ti,tj,ni,nj,ci,cj,c):
    delta=cj-ci;length=np.linalg.norm(delta)
    if length<1e-14:return False
    cosine=float(np.dot(ni,nj));offset=(abs(np.dot(ni,delta))+abs(np.dot(nj,delta)))/2
    # Eq. 19 as printed is directional. Symmetric is an explicitly selectable variant.
    z=float(np.dot(ni+nj if c.convexity_mode=='printed' else ni-nj,delta/length))
    similar=cosine>math.cos(math.radians(c.angle_deg));close=offset<=c.offset;convex=z>=0
    if ti and tj:return (similar and close) or (convex and close)
    if ti!=tj:return similar and close and convex
    return similar or close


def grow(f,edges,c,emit):
    graph=graph_lists(edges,len(f['centre']));tiers=[];scores=[]
    for i,near in enumerate(graph):
        if not near:tier=3
        elif np.all(f['low'][near]):tier=0
        elif f['low'][i]:tier=1
        else:tier=2
        # Eq.16 repeats G for Q3; accompanying prose specifies L. G is interpreted as O.
        score=f['curvature'][i] if tier==3 else 1-(f['linearity'][i] if tier==2 else f['planarity'][i])+.1*f['curvature'][i]
        tiers.append(tier);scores.append(score)
    order=np.lexsort((np.arange(len(tiers)),scores,tiers));regions=np.full(len(tiers),-1,np.int32);count=0
    for seed in order:
        if regions[seed]>=0:continue
        if count%100==0:emit(f'4/6 Growing surface regions: {count:,} clusters')
        regions[seed]=count;queue=deque([int(seed)])
        while queue:
            i=queue.popleft()
            for j in graph[i]:
                if regions[j]>=0:continue
                if growth_allowed(f['low'][i],f['low'][j],f['normal'][i],f['normal'][j],f['centre'][i],f['centre'][j],c):
                    regions[j]=count
                    if f['curvature'][j]<=c.curvature:queue.append(j)
        count+=1
    return regions,np.asarray(tiers)


def hull_vertices(points):
    if len(points)<4:return points
    centre=points.mean(0);_,s,vt=np.linalg.svd(points-centre,full_matrices=False);rank=int(np.sum(s>max(s[0]*1e-10,1e-12)))
    if rank==0:return points[:1]
    if rank==1:
        t=(points-centre)@vt[0];return points[[np.argmin(t),np.argmax(t)]]
    projected=(points-centre)@vt[:rank].T
    try:return points[ConvexHull(projected).vertices]
    except QhullError:return points  # conservative rank fallback, recorded in method notes


def recover(points,labels,spacing,c,emit,weights=None):
    """Same exact boundary-overlap decisions with bounded tree rebuild work.

    Formerly each cluster scanned every point, and every accepted cluster
    rebuilt the entire growing retained tree. Stable group indexing removes
    the repeated N*C scans. A binary-sized forest answers the identical
    nearest-distance query; each retained point is rebuilt O(log N) times.
    Neither points nor overlap tests are sampled.
    """
    valid=labels>=0
    counts=np.bincount(labels[valid],weights=None if weights is None else weights[valid]);main=int(np.argmax(counts));kept=[main];overlap={main:1.0}
    if not c.repair:return kept,overlap
    indices=np.flatnonzero(valid);indices=indices[np.argsort(labels[indices],kind='stable')]
    raw_counts=np.bincount(labels[valid],minlength=len(counts));starts=np.r_[0,np.cumsum(raw_counts)]
    def cluster_points(cluster):return points[indices[starts[cluster]:starts[cluster+1]]]
    forest={}
    def add(q):
        level=max(0,len(q).bit_length()-1)
        while level in forest:
            q=np.concatenate([forest.pop(level).data,q]);level=max(0,len(q).bit_length()-1)
        emit(f'5/6 Indexing retained boundary support: {len(q):,} locations')
        forest[level]=cKDTree(q)
    add(cluster_points(main));radius=2*float(np.mean(spacing));order=np.argsort(counts)[::-1]
    for k,cluster in enumerate(order):
        if cluster==main or not raw_counts[cluster]:continue
        if k%100==0:emit(f'5/6 Checking convex-hull boundary overlap: {k:,}/{len(order):,}')
        q=cluster_points(cluster);hull=hull_vertices(q);distance=np.full(len(hull),np.inf)
        for tree in forest.values():distance=np.minimum(distance,tree.query(hull,k=1)[0])
        ratio=float(np.mean(distance<=radius+1e-12));overlap[int(cluster)]=ratio
        if ratio>c.overlap:kept.append(int(cluster));add(q)
    return kept,overlap



def extract_tunnel_envelope(points,c,progress=lambda s:None,cancel=lambda:False):
    """In-memory compatibility entry point using the same whole-area model."""
    import tempfile
    from pathlib import Path
    import tunnel_lining
    raw=np.asarray(points,float)
    def batches():yield np.arange(len(raw)),raw,{'mode':'full'}
    with tempfile.TemporaryDirectory(prefix='lining_') as folder:
        ids=Path(folder)/'ids';labels=Path(folder)/'labels'
        r=tunnel_lining.run_batches(batches,ids,labels,asdict(c),progress,cancel)
        r['labels']=np.fromfile(labels,'<i4')
    r.update(settings=asdict(c),method='One supported tunnel lining model; original-point classification',doi='',method_code='tunnel_envelope')
    return r

def extract(points,settings=None,progress=lambda s:None,cancel=lambda:False):
    c=Settings.read(settings);raw=np.asarray(points,dtype=float)
    if raw.ndim!=2 or raw.shape[1]!=3 or not np.isfinite(raw).all():raise ValueError('Input must be finite XYZ in metres.')
    if c.method=='noise_clean':raise ValueError('Use the spatial Clean Tunnel workflow for noise screening.')
    if c.method in ('gusac','tmatch'):
        # Compatibility for callers that already own a small XYZ array. The
        # application routes file-backed scans directly to run_batches.
        import tempfile
        from pathlib import Path
        if len(raw)<50:raise ValueError('Select at least 50 points for tunnel analysis.')
        def batches():
            for start in range(0,len(raw),min(c.max_points,300000)):
                end=min(len(raw),start+min(c.max_points,300000))
                yield np.arange(start,end,dtype=np.int64),raw[start:end],{'mode':'full'}
        with tempfile.TemporaryDirectory(prefix=c.method+'_') as folder:
            ip=Path(folder)/'ids';lp=Path(folder)/'labels'
            r=__import__(c.method).run_batches(batches,Path(folder),ip,lp,asdict(c),progress,cancel,len(raw))
            ids=np.fromfile(ip,'<i8');labels=np.fromfile(lp,'<i4')
            if len(ids)!=len(raw) or len(labels)!=len(raw):raise ValueError('Incomplete classification output.')
            out=np.empty(len(raw),np.int32);out[ids]=labels;r['labels']=out
        r.update(settings=asdict(c),method=c.method.upper()+'-based interpretation of Wang et al. (2025)',doi='10.3390/app15062926',method_code=c.method)
        return r
    if c.method=='tunnel_envelope':return extract_tunnel_envelope(raw,c,progress,cancel)
    if len(raw)>c.max_points:raise ValueError(f'Internal SMRG batch contains {len(raw):,} points, above the {c.max_points:,} batch limit. Reduce the advanced batch size.')
    if len(raw)<max(50,c.normal_neighbours+1):raise ValueError('Select at least 50 points for surface extraction.')
    def emit(s):
        if cancel():raise ValueError('Surface extraction cancelled. The previous result and source scan are unchanged.')
        progress(s)
    started=time.monotonic();origin=raw.min(0);q=raw-origin;points,inverse,multiplicity=np.unique(q,axis=0,return_inverse=True,return_counts=True);n=len(points)
    if n<max(50,c.normal_neighbours+1):raise ValueError('Fewer than 50 distinct coordinate locations.')
    emit('1/6 Estimating point normals and spacing (15-neighbour PCA by default)')
    tree=cKDTree(points);dist,index=tree.query(points,k=c.normal_neighbours+1,workers=__import__('compute_runtime').workers());knn=index[:,1:].astype(np.int32);near_dist=dist[:,1:];spacing=near_dist[:,0];normals=np.empty((n,3))
    for start in range(0,n,8192):
        emit(f'1/6 Point normals: {min(start+8192,n):,}/{n:,}');local=points[index[start:start+8192,1:]];local=local-local.mean(axis=1,keepdims=True);cov=np.einsum('nki,nkj->nij',local,local)/c.normal_neighbours;_,vec=np.linalg.eigh(cov);normals[start:start+len(local)]=vec[:,:,0]
    labels,expected,passes=agglomerate(points,normals,knn,spacing,c,emit)
    labels=split_density(points,labels,spacing,c,emit);valid=labels>=0
    if valid.sum()<c.dbscan_min:raise ValueError('No dense supervoxels survived. Check point spacing, resolution and density settings.')
    # Relabel densely after noise removal; all original point identities are retained.
    _,labels[valid]=np.unique(labels[valid],return_inverse=True);f=features(points[valid],labels[valid],spacing[valid]);m=len(f['centre'])
    initial=adjacency(points,labels,knn,near_dist,f,c,emit)
    interior=np.asarray(c.interior)-origin if c.interior is not None else (points.min(0)+points.max(0))/2
    orient_normals(f,initial,interior);edges,pruned=prune_graph(f,initial,c,emit);svregions,tiers=grow(f,edges,c,emit)
    clusterlabels=np.full(n,-1,np.int32);clusterlabels[valid]=svregions[labels[valid]]
    kept,overlap=recover(points,clusterlabels,spacing,c,emit,multiplicity);labels_original=clusterlabels[inverse];counts=np.bincount(labels_original[labels_original>=0]);clusters=[]
    for i,count in enumerate(counts):
        use=svregions==i;weights=f['count'][use];centre=np.average(f['centre'][use],axis=0,weights=weights)+origin
        clusters.append({'id':int(i),'points':int(count),'supervoxels':int(use.sum()),'centre':centre.tolist(),'linearity':float(np.average(f['linearity'][use],weights=weights)),'planar_fraction':float(np.average(f['low'][use],weights=weights)),'overlap':overlap.get(i,0),'automatic_keep':i in kept})
    clusters.sort(key=lambda x:x['points'],reverse=True);kept_count=int(np.isin(labels_original,kept).sum());warnings=[]
    if not len(initial):warnings.append('No supervoxel graph edges: inspect density and graph mode; centroid shared-neighbour mode can be too restrictive.')
    if kept_count/len(raw)<.5:warnings.append('The automatic surface suggestion contains less than half of the selected points. Review the highlighted result before applying it; if tunnel lining is missing, pick an interior point in the main 3D view or try Tunnel lining envelope.')
    if kept_count/len(raw)>.99:warnings.append('Almost all points were selected. This does not prove that pipes or other objects were removed.')
    if c.interior is None:warnings.append('Tunnel Studio inferred the interior reference automatically. If the suggested surface is on the wrong side, use Pick interior point and click inside the tunnel void in the main 3D view.')
    warnings.append('SMRG groups connected surface patches; its largest-region suggestion is not a semantic tunnel-versus-pipe classifier. Use primary GuSAC cleaning for lining extraction and review the candidates.')
    warnings.append('Closely attached cables, thin surface gaps and incomplete scans can be misclassified. Review the coloured result before applying.')
    emit('6/6 Preparing reviewable extraction result')
    return {'labels':labels_original,'kept':kept,'clusters':clusters,'settings':asdict(c),'warnings':warnings,'statistics':{'input_points':len(raw),'unique_points':n,'duplicate_locations':len(raw)-n,'supervoxels':m,'expected_supervoxels':expected,'merge_passes':passes,'initial_edges':len(initial),'removed_edges':pruned,'retained_edges':len(edges),'clusters':len(clusters),'kept_points':kept_count,'noise_points':int((labels_original<0).sum()),'spacing_m':float(spacing.mean()),'seconds':round(time.monotonic()-started,3)},'method':'SMRG research implementation, Song et al. 2026; independent interpretation, not author-validated.','doi':'10.1038/s41598-026-73755-6','method_code':'smrg'}
