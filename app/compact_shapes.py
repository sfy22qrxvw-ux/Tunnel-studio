# Copyright (c) 2026 Damian Gulczynski.
"""Compare complete compact lining departures in bounded, overlapping maps.

Connected measured cells supply each candidate footprint. Principal directions,
small size variation and bidirectional footprint/depth agreement allow rotated
and incompletely observed copies of an example. An accepted candidate assigns
its complete measured off-lining component, including cells outside the taught
mask. Artificial closing cells never create observations. Only bounded map tiles
and bounded descriptor samples occupy RAM; exact original IDs remain streamed.

These are geometric candidates, not semantic identities or survey accuracy.
Connected seams and ambiguous/oversized components need manual review; no ring
layout, missing measurements or hidden wall is inferred.
"""
import math
from pathlib import Path
import numpy as np
from scipy.ndimage import binary_closing, distance_transform_edt, find_objects, label
from scipy.spatial import cKDTree

MAX_DESCRIPTOR = 4096
MAX_MAP_CELLS = 4_000_000
MIN_SCALE, MAX_SCALE = .75, 1.30


def descriptor(mask, depth):
    points = np.argwhere(mask).astype(float)
    centre = points.mean(0)
    local = points-centre
    values, vectors = np.linalg.eigh(local.T@local/max(1, len(points)))
    major = vectors[:, -1]
    basis = np.column_stack([major, [-major[1], major[0]]])
    aligned = local@basis
    extent = np.maximum(np.ptp(aligned, axis=0)+1, 1)
    # Spatially ordered, deterministic descriptor budget, not an input cap.
    take = np.linspace(0, len(points)-1, min(len(points), MAX_DESCRIPTOR)).astype(int)
    return dict(points=local[take], aligned=aligned[take], depth=depth[mask][take],
                extent=extent, count=len(points), basis=basis)


def agreement(example, candidate, cfg):
    """Symmetric footprint support and RMS depth agreement; no probability."""
    ratio = candidate['count']/example['count']
    if not .45 <= ratio <= 1.85:
        return 0.
    lengths = candidate['extent']/example['extent']
    if np.any(lengths < .55) or np.any(lengths > 1.55):
        return 0.
    scales = np.unique(np.clip([1., math.sqrt(ratio), *lengths], MIN_SCALE, MAX_SCALE))
    target = example['aligned'] if cfg['rotations'] else example['points']
    moving = candidate['aligned'] if cfg['rotations'] else candidate['points']
    tree = cKDTree(target)
    best = 0.; tolerance = cfg['depth_tolerance_mm']/1000
    # A nearly square/circular footprint has no stable principal direction.
    # The depth pattern still distinguishes quarter turns of the same pocket.
    ambiguous=all(float(d['extent'].max()/d['extent'].min())<1.4 for d in (example,candidate))
    turns=range(4) if cfg['rotations'] and ambiguous else (0,2) if cfg['rotations'] else (0,)
    for scale in scales:
        for turn in turns:
            rotation=np.array([[[1,0],[0,1]],[[0,-1],[1,0]],[[-1,0],[0,-1]],[[0,1],[-1,0]]][turn])
            transformed = (moving@rotation.T)/scale
            distance, neighbour = tree.query(transformed, workers=1)
            reverse, _ = cKDTree(transformed).query(target, workers=1)
            matched = distance <= 1.5
            coverage_a = float(matched.mean()); coverage_b = float((reverse <= 1.5).mean())
            if min(coverage_a, coverage_b) < .55:
                continue
            shape = 2*coverage_a*coverage_b/max(coverage_a+coverage_b, 1e-12)
            error = candidate['depth'][matched]-example['depth'][neighbour[matched]]
            rms2 = float(np.mean(error*error))
            best = max(best, shape*math.exp(-rms2/(2*tolerance*tolerance)))
    return float(np.clip(best, 0, 1))


