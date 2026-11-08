# Copyright (c) 2026 Damian Gulczynski.
"""Memory-bounded point-cloud surface meshing for Tunnel Studio.

The generic methods here deliberately avoid requiring a tunnel alignment.  They
work from a scan item or current 3D selection.  Tunnel-grid / design-aware
meshing remains a separate alignment-aware workflow.
"""
from pathlib import Path
import math, uuid
import compute_runtime
import parallel_meshing
import numpy as np
from scipy.spatial import Delaunay, ConvexHull, cKDTree, QhullError
import jobdata,registration_pose
import mesh_geometry as geom
from bolt_gaps import fill_bolt_gaps
from functools import lru_cache


@lru_cache(maxsize=1)
def capabilities():
    methods={
        'tunnel_surface': {'available': True, 'label':'Create Tunnel Surface', 'note':'Joined lining grid for one straight tunnel branch; median radial fit and bounded gap interpolation.'},
        'auto_scan': {'available': True, 'label':'Scan to Mesh · Auto', 'note':'Chooses a practical reconstruction from the selected geometry; designed for quick global meshes.'},
        'pca_tunnel': {'available': True, 'label':'Tunnel unwrap (PCA)', 'note':'Best for a single tunnel branch; no alignment required.'},
        'watertight_tunnel': {'available': True, 'label':'Capped tunnel (PCA)', 'note':'Tunnel unwrap plus boundary capping; intended for one separated tunnel branch.'},
        'delaunay': {'available': True, 'label':'2D / Planar Delaunay', 'note':'Best for walls, slabs, terrain patches and locally planar selections.'},
        'alpha_shape': {'available': True, 'label':'3D Alpha Shape', 'note':'Boundary of Delaunay tetrahedra; alpha radius controls detail and bridging. Up to 100,000 working points.'},
        'convex_hull': {'available': True, 'label':'Convex hull', 'note':'Fast closed outer envelope; fills concavities/openings.'},
    }
    try:
        import open3d  # noqa: F401
        methods['ball_pivoting']={'available':True,'label':'Ball Pivoting','note':'Preserves local shape when point density and normals are suitable.'}
    except Exception:
        methods['ball_pivoting']={'available':False,'label':'Ball Pivoting','note':'Optional Open3D engine is not installed in this desktop runtime.'}
    methods['poisson']={'available':methods['ball_pivoting']['available'],'label':'Screened Poisson','note':'Smooth 3D reconstruction from consistently oriented normals; requires Open3D.'}
    try:
        from skimage import measure  # noqa: F401
        methods['marching_cubes']={'available':True,'label':'Voxel surface · Marching Cubes','note':'Extracts a voxel isosurface. Inspect its boundary counts and source deviation.'}
    except Exception:
        methods['marching_cubes']={'available':False,'label':'Voxel surface · Marching Cubes','note':'Optional scikit-image engine is not installed in this desktop runtime.'}
    return methods


def working_budget(method,quality='balanced'):
    """Internal resource choice; users choose quality, not point counts."""
    requested={'fast':120000,'balanced':500000,'fine':1200000}.get(quality)
    if requested is None:raise ValueError('Choose Fast, Balanced or Fine quality.')
    available=4*1024**3
    try:
        import psutil
        available=psutil.virtual_memory().available
    except ImportError:
        try:
            import os
            available=os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
        except (AttributeError,ValueError,OSError):pass
    cap={'alpha_shape':80000,'poisson':600000,'marching_cubes':500000}.get(method,1200000)
    return max(10000,min(requested,cap,int(available*.15/600)))


def _retained_full_sample(meta,max_points,progress,selection=None):
    """Sample original retained IDs, reading XYZ only for selected rows."""
    n=int(meta['count']);max_points=max(3,min(2000000,int(max_points)))
    if selection:
        total=sum(len(ids) for ids in jobdata.selection_ids(meta,selection,progress))
        batches=jobdata.selection_ids(meta,selection,progress)
    elif meta.get('mask_file'):
        total=__import__('scan_edits').properties(meta,progress)['count']
        def retained():
            mask=np.memmap(meta['mask_file'],'u1','r',shape=(n,))
            try:
                for start in range(0,n,500000):
                    progress(f'Reading retained point identities · {min(n,start+500000):,}/{n:,}')
                    yield np.flatnonzero(mask[start:start+500000]==0)+start
            finally:mask._mmap.close()
        batches=retained()
    else:
        ids=jobdata._display_sample_ids(n,max_points)
        points=registration_pose.points(meta,jobdata._display_rows(meta['file'],n,ids))
        points=points[np.isfinite(points).all(1)]
        if len(points)<3:raise ValueError('Too few finite retained points to mesh.')
        return points,n
    if total<3:raise ValueError('The selected area contains too few retained points for meshing.')
    wanted=jobdata._display_sample_ids(total,max_points);offset=0;parts=[]
    for ids in batches:
        lo,hi=np.searchsorted(wanted,[offset,offset+len(ids)])
        if hi>lo:parts.append(ids[wanted[lo:hi]-offset])
        offset+=len(ids)
    selected=np.concatenate(parts) if parts else np.empty(0,np.int64);selected.sort()
    points=registration_pose.points(meta,jobdata._display_rows(meta['file'],n,selected));points=points[np.isfinite(points).all(1)]
    if len(points)<3:raise ValueError('Too few finite retained points to mesh.')
    return points,total


