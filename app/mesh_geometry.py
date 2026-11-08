# Copyright (c) 2026 Damian Gulczynski.
"""Triangle reconstruction and explicit geometric validation; coordinates in metres."""
import math
import sys
from pathlib import Path

# Optional engines installed separately; existing runtime packages take precedence.
_engine_folder=Path(__file__).resolve().parents[1]/'runtime'/'mesh_engines'
if _engine_folder.is_dir() and str(_engine_folder) not in sys.path:
    sys.path.append(str(_engine_folder))
import numpy as np
from scipy.spatial import Delaunay, cKDTree, QhullError


def clean_mesh(vertices, faces):
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces)
    if v.ndim != 2 or v.shape[1] != 3 or not np.isfinite(v).all():
        raise ValueError('Mesh vertices must be finite XYZ coordinates.')
    if f.ndim != 2 or f.shape[1] != 3 or not len(f):
        raise ValueError('The method produced no triangular faces. Adjust its spacing/radius or select a smaller area.')
    if not np.issubdtype(f.dtype, np.integer) or f.min() < 0 or f.max() >= len(v):
        raise ValueError('Invalid triangle vertex indices.')
    f = f.astype(np.int64)
    q = v[f]
    area2 = np.linalg.norm(np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0]), axis=1)
    f = f[area2 > 1e-14]
    if not len(f):
        raise ValueError('Only zero-area triangles were produced; select non-collinear surface points.')
    _, first = np.unique(np.sort(f, axis=1), axis=0, return_index=True)
    f = f[np.sort(first)]
    used, inv = np.unique(f, return_inverse=True)
    return np.ascontiguousarray(v[used]), np.ascontiguousarray(inv.reshape(-1, 3), dtype=np.uint32)


def topology(vertices, faces):
    v, f = np.asarray(vertices), np.asarray(faces, dtype=np.int64)
    edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    ordered_edges = np.sort(edges, axis=1)
    order = np.lexsort((ordered_edges[:, 1], ordered_edges[:, 0]))
    ordered_edges = ordered_edges[order]
    same = np.all(ordered_edges[1:] == ordered_edges[:-1], axis=1)
    starts = np.r_[0, np.flatnonzero(~same)+1]
    count = np.diff(np.r_[starts, len(edges)])
    owners = np.tile(np.arange(len(f), dtype=np.int64), 3)[order]
    # Faces are connected only through a shared edge. Meeting at a lone vertex
    # is not a continuous sheet and must not hide an island in the summary.
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    graph = coo_matrix((np.ones(int(same.sum()), np.uint8),
                        (owners[:-1][same], owners[1:][same])),
                       shape=(len(f), len(f))).tocsr()
    components, labels = connected_components(graph, directed=False)
    component_faces = np.bincount(labels, minlength=components)
    direction = (edges[:, 0] < edges[:, 1])[order]
    paired = starts[count == 2]
    orientation_conflicts = int((direction[paired] == direction[paired+1]).sum())
    q = v[f]
    areas = np.linalg.norm(np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0]), axis=1)/2
    lengths2=sum(np.sum((q[:,i]-q[:,j])**2,axis=1) for i,j in ((0,1),(1,2),(2,0)))
    quality=np.divide(4*math.sqrt(3)*areas,lengths2,out=np.zeros(len(f)),where=lengths2>0)
    area = float(areas.sum())
    boundary, nonmanifold = int((count == 1).sum()), int((count > 2).sum())
    component_areas = np.bincount(labels, weights=areas, minlength=components)
    isolated = int((component_faces == 1).sum())
    largest_triangles = int(component_faces.max()) if components else 0
    largest_area_fraction = float(component_areas.max()/area) if area > 0 else 0.0
    fragmented = components >= 20 or (components >= 4 and isolated/max(len(f), 1) > .1)
    status = ('nonmanifold' if nonmanifold else 'fragmented' if fragmented else
              'multiple' if components > 1 else 'connected')
    reasons = []
    if components > 1:
        reasons.append(f'{components:,} separate edge-connected surfaces. '
                       'Inspect whether these are intentional separate areas or missing connections; all components were retained.')
    if fragmented:
        reasons.append('Many disconnected triangle pieces remain. Do not treat this result as a continuous tunnel lining.')
    if nonmanifold:
        reasons.append(f'{nonmanifold:,} edges are shared by more than two triangles.')
    if orientation_conflicts:
        reasons.append(f'{orientation_conflicts:,} shared edges have inconsistent triangle winding.')
    return {'area_m2': area, 'boundary_edges': boundary, 'nonmanifold_edges': nonmanifold,
            'connected_components':int(components),
            'largest_component_triangles':largest_triangles,
            'largest_component_triangle_fraction':largest_triangles/max(len(f), 1),
            'largest_component_area_fraction':largest_area_fraction,
            'isolated_triangles':isolated, 'orientation_conflict_edges':orientation_conflicts,
            'surface_status':status, 'review_required':bool(reasons), 'review_reasons':reasons,
            'triangle_quality_mean':float(quality.mean()) if len(f) else None,
            'triangle_quality_min':float(quality.min()) if len(f) else None,
            'triangle_quality_p05':float(np.percentile(quality,5)) if len(f) else None,
            'triangle_quality_note':'Q = 4 sqrt(3) area / sum(edge length squared). 1 is equilateral; 0 is degenerate. Shape quality is not surface accuracy.',
            'edge_closed': boundary == 0 and nonmanifold == 0,
            'topology_note': 'Components are triangles joined through shared edges. Open boundary edges can be intentional portals or measurement limits. These checks do not test self-intersections or certify watertightness or measurement coverage.'}


