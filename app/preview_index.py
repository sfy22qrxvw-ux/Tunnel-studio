# Copyright (c) 2026 Damian Gulczynski.
"""Conservative block bounds for repeated full-density viewport reads.

An index never supplies replacement points. Candidate blocks are still checked
against the exact current view and current deletion mask by detail_preview.
"""
from pathlib import Path
import os, threading, uuid
from collections import OrderedDict
import numpy as np

BLOCK = 16384
_cache = OrderedDict()
_lock = threading.Lock()


def _key(meta):
    p = Path(meta['file']).resolve(); stat = p.stat()
    n = int(meta['count'])
    if n < 1 or stat.st_size != n * 24: raise ValueError('Invalid point cache for detail view.')
    return str(p), n, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, BLOCK


def bounds(meta, progress=None):
    key = _key(meta)
    cached = cached_bounds(meta, key)
    if cached is not None:
        return cached
    builder = BoundsBuilder(meta, key)
    path, n, size, modified, changed, step = key
    # Larger contiguous reads avoid thousands of small I/O calls. Bound boxes
    # still describe the same 16,384 original rows and never reorder a scan.
    chunk = step*8
    with Path(path).open('rb') as source:
        for start in range(0, n, chunk):
            if progress:
                progress(f'Preparing spatial index · {start:,} / {n:,} points')
            count = min(chunk, n-start)
            q = np.fromfile(source, '<f8', count=count*3)
            if q.size != count*3:
                raise ValueError('The point cache is incomplete while preparing detail.')
            builder.add(start, q.reshape(count, 3))
    return builder.finish()


def cached_bounds(meta, key=None):
    """Read an existing index only; never start an unexpected full scan pass."""
    key = key or _key(meta)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key); return _cache[key]
    path, n, size, modified, changed, step = key
    index_path = Path(path + '.viewindex.npz')
    total = (n + step - 1) // step
    try:
        with np.load(index_path, allow_pickle=False) as saved:
            signature = saved['signature']; boxes = saved['boxes']
        if (np.array_equal(signature, [n, size, modified, changed, step])
                and boxes.shape == (total, 2, 3) and boxes.dtype == np.float64
                and not np.isnan(boxes).any()):
            return _remember(key, boxes)
    except (OSError, ValueError, KeyError, EOFError): pass
    return None


class BoundsBuilder:
    """Build conservative original-row block boxes during another full scan.

    Partial and cancelled scans never publish an index. Working storage is
    six float64 values per 16,384 rows (about 1.6 MB at 550 million points),
    plus the caller's bounded input chunk. No points or selected IDs are kept.
    """
    def __init__(self, meta, key=None):
        self.meta = meta
        self.key = key or _key(meta)
        self.seen = 0
        total = (self.key[1]+BLOCK-1)//BLOCK
        self.boxes = np.empty((total, 2, 3), dtype='<f8')
        self.boxes[:, 0] = np.inf
        self.boxes[:, 1] = -np.inf

    def add(self, start, points):
        points = np.asarray(points, dtype=np.float64)
        if start != self.seen or points.ndim != 2 or points.shape[1] != 3 or start+len(points) > self.key[1]:
            raise ValueError('Spatial index blocks must follow the original point order.')
        offset = 0
        while offset < len(points):
            row = start+offset
            block = row//BLOCK
            count = min(len(points)-offset, BLOCK-row % BLOCK)
            q = points[offset:offset+count]
            finite = np.isfinite(q).all(1)
            if not finite.all():
                q = q[finite]
            if len(q):
                self.boxes[block, 0] = np.minimum(self.boxes[block, 0], q.min(0))
                self.boxes[block, 1] = np.maximum(self.boxes[block, 1], q.max(0))
            offset += count
        self.seen += len(points)

    def finish(self):
        if self.seen != self.key[1]:
            raise ValueError('An incomplete spatial index cannot be saved.')
        return _publish(self.meta, self.key, self.boxes)