def _voxel_downsample(points, distance, progress=lambda s:None):
    distance=float(distance or 0)
    if distance<=0 or len(points)<4:return points
    lo=points.min(axis=0);keys=np.floor((points-lo)/distance).astype(np.int64)
    # deterministic centroid per occupied voxel
    _,inv=np.unique(keys,axis=0,return_inverse=True)
    counts=np.bincount(inv);out=np.empty((len(counts),3),dtype=np.float64)
    for axis in range(3):out[:,axis]=np.bincount(inv,weights=points[:,axis])/counts
    progress(f'Point sampling distance {distance:.4f} m · {len(points):,} → {len(out):,} points')
    return out


def _noise_filter(points, max_gap, progress=lambda s:None, cancel=None):
    max_gap=float(max_gap or 0)
    if max_gap<=0 or len(points)<8:return points
    if compute_runtime.workers()>1 and len(range(0,len(points),100000))>=4:
        return parallel_meshing.noise_filter_parallel(points,max_gap,progress,cancel)
    compute_runtime.check_cancel(cancel)
    tree=cKDTree(points);dist,_=tree.query(points,k=2,workers=1)
    compute_runtime.check_cancel(cancel)
    keep=np.isfinite(dist[:,1])&(dist[:,1]<=max_gap)
    out=points[keep]
    progress(f'Noise filter ≤ {max_gap:.4f} m neighbour gap · kept {len(out):,} / {len(points):,} points')
    if len(out)<3:raise ValueError('Noise filtering removed too many points. Increase the noise gap or turn the filter off.')
    return out


def _boundary_loops(faces):
    f=np.asarray(faces,dtype=np.int64)
    edge_count={}
    for a,b,c in f:
        for u,v in ((a,b),(b,c),(c,a)):
            key=(min(int(u),int(v)),max(int(u),int(v)))
            edge_count[key]=edge_count.get(key,0)+1
    edges=[e for e,n in edge_count.items() if n==1]
    if not edges:return []
    adj={}
    for a,b in edges:adj.setdefault(a,[]).append(b);adj.setdefault(b,[]).append(a)
    unused={tuple(sorted(e)) for e in edges};loops=[]
    while unused:
        e=next(iter(unused));start,cur=e;loop=[start,cur];unused.discard(e);prev=start
        while True:
            nxts=[n for n in adj.get(cur,[]) if n!=prev and tuple(sorted((cur,n))) in unused]
            if not nxts:break
            nxt=nxts[0];unused.discard(tuple(sorted((cur,nxt))));prev,cur=cur,nxt
            if cur==start:break
            loop.append(cur)
        if len(loop)>=3 and cur==start:loops.append(loop)
    return loops


def _fill_boundary_loops(vertices,faces,mode='preserve',max_diameter=.5,progress=lambda s:None,return_summary=False):
    v,f=geom.clean_mesh(vertices,faces)
    if mode=='preserve':return (v,f,0,{'filled':0}) if return_summary else (v,f,0)
    # Use the same portal, topology, extent and overlap checks as selected repair.
    # The legacy "watertight" option no longer silently caps tunnel ends.
    import mesh_repair
    v,f,summary=mesh_repair.fill_small(v,f,max_diameter,progress)
    progress(f"Boundary repair: {summary['filled']} filled, {summary['preserved']} preserved, {summary['unfilled']} unfilled. Use Preview / repair holes for individual reasons.")
    return (v,f,summary['filled'],summary) if return_summary else (v,f,summary['filled'])


def _vertex_adjacency(vertex_count,faces):
    """Native sparse graph; shared vertices connect faces, including seams."""
    from scipy.sparse import coo_matrix
    f=np.asarray(faces,dtype=np.int64)
    graph=coo_matrix((np.ones(f.size,dtype=bool),(f.ravel(),f[:,[1,2,0]].ravel())),shape=(vertex_count,vertex_count)).tocsr()
    return graph.maximum(graph.T)


def _largest_component(vertices,faces):
    from scipy.sparse.csgraph import connected_components
    v=np.asarray(vertices,float);f=np.asarray(faces,dtype=np.int64)
    if not len(f):return v,np.asarray(faces,dtype=np.uint32)
    graph=_vertex_adjacency(len(v),f)
    _,labels=connected_components(graph,directed=False)
    face_labels=labels[f[:,0]]
    counts=np.bincount(face_labels)
    # Deterministic tie break: first component appearing in the face array.
    winners=set(np.flatnonzero(counts==counts.max()).tolist())
    chosen=next(int(x) for x in face_labels if int(x) in winners)
    keep=f[face_labels==chosen]
    used,remap=np.unique(keep,return_inverse=True)
    return v[used],remap.reshape(-1,3).astype(np.uint32)