def spacing_estimate(points):
    sample = points[::max(1, math.ceil(len(points)/20000))]
    distances, _ = cKDTree(points).query(sample, k=2)
    d = distances[:, 1]
    d = d[np.isfinite(d) & (d > 0)]
    return float(np.median(d)) if len(d) else 0.0


def limit_edges(vertices, faces, max_edge):
    f = np.asarray(faces, dtype=np.uint32)
    if float(max_edge or 0) <= 0:
        return f
    q = np.asarray(vertices)[f]
    lengths = np.linalg.norm(q[:, [1, 2, 0]]-q, axis=2)
    return f[lengths.max(axis=1) <= float(max_edge)]


def alpha_shape(points, alpha=0, progress=lambda s: None):
    """Boundary of the 3D Delaunay tetrahedra with circumradius <= alpha.

    2*(p_i-p_0).c = |p_i-p_0|^2; R=|c|. Degenerate tetrahedra
    are excluded, and each surviving boundary face points out of its tetrahedron.
    """
    if len(points) > 100000:
        raise ValueError('3D Alpha Shape is limited to 100,000 working points. Choose Fast quality or increase point sampling distance.')
    origin = points.mean(axis=0)
    p = points-origin
    if len(p) < 4 or np.linalg.matrix_rank(p) < 3:
        raise ValueError('3D Alpha Shape needs non-coplanar points. Use Planar Delaunay for a wall/slab.')
    progress(f'3D Delaunay tetrahedralisation: {len(p):,} points')
    try:
        tets = Delaunay(p).simplices
    except QhullError as e:
        raise ValueError('3D Delaunay failed for this geometry. Clean duplicates or use a local planar selection.') from e
    delta = p[tets[:, 1:]]-p[tets[:, :1]]
    valid = np.abs(np.linalg.det(delta)) > 1e-15
    tets, delta = tets[valid], delta[valid]
    if not len(tets):
        raise ValueError('No non-degenerate tetrahedra for Alpha Shape.')
    centres = np.linalg.solve(2*delta, np.sum(delta*delta, axis=2)[..., None])[..., 0]
    radii = np.linalg.norm(centres, axis=1)
    radius = float(alpha or 0)
    if radius <= 0:
        radius = float(np.quantile(radii[np.isfinite(radii)], .75))*1.00001
    tets = tets[radii <= radius]
    if not len(tets):
        raise ValueError('Alpha radius retained no tetrahedra. Increase Alpha radius.')
    faces = np.concatenate([tets[:, [1, 2, 3]], tets[:, [0, 3, 2]], tets[:, [0, 1, 3]], tets[:, [0, 2, 1]]])
    opposite = np.concatenate([tets[:, 0], tets[:, 1], tets[:, 2], tets[:, 3]])
    q = p[faces]
    inward = np.einsum('ij,ij->i', np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0]), p[opposite]-q[:, 0]) > 0
    faces[inward] = faces[inward][:, [0, 2, 1]]
    _, first, counts = np.unique(np.sort(faces, axis=1), axis=0, return_index=True, return_counts=True)
    faces = faces[first[counts == 1]]
    v, f = clean_mesh(points, faces)
    return v, f, {'alpha_radius_m': radius, 'warning': 'Alpha Shape is the boundary of retained tetrahedra. Radius controls bridging; inspect gaps, concavities and edge counts.'}


def oriented_cloud(points, progress=lambda s: None):
    try:
        import open3d as o3d
    except ImportError as e:
        raise ValueError('This method needs Open3D. Reinstall the complete current Tunnel Studio installer to restore its bundled engines.') from e
    if len(points) < 10:
        raise ValueError('This method needs at least 10 distinct surface points.')
    origin = points.mean(axis=0)
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points-origin))
    progress('Estimating and consistently orienting surface normals…')
    cloud.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(knn=min(30, len(points))))
    try:
        cloud.orient_normals_consistent_tangent_plane(min(30, len(points)-1))
    except RuntimeError as e:
        raise ValueError('Could not orient normals reliably. Use Planar Delaunay for flat surfaces or a smaller clean 3D selection.') from e
    return o3d, cloud, origin


def poisson(points, depth=8, progress=lambda s: None):
    o3d, cloud, origin = oriented_cloud(points, progress)
    depth = max(5, min(10, int(depth)))
    from compute_runtime import workers
    threads = workers(memory_per_worker=256*1024**2)
    progress(f'Screened Poisson reconstruction · octree depth {depth} · {threads} CPU workers')
    mesh, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(cloud, depth=depth, scale=1.1, linear_fit=False, n_threads=threads)
    v, f = clean_mesh(np.asarray(mesh.vertices)+origin, np.asarray(mesh.triangles))
    return v, f, {'poisson_depth': depth, 'cpu_workers': threads, 'compute_device': 'CPU', 'warning': 'Poisson can extrapolate across unmeasured regions. Edge trimming and cropping can open holes; inspect topology and source deviation.'}


