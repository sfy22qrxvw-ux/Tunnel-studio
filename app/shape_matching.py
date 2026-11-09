# Copyright (c) 2026 Damian Gulczynski.
"""Find geometric examples using lining offsets and bounded, tiled height maps.

Every retained observation is streamed. A bounded calibration sample supplies
the existing measured-lining reference, then sparse disk cells describe a
drawn example and matching footprints/depths. Overlapping FFT windows, angular
wrapping and a final full-density pass retain exact original point identities.
The cell size and calibration budget are working resolutions, not cloud-size
admission limits. Source coordinates and attributes are never rewritten.

This is an independent geometric adaptation of the template/height-feature
ideas in Shi et al. (2021), doi:10.1088/1742-6596/1828/1/012155, and the offset
feature in Zhang et al. (2026), doi:10.3390/s26154901. It does not implement the
latter's trained PointNet++ classifier or reproduce either paper's accuracy.
Scores measure geometric similarity, not probability or semantic identity.
"""
from pathlib import Path
import math
import sqlite3
import time

import numpy as np
from scipy.ndimage import binary_closing, label, maximum_filter, distance_transform_edt
from scipy.signal import fftconvolve
from scipy.fft import set_workers

import compute_runtime
import jobdata
import lining_features
import tunnel_lining
from combined_denoising import sample_originals

BLOCK = 100000
DISPLAY = 30000
DEFAULTS = dict(min_offset_mm=5., max_offset_mm=2000., depth_tolerance_mm=15.,
                grid_mm=10., similarity_percent=75., side='either',
                continuation='auto', rotations=True, respect_view=False,lining_reference='local',
                include_reviewed=False, search_scope='auto')
MATCH_SCHEMA = '''CREATE TABLE matches (
    id INTEGER PRIMARY KEY, station REAL NOT NULL, angle REAL NOT NULL,
    score REAL NOT NULL, side INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 0,
    selected_points INTEGER NOT NULL DEFAULT 0)'''
REGION_SCHEMA = '''CREATE TABLE match_regions (
    region INTEGER NOT NULL, match_id INTEGER NOT NULL, points INTEGER NOT NULL,
    PRIMARY KEY(region,match_id)) WITHOUT ROWID'''


def settings(raw=None):
    if raw is None: raw = {}
    if not isinstance(raw, dict) or set(raw)-set(DEFAULTS):
        raise ValueError('Unknown example-shape setting.')
    out = {**DEFAULTS, **raw}
    for key, lo, hi in [('min_offset_mm', .5, 5000), ('max_offset_mm', 1, 10000),
                        ('depth_tolerance_mm', .5, 1000), ('grid_mm', 2, 100),
                        ('similarity_percent', 20, 99.9)]:
        try: value = float(out[key])
        except (ValueError, TypeError): raise ValueError('Enter a finite number for '+key+'.')
        if not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'{key} must be between {lo:g} and {hi:g}.')
        out[key] = value
    if out['max_offset_mm'] <= out['min_offset_mm']:
        raise ValueError('Maximum lining offset must exceed the minimum.')
    if out['side'] not in ('either', 'raised', 'recess'):
        raise ValueError('Choose raised, recessed, or either side of the lining.')
    if out['continuation'] not in ('auto', 'compact', 'long'):
        raise ValueError('Choose a compact object or a continuing service.')
    if out['lining_reference'] not in ('local','longitudinal'):
        raise ValueError('Choose a local or longitudinal measured lining reference.')
    if out['search_scope'] not in ('auto','whole'):
        raise ValueError('Choose whether to use the active limit box or ignore it.')
    if any(type(out[k]) is not bool for k in ('rotations','respect_view','include_reviewed')):
        raise ValueError('Rotation, view-filter and reviewed-pocket options must be true or false.')
    return out


def emit(progress, cancel, message):
    if cancel(): raise ValueError('Shape selection stopped. Source points and the previous result are unchanged.')
    progress(message)


def pieces(batches):
    """Bound output even if a caller yields a larger owned block."""
    for ids, points, spec in batches():
        ids = np.asarray(ids); points = np.asarray(points, float)
        if ids.ndim != 1 or ids.dtype.kind not in 'iu' or points.shape != (len(ids), 3):
            raise ValueError('Shape matching needs integer original IDs and XYZ rows.')
        if not np.isfinite(points).all() or (ids < 0).any():
            raise ValueError('Invalid original IDs or nonfinite point coordinates.')
        for start in range(0, len(ids), BLOCK):
            yield ids[start:start+BLOCK], points[start:start+BLOCK], spec