def _vertex_cluster(vertices,faces,cell):
    cell=float(cell or 0)
    if cell<=0:return np.asarray(vertices,float),np.asarray(faces,dtype=np.uint32)
    v=np.asarray(vertices,float);f=np.asarray(faces,dtype=np.int64);lo=v.min(0);keys=np.floor((v-lo)/cell).astype(np.int64)
    _,inv=np.unique(keys,axis=0,return_inverse=True);counts=np.bincount(inv);out=np.empty((len(counts),3),float)
    for axis in range(3):out[:,axis]=np.bincount(inv,weights=v[:,axis])/counts
    nf=inv[f];keep=(nf[:,0]!=nf[:,1])&(nf[:,1]!=nf[:,2])&(nf[:,0]!=nf[:,2]);nf=nf[keep]
    if len(nf):
        _,idx=np.unique(np.sort(nf,axis=1),axis=0,return_index=True);nf=nf[np.sort(idx)]
    return out,np.asarray(nf,dtype=np.uint32)


def _smoothed_voxels(grid, sigma, progress, cancel=None):
    """Independent halo slabs, equivalent to filtering the complete grid.

    This parallelises the scalar field, not separate triangle meshes. The
    single global isosurface below retains connectivity across slab boundaries.
    Input point-stream chunks remain unchanged at 100,000 rows.
    """
    return parallel_meshing.smoothed_voxel_field_parallel(grid,sigma,progress,cancel)


def marching_cubes(points,voxel=.08,smoothing=1,progress=lambda s:None,cancel=None):
    try:
        from skimage import measure
    except Exception as e:raise ValueError('Marching Cubes is not available because the optional scikit-image engine is not installed.') from e
    points=parallel_meshing._xyz(points)
    if len(points)<3:raise ValueError('At least three points are required for voxel reconstruction.')
    lo,voxel,dims=parallel_meshing._grid_spec(points,voxel)
    progress(f'Voxelising cloud · {tuple(int(x) for x in dims)} cells · {voxel:.4f} m')
    grid=np.zeros(tuple(dims),dtype=np.uint8)
    for start in range(0,len(points),100000):
        compute_runtime.check_cancel(cancel)
        idx=np.clip(np.floor((points[start:start+100000]-lo)/voxel),0,dims-1).astype(np.int64)
        grid[idx[:,0],idx[:,1],idx[:,2]]=1
    field=_smoothed_voxels(grid,max(.6,float(smoothing or 1)),progress,cancel)
    if float(field.max())<=0:raise ValueError('Voxel reconstruction produced an empty field.')
    progress('Extracting voxel triangle faces…')
    verts,faces,_,_=measure.marching_cubes(field,level=max(.15,float(field.max())*.35),spacing=(voxel,voxel,voxel),allow_degenerate=False)
    compute_runtime.check_cancel(cancel)
    verts=verts+lo
    return np.asarray(verts,float),np.asarray(faces,dtype=np.uint32),{'voxel_m':voxel,'warning':'Marching Cubes extracts the boundary of a smoothed occupied-voxel field. It can thicken a scanned surface and produce inner and outer sheets; it does not guarantee a watertight lining or survey accuracy. Inspect connected surfaces and source deviation.'}


def auto_scan(points,spacing=.12,degrees=2,max_edge=.65,smoothing=0,progress=lambda s:None,cancel=None):
    compute_runtime.check_cancel(cancel)
    centre,axes,vals=_pca_frame(points);v0,v1,v2=[float(x) for x in vals]
    if v1>0 and v2/max(v1,1e-30)<.06:
        progress('Auto Scan to Mesh chose planar Delaunay from PCA geometry.')
        v,f,e=planar_delaunay(points,max_edge,smoothing,progress,cancel);e['auto_choice']='delaunay';return v,f,e
    # Tube-like geometry: one PCA direction differs while the other two variances are similar.
    raw=np.linalg.eigvalsh(np.cov(points.T));pairs=[]
    for axis in range(3):
        rad=[i for i in range(3) if i!=axis];ratio=max(raw[rad])/max(min(raw[rad]),1e-30);contrast=max(raw[axis],(raw[rad[0]]+raw[rad[1]])/2)/max(min(raw[axis],(raw[rad[0]]+raw[rad[1]])/2),1e-30);pairs.append((ratio,-contrast,axis))
    ratio,negcontrast,_=min(pairs)
    if ratio<1.35 and -negcontrast>1.18:
        progress('Auto Scan to Mesh chose tunnel unwrap from tube-like geometry.')
        v,f,e=tunnel_unwrap(points,spacing,degrees,max_edge,smoothing,progress);e['auto_choice']='pca_tunnel';return v,f,e
    if capabilities()['ball_pivoting']['available']:
        progress('Auto chose Ball Pivoting for a general 3D surface.')
        v,f,e=ball_pivoting(points,None,smoothing,progress);e['auto_choice']='ball_pivoting';return v,f,e
    raise ValueError('Auto could not identify a plane or a single tunnel branch. Choose 3D Alpha Shape (up to 100,000 working points), or install Open3D for Ball Pivoting/Poisson. Convex hull is available only as an explicit envelope method.')


def _pca_frame(points):
    centre=np.mean(points,axis=0);q=points-centre
    cov=(q.T@q)/max(len(q)-1,1)
    vals,vecs=np.linalg.eigh(cov);order=np.argsort(vals)[::-1];axes=vecs[:,order]
    # Deterministic right-handed frame.
    if np.linalg.det(axes)<0:axes[:,2]*=-1
    return centre,axes,vals[order]