class StreamingBoundsBuilder:
    """Collect original-row bounds while an importer already has each XYZ block.

    The final retained count may be unknown until invalid input rows have been
    removed. Capacity grows only for the tiny block-box array, never for points.
    Publication happens after the final coordinate cache has been closed.
    """
    def __init__(self):
        self.seen=0
        self.boxes=np.empty((128,2,3),dtype='<f8')
        self.boxes[:,0]=np.inf;self.boxes[:,1]=-np.inf

    def add(self,start,points):
        points=np.asarray(points,dtype=np.float64)
        if start!=self.seen or points.ndim!=2 or points.shape[1]!=3:
            raise ValueError('Import spatial bounds must follow original point order.')
        needed=(start+len(points)+BLOCK-1)//BLOCK
        if needed>len(self.boxes):
            expanded=np.empty((max(needed,len(self.boxes)*2),2,3),dtype='<f8')
            expanded[:,0]=np.inf;expanded[:,1]=-np.inf
            expanded[:len(self.boxes)]=self.boxes;self.boxes=expanded
        offset=0
        while offset<len(points):
            row=start+offset;block=row//BLOCK;count=min(len(points)-offset,BLOCK-row%BLOCK)
            q=points[offset:offset+count];finite=np.isfinite(q).all(1)
            if not finite.all():q=q[finite]
            if len(q):
                self.boxes[block,0]=np.minimum(self.boxes[block,0],q.min(0))
                self.boxes[block,1]=np.maximum(self.boxes[block,1],q.max(0))
            offset+=count
        self.seen+=len(points)

    def finish(self,meta):
        key=_key(meta)
        if self.seen!=key[1]:raise ValueError('An incomplete import spatial index cannot be saved.')
        count=(self.seen+BLOCK-1)//BLOCK
        return _publish(meta,key,self.boxes[:count].copy())


def world_boxes(meta,boxes):
    """Conservative world bounds without rewriting or reindexing source points."""
    import registration_pose
    R,t=registration_pose.get(meta)
    if registration_pose.is_identity(meta):return boxes
    boxes=np.asarray(boxes,dtype=np.float64);out=boxes.copy()
    valid=np.isfinite(boxes).all(axis=(1,2))
    centre=boxes[valid,0]/2+boxes[valid,1]/2
    half=np.maximum(0,boxes[valid,1]/2-boxes[valid,0]/2)
    transformed=centre@R.T+t;radius=half@np.abs(R).T
    # Outward rounding covers the floating arithmetic in both interval and
    # exact point transforms, including large survey-coordinate translations.
    error=16*np.finfo(float).eps*(np.abs(centre)@np.abs(R).T+np.abs(t)+radius+1)
    out[valid,0]=np.nextafter(transformed-radius-error,-np.inf)
    out[valid,1]=np.nextafter(transformed+radius+error,np.inf)
    return out


def selection_possible(meta,spec):
    """Reject a wholly disjoint scan from metadata before creating any mask."""
    try:
        boxes=np.asarray([[meta['minimum'],meta['maximum']]],dtype=np.float64)
        if boxes.shape!=(1,2,3) or not np.isfinite(boxes).all() or np.any(boxes[:,0]>boxes[:,1]):return True
        return bool(selection_box_mask(world_boxes(meta,boxes),spec).any())
    except (KeyError,TypeError):return True