def indices(points, model, cell, columns):
    t, a = lining_features.frame(points, model)
    rows = np.floor(t/cell).astype(np.int64)
    cols = np.minimum(columns-1, np.floor(a*columns/(2*np.pi)).astype(np.int64))
    return rows, cols


def candidates(points, model, cfg, *, with_angle=False):
    if 'shape_reference' in model:
        import shape_lining
        result=shape_lining.deviations(points,model,with_angle=with_angle);d,supported=result[:2]
        if with_angle:angle=result[2]
    else:
        d,supported=tunnel_lining.deviations(points,model)
        if with_angle:angle=lining_features.frame(points,model)[1]
    use = supported & (np.abs(d) > cfg['min_offset_mm']/1000) & (np.abs(d) <= cfg['max_offset_mm']/1000)
    if cfg['side'] == 'raised': use &= d < 0
    elif cfg['side'] == 'recess': use &= d > 0
    return (d,use,supported,angle) if with_angle else (d,use,supported)


class Evidence:
    """Sparse candidate cells; SQLite's cache is bounded independently of N."""
    def __init__(self, path):
        self.path = Path(path); self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA cache_size=-32768')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('''CREATE TABLE cells (
            r INTEGER, c INTEGER, side INTEGER, n INTEGER, v REAL,
            en INTEGER, ev REAL, gid INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(r,c,side)) WITHOUT ROWID''')
        self.db.execute(MATCH_SCHEMA)

    def close(self): self.db.close()

    def accumulate(self, rows, cols, d, example):
        if not len(rows): return
        side = np.where(d < 0, -1, 1)
        keys, inverse = np.unique(np.c_[rows, cols, side], axis=0, return_inverse=True)
        n = np.bincount(inverse); values = np.bincount(inverse, weights=np.abs(d))
        en = np.bincount(inverse, weights=example.astype(np.int64), minlength=len(keys))
        ev = np.bincount(inverse, weights=np.abs(d)*example, minlength=len(keys))
        records = ((int(k[0]), int(k[1]), int(k[2]), int(a), float(b), int(c), float(e))
                   for k, a, b, c, e in zip(keys, n, values, en, ev))
        self.db.executemany('''INSERT INTO cells(r,c,side,n,v,en,ev) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(r,c,side) DO UPDATE SET n=n+excluded.n, v=v+excluded.v,
            en=en+excluded.en, ev=ev+excluded.ev''', records)
        self.db.commit()


def template(evidence, cfg, columns):
    db = evidence.db
    bounds = db.execute('SELECT min(r), max(r), count(*) FROM cells WHERE en>0').fetchone()
    if not bounds[2]: raise ValueError('The drawn example has no supported points in the chosen lining-offset range. Check side, offset and the drawn area.')
    cols = np.array([r[0] for r in db.execute('SELECT DISTINCT c FROM cells WHERE en>0 ORDER BY c')], int)
    gaps = np.diff(np.r_[cols, cols[0]+columns]); origin = int(cols[(int(np.argmax(gaps))+1) % len(cols)])
    width = int(((cols-origin) % columns).max())+1; height = bounds[1]-bounds[0]+1
    if max(width, height) > 512 or width*height > 262144:
        raise ValueError('Draw one object or a short section of the service as the example, or increase Shape grid size. The whole cloud will still be searched without a point-count cap.')
    sign = db.execute('SELECT side FROM cells WHERE en>0 GROUP BY side ORDER BY sum(en) DESC,side LIMIT 1').fetchone()[0]
    mask = np.zeros((height, width), bool); depth = np.zeros((height, width))
    example_points = 0
    for r, c, n, v in db.execute('SELECT r,c,en,ev FROM cells WHERE en>0 AND side=?', (sign,)):
        mask[r-bounds[0], (c-origin) % columns] = True
        depth[r-bounds[0], (c-origin) % columns] = v/n; example_points += n
    # A one-cell closing connects sampling gaps; it creates no scan points.
    mask = binary_closing(np.pad(mask, 2), structure=np.ones((3,3)))[2:-2, 2:-2] | mask
    regions, number = label(mask, structure=np.ones((3,3)))
    sizes = np.bincount(regions.ravel()); sizes[0] = 0
    best = int(np.argmax(sizes)); mask = regions == best
    if mask.sum() < 8: raise ValueError('The example has too little spatial shape evidence. Draw more of the object or reduce Shape grid size.')
    rr, cc = np.where(mask); crop = (slice(rr.min(), rr.max()+1), slice(cc.min(), cc.max()+1))
    mask = mask[crop]; depth = depth[crop]
    observed = mask & (depth > 0)
    nearest = distance_transform_edt(~observed, return_distances=False, return_indices=True)
    depth = np.where(mask, depth[tuple(nearest)], 0)
    long = cfg['continuation'] == 'long' or cfg['continuation'] == 'auto' and max(mask.shape)/max(1,min(mask.shape)) >= 3
    return dict(mask=mask, depth=depth, long=bool(long), example_sign=int(sign),
                example_points=int(example_points), components=int(number),
                footprint_cells=int(mask.sum()), size_m=[v*cfg['grid_mm']/1000 for v in mask.shape])


def pattern(t, rotation):
    mask = np.rot90(t['mask'], rotation); depth = np.rot90(t['depth'], rotation)
    pad = max(2, min(16, int(math.ceil(min(mask.shape)*.3))))
    m = np.pad(mask, pad); h = np.pad(depth, pad)
    guard = ~m
    if t['long']:
        # A pipe/duct may continue beyond the example's two ends. Width and
        # depth remain constrained; this is not an unlimited wall-plane match.
        if mask.shape[0] >= mask.shape[1]: guard[:pad] = False; guard[-pad:] = False
        else: guard[:, :pad] = False; guard[:, -pad:] = False
    return m.astype(np.float32), h.astype(np.float32), guard.astype(np.float32)


def scores(occupied, depth, mask, height, guard, tolerance):
    """Footprint overlap plus RMS depth agreement; not a confidence estimate."""
    def corr(a, b): return fftconvolve(a, b[::-1, ::-1], mode='valid')
    c = occupied.astype(np.float32)
    # Centre both height fields before the squared-error convolutions. Without
    # this, large service offsets can cancel at float32 precision when the
    # requested depth agreement is only a few millimetres.
    reference=float(np.mean(height[mask>0],dtype=np.float64))
    d=(depth-reference).astype(np.float32)
    height=(height-reference).astype(np.float32)
    n = np.maximum(corr(c, mask), 0); stray = np.maximum(corr(c, guard), 0)
    square = corr(c*d*d, mask)-2*corr(c*d, mask*height)+corr(c, mask*height*height)
    rms2 = np.maximum(square, 0)/np.maximum(n, 1)
    shape = 2*n/np.maximum(float(mask.sum())+n+stray, 1)
    value = shape*np.exp(-rms2/(2*tolerance*tolerance))
    value[n < .55*mask.sum()] = 0
    return np.clip(value, 0, 1)


def _match_fft(evidence, t, cfg, columns, progress, cancel):
    db = evidence.db; rotations = range(4) if cfg['rotations'] else range(1)
    patterns = [pattern(t, r) for r in rotations]
    halo = max(p[0].shape[0] for p in patterns); pad = max(p[0].shape[1] for p in patterns)
    core = max(16, min(256, 1000000//columns))
    if (core+2*halo)*(columns+2*pad) > 4000000:
        raise ValueError('Increase Shape grid size or draw a shorter example to keep each shape-matching window bounded. There is no cloud point-count limit.')
    low, high = db.execute('SELECT min(r),max(r) FROM cells').fetchone()
    threshold = cfg['similarity_percent']/100; next_id = int(db.execute('SELECT coalesce(max(id),0)+1 FROM matches').fetchone()[0]); windows = 0
    for first in range(low, high+1, core):
        emit(progress, cancel, f'Matching example in search area · section {first*cfg["grid_mm"]/1000:.2f} m · {windows} windows checked')
        start = first-halo; stop = first+core+halo
        for side in (-1, 1):
            occupied = np.zeros((stop-start, columns), np.float32); depth = np.zeros_like(occupied); assigned = np.zeros_like(occupied, dtype=np.int32)
            for r,c,n,v,gid in db.execute('SELECT r,c,n,v,gid FROM cells WHERE r>=? AND r<? AND side=?', (start,stop,side)):
                occupied[r-start,c] = 1; depth[r-start,c] = v/n; assigned[r-start,c] = gid
            if not occupied.any(): continue
            for rotation, (m,h,g) in enumerate(patterns):
                emit(progress, cancel, f'Checking shape orientation {rotation+1}/{len(patterns)} · whole-cloud section {first*cfg["grid_mm"]/1000:.2f} m')
                # Circular padding makes the arbitrary angular seam invisible.
                wrap = m.shape[1]
                raw = np.pad(occupied, ((0,0),(wrap,wrap)), mode='wrap')
                d = np.pad(depth, ((0,0),(wrap,wrap)), mode='wrap')
                # Use the same one-cell sampling-gap representation for the
                # example and target. Final assignment still uses raw cells.
                c = binary_closing(raw>0,structure=np.ones((3,3))) | (raw>0)
                fill=c & (raw==0)
                if fill.any():
                    nearest=distance_transform_edt(raw==0,return_distances=False,return_indices=True)
                    d[fill]=d[tuple(nearest[:,fill])]
                with set_workers(compute_runtime.workers()):
                    values = scores(c,d,m,h,g,cfg['depth_tolerance_mm']/1000)
                rows = np.arange(values.shape[0])+start+m.shape[0]//2
                cols = np.arange(values.shape[1])-wrap+m.shape[1]//2
                allowed = (rows[:,None]>=first)&(rows[:,None]<first+core)&(cols[None,:]>=0)&(cols[None,:]<columns)
                values[~allowed] = 0
                # Deterministic sub-epsilon tie breaking avoids a peak at every
                # pixel along an otherwise identical continuing pipe section.
                ii,jj = np.indices(values.shape, sparse=True)
                ranked = values.astype(np.float64)+((ii*73856093 ^ jj*19349663) % 1021)*1e-12
                peaks = (values>=threshold)&(ranked==maximum_filter(ranked,size=(max(3,m.shape[0]//2),max(3,m.shape[1]//2)),mode='constant'))
                rr,cc = np.where(peaks); order = np.argsort(-values[rr,cc],kind='stable')
                mr,mc = np.where(m)
                for serial, at in enumerate(order):
                    if serial%64 == 0: emit(progress,cancel,f'Reviewing geometric matches · {next_id-1:,} candidate groups')
                    r=int(rr[at]); col=int(cc[at]); ar=r+mr; ac=(col+mc-wrap)%columns
                    present=occupied[ar,ac]>0
                    present &= abs(depth[ar,ac]-h[mr,mc])<=max(.002,2*cfg['depth_tolerance_mm']/1000)
                    if present.sum()<max(4,.15*len(mr)): continue
                    ar=ar[present]; ac=ac[present]; old=assigned[ar,ac]; new=old==0
                    if new.sum()<max(1,.15*len(old)): continue
                    if t['long'] and np.any(old):
                        counts=np.bincount(old[old>0]); gid=int(np.argmax(counts))
                    else:
                        if np.count_nonzero(old)>.5*len(old): continue
                        gid=next_id; next_id+=1
                        db.execute('INSERT INTO matches(id,station,angle,score,side) VALUES(?,?,?,?,?)',
                                   (gid,(r+start+m.shape[0]//2)*cfg['grid_mm']/1000,((col-wrap+m.shape[1]//2)%columns)*2*np.pi/columns,float(values[r,col]),side))
                    ar=ar[new]; ac=ac[new]; assigned[ar,ac]=gid
                    db.executemany('UPDATE cells SET gid=? WHERE r=? AND c=? AND side=? AND gid=0',
                                   ((gid,int(a+start),int(b),side) for a,b in zip(ar,ac)))
                db.commit()
            windows += 1
    if t['long']:
        # Match a short section, then follow connected off-lining cells to the
        # service ends. The recursive frontier and visited set live in SQLite,
        # not an in-memory whole-cloud graph. Offset agreement still applies.
        low_depth=max(cfg['min_offset_mm']/1000,float(t['depth'][t['mask']].min())-2*cfg['depth_tolerance_mm']/1000)
        high_depth=min(cfg['max_offset_mm']/1000,float(t['depth'][t['mask']].max())+2*cfg['depth_tolerance_mm']/1000)
        groups=db.execute('SELECT id FROM matches ORDER BY id').fetchall()
        db.set_progress_handler(lambda:int(bool(cancel())),10000)
        try:
            for (gid,) in groups:
                emit(progress,cancel,f'Following continuing service {gid} to its measured ends')
                db.execute('''WITH RECURSIVE connected(r,c,side) AS (
                    SELECT r,c,side FROM cells WHERE gid=?
                    UNION
                    SELECT b.r,b.c,b.side FROM cells b JOIN connected a
                      ON b.r BETWEEN a.r-2 AND a.r+2 AND b.side=a.side
                      AND min(abs(b.c-a.c),?-abs(b.c-a.c))<=2
                    WHERE b.gid IN (0,?) AND b.v/b.n BETWEEN ? AND ?)
                    UPDATE cells SET gid=? WHERE gid=0 AND (r,c,side) IN connected''',
                           (gid,columns,gid,low_depth,high_depth,gid))
            db.commit()
        except sqlite3.OperationalError:
            if cancel():raise ValueError('Shape selection stopped. Source points are unchanged.')
            raise
        finally:db.set_progress_handler(None,0)
    return windows


def match(evidence, t, cfg, columns, progress, cancel, **compact_options):
    if t['long']:
        return _match_fft(evidence,t,cfg,columns,progress,cancel)
    import compact_shapes
    return compact_shapes.match(evidence,t,cfg,columns,progress,cancel,emit,**compact_options)


class Lookup:
    def __init__(self, db, columns): self.db=db; self.columns=columns; self.cache={}
    def labels(self, rows, cols, side):
        out=np.zeros(len(rows),'<i4'); blocks=rows//32
        for number in np.unique(blocks):
            if int(number) not in self.cache:
                self.cache.clear(); start=int(number)*32
                tile=np.zeros((32,self.columns,2),'<i4')
                for r,c,s,gid in self.db.execute('SELECT r,c,side,gid FROM cells WHERE r>=? AND r<? AND gid>0',(start,start+32)):
                    tile[r-start,c,int(s>0)] = gid
                self.cache[int(number)] = tile
            use=blocks==number
            out[use]=self.cache[int(number)][rows[use]-int(number)*32,cols[use],(side[use]>0).astype(int)]
        return out


def run(batches, example, folder, cfg, progress=lambda _:None, cancel=lambda:False, model=None, bank=None,
        *, reviewed=()):
    """Return full-density candidate ID files and a bounded review preview."""
    cfg=settings(cfg); example=None if bank else jobdata.selection_spec(example); folder=Path(folder)
    if bank and cfg['continuation']=='long':raise ValueError('Saved shape types describe compact objects. Use a drawn service example for a continuing pipe or duct.')
    started=time.monotonic(); source=lambda:pieces(batches)
    emit(progress,cancel,'Learning lining in the shape search area')
    sample,total=sample_originals(source,.015,160000,progress,cancel)
    if not total: raise ValueError('The retained source is empty.')
    if model is None:
        model=tunnel_lining.fit_model(sample,{'envelope_thickness':.04,'envelope_angle_deg':1.5,'resolution':.2},progress,cancel)
        model=tunnel_lining.longitudinal_reference(sample,model)
        if cfg['lining_reference']=='local':
            import shape_lining
            model=shape_lining.build(sample,model,cfg['min_offset_mm'],progress,cancel)
    reference=model.get('shape_reference');coverage=np.zeros((8,4),np.int64)
    up_angle=float(np.arctan2(model['v'][2],model['u'][2]))
    def sectors(angle):return np.floor(np.mod(angle-up_angle+np.pi/8,2*np.pi)*4/np.pi).astype(np.int64)%8
    cell=cfg['grid_mm']/1000; columns=int(np.ceil(2*np.pi*model['radius']/cell))
    if columns<8 or columns>50000: raise ValueError('Choose a coarser Shape grid size for this tunnel circumference.')
    evidence=Evidence(folder/'evidence.sqlite'); written=unsupported=example_total=0
    try:
        review_path=folder/'review.sqlite'
        evidence.db.execute('ATTACH DATABASE ? AS review',(str(review_path),))
        for ids,p,_ in source():
            emit(progress,cancel,f'Building shape evidence in the search area · {written+len(ids):,}/{total:,} original points')
            d,use,supported,angle=candidates(p,model,cfg,with_angle=True); unsupported+=int((~supported).sum());sector=sectors(angle)
            coverage[:,0]+=np.bincount(sector,minlength=8);coverage[:,1]+=np.bincount(sector[supported],minlength=8);coverage[:,2]+=np.bincount(sector[use],minlength=8)
            ex=np.zeros(len(p),bool) if bank else jobdata.selection_mask(p,example); example_total+=int(ex.sum())
            r,c=indices(p[use],model,cell,columns)
            evidence.accumulate(r,c,d[use],ex[use]); written+=len(ids)
        if written!=total: raise ValueError('The retained source changed during shape matching.')
        if bank:
            positive=[e for e in bank['entries'] if e['label']=='positive']
            t=dict(components=1,long=False,example_points=0,footprint_cells=sum(e['footprint_cells'] for e in positive),size_m=[])
        else:t=template(evidence,cfg,columns)
        emit(progress,cancel,'Example learned. Searching the chosen area for matching footprints and offsets')
        skipped=set()
        options=dict(review=True,reviewed=() if cfg['include_reviewed'] else reviewed,skipped=skipped)
        if bank:
            import compact_shapes
            windows=compact_shapes.match(evidence,None,cfg,columns,progress,cancel,emit,bank=bank,**options)
        else:windows=match(evidence,t,cfg,columns,progress,cancel,**options)
        lookup=Lookup(evidence.db,columns); matched=checked=0
        ids_path=folder/'candidate_ids.bin'; labels_path=folder/'candidate_labels.bin'; flags_path=folder/'selected.bin'
        expected=evidence.db.execute('SELECT coalesce(sum(n),0) FROM cells WHERE gid>0').fetchone()[0]
        stride=max(1,math.ceil(expected/DISPLAY))
        preview=[];preview_labels=[];preview_indices=[];preview_regions=[]
        group_counts={};region_counts=[{} for _ in coverage]
        with ids_path.open('wb') as out, labels_path.open('wb') as lab, flags_path.open('wb') as flags:
            for ids,p,_ in source():
                emit(progress,cancel,f'Selecting matched original points · {checked+len(ids):,}/{total:,} checked · {matched:,} matched')
                d,use,_=candidates(p,model,cfg); local=np.flatnonzero(use)
                r,c=indices(p[use],model,cell,columns); labels=lookup.labels(r,c,np.where(d[use]<0,-1,1))
                keep=labels>0; local=local[keep]; labels=labels[keep]
                region=sectors(lining_features.frame(p[local],model)[1])
                coverage[:,3]+=np.bincount(region,minlength=len(coverage))
                np.asarray(ids[local],'<i8').tofile(out); labels.tofile(lab); np.ones(len(local),'u1').tofile(flags)
                for key,size in zip(*np.unique(labels.astype(np.int64)*len(coverage)+region,return_counts=True)):
                    ident,sector=divmod(int(key),len(coverage))
                    group_counts[ident]=group_counts.get(ident,0)+int(size)
                    counts=region_counts[sector]
                    counts[ident]=counts.get(ident,0)+int(size)
                visible=(np.arange(len(local))+matched)%stride==0
                if visible.any():
                    preview.extend(p[local[visible]].tolist()); preview_labels.extend(labels[visible].tolist())
                    preview_indices.extend((np.flatnonzero(visible)+matched).tolist())
                    preview_regions.extend(region[visible].tolist())
                matched+=len(local); checked+=len(ids)
        if checked!=total or matched!=expected: raise ValueError('The retained source changed before the exact shape selection was completed.')
        emit(progress,cancel,'Saving candidate counts and coverage')
        review=sqlite3.connect(review_path)
        try:
            review.execute(MATCH_SCHEMA)
            for row in evidence.db.execute('SELECT id,station,angle,score,side FROM matches ORDER BY id'):
                n=group_counts.get(row[0],0)
                if n: review.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?)',(*row,n,n))
            review.execute(REGION_SCHEMA)
            review.executemany('INSERT INTO match_regions VALUES(?,?,?)',
                               ((sector,ident,size) for sector,counts in enumerate(region_counts) for ident,size in counts.items()))
            review.commit()
        finally: review.close()
        emit(progress,cancel,'Saving the shape review preview')
        context=sample[::max(1,math.ceil(len(sample)/DISPLAY))][:DISPLAY]
        np.savez(folder/'preview.npz',context=context,points=np.asarray(preview,float).reshape(-1,3),labels=np.asarray(preview_labels,'<i4'),
                 indices=np.asarray(preview_indices,'<i8'),regions=np.asarray(preview_regions,'u1'))
        warnings=['Matches describe similar geometric footprints and lining offsets. They do not prove that an object is a bolt, pipe or ventilation duct. Review before removing or separating.',
                  'The drawn area teaches one example. The search checks the retained observations in the chosen search area.',
                  'No trained neural classifier or published accuracy claim is included. Source points are never moved and missing lining is not reconstructed.']
        if t['components']>1: warnings.append('The example contains several disconnected departures. The largest connected footprint was used; draw a tighter example to choose another shape.')
        if unsupported: warnings.append(f'{unsupported:,} points lack supported lining reference and remain unselected.')
        if cfg['grid_mm']>cfg['min_offset_mm']: warnings.append('Shape grid size is coarser than the minimum offset. Inspect fine edges; the final offset test still uses each original observation.')
        if t['long']: warnings.append('Continuing services follow connected off-lining cells within the example depth range, including attached branches with similar offsets. Inspect junctions before applying the selection.')
        if bank:warnings.append('All confirmed examples of the chosen type are compared. Confirmed incorrect examples veto equally close matches. More examples can improve coverage, but do not guarantee recognition; review each new result.')
        if reference:warnings.append('Local robust wall patches follow nearby measured lining around the full circumference. Unsupported patches remain unselected. The reference is not a design surface or survey control.')
        coverage_names=['Crown','Upper side A','Side A','Lower side A','Invert','Lower side B','Side B','Upper side B']
        if np.hypot(model['u'][2],model['v'][2])<.2:coverage_names=['Sector '+str(i+1) for i in range(8)]
        coverage_report=[dict(region=coverage_names[i],processed=int(v[0]),supported=int(v[1]),offset_candidates=int(v[2]),
                              matched=int(v[3]),candidate_count=len(region_counts[i])) for i,v in enumerate(coverage)]
        hemisphere=[0,1,7];upper=coverage[hemisphere].sum(0);lower=coverage[[3,4,5]].sum(0)
        if upper[0] and not upper[3] and lower[3]:warnings.append('No matches were selected in the upper region. Review the circumference coverage panel: missing wall support, offset candidates and rejected shape matches are reported separately.')
        return dict(count=matched,point_ids_file=str(ids_path),labels_file=str(labels_path),selected_file=str(flags_path),review_file=str(review_path),preview_file=str(folder/'preview.npz'),
                    statistics=dict(input_points=total,candidate_points=matched,selected_points=matched,unsupported_points=unsupported,example_area_points=example_total,example_candidate_points=t['example_points'],example_footprint_cells=t['footprint_cells'],example_size_m=t['size_m'],continuing_service=t['long'],map_cell_mm=cfg['grid_mm'],calibration_points=len(sample),matching_windows=windows,seconds=round(time.monotonic()-started,3),library_positive=sum(e['label']=='positive' for e in bank['entries']) if bank else 0,library_negative=sum(e['label']=='negative' for e in bank['entries']) if bank else 0),
                     library_names=bank['names'] if bank else {},coverage=coverage_report,
                     reviewed_skipped=len(skipped),
                    lining_reference=dict(method='Robust local quadratic measured wall patches' if reference else 'Longitudinal measured reference',
                        supported_patches=reference['valid_patches'] if reference else None,patches=reference['patches'] if reference else None,
                        median_wall_rms_mm=reference['median_wall_rms_mm'] if reference else None,fit_limit_mm=reference['fit_limit_mm'] if reference else None),
                    warnings=warnings,method='Example footprint and depth matching against measured lining; tiled geometric candidates',research_dois=['10.1088/1742-6596/1828/1/012155','10.3390/s26154901'])
    finally:
        evidence.close()
        for suffix in ('','-journal','-wal','-shm'):(folder/('evidence.sqlite'+suffix)).unlink(missing_ok=True)