def _tube_frame(points):
    """Choose the tunnel axis as the eigenvector whose orthogonal variances are most similar.

    This is more stable than blindly taking the largest-variance PCA axis when the selected
    tunnel portion is short relative to its diameter.
    """
    origin=np.asarray(points[0],float);centre=origin+np.mean(points-origin,axis=0)
    q=points-centre;cov=(q.T@q)/max(len(q)-1,1)
    return _tube_frame_covariance(centre,cov)


def _tube_frame_covariance(centre,cov):
    """Shared frame choice for array and stable full-source streaming paths."""
    vals,vecs=np.linalg.eigh(cov)
    best=None
    for axis in range(3):
        radial=[i for i in range(3) if i!=axis];a,b=float(vals[radial[0]]),float(vals[radial[1]])
        similarity=abs(math.log(max(a,b,1e-30)/max(min(a,b),1e-30)))
        contrast=abs(math.log(max(float(vals[axis]),1e-30)/max((a+b)/2,1e-30)))
        score=similarity-.15*contrast
        if best is None or score<best[0]:best=(score,axis,radial)
    _,axis,radial=best;axes=np.column_stack((vecs[:,axis],vecs[:,radial[0]],vecs[:,radial[1]]))
    if abs(float(vals[radial[0]]-vals[radial[1]]))<=max(abs(float(vals[radial[0]])),abs(float(vals[radial[1]])),1e-30)*1e-10:
        # A circular cross-section has no preferred radial eigenvector. Tiny
        # rounding changes must not randomly rotate its angular bin boundaries.
        direction=axes[:,0].copy()
        if direction[np.argmax(np.abs(direction))]<0:direction=-direction
        reference=np.array([0.,1.,0.]) if abs(direction[0])>.9 else np.array([1.,0.,0.])
        transverse=reference-direction*np.dot(reference,direction);transverse/=np.linalg.norm(transverse)
        axes=np.column_stack((direction,transverse,np.cross(direction,transverse)))
    if np.linalg.det(axes)<0:axes[:,2]*=-1
    return centre,axes,np.array([vals[axis],vals[radial[0]],vals[radial[1]]],float)


def _laplacian(vertices,faces,iterations=0,strength=.35):
    iterations=max(0,min(20,int(iterations)))
    if not iterations or not len(faces):return vertices
    v=np.asarray(vertices,float).copy()
    graph=_vertex_adjacency(len(v),faces).astype(float)
    degree=np.asarray(graph.sum(axis=1)).ravel()
    use=degree>0
    for _ in range(iterations):
        means=graph@v
        v[use]=(1-strength)*v[use]+strength*means[use]/degree[use,None]
    return v


def tunnel_unwrap(points,spacing=.12,degrees=2.0,max_edge=.65,smoothing=0,progress=lambda s:None):
    """Mesh a single tube-like branch using PCA axis + cylindrical unwrap.

    This is intentionally not suitable for an unseparated T-junction; users
    should Separate the main tunnel/cross passage first.
    """
    spacing=max(.005,float(spacing));degrees=max(.25,min(20,float(degrees)));max_edge=max(.01,float(max_edge))
    centre,axes,vals=_tube_frame(points);local=(points-centre)@axes
    s=local[:,0];theta=np.arctan2(local[:,2],local[:,1]);ds=max(spacing,(s.max()-s.min())/2500 if s.max()>s.min() else spacing)
    dtheta=math.radians(degrees);ns=max(2,int(math.ceil((s.max()-s.min())/ds))+1);nt=max(12,int(math.ceil(2*math.pi/dtheta)))
    if ns*nt>2_500_000:
        factor=math.sqrt(ns*nt/2_500_000);ds*=factor;ns=max(2,int(math.ceil((s.max()-s.min())/ds))+1)
    from mesh_sampling import coordinate_roundoff,stable_floor
    error=coordinate_roundoff(points.min(0),points.max(0));radius_each=np.maximum(np.linalg.norm(local[:,1:],axis=1),.05)
    si=np.clip(stable_floor((s-s.min())/max(ds,1e-12),error/ds),0,ns-1)
    ti=stable_floor((theta+math.pi)/(2*math.pi)*nt,error/(radius_each*(2*math.pi/nt)))%nt
    key=si*nt+ti;order=np.argsort(key);key_sorted=key[order];pts_sorted=(points-centre)[order]
    uniq,starts,counts=np.unique(key_sorted,return_index=True,return_counts=True)
    # Summing survey eastings/northings can move equal-angle means across the
    # periodic seam by roundoff. Average local deltas and restore the origin.
    sums=np.add.reduceat(pts_sorted,starts,axis=0);verts=centre+sums/counts[:,None]
    # Delaunay in a periodic cylindrical parameter domain: interpolate only
    # between occupied cells, instead of requiring three neighbouring grid cells.
    local_v=(verts-centre)@axes
    radius=float(np.median(np.linalg.norm(local_v[:,1:],axis=1)))
    angle=np.arctan2(local_v[:,2],local_v[:,1])
    uv=np.column_stack((local_v[:,0],radius*angle));period=2*math.pi*radius
    if radius<=1e-10:raise ValueError('Selection does not define a tunnel surface.')
    copies=np.vstack((uv-[0,period],uv,uv+[0,period]));mapping=np.tile(np.arange(len(verts)),3)
    progress(f'Triangulating cylindrical surface · {len(verts):,} occupied cells')
    try:raw=Delaunay(copies).simplices
    except QhullError as e:raise ValueError('Tunnel triangulation failed. Select one non-degenerate tunnel branch.') from e
    # Retain just the central revolution; reject triangles spanning the far side.
    centre_angle=copies[raw,1].mean(axis=1)
    span=np.ptp(copies[raw,1],axis=1)
    raw=raw[(centre_angle>=-period/2)&(centre_angle<period/2)&(span<period/2)]
    faces=mapping[raw]
    before_edges=len(faces)
    faces=geom.limit_edges(verts,faces,max_edge)
    removed_edges=before_edges-len(faces)
    if not len(faces):raise ValueError('No tunnel triangles fit the maximum edge. Increase it or use 0 for an automatic limit.')
    verts,faces=geom.clean_mesh(verts,faces)
    # Make winding radial-outward before any cap operation.
    q=verts[faces];n=np.cross(q[:,1]-q[:,0],q[:,2]-q[:,0]);c=q.mean(axis=1)-centre
    radial=c-np.outer(c@axes[:,0],axes[:,0]);flip=np.einsum('ij,ij->i',n,radial)<0
    faces[flip]=faces[flip][:,[0,2,1]]
    verts=_laplacian(verts,faces,smoothing)
    return verts,faces,{'frame':'Periodic cylindrical Delaunay / PCA','pca_variance':vals.tolist(),'spacing_m':ds,'angular_step_deg':360/nt,'triangles_before_edge_limit':before_edges,'triangles_removed_by_edge_limit':removed_edges,'warning':'For a single approximately straight tunnel branch. Edge limit controls bridging across missing measurements.'}