def match(evidence, template, cfg, columns, progress, cancel, emit, bank=None, folder=None):
    """Assign complete accepted components using deterministic tile ownership."""
    db = evidence.db
    if bank:
        from shape_library import load_template
        entries=bank['entries']
        positives=[e for e in entries if e['label']=='positive']
        dimensions=[np.asarray(e['size_m'])*1000/cfg['grid_mm'] for e in positives]
        sizes=[int(e['footprint_cells'])*(e['grid_mm']/cfg['grid_mm'])**2 for e in positives]
    else:
        entries=None;positives=None
        taught = descriptor(template['mask'], template['depth'])
        dimensions=[np.asarray(template['mask'].shape)];sizes=[taught['count']]
    example_sizes=sizes
    db.execute('CREATE TABLE IF NOT EXISTS group_examples(id INTEGER PRIMARY KEY,class_id TEXT,example_id TEXT,path TEXT NOT NULL)')
    descriptor_folder=Path(folder) / 'descriptors' if folder is not None else evidence.path.parent/'descriptors'
    descriptor_folder.mkdir(exist_ok=True)
    # Every potentially accepted rotated footprint fits in this halo. Large
    # continuing components touching it are rejected rather than truncated.
    reach = max(4, int(math.ceil(max(np.linalg.norm(a) for a in dimensions)*1.65))+3)
    core = max(16, min(256, 1_000_000//max(1, columns)))
    width = columns+2*reach
    if (core+2*reach)*width > MAX_MAP_CELLS:
        raise ValueError('Increase Shape grid size or draw a smaller compact example to keep shape tiles bounded. The whole cloud still has no point-count admission cap.')
    low, high = db.execute('SELECT min(r),max(r) FROM cells').fetchone()
    if low is None:
        return 0
    next_id = int(db.execute('SELECT coalesce(max(id),0)+1 FROM matches').fetchone()[0])
    windows = 0; threshold = cfg['similarity_percent']/100
    for first in range(low, high+1, core):
        start = first-reach; stop = first+core+reach
        for side in (-1, 1):
            emit(progress, cancel, f'Comparing complete pocket footprints · {first*cfg["grid_mm"]/1000:.2f} m · {windows:,} tiles checked')
            occupied = np.zeros((stop-start, columns), bool)
            depth = np.zeros(occupied.shape, float)
            assigned = np.zeros(occupied.shape, np.int32)
            for r,c,n,v,gid in db.execute('SELECT r,c,n,v,gid FROM cells WHERE r>=? AND r<? AND side=?', (start,stop,side)):
                occupied[r-start,c] = True; depth[r-start,c] = v/n; assigned[r-start,c] = gid
            if not occupied.any():
                continue
            raw = np.pad(occupied, ((0,0),(reach,reach)), mode='wrap')
            depths = np.pad(depth, ((0,0),(reach,reach)), mode='wrap')
            joined = binary_closing(raw, structure=np.ones((3,3))) | raw
            components, number = label(joined, structure=np.ones((3,3)))
            sizes = np.bincount(components.ravel())
            objects = find_objects(components)
            for serial, box in enumerate(objects, 1):
                if serial % 64 == 0:
                    emit(progress, cancel, f'Checking compact candidates · {next_id-1:,} groups · tile {windows+1}')
                if box is None or not max(4, .45*min(example_sizes)) <= sizes[serial] <= 1.85*max(example_sizes):
                    continue
                rbox,cbox = box
                if rbox.start == 0 or rbox.stop == len(raw) or cbox.start == 0 or cbox.stop == raw.shape[1]:
                    continue
                footprint = components[box] == serial
                rr,cc = np.where(footprint)
                centre_r = float(rr.mean()+rbox.start)
                centre_c = float(cc.mean()+cbox.start)
                # The centre owns a component once across tiles and angular
                # copies. Halo samples only supply support, never new owners.
                if not first <= start+centre_r < first+core or not reach <= centre_c < reach+columns:
                    continue
                measured = raw[box] & footprint
                if measured.sum() < 4:
                    continue
                values = depths[box].copy()
                fill = footprint & ~measured
                if fill.any():
                    nearest = distance_transform_edt(~measured, return_distances=False, return_indices=True)
                    values[fill] = values[tuple(nearest[:,fill])]
                candidate=descriptor(footprint, values);winner=None;score=0.
                if bank:
                    for entry in positives:
                        if entry['side'] not in ('either','recess' if side>0 else 'raised'):continue
                        emit(progress,cancel,f'Comparing saved examples · candidate {next_id} · {bank["names"][entry["class_id"]]}')
                        t=load_template(entry,cfg['grid_mm'])
                        value=agreement(descriptor(t['mask'],t['depth']),candidate,cfg)
                        if value>score:score=value;winner=entry
                    if winner and score>=threshold:
                        for entry in entries:
                            if entry['label']!='negative' or entry['class_id']!=winner['class_id'] or entry['side'] not in ('either','recess' if side>0 else 'raised'):continue
                            emit(progress,cancel,'Checking confirmed incorrect matches')
                            t=load_template(entry,cfg['grid_mm'])
                            if agreement(descriptor(t['mask'],t['depth']),candidate,cfg)>=score-1e-6:
                                score=0.;break
                else:score = agreement(taught,candidate,cfg)
                if score < threshold:
                    continue
                ar,ac = np.where(measured)
                ar = ar+rbox.start; ac = (ac+cbox.start-reach) % columns
                # A full off-lining component is one candidate. No made-up
                # cells or below-threshold wall points are selected.
                old = assigned[ar,ac]
                if (old > 0).any():
                    continue
                gid = next_id; next_id += 1
                db.execute('INSERT INTO matches(id,station,angle,score,side) VALUES(?,?,?,?,?)',
                           (gid,(start+centre_r)*cfg['grid_mm']/1000,
                            ((centre_c-reach)%columns)*2*np.pi/columns,score,side))
                dest=descriptor_folder/(str(gid)+'.npz')
                np.savez_compressed(dest,mask=footprint,depth=np.where(footprint,values,0),grid_mm=cfg['grid_mm'],side=side)
                db.execute('INSERT INTO group_examples VALUES(?,?,?,?)',(gid,winner['class_id'] if winner else None,winner['id'] if winner else None,str(dest)))
                db.executemany('UPDATE cells SET gid=? WHERE r=? AND c=? AND side=? AND gid=0',
                               ((gid,int(a+start),int(b),side) for a,b in zip(ar,ac)))
                assigned[ar,ac] = gid
            db.commit(); windows += 1
    emit(progress, cancel, f'Compact matching complete · {next_id-1:,} candidate groups')
    return windows