def sampled_fit(points, vertices, faces, samples=12000):
    """Area-weighted sampling (Point2Mesh sec. 3.2), normalized bidirectional CD.

    This is a deterministic sampled point-set comparison, NOT exact distance
    to triangle interiors or a survey acceptance test. CD units are square metres.
    """
    rng = np.random.default_rng(42)
    q = vertices[faces]
    areas = np.linalg.norm(np.cross(q[:, 1]-q[:, 0], q[:, 2]-q[:, 0]), axis=1)
    ids = rng.choice(len(q), size=samples, p=areas/areas.sum())
    uv = rng.random((samples, 2))
    reflect = uv.sum(axis=1) > 1
    uv[reflect] = 1-uv[reflect]
    tri = q[ids]
    surface = tri[:, 0]+uv[:, :1]*(tri[:, 1]-tri[:, 0])+uv[:, 1:]*(tri[:, 2]-tri[:, 0])
    source = points[::max(1, math.ceil(len(points)/samples))]
    forward = cKDTree(surface).query(source)[0]
    backward = cKDTree(points).query(surface)[0]
    # The same source set is used in both directions for normalized Chamfer.
    backward_cd = cKDTree(source).query(surface)[0]
    cd = .5*(float(np.mean(forward**2))+float(np.mean(backward_cd**2)))
    return {'sampled_chamfer_m2': cd, 'mesh_sample_to_cloud_rms_m': float(np.sqrt(np.mean(backward**2))),
            'mesh_sample_to_cloud_p95_m': float(np.quantile(backward, .95)),
            'mesh_fit_samples': samples, 'cloud_fit_samples': len(source),
            'fit_note': 'Area-weighted surface samples; normalized squared Chamfer = half the sum of directional mean squared nearest-neighbour distances. Approximate, not exact point-to-triangle deviation.'}


def taubin(vertices, faces, iterations=2):
    """Two-pass uniform graph Laplacian, lambda=.5 and mu=-.53."""
    v=np.asarray(vertices,float).copy();f=np.asarray(faces,int)
    edges=np.concatenate([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]);edges=np.unique(np.sort(edges,axis=1),axis=0)
    a=np.concatenate([edges[:,0],edges[:,1]]);b=np.concatenate([edges[:,1],edges[:,0]])
    count=np.bincount(a,minlength=len(v));active=count>0
    for _ in range(max(0,min(20,int(iterations)))):
        for strength in (.5,-.53):
            mean=np.column_stack([np.bincount(a,weights=v[b,j],minlength=len(v)) for j in range(3)])
            mean[active]/=count[active,None]
            v[active]+=strength*(mean[active]-v[active])
    return v


def quadric_reduce(vertices, faces, target=50000):
    try:import open3d as o3d
    except ImportError as e:raise ValueError('Quadric reduction requires Open3D. Reinstall the complete current Tunnel Studio installer.') from e
    target=max(4,min(int(target),len(faces)))
    origin=vertices.mean(axis=0)
    mesh=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(vertices-origin),o3d.utility.Vector3iVector(faces))
    reduced=mesh.simplify_quadric_decimation(target_number_of_triangles=target)
    return clean_mesh(np.asarray(reduced.vertices)+origin,np.asarray(reduced.triangles))


def cap_loop(vertices, loop):
    """Ear-clip a simple nearly planar boundary; never fan across a concavity."""
    pts=vertices[np.asarray(loop)];q=pts-pts.mean(0)
    _,_,axes=np.linalg.svd(q,full_matrices=False);uv=q@axes[:2].T
    scale=max(float(np.linalg.norm(np.ptp(pts,axis=0))),1e-12)
    if len(loop)>2000 or np.max(np.abs(q@axes[2]))>.02*scale:
        return []
    cross=lambda a,b:float(a[0]*b[1]-a[1]*b[0])
    area=sum(cross(uv[i],uv[(i+1)%len(uv)]) for i in range(len(uv)))
    sign=1 if area>0 else -1;indices=list(range(len(loop)));triangles=[];tol=1e-12*scale*scale
    while len(indices)>3:
        found=False
        for j,b in enumerate(indices):
            a,c=indices[j-1],indices[(j+1)%len(indices)]
            if sign*cross(uv[b]-uv[a],uv[c]-uv[b])<=tol:continue
            other=[k for k in indices if k not in (a,b,c)]
            if any(all(sign*cross(uv[v]-uv[u],uv[k]-uv[u])>=-tol for u,v in ((a,b),(b,c),(c,a))) for k in other):continue
            triangles.append((loop[a],loop[b],loop[c]));indices.pop(j);found=True;break
        if not found:return []
    if len(indices)==3:triangles.append(tuple(loop[i] for i in indices))
    return triangles