def planar_delaunay(points,max_edge=.5,smoothing=0,progress=lambda s:None,cancel=None):
    max_edge=float(max_edge);centre,axes,vals=_pca_frame(points);uv=(points-centre)@axes[:,:2]
    if vals[1]<=max(vals[0],1e-30)*1e-12:raise ValueError('Planar meshing needs at least three non-collinear points.')
    _, unique=np.unique(uv,axis=0,return_index=True);points=points[unique];uv=uv[unique]
    if vals[1]<=0 or vals[2]/max(vals[1],1e-30)>.35:
        # Still permit it, but surface may fold; report this clearly.
        warning='Selection is not strongly planar; Delaunay is intended for locally planar surfaces.'
    else:warning=''
    progress(f'Running planar Delaunay on {len(points):,} sampled points')
    try:tri=Delaunay(uv)
    except QhullError as e:raise ValueError('Delaunay triangulation failed. Select a less degenerate/local planar area.') from e
    faces=np.asarray(tri.simplices,dtype=np.uint32)
    compute_runtime.check_cancel(cancel)
    before_edges=len(faces);removed_edges=0
    if compute_runtime.workers()>1 and len(range(0,len(faces),100000))>=4:
        faces,removed_edges=parallel_meshing.triangle_filter_parallel(points,faces,max_edge,progress,cancel)
    elif len(faces):
        q=points[faces];edges=np.stack((np.linalg.norm(q[:,1]-q[:,0],axis=1),np.linalg.norm(q[:,2]-q[:,1],axis=1),np.linalg.norm(q[:,0]-q[:,2],axis=1)),axis=1)
        area=np.linalg.norm(np.cross(q[:,1]-q[:,0],q[:,2]-q[:,0]),axis=1)/2
        within=(edges.max(axis=1)<=max_edge) if max_edge>0 else np.ones(len(faces),bool)
        removed_edges=int((~within).sum());keep=within&(area>1e-14);faces=faces[keep]
    if not len(faces):raise ValueError('Delaunay created no triangles inside the maximum-edge limit. Increase maximum edge or reduce the selection.')
    vertices=_laplacian(points,faces,smoothing)
    return vertices,faces,{'frame':'Best-fit PCA plane','pca_variance':vals.tolist(),'triangles_before_edge_limit':before_edges,'triangles_removed_by_edge_limit':removed_edges,'warning':warning}


def convex_hull(points,smoothing=0,progress=lambda s:None):
    progress(f'Computing convex hull from {len(points):,} sampled points')
    if np.linalg.matrix_rank(points-points.mean(0))<3:raise ValueError('Convex hull requires a 3D volume. Use Planar Delaunay for flat points.')
    try:h=ConvexHull(points-points.mean(0))
    except QhullError as e:raise ValueError('Convex hull failed for this selection.') from e
    faces=np.asarray(h.simplices,dtype=np.uint32);q=points[faces];flip=np.einsum('ij,ij->i',np.cross(q[:,1]-q[:,0],q[:,2]-q[:,0]),h.equations[:,:3])<0;faces[flip]=faces[flip][:,[0,2,1]];vertices=_laplacian(points,faces,smoothing)
    return vertices,faces,{'warning':'Convex hull fills concavities and openings. It is a rough envelope, not a faithful tunnel/cross-passage surface.'}


def ball_pivoting(points,radius=None,smoothing=0,progress=lambda s:None):
    o3d,cloud,origin=geom.oriented_cloud(points,progress)
    r=float(radius or 0)
    if r<=0:r=max(geom.spacing_estimate(points)*2.5,1e-5)
    progress(f'Ball Pivoting · radii {r:.4f} / {2*r:.4f} m')
    mesh=o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(cloud,o3d.utility.DoubleVector([r,2*r]))
    v,f=geom.clean_mesh(np.asarray(mesh.vertices)+origin,np.asarray(mesh.triangles))
    return _laplacian(v,f,smoothing),f,{'ball_radius_m':r,'warning':'Ball Pivoting preserves sampled local surfaces; gaps can remain where points are missing.'}