def _publish(meta, key, boxes):
    path, n, size, modified, changed, step = key
    index_path = Path(path + '.viewindex.npz')
    # Sources are immutable in normal use. A replaced cache must never publish
    # bounds computed from a mixture of old and new file contents.
    if _key(meta) != key: raise ValueError('Scan changed while preparing detail. Refresh the view.')
    temp = index_path.with_name(index_path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as f:
            np.savez(f, signature=np.asarray([n, size, modified, changed, step], dtype='<i8'), boxes=boxes)
        os.replace(temp, index_path)
    except OSError:
        # A read-only/full drive still permits an in-memory index for this run.
        pass
    finally: temp.unlink(missing_ok=True)
    return _remember(key, boxes)


def _remember(key, value):
    value.flags.writeable = False
    with _lock:
        _cache[key] = value; _cache.move_to_end(key)
        while len(_cache) > 16 or sum(v.nbytes for v in _cache.values()) > 32 * 1024 * 1024:
            _cache.popitem(last=False)
    return value


def candidate_blocks(meta, view, frame, boxes=None):
    target, axes, scale, aspect = frame
    boxes = bounds(meta) if boxes is None else boxes
    valid = np.isfinite(boxes).all(axis=(1, 2))
    centre = np.zeros((len(boxes), 3)); half = centre.copy()
    centre[valid] = boxes[valid, 0] / 2 + boxes[valid, 1] / 2
    half[valid] = np.maximum(0, boxes[valid, 1] / 2 - boxes[valid, 0] / 2)
    delta = centre - target
    if view.get('projection') == 'perspective':
        right, up, forward = axes
        tx, ty = scale * aspect * 1.15, scale * 1.15
        planes = [(forward, float(view['far'])), (-forward, -float(view['near'])),
                  (right - tx * forward, 0.), (-right - tx * forward, 0.),
                  (up - ty * forward, 0.), (-up - ty * forward, 0.)]
        for normal, limit in planes:
            valid &= delta @ normal - half @ np.abs(normal) <= limit + 1e-8
    else:
        limits = np.asarray([scale * 1.15, scale / aspect * 1.15, np.inf])
        projected = delta @ axes.T; radius = half @ np.abs(axes).T
        valid &= (np.abs(projected) - radius <= limits + 1e-8).all(1)
    n = int(meta['count'])
    for b in np.flatnonzero(valid):
        start = int(b) * BLOCK
        yield start, min(n, start + BLOCK)


def selection_blocks(meta,spec,progress=None,boxes=None):
    """Conservative candidates; every surviving point still gets an exact test."""
    if boxes is None:boxes=bounds(meta,progress)
    valid=selection_box_mask(boxes,spec)
    for b in np.flatnonzero(valid):yield int(b)*BLOCK,min(meta['count'],(int(b)+1)*BLOCK)


def indexed_selection_blocks(meta,spec,progress=None):
    """Find possible world-space hits using the original-coordinate index."""
    if not selection_possible(meta,spec):return
    yield from selection_blocks(meta,spec,boxes=world_boxes(meta,bounds(meta,progress)))


def selection_box_mask(boxes,spec):
    """Conservative rejection for any set of original-coordinate AABBs."""
    valid=np.isfinite(boxes).all(axis=(1,2))
    centre=np.zeros((len(boxes),3));half=centre.copy()
    centre[valid]=boxes[valid,0]/2+boxes[valid,1]/2;half[valid]=(boxes[valid,1]-boxes[valid,0])/2
    def overlaps(area):
        if area.get('invert'):return np.ones(len(boxes),bool)
        if area['mode']=='box':
            return ((boxes[:,1]>=area['minimum'])&(boxes[:,0]<=area['maximum'])).all(1)
        if area['mode']=='oriented_box':
            axes=np.asarray(area['axes']);d=(centre-area['centre'])@axes.T;r=half@np.abs(axes).T
            return (np.abs(d)-r<=np.asarray(area['half_size'])+1e-8).all(1)
        axes=np.asarray([area['right'],area['up']]);d=(centre-area['target'])@axes.T;r=half@np.abs(axes).T
        if area['mode']=='screen_brush':
            path=np.asarray(area['path']);lo=(path.min(0)-area['radius'])*area['scale'];hi=(path.max(0)+area['radius'])*area['scale']
        else:
            polygon=np.asarray(area['polygon']);factor=np.asarray([area['scale'],area['scale']/area['aspect']]);lo=polygon.min(0)*factor;hi=polygon.max(0)*factor
        use=((d+r>=lo-1e-8)&(d-r<=hi+1e-8)).all(1)
        if area.get('forward') is not None:
            forward=np.asarray(area['forward']);depth=(centre-area['target'])@forward;radius=half@np.abs(forward)
            use&=(depth+radius>=area['depth_min']-1e-8)&(depth-radius<=area['depth_max']+1e-8)
        return use
    valid&=overlaps(spec)
    if spec.get('clip_box'):valid&=overlaps(spec['clip_box'])
    return valid


def retained_bounds(meta,excluded,progress):
    """Exact retained extremes using conservative block bounds to prune reads."""
    boxes=bounds(meta,progress);valid=np.isfinite(boxes).all(axis=(1,2));p=np.memmap(meta['file'],'<f8','r',shape=(meta['count'],3));seen={}
    lo=np.full(3,np.inf);hi=-lo
    try:
        for axis in range(3):
            for end in (0,1):
                order=np.flatnonzero(valid);order=order[np.argsort(boxes[order,end,axis])]
                if end:order=order[::-1]
                for b in order:
                    if end and boxes[b,1,axis]<=hi[axis] or not end and boxes[b,0,axis]>=lo[axis]:break
                    if int(b) not in seen:
                        start=int(b)*BLOCK;stop=min(meta['count'],start+BLOCK);q=np.asarray(p[start:stop])[excluded[start:stop]==0]
                        seen[int(b)]=(q.min(0),q.max(0)) if len(q) else (np.full(3,np.inf),np.full(3,-np.inf))
                        progress(f'Updating remaining bounds · {len(seen):,} spatial blocks checked')
                    low,high=seen[int(b)];lo=np.minimum(lo,low);hi=np.maximum(hi,high)
        return lo,hi
    finally:p._mmap.close()