def _write_mesh(work,name,vertices,faces,progress=lambda s:None,quality=''):
    vertices,faces=geom.clean_mesh(vertices,faces)
    vertices=np.asarray(vertices,dtype='<f8');faces=np.asarray(faces,dtype='<u4')
    if vertices.ndim!=2 or vertices.shape[1]!=3 or faces.ndim!=2 or faces.shape[1]!=3 or not len(vertices) or not len(faces):raise ValueError('Meshing did not produce valid triangular geometry.')
    ident=uuid.uuid4().hex;vp=Path(work)/(ident+'.vertices64');fp=Path(work)/(ident+'.faces32');meta=None
    try:
        vertices.tofile(vp);faces.tofile(fp);lo=vertices.min(axis=0);hi=vertices.max(axis=0)
        meta={'name':name,'vertices_file':str(vp),'faces_file':str(fp),'vertices':int(len(vertices)),'triangles':int(len(faces)),'minimum':lo.tolist(),'maximum':hi.tolist(),'degenerate_triangles':0,'units':'Source scan coordinates preserved','quality':quality or 'Generated from point-cloud sample; review topology, holes and deviation before design use.'}
        meta['mesh_summary']=geom.topology(vertices,faces)
        jobdata.preview_cache(meta,work,progress);return meta
    except Exception:
        if meta is not None:jobdata.remove_assets(meta)
        vp.unlink(missing_ok=True);fp.unlink(missing_ok=True);raise


def _read_mesh(meta):
    nv=int(meta.get('vertices',0));nf=int(meta.get('triangles',0))
    if nv<1 or nf<1:raise ValueError('Selected mesh has no triangular geometry.')
    v=np.fromfile(meta['vertices_file'],dtype='<f8').reshape((-1,3));f=np.fromfile(meta['faces_file'],dtype='<u4').reshape((-1,3))
    return v,f


def refine(meta,work,name,operation='smooth',smoothing=2,hole_size=.5,decimate_cell=.03,target_triangles=50000,progress=lambda s:None,cancel=None,
           gap_min_mm=50, gap_max_mm=150):
    callback=progress
    def progress(message):
        compute_runtime.check_cancel(cancel);callback(message);compute_runtime.check_cancel(cancel)
    compute_runtime.check_cancel(cancel)
    operation = str(operation)
    if operation == 'fill_bolt_gaps':
        import mesh_repair
        for key, file_key, width, cap in (('vertices', 'vertices_file', 24, mesh_repair.MAX_VERTICES),
                                          ('triangles', 'faces_file', 12, mesh_repair.MAX_TRIANGLES)):
            count = int(meta[key])
            if not 1 <= count <= cap:
                raise ValueError(f'Bolt-gap reconstruction supports up to {cap:,} {key} per mesh. Use a smaller section.')
            if Path(meta[file_key]).stat().st_size != count*width:
                raise ValueError(f'The {key} cache differs from its recorded size. Reopen the source mesh.')
    v,f=_read_mesh(meta);hole_summary=None
    flags=np.zeros(len(f),bool)
    if meta.get('face_flags_file'):
        flags=np.fromfile(meta['face_flags_file'],dtype='u1').astype(bool)
        if len(flags)!=len(f):raise ValueError('Reconstruction flags do not match this mesh. Reopen its original mesh before refinement.')
    if operation == 'fill_bolt_gaps':
        v, f, report = fill_bolt_gaps(v, f, gap_min_mm, gap_max_mm, progress, cancel,
                                      estimated_faces=flags)
        if not report['filled']:
            reasons = ', '.join(f'{count} × {reason}' for reason, count in report['preserved_reasons'].items())
            raise ValueError('No supported open bolt gaps were found in this mesh. ' + (reasons or 'The mesh has no open boundary.'))
        out = _write_mesh(work, name, v, f, progress, quality='Derived lining mesh with estimated bolt-gap patches.')
        path = Path(out['faces_file']).with_suffix('.face_flags')
        try:
            if out['triangles'] != len(f):
                raise ValueError('Source mesh contains duplicate or invalid faces. Repair its topology before estimating bolt gaps.')
            progress('Saving estimated triangle flags…')
            combined = np.r_[flags, np.ones(report['added_triangles'], dtype=bool)]
            combined.astype('u1').tofile(path)
            compute_runtime.check_cancel(cancel)
        except BaseException:
            jobdata.remove_assets(out)
            path.unlink(missing_ok=True)
            raise
        out['face_flags_file'] = str(path)
        out['repair_report'] = report
        out['mesh_summary'].update(method='refine:fill_bolt_gaps', filled_boundary_loops=report['filled'],
                                   preserved_openings=report['preserved'], reconstructed_triangles=int(combined.sum()),
                                   estimated_bolt_triangles=report['added_triangles'],
                                   reconstruction_note='Orange faces show estimated lining across open boundaries.')
        return out
    original_faces=len(f);repair_centres=v[f[flags]].mean(1) if np.any(flags) else None
    if operation=='smooth':
        progress(f'Smoothing mesh · {int(smoothing)} iteration(s)');v=_laplacian(v,f,max(1,int(smoothing)))
    elif operation=='taubin':v=geom.taubin(v,f,max(1,int(smoothing)))
    elif operation=='quadric':v,f=geom.quadric_reduce(v,f,target_triangles)
    elif operation=='fill_small':v,f,n,hole_summary=_fill_boundary_loops(v,f,'fill_small',hole_size,progress,return_summary=True)
    elif operation=='watertight':v,f,n,hole_summary=_fill_boundary_loops(v,f,'watertight',hole_size,progress,return_summary=True)
    elif operation=='largest_component':progress('Keeping largest connected mesh component…');v,f=_largest_component(v,f)
    elif operation=='decimate':progress(f'Vertex-cluster reduction · {float(decimate_cell):.4f} m');v,f=_vertex_cluster(v,f,decimate_cell)
    elif operation=='flip_normals':progress('Flipping triangle winding / normals…');f=f[:,[0,2,1]]
    else:raise ValueError('Unknown mesh refinement operation.')
    if not len(f):raise ValueError('Mesh refinement removed all triangles. Use a less aggressive setting.')
    conservative=operation in ('quadric','decimate') and repair_centres is not None
    if operation in ('smooth','taubin','flip_normals') and np.any(flags):repair_centres=v[f[flags]].mean(1)
    if operation in ('fill_small','watertight') and len(f)>original_faces:
        added=v[f[original_faces:]].mean(1);repair_centres=added if repair_centres is None else np.vstack([repair_centres,added])
    out=_write_mesh(work,name,v,f,progress,quality='Derived non-destructively from an existing mesh; review topology and source deviation before survey/design use.')
    out['mesh_summary'].update(method='refine:'+operation,vertices=out['vertices'],triangles=out['triangles'])
    if hole_summary:
        out['repair_report']=hole_summary
        out['mesh_summary'].update(filled_boundary_loops=hole_summary['filled'],preserved_openings=hole_summary.get('preserved',0),unfilled_openings=hole_summary.get('unfilled',0),hole_extent_metric=hole_summary.get('extent_metric'))
    if repair_centres is not None:
        out_v,out_f=_read_mesh(out)
        out_flags=np.ones(len(out_f),bool) if conservative else cKDTree(repair_centres).query(out_v[out_f].mean(1))[0]<1e-7
        path=Path(out['faces_file']).with_suffix('.face_flags');out_flags.astype('u1').tofile(path);out['face_flags_file']=str(path)
        out['mesh_summary'].update(reconstructed_triangles=int(out_flags.sum()),reconstruction_note='Simplification mixes measured and reconstructed support. All triangles are conservatively marked for review; use the original mesh to inspect individual repairs.' if conservative else 'Orange triangles retain the reconstructed-gap classification through refinement.')
    return out


def create(meta,work,name,method='pca_tunnel',selection=None,max_points=500000,spacing=.12,degrees=2,max_edge=.65,smoothing=0,ball_radius=None,sampling_distance=0,noise_gap=0,hole_handling='preserve',hole_size=.5,alpha_radius=0,poisson_depth=8,tunnel_gap=.25,progress=lambda s:None,quality='balanced',cancel=None):
    callback=progress
    def progress(message):
        compute_runtime.check_cancel(cancel);callback(message);compute_runtime.check_cancel(cancel)
    compute_runtime.check_cancel(cancel)
    from mesh_sampling import Source, spatial_sample
    source=Source(meta,selection)
    max_edge=float(max_edge or 0)
    if method=='tunnel_surface':
        if float(sampling_distance or 0)!=0 or float(noise_gap or 0)!=0:
            raise ValueError('Create Tunnel Surface uses all retained measurements. Apply isolated-point cleaning to the source first; point sampling distance and working-sample noise filters apply to the other mesh methods.')
        import tunnel_surface
        v,f,extra,points=tunnel_surface.create_streamed(source,spacing,degrees,max_edge,tunnel_gap,quality,progress)
        source_count=int(extra['finite_source_points'])
        sampled=int(extra['grid_fit_samples'])
        processed=source_count
        if int(smoothing)>0:
            v=geom.taubin(v,f,int(smoothing));extra['smoothing']='Taubin · '+str(int(smoothing))+' iterations'
        source_spacing=geom.spacing_estimate(points)
    else:
        points,sample_extra=spatial_sample(source,max_points,sampling_distance,progress)
        source_count=int(sample_extra['finite_source_points']);sampled=len(points)
        progress(f'Spatial measured sample ready: {sampled:,} / {source_count:,} retained points')
        points=_noise_filter(points,noise_gap,progress,cancel)
        points=np.unique(points,axis=0)
        if len(points)<3:raise ValueError('At least three distinct retained points are required.')
        processed=len(points);source_spacing=geom.spacing_estimate(points)
        if max_edge<=0 and method in ('auto_scan','delaunay','pca_tunnel','watertight_tunnel','ball_pivoting'):
            max_edge=max(6*source_spacing,3*float(spacing),.01)
        progress(f'Working point spacing approximately {source_spacing:.4f} m; edge limit {max_edge:.4f} m (0 = off)')
        if method=='auto_scan':v,f,extra=auto_scan(points,spacing,degrees,max_edge,smoothing,progress,cancel)
        elif method=='pca_tunnel':v,f,extra=tunnel_unwrap(points,spacing,degrees,max_edge,smoothing,progress)
        elif method=='watertight_tunnel':
            v,f,extra=tunnel_unwrap(points,spacing,degrees,max_edge,smoothing,progress);extra['warning']='The selected hole-handling control governs local caps. Portals and unsupported large openings remain open. Review boundary/non-manifold counts; the result is not certified watertight.'
        elif method=='delaunay':v,f,extra=planar_delaunay(points,max_edge,smoothing,progress,cancel)
        elif method=='alpha_shape':v,f,extra=geom.alpha_shape(points,alpha_radius,progress)
        elif method=='poisson':v,f,extra=geom.poisson(points,poisson_depth,progress)
        elif method=='convex_hull':v,f,extra=convex_hull(points,smoothing,progress)
        elif method=='ball_pivoting':v,f,extra=ball_pivoting(points,ball_radius,smoothing,progress)
        elif method=='marching_cubes':
            engine=parallel_meshing.marching_cubes_parallel if compute_runtime.workers()>1 else marching_cubes
            v,f,extra=engine(points,spacing,max(1,smoothing),progress,cancel)
        else:raise ValueError('Unknown meshing method.')
        extra={**sample_extra,**extra}
        if method in ('alpha_shape','poisson') and int(smoothing)>0:
            v=geom.taubin(v,f,int(smoothing));extra['smoothing']='Taubin (lambda=0.5, mu=-0.53)'
        if method in ('alpha_shape','poisson','ball_pivoting') or extra.get('auto_choice')=='ball_pivoting':
            before_edges=len(f);f=geom.limit_edges(v,f,max_edge)
            extra.update(triangles_before_edge_limit=before_edges,triangles_removed_by_edge_limit=before_edges-len(f))
    repaired=extra.pop('_reconstructed_faces',None)
    repair_centres=[v[f[repaired]].mean(1)] if repaired is not None and np.any(repaired) else []
    v,f=geom.clean_mesh(v,f)
    if hole_handling!='preserve':
        previous_faces=len(f)
        v,f,filled,hole_summary=_fill_boundary_loops(v,f,hole_handling,hole_size,progress,return_summary=True);extra['filled_boundary_loops']=int(filled);extra['hole_handling']=hole_handling
        extra.update(preserved_openings=hole_summary['preserved'],unfilled_openings=hole_summary['unfilled'],hole_extent_metric=hole_summary['extent_metric'],hole_repair_reasons=hole_summary['reason_counts'])
        if len(f)>previous_faces:repair_centres.append(v[f[previous_faces:]].mean(1))
    source.assert_unchanged()
    progress(f'Writing mesh: {len(v):,} vertices · {len(f):,} triangles')
    meta_out=None
    try:
        meta_out=_write_mesh(work,name,v,f,progress,quality='Generated from retained measured support; review connectivity, coverage, openings and source deviation before design use.')
        v,f=_read_mesh(meta_out)
        if repair_centres:
            flags=cKDTree(np.vstack(repair_centres)).query(v[f].mean(1))[0]<1e-7
            path=Path(meta_out['faces_file']).with_suffix('.face_flags');flags.astype('u1').tofile(path)
            meta_out['face_flags_file']=str(path);extra['reconstructed_triangles']=int(flags.sum())
            extra['reconstruction_note']='Orange triangles bridge unmeasured gaps; compare volume with and without repair.'
        progress('Checking triangle surface against spatially distributed measured reference points…')
        fit=geom.sampled_fit(points,v,f)
        fit['fit_note']+=' Reference points are bounded spatial representatives of retained measurements; this is not full-density survey deviation.'
        meta_out['mesh_summary'].update({'point_spacing_m':source_spacing,'effective_max_edge_m':max_edge,**fit,'method':method,'source_points':int(source_count),'sampled_points':int(sampled),'processed_points':int(processed),'fit_reference_points':int(len(points)),'vertices':int(len(v)),'triangles':int(len(f)),**extra})
        coverage=extra.get('observed_cell_coverage_fraction')
        if coverage is not None and coverage<.97:
            summary=meta_out['mesh_summary'];summary['review_required']=True
            summary['review_reasons'].append(f'Only {coverage:.1%} of measured lining cells support a triangle. Inspect sparse areas, cell size and the maximum edge before treating this as a continuous lining.')
        before_edges=extra.get('triangles_before_edge_limit',0);removed_edges=extra.get('triangles_removed_by_edge_limit',0)
        if before_edges and removed_edges/before_edges>.2:
            summary=meta_out['mesh_summary'];summary['review_required']=True
            summary['review_reasons'].append(f'The maximum edge removed {removed_edges:,} of {before_edges:,} candidate triangles ({removed_edges/before_edges:.1%}). Inspect the edge limit and unsupported gaps before accepting the surface.')
        source.assert_unchanged()
        progress('Triangle geometry, connected surfaces and measured support checked')
        return meta_out
    except Exception:
        if meta_out is not None:
            jobdata.remove_assets(meta_out)
            if meta_out.get('face_flags_file'):Path(meta_out['face_flags_file']).unlink(missing_ok=True)
        raise
